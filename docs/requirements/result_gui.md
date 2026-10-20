# result_gui: requirements

Status: FOR REVIEW (20 Oct 2026). Sources: `scenarios/temperature-skeleton/README.md`
(beats 1-5), node_agent N7 and N12, WORKFLOW `apps/`. Approved rows become the
behaviour table in `apps/tooling/result_gui/README.md`.

Purpose: the audience's main screen. It shows, from the back of a room, that
every machine is up, what the data is doing, and when something goes wrong.
It only listens: it publishes nothing but its own heartbeat.

## G1. One window, three areas  [for review]

- **Alert banner** across the top, **live plots** in the middle, **node board**
  down the left side.
- Readable at 5 m: dark background, large type, and every state shown by a
  word as well as a colour (never colour alone).
- F11 toggles full screen. Closing the window stops the app cleanly (exit 0,
  the agent shows STOPPED).

## G2. Node board  [for review]

- One card per node in `scenario.yaml`, in file order, shown from the start
  as grey "not seen yet", so a missing machine is visible, not absent.
- Managed machine, from its agent's heartbeat: **up** (green), **lost** (red,
  heartbeat lost for 3 s: cable out, machine off, agent crashed), or
  **stopped** (grey, agent stopped cleanly).
- Under it, one row per app from `_sys/AppState`: the state word and its
  detail (e.g. "KILLED  killed by windows/control_panel"). RUNNING green;
  STARTING, RESTARTING, STOPPING amber; CRASHED, KILLED red; the rest grey.
  A lost agent's rows show "unknown: agent lost" (N12 rule 1).
- An app running a QoS variant shows its name on its row (from its
  heartbeat), so beat 5's switch is visible.
- External node (e.g. tc397), from its data topics in `external.yaml`:
  **data arriving** (green), **no data for N s** (red, after `--silence`
  seconds, default 2), or "not seen yet". (Not by a QoS deadline: readers
  of the ECU's topics must not request one, or they'd never match it.)
- A heartbeat with no AppState row (an app started by hand): listed under
  its node as "unmanaged" (N12).
- A machine whose heartbeat send time is more than 1 s off its arrival
  time: "clock off by N s" on its card (N12).

## G3. Live plots  [for review]

- `--plot "<topic>.<field>"`, one per plot, e.g.
  `result_gui --plot 'Example Temperature.temperature'`. The only
  scenario-specific setting: nodes come from `scenario.yaml`, external
  topics from `external.yaml`.
- One line per instance (per key value, labelled); a rolling window of
  `--window` seconds (default 30) by arrival time; the latest value shown
  large.
- Silent for `--silence` seconds: "no data for N s" over the plot.
- An unknown topic or field: a clear message and exit 2 at start (the agent
  shows it as CRASHED with that message), never a blank plot.

## G4. Alert banner  [for review]

- Active alerts (alive `Alert` instances), most severe first, then newest:
  "CRITICAL  hpc-pi/hpc_monitor: Temperature high (14.7)". CRITICAL red,
  WARNING amber, INFO blue. At most 5 shown, then "+N more".
- Cleared (disposed): removed. None active: "No active alerts".
- An alert whose source vanished without clearing it: kept, greyed, "source
  lost", so a dead app's alert never silently looks cleared.

## G5. As an app  [for review]

- Starts in any order and late: rows and alerts are kept for late joiners,
  so a restarted GUI shows the current state at once.
- Bad or extreme data never crashes it or freezes the screen: non-finite
  values skipped, long text trimmed, unknown enum values shown as numbers,
  bursts drawn at most 20 times a second.
- QoS from `qos/` like any app; `--qos-variant` works (e.g.
  `Variant.Alert.BestEffort` switches its alert reader).

## G6. How it's built  [for review]

- Python, **PySide6 6.8.3 + pyqtgraph 0.14**, both pinned. Checked 20 Oct
  2026: 20 frames a second headless, screenshots work. The newest PySide6
  (6.12) crashes inside pyqtgraph, hence the pin.
- A small shared helper, `libs/py/fw/gui.py` (control_panel needs the same):
  fw.App's DDS loop on a background thread, Qt on the main thread, Ctrl-C
  and the agent's stop still work.
- Tests are black-box over DDS with the real window running headless: the
  test bus publishes heartbeats, rows, alerts and data; result_gui logs one
  line per visible change ("board hpc-pi: lost"), which the tests read. Each
  test saves a screenshot to `build/test-shots/` for review. Fuzzed:
  random contents, bursts, silence, publishers killed and restarted, late
  join.

## Decisions for you

| # | Question | Recommended | Alternative |
|---|---|---|---|
| D1 | GUI toolkit | PySide6 + pyqtgraph: modern look, smooth plots, testable headless | Tkinter + matplotlib: no install, but dated look and slower plots |
| D2 | Where the GUI packages are installed | Every machine, one `requirements.txt`: one install command everywhere (about 100 MB on the Pi) | Windows only: smaller Pi, but two package lists to keep |

## Open, not blocking

- Beat 5 as written ("toggle a QoS variant, behaviour changes on screen"):
  `Variant.Alert.BestEffort` only loses alerts when packets are lost, so on a
  clean switch nothing visibly changes. The board will show the variant;
  whether the beat needs packet loss to make it visible is a question for
  the Control Panel's requirements (roadmap row 6).

## Not included

Recording or history, acknowledging alerts (that would mean publishing),
machine CPU and memory (roadmap row 30), more than one window, a web page.
