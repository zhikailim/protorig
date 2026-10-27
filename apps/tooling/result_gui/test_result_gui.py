"""
test_result_gui.py — result_gui over DDS, the real window running headless
(Qt's "offscreen" platform). Behaviour rows in README.md: G1-G8, G12-G15, G20-G23, G28-G29, R6; G4-G6 need
to act on the window itself and are tested in-process at the end.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
dds = pytest.importorskip("rti.connextdds")
pytest.importorskip("PySide6", reason="needs PySide6: pip install -r bootstrap/requirements.txt")
from fw.app import DDS_ERRORS      # noqa: E402
from fw.testing import wait_for    # noqa: E402

HEADLESS = {"QT_QPA_PLATFORM": "offscreen", "QT_LOGGING_RULES": "qt.qpa.*=false"}
NODE = "windows"


def heartbeat_states(bus, node=NODE, app="result_gui"):
    """Every state of this app's heartbeat instance, with when it was seen."""
    r = dds.DataReader(bus._sub, bus._topic("_sys/NodeStatus"), bus.provider.get_topic_datareader_qos("_sys/NodeStatus"))
    seen, stop, mine = [], threading.Event(), set()

    def loop():
        while not stop.is_set():
            try:
                for d, info in r.take():
                    if info.valid and (d.node, d.app) == (node, app):
                        mine.add(info.instance_handle)
                        seen.append(("alive", time.monotonic()))
                    elif not info.valid and info.instance_handle in mine:
                        seen.append((str(info.state.instance_state), time.monotonic()))
            except DDS_ERRORS:
                pass
            time.sleep(0.05)
    t = threading.Thread(target=loop, daemon=True)
    t.start()

    def close():
        stop.set()
        t.join(2)
        r.close()
    return seen, close


def start(start_app, *args, **kw):
    return start_app("result_gui", *args, node=NODE, env=HEADLESS, **kw)


def test_G1_starts_with_a_window_and_heartbeats(bus, start_app):
    app = start(start_app)                       # waits for the first heartbeat
    wait_for(lambda: "window shown" in app.output, 5, "the window")
    assert "ERROR" not in app.output and "Traceback" not in app.output, app.output


def test_G2_interrupt_stops_cleanly(bus, start_app):
    """Ctrl-C on Linux, Ctrl-Break on Windows (Supervised.interrupt sends the right one)."""
    seen, close = heartbeat_states(bus)
    try:
        app = start(start_app)
        wait_for(lambda: "window shown" in app.output and any(k == "alive" for k, _ in seen), 10, "a running window")
        t0 = time.monotonic()
        app.interrupt()
        assert app.wait_exit(5) == 0, app.output
        assert time.monotonic() - t0 < 2.0, "should stop within about a second"
        wait_for(lambda: any("DISPOSED" in k for k, _ in seen), 3, "the heartbeat disposed (a clean stop)")
        assert "stopped" in app.output and "Traceback" not in app.output, app.output
    finally:
        close()


LAUNCHER = '''
import os, sys, time
sys.path.insert(0, {libs!r})
from fw.supervise import Supervised
s = Supervised([sys.executable, {main!r}], ["--node", "{node}", "--domain", "{domain}"], dict(os.environ))
print("CHILD", s.proc.pid, flush=True)
time.sleep(120)
'''


def test_G3_stops_when_its_launcher_dies(bus):
    from fw.supervise import process_alive
    seen, close = heartbeat_states(bus)
    env = dict(os.environ, PYTHONPATH=str(REPO / "libs" / "py"), PYTHONUNBUFFERED="1", **HEADLESS)
    code = LAUNCHER.format(libs=str(REPO / "libs" / "py"), main=str(HERE / "main.py"), node=NODE, domain=bus.domain)
    launcher = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    child = None
    try:
        child = int(launcher.stdout.readline().split()[1])
        wait_for(lambda: any(k == "alive" for k, _ in seen), 15, "result_gui's heartbeat")
        launcher.kill()
        launcher.wait()
        wait_for(lambda: any("DISPOSED" in k for k, _ in seen), 6, "a clean stop of the orphaned window")
        wait_for(lambda: not process_alive(child), 5, "the process to end")
    finally:
        close()
        if launcher.poll() is None:
            launcher.kill()
        if child and process_alive(child):
            import signal
            os.kill(child, getattr(signal, "SIGKILL", signal.SIGTERM))


