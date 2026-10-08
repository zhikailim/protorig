"""
live.py — `protorig run <scenario> --live` (run.md U11, U12, U13): bring the whole
rig up through its node agents, show it, and stop it on Ctrl-C.

    1. finds this machine's node (U13) and uses its discovery settings, in its own
       file build/<scenario>/<node>/live_qos.xml (never the agent's), participant
       <node>/run-live (U12)
    2. waits for every managed node's agent (--partial: whichever are there);
       external nodes are listed once, and reported when their data is seen
    3. refuses if an agent runs another scenario
    4. sends "start *" to each agent (or reattaches if the demo already runs),
       resending once after 3 s to an agent that showed no change
    5. prints every app state change, alert and "agent LOST/back" until Ctrl-C,
       flagging apps not RUNNING after --start-timeout seconds
    6. Ctrl-C sends "stop *", waits up to 15 s, reports how each app ended; the
       agents stay up. A second Ctrl-C exits at once.

Exit code: 0 everything started and stopped cleanly; 1 some app failed;
2 could not start (agents missing, another scenario, wrong machine).

Only reads and commands over DDS: nothing here starts or stops a process itself.
"""
from __future__ import annotations

import os
import random
import signal
import sys
import threading
import time

import yaml

import agent as agentcmd
import repo
from run import RunError, this_node

sys.path.insert(0, str(repo.ROOT / "libs" / "py"))
from fw.agentfiles import AgentFiles          # noqa: E402

POLL = 0.1
FIRST_NOTE = 2.0        # seconds before the first "waiting for" line
WAIT_NOTE = 5.0         # seconds between "still waiting for" lines
PARTIAL_SETTLE = 3.0    # --partial: after the first agent, wait this long for others
SCENARIO_WAIT = 2.0     # an agent's own row (with its scenario) arrives just after its heartbeat
MATCH_WAIT = 5.0        # for an agent's command reader to match ours before sending
RETRY_AFTER = 3.0       # U11 step 3
STOP_WAIT = 15.0        # U11 step 5
RUNNING = {"STARTING", "RUNNING", "RESTARTING"}                       # an app's process is up
ENDED = {"NOT_RUNNING", "STOPPED", "KILLED", "CRASHED", "UNAVAILABLE"}  # no process


def say(text: str) -> None:
    t = time.time()
    print(time.strftime("%H:%M:%S", time.localtime(t)) + f" {text}", flush=True)


def short(state) -> str:
    return state.name.removeprefix("APP_")


class Rig(agentcmd.Listener):
    """The agents as seen over DDS: their heartbeats, app rows and alerts in,
    commands out. Rows are taken as they change (so every change is printed once)."""

    def __init__(self, domain: int, settings, name: str):
        super().__init__(domain, settings, name)
        dds = self._dds
        from fw import types as T
        from fw.topics import type_of
        self._T = T
        sub = dds.Subscriber(self.participant)
        self._cmd_w = dds.DataWriter(dds.Publisher(self.participant),
                                     dds.Topic(self.participant, "_sys/DemoControl", T.DemoControl),
                                     self._prov.get_topic_datawriter_qos("_sys/DemoControl"))
        self._alerts = dds.DataReader(sub, dds.Topic(self.participant, "Alert", T.Alert),
                                      self._prov.get_topic_datareader_qos("Alert"))
        self._type_of = type_of
        self._sub = sub
        self._ext: dict[str, object] = {}         # external topic -> reader (only to see its writers)
        self.table: dict[tuple[str, str], object] = {}

    def _entities(self) -> list:
        return [*super()._entities(), self._alerts, *self._ext.values(), self._cmd_w]

    # --- external devices: seen when a writer of their topic is discovered
    def watch_external(self, topic: str) -> None:
        if topic in self._ext:
            return
        try:
            cls = self._type_of(topic)
        except KeyError:
            return                                # not a topic the framework knows: nothing to watch
        dds = self._dds
        self._ext[topic] = dds.DataReader(self._sub, dds.Topic(self.participant, topic, cls),
                                          self._prov.get_topic_datareader_qos(topic))

    def external_seen(self, topic: str) -> bool:
        r = self._ext.get(topic)
        return r is not None and r.subscription_matched_status.current_count > 0

    # --- rows
    def take_rows(self) -> list:
        """New app rows since the last call (valid samples only), also kept in .table."""
        out = []
        for d, info in self._rows.take():
            if info.valid:
                self.table[(d.node, d.app)] = d
                out.append(d)
        return out

    def apps(self, node: str) -> list:
        return [r for (n, a), r in sorted(self.table.items()) if n == node and a != "node_agent"]

    def scenario_of(self, node: str) -> str | None:
        own = self.table.get((node, "node_agent"))
        return own.scenario if own is not None else None

    def take_alerts(self) -> list[str]:
        out = []
        for d, info in self._alerts.take():
            if info.valid:
                out.append(f"ALERT {d.severity.name.removeprefix('SEVERITY_')} {d.alert_id} ({d.source}): {d.message}")
            elif "DISPOSED" in str(info.state.instance_state):
                try:
                    k = self._alerts.key_value(info.instance_handle)
                    out.append(f"alert cleared: {k.alert_id} ({k.source})")
                except self._dds.Error:
                    pass
        return out

    # --- commands
    def agent_matched(self, node: str) -> bool:
        """Has this node's agent's command reader matched our writer? A command sent
        before that is lost (the topic keeps no history for late readers)."""
        for h in self._cmd_w.matched_subscriptions:
            try:
                if self._cmd_w.matched_subscription_participant_data(h).participant_name.name == f"{node}/node_agent":
                    return True
            except self._dds.Error:
                pass
        return False

    def send(self, node: str, command: str, cmd_id: int) -> None:
        T = self._T
        self._cmd_w.write(T.DemoControl(target_node=node, target_app="*", cmd_id=cmd_id,
                                        command=T.Command[command]))


