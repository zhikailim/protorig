"""
test_node_agent.py — the real agent over DDS, controlling real test apps
(behaviour rows B1-B27 in README.md; the decision rules themselves are proven
in test_control.py). Runs on a throwaway copy of the repo whose only scenario
is built here, so no test depends on a real demo.

Node "pi" runs one small probe app under several names, each misbehaving in
its own way through its run: arguments.
"""
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

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
dds = pytest.importorskip("rti.connextdds")
from fw import types as T          # noqa: E402
from fw.testing import wait_for    # noqa: E402

SCEN = "agent-sample"
V = "Variant.Temperature.LongHistory"
UNI = "Fehler: Größenüberschreitung " + "ü" * 120          # > 128 bytes, multi-byte characters
PI_RUN = ["mon", "worker --crash-after 1", "quiet --no-heartbeat", "stubborn --ignore-stop",
          "hanger --hang-after 1.5", f"badvar --fail-variant {V}", "flood --flood",
          f"uni --crash-after 1 --crash-text '{UNI}'", "missing_app", "cpp_thing"]
PROBE = '''import sys, time
from pathlib import Path
from fw.app import App
name = Path(__file__).resolve().parent.name
no_beat = "--no-heartbeat" in sys.argv
app = App(name, "agent test probe", heartbeat=not no_beat)
app.arg("--no-heartbeat", False, "no heartbeat")
crash_after = app.arg("--crash-after", 0.0, "exit 3 after this many seconds")
crash_text = app.arg("--crash-text", "boom", "last line before crashing")
ignore = app.arg("--ignore-stop", False, "ignore Ctrl-C")
hang_after = app.arg("--hang-after", 0.0, "freeze after this many seconds")
fail_variant = app.arg("--fail-variant", "", "exit 3 at start on this variant")
flood = app.arg("--flood", False, "print as fast as possible")
if fail_variant and app.qos_variant == fail_variant:
    print("this variant fails", flush=True)
    sys.exit(3)
t0 = time.monotonic()
def tick():
    if crash_after and time.monotonic() - t0 > crash_after:
        print(crash_text, flush=True)
        import os; os._exit(3)
    if hang_after and time.monotonic() - t0 > hang_after:
        time.sleep(3600)
    if flood:
        for i in range(2000):
            print(f"flood line {i} " + "x" * 80)
app.every(0.05, tick)
if ignore:
    import signal
    for s in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
        if s is not None:
            signal.signal(s, signal.SIG_IGN)
sys.exit(app.run())
'''


@pytest.fixture(scope="module")
def agent_repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("agent") / "repo"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "build", ".venv", ".local"))
    for d in (root / "scenarios").iterdir():
        if d.is_dir():
            shutil.rmtree(d)
    s = root / "scenarios" / SCEN
    s.mkdir(parents=True)
    (s / "scenario.yaml").write_text(yaml.safe_dump({
        "description": "node agent tests", "domain": 0,
        "nodes": {"pi": {"ip": "127.0.0.1", "os": "linux", "run": PI_RUN},
                  "vm": {"ip": "203.0.113.9", "os": "linux", "run": ["mon"]}}}, allow_unicode=True),
        encoding="utf-8")                    # explicit: Windows would otherwise write its old code page
    for name in ("mon", "worker", "quiet", "stubborn", "hanger", "badvar", "flood", "uni"):
        d = s / "apps" / "tooling" / name
        d.mkdir(parents=True)
        (d / "main.py").write_text(PROBE, encoding="utf-8")
    (root / "apps" / "vehicle" / "cpp_thing").mkdir(parents=True)
    (root / "apps" / "vehicle" / "cpp_thing" / "CMakeLists.txt").write_text("# stub\n", encoding="utf-8")
    return root


class Rows:
    """Every _sys/AppState row from the pi agent: latest value and instance state."""

    def __init__(self, bus):
        self.r = dds.DataReader(bus._sub, bus._topic("_sys/AppState"),
                                bus.provider.get_topic_datareader_qos("_sys/AppState"))
        self.rows, self.inst, self._h = {}, {}, {}
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                for data, info in self.r.take():
                    if info.valid:
                        self.rows[data.app] = data
                        self._h[info.instance_handle] = data.app
                    app = self._h.get(info.instance_handle)
                    if app:
                        self.inst[app] = str(info.state.instance_state)
            except dds.Error:
                pass
            time.sleep(0.02)

    def state(self, app):
        r = self.rows.get(app)
        return r.state.name if r else None

    def wait(self, app, state, timeout=10, detail=None):
        wait_for(lambda: self.state(app) == state and (detail is None or detail in self.rows[app].detail),
                 timeout, f"{app} {state}{' with ' + repr(detail) if detail else ''} (now {self.state(app)}: "
                          f"{getattr(self.rows.get(app), 'detail', '')!r})")
        return self.rows[app]

    def close(self):
        self._stop.set()
        time.sleep(0.05)
        self.r.close()