def test_G7_unknown_argument_exits_2(bus, start_app):
    app = start(start_app, "--bogus", wait_heartbeat=False)
    assert app.wait_exit(10) == 2 and "unknown argument" in app.output, app.output


def test_help_shows_no_window(bus, start_app):
    app = start(start_app, "--help", wait_heartbeat=False)
    assert app.wait_exit(10) == 0 and "--scenario" in app.output and "window shown" not in app.output, app.output


def test_G8_without_pyside_says_how_to_install(bus, start_app, tmp_path):
    """PySide6 missing: a clear message and exit 2, never a traceback."""
    fake = tmp_path / "PySide6"
    fake.mkdir()
    (fake / "__init__.py").write_text("raise ImportError('No module named PySide6 (test)')\n", encoding="utf-8")
    env = dict(HEADLESS, PYTHONPATH=f"{tmp_path}{os.pathsep}{REPO / 'libs' / 'py'}")
    app = start_app("result_gui", node=NODE, env=env, wait_heartbeat=False)
    assert app.wait_exit(10) == 2, app.output
    assert "pip install -r bootstrap/requirements.txt" in app.output and "Traceback" not in app.output, app.output


# --- the rig as seen over DDS (G10-G15), the real app on temperature-skeleton -------
# Lines checked are the app's log: one per visible change (R7). The drawing itself
# is checked in test_drawing.py.

SCENARIO = ("--scenario", "temperature-skeleton")
STAND_IN = """
from fw.app import App
from fw import types as T
app = App("hpc_monitor", "a stand-in with a heartbeat, for result_gui's tests")
if app.arg("--writes-alert", False):
    w = app.writer("Alert")
    app.every(0.5, lambda: w.write(T.Alert(source=app.who, alert_id="test")))
if app.arg("--reads-temperature", False):
    app.on_data(app.reader("Example Temperature"), lambda s: None)
if app.arg("--reads-alert", False):
    app.on_data(app.reader("Alert"), lambda s: None)
raise SystemExit(app.run())
"""


@pytest.fixture
def stand_in(tmp_path):
    d = tmp_path / "hpc_monitor"
    d.mkdir()
    (d / "main.py").write_text(STAND_IN, encoding="utf-8")
    return d


def said(app, text, seconds):
    """Wait for a line in the app's log (a regex); on time-out, show the log."""
    import re
    try:
        wait_for(lambda: re.search(text, app.output), seconds, repr(text))
    except AssertionError as e:
        raise AssertionError(f"{e}\n--- {app.name}'s output ---\n{app.output}") from None


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGSTOP"), reason="pausing a process needs SIGSTOP (Linux)")
def test_G12_G13_lost_then_back_with_no_restarts(bus, start_app, stand_in):
    """A paused process stands in for a pulled cable: heartbeats stop, nothing restarts."""
    import re
    import signal
    gui = start(start_app, *SCENARIO)
    pi = start_app("hpc_monitor", node="hpc-pi", folder=stand_in)
    said(gui, "machine hpc-pi: up", 5)
    os.kill(pi.proc.pid, signal.SIGSTOP)
    t0 = time.monotonic()
    try:
        said(gui, "machine hpc-pi: lost", 6)
        assert time.monotonic() - t0 < 4.5, "lost should show about 3 s after the last heartbeat"
        assert re.search(r"caption: hpc-pi lost after 3\.\d s · everything else carries on", gui.output), gui.output
    finally:
        os.kill(pi.proc.pid, signal.SIGCONT)
    said(gui, r"caption: hpc-pi back after \d+ s, no restarts", 5)


def test_G12_G13_killed_then_restarted(bus, start_app, stand_in):
    import re
    gui = start(start_app, *SCENARIO)
    pi = start_app("hpc_monitor", node="hpc-pi", folder=stand_in)
    said(gui, "machine hpc-pi: up", 5)
    pi.kill()
    said(gui, "machine hpc-pi: lost", 6)
    start_app("hpc_monitor", node="hpc-pi", folder=stand_in)
    said(gui, r"caption: hpc-pi back after \d+ s, apps restarted", 10)
    assert "Traceback" not in gui.output and "ERROR" not in gui.output, gui.output


