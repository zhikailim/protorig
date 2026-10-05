"""
test_python_layer.py — tests for fw.app, fw.testing, templates and `protorig new`.

The fw.app tests start a small probe app (written into a temp folder) as a
real process and drive it over DDS, exactly as app tests do. They need the
Connext Python package and a license; without them they are skipped.
"""
import math
import random
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SAMPLE = "sample-scenario"          # the framework tests' own scenario (never a real demo's name)
sys.path.insert(0, str(REPO / "libs" / "py"))
sys.path.insert(0, str(REPO / "cli"))

PROBE = '''
import os
import sys
from fw.app import App
from fw import types as T
# PROBE_OBEYS="CMD_STOP_APP,CMD_KILL_APP": opt in, like a sim twin (N14)
obeys = {T.Command[c] for c in os.environ["PROBE_OBEYS"].split(",")} if os.environ.get("PROBE_OBEYS") else None
app = App("probe", "test probe", **({"obeys": obeys} if obeys is not None else {}))
rate = app.arg("--rate", 5.0, "Hz")
seen = []
app.on_param("rate", lambda v: print(f"PARAM rate={v}", flush=True))
alerts = app.writer("Alert")
inp = app.reader("Example Temperature")
def on_temp(s):
    if s.temperature < 0:
        raise RuntimeError("below zero, on purpose")
    alerts.write(T.Alert(source=app.who, alert_id="ECHO", message="echo", value=s.temperature))
app.on_data(inp, on_temp)
sys.exit(app.run())
'''


@pytest.fixture
def probe_dir(tmp_path):
    d = tmp_path / "probe"
    d.mkdir()
    (d / "main.py").write_text(PROBE)
    return d


def _start(start_app, probe_dir, *args, **kw):
    return start_app("probe", *args, folder=probe_dir, **kw)


# --- fw.app behaviour ---------------------------------------------------------------

def test_probe_runs_and_echoes(bus, start_app, probe_dir):
    from fw.testing import wait_for
    out = bus.listen("Alert")
    app = _start(start_app, probe_dir)
    bus.send("Example Temperature", {"temperature": 31.5}, to=app)
    wait_for(lambda: out.count() > 0, 3, "the echo alert")
    assert out.last().value == 31.5 and out.last().source == "test-node/probe"


def test_participant_is_named_node_slash_app(bus, start_app, probe_dir):
    import rti.connextdds as dds
    _start(start_app, probe_dir, node="hpc-vm")
    names = [bus.participant.discovered_participant_data(h).participant_name.name
             for h in bus.participant.discovered_participants()]
    assert "hpc-vm/probe" in names


def test_callback_exception_does_not_kill_the_app(bus, start_app, probe_dir):
    from fw.testing import wait_for
    out = bus.listen("Alert")
    app = _start(start_app, probe_dir)
    bus.send("Example Temperature", {"temperature": -1.0}, to=app)   # raises inside on_data
    bus.send("Example Temperature", {"temperature": 29.0}, to=app)   # must still be handled
    wait_for(lambda: out.count() > 0, 3, "an alert after the error")
    assert not app.exited()
    assert "below zero, on purpose" in app.output


def _send(bus, app, node, target, command, arg="", value=0.0):
    from fw import types as T
    bus._cmd_id += 1
    bus.send("_sys/DemoControl", T.DemoControl(target_node=node, target_app=target, cmd_id=bus._cmd_id,
                                               command=command, arg=arg, value=value), to=app)


def test_commands_for_others_are_ignored_silently(bus, start_app, probe_dir):
    """Every app receives every command: those for other apps, other nodes or
    "*" apps are dropped without a word (N14), not even a log line."""
    from fw import types as T
    app = _start(start_app, probe_dir, node="hpc-vm")
    p = T.Command.CMD_SET_PARAM
    _send(bus, app, "hpc-pi", "probe", p, "rate", 1.0)          # other node
    _send(bus, app, "hpc-vm", "other_app", p, "rate", 2.0)      # other app
    _send(bus, app, "hpc-vm", "*", p, "rate", 3.0)              # "*" apps: parameter names differ per app
    _send(bus, app, "hpc-vm", "", T.Command.CMD_KILL_APP)      # empty
    _send(bus, app, "*", "*", T.Command.CMD_KILL_APP)
    time.sleep(1.0)
    assert not app.exited()
    assert "PARAM rate=" not in app.output and "ignoring" not in app.output, app.output


