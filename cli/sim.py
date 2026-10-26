"""
sim.py — `protorig run <scenario> --sim` through node agents (run.md U2 as changed
by node_agent N2, U3).

The whole scenario on this PC, the way the rig runs it:
    1. refuses if agents for this scenario's nodes already run here (decision 1,
       4b): sim agents would meet them over shared memory, and one would quit
    2. starts one node agent per managed node, named after its node, and each
       external node's twin directly (a twin has no agent, like its device)
    3. uses the --live steps (live.py): waits for the agents, sends "start *",
       shows each app's state; Ctrl-C stops every app through its agent
    4. then stops the agents and the twins
Everything uses shared memory only (U3): nothing reaches the network.

Agents print their apps' lines already labelled "node/app | ...", so those are
passed through as they are (decision 3, 4b); twins' lines get run's label.
"""
from __future__ import annotations

import sys
import time

import agent as agentcmd
import discovery
import live as livemod
import repo
from run import BANNER, Console, Launch, RunError, describe_exit, resolve, stop_result

sys.path.insert(0, str(repo.ROOT / "libs" / "py"))
from fw.agentfiles import AgentFiles          # noqa: E402
from fw.app import NODE_QOS_ENV               # noqa: E402
from fw.supervise import Supervised           # noqa: E402

AGENT = "node_agent"
CHECK = 2.0             # seconds to listen for anything of this scenario already running here
STOP_GRACE = 10.0       # for agents and twins after their apps were stopped (agents need their own grace)


def plan(scenario: str, data: dict) -> tuple[list[Launch], list[Launch]]:
    """(agents, twins). An agent per managed node; a twin per external node with sim:."""
    agents, twins = [], []
    for node, spec in data["nodes"].items():
        if spec.get("external"):
            twin = spec.get("sim")
            twins.append(resolve(scenario, twin, node, [], want_sim=True) if twin
                         else Launch(node, "(external)", missing="external node with no sim: twin; nothing stands in for it"))
        else:
            agents.append(Launch(node, AGENT, cmd=[sys.executable, str(agentcmd.AGENT_MAIN)]))
    return agents, twins


class SimConsole(Console):
    """run's console, plus agents' lines passed through unlabelled. Connext's
    license banner, wherever it comes from, is shown once."""

    def raw(self, text: str) -> None:
        body = text.split(" | ", 1)[1] if " | " in text and "/" in text.split(" | ", 1)[0] else text
        if not body.strip():
            return                                    # the blank lines after Connext's banner
        with self._lock:
            if body.startswith(BANNER):
                if body in self._banner_seen:
                    return
                self._banner_seen.add(body)
            print(text, flush=True)


class SimLive(livemod.Live):
    """The --live steps, also watching the agent and twin processes started here."""

    def __init__(self, args, data, procs: dict[str, Supervised]):
        super().__init__(args, data, sim=True)
        self.procs = procs
        self.reported: set[str] = set()

    def tick(self) -> None:
        for who, p in self.procs.items():
            if who not in self.reported and p.exited():
                self.reported.add(who)
                self.failed.add(who)
                hint = ""
                if who.endswith("/" + AGENT):
                    if self.alive:                    # mid-demo: the other nodes carry on (B13)
                        hint = ". Its apps stopped with it; Ctrl-C and run again to bring it back"
                    else:
                        self.give_up = True           # at start: it won't come up, stop waiting
                livemod.say(f"{who} ended by itself: {describe_exit(p.returncode)}{hint}")
                if self.alive and not who.endswith("/" + AGENT):
                    self.rest_keeps_running()         # a twin gone mid-demo (B14)


