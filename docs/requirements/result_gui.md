# result_gui: requirements (base phase)

Status: R1-R7 APPROVED 22 Oct 2026, with D1 and D2 as recommended. Replaces
the first draft of 20 Oct (a node board, never approved). Agreed on 22 Oct: a physical view of the rig as the
base; the rest added one increment at a time, after trying the real screen.
Approved rows become the behaviour table in `apps/tooling/result_gui/README.md`.

Purpose: the "under the hood" screen. It shows the audience the rig as it
really is, where the data goes, and what happens when something fails. It
only listens. The story screen is `ivi_standin`; the presenter's tool is
`control_panel`. Mockups: [running, with an alert](result_gui/base_1_running.png),
[a machine lost](result_gui/base_2_lost.png).

## R1. What is on screen  [approved]

- The machines from `scenario.yaml`, with the demo's apps inside each, in
  three layers: **cloud** on top, **compute** in the middle (HPCs, the
  Windows PC), **edge** at the bottom (ECUs), the switch between compute and
  edge. Each layer is a faint band labelled at the left; a dashed line
  marked "WAN" separates the cloud. The layer comes from an optional
  `layer:` key per node (`cloud`, `compute` or `edge`); without it, a
  managed machine is `compute` and an `external: true` device is `edge`.
  Only cloud nodes need the key. `check` rejects an unknown layer. The presenter tools (`node_agent`, `result_gui`,
  `control_panel`) are never drawn.
- A machine shows **up**, **lost** or "not seen yet"; an external device
  (e.g. tc397) shows **data arriving** or **no data**.
- Nothing else by default: no side panes, no QoS text, no event log. One
  alert banner while an alert is active; one short caption when something
  happens. Readable at 5 m: dark background, large type, every state shown
  by a word as well as a colour. F11 full screen.
- No configuration: in a `run:` list, `result_gui` needs no arguments.

## R2. Links  [approved]

- One link per writer and reader that would match: same topic and
  compatible QoS, worked out from Connext's discovery data with the rules
  `check` already uses.
- Drawn along the physical path: out through the machine's cable, the
  switch, into the other machine (UDP). Between two apps on the same
  machine, a short link inside the box (shared memory).
- Coloured by topic. **Lit** while data flows, **dim and dashed** when it
  doesn't.
- Apps are named by their participant name (`node/app`). A device whose
  name we don't control (the real TC397) is placed by its IP address.

## R3. Failures and captions  [approved]

- Lost comes from heartbeats (3 s) and, for external devices, data silence
  (`--silence`, default 2 s), never from discovery, which takes 10 s or more.
- A lost machine: red, its cable dashed, its links dim. Caption: "hpc-pi
  lost after 3.0 s · everything else carries on". Back: "hpc-pi back after
  7 s, no restarts".
- An app that crashes or is killed (from `_sys/AppState`): its box turns red
  with the state word; caption "hpc_monitor on hpc-pi crashed".
- Captions fade after 5 s; the newest replaces the previous one.

## R4. Alert banner  [approved]

- Active alerts across the top, most severe first: "CRITICAL  Temperature
  high: 14.7 °C (hpc-vm, hpc-pi)". At most 3, then "+N more". Gone when
  cleared. An alert whose source vanished stays, greyed, "source lost".

## R5. Data rates, for "lit"  [approved]

- Every app's heartbeat (`_sys/NodeStatus`) gains the sample rate of each of
  its writers, read once a second from Connext's own counters: no counting
  per sample. A bounded list (16 writers), so it stays Micro-compatible.
- External devices send no heartbeat: `result_gui` subscribes to their
  topics from `external.yaml` (small, e.g. 10 Hz temperature) to see data
  arriving. It subscribes to nothing else, so it never pulls large data
  (an OTA transfer, say) onto the network a second time.

## R6. Never disturbs the demo  [approved]

1. Nothing in the data path: reporting reads counters once a second.
2. The screen can never slow an app: nothing it reads makes a writer wait.
   A frozen or crashed `result_gui` changes nothing for the demo.
3. Reporting never crashes an app: failures are logged and skipped.
4. Measured: a test compares an app's CPU and its data latency with
   reporting on and off (target: under 1 % CPU, no measurable latency);
   `result_gui`'s own CPU is measured too.

## R7. How it's built  [approved]

- Python, PySide6 6.8.3 and pyqtgraph 0.14, pinned (the newest PySide6
  crashes with pyqtgraph; checked 20 Oct).
- `libs/py/fw/gui.py`, shared with `control_panel`: Qt on the main thread,
  fw.App's DDS loop in the background, Ctrl-C and the agent's stop still work.
- One model, fed by DDS in one place; drawing pieces only read it, have no
  DDS entities and no threads. Each later increment is one new piece.
- Tests: black-box over DDS with the real window running headless; one log
  line per visible change ("machine hpc-pi: lost"); a screenshot per test
  for review; fuzzed (bursts, silence, kills, late join); plus R6's test.

## Decisions (22 Oct 2026)

- D1: PySide6 (pyqtgraph only when the live-plot increment needs it).
- D2: GUI packages on every machine, one `requirements.txt`. If PySide6
  doesn't install on the Pi, fall back to Windows only.

## Build order (risk first, one merge each, checked on Windows)

1. `fw/gui.py` and an empty window that starts and stops cleanly (R7).
2. Machines, layers and states (R1, R3).
3. Links, with a test that they agree with what Connext matches (R2).
4. Heartbeat rates and lit/dim, alert banner, overhead test (R4, R5, R6).

## Later, one at a time, by value

1. Events: deadline missed, QoS mismatch, samples lost (apps report them on a
   new `_sys/` topic; the link flashes, with a caption).
2. Dots travelling at the real rate.
3. QoS visible on a link (reliable solid, best effort dashed), and a caption
   when a variant switch changes it.
4. Ownership changed (opt-in: the one feature that checks every sample).
5. A countdown ring as a machine's liveliness runs out.
6. Network saturation (cables glow with load).
7. Per-demo panels (e.g. OTA progress).
8. Live plot of a field.
9. A databus view, switched to when explaining decoupling.

## Checked 22 Oct 2026

Connext's Python API gives every writer and reader with topic, type and full
QoS, participant names, and each participant's UDP and shared-memory
addresses (the UDP one carries its IP). A clean stop is seen in 0.25 s; a
killed process only after the participant lease (10 s with our QoS), hence
R3. The real TC397 is confirmed at bring-up.
