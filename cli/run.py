"""
run.py — `protorig run`: start a scenario, one node of it, or one app (U1-U10).

    protorig run [--scenario S] [--domain N] --app <app> [its own arguments]
                                                 one app on this machine, to try it (U1)
    protorig run <scenario> --sim                whole scenario on this machine, twins included (U2, U3)
    protorig run <scenario> --node <node>        this machine's part of the real rig (U4, U5)
    add --dry-run to see what would start, --domain N to override the domain

Every app is run by fw.supervise.Supervised, the same code the tests use (U7):
it stops by itself if `run` dies (N6); a crash is reported and the rest keep
running (decision 2). QoS-variant switches are the node agent's job (N13), so
an app started by `run` ignores them. Apps that don't exist or aren't built yet are listed and
skipped (decisions 1 and 3). Ctrl-C stops everything politely (U9).
"""
from __future__ import annotations

import os
import shlex
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import discovery
import repo

sys.path.insert(0, str(repo.ROOT / "libs" / "py"))
from fw.app import NODE_QOS_ENV            # noqa: E402
from fw.supervise import EXIT_KILLED, Supervised   # noqa: E402

STOP_GRACE = 10.0      # seconds apps get to stop politely before they are forced


@dataclass
class Launch:
    """One app to start (or a reason it can't be)."""
    node: str
    app: str
    args: list[str] = field(default_factory=list)
    cmd: list[str] | None = None       # None = can't start: see `missing`
    missing: str = ""

    @property
    def who(self) -> str:
        return f"{self.node}/{self.app}"


class RunError(Exception):
    """A request `run` refuses, with a message saying why and what to do."""


# ------------------------------------------------------------------ planning

def command_for(app: repo.App) -> tuple[list[str] | None, str]:
    """How to start an app, or why it can't be started yet."""
    if app.language == "python":
        return [sys.executable, str(app.path / "main.py")], ""
    if app.language == "c/c++":
        return None, "C/C++ app, not built yet (needs `protorig build`)"
    return None, "no main.py or CMakeLists.txt in its folder"


def resolve(scenario: str | None, name: str, node: str, args: list[str], want_sim: bool = False) -> Launch:
    """A Launch for app `name` as seen from `scenario` (its own apps first, then shared)."""
    hits = repo.find_apps(scenario).get(name) or []
    if not hits:
        return Launch(node, name, args, missing="no such app yet (create it with `protorig new app`)")
    app = hits[0]
    if want_sim and app.kind != "sim":
        return Launch(node, name, args, missing=f"is in apps/{app.kind}/, but a twin must be in apps/sim/")
    cmd, why = command_for(app)
    return Launch(node, name, args, cmd=cmd, missing=why)


def load_scenario(name: str) -> dict:
    s = next((s for s in repo.load_scenarios() if s.name == name), None)
    if s is None:
        known = ", ".join(x.name for x in repo.load_scenarios()) or "none"
        raise RunError(f"no scenario '{name}' (known: {known})")
    if s.error:
        raise RunError(f"scenario '{name}': {s.error} (run `protorig check`)")
    nodes = s.data.get("nodes")
    if not isinstance(nodes, dict) or not nodes or not all(isinstance(v, dict) for v in nodes.values()):
        raise RunError(f"scenario '{name}': nodes: must map each node to its settings (run `protorig check`)")
    return s.data


def entries(spec: dict, node: str) -> list[tuple[str, list[str]]]:
    """The (app, args) pairs a node's run: list asks for (U6)."""
    out = []
    run = spec.get("run") or []
    if not isinstance(run, list):
        raise RunError(f"node '{node}': run: must be a list (run `protorig check`)")
    for item in run:
        try:
            parts = shlex.split(str(item))
        except ValueError as e:
            raise RunError(f"node '{node}': can't parse run entry {item!r}: {e}")
        if parts:
            out.append((parts[0], parts[1:]))
    return out


