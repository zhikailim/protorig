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
sys.path.insert(0, str(REPO / "libs" / "py"))
sys.path.insert(0, str(REPO / "cli"))

PROBE = '''
import sys
from fw.app import App
from fw import types as T
app = App("probe", "test probe")
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
# Failure switches for the restart tests (S6b, S6c):
import os
from pathlib import Path
if os.environ.get("PROBE_CRASH_ON") and os.environ["PROBE_CRASH_ON"] == app.qos_variant:
    sys.exit(3)                                   # this variant "fails to start"
count_file = os.environ.get("PROBE_COUNT_FILE")
if count_file:
    n = int(Path(count_file).read_text()) if Path(count_file).exists() else 0
    Path(count_file).write_text(str(n + 1))
    if n >= 1:
        sys.exit(4)                               # every start after the first fails
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
    _start(start_app, probe_dir)
    bus.send("Example Temperature", {"temperature": 31.5})
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
    bus.send("Example Temperature", {"temperature": -1.0})       # raises inside on_data
    bus.send("Example Temperature", {"temperature": 29.0})       # must still be handled
    wait_for(lambda: out.count() > 0, 3, "an alert after the error")
    assert not app.exited()
    assert "below zero, on purpose" in app.output


def test_commands_for_others_are_ignored(bus, start_app, probe_dir):
    import time
    from fw import types as T
    app = _start(start_app, probe_dir, node="hpc-vm")
    bus.command(app, T.Command.CMD_KILL_APP, target_node="hpc-pi")       # other node
    bus._cmd_id += 1
    bus.send("_sys/DemoControl", T.DemoControl(target_node="hpc-vm", target_app="other_app",
                                               cmd_id=bus._cmd_id, command=T.Command.CMD_KILL_APP))
    bus._cmd_id += 1
    bus.send("_sys/DemoControl", T.DemoControl(target_node="hpc-vm", target_app="",   # node-level: node_agent's job
                                               cmd_id=bus._cmd_id, command=T.Command.CMD_KILL_APP))
    time.sleep(1.0)
    assert not app.exited()


def test_wildcard_node_reaches_the_app(bus, start_app, probe_dir):
    from fw import types as T
    from fw.app import EXIT_KILLED
    app = _start(start_app, probe_dir)
    bus.command(app, T.Command.CMD_KILL_APP, target_node="*")
    assert app.wait_exit(5) == EXIT_KILLED


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


LONG = "Variant.Temperature.LongHistory"


def _beat_with(beats, variant, after=0):
    """A probe heartbeat carrying `variant`, among heartbeats received after index `after`."""
    return any(b.app == "probe" and b.qos_variant == variant for b in beats.all()[after:])


def test_qos_variant_restart(bus, start_app, probe_dir):
    """S1, S4, S5: a switch restarts the app through its launcher; the heartbeat
    then reports the new variant; same variant again is ignored; "" = back to default."""
    from fw import types as T
    from fw.testing import wait_for
    beats = bus.listen("_sys/NodeStatus")
    app = _start(start_app, probe_dir)
    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg=LONG)
    wait_for(lambda: _beat_with(beats, LONG), 10, "a heartbeat carrying the new variant")
    assert app.restarts == 1 and not app.exited()
    assert f"[launcher] switching QoS variant to '{LONG}'" in app.output

    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg=LONG)                 # same again: no restart
    wait_for(lambda: f"already using QoS variant '{LONG}'" in app.output, 3, "'already using'")
    assert app.restarts == 1

    mark = len(beats.all())
    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg="")                   # back to default
    wait_for(lambda: _beat_with(beats, "", mark), 10, "a heartbeat on the default QoS")
    assert app.restarts == 2

    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg="Variant.Does.Not.Exist")
    wait_for(lambda: "unknown QoS variant" in app.output, 3, "a warning for an unknown variant")
    assert app.restarts == 2


def test_variant_switch_refused_without_launcher(bus, start_app, probe_dir):
    """S3: started by hand (nobody to restart it): refuse the switch and stay up."""
    from fw import types as T
    from fw.testing import wait_for
    beats = bus.listen("_sys/NodeStatus")
    app = _start(start_app, probe_dir, supervised=False)
    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg=LONG)
    wait_for(lambda: "not started by a protorig launcher" in app.output, 3, "the refusal")
    mark = len(beats.all())
    wait_for(lambda: _beat_with(beats, "", mark), 3, "heartbeats still on the default QoS")
    assert not app.exited() and not _beat_with(beats, LONG)


def test_variant_switch_with_unwritable_note_stays_up(bus, start_app, probe_dir, tmp_path):
    """S6a: the note can't be written: the app stays up on its current variant."""
    from fw import types as T
    from fw.supervise import RESTART_ENV
    from fw.testing import wait_for
    bad = str(tmp_path / "no" / "such" / "dir" / "restart.note")
    app = _start(start_app, probe_dir, env={RESTART_ENV: bad})
    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg=LONG)
    wait_for(lambda: "can't write the restart note" in app.output, 3, "the failure to be logged")
    time.sleep(0.5)
    assert not app.exited() and app.restarts == 0


def test_failed_switch_rolls_back(bus, start_app, probe_dir):
    """S6b: the app fails to start on the new variant: the launcher restarts it on the old one."""
    from fw import types as T
    from fw.testing import wait_for
    beats = bus.listen("_sys/NodeStatus")
    app = _start(start_app, probe_dir, env={"PROBE_CRASH_ON": LONG})
    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg=LONG)
    wait_for(lambda: "[launcher] the new QoS variant failed to start (exit 3): rolling back" in app.output,
             10, "the rollback")
    mark = len(beats.all())
    wait_for(lambda: _beat_with(beats, "", mark), 10, "heartbeats on the old (default) QoS again")
    assert app.restarts == 2 and not app.exited()


def test_failed_rollback_gives_up(bus, start_app, probe_dir, tmp_path):
    """S6c: the rollback fails too: the launcher gives up instead of looping."""
    from fw import types as T
    app = _start(start_app, probe_dir, env={"PROBE_COUNT_FILE": str(tmp_path / "starts")})
    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg=LONG)
    assert app.wait_exit(15) == 4
    assert "[launcher] the rollback failed too (exit 4): giving up" in app.output
    assert app.restarts == 2                         # the switch, then one rollback: no loop


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


def test_domain_comes_from_scenario():
    from fw.app import scenario_domain
    assert scenario_domain("temperature-skeleton") == 0
    assert scenario_domain("no-such-scenario") is None


# --- fuzz: garbage Control Panel commands must never take the app down -------------

def test_fuzz_control_commands(bus, start_app, probe_dir):
    import time
    from fw import types as T
    from fw.testing import wait_for
    app = _start(start_app, probe_dir)
    rnd = random.Random(7)
    safe = [T.Command.CMD_SET_PARAM, T.Command.CMD_START_APP, T.Command.CMD_SET_QOS_VARIANT]
    junk_text = ["", "rate", "x" * 64, "Variant.", "../../etc", "ñandú", "rate;rm -rf", " "]
    for i in range(200):
        cmd = rnd.choice(safe)
        arg = rnd.choice(junk_text)
        if cmd == T.Command.CMD_SET_QOS_VARIANT and arg:
            arg = "Variant.Bogus." + str(i)          # unknown variants: must warn, not restart
        value = rnd.choice([0.0, -1.0, 1e308, float("nan"), float("inf"), 3.3])
        bus._cmd_id += 1
        bus.send("_sys/DemoControl", T.DemoControl(target_node=rnd.choice(["*", app.node]), target_app="probe",
                                                   cmd_id=bus._cmd_id, command=cmd, arg=arg[:64], value=value))
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
    dst = tmp_path / "repo"
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "build", ".venv", ".local"))
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
    (["new", "scenario", "temperature-skeleton"], "already exists"),
])
def test_new_refuses_bad_requests(repo_copy, args, expected):
    out = _protorig(repo_copy, *args)
    assert out.returncode != 0 and expected in out.stdout


def test_new_app_refuses_duplicates_across_kinds(repo_copy):
    _protorig(repo_copy, "new", "app", "dup", "--kind", "sim")
    out = _protorig(repo_copy, "new", "app", "dup", "--kind", "tooling")
    assert out.returncode == 1 and "already exists" in out.stdout


def test_new_scenario_is_valid_apart_from_unbuilt_apps(repo_copy):
    assert _protorig(repo_copy, "new", "scenario", "fresh-demo", "--desc", "A test").returncode == 0
    out = _protorig(repo_copy, "check").stdout
    fresh = [l for l in out.splitlines() if "fresh-demo" in l]
    assert fresh and all("not found" in l for l in fresh), fresh      # only node_agent, which isn't built yet


def test_scenario_local_app(repo_copy):
    assert _protorig(repo_copy, "new", "app", "only_here", "--scenario", "temperature-skeleton").returncode == 0
    assert (repo_copy / "scenarios" / "temperature-skeleton" / "apps" / "tooling" / "only_here" / "main.py").exists()
