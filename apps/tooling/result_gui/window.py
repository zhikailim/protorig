"""result_gui's window (Qt widgets, main thread only). Merge 1: a placeholder
showing the scenario's name; the rig is drawn here from merge 2."""
from PySide6 import QtCore, QtWidgets

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

    def keyPressEvent(self, event):                  # R1: F11 toggles full screen
        if event.key() == QtCore.Qt.Key.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        else:
            super().keyPressEvent(event)
