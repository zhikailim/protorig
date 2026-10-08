"""
test_agent_cli.py — `protorig agent start|stop|status` (node_agent N1, N1a) and
the files it shares with the agent (fw.agentfiles).

Every test works on a copy of the repo whose only scenario is built here:

    desk  127.0.0.1    a real address of this machine, with its os/arch: the agent runs here
    far   203.0.113.9  documentation-only address, never local: its agent is always missing
    ecu   external

DDS tests use the shared test bus's domain, so they never meet a real rig.
"""
import json
import os
import random
import shutil
import signal
import subprocess
import sys
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

SCEN = "agent-cli"
MY_OS, MY_ARCH = discovery.this_machine()
STOP_WAIT = 15            # cli/agent.py: STOP_GRACE + STOP_EXTRA
PROBE = '''import sys
from fw.app import App
app = App("probe", "probe for agent command tests")
sys.exit(app.run())
'''
SLEEPER = "import time\nprint('ready', flush=True)\ntime.sleep(120)\n"


@pytest.fixture
def rig(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "build", ".venv", ".local"))
    for d in (root / "scenarios").iterdir():
        if d.is_dir():
            shutil.rmtree(d)
    s = root / "scenarios" / SCEN
    (s / "apps" / "tooling" / "probe").mkdir(parents=True)
    (s / "scenario.yaml").write_text(yaml.safe_dump({
        "description": "agent command tests", "domain": 0,
        "nodes": {"desk": {"ip": "127.0.0.1", "os": MY_OS, "arch": MY_ARCH, "run": ["probe"]},
                  "far": {"ip": "203.0.113.9", "os": "linux", "run": ["probe"]},
                  "ecu": {"ip": "203.0.113.10", "external": True}}}), encoding="utf-8")
    (s / "apps" / "tooling" / "probe" / "main.py").write_text(PROBE, encoding="utf-8")
    seen.clear()
    yield root
    # Whatever happened (even a second agent overwriting agent.pid when a check is
    # broken), no agent outlives its test: every one ever named is ended.
    note(root)
    for rec in seen:
        if process_alive(rec.pid) and process_started(rec.pid) == rec.started:
            os.kill(rec.pid, getattr(signal, "SIGKILL", signal.SIGTERM))


seen: list[PidRecord] = []          # every agent the current test started (see the rig fixture)


def files(rig) -> AgentFiles:
    return AgentFiles.of(rig, SCEN, "desk")


def note(rig) -> None:
    rec = files(rig).read_pid()
    if rec is not None and rec not in seen:
        seen.append(rec)


def agent(rig, *args, timeout=60) -> subprocess.CompletedProcess:
    note(rig)
    out = subprocess.run([sys.executable, str(rig / "cli" / "main.py"), "agent", *args],
                         capture_output=True, text=True, encoding="utf-8", cwd=rig, timeout=timeout)
    note(rig)
    assert "Traceback" not in out.stdout + out.stderr, out.stdout + out.stderr
    return out


def start_bg(rig, bus) -> int:
    out = agent(rig, "start", SCEN, "--background", "--domain", str(bus.domain))
    assert out.returncode == 0, out.stdout
    assert "this machine is desk (127.0.0.1)" in out.stdout and "agent for desk is up (process" in out.stdout
    assert "[RTI LICENSE]" not in out.stdout                    # Connext's banner filtered
    rec = files(rig).read_pid()
    assert rec and process_alive(rec.pid)
    return rec.pid


def sleeper() -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-c", SLEEPER], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ready"
    return p


def until(cond, timeout, what):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout} s waiting for {what}")


# --- start, status, stop (N1, N1a steps 1-5) -----------------------------------------