@pytest.fixture
def rows(bus):
    r = Rows(bus)
    yield r
    r.close()


@pytest.fixture
def start_agent(start_app, agent_repo):
    def start(*extra, node="pi"):
        return start_app("node_agent", "--scenario", SCEN, "--stop-grace", "2", "--start-timeout", "4",
                         "--fail-window", "1.5", *extra, node=node,
                         folder=agent_repo / "apps" / "tooling" / "node_agent",
                         env={"PYTHONPATH": str(agent_repo / "libs" / "py")})
    return start


def send(bus, agent, command, app, arg="", node="pi"):
    bus._cmd_id += 1
    bus.send("_sys/DemoControl", T.DemoControl(target_node=node, target_app=app, cmd_id=bus._cmd_id,
                                               command=T.Command[command], arg=arg), to=agent)


# --- start-up ---------------------------------------------------------------------------

def test_B1_rows_at_start(bus, rows, start_agent):
    start_agent()
    rows.wait("mon", "APP_NOT_RUNNING")
    assert rows.wait("missing_app", "APP_UNAVAILABLE").detail.startswith("no such app")
    assert "not built" in rows.wait("cpp_thing", "APP_UNAVAILABLE").detail
    own = rows.wait("node_agent", "APP_RUNNING")
    assert own.scenario == SCEN and own.node == "pi"


# --- commands ---------------------------------------------------------------------------

def test_B2_B24_start_running_and_console(bus, rows, start_agent):
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "mon")
    rows.wait("mon", "APP_RUNNING")
    wait_for(lambda: any(l.startswith("pi/mon | ") and l.endswith(" running") for l in agent.lines), 5,
             "the app's output, prefixed pi/mon")
    banners = [l for l in agent.lines if l.startswith("pi/") and "[RTI LICENSE]" in l]
    assert len(banners) == 1, banners                    # apps' license banner shown once (N8)


def test_B5_stop_and_forced_stop(bus, rows, start_agent):
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "mon")
    send(bus, agent, "CMD_START_APP", "stubborn")
    rows.wait("mon", "APP_RUNNING")
    rows.wait("stubborn", "APP_RUNNING")
    send(bus, agent, "CMD_STOP_APP", "mon")
    send(bus, agent, "CMD_STOP_APP", "stubborn")
    assert "stopped by test/bus" in rows.wait("mon", "APP_STOPPED").detail
    assert rows.wait("stubborn", "APP_KILLED", timeout=8).detail == "forced after 2 s"


def test_B6_kill_with_simulated_crash_alert(bus, rows, start_agent):
    alerts = bus.listen("Alert")
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "mon")
    rows.wait("mon", "APP_RUNNING")
    send(bus, agent, "CMD_KILL_APP", "mon")
    assert "killed by test/bus" in rows.wait("mon", "APP_KILLED").detail
    wait_for(lambda: any(a.alert_id == "crash:mon" and "simulated" in a.message for a in alerts.all()), 5,
             "the simulated-crash alert")
    a = next(a for a in alerts.all() if a.alert_id == "crash:mon")
    assert a.source == "pi/node_agent" and a.severity == T.Severity.SEVERITY_CRITICAL


def test_B9_refusals_are_logged(bus, rows, start_agent):
    agent = start_agent()
    send(bus, agent, "CMD_KILL_APP", "node_agent")
    send(bus, agent, "CMD_START_APP", "not_in_list")
    wait_for(lambda: sum("refused" in l for l in agent.lines) >= 2, 5, "two refusals")
    assert rows.state("node_agent") == "APP_RUNNING" and not agent.exited()


# --- the sender (N11) -------------------------------------------------------------------

def _participant_named(bus, name):
    pqos = bus.provider.participant_qos
    pqos.participant_name.name = name
    p = dds.DomainParticipant(bus.domain, pqos)
    w = dds.DataWriter(dds.Publisher(p), dds.Topic(p, "_sys/DemoControl", T.DemoControl),
                       bus.provider.get_topic_datawriter_qos("_sys/DemoControl"))
    return p, w


