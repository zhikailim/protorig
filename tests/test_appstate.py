"""
test_appstate.py — the _sys/AppState topic (node_agent N12): type, QoS and the
DDS behaviour the agent and displays rely on. The agent itself is tested later.

Uses real Connext (skipped without it or a license).
"""
import random
import string
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "libs" / "py"))

dds = pytest.importorskip("rti.connextdds")
from fw import types as T          # noqa: E402
from fw.testing import wait_for    # noqa: E402

TOPIC = "_sys/AppState"
S = T.AppStateKind


def _row(app, state=S.APP_RUNNING, node="hpc-pi", **kw):
    return T.AppState(node=node, app=app, state=state, scenario="sample-scenario",
                      changed_at_ns=time.time_ns(), **kw)


def _writer(bus):
    """A writer with the topic's QoS from qos/topics.xml, on its own participant
    (so closing it is like an agent leaving)."""
    pqos = bus.provider.participant_qos
    pqos.participant_name.name = "hpc-pi/node_agent"
    p = dds.DomainParticipant(bus.domain, pqos)
    w = dds.DataWriter(dds.Publisher(p), dds.Topic(p, TOPIC, T.AppState),
                       bus.provider.get_topic_datawriter_qos(TOPIC))
    return p, w


def _reader(bus):
    return dds.DataReader(bus._sub, bus._topic(TOPIC), bus.provider.get_topic_datareader_qos(TOPIC))


def _states(reader, timeout=3.0, want=None):
    """{(node, app): (instance_state, last valid row)} from everything read so far."""
    seen = {}
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for data, info in reader.read():
            key = (data.node, data.app) if info.valid else None
            if key is None:
                key = next((k for k, (h, _, _) in seen.items() if h == info.instance_handle), None)
            if key is None:
                continue
            prev = seen.get(key, (info.instance_handle, None, None))[2]
            seen[key] = (info.instance_handle, str(info.state.instance_state), data if info.valid else prev)
        view = {k: (st, row) for k, (_, st, row) in seen.items()}
        if want is None or want(view):
            break
        time.sleep(0.05)
    return {k: (st, row) for k, (_, st, row) in seen.items()}


def test_late_joiner_sees_every_current_row(bus):
    """A display started after the agent gets the latest row of every app at
    once (transient local, keep last 1 per row), not the history."""
    p, w = _writer(bus)
    r = None
    try:
        w.write(_row("hpc_monitor", S.APP_STARTING))
        w.write(_row("hpc_monitor", S.APP_RUNNING))           # replaces STARTING
        w.write(_row("logger", S.APP_CRASHED, exit_code=3, detail="Traceback ..."))
        w.write(_row("node_agent", S.APP_RUNNING))
        time.sleep(0.3)
        r = _reader(bus)                                        # joins late
        got = _states(r, 5, lambda s: len(s) == 3)
        assert {k: row.state for k, (_, row) in got.items()} == {
            ("hpc-pi", "hpc_monitor"): S.APP_RUNNING,
            ("hpc-pi", "logger"): S.APP_CRASHED,
            ("hpc-pi", "node_agent"): S.APP_RUNNING}
        assert got[("hpc-pi", "logger")][1].exit_code == 3
        assert len(r.select().state(dds.DataState.any).read()) == 3     # keep last 1: no history replayed
    finally:
        if r is not None:
            r.close()
        p.close()


def test_disposed_rows_show_as_disposed(bus):
    """A clean agent stop disposes its rows: readers see "gone on purpose"."""
    r = _reader(bus)
    p, w = _writer(bus)
    try:
        wait_for(lambda: w.publication_matched_status.current_count > 0, 5, "a match")
        w.write(_row("hpc_monitor"))
        _states(r, 3, lambda s: ("hpc-pi", "hpc_monitor") in s)
        w.dispose_instance(w.lookup_instance(_row("hpc_monitor")))
        got = _states(r, 5, lambda s: "DISPOSED" in s.get(("hpc-pi", "hpc_monitor"), ("", None))[0])
        assert "DISPOSED" in got[("hpc-pi", "hpc_monitor")][0]
    finally:
        r.close()
        p.close()


