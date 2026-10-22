"""
result_gui — the "under the hood" screen: the rig as it really is, where the
data goes, and what happens when something fails (docs/requirements/result_gui.md).

Topics in : (merge 2 onwards) _sys/NodeStatus, _sys/AppState, Alert, discovery
Topics out: none but its heartbeat
Arguments : standard only (--node --scenario --domain --qos-variant --verbose)

Merge 1 of 4: the window and its clean start and stop (R7). It shows the
scenario's name and waits; the rig is drawn from merge 2.

Run alone:  ./protorig run <scenario> --app result_gui
"""
import sys

from fw.app import App
from fw.gui import run_gui

app = App("result_gui", "The rig as it really is: machines, data links, failures, alerts")



def make_window():
    from window import Window        # imported here: Qt only once the app is set up (fw.gui)
    return Window(app.scenario)


sys.exit(run_gui(app, make_window))