def test_background_start_status_stop(rig, bus):
    pid = start_bg(rig, bus)
    d = str(bus.domain)
    st = agent(rig, "status", SCEN, "--domain", d)
    assert st.returncode == 1                                   # far's agent is missing
    assert "desk: agent alive; apps: probe NOT_RUNNING" in st.stdout, st.stdout
    assert "far: agent MISSING" in st.stdout and "ecu" not in st.stdout
    assert agent(rig, "status", SCEN, "--node", "desk", "--domain", d).returncode == 0

    out = agent(rig, "stop", SCEN)
    assert out.returncode == 0 and "agent for desk stopped" in out.stdout, out.stdout
    assert not process_alive(pid)
    assert not files(rig).pid_file.exists() and not files(rig).stop_file.exists()
    assert "stopping (asked by `protorig agent stop`)" in files(rig).log_file.read_text(encoding="utf-8")
    again = agent(rig, "stop", SCEN)
    assert again.returncode == 0 and "no agent running for desk" in again.stdout
    assert agent(rig, "status", SCEN, "--node", "desk", "--domain", d).returncode == 1


def test_status_without_agents(rig, bus):
    st = agent(rig, "status", SCEN, "--domain", str(bus.domain))
    assert st.returncode == 1
    assert "desk: agent MISSING" in st.stdout and "far: agent MISSING" in st.stdout


def test_second_start_refused_on_this_machine(rig, bus):
    """N1a step 7: refused from agent.pid, before any DDS check."""
    pid = start_bg(rig, bus)
    out = agent(rig, "start", SCEN, "--background", "--domain", str(bus.domain))
    assert out.returncode == 1 and f"already running (process {pid})" in out.stdout, out.stdout
    assert process_alive(pid)


def test_second_start_refused_over_dds(rig, bus):
    """N1: an agent for this node heard on the rig (e.g. on another machine) also refuses."""
    pid = start_bg(rig, bus)
    saved = files(rig).pid_file.read_bytes()
    files(rig).pid_file.unlink()                                # as if that agent ran elsewhere
    out = agent(rig, "start", SCEN, "--background", "--domain", str(bus.domain))
    assert out.returncode == 1 and "already running on the rig" in out.stdout, out.stdout
    files(rig).pid_file.write_bytes(saved)
    assert agent(rig, "stop", SCEN).returncode == 0 and not process_alive(pid)