def test_G14_a_crash_reported_by_the_agent(bus, start_app):
    from fw import types as T
    gui = start(start_app, *SCENARIO)
    row = dict(node="hpc-vm", app="hpc_monitor", scenario="temperature-skeleton")
    w = bus.writer("_sys/AppState")
    try:
        bus.send("_sys/AppState", T.AppState(state=T.AppStateKind.APP_RUNNING, **row), to=gui)
        said(gui, "app hpc-vm/hpc_monitor: running", 5)
        bus.send("_sys/AppState", T.AppState(state=T.AppStateKind.APP_CRASHED, exit_code=1, **row), to=gui)
        said(gui, "caption: hpc_monitor on hpc-vm crashed", 5)
        assert "app hpc-vm/hpc_monitor: crashed" in gui.output
    finally:                       # the bus is shared by every test: leave no row behind
        w.dispose_instance(w.lookup_instance(T.AppState(**row)))


def test_G15_external_device_data_and_silence(bus, start_app):
    import re
    gui = start(start_app, *SCENARIO)
    twin = start_app("tc397_twin", node="tc397", wait_heartbeat=False)
    said(gui, "external tc397: data arriving", 10)
    twin.kill()
    t0 = time.monotonic()
    said(gui, "external tc397: no data", 5)
    assert time.monotonic() - t0 < 3.5, "no data should show about 2 s after the last sample"
    assert re.search(r"caption: tc397: no data for 2\.\d s", gui.output), gui.output


def test_G21_G23_links_seen_over_dds(bus, start_app, stand_in):
    """Real writers and readers in three processes: result_gui logs one line per link,
    an ECU twin's link from the device itself; a stopped writer's link goes."""
    gui = start(start_app, *SCENARIO)
    start_app("tc397_twin", node="tc397", wait_heartbeat=False)
    vm = start_app("hpc_monitor", "--reads-temperature", "--writes-alert", node="hpc-vm", folder=stand_in)
    start_app("hpc_monitor", "--reads-alert", node="hpc-pi", folder=stand_in)
    said(gui, "link Example Temperature: tc397 -> hpc-vm/hpc_monitor", 10)
    said(gui, "link Alert: hpc-vm/hpc_monitor -> hpc-pi/hpc_monitor", 10)
    assert "_sys/" not in "".join(l for l in gui.output.splitlines() if " link " in l), "plumbing is never drawn"
    vm.terminate()
    said(gui, "link gone Alert: hpc-vm/hpc_monitor -> hpc-pi/hpc_monitor", 5)
    assert "Traceback" not in gui.output and "ERROR" not in gui.output, gui.output


def test_G28_link_lit_while_data_flows_dim_when_it_stops(bus, start_app, stand_in):
    """The writer's own heartbeat says how fast it writes (R5): lit while it does."""
    gui = start(start_app, *SCENARIO)
    start_app("hpc_monitor", "--writes-alert", node="hpc-vm", folder=stand_in)
    start_app("hpc_monitor", "--reads-alert", node="hpc-pi", folder=stand_in)
    said(gui, "link lit Alert: hpc-vm/hpc_monitor -> hpc-pi/hpc_monitor", 10)
    assert "link dim Alert" not in gui.output


def test_G29_alert_banner_over_dds(bus, start_app):
    from fw import types as T
    gui = start(start_app, *SCENARIO)
    key = dict(source="hpc-vm/hpc_monitor", alert_id="temp_high")
    w = bus.writer("Alert")
    try:
        bus.send("Alert", T.Alert(severity=T.Severity.SEVERITY_CRITICAL, message="Temperature high: 14.7 C", **key), to=gui)
        said(gui, r"alert CRITICAL: Temperature high: 14\.7 C \(hpc-vm/hpc_monitor\)", 5)
        w.dispose_instance(w.lookup_instance(T.Alert(**key)))
        said(gui, r"alert cleared: Temperature high: 14\.7 C", 5)
    finally:                                   # the bus is shared by every test: leave no alert behind
        h = w.lookup_instance(T.Alert(**key))
        if h != dds.InstanceHandle.nil():
            w.dispose_instance(h)


