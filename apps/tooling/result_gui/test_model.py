"""test_model.py — what the screen says, worked out from heartbeats and rows
(model.py, no DDS, time injected). Rows G9-G16 in README.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import BAD, IDLE, OK, WARN, Model   # noqa: E402

NODES = {
    "windows": {"layer": "compute", "run": ["result_gui", "control_panel", "ivi_standin"]},
    "hpc-pi": {"layer": "compute", "run": ["hpc_monitor"]},
    "tc397": {"layer": "edge", "external": True},
}


def model():
    return Model(NODES, silence=2.0)


def beat(m, node, app, seq, now, state="alive", writer="w1"):
    m.heartbeat(node, app, seq, state, now, writer)


def machine(view, name):
    return next(x for x in view.machines if x.name == name)


def up(m, now, seq=1):
    for node, app in (("windows", "result_gui"), ("hpc-pi", "node_agent"), ("hpc-pi", "hpc_monitor")):
        beat(m, node, app, seq, now)
    m.data("tc397", now)
    return m.tick(now)


def test_G9_presenter_tools_hidden_and_heard_apps_added():
    m = model()
    beat(m, "hpc-pi", "extra_probe", 1, 0.0)
    m.tick(0.0)
    v = m.snapshot(0.0)
    assert [a.name for a in machine(v, "windows").apps] == ["ivi_standin"]
    assert [a.name for a in machine(v, "hpc-pi").apps] == ["hpc_monitor", "extra_probe"]
    assert [x.name for x in v.machines] == ["windows", "hpc-pi", "tc397"]      # scenario order


def test_G10_G11_up_and_not_seen_yet():
    m = model()
    m.tick(0.0)
    v = m.snapshot(0.0)
    assert {x.name: x.word for x in v.machines} == {"windows": "not seen yet", "hpc-pi": "not seen yet",
                                                    "tc397": "not seen yet"}
    assert all(x.tone == IDLE for x in v.machines)
    log = up(m, 1.0)
    v = m.snapshot(1.0)
    assert {x.name: (x.word, x.tone) for x in v.machines} == {
        "windows": ("up", OK), "hpc-pi": ("up", OK), "tc397": ("data arriving", OK)}
    assert "machine hpc-pi: up" in log and "external tc397: data arriving" in log
    assert v.caption == "", "coming up for the first time is not an event"


def test_G12_lost_with_the_time_since_the_last_heartbeat():
    m = model()
    up(m, 0.0, seq=1)
    beat(m, "hpc-pi", "node_agent", 2, 1.0)
    beat(m, "hpc-pi", "hpc_monitor", 2, 1.0)
    m.tick(1.0)
    for app in ("node_agent", "hpc_monitor"):        # the lease runs out 3 s after the last beat
        beat(m, "hpc-pi", app, 2, 4.2, state="lost")
    m.data("tc397", 4.2)
    log = m.tick(4.2)
    v = m.snapshot(4.2)
    pi = machine(v, "hpc-pi")
    assert (pi.word, pi.tone) == ("lost", BAD)
    assert v.caption == "hpc-pi lost after 3.2 s · everything else carries on"
    assert "machine hpc-pi: lost" in log and f"caption: {v.caption}" in log
    assert all(a.tone == IDLE for a in pi.apps), "a lost machine's apps are unknown, so grey"


def test_G12_one_heartbeat_alive_keeps_the_machine_up():
    m = model()
    up(m, 0.0)
    beat(m, "hpc-pi", "hpc_monitor", 1, 3.5, state="lost")     # only the app hangs
    m.tick(3.5)
    assert machine(m.snapshot(3.5), "hpc-pi").word == "up"


def lose_and_return(back_seq, writer="w1"):
    m = model()
    up(m, 0.0, seq=5)
    for app in ("node_agent", "hpc_monitor"):
        beat(m, "hpc-pi", app, 5, 3.1, state="lost")
    m.tick(3.1)
    beat(m, "hpc-pi", "node_agent", back_seq, 7.4)
    beat(m, "hpc-pi", "hpc_monitor", back_seq, 7.4, writer=writer)
    log = m.tick(7.4)
    return m, log


def test_G13_back_with_no_restarts():
    m, log = lose_and_return(back_seq=12)
    v = m.snapshot(7.4)
    assert machine(v, "hpc-pi").word == "up"
    assert v.caption == "hpc-pi back after 7 s, no restarts"
    assert "machine hpc-pi: up" in log


def test_G13_back_with_apps_restarted():
    """A new heartbeat writer = a new process, even if its count has caught up."""
    m, _ = lose_and_return(back_seq=9, writer="w2")
    assert m.snapshot(7.4).caption == "hpc-pi back after 7 s, apps restarted"


def test_G14_crash_caption_only_for_a_change_seen_live():
    m = model()
    m.app_row("hpc-pi", "hpc_monitor", "APP_CRASHED", "", 0.0)       # found at start-up
    m.tick(0.0)
    assert m.snapshot(0.0).caption == ""
    m.app_row("hpc-pi", "hpc_monitor", "APP_RUNNING", "", 1.0)
    m.app_row("hpc-pi", "hpc_monitor", "APP_KILLED", "", 2.0)
    log = m.tick(2.0)
    v = m.snapshot(2.0)
    assert v.caption == "hpc_monitor on hpc-pi was killed"
    a = machine(v, "hpc-pi").apps[0]
    assert (a.word, a.tone) == ("killed", BAD)
    assert "app hpc-pi/hpc_monitor: killed" in log


def test_G14_rows_win_over_heartbeats_and_not_responding_is_amber():
    m = model()
    beat(m, "hpc-pi", "hpc_monitor", 1, 0.0)
    m.app_row("hpc-pi", "hpc_monitor", "APP_RUNNING", "not responding", 0.0)
    m.tick(0.0)
    a = machine(m.snapshot(0.0), "hpc-pi").apps[0]
    assert (a.word, a.tone) == ("not responding", WARN)


def test_app_word_from_heartbeat_without_an_agent():
    m = model()
    m.tick(0.0)
    assert machine(m.snapshot(0.0), "hpc-pi").apps[0].word == "not started"
    beat(m, "hpc-pi", "hpc_monitor", 1, 0.0)
    m.tick(0.0)
    assert machine(m.snapshot(0.0), "hpc-pi").apps[0].word == "running"


def test_G15_external_silence_and_return():
    m = model()
    up(m, 0.0)
    m.tick(2.0)
    assert machine(m.snapshot(2.0), "tc397").word == "data arriving"      # 2.0 s is not over 2 s
    log = m.tick(2.3)
    v = m.snapshot(2.3)
    assert (machine(v, "tc397").word, machine(v, "tc397").tone) == ("no data", BAD)
    assert v.caption == "tc397: no data for 2.3 s · everything else carries on"
    assert "external tc397: no data" in log
    m.data("tc397", 9.0)
    m.tick(9.0)
    assert m.snapshot(9.0).caption == "tc397: data back after 9 s"


def test_G16_a_clean_stop_is_stopped_without_a_caption():
    m = model()
    up(m, 0.0)
    for app in ("node_agent", "hpc_monitor"):
        beat(m, "hpc-pi", app, None, 1.0, state="stopped")
    log = m.tick(1.0)
    v = m.snapshot(1.0)
    assert (machine(v, "hpc-pi").word, machine(v, "hpc-pi").tone) == ("stopped", IDLE)
    assert v.caption == "" and "machine hpc-pi: stopped" in log


def test_G17_caption_fades_and_newest_replaces():
    m = model()
    up(m, 0.0)
    m.app_row("hpc-pi", "hpc_monitor", "APP_RUNNING", "", 0.0)
    m.app_row("hpc-pi", "hpc_monitor", "APP_CRASHED", "", 10.0)
    assert m.snapshot(13.9).caption_alpha == 1.0
    assert 0 < m.snapshot(14.5).caption_alpha < 1
    assert m.snapshot(15.0).caption == ""
    m.app_row("hpc-pi", "hpc_monitor", "APP_RUNNING", "", 11.0)
    m.app_row("hpc-pi", "hpc_monitor", "APP_KILLED", "", 11.0)
    assert m.snapshot(11.0).caption == "hpc_monitor on hpc-pi was killed"


def test_last_machine_lost_has_no_carry_on():
    m = Model({"a": {"layer": "compute"}}, 2.0)
    beat(m, "a", "x", 1, 0.0)
    m.tick(0.0)
    beat(m, "a", "x", 1, 3.0, state="lost")
    m.tick(3.0)
    assert m.snapshot(3.0).caption == "a lost after 3.0 s"


def test_fuzz_never_raises():
    """Random feeds in any order: no exception, every word is a known one."""
    import random
    rnd = random.Random(7)
    m = model()
    words = {"up", "lost", "stopped", "not seen yet", "data arriving", "no data"}
    nodes, apps = ["windows", "hpc-pi", "tc397", "stranger", ""], ["hpc_monitor", "node_agent", "x" * 40, ""]
    kinds = ["APP_RUNNING", "APP_CRASHED", "APP_KILLED", "APP_STARTING", "APP_WHATEVER", None]
    t = 0.0
    for _ in range(5000):
        t += rnd.choice([0.0, 0.01, 0.3, 4.0])
        r = rnd.random()
        if r < 0.5:
            beat(m, rnd.choice(nodes), rnd.choice(apps), rnd.choice([None, 0, 1, 2, 2**64 - 1]), t,
                 rnd.choice(["alive", "lost", "stopped"]))
        elif r < 0.8:
            m.app_row(rnd.choice(nodes), rnd.choice(apps), rnd.choice(kinds), rnd.choice(["", "not responding"]), t)
        else:
            m.data(rnd.choice(nodes), t)
        m.tick(t)
        v = m.snapshot(t)
        assert all(x.word in words for x in v.machines)


def test_R1_layer_from_the_scenario():
    """layer: when given; otherwise edge for an external device, compute for the rest."""
    from fw.app import ROOT
    from fw.scenario import external_topics, layer_of, load_nodes
    assert layer_of({"layer": "cloud"}) == "cloud" and layer_of({"external": True}) == "edge"
    assert layer_of({}) == "compute" and layer_of({"external": True, "layer": "compute"}) == "compute"
    nodes = load_nodes(ROOT, "temperature-skeleton")
    assert {n: layer_of(s) for n, s in nodes.items()} == {
        "windows": "compute", "hpc-vm": "compute", "hpc-pi": "compute", "tc397": "edge"}
    assert external_topics(ROOT, "tc397") == ["Example Temperature"]
    assert external_topics(ROOT, "no-such-node") == []


# --- links (merge 3, R2) -------------------------------------------------------------

LINKS = [("Example Temperature", ("tc397", "tc397_twin"), ("hpc-pi", "hpc_monitor")),
         ("Alert", ("hpc-pi", "hpc_monitor"), ("windows", "ivi_standin"))]


def test_G21_links_shown_with_a_log_line_each():
    m = model()
    m.links(LINKS)
    log = m.tick(0.0)
    v = m.snapshot(0.0)
    assert {(k.topic, k.writer, k.reader) for k in v.links} == {
        ("Example Temperature", ("tc397", None), ("hpc-pi", "hpc_monitor")),     # the device itself
        ("Alert", ("hpc-pi", "hpc_monitor"), ("windows", "ivi_standin"))}
    assert "link Alert: hpc-pi/hpc_monitor -> windows/ivi_standin" in log
    assert "link Example Temperature: tc397 -> hpc-pi/hpc_monitor" in log
    assert [t for t, _ in v.topics] == ["Alert", "Example Temperature"]
    m.links(LINKS[:1])
    assert "link gone Alert: hpc-pi/hpc_monitor -> windows/ivi_standin" in m.tick(1.0)


def test_G25_plumbing_presenter_tools_and_strangers_left_out():
    m = model()
    m.links([("_sys/NodeStatus", ("hpc-pi", "hpc_monitor"), ("windows", "ivi_standin")),
             ("Alert", ("hpc-pi", "node_agent"), ("windows", "ivi_standin")),
             ("Alert", ("hpc-pi", "hpc_monitor"), ("windows", "result_gui")),
             ("Alert", ("test", "bus"), ("windows", "ivi_standin")),
             ("Alert", ("hpc-pi", None), ("windows", "ivi_standin")),        # only a device has no app
             ("Alert", ("hpc-pi", "hpc_monitor"), ("hpc-pi", "hpc_monitor"))])  # an app to itself
    m.tick(0.0)
    assert m.snapshot(0.0).links == ()


def test_colours_stay_with_their_topic():
    m = model()
    m.links(LINKS)
    first = {k.topic: k.colour for k in m.snapshot(0.0).links}
    m.links([("Zebra", ("hpc-pi", "hpc_monitor"), ("windows", "ivi_standin"))] + LINKS)
    again = {k.topic: k.colour for k in m.snapshot(0.0).links}
    assert again["Alert"] == first["Alert"] and again["Example Temperature"] == first["Example Temperature"]
    assert len(set(again.values())) == 3


def test_G26_links_dim_when_an_end_is_lost():
    m = model()
    up(m, 0.0)
    m.links(LINKS)
    m.tick(0.0)
    assert not any(k.dim for k in m.snapshot(0.0).links)
    for app in ("node_agent", "hpc_monitor"):
        beat(m, "hpc-pi", app, 1, 3.5, state="lost")
    m.data("tc397", 3.5)
    m.tick(3.5)
    assert all(k.dim for k in m.snapshot(3.5).links), "both links touch hpc-pi"


def test_an_app_heard_only_through_its_data_gets_a_pill():
    m = model()
    m.links([("Alert", ("hpc-pi", "logger"), ("windows", "ivi_standin"))])
    v = m.snapshot(0.0)
    assert "logger" in [a.name for a in machine(v, "hpc-pi").apps]
    assert machine(v, "tc397").apps == ()
