"""
control.py — node_agent's decisions, with no DDS and no processes.

main.py feeds it events (a command arrived, an app printed "running", a
heartbeat, an app exited, time passed) and carries out the actions it returns
(start, stop or kill a process, publish a row, raise or clear an alert, log).
Keeping the rules here makes every one of them testable and fuzzable in
milliseconds (test_control.py). Behaviour rows B1-B27: README.md.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

# Commands and states by name, so this file needs no DDS types.
START, STOP, KILL, VARIANT, PARAM = "CMD_START_APP", "CMD_STOP_APP", "CMD_KILL_APP", "CMD_SET_QOS_VARIANT", "CMD_SET_PARAM"
NOT_RUNNING, UNAVAILABLE, STARTING, RUNNING, RESTARTING = "APP_NOT_RUNNING", "APP_UNAVAILABLE", "APP_STARTING", "APP_RUNNING", "APP_RESTARTING"
STOPPING, STOPPED, KILLED, CRASHED = "APP_STOPPING", "APP_STOPPED", "APP_KILLED", "APP_CRASHED"
WARNING, CRITICAL = "SEVERITY_WARNING", "SEVERITY_CRITICAL"

ALIVE = {STARTING, RUNNING, RESTARTING, STOPPING}      # a process exists
ENDED = {NOT_RUNNING, STOPPED, KILLED, CRASHED}         # START may start it
APP_NAME = re.compile(r"^[a-z][a-z0-9_]*$")             # same rule as `protorig check`
AGENT = "node_agent"
DETAIL_BYTES = 128                                      # AppState.detail is string<128> (UTF-8 bytes)
ALERT_KINDS = ("crash", "hang", "var", "start")         # alert id = "<kind>:<app>" (N7); prefixes of at most
                                                        # 6 bytes + a 26-character app name fit Alert.alert_id (32)
SEEN_IDS = 1000                                         # commands remembered (N4)


def fit(text: str, max_bytes: int = DETAIL_BYTES) -> str:
    """N12 rule 5: at most max_bytes of UTF-8, cut at a character boundary with
    "…". Connext refuses an over-long string outright (the write fails), so a
    row with an untrimmed crash line would never be published."""
    text = " ".join(str(text).replace("\x00", "").split())       # one line
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    budget = max_bytes - len("…".encode("utf-8"))
    out = text.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return out + "…"


# ---------------------------------------------------------------- actions

@dataclass
class Spawn:            # start the app's process with these extra arguments
    app: str
    args: list[str]


@dataclass
class Interrupt:        # polite stop (Ctrl-C / Ctrl-Break)
    app: str


@dataclass
class Kill:             # forced stop, at once
    app: str


@dataclass
class Publish:          # publish the app's row as it is now
    app: str


@dataclass
class RaiseAlert:
    alert_id: str
    severity: str
    message: str


@dataclass
class ClearAlert:
    alert_id: str


@dataclass
class Log:
    level: str
    text: str


# ---------------------------------------------------------------- per-app record

@dataclass
class AppRec:
    name: str
    args: list[str]                       # from the run: entry (N5)
    available: bool = True
    missing: str = ""
    state: str = NOT_RUNNING
    exit_code: int = 0
    restarts: int = 0
    detail: str = ""
    changed_at: float = 0.0
    # supervision
    started_at: float = 0.0
    beat_seen: bool = False               # has this run heartbeated?
    heartbeats: bool = False              # has it EVER heartbeated (a heartbeat app)?
    last_beat: float = 0.0
    hung: bool = False
    start_warned: bool = False
    deadline: float | None = None         # stop grace or switch timeout
    end_reason: str = ""                  # what the next exit means: stop | kill | forced | switch | failed | ""
    fail_why: str = ""                    # why a switch is being abandoned (end_reason "failed")
    ask_by: str = ""                      # sender of the stop/kill/switch
    # variants (N13)
    variant: str = ""                     # in effect for the current/last run
    phase: str = "normal"                 # normal | switching | rollback
    target: str = ""                      # variant being started (switching/rollback)
    previous: str = ""                    # variant to roll back to


@dataclass
class Settings:
    stop_grace: float = 10.0
    start_timeout: float = 15.0
    fail_window: float = 5.0
    hang_after: float = 3.0               # from the heartbeat topic's QoS (N7)


class Controller:
    def __init__(self, node: str, entries: list[tuple[str, list[str], bool, str]],
                 known_variants: set[str], settings: Settings, now: float):
        """entries: (app, args, available, why-not) from the node's run: list,
        read once (N5)."""
        self.node, self.s = node, settings
        self.known = set(known_variants) | {""}
        self.apps: dict[str, AppRec] = {}
        for name, args, available, missing in entries:
            if name == AGENT or name in self.apps:
                continue                  # `check` reports these; never supervise ourselves
            self.apps[name] = AppRec(name, list(args), available, missing, changed_at=now,
                                     state=NOT_RUNNING if available else UNAVAILABLE,
                                     detail="" if available else fit(missing))
        self._seen: deque[int] = deque()
        self._seen_set: set[int] = set()

    # ------------------------------------------------------------ helpers

    def _set(self, a: AppRec, state: str, now: float, detail: str | None = None, out=None):
        a.state, a.changed_at = state, now
        if detail is not None:
            a.detail = fit(detail)
        out.append(Publish(a.name))

    @staticmethod
    def _alert_id(kind: str, app: str) -> str:
        return f"{kind}:{app}"

    def startup(self) -> list:
        """B1: every row published, stale alerts of every app cleared."""
        out = []
        for a in self.apps.values():
            out += [ClearAlert(self._alert_id(k, a.name)) for k in ALERT_KINDS]
            out.append(Publish(a.name))
        return out

    def set_available(self, app: str, available: bool, missing: str, now: float) -> list:
        """B4: availability is rechecked at each START (main re-resolves the app)."""
        a, out = self.apps.get(app), []
        if a is None or a.state not in ENDED | {UNAVAILABLE}:
            return out
        a.available, a.missing = available, missing
        if not available and a.state != UNAVAILABLE:
            self._set(a, UNAVAILABLE, now, missing, out)
        elif available and a.state == UNAVAILABLE:
            self._set(a, NOT_RUNNING, now, "", out)
        return out

    # ------------------------------------------------------------ commands (N4, N5, N11)

    def command(self, target_node: str, target_app: str, cmd_id: int, command: str,
                arg: str, sender: str | None, now: float) -> list:
        """One DemoControl sample. sender: "node/app", or None if unknown (N11 C)."""
        out: list = []
        if target_node not in ("*", self.node):
            return out                                        # B8: another node's
        if command == PARAM:
            return out                                        # B8: the app's job (N14)
        if cmd_id in self._seen_set:
            return out                                        # B7
        self._seen.append(cmd_id)
        self._seen_set.add(cmd_id)
        if len(self._seen) > SEEN_IDS:
            self._seen_set.discard(self._seen.popleft())
        who = sender or "unknown"
        what = f"{command.removeprefix('CMD_').lower()} {target_app!r} from {who}"

        def refuse(why):
            out.append(Log("WARN", f"refused {what}: {why}"))
            return out

        if command not in (START, STOP, KILL, VARIANT):
            return refuse("not a node agent command")
        if target_app == AGENT:
            return refuse("the agent never acts on itself")                       # B9
        if target_app == "*":
            if command == VARIANT:
                return refuse("a variant fits one app's topics: name the app")   # B18
            names = list(self.apps)
        elif not APP_NAME.match(target_app or ""):
            return refuse("not a plain app name")                                 # B9
        elif target_app not in self.apps:
            return refuse(f"not in node {self.node}'s run: list")                 # B9
        else:
            names = [target_app]

        mine = sender.split("/", 1)[1] if sender and sender.startswith(self.node + "/") else None
        if mine in names and command in (STOP, KILL, VARIANT):                    # N11: never the sender
            out.append(Log("INFO", f"not {'switching' if command == VARIANT else 'stopping'} {mine}: "
                                   f"it sent the command"))
            names = [n for n in names if n != mine]
            if command == VARIANT:
                return out
        out.append(Log("INFO", f"accepted {what}" + (f" {arg!r}" if command == VARIANT else "")))
        for n in names:
            a = self.apps[n]
            if command == START:
                out += self._start(a, now)
            elif command == STOP:
                out += self._stop(a, who, now)
            elif command == KILL:
                out += self._kill(a, who, now)
            else:
                out += self._switch(a, arg, who, now)
        return out

    def _start(self, a: AppRec, now: float) -> list:
        out: list = []
        if a.state in (STARTING, RUNNING, RESTARTING, STOPPING):
            out.append(Log("INFO", f"{a.name} already running ({a.state.removeprefix('APP_').lower()})"))  # B3
            return out
        if not a.available:
            out.append(Log("WARN", f"can't start {a.name}: {a.missing}"))                               # B4
            return out
        out += [ClearAlert(self._alert_id(k, a.name)) for k in ALERT_KINDS]                             # B2
        a.restarts, a.exit_code, a.variant, a.phase = 0, 0, _variant_in(a.args), "normal"
        out += self._launch(a, list(a.args), now)
        self._set(a, STARTING, now, "", out)
        return out

    def _launch(self, a: AppRec, args: list[str], now: float) -> list:
        a.started_at, a.beat_seen, a.hung, a.start_warned = now, False, False, False
        a.deadline, a.end_reason, a.ask_by = None, "", ""
        return [Spawn(a.name, args)]

    def _stop(self, a: AppRec, who: str, now: float) -> list:
        out: list = []
        if a.state not in (STARTING, RUNNING, RESTARTING):
            if a.state == STOPPING:
                out.append(Log("INFO", f"{a.name} is already stopping"))
            return out
        # B5: a switch under way is cancelled: "stop" decides what the exit means,
        # and the next START resets the switch's phase.
        a.end_reason, a.ask_by, a.deadline = "stop", who, now + self.s.stop_grace
        out.append(Interrupt(a.name))
        self._set(a, STOPPING, now, f"stopping, asked by {who}", out)
        return out

    def _kill(self, a: AppRec, who: str, now: float) -> list:
        if a.state not in ALIVE:
            return []
        a.end_reason, a.ask_by, a.deadline = "kill", who, None                   # B6: at once, even mid-stop or mid-switch
        return [Kill(a.name)]

    def _switch(self, a: AppRec, variant: str, who: str, now: float) -> list:
        out: list = []
        if a.state != RUNNING:
            out.append(Log("WARN", f"refused variant switch of {a.name}: it is "
                                   f"{a.state.removeprefix('APP_').lower()}, not running"))       # B18
            return out
        if variant == a.variant:
            out.append(Log("INFO", f"{a.name} already uses QoS variant {variant or 'default'!r}"))  # B18
            return out
        if variant not in self.known:
            msg = f"unknown QoS variant {variant!r}: not a profile in the QoS files"
            out.append(Log("WARN", f"refused variant switch of {a.name}: {msg}"))
            out.append(RaiseAlert(self._alert_id("var", a.name), WARNING, fit(f"{a.name}: {msg}")))
            return out
        a.phase, a.target, a.previous = "switching", variant, a.variant                       # B17
        a.end_reason, a.ask_by, a.deadline = "switch", who, now + self.s.stop_grace
        out.append(Interrupt(a.name))
        self._set(a, RESTARTING, now, f"switching to {variant or 'default'}, by {who}", out)
        return out

    # ------------------------------------------------------------ what the apps do

    def running_line(self, app: str, now: float) -> list:
        """The app printed fw.App's "running" line. Enough for RUNNING only for an
        app that has never heartbeated (B2); a switch waits for its heartbeat (V4)."""
        a, out = self.apps.get(app), []
        if a and a.state == STARTING and not a.heartbeats:
            out += self._now_running(a, now)
        return out

    def heartbeat(self, app: str, variant: str, now: float) -> list:
        a, out = self.apps.get(app), []
        if a is None or a.state not in ALIVE:
            return out                    # a namesake started by hand, or a late beat after exit
        a.beat_seen, a.heartbeats, a.last_beat = True, True, now
        if a.state == STARTING:
            out += self._now_running(a, now)
        elif a.state == RESTARTING and a.phase in ("switching", "rollback") and a.end_reason == "":
            if variant == a.target:                                              # V4: confirmed
                rolled_back = a.phase == "rollback"
                a.variant, a.phase = a.target, "normal"
                out += self._now_running(a, now, f"rolled back to {a.target or 'default'}" if rolled_back else "")
            else:                                                                # V4: another variant
                out += self._abandon(a, f"reported variant {variant or 'default'!r}")
        elif a.hung:                                                             # B15: recovered
            a.hung = False
            out.append(ClearAlert(self._alert_id("hang", a.name)))
            self._set(a, a.state, now, "", out)
        return out

    def _now_running(self, a: AppRec, now: float, detail: str = "") -> list:
        out: list = []
        if a.start_warned:
            out.append(ClearAlert(self._alert_id("start", a.name)))
        a.start_warned, a.deadline = False, None
        self._set(a, RUNNING, now, detail, out)
        return out

    def exited(self, app: str, code: int, last_line: str, now: float) -> list:
        a, out = self.apps.get(app), []
        if a is None or a.state not in ALIVE:
            return out
        reason, who = a.end_reason, a.ask_by
        a.exit_code, a.deadline = code, None
        if reason == "kill":                                                     # B6
            a.phase = "normal"
            out.append(RaiseAlert(self._alert_id("crash", a.name), CRITICAL,
                                  fit(f"{a.name}: simulated crash, killed by {who}")))
            self._set(a, KILLED, now, f"killed by {who}", out)
        elif reason == "forced":                                                 # B5
            a.phase = "normal"
            self._set(a, KILLED, now, f"forced after {self.s.stop_grace:g} s", out)
        elif reason == "stop":                                                   # B5
            self._set(a, STOPPED, now, f"stopped by {who}", out)
        elif reason == "failed":                                                 # V4: we killed a run that didn't report in
            out += self._switch_failed(a, a.fail_why, now)
        elif reason == "switch":                                                 # V3: old variant ended
            a.restarts += 1
            out += self._launch(a, _with_variant(a.args, a.target), now)
            self._set(a, RESTARTING, now, f"switching to {a.target or 'default'}, by {who}", out)
        elif a.phase in ("switching", "rollback"):                               # V4: the new run failed
            out += self._switch_failed(a, f"exited with code {code}", now)
        elif code == 0:                                                          # B14
            out.append(RaiseAlert(self._alert_id("crash", a.name), WARNING,
                                  fit(f"{a.name} ended on its own (exit 0): {last_line}")))
            self._set(a, STOPPED, now, "ended on its own (exit 0)", out)
        else:                                                                    # B13
            out.append(RaiseAlert(self._alert_id("crash", a.name), CRITICAL,
                                  fit(f"{a.name} crashed (exit {code}): {last_line}")))
            self._set(a, CRASHED, now, last_line or f"exit {code}", out)
        return out

    def _switch_failed(self, a: AppRec, why: str, now: float) -> list:
        out: list = []
        if a.phase == "switching":                                               # V4: roll back once
            out.append(RaiseAlert(self._alert_id("var", a.name), WARNING,               # outcome first: may be trimmed
                                  fit(f"{a.name} rolled back to {a.previous or 'default'}: "
                                      f"{a.target or 'default'} failed ({why})")))
            a.phase, a.target, a.restarts = "rollback", a.previous, a.restarts + 1
            out += self._launch(a, _with_variant(a.args, a.previous), now)
            self._set(a, RESTARTING, now, f"rolling back to {a.previous or 'default'}", out)
        else:                                                                    # the rollback failed too
            a.phase = "normal"
            out.append(RaiseAlert(self._alert_id("crash", a.name), CRITICAL,
                                  fit(f"{a.name} is down: the rollback failed too ({why})")))
            self._set(a, CRASHED, now, f"rollback failed ({why})", out)
        return out

    # ------------------------------------------------------------ time

    def _abandon(self, a: AppRec, why: str) -> list:
        """V4: the new run is not a success: kill it; its exit decides what next."""
        a.end_reason, a.fail_why, a.deadline = "failed", why, None
        return [Kill(a.name)]

    def tick(self, now: float) -> list:
        out: list = []
        for a in self.apps.values():
            if a.state not in ALIVE:
                continue
            if a.deadline is not None and now >= a.deadline:                         # B5, V3: grace is over
                a.deadline = None
                if a.end_reason == "stop":
                    a.end_reason = "forced"
                out.append(Kill(a.name))            # a forced old run still ends the switch's first half
                continue
            if a.phase in ("switching", "rollback") and a.end_reason == "":          # V4: waiting for the new run
                if a.heartbeats and now - a.started_at > self.s.start_timeout:
                    out += self._abandon(a, f"no heartbeat on {a.target or 'default'!r} within "
                                            f"{self.s.start_timeout:g} s")
                elif not a.heartbeats and now - a.started_at > self.s.fail_window:
                    rolled_back = a.phase == "rollback"                              # survived: success
                    a.variant, a.phase = a.target, "normal"
                    out += self._now_running(a, now, f"rolled back to {a.target or 'default'}" if rolled_back else "")
                continue
            if a.state == STARTING and not a.start_warned and now - a.started_at > self.s.start_timeout:
                a.start_warned = True                                                # B16
                out.append(RaiseAlert(self._alert_id("start", a.name), WARNING,
                                      fit(f"{a.name} not running after {self.s.start_timeout:g} s")))
            if a.state == RUNNING and a.beat_seen and not a.hung and now - a.last_beat > self.s.hang_after:
                a.hung = True                                                        # B15
                out.append(RaiseAlert(self._alert_id("hang", a.name), WARNING,
                                      fit(f"{a.name} not responding (no heartbeat for {self.s.hang_after:g} s)")))
                self._set(a, RUNNING, now, "not responding", out)
        return out

    # ------------------------------------------------------------ shutdown (B26)

    def shutdown(self) -> list:
        """Every app asked to stop; main waits stop-grace, then kills the rest."""
        return [Interrupt(a.name) for a in self.apps.values() if a.state in ALIVE]

    def clear_all_alerts(self) -> list:
        return [ClearAlert(self._alert_id(k, a)) for a in self.apps for k in ALERT_KINDS]


def _variant_in(args: list[str]) -> str:
    """The --qos-variant a run: entry already sets, or "" (default)."""
    for i, x in enumerate(args):
        if x == "--qos-variant" and i + 1 < len(args):
            return args[i + 1]
        if x.startswith("--qos-variant="):
            return x.split("=", 1)[1]
    return ""


def _with_variant(args: list[str], variant: str) -> list[str]:
    """N13 V3: the run: entry's arguments, --qos-variant replaced."""
    from fw.supervise import with_variant
    return with_variant(args, variant)
