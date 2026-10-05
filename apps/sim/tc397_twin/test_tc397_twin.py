"""
Tests for tc397_twin. Run: ./protorig test tc397_twin

One or more tests per requirement R1-R9 (README.md). The twin runs as a
separate process; these tests only talk to it over DDS, like any other node.

The expected values come from the firmware (TASK(DDS_Subscriber_task)) and
external/tc397/external.yaml, never from the twin's own code, so a test can't
pass just because the twin and the test share a mistake.
"""
import random
import string
import time

import pytest
import rti.connextdds as dds
import yaml

from fw import types as T
from fw.app import EXIT_KILLED, ROOT
from fw.testing import wait_for

TOPIC = "Example Temperature"
ECU = yaml.safe_load((ROOT / "external" / "tc397" / "external.yaml").read_text(encoding="utf-8"))


def start_twin(start_app, node="twin-node"):
    # The twin has no heartbeat (R7), so wait for its "running" log line instead.
    return start_app("tc397_twin", node=node, wait_heartbeat=False)


def cycle_of(s: T.Temperature) -> int:
    """Which run of the ECU task produced this sample: the stamp is cycle x 100 ms (R6)."""
    ns = s.header.stamp.sec * 1_000_000_000 + s.header.stamp.nanosec
    assert ns % 100_000_000 == 0, f"stamp {ns} ns is not a whole number of 100 ms cycles"
    return ns // 100_000_000


def firmware_temperature(cycle: int) -> float:
    """The firmware's value in a given cycle: deci_celsius starts at 100, +1 per
    successful write, wraps from 150 back to 100 (51 values), divided by 10.0."""
    return (100 + (cycle - 1) % 51) / 10.0


# --- R1 ----------------------------------------------------------------------------

def test_R1_topic_and_type(bus, start_app):
    got = bus.listen(TOPIC)
    start_twin(start_app)
    wait_for(lambda: got.count() >= 3, 5, f"samples on '{TOPIC}'")
    assert isinstance(got.last(), T.Temperature)
    assert ECU["topics"][TOPIC]["type"] == "sensor_msgs::msg::Temperature"


# --- R2 ----------------------------------------------------------------------------

def _kind(policy_kind) -> str:
    return str(policy_kind).split(".")[-1]          # "ReliabilityKind.RELIABLE" -> "RELIABLE"


def test_R2_writer_qos_matches_ecu(bus, start_app):
    """What the twin's writer announces on the wire equals what the ECU offers."""
    got = bus.listen(TOPIC)
    start_twin(start_app)
    wait_for(lambda: len(got.reader.matched_publications) > 0, 5, "the twin's writer to match")
    pub = got.reader.matched_publication_data(got.reader.matched_publications[0])
    offer = ECU["topics"][TOPIC]["writer"]
    assert _kind(pub.reliability.kind) == offer["reliability"] == "RELIABLE"
    assert _kind(pub.durability.kind) == offer["durability"] == "VOLATILE"
    assert _kind(pub.ownership.kind) == offer["ownership"] == "SHARED"
    assert offer["deadline"] == "infinite" and pub.deadline.period == dds.Duration.infinite
    assert offer["liveliness_lease"] == "infinite" and pub.liveliness.lease_duration == dds.Duration.infinite
    # History depth and the 250 ms heartbeat aren't announced on the wire; they
    # come from qos/topics.xml. Check that's what the twin's writer gets.
    wqos = bus.provider.get_topic_datawriter_qos(TOPIC)
    assert wqos.history.depth == 1 and _kind(wqos.history.kind) == "KEEP_LAST"
    assert wqos.data_writer_protocol.rtps_reliable_writer.heartbeat_period == dds.Duration.from_milliseconds(250)


# --- R3 ----------------------------------------------------------------------------

def test_R3_rate(bus, start_app):
    got = bus.listen(TOPIC)
    start_twin(start_app)
    wait_for(lambda: got.count() >= 1, 5, "the first sample")
    n0, t0 = got.count(), time.monotonic()
    time.sleep(3.0)
    rate = (got.count() - n0) / (time.monotonic() - t0)
    assert 9.0 <= rate <= 11.0, f"{rate:.1f} Hz, expected 10 Hz"


# --- R4, R5, R6 --------------------------------------------------------------------

