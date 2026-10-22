"""
fw.gui — a native window (Qt, PySide6) for an fw.App: the screens on the
Windows PC (result_gui, control_panel). result_gui requirement R7.

    app = App("result_gui", "...")
    sys.exit(run_gui(app, lambda: MyWindow(app)))      # make_window returns a QWidget

Qt must own the main thread, so fw.App's DDS loop (app.run()) runs in a
background thread. What this guarantees:
  - Every way of stopping ends the same way: Ctrl-C, Ctrl-Break (how the
    node agent stops an app on Windows), the launcher going away (N6), or
    the window being closed -> app.stop() -> the DDS loop finishes (heartbeat
    disposed, DDS closed in order) -> Qt quits -> app.run()'s exit code.
  - Python runs signal handlers only when it gets control, and Qt's event loop
    is C++: a short timer hands Python control, so a Ctrl-C is seen in ~0.2 s.
  - A frozen window is not a running app: the heartbeat is sent only while the
    event loop is responsive (App.alive_if), so the agent reports it.
  - Widgets are main-thread only: DDS callbacks must not touch them. Keep
    received data in plain Python objects; the window reads them on its own
    QTimer.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Callable

from fw.app import App, log

TICK = 0.2      # seconds: how often Python gets control (signals) and the DDS loop is checked
FROZEN = 3.0    # seconds without a tick: the window is frozen, withhold the heartbeat
JOIN = 15.0     # seconds to wait for the DDS loop to finish after Qt has quit


def run_gui(app: App, make_window: Callable[[], object]) -> int:
    """Show make_window()'s widget and run until the app stops. Returns the exit code."""
    if app.help_requested:
        return app.run()                               # prints --help; no window, no DDS
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError as e:
        log(app.who, "ERROR", f"needs PySide6 ({e}). Install the tooling packages: "
                              "python -m pip install -r bootstrap/requirements.txt")
        app.stop("no GUI toolkit")
        app.run()                                      # closes DDS cleanly
        return 2

    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    qt.setQuitOnLastWindowClosed(False)                # closing = app.stop(); quit when DDS is done
    last_tick = [time.monotonic()]
    app.alive_if(lambda: time.monotonic() - last_tick[0] < FROZEN)

    code: list[int] = []
    loop = threading.Thread(target=lambda: code.append(app.run()), name="dds", daemon=True)

    class CloseToStop(QtCore.QObject):
        def eventFilter(self, obj, event):             # the user closed the window
            if event.type() == QtCore.QEvent.Type.Close:
                app.stop("window closed")
            return False

    def tick():
        last_tick[0] = time.monotonic()
        if not loop.is_alive():
            qt.quit()

    window = make_window()
    closer = CloseToStop()
    window.installEventFilter(closer)
    timer = QtCore.QTimer()
    timer.timeout.connect(tick)
    timer.start(int(TICK * 1000))
    loop.start()
    window.show()
    log(app.who, "INFO", "window shown")
    qt.exec()
    timer.stop()
    app.stop("window gone")                            # if Qt ended for any other reason
    loop.join(JOIN)
    if loop.is_alive():
        log(app.who, "ERROR", f"DDS loop didn't finish within {JOIN:g} s")
        return 1
    window.close()
    return code[0] if code else 1
