"""result_gui's window (Qt widgets, main thread only). Merge 1: a placeholder
showing the scenario's name; the rig is drawn here from merge 2."""
from PySide6 import QtCore, QtGui, QtWidgets

BG, TEXT, DIM = "#0f1216", "#e8eaed", "#7d8590"


class Window(QtWidgets.QWidget):
    def __init__(self, scenario: str | None):
        super().__init__()
        self.setWindowTitle(f"protorig · {scenario or 'no scenario'}")
        self.resize(1600, 900)
        self.setStyleSheet(f"background:{BG};")
        title = QtWidgets.QLabel(scenario or "no scenario")
        title.setStyleSheet(f"color:{TEXT}; font-size:40px; font-weight:bold;")
        note = QtWidgets.QLabel("waiting for the rig")
        note.setStyleSheet(f"color:{DIM}; font-size:22px;")
        box = QtWidgets.QVBoxLayout(self)
        box.addStretch()
        for w in (title, note):
            w.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            box.addWidget(w)
        box.addStretch()
        # R1: full screen. Double-click works everywhere; F11 is a hardware key
        # on many laptops (Windows opens Settings unless Fn is held), so it's a
        # convenience only. Esc leaves. Window-wide shortcuts, not a key handler:
        # keys go to whichever widget has focus, and this window's labels take none.
        for key, slot in ((QtCore.Qt.Key.Key_F11, self.toggle_full_screen),
                          (QtCore.Qt.Key.Key_Escape, self.showNormal)):
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            sc.setContext(QtCore.Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)

    def mouseDoubleClickEvent(self, event):
        self.toggle_full_screen()

    def toggle_full_screen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def showEvent(self, event):
        # Started from a terminal, Windows may open it without focus (F11 would go
        # to the terminal): ask to be the active window.
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
