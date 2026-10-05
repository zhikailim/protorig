"""
test_control.py — node_agent's decisions (control.py), without DDS or processes.
Each test names the behaviour row (README.md B1-B27) it proves. The DDS-level
tests are in test_node_agent.py.
"""
import random
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "libs" / "py"))

import control as C  # noqa: E402

V = "Variant.Temperature.LongHistory"
SET = C.Settings(stop_grace=10, start_timeout=15, fail_window=5, hang_after=3)


class Rig:
    """A Controller plus a fake clock and the set of processes it thinks exist."""

    def __init__(self, entries=None, known=(V,)):
        entries = entries or [("mon", ["--strength", "20"], True, ""), ("gui", [], True, ""),
                              ("cpp", [], False, "C/C++ app, not built yet")]
        self.now = 1000.0
        self.c = C.Controller("pi", entries, set(known), SET, self.now)
        self.log: list = []
        self.alive: dict[str, list[str]] = {}       # app -> args of its running process
        self.do(self.c.startup())

    def do(self, actions):
        self.log += actions
        for a in actions:
            if isinstance(a, C.Spawn):
                assert a.app not in self.alive, f"{a.app} started twice"
                self.alive[a.app] = a.args
        return actions

    def cmd(self, command, app, sender="windows/control_panel", node="pi", arg="", cid=None):
        cid = cid if cid is not None else random.getrandbits(63)
        return self.do(self.c.command(node, app, cid, command, arg, sender, self.now))

    def beat(self, app, variant=""):
        return self.do(self.c.heartbeat(app, variant, self.now))

    def exit(self, app, code=0, line="bye"):
        self.alive.pop(app, None)
        return self.do(self.c.exited(app, code, line, self.now))

    def later(self, s):
        self.now += s
        return self.do(self.c.tick(self.now))

    def state(self, app):
        return self.c.apps[app].state

    def kinds(self, actions, kind):
        return [a for a in actions if isinstance(a, kind)]


def running(r, app="mon", variant=""):
    r.cmd(C.START, app)
    r.beat(app, variant)
    assert r.state(app) == C.RUNNING


# --- start-up and commands ------------------------------------------------------------

def test_B1_startup_rows_and_stale_alerts_cleared():
    r = Rig()
    assert r.state("mon") == C.NOT_RUNNING and r.state("cpp") == C.UNAVAILABLE
    assert "not built" in r.c.apps["cpp"].detail
    cleared = {a.alert_id for a in r.kinds(r.log, C.ClearAlert)}
    assert {"crash:mon", "hang:mon", "variant:mon", "start:mon"} <= cleared
    assert not r.kinds(r.log, C.Spawn)                                   # starts nothing by itself


def test_B1_never_supervises_itself_or_duplicates():
    r = Rig([("node_agent", [], True, ""), ("mon", [], True, ""), ("mon", ["x"], True, "")])
    assert list(r.c.apps) == ["mon"] and r.c.apps["mon"].args == []


def test_B2_start_then_running_on_heartbeat_alerts_cleared():
    r = Rig()
    acts = r.cmd(C.START, "mon")
    assert r.alive["mon"] == ["--strength", "20"] and r.state("mon") == C.STARTING
    assert {"crash:mon", "hang:mon"} <= {a.alert_id for a in r.kinds(acts, C.ClearAlert)}
    r.beat("mon")
    assert r.state("mon") == C.RUNNING


def test_B2_running_line_is_enough_for_an_app_without_heartbeat():
    r = Rig()
    r.cmd(C.START, "gui")
    r.do(r.c.running_line("gui", r.now))
    assert r.state("gui") == C.RUNNING


def test_B2_start_star_starts_every_available_app():
    r = Rig()
    r.cmd(C.START, "*")
    assert set(r.alive) == {"mon", "gui"} and r.state("cpp") == C.UNAVAILABLE


def test_B3_already_running_is_ignored():
    r = Rig()
    running(r)
    acts = r.cmd(C.START, "mon")
    assert not r.kinds(acts, C.Spawn) and any("already running" in l.text for l in r.kinds(acts, C.Log))


def test_B4_unavailable_refused_and_rechecked():
    r = Rig()
    acts = r.cmd(C.START, "cpp")
    assert not r.kinds(acts, C.Spawn) and any("not built" in l.text for l in r.kinds(acts, C.Log))
    r.do(r.c.set_available("cpp", True, "", r.now))                     # built since
    assert r.state("cpp") == C.NOT_RUNNING
    r.cmd(C.START, "cpp")
    assert "cpp" in r.alive