def test_R6_result_gui_own_cpu(bus, start_app, stand_in):
    """Drawing a busy screen ten times a second: its own heartbeat reports its CPU."""
    beats = bus.listen("_sys/NodeStatus")
    gui = start(start_app, *SCENARIO)
    start_app("tc397_twin", node="tc397", wait_heartbeat=False)
    start_app("hpc_monitor", "--reads-temperature", "--writes-alert", node="hpc-vm", folder=stand_in)
    start_app("hpc_monitor", "--reads-alert", node="hpc-pi", folder=stand_in)
    said(gui, "link lit Alert", 10)
    time.sleep(6)
    loads = [x.cpu_load for x in beats.all() if x.app == "result_gui" and x.node == NODE][-5:]
    print(f"\nresult_gui CPU (one core = 1.0): {', '.join(f'{x:.3f}' for x in loads)}")
    assert loads and max(loads) < 0.25, "result_gui should take well under a quarter of one core"


def test_G20_bad_scenario_exits_2_with_a_reason(bus, start_app):
    app = start(start_app, "--scenario", "no-such-scenario", wait_heartbeat=False)
    assert app.wait_exit(10) == 2 and "can't read" in app.output and "Traceback" not in app.output, app.output


# --- in-process: acting on the window itself (G4, G5, G6) ----------------------------

@pytest.fixture
def gui_app(bus, monkeypatch):
    """An fw.App on the bus, inside this test process, with signal handlers restored after."""
    import signal
    for k, v in HEADLESS.items():
        monkeypatch.setenv(k, v)
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)) if s}
    from fw.app import App
    app = App("result_gui", "test", argv=["--node", NODE, "--domain", str(bus.domain)])
    yield app
    app.stop("test over")
    for s, h in saved.items():
        signal.signal(s, h)


def window_module():
    sys.path.insert(0, str(HERE))
    import window
    return window


def test_G4_closing_the_window_stops_the_app(bus, gui_app):
    from PySide6 import QtCore
    from fw.gui import run_gui
    seen, close = heartbeat_states(bus)
    made = []

    def make():
        w = window_module().Window("test-scenario")
        made.append(w)
        QtCore.QTimer.singleShot(2500, w.close)          # the user closes it
        QtCore.QTimer.singleShot(20000, lambda: gui_app.stop("safety: the test would hang"))
        return w
    try:
        t0 = time.monotonic()
        assert run_gui(gui_app, make) == 0
        assert time.monotonic() - t0 < 10, "closing the window didn't stop the app (the safety stop did)"
        assert made and made[0].windowTitle() == "protorig · test-scenario"
        wait_for(lambda: any("DISPOSED" in k for k, _ in seen), 3, "the heartbeat disposed")
    finally:
        close()


def test_G5_a_frozen_window_pauses_the_heartbeat(bus, gui_app):
    from PySide6 import QtCore
    from fw.gui import FROZEN, run_gui
    seen, close = heartbeat_states(bus)

    def make():
        w = window_module().Window("test-scenario")
        QtCore.QTimer.singleShot(2000, lambda: time.sleep(FROZEN + 2.5))   # the window freezes
        QtCore.QTimer.singleShot(9500, w.close)
        QtCore.QTimer.singleShot(30000, lambda: gui_app.stop("safety: the test would hang"))
        return w
    try:
        t0 = time.monotonic()
        assert run_gui(gui_app, make) == 0
        assert time.monotonic() - t0 < 16, "closing the window didn't stop the app (the safety stop did)"
        beats = [t for k, t in seen if k == "alive"]
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        assert gaps and max(gaps) >= 2.5, f"no pause while frozen: {gaps}"
        assert beats[-1] - (beats[0] + 2.0 + FROZEN + 2.5) > 0, "the heartbeat should resume after the freeze"
    finally:
        close()


def test_G6_f11_toggles_full_screen(monkeypatch):
    """The key goes in through the window system, as a real keyboard's would.
    Limit: headless Qt always gives the window focus, so the Windows failure of
    22 Oct 2026 (F11 did nothing) can't be reproduced here; it's checked by hand
    on Windows. This test catches a missing or broken shortcut."""
    for k, v in HEADLESS.items():
        monkeypatch.setenv(k, v)
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtTest import QTest
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = window_module().Window("x")
    w.show()
    assert QTest.qWaitForWindowActive(w, 3000), "the window should make itself active when shown"
    QTest.keyClick(w.windowHandle(), QtCore.Qt.Key.Key_F11)
    assert w.isFullScreen()
    QTest.keyClick(w.windowHandle(), QtCore.Qt.Key.Key_F11)
    assert not w.isFullScreen()
    w.close()
