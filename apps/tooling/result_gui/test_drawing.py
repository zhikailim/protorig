"""test_drawing.py — is the rig drawn correctly, at every window size? (G9, G18)

Two kinds of check:
- layout.py, pure arithmetic: nothing overlaps, nothing leaves the window,
  layers in order, text sizes fit their boxes, "+N more" adds up.
- the real window, rendered headless: colours at known points (a lost
  machine's dot is red), and a screenshot per case in build/test-shots/ for review.
"""
import itertools
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
from layout import MIN_H, MIN_W, anchor, layout, route   # noqa: E402
from model import Model                    # noqa: E402

SIZES = [(MIN_W, MIN_H), (1280, 720), (1366, 768), (1920, 1080), (3840, 2160), (1000, 1000), (2560, 600)]
SKELETON = [("windows", "compute", 1, False), ("hpc-vm", "compute", 1, False),
            ("hpc-pi", "compute", 1, False), ("tc397", "edge", 0, True)]
CASES = {
    "skeleton": SKELETON,
    "with-cloud": [("fleet-cloud", "cloud", 3, False)] + SKELETON,
    "crowded": [(f"zone-{i}", "edge", 2, False) for i in range(6)] + [(f"hpc-{i}", "compute", 4, False) for i in range(5)],
    "many-apps": [("hpc", "compute", 25, False), ("ecu", "edge", 0, True)],
    "compute-only": [("one", "compute", 2, False)],
}


@pytest.mark.parametrize("case,size,banner", list(itertools.product(CASES, SIZES, (0, 3))), ids=lambda x: str(x))
def test_G18_nothing_overlaps_or_leaves_the_window(case, size, banner):
    """With no alerts and with the most alert lines (3, R4)."""
    machines = CASES[case]
    lay = layout(machines, *size, banner)
    win = lay.window
    assert len(lay.banner) == banner and all(r.inside(lay.title) for r in lay.banner)
    for a, b in itertools.combinations(lay.banner, 2):
        assert not a.overlaps(b)
    fixed = [("title", lay.title), ("caption", lay.caption), ("switch", lay.switch)]
    boxes = [(n, b.rect) for n, b in lay.boxes.items()]
    for name, r in fixed + boxes:
        assert r.inside(win), f"{name} {r} leaves the window {win}"
        assert r.w > 0 and r.h > 0, name
    for (a, ra), (b, rb) in itertools.combinations(fixed + boxes, 2):
        assert not ra.overlaps(rb), f"{a} overlaps {b}"
    for name, layer, n_apps, _ in machines:
        b = lay.boxes[name]
        assert b.rect.inside(lay.bands[layer]), f"{name} leaves its {layer} band"
        assert b.name.inside(b.rect) and b.state.inside(b.rect), name
        x, y, r = b.dot
        e = 0.01                             # float rounding
        assert b.state.x - e <= x - r and x + r <= b.state.right + e
        assert b.state.y - e <= y - r and y + r <= b.state.bottom + e
        for p in b.pills:
            assert p.inside(b.rect) and p.y >= b.state.bottom, f"{name}: pill {p} outside its box"
        for p, q in itertools.combinations(b.pills, 2):
            assert not p.overlaps(q), f"{name}: pills overlap"
        shown = len(b.pills) - (1 if b.more else 0)
        assert shown + b.more == n_apps, f"{name}: {shown} shown + {b.more} more != {n_apps} apps"
        # Text fits its line (Qt draws a line about 1.2 x the pixel size tall).
        assert lay.name_px * 1.2 <= b.name.h + 0.5 and lay.word_px * 1.2 <= b.state.h + 0.5, name
        for p in b.pills:
            assert lay.pill_px * 1.2 <= p.h + 0.5, f"{name}: pill text taller than its pill"
        sx, sy = b.plug                      # the cable ends on the switch, or the WAN line for the cloud
        if layer == "cloud":
            assert (sx, sy) == (b.rect.cx, lay.wan_y)
        else:
            assert lay.switch.x < sx < lay.switch.right and sy in (lay.switch.y, lay.switch.bottom)
    for layer in ("compute", "edge"):        # cables to the switch never cross
        xs = [(lay.boxes[n].port[0], lay.boxes[n].plug[0]) for n, l, _, _ in machines if l == layer]
        assert xs == sorted(xs) and [p for _, p in xs] == sorted(p for _, p in xs), f"{layer} cables cross"


@pytest.mark.parametrize("size", SIZES, ids=str)
def test_G9_layers_top_to_bottom(size):
    lay = layout(CASES["with-cloud"], *size)
    b = lay.bands
    assert b["cloud"].bottom < lay.wan_y < b["compute"].y
    assert b["compute"].bottom < lay.switch.y and lay.switch.bottom < b["edge"].y
    assert lay.title.bottom <= b["cloud"].y and b["edge"].bottom <= lay.caption.y
    assert layout(SKELETON, *size).wan_y is None, "no cloud: no WAN line"


def test_readable_at_5_m_on_a_1080p_screen():
    """R1: large type. Names at least 2.5 % of the screen height."""
    lay = layout(SKELETON, 1920, 1080)
    assert lay.name_px >= 27 and lay.word_px >= 22, (lay.name_px, lay.word_px)


