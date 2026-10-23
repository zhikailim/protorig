"""result_gui's window (Qt, main thread only): draws the model's snapshot ten
times a second, at whatever size the window is. Reads the model, owns no DDS."""
import time
import traceback

from PySide6 import QtCore, QtGui, QtWidgets

from layout import MIN_H, MIN_W, Rect, layout
from model import BAD, IDLE, OK, WARN, Model

from fw.app import log

BG, BAND, BOX, PILL, LINE = "#0f1216", "#151a21", "#161b22", "#1f2630", "#5b6470"
TEXT, DIM = "#e8eaed", "#7d8590"
TONE = {OK: "#3fb950", BAD: "#f85149", WARN: "#d29922", IDLE: "#7d8590"}
REDRAW_MS = 100


class Window(QtWidgets.QWidget):
    def __init__(self, scenario: str | None, model: Model | None = None, clock=time.monotonic):
        super().__init__()
        self.scenario = scenario or "no scenario"
        self.model = model or Model({}, 2.0)
        self.clock = clock
        self.last_layout = None                  # what was drawn last (tests read it)
        self._failed = None                      # the last drawing error logged
        self.setWindowTitle(f"protorig · {self.scenario}")
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(1600, 900)
        # R1: F11 toggles full screen. A window-wide shortcut, not a key handler,
        # so it works whichever part of the window has focus.
        full = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_F11), self)
        full.setContext(QtCore.Qt.ShortcutContext.WindowShortcut)
        full.activated.connect(self.toggle_full_screen)
        self._redraw = QtCore.QTimer(self)
        self._redraw.timeout.connect(self.update)
        self._redraw.start(REDRAW_MS)

    def toggle_full_screen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def showEvent(self, event):
        # Started from a terminal, Windows may open it without focus (F11 would go
        # to the terminal): ask to be the active window.
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ drawing

    def paintEvent(self, _event):
        # A drawing error must never take the window down (an exception escaping a
        # paint event with the painter open can crash Qt): log it once, carry on.
        p = QtGui.QPainter(self)
        try:
            self._paint(p)
        except Exception:
            err = traceback.format_exc()
            if err != self._failed:
                self._failed = err
                log("result_gui", "ERROR", f"drawing failed (the screen may be incomplete):\n{err}")
        finally:
            p.end()

    def _paint(self, p):
        view = self.model.snapshot(self.clock())
        lay = layout([(m.name, m.layer, len(m.apps), m.external) for m in view.machines],
                     self.width(), self.height())
        self.last_layout = lay
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(BG))
        self._text(p, lay.title, self.scenario, DIM, round(lay.label_px * 1.4), bold=True)
        if not view.machines:
            self._text(p, lay.window, "no machines: start it with  protorig run <scenario> --app result_gui",
                       DIM, lay.name_px, align=QtCore.Qt.AlignmentFlag.AlignCenter)
            return
        for layer, band in lay.bands.items():
            p.fillRect(QtCore.QRectF(band.x, band.y, band.w, band.h), QtGui.QColor(BAND))
            self._text(p, band, f"  {layer.upper()}", DIM, lay.label_px, bold=True,
                       align=QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        if lay.wan_y is not None:
            p.setPen(self._pen(LINE, 2, dashed=True))
            p.drawLine(QtCore.QPointF(lay.window.x, lay.wan_y), QtCore.QPointF(lay.window.right, lay.wan_y))
            above = Rect(lay.title.x, lay.wan_y - lay.label_px * 1.6, lay.label_px * 6, lay.label_px * 1.5)
            self._text(p, above, "  WAN", DIM, lay.label_px, bold=True)
        # Cables first, so the boxes sit on top of them. A broken link is dashed (R3).
        for m in view.machines:
            b = lay.boxes[m.name]
            p.setPen(self._pen(TONE[BAD] if m.tone == BAD else LINE, 3, dashed=m.tone == BAD))
            p.drawLine(QtCore.QPointF(*b.port), QtCore.QPointF(*b.plug))
        s = lay.switch
        self._box(p, s, PILL, LINE, 2)
        self._text(p, s, "switch", DIM, lay.word_px, align=QtCore.Qt.AlignmentFlag.AlignCenter)
        for m in view.machines:
            self._machine(p, m, lay.boxes[m.name], lay)
        if view.caption:
            colour = QtGui.QColor(TEXT)
            colour.setAlphaF(view.caption_alpha)
            self._text(p, lay.caption, view.caption, colour, round(lay.name_px * 1.1),
                       align=QtCore.Qt.AlignmentFlag.AlignCenter, bold=True)

    def _machine(self, p, m, b, lay):
        tone = TONE[m.tone]
        self._box(p, b.rect, BOX, tone, 3)
        self._text(p, b.name, m.name, TEXT, lay.name_px, bold=True)
        x, y, r = b.dot
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(tone))
        p.drawEllipse(QtCore.QPointF(x, y), r, r)
        word = QtCore.QRectF(b.state.x + 3 * r, b.state.y, b.state.w - 3 * r, b.state.h)
        self._text(p, word, m.word, tone, lay.word_px, bold=True)
        if m.word == "lost":
            p.setOpacity(0.45)                     # nothing is known about a lost machine's apps
        for i, pill in enumerate(b.pills):
            self._box(p, pill, PILL, PILL, 1)
            inner = QtCore.QRectF(pill.x + 8, pill.y, pill.w - 16, pill.h)
            if b.more and i == len(b.pills) - 1:
                self._text(p, inner, f"+{b.more} more", DIM, lay.pill_px)
                break
            a = m.apps[i]
            f = self._font(lay.pill_px, bold=True)
            # The state word matters more than the name: the name is shortened first.
            ww = min(QtGui.QFontMetricsF(f).horizontalAdvance(a.word) + 4, inner.width() * 0.7)
            self._text(p, QtCore.QRectF(inner.right() - ww, inner.y(), ww, inner.height()), a.word,
                       TONE[a.tone], lay.pill_px, bold=True,
                       align=QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            name = QtCore.QRectF(inner.x(), inner.y(), inner.width() - ww - 6, inner.height())
            self._text(p, name, a.name, TEXT, lay.pill_px)
        p.setOpacity(1.0)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _font(px: int, bold: bool = False) -> QtGui.QFont:
        f = QtGui.QFont()
        f.setPixelSize(max(1, int(px)))           # pixels, not points: scales with the window
        f.setBold(bold)
        return f

    def _text(self, p, rect, text, colour, px, bold=False,
              align=QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter):
        """Text that never spills out of its rectangle: shortened with "…" instead."""
        r = rect if isinstance(rect, QtCore.QRectF) else QtCore.QRectF(rect.x, rect.y, rect.w, rect.h)
        f = self._font(px, bold)
        p.setFont(f)
        p.setPen(QtGui.QColor(colour))
        short = QtGui.QFontMetricsF(f).elidedText(text, QtCore.Qt.TextElideMode.ElideRight, r.width())
        p.drawText(r, int(align), short)

    @staticmethod
    def _pen(colour, width, dashed=False):
        pen = QtGui.QPen(QtGui.QColor(colour), width)
        if dashed:
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        return pen

    def _box(self, p, r, fill, edge, width):
        p.setPen(self._pen(edge, width))
        p.setBrush(QtGui.QColor(fill))
        p.drawRoundedRect(QtCore.QRectF(r.x, r.y, r.w, r.h), 8, 8)