AGENT = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, {libs!r})
    import rti.connextdds as dds
    from fw import types as T
    prov = dds.QosProvider({qos!r})
    prov.default_profile = "protorig::Topics"
    p = dds.DomainParticipant({domain}, prov.participant_qos)   # base.xml: 10 s participant lease, like fw.App
    w = dds.DataWriter(dds.Publisher(p), dds.Topic(p, "_sys/AppState", T.AppState),
                       prov.get_topic_datawriter_qos("_sys/AppState"))
    w.write(T.AppState(node="hpc-pi", app="hpc_monitor", state=T.AppStateKind.APP_RUNNING))
    print("WRITTEN", flush=True)
    time.sleep(120)
''')


def test_rows_of_a_dead_agent_show_no_writers(bus):
    """An agent that dies (power cut) leaves its rows with no writer, which a
    display must show as "unknown: agent lost", never as their last state.
    (Displays also use the agent's 3 s heartbeat, N12 rule 1; this is the
    slower DDS-level signal, bounded by the 10 s participant lease.)"""
    import os
    from fw.app import QOS_FILES
    qos = ";".join(str(REPO / "qos" / f) for f in QOS_FILES)
    code = AGENT.format(libs=str(REPO / "libs" / "py"), domain=bus.domain, qos=qos)
    r = _reader(bus)
    agent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=dict(os.environ))
    try:
        lines = iter(agent.stdout.readline, "")
        assert any(l.strip() == "WRITTEN" for l in lines)       # after Connext's license banner
        _states(r, 5, lambda s: ("hpc-pi", "hpc_monitor") in s)
        agent.kill()                                          # no goodbye
        agent.wait()
        got = _states(r, 15, lambda s: "NO_WRITERS" in s.get(("hpc-pi", "hpc_monitor"), ("", None))[0])
        st, row = got[("hpc-pi", "hpc_monitor")]
        assert "NO_WRITERS" in st, st
        assert row.state == S.APP_RUNNING                     # the stale row is still there: never trust it alone
    finally:
        r.close()
        if agent.poll() is None:
            agent.kill()


def test_detail_bound_is_bytes_and_over_long_is_refused(bus):
    """Bounds count UTF-8 bytes, and an over-long string makes the WRITE FAIL
    (nothing published). So the agent must trim `detail` to 128 bytes at a
    character boundary (N12 rule 5) before writing."""
    p, w = _writer(bus)
    try:
        w.write(_row("a", detail="x" * 128))
        w.write(_row("a", detail="é" * 64))                   # 64 characters, 128 bytes
        for too_long in ("x" * 129, "é" * 64 + "x"):
            with pytest.raises(Exception):
                w.write(_row("a", detail=too_long))
    finally:
        p.close()


def test_fuzz_rows_within_bounds_round_trip(bus):
    """500 random rows with every state, extreme numbers and random text up to
    each field's byte bound (any Unicode): all arrive intact."""
    rnd = random.Random(11)
    alphabet = string.printable + "éß漢字🙂"

    def text(max_bytes):
        s = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, max_bytes)))
        while len(s.encode("utf-8")) > max_bytes:
            s = s[:-1]
        return s.replace("\x00", "")

    r = _reader(bus)
    p, w = _writer(bus)
    try:
        wait_for(lambda: w.publication_matched_status.current_count > 0, 5, "a match")
        sent = {}
        for i in range(500):
            row = T.AppState(node=text(32) or "n", app=f"app{i % 40}", state=rnd.choice(list(S)),
                             exit_code=rnd.choice([0, 1, 137, -9, 2**31 - 1, -2**31]),
                             restarts=rnd.choice([0, 1, 2**32 - 1]), detail=text(128), scenario=text(64),
                             changed_at_ns=rnd.choice([0, -1, 2**63 - 1, time.time_ns()]))
            w.write(row)
            sent[(row.node, row.app)] = row
        got = _states(r, 10, lambda s: len(s) >= len(sent))
        assert {k: row for k, (_, row) in got.items()} == sent
    finally:
        r.close()
        p.close()