def test_B5_stop_then_stopped_by_sender():
    r = Rig()
    running(r)
    acts = r.cmd(C.STOP, "mon")
    assert r.kinds(acts, C.Interrupt) and r.state("mon") == C.STOPPING
    r.exit("mon", 0)
    assert r.state("mon") == C.STOPPED and "windows/control_panel" in r.c.apps["mon"].detail


def test_B5_forced_after_grace_is_killed():
    r = Rig()
    running(r)
    r.cmd(C.STOP, "mon")
    assert not r.kinds(r.later(9.9), C.Kill)
    assert r.kinds(r.later(0.2), C.Kill)
    r.exit("mon", -9)
    assert r.state("mon") == C.KILLED and r.c.apps["mon"].detail == "forced after 10 s"


def test_B6_kill_at_once_even_mid_stop_with_simulated_crash_alert():
    r = Rig()
    running(r)
    r.cmd(C.STOP, "mon")
    acts = r.cmd(C.KILL, "mon")
    assert r.kinds(acts, C.Kill)                                        # not after the grace
    acts = r.exit("mon", -9)
    assert r.state("mon") == C.KILLED and "killed by windows/control_panel" in r.c.apps["mon"].detail
    alert = r.kinds(acts, C.RaiseAlert)[0]
    assert alert.alert_id == "crash:mon" and alert.severity == C.CRITICAL and "simulated" in alert.message


def test_B7_repeated_cmd_id_ignored_last_1000_only():
    r = Rig()
    r.cmd(C.START, "mon", cid=1)
    r.beat("mon")
    assert not r.cmd(C.STOP, "mon", cid=1)                               # same id: nothing at all
    for i in range(2, 1003):
        r.cmd(C.START, "gui", cid=i)                                     # pushes id 1 out of memory
    assert r.cmd(C.STOP, "mon", cid=1)


@pytest.mark.parametrize("node,command", [("pc", C.STOP), ("other", C.KILL), ("pi", C.PARAM), ("*", C.PARAM)])
def test_B8_other_nodes_and_params_ignored_silently(node, command):
    r = Rig()
    running(r)
    assert r.cmd(command, "mon", node=node) == [] and r.state("mon") == C.RUNNING


@pytest.mark.parametrize("app,why", [("node_agent", "itself"), ("nope", "run: list"),
                                     ("../x", "plain app name"), ("", "plain app name"), ("Mon", "plain app name")])
def test_B9_refusals_logged_with_sender_and_reason(app, why):
    r = Rig()
    acts = r.cmd(C.KILL, app)
    logs = [l.text for l in r.kinds(acts, C.Log)]
    assert len(logs) == 1 and "refused" in logs[0] and why in logs[0] and "windows/control_panel" in logs[0]


# --- the sender (N11) -----------------------------------------------------------------

def test_B10_sender_skipped_rest_done():
    r = Rig()
    running(r, "mon")
    running(r, "gui")
    acts = r.cmd(C.STOP, "*", sender="pi/gui")
    assert [a.app for a in r.kinds(acts, C.Interrupt)] == ["mon"]
    assert any("not stopping gui: it sent the command" in l.text for l in r.kinds(acts, C.Log))
    assert not r.kinds(r.cmd(C.KILL, "gui", sender="pi/gui"), C.Kill)


def test_B10_same_app_name_on_another_node_is_not_protected():
    r = Rig()
    running(r, "gui")
    assert r.kinds(r.cmd(C.KILL, "gui", sender="windows/gui"), C.Kill)


def test_B11_variant_switch_of_the_sender_refused():
    r = Rig()
    running(r)
    assert not r.kinds(r.cmd(C.VARIANT, "mon", sender="pi/mon", arg=V), C.Interrupt)


def test_B12_unknown_sender_acted_on():
    r = Rig()
    running(r)
    acts = r.cmd(C.KILL, "mon", sender=None)
    assert r.kinds(acts, C.Kill) and any("unknown" in l.text for l in r.kinds(acts, C.Log))


# --- supervision and alerts ----------------------------------------------------------

def test_B13_crash_reported_not_restarted():
    r = Rig()
    running(r)
    running(r, "gui")
    acts = r.exit("mon", 3, "ZeroDivisionError: division by zero")
    assert r.state("mon") == C.CRASHED and r.c.apps["mon"].exit_code == 3
    assert "ZeroDivisionError" in r.c.apps["mon"].detail
    alert = r.kinds(acts, C.RaiseAlert)[0]
    assert alert.severity == C.CRITICAL and "ZeroDivisionError" in alert.message
    assert not r.kinds(acts, C.Spawn) and r.state("gui") == C.RUNNING


def test_B14_exit_0_on_its_own():
    r = Rig()
    running(r)
    acts = r.exit("mon", 0)
    assert r.state("mon") == C.STOPPED and r.c.apps["mon"].detail == "ended on its own (exit 0)"
    assert r.kinds(acts, C.RaiseAlert)[0].severity == C.WARNING


