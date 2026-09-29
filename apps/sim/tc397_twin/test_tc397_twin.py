"""
Tests for tc397_twin. Run: ./protorig test tc397_twin
One test per row of the behaviour table in README.md, plus the three standard
tests every app has, plus fuzzing. Pattern: LISTEN, then ACT, then CHECK.
"""
import time

import pytest

from fw import types as T
from fw.app import EXIT_KILLED
from fw.testing import wait_for

TOPIC = "Example Temperature"


# --- standard tests (from the template) -------------------------------------------

def test_starts_and_heartbeats(bus, start_app):
    app = start_app("tc397_twin")
    beats = bus.listen("_sys/NodeStatus")
    wait_for(lambda: sum(1 for b in beats.all() if b.app == "tc397_twin") >= 2, 4, "two more heartbeats")
    assert not app.exited()


def test_clean_stop(bus, start_app):
    app = start_app("tc397_twin")
    bus.command(app, T.Command.CMD_STOP_APP)
    assert app.wait_exit(5) == 0
    app2 = start_app("tc397_twin")
    app2.interrupt()
    assert app2.wait_exit(5) in (0, -15, 1)


def test_kill_command(bus, start_app):
    app = start_app("tc397_twin")
    bus.command(app, T.Command.CMD_KILL_APP)
    assert app.wait_exit(5) == EXIT_KILLED


# --- behaviour table ---------------------------------------------------------------

def test_B1_rate_and_range(bus, start_app):
    start_app("tc397_twin", "--rate", "20")
    got = bus.collect(TOPIC, seconds=2.0)
    assert 32 <= len(got) <= 48, f"{len(got)} samples in 2 s at 20 Hz"
    assert all(230 - 5 * 0.5 <= s.temperature <= 230 + 5 * 0.5 for s in got)


def test_B2_no_noise_is_exact(bus, start_app):
    start_app("tc397_twin", "--noise", "0", "--nominal", "241.5")
    got = bus.collect(TOPIC, seconds=0.6)
    assert got and all(s.temperature == 241.5 and s.variance == 0.0 for s in got)


def test_B3_leak_lowers_pressure(bus, start_app):
    app = start_app("tc397_twin", "--noise", "0", "--rate", "20")
    samples = bus.listen(TOPIC)
    bus.command(app, T.Command.CMD_SET_PARAM, arg="leak", value=20.0)
    time.sleep(2.0)
    got = [s.temperature for s in samples.all()]
    drop = got[0] - got[-1]
    assert 25 <= drop <= 45, f"expected ~20/s over ~2 s, dropped {drop:.1f}"
    assert all(a >= b for a, b in zip(got, got[1:])), "pressure must only go down while leaking"


def test_B4_setting_nominal_resets(bus, start_app):
    app = start_app("tc397_twin", "--noise", "0", "--leak", "50")
    time.sleep(1.0)
    samples = bus.listen(TOPIC)
    bus.command(app, T.Command.CMD_SET_PARAM, arg="nominal", value=250.0)
    wait_for(lambda: any(s.temperature >= 245 for s in samples.all()), 3, "pressure back near 250")


def test_B5_never_negative(bus, start_app):
    start_app("tc397_twin", "--noise", "0", "--nominal", "10", "--leak", "100")
    time.sleep(0.5)
    got = bus.collect(TOPIC, seconds=0.5)
    assert got and all(s.temperature == 0.0 for s in got)


def test_B6_stamp_and_frame_id(bus, start_app):
    start_app("tc397_twin", "--frame-id", "FL")
    got = bus.collect(TOPIC, seconds=0.5)
    assert got
    now = time.time()
    for s in got:
        stamp = s.header.stamp.sec + s.header.stamp.nanosec / 1e9
        assert abs(now - stamp) < 1.5 and s.header.frame_id == "FL"


@pytest.mark.parametrize("rate", ["0", "-5"])
def test_B7_bad_rate_refuses_to_start(bus, start_app, rate):
    app = start_app("tc397_twin", "--rate", rate, wait_heartbeat=False)
    assert app.wait_exit(10) == 2 and "--rate must be > 0" in app.output


def test_B8_rate_changes_live(bus, start_app):
    app = start_app("tc397_twin", "--rate", "5")
    slow = bus.collect(TOPIC, seconds=1.0)
    bus.command(app, T.Command.CMD_SET_PARAM, arg="rate", value=40.0)
    time.sleep(0.3)
    fast = bus.collect(TOPIC, seconds=1.0)
    assert len(slow) <= 8 and len(fast) >= 30, f"slow={len(slow)} fast={len(fast)}"


# --- fuzz ------------------------------------------------------------------------

def test_fuzz_live_parameters(bus, start_app):
    """Hostile parameter values must never crash it or produce non-finite / negative pressure."""
    app = start_app("tc397_twin", "--rate", "50")
    samples = bus.listen(TOPIC)
    for name in ("leak", "nominal", "noise", "rate"):
        for v in (0.0, -1.0, -1e9, 1e12, float("nan"), float("inf"), float("-inf"), 1e-9):
            bus.command(app, T.Command.CMD_SET_PARAM, arg=name, value=v)
    bus.command(app, T.Command.CMD_SET_PARAM, arg="rate", value=20.0)       # end in a sane state
    bus.command(app, T.Command.CMD_SET_PARAM, arg="noise", value=0.5)
    time.sleep(1.0)
    assert not app.exited(), app.output
    recent = samples.all()[-10:]
    assert recent, "still publishing after the fuzz burst"
    import math
    assert all(math.isfinite(s.temperature) and s.temperature >= 0 for s in samples.all())


def test_matches_a_reader_that_requests_what_the_ecu_offers(bus, start_app):
    """The twin must be readable by exactly the readers the real ECU can serve."""
    import rti.connextdds as dds
    q = bus.provider.get_topic_datareader_qos(TOPIC)
    q.reliability.kind = dds.ReliabilityKind.BEST_EFFORT
    start_app("tc397_twin")
    box = bus.listen(TOPIC, qos=q)
    wait_for(lambda: box.count() > 0, 3, "samples on a best-effort reader")
