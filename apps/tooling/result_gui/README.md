# result_gui

The "under the hood" screen: the rig as it really is, where the data goes, and
what happens when something fails. Listens only. Requirements:
[docs/requirements/result_gui.md](../../../docs/requirements/result_gui.md) (R1-R7,
approved 22 Oct 2026). Built in four merges; this table grows with each.

Run: `./protorig run <scenario> --app result_gui`. Needs PySide6 (`bootstrap/requirements.txt`).
Optional: `--silence S` (default 2): seconds without data before an external device shows "no data".

| Topics in | Topics out |
|---|---|
| `_sys/NodeStatus`, `_sys/AppState`, each external device's topics (`external/<node>/external.yaml`), Connext's discovery data; later `Alert` | its heartbeat only |

Code: `main.py` reads DDS, `links.py` works out which writer reaches which
reader, `model.py` decides what to show (no DDS, no Qt), `layout.py` places it
for the window size, `window.py` draws it.

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

## Behaviour (merge 2: machines, layers, states, R1 and R3)

Every visible change is also one log line, e.g. `machine hpc-pi: lost`, `caption: ...`.

| # | Given | When | Then |
|---|---|---|---|
| G9 | a scenario | result_gui starts | each node drawn as a machine in its layer: cloud on top (a dashed WAN line under it), compute, the switch, edge at the bottom; a cable from each machine to the switch (cloud: to the WAN line); inside each machine its apps from `run:` plus any it hears, never `node_agent`, `result_gui`, `control_panel` |
| G10 | a machine | any of its heartbeats arrive | "up", green |
| G11 | a machine | nothing heard from it yet | "not seen yet", grey |
| G12 | a machine is up | all its heartbeats stop (killed, paused, cable pulled) | after about 3 s: "lost", red, cable dashed red, its apps greyed; caption "hpc-pi lost after 3.2 s · everything else carries on" (the second part only if another machine is up) |
| G13 | a machine is lost | its heartbeats return | "up"; caption "hpc-pi back after 7 s, no restarts", or "apps restarted" if any heartbeat now comes from a new process |
| G14 | the agent reports an app (`_sys/AppState`) | the state changes | the app shows the word (running, starting, crashed, killed, not responding...), coloured; crashed or killed adds the caption "hpc_monitor on hpc-pi crashed" / "was killed" (not for a state found at start-up) |
| G15 | an external device (e.g. tc397) | its topics' data arrive / stop for `--silence` s | "data arriving" green / "no data" red with caption "tc397: no data for 2.1 s · everything else carries on"; on return "tc397: data back after 9 s" |
| G16 | a machine | all its heartbeats are disposed (clean stop) | "stopped", grey, no caption |
| G17 | a caption | 5 s pass, or a newer one comes | it fades out over its last second / is replaced |
| G18 | any window size from 800x450 (the minimum) up | | nothing overlaps or leaves the window; long names shortened with "…" (the app's name before its state word); apps that don't fit end with "+N more" |
| G19 | anything goes wrong while drawing | | logged once ("drawing failed"), the window stays up |
| G20 | `--scenario` names no readable scenario | | exit 2 with the reason |

## Behaviour (merge 3: links, R2)

| # | Given | When | Then |
|---|---|---|---|
| G21 | a writer and a reader that Connext matches (same topic and type, a shared partition, compatible QoS: the rules `check` uses) | | one link from the writer's app to the reader's app, in its topic's colour, an arrow where it enters the reader; the topics and colours listed top right; log "link Alert: hpc-vm/hpc_monitor -> windows/ivi_standin" |
| G22 | both apps on one machine | | a short bracket beside the apps, inside the box (shared memory) |
| G23 | apps on two machines | | along the cables: out of the writer's machine, through the switch, into the reader's (UDP); each topic in its own lane |
| G24 | QoS, partition or type that don't match | | no link (tested against what Connext itself matched) |
| G25 | `_sys/` topics, presenter tools, participants outside the scenario | | never drawn |
| G26 | an end's machine is lost, stopped or silent | | its links dim and dashed; when Connext forgets the writer or reader, the link goes (log "link gone ...") |
| G27 | a device whose participant name we don't control (the real TC397) | | placed on the external node whose `ip:` is its address; links start or end at the device's box |