def test_wildcard_node_reaches_the_app(bus, start_app, probe_dir):
    from fw import types as T
    from fw.testing import wait_for
    app = _start(start_app, probe_dir)
    bus.command(app, T.Command.CMD_SET_PARAM, arg="rate", value=7.0, target_node="*")
    wait_for(lambda: "PARAM rate=7.0" in app.output, 3, "the parameter change via node '*'")


@pytest.mark.parametrize("command", ["CMD_STOP_APP", "CMD_KILL_APP", "CMD_START_APP", "CMD_SET_QOS_VARIANT"])
def test_lifecycle_commands_are_the_agents_job(bus, start_app, probe_dir, command):
    """N14: by default an app obeys only SET_PARAM. A start, stop, kill or variant
    switch sent to the app itself is ignored with one line saying who does it."""
    from fw import types as T
    from fw.testing import wait_for
    beats = bus.listen("_sys/NodeStatus")
    app = _start(start_app, probe_dir)
    bus.command(app, T.Command[command], arg="Variant.Temperature.LongHistory")
    wait_for(lambda: f"ignoring {command}:" in app.output, 3, "the 'ignoring' line")
    line = next(l for l in app.lines if f"ignoring {command}:" in l)
    assert "agent" in line, line
    mark = len(beats.all())
    wait_for(lambda: any(b.app == "probe" for b in beats.all()[mark:]), 3, "heartbeats to continue")
    assert not app.exited()
    assert all(b.qos_variant == "" for b in beats.all() if b.app == "probe")       # no switch happened


def test_opt_in_stop_and_kill_only_when_named_exactly(bus, start_app, probe_dir):
    """A sim twin's opt-in (N14): stop and kill are obeyed only when its node is
    named exactly, never via "*" (N4: a "stop all" leaves the twin running)."""
    from fw import types as T
    from fw.app import EXIT_KILLED
    from fw.testing import wait_for
    env = {"PROBE_OBEYS": "CMD_STOP_APP,CMD_KILL_APP"}
    app = _start(start_app, probe_dir, env=env)
    bus.command(app, T.Command.CMD_STOP_APP, target_node="*")
    bus.command(app, T.Command.CMD_KILL_APP, target_node="*")
    wait_for(lambda: app.output.count("never '*'") >= 2, 3, "both refusals")
    assert not app.exited()
    bus.command(app, T.Command.CMD_STOP_APP)                              # named exactly
    assert app.wait_exit(5) == 0
    app2 = _start(start_app, probe_dir, env=env)
    bus.command(app2, T.Command.CMD_KILL_APP)
    assert app2.wait_exit(5) == EXIT_KILLED


def test_obeys_refuses_commands_an_app_cannot_do():
    pytest.importorskip("rti.connextdds")
    from fw import types as T
    from fw.app import App
    for cmd in (T.Command.CMD_START_APP, T.Command.CMD_SET_QOS_VARIANT):
        with pytest.raises(ValueError, match="node agent"):
            App("x", obeys={cmd, T.Command.CMD_SET_PARAM}, argv=[])


# --- N3: a clean stop disposes the heartbeat; a kill doesn't -----------------------

def _heartbeat_states(bus, node):
    """A reader of the probe's heartbeat that records every instance state it sees."""
    import threading
    import rti.connextdds as dds
    r = dds.DataReader(bus._sub, bus._topic("_sys/NodeStatus"),
                       bus.provider.get_topic_datareader_qos("_sys/NodeStatus"))
    seen, stop = [], threading.Event()
    def loop():
        while not stop.is_set():
            try:
                for data, info in r.take():
                    if info.valid and (data.node, data.app) == (node, "probe"):
                        seen.append(("alive", info.instance_handle))
                    elif not info.valid and any(h == info.instance_handle for _, h in seen):
                        seen.append((str(info.state.instance_state), info.instance_handle))
            except dds.Error:
                pass
            time.sleep(0.05)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    def close():
        stop.set()
        t.join(2)
        r.close()
    return seen, close


