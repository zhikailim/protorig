"""
test_live.py — `protorig run <scenario> --live` (run.md U11, U12) with real node
agents, plus the agent skipping a node_agent entry in run: (B31, N2).

Every test works on a copy of the repo whose scenarios are built here:

    desk   127.0.0.1    this machine (its os/arch): its agent is started by the test
    far    203.0.113.9  never local: its agent is always missing (only where a test adds it)
    tc397  external     watched for its "Example Temperature" data

DDS tests use the shared test bus's domain, so they never meet a real rig.
"""
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "cli"))
sys.path.insert(0, str(REPO / "libs" / "py"))

import discovery                                              # noqa: E402
from fw.agentfiles import AgentFiles, PidRecord, process_started   # noqa: E402
from fw.supervise import process_alive                        # noqa: E402

SCEN = "live-sample"
MY_OS, MY_ARCH = discovery.this_machine()
DESK = {"ip": "127.0.0.1", "os": MY_OS, "arch": MY_ARCH}
FAR = {"ip": "203.0.113.9", "os": "linux", "run": ["probe"]}
TC397 = {"ip": "203.0.113.10", "external": True, "sim": "tc397_twin"}
PROBE = '''import sys
from fw.app import App
app = App("probe", "probe for --live tests")
sys.exit(app.run())
'''
CRASHER = '''import os, sys, time
from fw.app import App
app = App("crasher", "crashes after --after seconds")
after = app.arg("--after", 1.5, "seconds before crashing")
t0 = time.monotonic()
def tick():
    if time.monotonic() - t0 > after:
        print("boom", flush=True)
        os._exit(3)
app.every(0.1, tick)
sys.exit(app.run())
'''
STUBBORN = '''import signal, sys
from fw.app import App
app = App("stubborn", "ignores Ctrl-C")
for s in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
    if s is not None:
        signal.signal(s, signal.SIG_IGN)
sys.exit(app.run())
'''


# --- fixtures and helpers ---------------------------------------------------------------

seen: list[PidRecord] = []          # every agent the current test started


