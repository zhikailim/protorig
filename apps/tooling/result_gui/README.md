# result_gui

The "under the hood" screen: the rig as it really is, where the data goes, and
what happens when something fails. Listens only. Requirements:
[docs/requirements/result_gui.md](../../../docs/requirements/result_gui.md) (R1-R7,
approved 22 Oct 2026). Built in four merges; this table grows with each.

Run: `./protorig run <scenario> --app result_gui` (no arguments of its own).
Needs PySide6 (`bootstrap/requirements.txt`).

| Topics in | Topics out |
|---|---|
| (from merge 2) `_sys/NodeStatus`, `_sys/AppState`, `Alert`, discovery | its heartbeat only |

## Behaviour (merge 1: the window, R7)

| # | Given | When | Then |
|---|---|---|---|
| G1 | result_gui starts | | a window titled "protorig · <scenario>" opens; log "window shown"; heartbeat as `<node>/result_gui` |
| G2 | it runs | Ctrl-C, or Ctrl-Break (the agent's stop on Windows) | stops within 1 s: heartbeat disposed, DDS closed, window closed, exit 0 |
| G3 | it runs under a launcher (the agent, `run`) | the launcher dies | stops by itself, as G2 (N6) |
| G4 | it runs | the window is closed | stops, as G2 (the agent then shows STOPPED) |
| G5 | it runs | the window freezes for 3 s or more | its heartbeat pauses, so the agent reports "not responding"; it resumes when the window does |
| G6 | it runs | F11, whichever part of the window has focus (Fn+F11 on laptops that reserve F11) | full screen on and off; the window makes itself active when it opens (checked by hand on Windows, 22 Oct 2026) |
| G7 | an unknown argument | | exit 2 with a clear message, as every app |
| G8 | PySide6 isn't installed | | exit 2: "needs PySide6 ... pip install -r bootstrap/requirements.txt" |
