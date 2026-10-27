"""result_gui's layout: where everything goes, for a given window size.

Pure arithmetic (no Qt), so tests can check every size quickly: nothing
overlaps, nothing leaves the window. Rows top to bottom (R1): cloud, a WAN
line, compute, the switch, edge. Space is kept at the top for the title (the
alert banner from merge 4) and at the bottom for the caption.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MIN_W, MIN_H = 800, 450                  # the window can't be made smaller
ROW_WEIGHT = {"cloud": 1.0, "compute": 1.3, "switch": 0.35, "edge": 1.0}


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    def overlaps(self, o: "Rect") -> bool:
        return self.x < o.right and o.x < self.right and self.y < o.bottom and o.y < self.bottom

    def inside(self, o: "Rect") -> bool:
        return self.x >= o.x and self.y >= o.y and self.right <= o.right and self.bottom <= o.bottom


@dataclass
class Box:
    """One machine: its outline, the name and state lines, its app pills."""
    rect: Rect
    name: Rect
    state: Rect
    dot: tuple[float, float, float]      # x, y, radius of the state dot
    pills: list[Rect] = field(default_factory=list)
    more: int = 0                        # apps not shown: the last pill reads "+N more"
    port: tuple[float, float] = (0, 0)   # where its cable leaves the box
    plug: tuple[float, float] = (0, 0)   # where its cable enters the switch


@dataclass
class Layout:
    window: Rect
    title: Rect
    caption: Rect
    bands: dict[str, Rect]               # layer -> its faint band
    switch: Rect
    wan_y: float | None                  # the dashed WAN line, if there is a cloud row
    boxes: dict[str, Box]
    banner: list[Rect]                   # one per alert line, in the title's place
    name_px: int                         # font sizes in pixels
    word_px: int
    pill_px: int
    label_px: int


def layout(machines: list[tuple[str, str, int, bool]], w: float, h: float, banner: int = 0) -> Layout:
    """machines: (name, layer, number of apps, external), in scenario order.
    banner: alert lines to show (0-3); they take the title's place, one line each (R4)."""
    w, h = max(w, MIN_W), max(h, MIN_H)
    pad = round(min(w, h) * 0.02)
    gutter = max(70, w * 0.07)                       # layer labels on the left
    banner_h = h * 0.055                              # one alert line: large, it is the headline
    title = Rect(pad, pad, w - 2 * pad, max(h * 0.07, banner * (banner_h + pad * 0.4)))
    caption = Rect(pad, h - pad - h * 0.08, w - 2 * pad, h * 0.08)

    by_layer: dict[str, list] = {}
    for m in machines:
        by_layer.setdefault(m[1], []).append(m)
    rows = [r for r in ("cloud", "compute") if by_layer.get(r)] + ["switch"] + \
           (["edge"] if by_layer.get("edge") else [])
    top, bottom = title.bottom + pad, caption.y - pad
    gap = pad * (1.5 if "cloud" in rows else 1)
    unit = (bottom - top - gap * (len(rows) - 1)) / sum(ROW_WEIGHT[r] for r in rows)
    spans, y = {}, top
    for r in rows:
        spans[r] = Rect(pad, y, w - 2 * pad, unit * ROW_WEIGHT[r])
        y += spans[r].h + gap

    row_h = min([spans[r].h for r in rows if r != "switch"] or [unit])
    name_px = max(12, round(row_h * 0.11))
    word_px = max(11, round(row_h * 0.09))
    label_px = max(11, round(h * 0.018))
    sw = spans["switch"]
    switch = Rect(w / 2 - min(w * 0.25, 360), sw.y + sw.h * 0.2, 2 * min(w * 0.25, 360), sw.h * 0.6)

    wan_y = None
    if "cloud" in spans:
        wan_y = (spans["cloud"].bottom + spans["compute" if "compute" in spans else "switch"].y) / 2
    boxes: dict[str, Box] = {}
    pill_px = word_px
    for r in ("cloud", "compute", "edge"):
        ms = by_layer.get(r) or []
        if not ms:
            continue
        span = spans[r]
        x0, avail = gutter + pad, w - gutter - 3 * pad
        slot = avail / len(ms)
        bw = min(slot * 0.88, avail * 0.3)
        for i, (name, _, n_apps, external) in enumerate(ms):
            bh = span.h * (0.6 if external else 0.9)
            rect = Rect(x0 + slot * i + (slot - bw) / 2, span.y + (span.h - bh) / 2, bw, bh)
            inner = pad * 0.6
            line = name_px * 1.35
            name_r = Rect(rect.x + inner, rect.y + inner, bw - 2 * inner, line)
            state_r = Rect(rect.x + inner, name_r.bottom, bw - 2 * inner, word_px * 1.35)
            radius = word_px * 0.32
            dot = (state_r.x + radius, state_r.y + state_r.h / 2, radius)
            box = Box(rect, name_r, state_r, dot)
            pill_px = _pills(box, n_apps, rect, state_r.bottom + inner * 0.5, inner, word_px, pill_px)
            down = r != "edge"                       # cloud and compute cables leave from below
            box.port = (rect.cx, rect.bottom if down else rect.y)
            if r == "cloud":                         # the cloud is reached over the WAN, not the switch
                box.plug = (rect.cx, wan_y)
            else:
                side = [m[0] for m in machines if m[1] == r]
                k = side.index(name)
                box.plug = (switch.x + switch.w * (k + 1) / (len(side) + 1), switch.y if down else switch.bottom)
            boxes[name] = box
    bands = {r: spans[r] for r in ("cloud", "compute", "edge") if r in spans}
    lines = [Rect(title.x, title.y + i * (banner_h + pad * 0.4), title.w, banner_h) for i in range(banner)]
    return Layout(Rect(0, 0, w, h), title, caption, bands, switch, wan_y, boxes, lines,
                  name_px, word_px, pill_px, label_px)


