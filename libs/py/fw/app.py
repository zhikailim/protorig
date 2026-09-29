"""
fw.app — the standard way to write a Python protorig app (tooling or sim twin).

    from fw.app import App

    app = App("tc397_twin", "Publishes temperature like the real TC397")
    rate = app.arg("--rate", 10.0, "samples per second")
    out = app.writer("Example Temperature")
    app.every(1.0 / rate, lambda: out.write(...))
    app.run()

What App does for you, so apps contain only their own logic:
  - standard arguments: --node, --scenario, --domain, --qos-variant, --verbose
  - domain: --domain, else the scenario's domain, else 0
  - QoS: loads qos/base.xml, topics.xml, variants.xml (+ this node's generated
    node_qos.xml when it exists) as a list; the default profile gives every
    topic its behaviour; --qos-variant switches to a Variant.* profile.
    No QoS code in apps.
  - participant named "<node>/<app>", so Admin Console shows who's who
  - a 1 Hz heartbeat on _sys/NodeStatus (heartbeat=False turns it off: sim twins
    of external nodes, which must not publish anything the real node doesn't)
  - obeys _sys/DemoControl addressed to this app: stop, kill, switch QoS
    variant (restarts itself), set a parameter
  - logs incompatible-QoS events: when a reader and writer refuse to match,
    you see why instead of silence
  - clean shutdown on Ctrl-C / SIGTERM (the participant leaves discovery)

A shortcut, never a wall: app.participant and every reader/writer are the real
Connext objects, and participant_qos=fn lets an app adjust the participant QoS
before it is created (e.g. a twin copying its ECU's liveliness lease).
"""
from __future__ import annotations

import argparse
import math
import os
import platform
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

import rti.connextdds as dds
import yaml

from fw import topics as fw_topics
from fw import types as T

ROOT = Path(__file__).resolve().parents[3]
QOS_FILES = ("base.xml", "topics.xml", "variants.xml")
DEFAULT_PROFILE = "protorig::Topics"
VARIANT_LIBRARY = "protorig_variants"
HEARTBEAT_PERIOD = 1.0
EXIT_KILLED = 137          # exit code used for CMD_KILL_APP (like SIGKILL)


def log(who: str, level: str, msg: str) -> None:
    """One line, same format on every node, so logs from several machines line up."""
    t = time.time()
    stamp = time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"
    print(f"{stamp} {who} {level:<5} {msg}", flush=True)


def scenario_domain(scenario: str) -> int | None:
    f = ROOT / "scenarios" / scenario / "scenario.yaml"
    if not f.exists():
        return None
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    dom = data.get("domain")
    return dom if isinstance(dom, int) else None


def qos_files(scenario: str | None, node: str) -> list[str]:
    files = [str(ROOT / "qos" / f) for f in QOS_FILES if (ROOT / "qos" / f).exists()]
    if scenario:
        node_file = ROOT / "build" / scenario / node / "node_qos.xml"
        if node_file.exists():
            files.append(str(node_file))
    return files


class Timer:
    """Returned by App.every(). Change .period to change the rate live."""
    def __init__(self, period: float, fn: Callable[[], None]):
        self.period = period
        self.due = time.monotonic() + period
        self.fn = fn


class _NoDDS:
    """Stand-in for readers/writers while only --help is being printed."""
    def __getattr__(self, _name):
        return lambda *a, **k: None