def test_min_size_is_enforced():
    lay = layout(SKELETON, 300, 200)
    assert (lay.window.w, lay.window.h) == (MIN_W, MIN_H)


# --- the real window, rendered headless --------------------------------------------

SHOTS = REPO / "build" / "test-shots"
COLOURS = {"green": (0x3f, 0xb9, 0x50), "red": (0xf8, 0x51, 0x49), "grey": (0x7d, 0x85, 0x90),
           "bg": (0x0f, 0x12, 0x16)}


@pytest.fixture(scope="module")
def qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
    pytest.importorskip("PySide6", reason="needs PySide6: pip install -r bootstrap/requirements.txt")
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def rig_model(now):
    """temperature-skeleton at the moment hpc-pi is lost and hpc_monitor on hpc-vm crashed."""
    nodes = {"windows": {"layer": "compute", "run": ["result_gui", "control_panel", "ivi_standin"]},
             "hpc-vm": {"layer": "compute", "run": ["hpc_monitor"]},
             "hpc-pi": {"layer": "compute", "run": ["hpc_monitor"]},
             "tc397": {"layer": "edge", "external": True},
             "fleet-cloud": {"layer": "cloud", "run": []}}
    m = Model(nodes, 2.0)
    for node, app in (("windows", "ivi_standin"), ("hpc-vm", "node_agent"), ("hpc-vm", "hpc_monitor"),
                      ("hpc-pi", "node_agent"), ("hpc-pi", "hpc_monitor")):
        m.heartbeat(node, app, 1, "alive", now - 5)
    m.app_row("hpc-vm", "hpc_monitor", "APP_RUNNING", "", now - 5)
    m.data("tc397", now - 5)
    m.tick(now - 5)
    for app in ("node_agent", "hpc_monitor"):
        m.heartbeat("hpc-pi", app, 1, "lost", now)
    m.app_row("hpc-vm", "hpc_monitor", "APP_CRASHED", "", now)
    m.data("tc397", now)
    m.links(RIG_LINKS)
    m.tick(now)
    return m


RIG_LINKS = [("Example Temperature", ("tc397", "tc397_twin"), ("hpc-vm", "hpc_monitor")),   # lit
             ("Alert", ("hpc-pi", "hpc_monitor"), ("windows", "ivi_standin")),             # dim: hpc-pi lost
             ("Alert", ("windows", "ivi_standin"), ("windows", "logger"))]                 # shared memory


def render(qt, model, size, name):
    from window import Window
    w = Window("temperature-skeleton", model, clock=lambda: 100.0)
    w.resize(*size)
    img = w.grab().toImage()
    SHOTS.mkdir(parents=True, exist_ok=True)
    img.save(str(SHOTS / f"result_gui-{name}-{size[0]}x{size[1]}.png"))
    lay = w.last_layout
    w.close()
    return img, lay


def colour_at(img, x, y):
    c = img.pixelColor(int(x), int(y))
    return c.red(), c.green(), c.blue()


def near(rgb, name, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(rgb, COLOURS[name]))


@pytest.mark.parametrize("size", [(MIN_W, MIN_H), (1280, 720), (1920, 1080)], ids=str)
def test_G12_G14_colours_on_screen(qt, size):
    img, lay = render(qt, rig_model(100.0), size, "lost")
    assert (img.width(), img.height()) == size
    dot = {n: colour_at(img, *b.dot[:2]) for n, b in lay.boxes.items()}
    assert near(dot["hpc-pi"], "red"), f"lost machine's dot: {dot['hpc-pi']}"
    assert near(dot["hpc-vm"], "green") and near(dot["windows"], "green") and near(dot["tc397"], "green"), dot
    assert near(dot["fleet-cloud"], "grey"), f"never seen: {dot['fleet-cloud']}"
    # The crashed app's pill: its right end (where the word is) has red in it.
    pill = lay.boxes["hpc-vm"].pills[0]
    row = [colour_at(img, x, pill.y + pill.h / 2) for x in range(int(pill.cx), int(pill.right) - 2)]
    assert any(near(c, "red", 60) for c in row), "the crashed app's word should be red"
    # The caption is drawn (bright pixels in its area), the corner is background.
    cap = lay.caption
    bright = [colour_at(img, x, cap.y + cap.h / 2) for x in range(int(cap.x), int(cap.right), 2)]
    assert any(sum(c) > 500 for c in bright), "no caption drawn"
    assert near(colour_at(img, 2, size[1] - 2), "bg", 5)


def test_no_machines_says_what_to_do(qt):
    img, lay = render(qt, Model({}, 2.0), (1280, 720), "empty")
    mid = [colour_at(img, x, 360) for x in range(200, 1080, 2)]
    assert any(sum(c) > 300 for c in mid), "the hint should be drawn mid-screen"