def test_clean_stop_disposes_heartbeat(bus, start_app, probe_dir):
    from fw.testing import wait_for
    seen, close = _heartbeat_states(bus, "n3-clean")
    try:
        app = _start(start_app, probe_dir, node="n3-clean")
        wait_for(lambda: any(k == "alive" for k, _ in seen), 5, "a heartbeat")
        app.interrupt()
        assert app.wait_exit(5) == 0
        wait_for(lambda: any("DISPOSED" in k for k, _ in seen), 5, "the heartbeat to be disposed")
    finally:
        close()


def test_kill_does_not_dispose_heartbeat(bus, start_app, probe_dir):
    """A kill looks like a crash on the network: the heartbeat goes "no writers"
    (lost), never "disposed". That difference is how displays tell them apart."""
    from fw import types as T
    from fw.testing import wait_for
    seen, close = _heartbeat_states(bus, "n3-kill")
    try:
        app = _start(start_app, probe_dir, node="n3-kill", env={"PROBE_OBEYS": "CMD_KILL_APP"})
        wait_for(lambda: any(k == "alive" for k, _ in seen), 5, "a heartbeat")
        bus.command(app, T.Command.CMD_KILL_APP)
        app.wait_exit(5)
        wait_for(lambda: any("NO_WRITERS" in k for k, _ in seen), 15, "the heartbeat to be lost")
        assert not any("DISPOSED" in k for k, _ in seen), seen
    finally:
        close()


# --- N6: the app stops by itself when its launcher dies ---------------------------

LAUNCHER = '''
import os, sys, time
sys.path.insert(0, {libs!r})
from fw.supervise import Supervised
s = Supervised([sys.executable, {probe!r}], ["--node", "n6-node", "--domain", "{domain}"], dict(os.environ))
print("CHILD", s.proc.pid, flush=True)
time.sleep(120)
'''