@pytest.mark.parametrize("sender,mon_survives", [("pi/mon", True), ("nonsense", False)])
def test_B10_B12_sender_skipped_unknown_acted_on(bus, rows, start_agent, sender, mon_survives):
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "mon")
    send(bus, agent, "CMD_START_APP", "quiet")
    rows.wait("mon", "APP_RUNNING")
    rows.wait("quiet", "APP_RUNNING")
    p, w = _participant_named(bus, sender)
    try:
        def agent_matched():                         # the AGENT's reader: apps' readers may match first
            for h in w.matched_subscriptions:
                try:
                    if w.matched_subscription_participant_data(h).participant_name.name == "pi/node_agent":
                        return True
                except dds.Error:
                    pass
            return False
        wait_for(agent_matched, 5, "the agent's reader")
        w.write(T.DemoControl(target_node="pi", target_app="*", cmd_id=random.getrandbits(63),
                              command=T.Command.CMD_STOP_APP))
        rows.wait("quiet", "APP_STOPPED")
        time.sleep(0.5)
        assert (rows.state("mon") == "APP_RUNNING") == mon_survives, rows.state("mon")
        if mon_survives:
            assert any("not stopping mon: it sent the command" in l for l in agent.lines)
        else:
            assert any("from unknown" in l for l in agent.lines)
    finally:
        p.close()


# --- supervision and alerts -------------------------------------------------------------

def test_B13_crash_reported_others_carry_on(bus, rows, start_agent):
    alerts = bus.listen("Alert")
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "mon")
    send(bus, agent, "CMD_START_APP", "worker")
    row = rows.wait("worker", "APP_CRASHED", timeout=10)
    assert row.exit_code == 3 and row.detail == "boom"
    wait_for(lambda: any(a.alert_id == "crash:worker" for a in alerts.all()), 5, "the crash alert")
    time.sleep(1.5)
    assert rows.state("worker") == "APP_CRASHED"                              # not restarted
    assert rows.state("mon") == "APP_RUNNING"


def test_B15_hang_detected(bus, rows, start_agent):
    alerts = bus.listen("Alert")
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "hanger")
    rows.wait("hanger", "APP_RUNNING", detail="not responding", timeout=10)
    wait_for(lambda: any(a.alert_id == "hang:hanger" for a in alerts.all()), 5, "the hang alert")


def test_B21_long_unicode_crash_line_still_published(bus, rows, start_agent):
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "uni")
    row = rows.wait("uni", "APP_CRASHED", timeout=10)
    assert row.detail.startswith("Fehler: Größenüberschreitung") and row.detail.endswith("…")
    assert len(row.detail.encode()) <= 128
    assert not any("couldn't publish" in l for l in agent.lines)


# --- variant switching (N13) ------------------------------------------------------------

def test_B17_switch_success(bus, rows, start_agent):
    beats = bus.listen("_sys/NodeStatus")
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "mon")
    rows.wait("mon", "APP_RUNNING")
    send(bus, agent, "CMD_SET_QOS_VARIANT", "mon", V)
    wait_for(lambda: any(b.app == "mon" and b.qos_variant == V for b in beats.all()), 10, "mon on the new variant")
    row = rows.wait("mon", "APP_RUNNING")
    assert row.restarts == 1


def test_B19_failed_switch_rolls_back(bus, rows, start_agent):
    alerts = bus.listen("Alert")
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "badvar")
    rows.wait("badvar", "APP_RUNNING")
    send(bus, agent, "CMD_SET_QOS_VARIANT", "badvar", V)
    row = rows.wait("badvar", "APP_RUNNING", detail="rolled back", timeout=15)
    assert row.restarts == 2
    wait_for(lambda: any(a.alert_id == "var:badvar" and "rolled back" in a.message for a in alerts.all()), 5,
             "the rollback alert")


def test_B19_app_without_heartbeat_switches_by_surviving(bus, rows, start_agent):
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "quiet")
    rows.wait("quiet", "APP_RUNNING")
    send(bus, agent, "CMD_SET_QOS_VARIANT", "quiet", V)
    wait_for(lambda: rows.state("quiet") == "APP_RESTARTING", 5, "the switch to start")
    row = rows.wait("quiet", "APP_RUNNING", timeout=10)
    assert row.restarts == 1


# --- two agents, scenario changes, output -----------------------------------------------

def test_B22_two_agents_one_leaves(bus, rows, start_agent, start_app):
    alerts = bus.listen("Alert")
    a1 = start_agent()
    a2 = start_agent()
    wait_for(lambda: a1.exited() or a2.exited(), 10, "one of the two agents to leave")
    time.sleep(1.0)
    assert a1.exited() != a2.exited(), "exactly one must stay"
    leaver = a1 if a1.exited() else a2
    assert leaver.returncode == 0 and any("this one leaves" in l for l in leaver.lines)
    wait_for(lambda: any(a.alert_id == "agents" and a.source == "pi/node_agent" for a in alerts.all()), 5, "the two-agents alert")