def test_B15_hang_then_recovery():
    r = Rig()
    running(r)
    assert not r.kinds(r.later(2.9), C.RaiseAlert)
    acts = r.later(0.2)
    assert r.kinds(acts, C.RaiseAlert)[0].alert_id == "hang:mon"
    assert r.c.apps["mon"].detail == "not responding" and r.state("mon") == C.RUNNING
    assert not r.kinds(r.later(5), C.RaiseAlert)                         # raised once, not every tick
    acts = r.beat("mon")
    assert [a.alert_id for a in r.kinds(acts, C.ClearAlert)] == ["hang:mon"] and r.c.apps["mon"].detail == ""


def test_B15_app_without_heartbeat_is_never_judged_hung():
    r = Rig()
    r.cmd(C.START, "gui")
    r.do(r.c.running_line("gui", r.now))
    assert not r.kinds(r.later(60), C.RaiseAlert)


def test_B16_slow_start_warned_then_cleared():
    r = Rig()
    r.cmd(C.START, "mon")
    assert not r.kinds(r.later(14), C.RaiseAlert)
    assert r.kinds(r.later(2), C.RaiseAlert)[0].alert_id == "start:mon"
    assert r.state("mon") == C.STARTING
    acts = r.beat("mon")
    assert [a.alert_id for a in r.kinds(acts, C.ClearAlert)] == ["start:mon"] and r.state("mon") == C.RUNNING


# --- variant switching (N13) ---------------------------------------------------------

def test_B17_switch_success():
    r = Rig()
    running(r)
    acts = r.cmd(C.VARIANT, "mon", arg=V)
    assert r.kinds(acts, C.Interrupt) and r.state("mon") == C.RESTARTING
    r.exit("mon", 0)                                                     # the old run stopped
    assert r.alive["mon"] == ["--strength", "20", "--qos-variant", V]
    r.beat("mon", V)
    a = r.c.apps["mon"]
    assert a.state == C.RUNNING and a.variant == V and a.restarts == 1


def test_B17_old_run_that_ignores_ctrl_c_is_forced_and_the_switch_goes_on():
    r = Rig()
    running(r)
    r.cmd(C.VARIANT, "mon", arg=V)
    assert r.kinds(r.later(10.1), C.Kill)
    r.exit("mon", -9)
    assert r.alive["mon"][-1] == V and r.state("mon") == C.RESTARTING


@pytest.mark.parametrize("variant,alert", [("", False), ("Variant.Nope", True)])
def test_B18_same_or_unknown_variant(variant, alert):
    r = Rig()
    running(r)
    acts = r.cmd(C.VARIANT, "mon", arg=variant)
    assert not r.kinds(acts, C.Interrupt) and r.state("mon") == C.RUNNING
    assert bool(r.kinds(acts, C.RaiseAlert)) == alert


def test_B18_star_and_not_running_refused():
    r = Rig()
    assert not r.kinds(r.cmd(C.VARIANT, "*", arg=V), C.Interrupt)
    r.cmd(C.START, "mon")                                                # STARTING, not RUNNING
    assert not r.kinds(r.cmd(C.VARIANT, "mon", arg=V), C.Interrupt)


@pytest.mark.parametrize("how", ["exits", "silent", "wrong variant"])
def test_B19_failed_switch_rolls_back_once(how):
    r = Rig()
    running(r)
    r.cmd(C.VARIANT, "mon", arg=V)
    r.exit("mon", 0)
    if how == "exits":
        acts = r.exit("mon", 3)
    else:
        acts = r.later(16) if how == "silent" else r.beat("mon", "Variant.Other")
        assert r.kinds(acts, C.Kill)
        acts = r.exit("mon", -9)
    alert = r.kinds(acts, C.RaiseAlert)[0]
    assert alert.alert_id == "variant:mon" and "rolled back" in alert.message
    assert "--qos-variant" not in r.alive["mon"]                          # back on the default
    r.beat("mon", "")
    a = r.c.apps["mon"]
    assert a.state == C.RUNNING and a.variant == "" and a.restarts == 2 and "rolled back" in a.detail


def test_B19_failed_rollback_gives_up():
    r = Rig()
    running(r)
    r.cmd(C.VARIANT, "mon", arg=V)
    r.exit("mon", 0)
    r.exit("mon", 3)                                                      # new variant fails
    acts = r.exit("mon", 4)                                               # rollback fails too
    assert r.state("mon") == C.CRASHED and not r.kinds(acts, C.Spawn)
    assert r.kinds(acts, C.RaiseAlert)[0].severity == C.CRITICAL