def test_app_stops_when_its_launcher_dies(bus, probe_dir):
    """Kill the launcher outright (no chance to clean up): the app notices within
    about a second and stops cleanly, so its heartbeat is DISPOSED, not lost."""
    import os
    import signal
    from fw.supervise import process_alive
    from fw.testing import wait_for
    seen, close = _heartbeat_states(bus, "n6-node")
    env = dict(os.environ, PYTHONPATH=str(REPO / "libs" / "py"), PYTHONUNBUFFERED="1")
    code = LAUNCHER.format(libs=str(REPO / "libs" / "py"), probe=str(probe_dir / "main.py"), domain=bus.domain)
    launcher = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env)
    child = None
    try:
        child = int(launcher.stdout.readline().split()[1])
        wait_for(lambda: any(k == "alive" for k, _ in seen), 10, "the app's heartbeat")
        launcher.kill()                                  # SIGKILL / TerminateProcess
        launcher.wait()
        wait_for(lambda: any("DISPOSED" in k for k, _ in seen), 6, "a clean stop of the orphaned app")
    finally:
        close()
        if launcher.poll() is None:
            launcher.kill()
        if child and process_alive(child):
            try:
                os.kill(child, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
            except OSError:
                pass


def test_garbage_launcher_id_is_ignored(bus, start_app, probe_dir):
    from fw.supervise import LAUNCHER_ENV
    from fw.testing import wait_for
    app = _start(start_app, probe_dir, supervised=False, env={LAUNCHER_ENV: "not-a-pid"})
    wait_for(lambda: "not a process ID" in app.output, 3, "the warning")
    time.sleep(1.5)
    assert not app.exited()


def test_launcher_already_gone_at_start(bus, start_app, probe_dir):
    """Started with the ID of a process that has ended: it stops at the first check."""
    from fw.supervise import LAUNCHER_ENV
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    app = _start(start_app, probe_dir, supervised=False, env={LAUNCHER_ENV: str(p.pid)})
    assert app.wait_exit(5) == 0
    assert f"launcher (process {p.pid}) is gone: stopping" in app.output


def test_set_param(bus, start_app, probe_dir):
    from fw import types as T
    from fw.testing import wait_for
    app = _start(start_app, probe_dir)
    bus.command(app, T.Command.CMD_SET_PARAM, arg="rate", value=12.5)
    wait_for(lambda: "PARAM rate=12.5" in app.output, 3, "the parameter callback")
    bus.command(app, T.Command.CMD_SET_PARAM, arg="nope", value=1)
    wait_for(lambda: "unknown parameter 'nope'" in app.output, 3, "a warning for an unknown parameter")
    assert not app.exited()


def test_set_param_rejects_non_finite(bus, start_app, probe_dir):
    from fw import types as T
    from fw.testing import wait_for
    app = _start(start_app, probe_dir)
    bus.command(app, T.Command.CMD_SET_PARAM, arg="rate", value=float("nan"))
    wait_for(lambda: "rejected non-finite value" in app.output, 3, "NaN to be rejected")
    assert "PARAM rate=" not in app.output          # the callback never saw it


def test_incompatible_qos_is_reported(bus, start_app, probe_dir):
    """Beat B of the variant discussion: a best-effort Alert writer vs the default
    reliable readers. DDS refuses to match, and the app says why."""
    from fw.testing import wait_for
    out = bus.listen("Alert")                         # default profile: RELIABLE reader
    app = _start(start_app, probe_dir, "--qos-variant", "Variant.Alert.BestEffort")
    wait_for(lambda: "incompatible QoS on 'Alert' (writer side)" in app.output, 5, "the incompatible-QoS warning")
    line = next(l for l in app.output.splitlines() if "incompatible QoS on 'Alert'" in l)
    assert line.rstrip().lower().endswith(("reliability", "durability")), line
    assert out.count() == 0


def test_unknown_argument_exits_2(bus, start_app, probe_dir):
    app = _start(start_app, probe_dir, "--bogus", wait_heartbeat=False)
    assert app.wait_exit(10) == 2 and "unknown argument" in app.output


def test_unknown_variant_at_start_refuses(bus, start_app, probe_dir):
    app = _start(start_app, probe_dir, "--qos-variant", "Nope", wait_heartbeat=False)
    assert app.wait_exit(10) != 0 and "unknown QoS variant" in app.output


def test_help_lists_app_arguments(probe_dir):
    import os
    env = dict(os.environ, PYTHONPATH=str(REPO / "libs" / "py"))
    out = subprocess.run([sys.executable, str(probe_dir / "main.py"), "--help"], capture_output=True, text=True, env=env)
    assert out.returncode == 0 and "--rate" in out.stdout and "--qos-variant" in out.stdout


def test_domain_comes_from_scenario(tmp_path, monkeypatch):
    """Uses its own sample scenario, so renaming or deleting a real one can't break it."""
    import fw.app
    d = tmp_path / "scenarios" / "sample-scenario"
    d.mkdir(parents=True)
    (d / "scenario.yaml").write_text("domain: 7\n")
    monkeypatch.setattr(fw.app, "ROOT", tmp_path)
    assert fw.app.scenario_domain("sample-scenario") == 7
    assert fw.app.scenario_domain("no-such-scenario") is None


# --- fuzz: garbage Control Panel commands must never take the app down -------------

def test_fuzz_control_commands(bus, start_app, probe_dir):
    import time
    from fw import types as T
    from fw.testing import wait_for
    app = _start(start_app, probe_dir)
    rnd = random.Random(7)
    safe = list(T.Command)               # by default an app obeys only SET_PARAM, so even stop/kill are safe
    junk_text = ["", "rate", "x" * 64, "Variant.", "../../etc", "ñandú", "rate;rm -rf", " "]
    for i in range(200):
        cmd = rnd.choice(safe)
        arg = rnd.choice(junk_text)
        value = rnd.choice([0.0, -1.0, 1e308, float("nan"), float("inf"), 3.3])
        bus._cmd_id += 1
        bus.send("_sys/DemoControl", T.DemoControl(target_node=rnd.choice(["*", app.node]), target_app="probe",
                                                   cmd_id=bus._cmd_id, command=cmd, arg=arg[:64], value=value), to=app)
    beats = bus.listen("_sys/NodeStatus")
    wait_for(lambda: sum(1 for b in beats.all() if b.app == "probe") >= 2, 5, "heartbeats after the fuzz burst")
    assert not app.exited(), app.output


def test_make_sample_rejects_misspelt_field():
    from fw.testing import make_sample
    with pytest.raises(KeyError, match="temprature"):
        make_sample("Example Temperature", {"temprature": 1.0})
    s = make_sample("Alert", {"severity": "SEVERITY_CRITICAL"})
    assert int(s.severity) == 2


# --- templates and `protorig new` ---------------------------------------------------

@pytest.fixture
def repo_copy(tmp_path):
    """A copy of the framework, with the real scenarios replaced by one sample scenario,
    so these tests never depend on what your demos are called."""
    dst = tmp_path / "repo"
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "build", ".venv", ".local"))
    for d in (dst / "scenarios").iterdir():
        if d.is_dir():
            shutil.rmtree(d)
    assert _protorig(dst, "new", "scenario", SAMPLE, "--desc", "sample for framework tests").returncode == 0
    return dst


