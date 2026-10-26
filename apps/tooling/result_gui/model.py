"""result_gui's model: what the screen shows, worked out from what DDS delivers.

No DDS and no Qt here. main.py feeds it from the DDS thread; the window reads
snapshot() from the Qt thread (hence the lock). Time is passed in, so tests
drive it without waiting.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

HIDDEN = {"node_agent", "result_gui", "control_panel"}   # presenter tools: never drawn (R1)
CAPTION_FOR = 5.0                                          # seconds a caption stays (R3)
FADE = 1.0                                                 # its last second fades out

# Tones: the window picks the colour; every tone also comes with a word (R1).
OK, BAD, WARN, IDLE = "ok", "bad", "warn", "idle"

# The agent's app rows (_sys/AppState) as screen words.
ROW_WORDS = {
    "APP_NOT_RUNNING": ("not running", IDLE), "APP_UNAVAILABLE": ("unavailable", BAD),
    "APP_STARTING": ("starting", WARN), "APP_RUNNING": ("running", OK),
    "APP_RESTARTING": ("restarting", WARN), "APP_STOPPING": ("stopping", WARN),
    "APP_STOPPED": ("stopped", IDLE), "APP_KILLED": ("killed", BAD), "APP_CRASHED": ("crashed", BAD),
}
CAPTIONED = {"APP_CRASHED": "crashed", "APP_KILLED": "was killed"}


@dataclass(frozen=True)
class AppView:
    name: str
    word: str
    tone: str


@dataclass(frozen=True)
class MachineView:
    name: str
    layer: str                 # cloud | compute | edge
    external: bool
    word: str                  # up | lost | stopped | not seen yet | data arriving | no data
    tone: str
    apps: tuple[AppView, ...]


@dataclass(frozen=True)
class LinkView:
    """A writer that reaches a reader (R2). app None: the device itself (an ECU)."""
    topic: str
    colour: int                # index into the window's topic palette, stable per topic
    writer: tuple[str, str | None]     # (node, app)
    reader: tuple[str, str | None]
    dim: bool                  # an end is lost, stopped or silent (R3)


@dataclass(frozen=True)
class View:
    machines: tuple[MachineView, ...]
    caption: str
    caption_alpha: float       # 1 = fully visible, 0 = gone
    links: tuple[LinkView, ...] = ()
    topics: tuple[tuple[str, int], ...] = ()   # the legend: (topic, colour) of the links shown


@dataclass
class _Beat:
    seq: int
    state: str                 # alive | lost | stopped
    changed: float             # when seq last changed (the last heartbeat heard)
    writer: str                # which DataWriter sent it: a new one = the app restarted


@dataclass
class _Machine:
    name: str
    layer: str
    external: bool
    run: list[str]             # apps from run:, in order
    word: str = "not seen yet"
    since: float = 0.0         # lost/no data: when the last heartbeat or sample came
    writers: dict | None = None  # app -> its heartbeat's writer when lost (to tell "no restarts")
    last_data: float | None = None


class Model:
    def __init__(self, nodes: dict[str, dict], silence: float):
        """nodes: name -> {"layer", "external", "run": [app, ...]}, in scenario order."""
        self._lock = threading.Lock()
        self._silence = silence
        self._m = {n: _Machine(n, s["layer"], bool(s.get("external")), list(s.get("run", [])))
                   for n, s in nodes.items()}
        self._beats: dict[tuple[str, str], _Beat] = {}
        self._rows: dict[tuple[str, str], tuple[str, str]] = {}   # (node, app) -> (kind, detail)
        self._caption, self._caption_at = "", -1e9
        self._log: list[str] = []
        self._links: set[tuple] = set()            # (topic, w_node, w_app, r_node, r_app)
        self._colour: dict[str, int] = {}          # topic -> palette index, kept once given

    # ---------------------------------------------------------------- feeds (DDS thread)

    def heartbeat(self, node: str, app: str, seq: int | None, state: str, now: float, writer: str = "") -> None:
        """One heartbeat instance as read now. seq None: no data with it (a disposed
        instance). writer: the sending DataWriter's identity (Connext's publication handle)."""
        with self._lock:
            b = self._beats.get((node, app))
            if b is None:
                self._beats[(node, app)] = _Beat(seq or 0, state, now, writer)
                return
            if seq is not None and (seq != b.seq or writer != b.writer):
                b.seq, b.changed = seq, now
                b.writer = writer or b.writer
            b.state = state

    def app_row(self, node: str, app: str, kind: str | None, detail: str, now: float) -> None:
        """The agent's row for an app; kind None: the row is gone (agent stopped)."""
        with self._lock:
            old = self._rows.get((node, app))
            if kind is None:
                self._rows.pop((node, app), None)
                return
            self._rows[(node, app)] = (kind, detail)
            # A caption only for a change seen live, not for a row found at start-up.
            if old is not None and old[0] != kind and kind in CAPTIONED and app not in HIDDEN:
                self._say(f"{app} on {node} {CAPTIONED[kind]}", now)
            if old is None or old != (kind, detail):
                word, _ = self._row_word(kind, detail)
                if app not in HIDDEN and node in self._m:
                    self._log.append(f"app {node}/{app}: {word}")

    def data(self, node: str, now: float) -> None:
        """A sample arrived on one of an external device's topics."""
        with self._lock:
            m = self._m.get(node)
            if m is not None:
                m.last_data = now

    def links(self, found) -> None:
        """Every writer -> reader pair Connext would match, as
        (topic, (w_node, w_app), (r_node, r_app)). Plumbing (_sys/ topics), presenter
        tools and nodes outside the scenario are left out here, in one place."""
        def end(e):                        # an external device is drawn as one box: no apps
            node, app = e
            m = self._m.get(node)
            if m is None or app in HIDDEN or (app is None and not m.external):
                return None
            return (node, None) if m.external else (node, app)
        ends = [(t, end(w), end(r)) for t, w, r in found if not t.startswith("_sys/")]
        new = {(t, *w, *r) for t, w, r in ends if w and r and w != r}
        with self._lock:
            for t in sorted({x[0] for x in new} - set(self._colour)):
                self._colour[t] = len(self._colour)
            for x in sorted(new - self._links):
                self._log.append(f"link {x[0]}: {self._who(x[1], x[2])} -> {self._who(x[3], x[4])}")
            for x in sorted(self._links - new):
                self._log.append(f"link gone {x[0]}: {self._who(x[1], x[2])} -> {self._who(x[3], x[4])}")
            self._links = new

    @staticmethod
    def _who(node, app):
        return f"{node}/{app}" if app else node

    # ---------------------------------------------------------------- tick (DDS thread)

    def tick(self, now: float) -> list[str]:
        """Work out each machine's state; returns the log lines for what changed."""
        with self._lock:
            for m in self._m.values():
                word, since = self._machine_word(m, now)
                if word != m.word:
                    self._changed(m, word, since, now)
            out, self._log = self._log, []
            return out

    def _machine_word(self, m: _Machine, now: float) -> tuple[str, float]:
        if m.external:
            if m.last_data is None:
                return "not seen yet", 0.0
            if now - m.last_data > self._silence:
                return "no data", m.last_data
            return "data arriving", m.last_data
        beats = [b for (n, _), b in self._beats.items() if n == m.name]
        if any(b.state == "alive" for b in beats):
            return "up", 0.0
        if any(b.state == "lost" for b in beats):
            return "lost", max(b.changed for b in beats if b.state == "lost")
        if beats:
            return "stopped", 0.0
        return "not seen yet", 0.0

    def _changed(self, m: _Machine, word: str, since: float, now: float) -> None:
        others_ok = any(o.word in ("up", "data arriving") for o in self._m.values() if o is not m)
        carry_on = " · everything else carries on" if others_ok else ""
        was = m.word
        if word == "lost":
            self._say(f"{m.name} lost after {now - since:.1f} s{carry_on}", now)
            m.since = since
            m.writers = {a: b.writer for (n, a), b in self._beats.items() if n == m.name}
        elif word == "no data":
            self._say(f"{m.name}: no data for {now - since:.1f} s{carry_on}", now)
            m.since = since
        elif was in ("lost", "no data") and word in ("up", "data arriving"):
            gone = now - m.since
            if m.external:
                self._say(f"{m.name}: data back after {gone:.0f} s", now)
            else:
                # Same writers as before: the processes were paused or cut off, not restarted.
                back = {a: b.writer for (n, a), b in self._beats.items() if n == m.name and b.state == "alive"}
                restarted = any(w != m.writers.get(a, w) for a, w in back.items())
                self._say(f"{m.name} back after {gone:.0f} s, "
                          f"{'apps restarted' if restarted else 'no restarts'}", now)
        m.word = word
        self._log.append(f"{'external' if m.external else 'machine'} {m.name}: {word}")

    def _say(self, text: str, now: float) -> None:
        """A caption; the newest replaces the previous one (R3)."""
        self._caption, self._caption_at = text, now
        self._log.append(f"caption: {text}")

    # ---------------------------------------------------------------- snapshot (Qt thread)

    @staticmethod
    def _row_word(kind: str, detail: str) -> tuple[str, str]:
        if kind == "APP_RUNNING" and detail == "not responding":
            return "not responding", WARN
        return ROW_WORDS.get(kind, (kind.removeprefix("APP_").lower().replace("_", " "), IDLE))

    def _app_view(self, m: _Machine, app: str) -> AppView:
        row = self._rows.get((m.name, app))
        if row is not None:
            word, tone = self._row_word(*row)
        else:                                      # no agent (e.g. `run --app`): the heartbeat
            b = self._beats.get((m.name, app))
            word, tone = (("not started", IDLE) if b is None else
                          {"alive": ("running", OK), "lost": ("lost", BAD)}.get(b.state, ("stopped", IDLE)))
        if m.word == "lost":                       # nothing is known about a lost machine's apps
            tone = IDLE
        return AppView(app, word, tone)

    def snapshot(self, now: float) -> View:
        with self._lock:
            machines = []
            for m in self._m.values():
                heard = {a for (n, a) in list(self._beats) + list(self._rows) if n == m.name}
                if not m.external:                 # an app heard only through its data is drawn too
                    heard |= {x[2] for x in self._links if x[1] == m.name}
                    heard |= {x[4] for x in self._links if x[3] == m.name}
                apps = [a for a in m.run if a not in HIDDEN]
                apps += sorted(heard - set(apps) - HIDDEN)
                tone = {"up": OK, "data arriving": OK, "lost": BAD, "no data": BAD}.get(m.word, IDLE)
                machines.append(MachineView(m.name, m.layer, m.external, m.word, tone,
                                            tuple(self._app_view(m, a) for a in apps)))
            quiet = {m.name for m in self._m.values() if m.word not in ("up", "data arriving")}
            links = tuple(LinkView(t, self._colour[t], (wn, wa), (rn, ra), wn in quiet or rn in quiet)
                          for t, wn, wa, rn, ra in sorted(self._links))
            topics = tuple(sorted({(x.topic, x.colour) for x in links}, key=lambda tc: tc[1]))
            age = now - self._caption_at
            alpha = 1.0 if age < CAPTION_FOR - FADE else max(0.0, (CAPTION_FOR - age) / FADE)
            return View(tuple(machines), self._caption if alpha > 0 else "", alpha, links, topics)