def test_B19_app_without_heartbeat_succeeds_by_surviving_the_fail_window():
    r = Rig()
    r.cmd(C.START, "gui")
    r.do(r.c.running_line("gui", r.now))
    r.cmd(C.VARIANT, "gui", arg=V)
    r.exit("gui", 0)
    assert r.state("gui") == C.RESTARTING
    r.later(5.1)
    assert r.state("gui") == C.RUNNING and r.c.apps["gui"].variant == V


def test_B20_start_after_switch_uses_run_entry():
    r = Rig()
    running(r)
    r.cmd(C.VARIANT, "mon", arg=V)
    r.exit("mon", 0)
    r.beat("mon", V)
    r.cmd(C.STOP, "mon")
    r.exit("mon", 0)
    r.cmd(C.START, "mon")
    assert r.alive["mon"] == ["--strength", "20"] and r.c.apps["mon"].variant == ""


def test_stop_during_switch_cancels_it():
    r = Rig()
    running(r)
    r.cmd(C.VARIANT, "mon", arg=V)
    r.cmd(C.STOP, "mon")
    r.exit("mon", 0)
    assert r.state("mon") == C.STOPPED and "mon" not in r.alive


# --- rows -----------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["x" * 500, "é" * 200, "漢" * 100, "🙂" * 60, "a\nb\tc", "\x00" * 10, ""])
def test_B21_fit_trims_to_bytes_at_a_character_boundary(text):
    out = C.fit(text)
    assert len(out.encode("utf-8")) <= C.DETAIL_BYTES and "\n" not in out and "\x00" not in out
    out.encode("utf-8").decode("utf-8")                                   # valid
    if len(" ".join(text.replace("\x00", "").split()).encode()) > C.DETAIL_BYTES:
        assert out.endswith("…")


def test_B21_crash_line_trimmed_in_row_and_alert():
    r = Rig()
    running(r)
    acts = r.exit("mon", 1, "Error: " + "ü" * 300)
    assert len(r.c.apps["mon"].detail.encode()) <= 128
    assert all(len(a.message.encode()) <= 128 for a in r.kinds(acts, C.RaiseAlert))


# --- shutdown --------------------------------------------------------------------------

def test_B26_shutdown_stops_every_running_app():
    r = Rig()
    running(r, "mon")
    r.cmd(C.START, "gui")
    assert {a.app for a in r.c.shutdown()} == {"mon", "gui"}
    assert {"crash:mon", "crash:gui", "crash:cpp"} <= {a.alert_id for a in r.c.clear_all_alerts()}


# --- fuzz -----------------------------------------------------------------------------

def test_fuzz_never_breaks_its_own_rules():
    """20,000 random events (commands, heartbeats, exits, time) on random
    targets. After every step: no app is started twice, every state is a known
    one, a process exists exactly when the state says so (once exits arrive),
    and every row's detail fits its bound."""
    rnd = random.Random(1)
    r = Rig()
    names = ["mon", "gui", "cpp", "*", "node_agent", "nope", ""]
    states = {C.NOT_RUNNING, C.UNAVAILABLE, C.STARTING, C.RUNNING, C.RESTARTING,
              C.STOPPING, C.STOPPED, C.KILLED, C.CRASHED}
    for step in range(20000):
        e = rnd.random()
        if e < 0.45:
            r.cmd(rnd.choice([C.START, C.STOP, C.KILL, C.VARIANT, C.PARAM, "CMD_BOGUS"]), rnd.choice(names),
                  sender=rnd.choice([None, "windows/control_panel", "pi/mon", "pi/gui", "x"]),
                  node=rnd.choice(["pi", "*", "pc"]), arg=rnd.choice(["", V, "Variant.Nope"]),
                  cid=rnd.randrange(3000))
        elif e < 0.65 and r.alive:
            app = rnd.choice(list(r.alive))
            r.beat(app, rnd.choice(["", V, "Variant.Other"]))
        elif e < 0.80 and r.alive:
            app = rnd.choice(list(r.alive))
            r.exit(app, rnd.choice([0, 1, 3, -9, 137]), rnd.choice(["", "x" * 300, "é" * 100]))
        elif e < 0.85:
            r.do(r.c.running_line(rnd.choice(["mon", "gui", "cpp"]), r.now))
        else:
            r.later(rnd.choice([0.1, 1, 3.5, 6, 11, 16]))
        for name, a in r.c.apps.items():
            assert a.state in states, (step, name, a.state)
            assert len(a.detail.encode()) <= C.DETAIL_BYTES
            if name in r.alive:
                assert a.state in C.ALIVE, (step, name, a.state)
            elif a.state in C.ALIVE:
                pytest.fail(f"step {step}: {name} is {a.state} but has no process")