def main(args, data: dict, common: list[str], env: dict) -> int:
    scenario = args.scenario
    agents, twins = plan(scenario, data)
    managed = [l.node for l in agents]
    domain = agentcmd.domain_of(args, data)

    # 1. Agents for this scenario already running on this PC (agent start, or another --sim).
    busy = []
    for n in managed:
        files = AgentFiles.of(repo.ROOT, scenario, n)
        rec = files.read_pid()
        ident = files.identity(rec)
        if ident == "unknown":
            busy.append(f"{n}: {files.unknown_hint(rec.pid)}")
        elif ident == "agent" and rec.launcher is not None:
            # Another --sim's agent. `agent stop` would refuse it (its node's IP
            # isn't this PC's); it stops with the run that started it.
            busy.append(f"{n} (process {rec.pid}): started by another `run {scenario} --sim` "
                        f"(process {rec.launcher}); press Ctrl-C there or close its window")
        elif ident == "agent":
            busy.append(f"{n} (process {rec.pid}): stop it with `protorig agent stop {scenario}`")
    if busy:
        raise RunError("agents for this scenario already run on this PC; sim agents would meet them "
                       "and one would quit:\n  " + "\n  ".join(busy))

    settings_text = discovery.sim_qos()
    settings_path = repo.ROOT / "build" / scenario / "_sim" / "node_qos.xml"
    startable = [l for l in agents + twins if l.cmd is not None]
    missing = [l for l in twins if l.cmd is None]

    if args.dry_run:                                                   # U10
        print(f"protorig run {scenario} --sim (this machine only): would start "
              f"{len(agents)} agent(s) and {len([t for t in twins if t.cmd])} twin(s), then send start * to each agent")
        for l in startable:
            print(f"  {l.who}: {' '.join(l.cmd + ['--node', l.node, *common, *l.args])}")
        for l in missing:
            print(f"  {l.who}: SKIPPED, {l.missing}")
        for n in managed:
            print(f"  {n}/{AGENT} will start: {will_start(scenario, n)}")
        print(f"discovery settings ({settings_path.relative_to(repo.ROOT).as_posix()}):")
        print(settings_text)
        return 0

    for l in missing:
        print(f"protorig run: skipping {l.who}: {l.missing}")
    if not agents and not startable:
        print("protorig run: nothing to start")
        return 1
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(settings_text, encoding="utf-8")
    env = dict(env)
    env[NODE_QOS_ENV] = str(settings_path)

    rig = agentcmd.open_listener(domain, settings_path, "sim/run", cls=livemod.Rig)
    procs: dict[str, Supervised] = {}
    try:
        # 1b. Anything for this scenario's nodes already on this PC and domain (an
        # agent of another scenario, an app or twin started by hand, a --live):
        # its data would mix with this run's, and a same-named agent would meet ours.
        def here() -> list[str]:
            return [p for p in rig.peers() if p.split("/", 1)[0] in data["nodes"]]
        if agentcmd.wait_for(lambda: bool(here()), CHECK):
            time.sleep(0.5)                               # let the rest be heard, for the message
            raise RunError(f"already running on this PC on domain {domain}: {', '.join(here())}. "
                           "Its data would mix with this run's. Stop it first (Ctrl-C the run that "
                           "started it, or `protorig agent stop <scenario>`), or use another "
                           "domain: --domain N")

        # 2. Agents and twins, each watching this run (N6): if run dies, they stop.
        console = SimConsole(max([len("run")] + [len(l.who) for l in twins if l.cmd]))
        console.run(f"{scenario} --sim (this machine only): starting {len(agents)} agent(s) and "
                    f"{len([t for t in twins if t.cmd])} twin(s); Ctrl-C stops everything")
        for l in startable:
            out = console.raw if l.app == AGENT else (lambda text, who=l.who: console.line(who, text))
            procs[l.who] = Supervised(l.cmd, ["--node", l.node, *common, *l.args], env, on_line=out)

        # 3. The --live steps.
        sim = SimLive(args, data, procs)
        sim.install_signals()
        code = sim.drive(rig, domain)
        failed = bool(sim.failed)
    finally:
        # 4. Agents (their apps are stopped already) and twins.
        stop_all(procs)
        rig.close()
    return 1 if (code != 0 or failed) else 0


def will_start(scenario: str, node: str) -> str:
    """What a node's agent will start, as the agent resolves it (fw.scenario)."""
    import shlex
    from fw import scenario as fw_scenario
    try:
        found = [e for e in fw_scenario.node_entries(repo.ROOT, scenario, node) if e.app != AGENT]
    except ValueError as e:
        return f"nothing ({e})"
    parts = [" ".join([e.app, *map(shlex.quote, e.args)]) + ("" if e.cmd else f" (UNAVAILABLE: {e.missing})")
             for e in found]
    return "; ".join(parts) or "no apps"


def stop_all(procs: dict[str, Supervised]) -> None:
    asked = time.monotonic()
    stopping = {who for who, p in procs.items() if not p.exited()}
    for who in stopping:
        procs[who].interrupt()
    end = asked + STOP_GRACE
    for who, p in procs.items():
        if who not in stopping:                       # ended by itself: its own exit code
            livemod.say(f"{who} {describe_exit(p.returncode)}")
            continue
        if not p.ended_within(max(0.1, end - time.monotonic())):
            livemod.say(f"{who} didn't stop within {STOP_GRACE:g} s: forcing it")
            p.terminate(grace=0.1)
        livemod.say(f"{who} {stop_result(p, asked)[0]}")
