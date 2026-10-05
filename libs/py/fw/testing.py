"""
fw.testing — helpers for testing protorig apps over DDS, from the outside.

A test starts the real app as a separate process (any language) and talks to
it only through DDS, like any other node would:

    def test_publishes_temperature(bus, start_app):
        app = start_app("tc397_twin", "--rate", "20")      # waits for its heartbeat
        samples = bus.collect("Example Temperature", seconds=1.0)
        assert len(samples) >= 15

Fixtures (defined in the repo's root conftest.py, available in every test):
  bus        one test participant on an isolated domain (never your live one)
  start_app  start an app by name; stopped automatically after the test

Bus methods:
  send(topic, {...}, to=app)     publish one sample; waits for that app's reader to match first
  listen(topic) -> Mailbox       start collecting NOW; read later with .all() .last() .count()
  collect(topic, seconds)        listen, wait, return the list
  command(app, Command, ...)     send a _sys/DemoControl command to an app
  heartbeats(app)                NodeStatus samples from that app

Plus wait_for(condition, timeout) instead of fixed sleeps.

Pattern: LISTEN BEFORE ACTING. Samples written before a reader exists are
missed (unless the topic's QoS keeps history), and the test would then pass or
fail for the wrong reason.
"""
from __future__ import annotations

import dataclasses
import os
import random
import signal
import subprocess
import sys
import threading
import time
import typing
from enum import IntEnum
from pathlib import Path

import rti.connextdds as dds

from fw import topics as fw_topics
from fw import types as T
from fw.app import DEFAULT_PROFILE, ROOT, qos_files
from fw.supervise import Supervised

# Test domains: never 0 or the low numbers a live rig uses, and all BELOW the
# operating system's range of temporary ports (Linux 32768-60999, Windows
# 49152-65535). Connext's ports for domain d start at 7400 + 250*d, so domains
# 60-99 use ports 22400-32200. Domains 150-199 (used before) landed inside the
# temporary range: any program briefly holding the domain's shared discovery
# port made the whole domain unusable ("automatic participant index failed").
TEST_DOMAINS = range(60, 100)
BEST_EFFORT_SETTLE = 0.3   # seconds; see Bus.send


def wait_for(condition, timeout: float, what: str = "condition", poll: float = 0.02):
    """Wait until condition() is true, or fail with a readable message."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(poll)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def make_sample(topic: str, values):
    """A sample of the topic's type from a dict; enum fields accept their names.
    A misspelt field fails immediately instead of silently sending defaults."""
    cls = fw_topics.type_of(topic)
    if not isinstance(values, dict):
        return values
    hints = typing.get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(values) - known
    if unknown:
        raise KeyError(f"{cls.__name__} has no field(s) {sorted(unknown)}; fields: {sorted(known)}")
    kwargs = {}
    for k, v in values.items():
        ftype = hints[k]
        if isinstance(ftype, type) and issubclass(ftype, IntEnum) and isinstance(v, str):
            v = ftype[v]
        kwargs[k] = v
    return cls(**kwargs)


class Mailbox:
    """Collects one topic's samples on a background thread, from creation on."""

    def __init__(self, reader: dds.DataReader):
        self.reader = reader
        self._items: list = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._waitset = dds.WaitSet()
        self._cond = dds.ReadCondition(reader, dds.DataState.any_data)
        self._waitset.attach_condition(self._cond)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._waitset.wait(dds.Duration.from_milliseconds(100))
            except dds.TimeoutError:
                continue
            batch = list(self.reader.take_data())
            with self._lock:
                self._items.extend(batch)

    def all(self) -> list:
        with self._lock:
            return list(self._items)

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def last(self):
        items = self.all()
        assert items, f"no samples received on '{self.reader.topic_name}'"
        return items[-1]

    def close(self):
        """Order matters: stop thread, detach condition, close it, close reader."""
        if self._stop.is_set():
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self._waitset.detach_all()
        self._cond.close()
        self.reader.close()