def test_R4_R5_R6_every_sample_follows_the_firmware(bus, start_app):
    got = bus.listen(TOPIC)
    start_twin(start_app)
    wait_for(lambda: got.count() >= 20, 5, "20 samples")
    samples = got.all()
    cycles = [cycle_of(s) for s in samples]
    for s, n in zip(samples, cycles):
        assert n >= 1, "R6: the first sample is stamped 0.100 s, never 0"
        assert s.temperature == firmware_temperature(n), f"R4: cycle {n}: {s.temperature}"
        assert s.variance == 0.1, "R4"
        assert s.header.frame_id == "sensor_frame", "R5"
    steps = [b - a for a, b in zip(cycles, cycles[1:])]
    assert all(st >= 1 for st in steps), f"R6: stamps must only go forward: {cycles}"
    assert steps.count(1) >= 0.9 * len(steps), f"R6: +100 ms per sample: {cycles}"
    assert cycles[0] < 20, f"R6: the stamp counts from start-up, not wall-clock time ({cycles[0]})"


def test_R4_ramp_wraps(bus, start_app):
    got = bus.listen(TOPIC)
    start_twin(start_app)

    def wrapped():
        v = [s.temperature for s in got.all()]
        return any(a == 15.0 and b == 10.0 for a, b in zip(v, v[1:]))
    wait_for(wrapped, 8, "the ramp to go 15.0 -> 10.0")
    values = [s.temperature for s in got.all()]
    assert min(values) == 10.0 and max(values) == 15.0


def test_R4_R6_restart_resets(bus, start_app):
    """A restart is a board reset: ramp and stamp start again from the beginning."""
    first = start_twin(start_app)
    got = bus.listen(TOPIC)
    wait_for(lambda: got.count() > 0 and cycle_of(got.last()) >= 15, 5, "the twin to run 1.5 s")
    bus.command(first, T.Command.CMD_STOP_APP)
    assert first.wait_exit(5) == 0
    got.close()

    got = bus.listen(TOPIC)
    start_twin(start_app)
    wait_for(lambda: got.count() >= 1, 5, "a sample after restart")
    s = got.all()[0]
    assert cycle_of(s) < 10, f"stamp didn't reset: cycle {cycle_of(s)}"
    assert s.temperature == firmware_temperature(cycle_of(s))


# --- R7 ----------------------------------------------------------------------------

def test_R7_no_heartbeat(bus, start_app):
    beats = bus.listen("_sys/NodeStatus")
    temps = bus.listen(TOPIC)
    app = start_twin(start_app)
    wait_for(lambda: temps.count() >= 25, 5, "2.5 s of data")
    assert not any(b.app == "tc397_twin" for b in beats.all()), "the real ECU sends no heartbeat"
    assert "no heartbeat" in app.output


def test_R7_stop(bus, start_app):
    app = start_twin(start_app)
    bus.command(app, T.Command.CMD_STOP_APP)
    assert app.wait_exit(5) == 0
    assert "stopped" in app.output


def test_R7_kill(bus, start_app):
    app = start_twin(start_app)
    bus.command(app, T.Command.CMD_KILL_APP)
    assert app.wait_exit(5) == EXIT_KILLED


def test_R7_ignores_other_commands(bus, start_app):
    """The real ECU can't switch QoS variant or change parameters; the twin ignores them."""
    app = start_twin(start_app)
    got = bus.listen(TOPIC)
    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg="Variant.Temperature.LongHistory")   # a real variant
    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg="")
    bus.command(app, T.Command.CMD_SET_PARAM, arg="rate", value=20.0)
    bus.command(app, T.Command.CMD_START_APP)
    wait_for(lambda: app.output.count("ignoring ") >= 4, 3, "four 'ignoring' log lines")
    n = got.count()
    wait_for(lambda: got.count() >= n + 10, 3, "data to keep flowing")
    assert not app.exited()
    assert "switching QoS variant" not in app.output


def test_R7_ignores_stop_and_kill_sent_to_everyone(bus, start_app):
    """N4/N14: a twin obeys stop and kill only when named exactly. A "stop all"
    from the Control Panel leaves it running, just as it leaves the real ECU
    running on the rig."""
    app = start_twin(start_app)
    got = bus.listen(TOPIC)
    for node, target in (("*", "tc397_twin"), ("*", "*"), ("twin-node", "*")):
        for cmd in (T.Command.CMD_STOP_APP, T.Command.CMD_KILL_APP):
            bus._cmd_id += 1
            bus.send("_sys/DemoControl", T.DemoControl(target_node=node, target_app=target,
                                                       cmd_id=bus._cmd_id, command=cmd), to=app)
    n = got.count()
    wait_for(lambda: got.count() >= n + 10, 3, "data to keep flowing")
    assert not app.exited()