def new_cmd_id() -> int:
    """Agents ignore a cmd_id seen recently (B7), whoever sent it: a random 63-bit
    number never collides with the Control Panel's or another run's."""
    return random.SystemRandom().getrandbits(63)


def external_topics(node: str) -> list[str]:
    f = repo.ROOT / "external" / node / "external.yaml"
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        topics = data.get("topics") or {}
        return [str(t) for t in topics] if isinstance(topics, dict) else []
    except (OSError, ValueError, yaml.YAMLError):
        return []


class Live:
    def __init__(self, args, data: dict):
        self.args = args
        self.scenario = args.scenario
        self.nodes = data["nodes"]
        self.managed = [n for n, s in self.nodes.items() if isinstance(s, dict) and not s.get("external")]
        self.external = [n for n, s in self.nodes.items() if isinstance(s, dict) and s.get("external")]
        self.data = data
        self.stopping = threading.Event()
        self.failed: set[str] = set()             # "node/app" that failed (exit code 1)
        self.alive: set[str] = set()

    # --- Ctrl-C: first asks to stop, second leaves at once (U11 step 5)
    def on_signal(self, *_):
        if self.stopping.is_set():
            say("second Ctrl-C: leaving without waiting (the agents carry on)")
            os._exit(1)
        self.stopping.set()

    def install_signals(self):
        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
            if sig is not None:
                try:
                    signal.signal(sig, self.on_signal)
                except (ValueError, OSError):
                    pass

    def run(self) -> int:
        me = this_node(self.scenario, self.data, self.args.node, prog="run")     # U12 via U13
        domain = agentcmd.domain_of(self.args, self.data)
        files = AgentFiles.of(repo.ROOT, self.scenario, me)
        settings = agentcmd.write_settings(files, self.data, me, "live_qos.xml")
        self.install_signals()
        rig = agentcmd.open_listener(domain, settings, f"{me}/run-live", cls=Rig)
        try:
            return self.drive(rig, domain)
        finally:
            rig.close()

    # --- the steps
    def drive(self, rig: Rig, domain: int) -> int:
        ext_topics = {n: external_topics(n) for n in self.external}
        for n, topics in ext_topics.items():
            for t in topics:
                rig.watch_external(t)
            say(f"{n}: external device, power it on" + (f" (watching for {', '.join(topics)})" if topics else ""))
        self.ext_seen: set[str] = set()

        targets = self.wait_for_agents(rig, domain, ext_topics)
        if targets is None:
            say("stopped before the rig came up")
            return 2

        wrong = self.check_scenarios(rig, targets)
        if wrong:
            for n, sc in wrong:
                say(f"{n}'s agent runs scenario '{sc}', not '{self.scenario}'. On {n}: "
                    f"protorig agent stop {sc}, then protorig agent start {self.scenario}")
            return 2

        rig.take_rows()
        for n in targets:
            self.show_node(rig, n)
        running = any(short(r.state) in RUNNING for n in targets for r in rig.apps(n))
        sent: dict[str, tuple[int, float, bool]] = {}         # node -> (cmd_id, when, resent)
        changed: set[str] = set()
        if running:
            say("the demo is already running: showing it (Ctrl-C stops it)")
        else:
            for n in targets:
                cid = new_cmd_id()
                self.wait_match(rig, n)
                rig.send(n, "CMD_START_APP", cid)
                sent[n] = (cid, time.monotonic(), False)
            say(f"sent start to {', '.join(targets)}")
        for n in targets:
            for r in rig.apps(n):
                if short(r.state) == "UNAVAILABLE":
                    self.failed.add(f"{n}/{r.app}")

        self.alive = set(targets)
        deadline = time.monotonic() + self.args.start_timeout
        flagged = False
        while not self.stopping.is_set():
            for r in rig.take_rows():
                if r.node in targets and r.app != "node_agent":
                    changed.add(r.node)
                    self.print_row(r)
            self.watch_rest(rig, ext_topics, targets)
            now = time.monotonic()
            for n, (cid, when, resent) in list(sent.items()):     # U11 step 3: once, same cmd_id
                if not resent and n not in changed and now - when >= RETRY_AFTER:
                    rig.send(n, "CMD_START_APP", cid)
                    sent[n] = (cid, when, True)
            if not flagged and now >= deadline and not running:
                flagged = True
                late = [f"{n}/{r.app} ({short(r.state)}{': ' + r.detail if r.detail else ''})"
                        for n in targets for r in rig.apps(n) if short(r.state) != "RUNNING"]
                for item in late:
                    self.failed.add(item.split(" ")[0])
                if late:
                    say(f"not RUNNING after {self.args.start_timeout:g} s: {'; '.join(late)}")
                else:
                    say("every app is RUNNING")
            time.sleep(POLL)
        return self.stop_all(rig, targets)

    def wait_for_agents(self, rig: Rig, domain: int, ext_topics) -> list[str] | None:
        first_seen = None
        next_note = time.monotonic() + FIRST_NOTE      # discovery takes a moment: don't cry wolf
        while not self.stopping.is_set():
            present = rig.agents() & set(self.managed)
            missing = [n for n in self.managed if n not in present]
            if not missing:
                return list(self.managed)
            if self.args.partial and present:
                first_seen = first_seen or time.monotonic()
                if time.monotonic() - first_seen >= PARTIAL_SETTLE:
                    say(f"--partial: going ahead without {', '.join(missing)}")
                    return [n for n in self.managed if n in present]
            if time.monotonic() >= next_note:
                say(f"waiting for the agents of: {', '.join(missing)} (not started, unreachable, or running a "
                    f"scenario on another domain; this run uses domain {domain})")
                next_note = time.monotonic() + WAIT_NOTE
            self.watch_ext(rig, ext_topics)
            time.sleep(POLL)
        return None

    def check_scenarios(self, rig: Rig, targets: list[str]) -> list[tuple[str, str]]:
        end = time.monotonic() + SCENARIO_WAIT
        while time.monotonic() < end and any(rig.scenario_of(n) is None for n in targets):
            rig.take_rows()
            time.sleep(POLL)
        return [(n, rig.scenario_of(n)) for n in targets
                if rig.scenario_of(n) not in (None, self.scenario)]

    def wait_match(self, rig: Rig, node: str) -> None:
        end = time.monotonic() + MATCH_WAIT
        while time.monotonic() < end and not rig.agent_matched(node) and not self.stopping.is_set():
            time.sleep(POLL)

    def show_node(self, rig: Rig, node: str) -> None:
        rows = rig.apps(node)
        say(f"{node}: agent ready" + ("" if rows else "; no apps"))
        width = max((len(r.app) for r in rows), default=0)
        for r in rows:
            print(f"    {r.app:<{width}}  {short(r.state):<11}  {r.detail}".rstrip(), flush=True)

    def print_row(self, r) -> None:
        state = short(r.state)
        if state == "CRASHED":
            self.failed.add(f"{r.node}/{r.app}")
        say(f"{r.node}/{r.app} {state}" + (f": {r.detail}" if r.detail else ""))

    def watch_ext(self, rig: Rig, ext_topics) -> None:
        for n, topics in ext_topics.items():
            if n not in self.ext_seen and any(rig.external_seen(t) for t in topics):
                self.ext_seen.add(n)
                say(f"{n}: its data is on the network")

    def watch_rest(self, rig: Rig, ext_topics, targets: list[str]) -> None:
        for line in rig.take_alerts():
            say(line)
        now_alive = rig.agents() & set(targets)
        for n in sorted(self.alive - now_alive):
            say(f"{n} agent LOST (no heartbeat): its apps' last states can't be trusted until it is back")
        for n in sorted(now_alive - self.alive):
            say(f"{n} agent back")
        self.alive = now_alive
        self.watch_ext(rig, ext_topics)

    def stop_all(self, rig: Rig, targets: list[str]) -> int:
        say("stopping every app (Ctrl-C again to leave at once) ...")
        here = [n for n in targets if n in rig.agents()]
        for n in here:
            rig.send(n, "CMD_STOP_APP", new_cmd_id())
        end = time.monotonic() + STOP_WAIT
        while time.monotonic() < end:
            for r in rig.take_rows():
                if r.node in targets and r.app != "node_agent":
                    self.print_row(r)
            if all(short(r.state) in ENDED for n in here for r in rig.apps(n)):
                break
            time.sleep(POLL)
        for n in targets:
            for r in rig.apps(n):
                state = short(r.state)
                if state not in ENDED:
                    self.failed.add(f"{n}/{r.app}")
                elif state == "KILLED" and r.detail.startswith("forced"):
                    self.failed.add(f"{n}/{r.app}")
        lines = [f"{n}/{r.app} {short(r.state)}" for n in targets for r in rig.apps(n)]
        say("how each app ended: " + (", ".join(lines) if lines else "no apps") + "; the agents stay up")
        if self.failed:
            say(f"failed: {', '.join(sorted(self.failed))}")
        return 1 if self.failed else 0


def main(args, data: dict) -> int:
    try:
        return Live(args, data).run()
    except RunError as e:
        print(f"protorig run: {e}", flush=True)
        return 2