def test_B23_scenario_change_is_only_logged(bus, rows, start_agent, agent_repo):
    agent = start_agent()
    rows.wait("mon", "APP_NOT_RUNNING")
    f = agent_repo / "scenarios" / SCEN / "scenario.yaml"
    original = f.read_text(encoding="utf-8")
    try:
        f.write_text(original + "\n# changed\n", encoding="utf-8")
        wait_for(lambda: any("restart the agent to use it" in l for l in agent.lines), 6, "the warning")
        send(bus, agent, "CMD_START_APP", "mon")
        rows.wait("mon", "APP_RUNNING")
    finally:
        f.write_text(original, encoding="utf-8")


def test_B25_flooding_app_never_blocks_the_agent(bus, rows, start_agent):
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "flood")
    rows.wait("flood", "APP_RUNNING")
    time.sleep(1.0)
    t0 = time.monotonic()
    send(bus, agent, "CMD_STOP_APP", "flood")
    rows.wait("flood", "APP_STOPPED", timeout=5)
    assert time.monotonic() - t0 < 4


def test_B25_console_drops_and_counts_when_printing_falls_behind():
    sys.path.insert(0, str(HERE))
    import main as agent_main
    printed = []
    def slow_out(line):                      # a console that can't keep up
        time.sleep(0.001)
        printed.append(line)
    c = agent_main.Console(out=slow_out)
    for i in range(agent_main.CONSOLE_LINES * 3):
        c.put("pi/flood", f"line {i}")
    wait_for(lambda: any("lines dropped" in p for p in printed), 10, "the dropped-lines notice")


# --- stop and death ---------------------------------------------------------------------

def test_B26_ctrl_c_stops_apps_clears_alerts_disposes_rows(bus, rows, start_agent):
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "mon")
    send(bus, agent, "CMD_START_APP", "worker")
    rows.wait("mon", "APP_RUNNING")
    rows.wait("worker", "APP_CRASHED", timeout=10)                          # leaves an alert
    agent.interrupt()
    assert agent.wait_exit(15) == 0
    wait_for(lambda: all("DISPOSED" in rows.inst.get(a, "") for a in ("mon", "worker", "node_agent")), 5,
             f"rows disposed (now {rows.inst})")
    assert any("pi/mon" in l and "stopped" in l for l in agent.lines)


def test_B27_apps_stop_when_the_agent_is_killed(bus, rows, start_agent):
    beats = bus.listen("_sys/NodeStatus")
    agent = start_agent()
    send(bus, agent, "CMD_START_APP", "mon")
    rows.wait("mon", "APP_RUNNING")
    agent.kill()
    agent.wait_exit(5)
    mark = len(beats.all())
    time.sleep(3.0)                                  # launcher watch: about 1 s
    assert not any(b.app == "mon" for b in beats.all()[mark + 2:]), "mon kept running without its agent"


# --- fuzz ------------------------------------------------------------------------------

def test_fuzz_commands(bus, rows, start_agent):
    """300 random commands (any target, id, text, variant) from the bus: the
    agent never crashes, keeps heartbeating, and still obeys a real stop at the end."""
    rnd = random.Random(5)
    agent = start_agent()
    w = bus.writer("_sys/DemoControl")
    wait_for(lambda: w.publication_matched_status.current_count > 0, 5, "a match")
    time.sleep(0.3)
    apps = [e.split()[0] for e in PI_RUN] + ["*", "node_agent", "", "x" * 31, "../x", "Ä"]
    for _ in range(300):
        w.write(T.DemoControl(target_node=rnd.choice(["pi", "*", "vm", ""]), target_app=rnd.choice(apps),
                              cmd_id=rnd.getrandbits(64), command=rnd.choice(list(T.Command)),
                              arg=rnd.choice(["", V, "Variant.Nope", "ü" * 30]), value=rnd.random()))
        time.sleep(0.005)
    time.sleep(3)
    assert not agent.exited(), agent.output[-3000:]
    own = [l for l in agent.lines if not l.startswith("pi/")]       # the agent's lines, not its apps'
    assert not any("Traceback" in l or "ERROR" in l for l in own), "\n".join(own[-60:])
    # (An app stopped while still loading Connext prints "ImportError: initialization
    # failed": that's the app being stopped mid-start, correctly recorded as STOPPED.)
    send(bus, agent, "CMD_START_APP", "mon")
    send(bus, agent, "CMD_STOP_APP", "*")
    rows.wait("mon", "APP_STOPPED", timeout=10)