class Bus:
    """The test's own DDS participant, on an isolated domain, using the repo's QoS."""

    def __init__(self, domain: int | None = None):
        self.domain = domain if domain is not None else random.choice(TEST_DOMAINS)
        self.provider = dds.QosProvider(";".join(qos_files(None, "test")))
        self.provider.default_profile = DEFAULT_PROFILE
        pqos = self.provider.participant_qos
        pqos.participant_name.name = "test/bus"
        self.participant = dds.DomainParticipant(self.domain, pqos)
        self._pub = dds.Publisher(self.participant)
        self._sub = dds.Subscriber(self.participant)
        self._topics: dict = {}
        self._writers: dict = {}
        self._boxes: list[Mailbox] = []
        self._cmd_id = int(time.time() * 1000)

    def _topic(self, name: str):
        if name not in self._topics:
            self._topics[name] = dds.Topic(self.participant, name, fw_topics.type_of(name))
        return self._topics[name]

    def writer(self, topic: str, qos: dds.DataWriterQos | None = None) -> dds.DataWriter:
        """The bus's writer for a topic (created once). Pass qos to deliberately differ."""
        if topic not in self._writers or qos is not None:
            w = dds.DataWriter(self._pub, self._topic(topic), qos or self.provider.get_topic_datawriter_qos(topic))
            if qos is not None:
                return w
            self._writers[topic] = w
        return self._writers[topic]

    def send(self, topic: str, values, match_timeout: float = 5.0, to=None) -> None:
        """Publish one sample. Waits until a reader has matched, otherwise the
        sample would be written before discovery finished and be lost.

        to: the app that must receive it (a RunningApp, or its participant name
        "node/app"). Then it waits for THAT app's reader, not just any reader:
        the test bus is shared by every test, so a reader left over from an
        earlier test's app could otherwise count as "matched" while the new app
        hasn't been discovered yet, and the sample would miss it."""
        w = self.writer(topic)
        if to is None:
            wait_for(lambda: w.publication_matched_status.current_count > 0, match_timeout,
                     f"a reader of '{topic}' to match")
        else:
            who = to if isinstance(to, str) else f"{to.node}/{to.name}"
            def matched():
                for h in w.matched_subscriptions:
                    try:
                        if w.matched_subscription_participant_data(h).participant_name.name == who:
                            return True
                    except dds.Error:                  # that reader went away meanwhile
                        pass
                return False
            wait_for(matched, match_timeout, f"{who}'s reader of '{topic}' to match")
        # A match is seen by the writer first. A BEST-EFFORT reader that hasn't yet
        # learned about this writer drops the sample, and nothing re-sends it (a
        # reliable one would recover it). The reader's side can't be observed from
        # here, so give it a moment when any matched reader is best effort.
        if any(w.matched_subscription_data(h).reliability.kind == dds.ReliabilityKind.BEST_EFFORT
               for h in w.matched_subscriptions):
            time.sleep(BEST_EFFORT_SETTLE)
        w.write(make_sample(topic, values))

    def listen(self, topic: str, qos: dds.DataReaderQos | None = None) -> Mailbox:
        """Start collecting a topic now. Pass qos to deliberately request different QoS."""
        r = dds.DataReader(self._sub, self._topic(topic), qos or self.provider.get_topic_datareader_qos(topic))
        box = Mailbox(r)
        self._boxes.append(box)
        return box

    def collect(self, topic: str, seconds: float) -> list:
        box = self.listen(topic)
        time.sleep(seconds)
        return box.all()

    def command(self, app: "RunningApp", command, arg: str = "", value: float = 0.0,
                target_node: str | None = None) -> None:
        """Send a _sys/DemoControl command to a running app."""
        self._cmd_id += 1
        self.send("_sys/DemoControl", T.DemoControl(
            target_node=target_node if target_node is not None else app.node, target_app=app.name,
            cmd_id=self._cmd_id, command=command, arg=arg, value=value), to=app)

    def close_listeners(self) -> None:
        for b in self._boxes:
            b.close()
        self._boxes.clear()

    def close(self) -> None:
        self.close_listeners()
        self.participant.close_contained_entities()
        self.participant.close()


def find_app(name: str) -> tuple[str, Path]:
    """(kind, folder) of an app, shared or scenario-local."""
    hits = [(k, p) for k in ("vehicle", "tooling", "sim")
            for p in [ROOT / "apps" / k / name, *(ROOT / "scenarios").glob(f"*/apps/{k}/{name}")]
            if p.is_dir()]
    if not hits:
        raise FileNotFoundError(f"app '{name}' not found under apps/ or scenarios/*/apps/")
    return hits[0]


class RunningApp(Supervised):
    """An app started by a test, run exactly like `protorig run` and node_agent
    run theirs (fw.supervise.Supervised). Adds the app's name and node."""

    def __init__(self, name: str, node: str, cmd: list[str], args: list[str], env: dict,
                 watch_launcher: bool = True):
        self.name, self.node = name, node
        super().__init__(cmd, args, env, watch_launcher=watch_launcher)

    def wait_exit(self, timeout: float = 5.0) -> int:
        try:
            return super().wait_exit(timeout)
        except AssertionError as e:
            raise AssertionError(f"{self.name} {e}") from None


class AppLauncher:
    """What the start_app fixture returns: call it to start an app."""

    def __init__(self, bus: Bus):
        self.bus = bus
        self.running: list[RunningApp] = []

    def __call__(self, name: str, *args: str, node: str = "test-node", folder: Path | None = None,
                 wait_heartbeat: bool = True, timeout: float = 10.0, supervised: bool = True,
                 env: dict | None = None) -> RunningApp:
        """Start app `name` (found in the repo, or in `folder` if given) on the bus's domain.

        Waits until it is ready: its first heartbeat, or with wait_heartbeat=False
        (apps without one, e.g. sim twins) its "running" log line. Raises if it
        exits during start-up, unless wait_heartbeat=False (tests of start-up errors).
        supervised=False starts it like a person would by hand: no launcher
        process ID for it to watch (N6).
        env: extra environment variables for the app."""
        if folder is None:
            _, folder = find_app(name)
        folder = Path(folder)
        if (folder / "main.py").exists():
            cmd = [sys.executable, str(folder / "main.py")]
        else:
            raise NotImplementedError(f"{name}: C/C++ apps are started from build/ once `protorig build` exists")
        full_env = dict(os.environ)
        full_env["PYTHONPATH"] = str(ROOT / "libs" / "py") + os.pathsep + full_env.get("PYTHONPATH", "")
        full_env["PYTHONUNBUFFERED"] = "1"
        full_env.update(env or {})
        app = RunningApp(name, node, cmd, ["--node", node, "--domain", str(self.bus.domain), *args],
                         full_env, watch_launcher=supervised)
        self.running.append(app)
        if wait_heartbeat:
            beats = self.bus.listen("_sys/NodeStatus")
            try:
                wait_for(lambda: any(s.app == name and s.node == node for s in beats.all()) or app.exited(),
                         timeout, f"{name}'s first heartbeat")
            finally:
                beats.close()
            if app.exited():
                raise AssertionError(f"{name} exited during start-up (code {app.returncode})\n"
                                     f"--- output ---\n{app.output}")
        else:
            wait_for(lambda: any(l.endswith(" running") for l in app.lines) or app.exited(),
                     timeout, f"{name} to start")
        return app

    def stop_all(self) -> None:
        for app in self.running:
            app.terminate()
        self.running.clear()