class App:
    def __init__(self, name: str, description: str = "", argv: list[str] | None = None,
                 heartbeat: bool = True,
                 participant_qos: Callable[[dds.DomainParticipantQos], None] | None = None):
        self.name = name
        self.heartbeat = heartbeat
        self._argv = list(sys.argv[1:] if argv is None else argv)
        self._parser = argparse.ArgumentParser(prog=name, description=description, add_help=False)
        self._parser.add_argument("-h", "--help", action="store_true", help="show this help and exit")
        g = self._parser.add_argument_group("standard protorig arguments")
        g.add_argument("--node", default="local", help="this machine's node name in the scenario (default: local)")
        g.add_argument("--scenario", default=None, help="scenario name; sets the domain and node QoS")
        g.add_argument("--domain", type=int, default=None, help="DDS domain (default: the scenario's, else 0)")
        g.add_argument("--qos-variant", default="", help="QoS variant, e.g. Variant.Alert.BestEffort")
        g.add_argument("--verbose", action="store_true", help="log every sample received")
        std, _ = self._parser.parse_known_args(self._argv)
        self._help_requested = std.help

        self.node: str = std.node
        self.scenario: str | None = std.scenario
        self.verbose: bool = std.verbose
        self.qos_variant: str = std.qos_variant
        self.domain: int = std.domain if std.domain is not None else (
            (scenario_domain(std.scenario) if std.scenario else None) or 0)
        self.who = f"{self.node}/{self.name}"
        self.params: dict[str, object] = {}
        self.incompatible: list[str] = []          # incompatible-QoS events seen (also logged)
        self._param_callbacks: dict[str, list[Callable]] = {}
        self._on_data: list[tuple] = []
        self._timers: list[Timer] = []
        self._running = True
        self._exit_code = 0
        self._seen_cmds: set[int] = set()
        self._seq = 0
        self._cpu_last = (time.monotonic(), time.process_time())
        if self._help_requested:
            return                                 # --help: no DDS, run() prints help

        # --- QoS: a list of files; the default profile (or a variant) decides everything
        self.provider = dds.QosProvider(";".join(qos_files(self.scenario, self.node)))
        profile = DEFAULT_PROFILE
        if self.qos_variant:
            variants = list(self.provider.qos_profiles(VARIANT_LIBRARY))
            if self.qos_variant not in variants:
                raise SystemExit(f"{self.who}: unknown QoS variant '{self.qos_variant}'. "
                                 f"Known: {', '.join(variants) or 'none'}")
            profile = f"{VARIANT_LIBRARY}::{self.qos_variant}"
        self.provider.default_profile = profile
        self.profile = profile

        pqos = self.provider.participant_qos
        pqos.participant_name.name = self.who
        if participant_qos is not None:
            participant_qos(pqos)                   # app-specific tweaks, applied last
        self.participant = dds.DomainParticipant(self.domain, pqos)
        self._pub = dds.Publisher(self.participant)
        self._sub = dds.Subscriber(self.participant)
        self._topics: dict[str, dds.Topic] = {}
        self._waitset = dds.WaitSet()
        self._conditions: list = []                # kept so they can be closed in order

        # --- framework plumbing
        # (no heartbeat = no NodeStatus writer at all, so nothing extra appears on the wire)
        self._status_w = self.writer("_sys/NodeStatus") if heartbeat else None
        self._control_r = self.reader("_sys/DemoControl")
        self.on_data(self._control_r, self._on_control, internal=True)
        if heartbeat:
            self.every(HEARTBEAT_PERIOD, self._heartbeat)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: self.stop("signal"))
            except (ValueError, OSError):          # not in the main thread / unsupported
                pass
        # The lease is read back from the live participant, so the log shows what is in effect.
        lease = self.participant.qos.discovery_config.participant_liveliness_lease_duration.to_seconds()
        log(self.who, "INFO", f"domain {self.domain}, QoS profile {self.profile}, "
                              f"liveliness lease {lease:g} s{'' if heartbeat else ', no heartbeat'}")

    # ------------------------------------------------------------------ arguments

    def arg(self, flag: str, default, help: str = ""):
        """Declare an app argument and return its value. Its type is the default's type.
        Parameters can later be changed at runtime with CMD_SET_PARAM (see on_param)."""
        kind = type(default)
        if kind is bool:
            self._parser.add_argument(flag, action="store_true", help=help)
        else:
            self._parser.add_argument(flag, type=kind, default=default, help=f"{help} (default: {default})")
        ns, _ = self._parser.parse_known_args(self._argv)
        dest = flag.lstrip("-").replace("-", "_")
        value = getattr(ns, dest)
        self.params[dest] = value
        return value

    def on_param(self, name: str, fn: Callable[[object], None]) -> None:
        """Call fn(new_value) when the Control Panel changes parameter `name` (e.g. "threshold")."""
        self._param_callbacks.setdefault(name, []).append(fn)

    # ------------------------------------------------------------------ DDS entities

    def _topic(self, name: str) -> dds.Topic:
        if name not in self._topics:
            self._topics[name] = dds.Topic(self.participant, name, fw_topics.type_of(name))
        return self._topics[name]

    def writer(self, topic: str) -> dds.DataWriter:
        """A DataWriter for a known topic (fw/topics.py), with that topic's QoS from qos/."""
        if self._help_requested:
            return _NoDDS()
        w = dds.DataWriter(self._pub, self._topic(topic), self.provider.get_topic_datawriter_qos(topic))
        self._watch_status(w, topic, dds.StatusMask.OFFERED_INCOMPATIBLE_QOS)
        return w

    def reader(self, topic: str) -> dds.DataReader:
        """A DataReader for a known topic, with that topic's QoS from qos/."""
        if self._help_requested:
            return _NoDDS()
        r = dds.DataReader(self._sub, self._topic(topic), self.provider.get_topic_datareader_qos(topic))
        self._watch_status(r, topic, dds.StatusMask.REQUESTED_INCOMPATIBLE_QOS)
        return r

    def _watch_status(self, entity, topic: str, mask) -> None:
        cond = dds.StatusCondition(entity)
        cond.enabled_statuses = mask
        role = "writer" if mask == dds.StatusMask.OFFERED_INCOMPATIBLE_QOS else "reader"

        def handler():
            status = (entity.offered_incompatible_qos_status if role == "writer"
                      else entity.requested_incompatible_qos_status)
            if status.total_count_change:
                # last_policy is a policy class, e.g. <class '...Durability'>: keep just "Durability"
                policy = getattr(status.last_policy, "__name__", None) or \
                    str(status.last_policy).split(".")[-1].strip("'<>")
                msg = f"incompatible QoS on '{topic}' ({role} side): {policy}"
                self.incompatible.append(msg)
                log(self.who, "WARN", msg)
        cond.set_handler(lambda _c: handler())
        self._waitset.attach_condition(cond)
        self._conditions.append(cond)

    def on_data(self, reader: dds.DataReader, fn: Callable, internal: bool = False) -> None:
        """Call fn(sample) for every valid sample the reader receives."""
        if self._help_requested:
            return
        def handler():
            for sample in reader.take_data():
                if self.verbose and not internal:
                    log(self.who, "DATA", f"{reader.topic_name}: {sample}")
                try:
                    fn(sample)
                except Exception:
                    log(self.who, "ERROR", f"in on_data({reader.topic_name}):\n{traceback.format_exc()}")
        cond = dds.ReadCondition(reader, dds.DataState.any_data, lambda _c: handler())
        self._waitset.attach_condition(cond)
        self._conditions.append(cond)

    def every(self, seconds: float, fn: Callable[[], None]) -> Timer:
        """Call fn() every `seconds` while the app runs. Returns the Timer;
        set timer.period to change the rate while running."""
        if not seconds > 0:
            raise ValueError("every(): period must be > 0")
        t = Timer(seconds, fn)
        self._timers.append(t)
        return t

    # ------------------------------------------------------------------ plumbing

    def _heartbeat(self) -> None:
        now, cpu = time.monotonic(), time.process_time()
        wall = now - self._cpu_last[0]
        load = (cpu - self._cpu_last[1]) / wall if wall > 0 else 0.0
        self._cpu_last = (now, cpu)
        self._seq += 1
        self._status_w.write(T.NodeStatus(
            node=self.node[:32], app=self.name[:32], os=platform.system().lower()[:16],
            qos_variant=self.qos_variant[:64], seq=self._seq, cpu_load=max(0.0, min(1.0, load)),
            stamp_ns=time.time_ns()))

    def _on_control(self, c: T.DemoControl) -> None:
        if c.target_node not in ("*", self.node) or c.target_app != self.name:
            return                                  # node-level commands belong to node_agent
        if c.cmd_id in self._seen_cmds:
            return
        self._seen_cmds.add(c.cmd_id)
        cmd = c.command
        if cmd == T.Command.CMD_STOP_APP:
            self.stop("stop command")
        elif cmd == T.Command.CMD_KILL_APP:
            log(self.who, "WARN", "kill command: exiting abruptly")
            os._exit(EXIT_KILLED)
        elif cmd == T.Command.CMD_SET_QOS_VARIANT:
            self._restart_with_variant(c.arg)
        elif cmd == T.Command.CMD_SET_PARAM:
            self._set_param(c.arg, c.value)

    def _set_param(self, name: str, value: float) -> None:
        if name not in self.params:
            log(self.who, "WARN", f"set_param: unknown parameter '{name}' (known: {', '.join(self.params) or 'none'})")
            return
        old = self.params[name]
        if not math.isfinite(value):
            log(self.who, "WARN", f"set_param: rejected non-finite value {value} for '{name}'")
            return
        try:
            new = type(old)(value) if not isinstance(old, bool) else bool(value)
        except (TypeError, ValueError):
            log(self.who, "WARN", f"set_param: can't convert {value!r} for '{name}'")
            return
        self.params[name] = new
        log(self.who, "INFO", f"parameter {name}: {old} -> {new}")
        for fn in self._param_callbacks.get(name, []):
            try:
                fn(new)
            except Exception:
                log(self.who, "ERROR", f"in on_param({name}):\n{traceback.format_exc()}")

    def _restart_with_variant(self, variant: str) -> None:
        known = list(self.provider.qos_profiles(VARIANT_LIBRARY)) if variant else []
        if variant and variant not in known:
            log(self.who, "WARN", f"unknown QoS variant '{variant}' (known: {', '.join(known) or 'none'})")
            return
        log(self.who, "INFO", f"switching QoS variant to '{variant or 'default'}': restarting")
        argv, skip = [], False
        for a in self._argv:                        # drop any previous --qos-variant
            if skip:
                skip = False
                continue
            if a == "--qos-variant":
                skip = True
                continue
            if a.startswith("--qos-variant="):
                continue
            argv.append(a)
        if variant:
            argv += ["--qos-variant", variant]
        self._close()
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable, sys.argv[0]] + argv)

    # ------------------------------------------------------------------ run / stop

    def stop(self, reason: str = "") -> None:
        """Ask run() to return after the current step (safe from callbacks)."""
        if self._running:
            log(self.who, "INFO", f"stopping ({reason})" if reason else "stopping")
        self._running = False

    def run(self) -> int:
        """Run until stopped. Returns the process exit code (use: sys.exit(app.run()))."""
        if self._help_requested:
            self._parser.print_help()
            return 0
        _, unknown = self._parser.parse_known_args(self._argv)
        if unknown:
            log(self.who, "ERROR", f"unknown argument(s): {' '.join(unknown)} (see --help)")
            self._close()
            return 2
        log(self.who, "INFO", "running")
        if self.heartbeat:
            self._heartbeat()
        while self._running:
            now = time.monotonic()
            next_due = min((t.due for t in self._timers), default=now + 0.2)
            timeout = max(0.0, min(0.2, next_due - now))
            try:
                self._waitset.dispatch(dds.Duration.from_seconds(timeout))   # runs handlers of triggered conditions
            except dds.TimeoutError:
                pass
            now = time.monotonic()
            for t in self._timers:
                if now >= t.due:
                    t.due = now + t.period if now - t.due > t.period else t.due + t.period   # no burst after a stall
                    try:
                        t.fn()
                    except Exception:
                        log(self.who, "ERROR", f"in every():\n{traceback.format_exc()}")
        self._close()
        log(self.who, "INFO", "stopped")
        return self._exit_code

    def _close(self) -> None:
        """Release everything in the order Connext requires: conditions, then entities."""
        if getattr(self, "participant", None) is None:
            return
        try:
            self._waitset.detach_all()
            for cond in self._conditions:
                if hasattr(cond, "close"):          # ReadConditions close; StatusConditions don't
                    cond.close()
            self._conditions.clear()
            self.participant.close_contained_entities()
            self.participant.close()
        except dds.Error as e:
            log(self.who, "WARN", f"cleanup: {e}")
        self.participant = None
