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
    name_px: int                         # font sizes in pixels
    word_px: int
    pill_px: int
    label_px: int


def layout(machines: list[tuple[str, str, int, bool]], w: float, h: float) -> Layout:
    """machines: (name, layer, number of apps, external), in scenario order."""
    w, h = max(w, MIN_W), max(h, MIN_H)
    pad = round(min(w, h) * 0.02)
    gutter = max(70, w * 0.07)                       # layer labels on the left
    title = Rect(pad, pad, w - 2 * pad, h * 0.07)
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
    return Layout(Rect(0, 0, w, h), title, caption, bands, switch, wan_y, boxes,
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
        box.pills.append(Rect(rect.x + inner, top + i * (h + gap), rect.w - 2 * inner, h))
    return min(pill_px, max(10, round(h * 0.55)))