def _protorig(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(root / "cli" / "main.py"), *args],
                          capture_output=True, text=True, cwd=root)


def test_new_app_then_its_own_tests_pass(repo_copy):
    """The most important template test: a freshly generated app passes its own tests unedited."""
    assert _protorig(repo_copy, "new", "app", "fresh_gui", "--desc", "Fresh").returncode == 0
    assert _protorig(repo_copy, "new", "app", "fresh_twin", "--kind", "sim").returncode == 0
    d = repo_copy / "apps" / "tooling" / "fresh_gui"
    assert (d / "main.py").exists() and (d / "test_fresh_gui.py").exists()
    assert "{{" not in (d / "main.py").read_text() + (d / "test_fresh_gui.py").read_text()
    try:
        import rti.connextdds  # noqa: F401
    except ImportError:
        pytest.skip("needs Connext to run the generated tests")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        str(d), str(repo_copy / "apps" / "sim" / "fresh_twin")], capture_output=True, text=True, cwd=repo_copy)
    assert r.returncode in (0, 5) and ("6 passed" in r.stdout or "skipped" in r.stdout), r.stdout[-2000:]


def test_new_app_passes_check(repo_copy):
    _protorig(repo_copy, "new", "app", "fresh_gui")
    out = _protorig(repo_copy, "check")
    assert "fresh_gui" not in out.stdout.split("[ ok ] Apps")[0].split("Apps and language policy")[-1]
    assert "[ ok ] Apps and language policy" in out.stdout


@pytest.mark.parametrize("args,expected", [
    (["new", "app", "Bad-Name"], "not a valid app name"),
    (["new", "app", "1starts_with_digit"], "not a valid app name"),
    (["new", "app", "x", "--kind", "vehicle"], "C/C++ apps arrive"),
    (["new", "app", "x", "--scenario", "nope"], "no scenario 'nope'"),
    (["new", "scenario", "Bad_Name"], "not a valid scenario name"),
    (["new", "scenario", SAMPLE], "already exists"),
])
def test_new_refuses_bad_requests(repo_copy, args, expected):
    out = _protorig(repo_copy, *args)
    assert out.returncode != 0 and expected in out.stdout


def test_new_app_refuses_duplicates_across_kinds(repo_copy):
    _protorig(repo_copy, "new", "app", "dup", "--kind", "sim")
    out = _protorig(repo_copy, "new", "app", "dup", "--kind", "tooling")
    assert out.returncode == 1 and "already exists" in out.stdout


def test_new_scenario_is_valid(repo_copy):
    assert _protorig(repo_copy, "new", "scenario", "fresh-demo", "--desc", "A test").returncode == 0
    out = _protorig(repo_copy, "check").stdout
    fresh = [l for l in out.splitlines() if "fresh-demo" in l and "ERROR" in l]
    assert not fresh, fresh                    # a fresh scenario is valid as generated (no node_agent in run:, N2)
    assert "no demo apps in run: yet" in out   # ... and says what to add


def test_scenario_local_app(repo_copy):
    assert _protorig(repo_copy, "new", "app", "only_here", "--scenario", SAMPLE).returncode == 0
    assert (repo_copy / "scenarios" / SAMPLE / "apps" / "tooling" / "only_here" / "main.py").exists()