@pytest.fixture
def rig(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "build", ".venv", ".local"))
    for d in (root / "scenarios").iterdir():
        if d.is_dir():
            shutil.rmtree(d)
    apps = root / "apps" / "tooling"
    for name, code in (("probe", PROBE), ("crasher", CRASHER), ("stubborn", STUBBORN)):
        (apps / name).mkdir()
        (apps / name / "main.py").write_text(code, encoding="utf-8")
    scenario(root, SCEN, desk={**DESK, "run": ["probe"]}, tc397=TC397)
    seen.clear()
    yield root
    note(root)
    for rec in seen:                                            # no agent outlives its test
        if process_alive(rec.pid) and process_started(rec.pid) == rec.started:
            os.kill(rec.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    from fw.testing import leftovers
    left = leftovers(root)
    assert not left, "processes outlived this test:\n" + "\n".join(left)


def scenario(root: Path, name: str, **nodes) -> None:
    s = root / "scenarios" / name
    s.mkdir(parents=True, exist_ok=True)
    (s / "scenario.yaml").write_text(yaml.safe_dump({"description": "live tests", "domain": 0, "nodes": nodes}),
                                     encoding="utf-8")


def note(root: Path, scen: str = SCEN) -> None:
    rec = AgentFiles.of(root, scen, "desk").read_pid()
    if rec is not None and rec not in seen:
        seen.append(rec)


def agent(rig, action, bus, scen=SCEN, *extra) -> subprocess.CompletedProcess:
    args = [sys.executable, str(rig / "cli" / "main.py"), "agent", action, scen, *extra]
    if action != "stop":
        args += ["--domain", str(bus.domain)]
    out = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", cwd=rig, timeout=60)
    note(rig, scen)
    assert "Traceback" not in out.stdout + out.stderr, out.stdout + out.stderr
    return out


def start_agent(rig, bus, scen=SCEN) -> int:
    out = agent(rig, "start", bus, scen, "--background")
    assert out.returncode == 0, out.stdout
    return AgentFiles.of(rig, scen, "desk").read_pid().pid


class Live:
    """`protorig run <scenario> --live` as a live process, its output collected."""

    def __init__(self, rig, bus, *extra, scen=SCEN, mode="--live"):
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        self.proc = subprocess.Popen(
            [sys.executable, str(rig / "cli" / "main.py"), "run", scen, mode, "--domain", str(bus.domain), *extra],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", cwd=rig, **kw)
        self.lines: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def wait_for(self, text: str, timeout: float = 20) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if text in self.output:
                return
            if self.proc.poll() is not None:
                time.sleep(0.2)
                break
            time.sleep(0.05)
        assert text in self.output, f"no {text!r} in:\n{self.output}"

    def ctrl_c(self) -> None:
        if os.name == "nt":
            self.proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(self.proc.pid, signal.SIGINT)

    def finish(self, timeout: float = 40) -> int:
        self.ctrl_c()
        code = self.proc.wait(timeout)
        time.sleep(0.2)                                           # the reader thread catches up
        assert "Traceback" not in self.output, self.output
        return code

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture
def live_procs():
    procs = []
    yield procs
    for p in procs:
        p.kill()


def live(rig, bus, live_procs, *extra, scen=SCEN, mode="--live") -> Live:
    p = Live(rig, bus, *extra, scen=scen, mode=mode)
    live_procs.append(p)
    return p


def sim(rig, bus, live_procs, scen=SCEN) -> Live:
    """`run <scenario> --sim`: its agents and twins run here, started by run."""
    return live(rig, bus, live_procs, scen=scen, mode="--sim")


def sim_agent_pid(rig, node="desk", scen=SCEN, timeout=20) -> int:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rec = AgentFiles.of(rig, scen, node).read_pid()
        if rec is not None:
            if rec not in seen:
                seen.append(rec)
            return rec.pid
        time.sleep(0.1)
    raise AssertionError("no agent.pid from the sim agent")


# --- U11: start, show, stop -------------------------------------------------------------

def test_brings_the_rig_up_and_down(rig, bus, live_procs):
    start_agent(rig, bus)
    p = live(rig, bus, live_procs, "--start-timeout", "5")
    p.wait_for("this machine is desk (127.0.0.1)")
    p.wait_for("tc397: external device, power it on (watching for Example Temperature)")
    p.wait_for("desk: agent ready")
    p.wait_for("sent start to desk")
    p.wait_for("desk/probe RUNNING")
    p.wait_for("every app is RUNNING", timeout=10)
    assert "waiting for the agents" not in p.output              # it was already there: no false alarm
    assert p.finish() == 0
    assert "desk/probe STOPPED: stopped by desk/run-live" in p.output
    assert "how each app ended: desk/probe STOPPED; the agents stay up" in p.output
    assert agent(rig, "status", bus, SCEN, "--node", "desk").returncode == 0     # the agent stays up
    assert "[RTI LICENSE]" not in p.output


def test_waits_for_an_agent_started_later(rig, bus, live_procs):
    p = live(rig, bus, live_procs)
    p.wait_for("waiting for the agents of: desk")
    assert "sent start" not in p.output
    start_agent(rig, bus)
    p.wait_for("desk/probe RUNNING")
    assert p.finish() == 0


def test_waits_for_every_agent_unless_partial(rig, bus, live_procs):
    scenario(rig, SCEN, desk={**DESK, "run": ["probe"]}, far=FAR)
    start_agent(rig, bus)
    waiting = live(rig, bus, live_procs)
    waiting.wait_for("waiting for the agents of: far")
    time.sleep(4)
    assert "sent start" not in waiting.output
    assert waiting.finish() == 2 and "stopped before the rig came up" in waiting.output

    partial = live(rig, bus, live_procs, "--partial")
    partial.wait_for("--partial: going ahead without far")
    partial.wait_for("desk/probe RUNNING")
    assert partial.finish() == 0


def test_refuses_an_agent_running_another_scenario(rig, bus, live_procs):
    scenario(rig, "other", desk={**DESK, "run": ["probe"]})
    start_agent(rig, bus, scen="other")
    p = live(rig, bus, live_procs)
    assert p.proc.wait(30) == 2
    time.sleep(0.2)
    assert "desk's agent runs scenario 'other', not 'live-sample'" in p.output, p.output
    assert "protorig agent stop other, then protorig agent start live-sample" in p.output
    assert "sent start" not in p.output


def test_failed_apps_are_reported_and_exit_1(rig, bus, live_procs):
    scenario(rig, SCEN, desk={**DESK, "run": ["probe", "crasher", "missing_app"]}, tc397=TC397)
    start_agent(rig, bus)
    p = live(rig, bus, live_procs, "--start-timeout", "4")
    p.wait_for("desk/crasher CRASHED: boom")
    p.wait_for("ALERT CRITICAL crash:crasher (desk/node_agent)")
    p.wait_for("not RUNNING after 4 s: desk/crasher (CRASHED: boom); desk/missing_app (UNAVAILABLE", timeout=10)
    assert p.finish() == 1
    assert "failed: desk/crasher, desk/missing_app" in p.output


def test_crash_after_everything_is_running_counts_as_failed(rig, bus, live_procs):
    scenario(rig, SCEN, desk={**DESK, "run": ["probe", "crasher --after 5"]})
    start_agent(rig, bus)
    p = live(rig, bus, live_procs, "--start-timeout", "2")
    p.wait_for("every app is RUNNING", timeout=10)
    p.wait_for("desk/crasher CRASHED: boom", timeout=10)
    assert p.finish() == 1 and "failed: desk/crasher" in p.output


def test_reattaches_to_a_running_demo(rig, bus, live_procs):
    start_agent(rig, bus)
    first = live(rig, bus, live_procs)
    first.wait_for("desk/probe RUNNING")
    first.kill()                                                  # gone without stopping anything
    second = live(rig, bus, live_procs)
    second.wait_for("the demo is already running: showing it (Ctrl-C stops it)")
    assert "sent start" not in second.output
    assert second.finish() == 0
    assert "desk/probe STOPPED" in second.output


def test_second_ctrl_c_leaves_at_once(rig, bus, live_procs):
    scenario(rig, SCEN, desk={**DESK, "run": ["stubborn"]})
    start_agent(rig, bus)                                         # the agent forces it only after 10 s
    p = live(rig, bus, live_procs)
    p.wait_for("desk/stubborn RUNNING")
    p.ctrl_c()
    p.wait_for("stopping every app")
    t0 = time.monotonic()
    p.ctrl_c()
    assert p.proc.wait(10) == 1 and time.monotonic() - t0 < 5
    time.sleep(0.2)
    assert "second Ctrl-C: leaving without waiting" in p.output


def test_agent_lost_and_back(rig, bus, live_procs):
    start_agent(rig, bus)
    p = live(rig, bus, live_procs)
    p.wait_for("desk/probe RUNNING")
    assert agent(rig, "stop", bus, SCEN, "--force").returncode == 0
    p.wait_for("desk agent LOST", timeout=15)
    start_agent(rig, bus)
    p.wait_for("desk agent back", timeout=15)
    p.finish()


def test_external_device_seen(rig, bus, live_procs):
    twin = subprocess.Popen([sys.executable, str(rig / "cli" / "main.py"), "run", "--scenario", SCEN,
                             "--domain", str(bus.domain), "--app", "tc397_twin"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, cwd=rig,
                            **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                               else {"start_new_session": True}))
    try:
        start_agent(rig, bus)
        p = live(rig, bus, live_procs)
        p.wait_for("tc397: its data is on the network", timeout=20)
        p.wait_for("desk/probe RUNNING")
        assert p.finish() == 0
    finally:
        if os.name == "nt":
            twin.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(twin.pid, signal.SIGINT)
        try:
            twin.wait(20)
        except subprocess.TimeoutExpired:
            twin.kill()


# --- U11 step 3: a start the agent missed is sent again, with the same cmd_id -----------

def test_start_resent_once_with_the_same_cmd_id(rig, bus, live_procs):
    """A stand-in agent on the test bus: it heartbeats as desk's agent but never acts."""
    from fw import types as T
    stop = threading.Event()

    def beat():
        w = bus.writer("_sys/NodeStatus")
        while not stop.is_set():
            w.write(T.NodeStatus(node="desk", app="node_agent"))
            time.sleep(0.3)
    threading.Thread(target=beat, daemon=True).start()
    fake_row = T.AppState(node="desk", app="node_agent", state=T.AppStateKind.APP_RUNNING, scenario=SCEN)
    bus.writer("_sys/AppState").write(fake_row)
    commands = bus.listen("_sys/DemoControl")
    try:
        p = live(rig, bus, live_procs)
        p.wait_for("desk: agent ready")
        ready = time.monotonic()
        p.wait_for("sent start to desk")
        # The stand-in's command reader isn't named desk/node_agent, so --live first
        # waits (5 s) for desk's agent to match, as it would for a real one.
        assert time.monotonic() - ready > 3.5
        deadline = time.monotonic() + 15
        starts = []
        while time.monotonic() < deadline:
            starts = [c for c in commands.all() if c.target_node == "desk" and c.command.name == "CMD_START_APP"]
            if len(starts) >= 2:
                break
            time.sleep(0.1)
        time.sleep(4)                                             # and never a third time
        starts = [c for c in commands.all() if c.target_node == "desk" and c.command.name == "CMD_START_APP"]
        assert len(starts) == 2, starts
        assert starts[0].cmd_id == starts[1].cmd_id and starts[0].target_app == "*"
        p.finish()
    finally:
        stop.set()
        # The bus lives for the whole session and keeps rows for late joiners:
        # left alone, this fake agent row would reach every later test's readers.
        w = bus.writer("_sys/AppState")
        w.dispose_instance(w.register_instance(fake_row))


# --- refusals and B31 -------------------------------------------------------------------

@pytest.mark.parametrize("args,expected,code", [
    ([SCEN, "--live", "--sim"], "choose one", 1),
    (["--live"], "which scenario?", 1),
    ([SCEN, "--partial"], "--partial and --start-timeout are for --live", 1),
    ([SCEN, "--start-timeout", "5"], "--partial and --start-timeout are for --live", 1),
    ([SCEN, "--live", "--dry-run"], "--dry-run isn't available with --live", 1),
    ([SCEN, "--live", "--start-timeout", "0"], "between 0 and 3600", 1),
    ([SCEN, "--live", "--domain", "300"], "not a DDS domain", 2),
    (["elsewhere", "--live"], "none of its managed nodes' IPs", 2),
])
def test_refusals(rig, args, expected, code):
    scenario(rig, "elsewhere", pc={"ip": "203.0.113.11", "os": "linux", "run": ["probe"]})
    out = subprocess.run([sys.executable, str(rig / "cli" / "main.py"), "run", *args],
                         capture_output=True, text=True, encoding="utf-8", cwd=rig, timeout=60)
    assert expected in out.stdout and out.returncode == code, (out.returncode, out.stdout + out.stderr)
    assert "Traceback" not in out.stdout + out.stderr


def test_fuzz_live_arguments(rig):
    """Odd argument mixes with --live never crash. The scenario has no node on this
    machine, so a valid --live ends at once (exit 2) instead of waiting."""
    scenario(rig, "elsewhere", pc={"ip": "203.0.113.11", "os": "linux", "run": ["probe"]})
    rnd = random.Random(21)
    words = ["elsewhere", "--live", "--partial", "--start-timeout", "5", "-1", "1e9", "nan", "--domain", "7",
             "--sim", "--node", "pc", "x", "--dry-run"]
    for _ in range(40):
        args = ["--live"] + [rnd.choice(words) for _ in range(rnd.randint(0, 5))]
        out = subprocess.run([sys.executable, str(rig / "cli" / "main.py"), "run", *args],
                             capture_output=True, text=True, encoding="utf-8", cwd=rig, timeout=60)
        assert "Traceback" not in out.stdout + out.stderr, (args, out.stdout + out.stderr)
        assert out.returncode in (1, 2), (args, out.returncode, out.stdout)


def test_agent_skips_node_agent_in_run_list(rig, bus, live_procs):
    """B31 (N2): node_agent listed in run: is skipped, so "start *" never makes a second agent."""
    scenario(rig, SCEN, desk={**DESK, "run": ["node_agent", "probe"]})
    start_agent(rig, bus)
    st = agent(rig, "status", bus, SCEN)
    assert "desk: agent alive\n    probe  NOT_RUNNING\n" in st.stdout, st.stdout
    p = live(rig, bus, live_procs, "--start-timeout", "3")
    p.wait_for("desk/probe RUNNING")
    p.wait_for("every app is RUNNING", timeout=10)
    log = AgentFiles.of(rig, SCEN, "desk").log_file.read_text(encoding="utf-8")
    assert "run: lists node_agent: skipped" in log
    assert "started node_agent" not in log and "desk/node_agent STARTING" not in p.output
    assert p.finish() == 0


# --- run --sim through agents (U2 as changed by N2; step 4b) ------------------------------

def test_sim_refuses_rig_agents_of_this_scenario(rig, bus):
    """Decision 1 (4b): a rig agent for this scenario on this PC would meet the sim's."""
    pid = start_agent(rig, bus)
    out = subprocess.run([sys.executable, str(rig / "cli" / "main.py"), "run", SCEN, "--sim", "--domain", str(bus.domain)],
                         capture_output=True, text=True, encoding="utf-8", cwd=rig, timeout=60)
    assert out.returncode == 1, out.stdout
    assert "agents for this scenario already run on this PC" in out.stdout
    assert f"desk (process {pid}): stop it with `protorig agent stop live-sample`" in out.stdout, out.stdout
    assert process_alive(pid)                                     # left alone


def test_sim_refuses_an_agent_already_heard_here(rig, bus):
    """An agent for the same node from another scenario, on the same domain."""
    scenario(rig, "other", desk={**DESK, "run": ["probe"]})
    start_agent(rig, bus, scen="other")
    out = subprocess.run([sys.executable, str(rig / "cli" / "main.py"), "run", SCEN, "--sim", "--domain", str(bus.domain)],
                         capture_output=True, text=True, encoding="utf-8", cwd=rig, timeout=60)
    assert out.returncode == 1 and f"already running on this PC on domain {bus.domain}: " in out.stdout, out.stdout
    assert "desk/node_agent" in out.stdout and "--domain N" in out.stdout
    assert "Traceback" not in out.stdout + out.stderr


def test_sim_unknown_pid_file_names_the_way_out(rig):
    """B11: agent.pid naming a process whose start time can't be read (Windows,
    after a reboot): refused, and the message says which file to delete."""
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        f = AgentFiles.of(rig, SCEN, "desk")
        f.folder.mkdir(parents=True)
        f.pid_file.write_text(json.dumps({"pid": other.pid, "started": None}), encoding="utf-8")
        out = subprocess.run([sys.executable, str(rig / "cli" / "main.py"), "run", SCEN, "--sim"],
                             capture_output=True, text=True, encoding="utf-8", cwd=rig, timeout=60)
        assert out.returncode == 1 and f"delete {f.pid_file}" in out.stdout, out.stdout
        assert other.poll() is None                               # never touched
    finally:
        other.kill()


def test_sim_refuses_while_another_sim_runs(rig, bus, live_procs):
    """B11: a second --sim. `agent stop` can't stop a sim agent of a node whose IP
    isn't this PC's (far), so the message points at the run that started it."""
    scenario(rig, "two", desk={**DESK, "run": ["probe"]}, far=FAR)
    first = sim(rig, bus, live_procs, scen="two")
    first.wait_for("far/probe RUNNING", timeout=30)
    far = sim_agent_pid(rig, node="far", scen="two")
    assert AgentFiles.of(rig, "two", "far").read_pid().launcher is not None
    out = subprocess.run([sys.executable, str(rig / "cli" / "main.py"), "run", "two", "--sim", "--domain", str(bus.domain)],
                         capture_output=True, text=True, encoding="utf-8", cwd=rig, timeout=60)
    assert out.returncode == 1, out.stdout
    assert f"far (process {far}): started by another `run two --sim` (process " in out.stdout, out.stdout
    assert "protorig agent stop" not in out.stdout                # it would refuse: far's IP isn't this PC's
    assert process_alive(far) and first.proc.poll() is None       # the first run is left alone
    assert first.finish() == 0


def test_sim_refuses_a_twin_left_running(rig, bus, live_procs):
    """B11: not only agents. A twin started by hand would publish the ECU's data
    next to the sim's twin, and displays would mix the two."""
    from fw.supervise import Supervised
    env = {**os.environ, "PYTHONPATH": str(rig / "libs" / "py")}
    twin = Supervised([sys.executable, str(rig / "apps" / "sim" / "tc397_twin" / "main.py")],
                      ["--node", "tc397", "--scenario", SCEN, "--domain", str(bus.domain)], env, watch_launcher=False)
    try:
        out = subprocess.run([sys.executable, str(rig / "cli" / "main.py"), "run", SCEN, "--sim", "--domain", str(bus.domain)],
                             capture_output=True, text=True, encoding="utf-8", cwd=rig, timeout=60)
        assert out.returncode == 1 and "tc397/tc397_twin" in out.stdout and "already running on this PC" in out.stdout, out.stdout
        assert not twin.exited()                                  # left alone
    finally:
        twin.terminate(grace=5)


def test_sim_machine_commands_reach_its_agents(rig, bus, live_procs):
    """The point of 4b: a Control Panel command to a machine works in sim as on the rig."""
    from fw import types as T
    p = sim(rig, bus, live_procs)
    p.wait_for("tc397: external device; its twin tc397_twin stands in")
    p.wait_for("desk/probe RUNNING", timeout=30)
    bus._cmd_id += 1
    bus.send("_sys/DemoControl", T.DemoControl(target_node="desk", target_app="*", cmd_id=bus._cmd_id,
                                               command=T.Command.CMD_KILL_APP), to="desk/node_agent")
    p.wait_for("desk/probe KILLED: killed by test/bus")
    assert p.finish() == 0                                        # a kill is a demo beat, not a failure
    for who in ("desk/node_agent", "tc397/tc397_twin"):
        assert f"{who} stopped (exit 0)" in p.output, p.output


def test_sim_everything_stops_if_run_is_killed(rig, bus, live_procs):
    """N6: the agents and twin watch run; the apps watch their agent."""
    p = sim(rig, bus, live_procs)
    p.wait_for("desk/probe RUNNING", timeout=30)
    pid = sim_agent_pid(rig)
    p.kill()                                                      # no goodbye
    end = time.monotonic() + 10
    while time.monotonic() < end and process_alive(pid):
        time.sleep(0.1)
    assert not process_alive(pid), "the sim agent outlived run"
    st = agent(rig, "status", bus, SCEN, "--node", "desk")
    assert st.returncode == 1                                     # nothing left heartbeating as desk


def test_sim_agent_that_dies_at_start(rig, bus, live_procs):
    (rig / "apps" / "tooling" / "node_agent" / "main.py").write_text("import sys\nsys.exit(5)\n", encoding="utf-8")
    t0 = time.monotonic()
    p = sim(rig, bus, live_procs)
    assert p.proc.wait(30) == 1 and time.monotonic() - t0 < 20, p.output
    time.sleep(0.2)
    assert "desk/node_agent ended by itself: CRASHED (exit 5)" in p.output
    assert "didn't come up" in p.output
    assert "tc397/tc397_twin stopped" in p.output                    # the twin is stopped too
    assert "desk/node_agent stopped" not in p.output                 # it crashed by itself: never relabelled
    assert "run again to bring it back" not in p.output             # at start, run stops everything itself


def test_sim_agent_that_dies_mid_demo(rig, bus, live_procs):
    """B13: the dead agent's node is reported with the way back; the others carry on."""
    scenario(rig, "two", desk={**DESK, "run": ["probe"]}, far=FAR)
    p = sim(rig, bus, live_procs, scen="two")
    p.wait_for("far/probe RUNNING", timeout=30)
    p.wait_for("desk/probe RUNNING", timeout=30)
    os.kill(sim_agent_pid(rig, node="far", scen="two"), getattr(signal, "SIGKILL", signal.SIGTERM))
    p.wait_for("far/node_agent ended by itself: ")
    p.wait_for("Its apps stopped with it; Ctrl-C and run again to bring it back")
    time.sleep(2)
    assert p.proc.poll() is None                                  # run carries on with desk
    assert "desk/probe STOPPED" not in p.output and "desk/probe CRASHED" not in p.output
    assert p.finish() == 1                                        # a node was lost: reported as a failure


def test_sim_second_ctrl_c_leaves_and_everything_stops(rig, bus, live_procs):
    scenario(rig, SCEN, desk={**DESK, "run": ["stubborn"]}, tc397=TC397)
    p = sim(rig, bus, live_procs)
    p.wait_for("desk/stubborn RUNNING", timeout=30)
    pid = sim_agent_pid(rig)
    p.ctrl_c()
    p.wait_for("stopping every app")
    p.ctrl_c()
    assert p.proc.wait(10) == 1
    time.sleep(0.2)
    assert "agents and twins stop by themselves" in p.output
    end = time.monotonic() + 20                                   # the agent forces its stubborn app first
    while time.monotonic() < end and process_alive(pid):
        time.sleep(0.1)
    assert not process_alive(pid)
