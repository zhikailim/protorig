"""
node_agent — starts, stops and kills this machine's apps when asked over DDS,
and reports their state (node_agent requirements N1-N14; behaviour B1-B27 in
README.md).

Topics in : _sys/DemoControl (commands), _sys/NodeStatus (its apps' heartbeats,
            and any other agent claiming this node)
Topics out: _sys/NodeStatus (own heartbeat), _sys/AppState (one row per app),
            Alert (source "<node>/node_agent")
Arguments : --scenario and --node (required), --stop-grace, --start-timeout,
            --fail-window, plus the standard ones.

How it is built: control.py makes every decision; this file only turns DDS
samples and process events into calls to it, and carries out what it returns.
Everything runs on fw.App's one loop thread, except reading app output, which
uses each app's own thread and a bounded queue, so a noisy app never blocks.
"""
import os
import queue
import sys
import threading
import time
from pathlib import Path

import rti.connextdds as dds

from fw.app import App, ROOT, VARIANT_LIBRARY, log
from fw import scenario as fw_scenario
from fw import types as T
from fw.supervise import Supervised

sys.path.insert(0, str(Path(__file__).resolve().parent))
import control as C  # noqa: E402

BANNER = ("[RTI LICENSE]", "Expires on", "Please contact support@rti.com")   # Connext's: shown once (N8)
CONSOLE_LINES = 2000          # app lines waiting to be printed; beyond this they are dropped (N8)
PUMP_PERIOD = 0.05            # seconds between checks for exits and timeouts
SCENARIO_CHECK_PERIOD = 2.0


class Console:
    """N8: every app line, prefixed node/app, printed by one thread. Apps hand
    lines over without ever waiting; if printing falls behind, lines are dropped
    and counted, so neither an app nor the agent is slowed down."""

    def __init__(self, out=None):
        self._out = out or (lambda line: print(line, flush=True))
        self._q: queue.Queue = queue.Queue(maxsize=CONSOLE_LINES)
        self._dropped = 0
        self._lock = threading.Lock()
        self._banner: set[str] = set()
        threading.Thread(target=self._print, daemon=True).start()

    def put(self, who: str, text: str) -> None:
        try:
            self._q.put_nowait((who, text))
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def _print(self) -> None:
        while True:
            who, text = self._q.get()
            if text.startswith(BANNER):
                if text in self._banner:
                    continue
                self._banner.add(text)
            try:
                self._out(f"{who} | {text}")
                with self._lock:
                    dropped, self._dropped = self._dropped, 0
                if dropped:
                    self._out(f"{who} | ... {dropped} lines dropped (output too fast to show)")
            except (OSError, ValueError):
                pass                                  # our own console is gone: keep supervising