def case_model(case):
    """Every machine up, with long app names, so text shortening is exercised too."""
    nodes = {n: {"layer": layer, "external": ext,
                 "run": [f"app_{i}_with_a_rather_long_name_{n}" for i in range(k)]}
             for n, layer, k, ext in CASES[case]}
    m = Model(nodes, 2.0)
    for n, spec in nodes.items():
        m.heartbeat(n, "node_agent", 1, "alive", 100.0)
        if spec["external"]:
            m.data(n, 100.0)
    m.tick(100.0)
    return m


@pytest.mark.parametrize("case,size", [(c, s) for c in CASES for s in ((MIN_W, MIN_H), (1920, 1080))], ids=str)
def test_G18_every_case_renders(qt, case, size, capfd):
    img, lay = render(qt, case_model(case), size, case)
    assert (img.width(), img.height()) == size and len(lay.boxes) == len(CASES[case])
    assert "drawing failed" not in capfd.readouterr().out


def test_G19_a_drawing_error_is_logged_not_fatal(qt, capfd):
    class Broken(Model):
        def snapshot(self, now):
            raise RuntimeError("boom (test)")
    img, _ = render(qt, Broken({}, 2.0), (1280, 720), "broken")
    out = capfd.readouterr().out
    assert img.width() == 1280 and "drawing failed" in out and "boom (test)" in out


# --- links (merge 3, R2) -------------------------------------------------------------

@pytest.mark.parametrize("case,size", list(itertools.product(CASES, SIZES)), ids=str)
def test_G23_link_routes_stay_inside_the_window(case, size):
    """Every machine to every other (and to itself): all points inside the window,
    through both machines' cables, never into another machine's box."""
    machines = CASES[case]
    lay = layout(machines, *size)
    names = [m[0] for m in machines]
    layer_of = {m[0]: m[1] for m in machines}
    for (wn, rn), lane in zip(itertools.product(names, names), itertools.cycle(range(3))):
        wb, rb = lay.boxes[wn], lay.boxes[rn]
        wp, rp = anchor(wb, 0), anchor(rb, 1 if wb is rb else 0)
        if wb is rb and (wp is None or wp is rp):
            continue
        pts, tip, back = route(lay, wb, wp, rb, rp, lane, 3)
        for x, y in pts:
            assert 0 <= x <= lay.window.w and 0 <= y <= lay.window.h, f"{wn}->{rn}: {(x, y)} outside"
        if wb is rb:                                   # shared memory: beside the pills, in the box
            assert all(wb.rect.x < x < wb.rect.right and wb.rect.y < y < wb.rect.bottom for x, y in pts)
            assert all(x >= max(wp.right, rp.right) for x, _ in pts)
        else:
            assert abs(tip[1] - rb.port[1]) < 0.01, "the arrow sits where the link enters the reader"
            if "cloud" in (layer_of[wn], layer_of[rn]):
                continue                               # known: a cloud link crosses the compute row
            others = [b.rect for n, b in lay.boxes.items() if n not in (wn, rn)]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                for k in (0.25, 0.5, 0.75):
                    x, y = x1 + (x2 - x1) * k, y1 + (y2 - y1) * k
                    assert not any(r.x < x < r.right and r.y < y < r.bottom for r in others), \
                        f"{wn}->{rn} crosses another machine at {(x, y)}"


def test_G26_link_colours_on_screen(qt):
    """A link is drawn in its topic's colour along the reader's cable."""
    from window import TOPIC
    img, lay = render(qt, rig_model(100.0), (1920, 1080), "links")
    view = rig_model(100.0).snapshot(100.0)
    colour = {k.topic: k.colour for k in view.links}
    temp = TOPIC[colour["Example Temperature"]]
    want = tuple(int(temp[i:i + 2], 16) for i in (1, 3, 5))
    tc, vm = lay.boxes["tc397"], lay.boxes["hpc-vm"]
    y = (vm.port[1] + lay.switch.y) / 2                    # the hpc-vm cable, between box and switch
    row = [colour_at(img, x, y) for x in range(int(vm.rect.x), int(vm.rect.right))]
    assert any(all(abs(a - b) < 40 for a, b in zip(c, want)) for c in row), "the temperature link isn't on hpc-vm's cable"
    assert tc.port[1] > lay.switch.bottom              # it came up from the edge row


# --- merge 4: the alert banner (R4) --------------------------------------------------

def test_G29_banner_on_screen(qt):
    """A critical alert: a red bar at the left of the first line, the message in light text."""
    m = rig_model(100.0)
    m.alert("hpc-vm/hpc_monitor", "temp_high", 2, "Temperature high: 14.7 °C", "alive", 100.0)
    m.alert("hpc-pi/x", "w", 1, "a warning whose source vanished", "lost", 100.0)
    m.tick(100.0)
    for size in ((MIN_W, MIN_H), (1920, 1080)):
        img, lay = render(qt, m, size, "banner")
        first, second = lay.banner
        assert near(colour_at(img, first.x + 1, first.y + first.h / 2), "red"), "critical: red bar"
        assert near(colour_at(img, second.x + 1, second.y + second.h / 2), "grey"), "source lost: grey bar"
        row = [colour_at(img, x, first.y + first.h / 2) for x in range(int(first.x), int(first.cx))]
        assert any(sum(c) > 600 for c in row), "the message should be drawn in light text"