def test_foreground_stops_on_ctrl_c(rig, bus):
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    p = subprocess.Popen([sys.executable, str(rig / "cli" / "main.py"), "agent", "start", SCEN, "--domain", str(bus.domain)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", cwd=rig, **kw)
    try:
        lines = []
        for line in p.stdout:
            lines.append(line)
            if "waiting for commands" in line:
                break
        assert files(rig).read_pid() is not None, "".join(lines)    # written in the foreground too
        # Ctrl-C in that terminal: the whole group gets it (Ctrl-Break on Windows).
        if os.name == "nt":
            p.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(p.pid, signal.SIGINT)
        assert p.wait(30) == 0
        assert not files(rig).pid_file.exists()
    finally:
        if p.poll() is None:
            p.kill()


def test_foreground_agent_stopped_from_another_terminal(rig, bus):
    p = subprocess.Popen([sys.executable, str(rig / "cli" / "main.py"), "agent", "start", SCEN, "--domain", str(bus.domain)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", cwd=rig)
    try:
        for line in p.stdout:
            if "waiting for commands" in line:
                break
        assert agent(rig, "stop", SCEN).returncode == 0
        assert p.wait(30) == 0
    finally:
        if p.poll() is None:
            p.kill()


# --- stale files and reused process IDs (N1a steps 3, 4, 6) ----------------------------

def test_leftover_stop_requests_are_ignored(rig, bus):
    """B29: a stop request for another process, or junk, is deleted; the agent keeps running."""
    pid = start_bg(rig, bus)
    f = files(rig)
    for content in (b"999999\n", b"not a pid", b"", b"\x00\xff" * 50):
        f.stop_file.write_bytes(content)
        until(lambda: not f.stop_file.exists(), 5, f"the agent to delete a stop file holding {content!r}")
        assert process_alive(pid)
    log = f.log_file.read_text(encoding="utf-8")
    assert "ignored a leftover stop request for process 999999" in log
    assert "ignored an unreadable stop request" in log


def test_pid_file_of_an_ended_process_is_stale(rig, bus):
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    f = files(rig)
    f.folder.mkdir(parents=True)
    f.pid_file.write_text(json.dumps({"pid": p.pid, "started": 1}), encoding="utf-8")
    f.stop_file.write_text("123\n", encoding="utf-8")
    out = agent(rig, "stop", SCEN)
    assert out.returncode == 0 and "no agent running" in out.stdout
    assert not f.pid_file.exists() and not f.stop_file.exists()
    f.pid_file.write_text(json.dumps({"pid": p.pid, "started": 1}), encoding="utf-8")
    start_bg(rig, bus)                                          # a stale file never blocks a start
    assert agent(rig, "stop", SCEN).returncode == 0


def test_reused_process_id_is_never_stopped_or_killed(rig, bus):
    """The PID in agent.pid now belongs to another program (its start time differs)."""
    other = sleeper()
    try:
        f = files(rig)
        f.folder.mkdir(parents=True)
        f.pid_file.write_text(json.dumps({"pid": other.pid, "started": process_started(other.pid) + 1}), encoding="utf-8")
        for args in (["stop", SCEN], ["stop", SCEN, "--force"]):
            out = agent(rig, *args)
            assert out.returncode == 0 and "no agent running" in out.stdout, out.stdout
            assert other.poll() is None, "an unrelated process was touched"
            f.pid_file.write_text(json.dumps({"pid": other.pid, "started": process_started(other.pid) + 1}), encoding="utf-8")
        start_bg(rig, bus)
        assert agent(rig, "stop", SCEN).returncode == 0
        assert other.poll() is None
    finally:
        other.kill()


def test_force_refuses_when_start_time_cant_be_compared(rig):
    """A pid file without a start time (e.g. written on another OS): --force must not kill."""
    other = sleeper()
    try:
        f = files(rig)
        f.folder.mkdir(parents=True)
        f.pid_file.write_text(json.dumps({"pid": other.pid, "started": None}), encoding="utf-8")
        out = agent(rig, "stop", SCEN, "--force")
        assert out.returncode == 1 and "can't confirm process" in out.stdout and "isn't killed" in out.stdout, out.stdout
        assert other.poll() is None
    finally:
        other.kill()


def test_stop_waits_then_force_kills(rig):
    """An agent that doesn't obey the stop file: stop gives up after the grace,
    then --force kills it (it is still the same process: start time matches)."""
    hung = sleeper()
    try:
        files(rig).claim(hung.pid)
        t0 = time.monotonic()
        out = agent(rig, "stop", SCEN, timeout=60)
        assert out.returncode == 1 and "didn't stop within 15 s" in out.stdout and "--force" in out.stdout, out.stdout
        assert STOP_WAIT - 1 <= time.monotonic() - t0 < STOP_WAIT + 10
        assert hung.poll() is None
        out = agent(rig, "stop", SCEN, "--force")
        assert out.returncode == 0 and f"killed the agent for desk (process {hung.pid})" in out.stdout
        hung.wait(5)
        assert not files(rig).pid_file.exists()
    finally:
        if hung.poll() is None:
            hung.kill()


def test_force_kill_a_real_agent_and_its_apps_stop(rig, bus):
    """N1a step 6: the killed agent's apps notice (launcher watch) and stop by themselves."""
    import rti.connextdds as dds
    from fw import types as T
    pid = start_bg(rig, bus)
    hb = dds.DataReader(bus._sub, bus._topic("_sys/NodeStatus"), bus.provider.get_topic_datareader_qos("_sys/NodeStatus"))
    try:
        bus._cmd_id += 1
        bus.send("_sys/DemoControl", T.DemoControl(target_node="desk", target_app="probe", cmd_id=bus._cmd_id,
                                                   command=T.Command.CMD_START_APP), to="desk/node_agent")

        def probe_state():
            states = [str(i.state.instance_state) for d, i in hb.read() if i.valid and d.node == "desk" and d.app == "probe"]
            gone = [str(i.state.instance_state) for d, i in hb.read() if not i.valid]
            return states[-1] if states else None, gone
        until(lambda: probe_state()[0] and "ALIVE" in probe_state()[0] and "NOT" not in probe_state()[0], 15,
              "the probe's heartbeat")
        out = agent(rig, "stop", SCEN, "--force")
        assert out.returncode == 0 and "killed the agent" in out.stdout
        assert not process_alive(pid)

        def probe_gone():
            for d, i in hb.read():
                if i.valid and d.node == "desk" and d.app == "probe":
                    return "NOT_ALIVE" in str(i.state.instance_state)
            return False
        until(probe_gone, 10, "the probe to stop by itself after its agent was killed")
    finally:
        hb.close()


# --- start that fails (N1a step 1) ------------------------------------------------------

def test_background_start_reports_an_agent_that_ends_at_once(rig, bus):
    (rig / "apps" / "tooling" / "node_agent" / "main.py").unlink()
    out = agent(rig, "start", SCEN, "--background", "--domain", str(bus.domain))
    assert out.returncode == 1 and "the agent ended at once (exit 2)" in out.stdout, out.stdout
    assert "Last lines of" in out.stdout and "main.py" in out.stdout      # the log's tail is shown
    assert not files(rig).pid_file.exists()


FAKE_AGENT = '''import os, sys, time
sys.path.insert(0, {libs!r})
from fw.agentfiles import AgentFiles
f = AgentFiles.of({root!r}, {scen!r}, "desk")
f.claim()
print("fake agent: no DDS, no heartbeat", flush=True)
while True:
    if f.stop_request() == os.getpid():
        f.release()
        sys.exit(0)
    time.sleep(0.1)
'''


def test_background_start_gives_up_without_a_heartbeat(rig, bus):
    (rig / "apps" / "tooling" / "node_agent" / "main.py").write_text(
        FAKE_AGENT.format(libs=str(rig / "libs" / "py"), root=str(rig), scen=SCEN), encoding="utf-8")
    t0 = time.monotonic()
    out = agent(rig, "start", SCEN, "--background", "--domain", str(bus.domain), timeout=90)
    assert out.returncode == 1 and "no heartbeat from the agent within 15 s; stopped it again" in out.stdout, out.stdout
    assert "fake agent: no DDS" in out.stdout                                # its log, shown
    assert time.monotonic() - t0 < 40
    assert not files(rig).pid_file.exists()                                 # it obeyed the stop request


# --- refusals: clear words, never a traceback -------------------------------------------

@pytest.mark.parametrize("args,expected", [
    (["start", SCEN, "--force"], "--force is for stop"),
    (["stop", SCEN, "--background"], "--background is for start"),
    (["status", SCEN, "--background"], "--background is for start"),
    (["start", "nope"], "no scenario 'nope'"),
    (["start", SCEN, "--node", "ecu"], "is an external node"),
    (["stop", SCEN, "--node", "far"], "doesn't have 203.0.113.9"),
    (["status", SCEN, "--node", "ecu"], "no managed node 'ecu'"),
    (["start", SCEN, "--domain", "300"], "not a DDS domain"),
    (["status", SCEN, "--domain", "-1"], "not a DDS domain"),
    (["stop", SCEN, "--bogus"], "unknown argument"),
])
def test_refusals(rig, args, expected):
    out = agent(rig, *args)
    assert out.returncode == 1 and expected in out.stdout, out.stdout + out.stderr


@pytest.mark.parametrize("action", ["start", "stop", "status"])
def test_machine_not_in_the_scenario(rig, action):
    s = rig / "scenarios" / SCEN / "scenario.yaml"
    data = yaml.safe_load(s.read_text(encoding="utf-8"))
    data["nodes"]["desk"]["ip"] = "203.0.113.11"
    s.write_text(yaml.safe_dump(data), encoding="utf-8")
    out = agent(rig, action, SCEN)
    assert out.returncode == 1 and "none of its managed nodes' IPs" in out.stdout, out.stdout


@pytest.mark.parametrize("content", [b"", b"{", b"null", b"[]", b'{"pid": "12"}', b'{"pid": -5}', b'{"pid": true}',
                                     b'{"pid": 99999999999}', b"\xff\xfe\x00junk", b"x" * 10000,
                                     b'{"pid": 1, "started": "soon"}'])
def test_garbled_pid_file_never_crashes_stop(rig, content):
    f = files(rig)
    f.folder.mkdir(parents=True)
    f.pid_file.write_bytes(content)
    out = agent(rig, "stop", SCEN)
    assert out.returncode in (0, 1), out.stdout
    if b'"pid": 1,' not in content:                           # PID 1 (init) is alive: never killed, though
        assert "no agent running" in out.stdout and not f.pid_file.exists(), out.stdout


# --- fw.agentfiles on its own -----------------------------------------------------------

def test_process_started_is_stable_and_specific():
    me = process_started(os.getpid())
    assert isinstance(me, int) and me == process_started(os.getpid())
    time.sleep(0.05)
    newer = sleeper()
    try:
        assert process_started(newer.pid) > me              # a real start time, not a constant
    finally:
        newer.kill()
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert process_started(p.pid) is None
    for bad in (0, -1, 2**32, "12", None, True, 1.5):
        assert process_started(bad) is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc parsing")
def test_process_started_with_an_awkward_process_name(tmp_path):
    """/proc/<pid>/stat puts the process name in brackets, and a name may hold
    spaces and ")". The start time must still be the real one: within a few
    seconds of now, measured from boot in clock ticks."""
    link = tmp_path / "py (x) y z"
    link.symlink_to(sys.executable)
    p = subprocess.Popen([str(link), "-c", SLEEPER], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "ready"
        assert ") y z" in Path(f"/proc/{p.pid}/stat").read_text()          # the awkward name is really there
        ticks = os.sysconf("SC_CLK_TCK")
        now = float(Path("/proc/uptime").read_text().split()[0]) * ticks
        assert abs(process_started(p.pid) - now) < 10 * ticks
    finally:
        p.kill()


def test_identity_rules(tmp_path):
    f = AgentFiles(tmp_path)
    me = os.getpid()
    assert f.identity(PidRecord(me, process_started(me))) == "agent"
    assert f.identity(PidRecord(me, process_started(me) + 1)) == "gone"      # same number, other process
    assert f.identity(PidRecord(me, None)) == "unknown"                     # can't compare: never killed
    assert f.identity(None) == "gone"
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert f.identity(PidRecord(p.pid, 5)) == "gone"


def test_claim_release_and_stop_request(tmp_path):
    f = AgentFiles(tmp_path / "build" / "s" / "n")
    f.claim()
    assert f.read_pid() == PidRecord(os.getpid(), process_started(os.getpid()))
    assert f.stop_request() is None
    f.request_stop(4242)
    assert f.stop_request() == 4242
    f.release(pid=4242)                                         # someone else's release: our pid file stays
    assert f.pid_file.exists() and not f.stop_file.exists()
    f.release()
    assert not f.pid_file.exists()
    assert not list(f.folder.glob("*.tmp")), "atomic writes leave no temporary files"


def test_fuzz_file_readers(tmp_path):
    f = AgentFiles(tmp_path)
    rnd = random.Random(5)
    pieces = [b"{", b"}", b'"pid"', b":", b"12", b"-3", b"null", b'"started"', b",", b"\x00", b"\xff", b" ", b"\n", b"1e9"]
    for _ in range(3000):
        blob = b"".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 12)))
        f.pid_file.write_bytes(blob)
        f.stop_file.write_bytes(blob)
        rec = f.read_pid()
        assert rec is None or (isinstance(rec.pid, int) and 0 < rec.pid <= 0xFFFFFFFF)
        req = f.stop_request()
        assert req == -1 or (isinstance(req, int) and 0 < req <= 0xFFFFFFFF)