def plan_sim(scenario: str, data: dict) -> list[Launch]:
    """U2: every managed node's apps, plus each external node's sim twin."""
    plan = []
    for node, spec in data["nodes"].items():
        if spec.get("external"):
            twin = spec.get("sim")
            plan.append(resolve(scenario, twin, node, [], want_sim=True) if twin
                        else Launch(node, "(external)", missing="external node with no sim: twin; nothing stands in for it"))
        else:
            plan += [resolve(scenario, app, node, args) for app, args in entries(spec, node)]
    return plan


def plan_node(scenario: str, data: dict, node: str) -> list[Launch]:
    """U4/U5: this node's apps, after checking this machine really is that node."""
    nodes = data["nodes"]
    if node not in nodes:
        raise RunError(f"scenario '{scenario}' has no node '{node}' (nodes: {', '.join(nodes)})")
    spec = nodes[node]
    if spec.get("external"):
        raise RunError(f"'{node}' is an external node: nothing runs there from this repo. "
                       f"Use --sim to run its twin instead")
    ip = spec.get("ip")
    if not ip or not discovery.is_local_ip(str(ip)):
        raise RunError(f"this machine doesn't have {ip}, the IP scenario '{scenario}' gives node '{node}'. "
                       "Run this on that machine, or fix the IP in scenario.yaml. (Starting anyway would "
                       "discover nothing, silently.)")
    return [resolve(scenario, app, node, args) for app, args in entries(spec, node)]


# ------------------------------------------------------------------ running

# Connext's evaluation-license banner, printed by every app at start-up: shown once per run.
BANNER = ("[RTI LICENSE]", "Expires on", "Please contact support@rti.com")


class Console:
    """One console for every app's output, each line prefixed with node/app (U8).
    A lock keeps lines from different apps from interleaving mid-line."""

    def __init__(self, width: int):
        self.width = width
        self._lock = threading.Lock()
        self._banner_seen: set[str] = set()

    def line(self, who: str, text: str):
        with self._lock:
            if text.startswith(BANNER):
                if text in self._banner_seen:
                    return
                self._banner_seen.add(text)
            print(f"{who:<{self.width}} | {text}", flush=True)

    def run(self, text: str):
        self.line("run", text)


def describe_exit(code: int | None) -> str:
    if code == 0:
        return "stopped (exit 0)"
    if code == EXIT_KILLED:
        return f"killed (exit {code})"
    return f"CRASHED (exit {code})"


def start_all(plan: list[Launch], common: list[str], env: dict, console: Console) -> dict[str, Supervised]:
    running = {}
    for l in plan:
        if l.cmd is None:
            continue
        running[l.who] = Supervised(
            l.cmd, ["--node", l.node, *common, *l.args], env,
            on_line=lambda text, who=l.who: console.line(who, text))
        console.run(f"started {l.who}")
    return running


def supervise_until_done(running: dict[str, Supervised], console: Console) -> int:
    """Wait until every app has ended or Ctrl-C (U9). A crash is reported loudly
    and the others keep running (decision 2). Returns the exit code for `run`."""
    stop = threading.Event()

    def on_signal(*_):
        stop.set()
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            try:
                signal.signal(sig, on_signal)
            except (ValueError, OSError):
                pass

    reported: set[str] = set()
    failed = False
    while not stop.is_set():
        for who, s in running.items():
            if who not in reported and s.exited():
                reported.add(who)
                console.run(f"{who} {describe_exit(s.returncode)}"
                            + ("; the other apps keep running" if s.returncode != 0 and len(reported) < len(running) else ""))
                failed |= s.returncode != 0
        if len(reported) == len(running):
            break
        stop.wait(0.2)

    if stop.is_set():
        console.run("stopping every app ...")
        for s in running.values():
            s.interrupt()
        deadline = time.monotonic() + STOP_GRACE
        for who, s in running.items():
            left = max(0.1, deadline - time.monotonic())
            if not s.ended_within(left):
                console.run(f"{who} didn't stop within {STOP_GRACE:g} s: forcing it")
                s.terminate(grace=0.1)
            if who not in reported:
                reported.add(who)
                code = s.returncode
                console.run(f"{who} {describe_exit(code)}")
                failed |= code not in (0, None) and not _stopped_by_signal(code)
    return 1 if failed else 0