def _pills(box: Box, n: int, rect: Rect, top: float, inner: float, word_px: int, pill_px: int) -> int:
    """Stack the app pills; if they don't all fit, the last one becomes "+N more"."""
    if n == 0:
        return pill_px
    room = rect.bottom - inner - top
    gap = max(2.0, inner * 0.4)
    tallest, shortest = word_px * 1.9, word_px * 1.35
    h = min(tallest, (room - gap * (n - 1)) / n)
    shown = n
    if h < shortest:                                   # too many: show what fits, then "+N more"
        h = shortest
        shown = max(1, int((room + gap) // (h + gap)))
        box.more = n - shown + 1 if shown < n else 0
    for i in range(shown):
        # Room kept on the right for links between apps of this machine (shared memory).
        box.pills.append(Rect(rect.x + inner, top + i * (h + gap), rect.w - 2 * inner - word_px, h))
    return min(pill_px, max(10, round(h * 0.55)))


# --- links (R2) ---------------------------------------------------------------------

def anchor(box: Box, index: int | None) -> Rect | None:
    """The pill an app's links start or end at: its own, or "+N more" if it isn't
    shown. None: the machine itself (a device has no apps drawn)."""
    if index is None or not box.pills:
        return None
    return box.pills[min(index, len(box.pills) - 1)]


def route(lay: Layout, wbox: Box, wpill: Rect | None, rbox: Box, rpill: Rect | None,
          lane: int, lanes: int):
    """A link's path as points, plus its arrow (tip, from) at the reader's end.
    Two machines: out of the writer's cable, through the switch, into the
    reader's cable (UDP). One machine: a short bracket beside its pills (shared
    memory). Each topic has its own lane, so links of different topics never
    lie on top of each other."""
    gap = min(max(6.0, lay.word_px * 0.6), lay.switch.h * 0.8 / max(lanes, 1))   # lanes fit in the switch
    dx = (lane - (lanes - 1) / 2) * gap
    if wbox is rbox and wpill is not None and rpill is not None:
        margin = wbox.rect.right - max(wpill.right, rpill.right)       # the room _pills() keeps
        x = max(wpill.right, rpill.right) + margin * (0.3 + 0.4 * lane / max(lanes - 1, 1))
        pts = [(wpill.right, wpill.y + wpill.h * 0.35), (x, wpill.y + wpill.h * 0.35),
               (x, rpill.y + rpill.h * 0.65), (rpill.right, rpill.y + rpill.h * 0.65)]
        return pts, pts[-1], pts[-2]
    sw = lay.switch
    y_mid = sw.y + sw.h / 2 + dx

    def side(box: Box, pill: Rect | None) -> list[tuple[float, float]]:
        """From an app (or the machine) to the middle of the switch, in that order."""
        port = (box.port[0] + dx, box.port[1])
        start = (port[0], pill.y + pill.h / 2) if pill is not None else port    # straight up from the cable
        plug_x, plug_y = box.plug[0] + dx, box.plug[1]
        if plug_y in (sw.y, sw.bottom):
            into = [(plug_x, plug_y), (plug_x, y_mid)]
        else:                                          # the cloud: over the WAN, into the switch's top
            into = [(plug_x, plug_y), (sw.cx + dx, sw.y), (sw.cx + dx, y_mid)]
        return [start, port] + into
    w, r = side(wbox, wpill), side(rbox, rpill)
    pts = w + list(reversed(r))
    port_r = r[1]
    return pts, port_r, r[2]