class Agent:
    def __init__(self):
        self.app = App("node_agent", "Starts, stops and kills this machine's apps when asked over DDS")
        a = self.app
        self.s = C.Settings(stop_grace=a.arg("--stop-grace", 10.0, "seconds to stop politely before forcing"),
                            start_timeout=a.arg("--start-timeout", 15.0, "seconds for a start or variant switch to report in"),
                            fail_window=a.arg("--fail-window", 5.0, "apps without a heartbeat: failing sooner = failed start"))
        if a._help_requested:
            return
        if not a.scenario or a.node == "local":
            log(a.who, "ERROR", "--scenario and --node are required (which scenario, and which node this machine is)")
            raise SystemExit(2)
        self.who = a.who
        self.scenario_file = ROOT / "scenarios" / a.scenario / "scenario.yaml"
        try:
            entries = fw_scenario.node_entries(ROOT, a.scenario, a.node)
        except ValueError as e:
            log(a.who, "ERROR", str(e))
            raise SystemExit(2)
        self.cmds = {e.app: e.cmd for e in entries}
        # N7: the hang threshold is the heartbeat topic's own liveliness lease.
        self.s.hang_after = a.provider.get_topic_datareader_qos("_sys/NodeStatus").liveliness.lease_duration.to_seconds()
        known = set(a.provider.qos_profiles(VARIANT_LIBRARY))
        self.ctl = C.Controller(a.node, [(e.app, e.args, e.cmd is not None, e.missing) for e in entries],
                                known, self.s, time.monotonic())
        self.state_w = a.writer("_sys/AppState")
        self.alert_w = a.writer("Alert")
        self.control_r = a.reader("_sys/DemoControl")
        self.status_r = a.reader("_sys/NodeStatus")
        a.on_data(self.control_r, self.on_command, with_info=True)
        a.on_data(self.status_r, self.on_status, with_info=True)
        a.every(PUMP_PERIOD, self.pump)
        a.every(SCENARIO_CHECK_PERIOD, self.check_scenario)
        a.on_stop(self.shutdown)
        self.console = Console()
        self.procs: dict[str, Supervised] = {}
        self.last_line: dict[str, str] = {}
        self.events: queue.Queue = queue.Queue()       # ("running", app) from output threads
        self.twin_warned = False
        self.scenario_mtime = self._mtime()
        self.scenario_changed = False
        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = str(ROOT / "libs" / "py") + os.pathsep + self.env.get("PYTHONPATH", "")
        self.env["PYTHONUNBUFFERED"] = "1"
        self.write_own_row()
        self.carry_out(self.ctl.startup())
        names = ", ".join(self.ctl.apps) or "none"
        log(self.who, "INFO", f"scenario {a.scenario}, node {a.node}: apps {names}; waiting for commands")

    # ------------------------------------------------------------ DDS in

    def sender_of(self, info) -> str | None:
        """N11: the command's writer, as "node/app", looked up at once (addition 2).
        None if unknown: gone already, or not named node/app."""
        try:
            name = self.control_r.matched_publication_participant_data(info.publication_handle).participant_name.name
        except dds.Error:
            return None
        return name if name.count("/") == 1 and all(name.split("/")) else None

    def on_command(self, c: T.DemoControl, info) -> None:
        sender = self.sender_of(info)
        cmd = c.command.name if hasattr(c.command, "name") else str(c.command)
        if cmd == C.START and c.target_node in ("*", self.app.node):     # B4: recheck availability
            for name in (self.ctl.apps if c.target_app == "*" else [c.target_app]):
                if name in self.ctl.apps:
                    self.refresh(name)
        self.carry_out(self.ctl.command(c.target_node, c.target_app, c.cmd_id, cmd, c.arg, sender,
                                        time.monotonic()))

    def refresh(self, name: str) -> None:
        hits = fw_scenario.find_apps(ROOT, self.app.scenario).get(name) or []
        cmd, why = fw_scenario.command_for(hits[0]) if hits else (None, "no such app (create it with `protorig new app`)")
        self.cmds[name] = cmd
        self.carry_out(self.ctl.set_available(name, cmd is not None, why, time.monotonic()))

    def on_status(self, s: T.NodeStatus, info) -> None:
        if info.publication_handle == self.app._status_w.instance_handle or s.node != self.app.node:
            return                                    # our own heartbeat, or another node's apps
        if s.app == "node_agent":
            self.other_agent(info)                    # B22
            return
        self.carry_out(self.ctl.heartbeat(s.app, s.qos_variant, time.monotonic()))

    def other_agent(self, info) -> None:
        """B22 (N12 rule 2): another agent claims this node. The one whose
        participant ID compares higher leaves, so exactly one stays."""
        try:
            key = self.status_r.matched_publication_participant_data(info.publication_handle).key.value
        except dds.Error:
            return
        theirs = "".join(f"{v:08x}" for v in key)
        mine = str(self.app.participant.instance_handle)
        if not self.twin_warned:
            self.twin_warned = True
            self.raise_alert("agents", "SEVERITY_CRITICAL",          # the node is in the source: <node>/node_agent
                             C.fit(f"two agents for {self.app.node}: only one may run"))
        if mine > theirs:
            log(self.who, "ERROR", f"another agent runs node {self.app.node}: this one leaves")
            self.app.stop("another agent for this node")

    # ------------------------------------------------------------ processes

    def on_line(self, name: str, text: str) -> None:
        """Called on the app's output thread: hand over, never wait."""
        if text.strip() and not text.startswith(BANNER):
            self.last_line[name] = text.strip()
        if text.endswith(" running"):
            self.events.put(("running", name))
        self.console.put(f"{self.app.node}/{name}", text)

    def pump(self) -> None:
        now = time.monotonic()
        while True:
            try:
                kind, name = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "running":
                self.carry_out(self.ctl.running_line(name, now))
        for name, p in list(self.procs.items()):
            if p.exited():
                del self.procs[name]
                self.carry_out(self.ctl.exited(name, p.returncode, self.last_line.get(name, ""), now))
        self.carry_out(self.ctl.tick(now))

    def check_scenario(self) -> None:
        """B23: the list was read once (N5); say so if the file changes."""
        if not self.scenario_changed and self._mtime() != self.scenario_mtime:
            self.scenario_changed = True
            log(self.who, "WARN", f"{self.scenario_file.name} changed: restart the agent to use it "
                                  "(still running the list it started with)")

    def _mtime(self):
        try:
            return self.scenario_file.stat().st_mtime_ns
        except OSError:
            return None

    # ------------------------------------------------------------ actions

    def carry_out(self, actions) -> None:
        for act in actions:
            if isinstance(act, C.Spawn):
                self.spawn(act.app, act.args)
            elif isinstance(act, C.Interrupt):
                if act.app in self.procs:
                    self.procs[act.app].interrupt()
            elif isinstance(act, C.Kill):
                if act.app in self.procs:
                    self.procs[act.app].kill()
            elif isinstance(act, C.Publish):
                self.write_row(act.app)
            elif isinstance(act, C.RaiseAlert):
                self.raise_alert(act.alert_id, act.severity, act.message)
            elif isinstance(act, C.ClearAlert):
                self.clear_alert(act.alert_id)
            elif isinstance(act, C.Log):
                log(self.who, act.level, act.text)

    def spawn(self, name: str, args: list[str]) -> None:
        a = self.app
        full = ["--node", a.node, "--scenario", a.scenario, "--domain", str(a.domain), *args]
        self.last_line.pop(name, None)
        try:
            self.procs[name] = Supervised(self.cmds[name], full, self.env,
                                          on_line=lambda text, n=name: self.on_line(n, text))
            log(self.who, "INFO", f"started {name} {' '.join(args)}".rstrip())
        except (OSError, TypeError) as e:             # e.g. Python or the app vanished since
            self.carry_out(self.ctl.exited(name, 127, f"couldn't start: {e}", time.monotonic()))

    def write_row(self, name: str) -> None:
        r = self.ctl.apps[name]
        self._write(self.state_w, T.AppState(node=self.app.node[:32], app=name[:32], state=T.AppStateKind[r.state],
                                             exit_code=int(max(-2**31, min(2**31 - 1, r.exit_code))),
                                             restarts=min(r.restarts, 2**32 - 1), detail=r.detail,
                                             scenario=C.fit(self.app.scenario, 64), changed_at_ns=time.time_ns()))

    def write_own_row(self) -> None:
        self._write(self.state_w, T.AppState(node=self.app.node[:32], app="node_agent",
                                             state=T.AppStateKind.APP_RUNNING,
                                             scenario=C.fit(self.app.scenario, 64), changed_at_ns=time.time_ns()))

    def raise_alert(self, alert_id: str, severity: str, message: str) -> None:
        self._write(self.alert_w, T.Alert(source=self.who[:64], alert_id=alert_id, severity=T.Severity[severity],
                                          message=C.fit(message), stamp_ns=time.time_ns()))

    def clear_alert(self, alert_id: str) -> None:
        try:
            h = self.alert_w.register_instance(T.Alert(source=self.who[:64], alert_id=alert_id))
            self.alert_w.dispose_instance(h)
        except dds.Error as e:
            log(self.who, "ERROR", f"couldn't clear alert {alert_id!r}: {e}")

    def _write(self, w, sample) -> None:
        """A write that can't be made (e.g. a field over its bound) is logged,
        never fatal: the agent keeps supervising."""
        try:
            w.write(sample)
        except Exception as e:                         # Connext raises several kinds here
            log(self.who, "ERROR", f"couldn't publish {type(sample).__name__}: {type(e).__name__}: {e}")

    # ------------------------------------------------------------ shutdown (B26)

    def shutdown(self) -> None:
        """Every app asked to stop, forced after stop-grace; then alerts cleared
        and rows disposed. DDS still works here (fw.App's on_stop)."""
        log(self.who, "INFO", "stopping every app ...")
        self.carry_out(self.ctl.shutdown())
        deadline = time.monotonic() + self.s.stop_grace
        for name, p in list(self.procs.items()):
            if not p.ended_within(max(0.1, deadline - time.monotonic())):
                log(self.who, "WARN", f"{name} didn't stop within {self.s.stop_grace:g} s: forcing it")
                p.kill()
                p.ended_within(5)
        self.carry_out(self.ctl.clear_all_alerts())
        for name in [*self.ctl.apps, "node_agent"]:
            try:
                self.state_w.dispose_instance(self.state_w.lookup_instance(T.AppState(node=self.app.node[:32], app=name)))
            except dds.Error:
                pass
        time.sleep(0.2)                                # let the disposals go out before DDS closes

    def run(self) -> int:
        return self.app.run()


if __name__ == "__main__":
    agent = Agent()
    sys.exit(agent.run())