def _stopped_by_signal(code: int) -> bool:
    """A negative code (POSIX) or Ctrl-Break's exit code (Windows) after we asked it to stop."""
    return code < 0 or (os.name == "nt" and code in (0xC000013A, -1073741510))


# ------------------------------------------------------------------ entry point

def main(args, extra: list[str], app_args: list[str] | None = None) -> int:
    """extra: unrecognised arguments before --app (an error); app_args: everything
    after `--app <name>`, passed to the app unchanged."""
    try:
        return _main(args, extra, app_args or [])
    except RunError as e:
        print(f"protorig run: {e}")
        return 1


def _main(args, extra: list[str], app_args: list[str]) -> int:
    modes = sum(bool(x) for x in (args.app, args.sim, args.node))
    if modes != 1:
        raise RunError("choose one: --app <app>, <scenario> --sim, or <scenario> --node <node>")
    if args.app and args.sim:
        raise RunError("--app runs one app; --sim runs a scenario")
    if extra:
        raise RunError(f"unknown argument(s): {' '.join(extra)} (run's options go before --app <name>)")
    if (args.sim or args.node) and not args.scenario:
        raise RunError("which scenario? e.g. protorig run my-demo --sim")

    common: list[str] = []
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo.ROOT / "libs" / "py") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env.pop(NODE_QOS_ENV, None)
    settings_text, settings_path = None, None

    if args.app:                                                       # U1
        scenario = args.scenario
        if scenario:
            load_scenario(scenario)
            common += ["--scenario", scenario]
        plan = [resolve(scenario, args.app, "local", list(app_args))]
        what = f"app {args.app}"
    else:
        data = load_scenario(args.scenario)
        common += ["--scenario", args.scenario]
        if args.sim:                                                   # U2, U3
            plan = plan_sim(args.scenario, data)
            settings_text = discovery.sim_qos()
            settings_path = repo.ROOT / "build" / args.scenario / "_sim" / "node_qos.xml"
            what = f"{args.scenario} --sim (this machine only)"
        else:                                                          # U4, U5
            plan = plan_node(args.scenario, data, args.node)
            settings_text = discovery.node_qos(data["nodes"], args.node)
            settings_path = repo.ROOT / "build" / args.scenario / args.node / "node_qos.xml"
            what = f"{args.scenario} --node {args.node}"
    if args.domain is not None:
        common += ["--domain", str(args.domain)]

    startable = [l for l in plan if l.cmd is not None]
    missing = [l for l in plan if l.cmd is None]

    if args.dry_run:                                                   # U10
        print(f"protorig run {what}: would start {len(startable)} app(s)")
        for l in startable:
            print(f"  {l.who}: {' '.join(shlex.quote(c) for c in l.cmd + ['--node', l.node, *common, *l.args])}")
        for l in missing:
            print(f"  {l.who}: SKIPPED, {l.missing}")
        if settings_text:
            print(f"discovery settings ({settings_path.relative_to(repo.ROOT).as_posix()}):")
            print(settings_text)
        return 0

    for l in missing:                                                  # decisions 1 and 3
        print(f"protorig run: skipping {l.who}: {l.missing}")
    if not startable:
        print("protorig run: nothing to start")
        return 1
    if settings_text:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(settings_text, encoding="utf-8")
        env[NODE_QOS_ENV] = str(settings_path)

    console = Console(max(len("run"), *(len(l.who) for l in startable)))
    console.run(f"{what}: starting {len(startable)} app(s); Ctrl-C stops them all")
    running = start_all(startable, common, env, console)
    return supervise_until_done(running, console)