# --- R8 ----------------------------------------------------------------------------

def test_R8_publishes_only_the_ecu_topics(bus, start_app):
    """Everything the twin's participant publishes, seen through discovery."""
    # Builtin discovery readers only record what is discovered after they exist,
    # so create an observer participant and its readers BEFORE starting the twin.
    observer = dds.DomainParticipant(bus.domain)
    try:
        participants, publications = observer.participant_reader, observer.publication_reader
        got = bus.listen(TOPIC)
        start_twin(start_app, node="r8-node")     # a unique name, to find its participant
        wait_for(lambda: got.count() >= 5, 5, "data")
        time.sleep(0.5)                           # let discovery of all its endpoints settle
        keys = {str(p.key) for p in participants.read_data()
                if p.participant_name.name == "r8-node/tc397_twin"}
        assert keys, "the twin's participant was not discovered"
        topics = {p.topic_name for p in publications.read_data() if str(p.participant_key) in keys}
    finally:
        observer.close()
    assert topics == set(ECU["topics"]), f"publishes {sorted(topics)}"


# --- R9 ----------------------------------------------------------------------------

def test_R9_lease_matches_ecu(bus, start_app):
    """The lease isn't announced on the wire, so read what the twin's live
    participant reports at start-up (fw.App logs it from participant.qos)."""
    app = start_twin(start_app)
    lease = ECU["participant"]["liveliness_lease"]
    assert f"liveliness lease {lease:g} s" in app.output, app.output


# --- robustness --------------------------------------------------------------------

@pytest.mark.parametrize("args", [["--rate", "20"], ["--puncture"], ["extra"]])
def test_rejects_options_it_does_not_have(bus, start_app, args):
    """R-list says: no app options. Unknown ones fail loudly (exit 2), never ignored."""
    app = start_app("tc397_twin", *args, wait_heartbeat=False)
    assert app.wait_exit(5) == 2


def test_ignores_commands_for_others(bus, start_app):
    app = start_twin(start_app)
    got = bus.listen(TOPIC)
    bus.command(app, T.Command.CMD_KILL_APP, target_node="some-other-node")
    bus.command(app, T.Command.CMD_SET_PARAM, arg="rate", value=float("nan"))
    bus.command(app, T.Command.CMD_SET_QOS_VARIANT, arg="Variant.Nope")
    n = got.count()
    wait_for(lambda: got.count() >= n + 10, 3, "data to keep flowing")
    assert not app.exited()


def test_fuzz_control_commands(bus, start_app):
    """500 random DemoControl commands (garbage targets, ids, strings, NaN/inf
    values; aimed at the twin: any command except stop/kill, including real QoS
    variants): it must keep publishing correct data, never crash or restart."""
    rnd = random.Random(7)                       # fixed seed: a failure is reproducible
    rs = lambda n: "".join(rnd.choice(string.printable) for _ in range(rnd.randint(0, n)))
    app = start_twin(start_app)
    got = bus.listen(TOPIC)
    w = bus.writer("_sys/DemoControl")
    wait_for(lambda: w.publication_matched_status.current_count > 0, 5, "the twin to match")
    harmless = [T.Command.CMD_SET_PARAM, T.Command.CMD_SET_QOS_VARIANT, T.Command.CMD_START_APP]
    real_variants = ["", "Variant.Temperature.LongHistory", "Variant.Alert.BestEffort"]
    for _ in range(500):
        mine = rnd.random() < 0.5
        w.write(T.DemoControl(
            target_node=rnd.choice(["*", "twin-node", rs(31)]) if mine else rs(31),
            target_app="tc397_twin" if mine else rnd.choice(["", rs(31), "tc397_twi"]),
            cmd_id=rnd.getrandbits(64),
            command=rnd.choice(harmless) if mine else rnd.choice(list(T.Command)),
            arg=rnd.choice(real_variants) if rnd.random() < 0.2 else rs(63),
            value=rnd.choice([float("nan"), float("inf"), -1e308, 0.0, rnd.uniform(-1e6, 1e6)])))
    n = got.count()
    wait_for(lambda: got.count() >= n + 20, 5, "data to keep flowing")
    assert not app.exited(), app.output
    assert "Traceback" not in app.output, app.output
    assert all(s.temperature == firmware_temperature(cycle_of(s)) for s in got.all())
