# Roadmap: everything planned, in priority order

The one list of work still to do. One line per item; the detail lives where
"Detail" points.

- **Priority** = row order. Move a row up or down to change it. The `#` is
  a fixed id, so "row 7" keeps meaning the same item.
- **Work on the next one:** take the top row whose state isn't "Waits".
- **Done:** move the row to "Done" at the bottom, with the date.
- **New idea:** add a row here first; write its detail later.

States: **Ready** (approved, can be built now) · **Approved** (requirements
approved, waits for an earlier row) · **Planned** (designed, requirements not
written yet) · **Waits** (deferred until its trigger happens) · **Decide**
(an open decision, usually at bring-up).

Status: 30 Oct 2026.


## 1. Next, in build order

Empty: the framework steps 1-5 are all built (20 Oct 2026). Next is the first
demo: write the requirements for row 5 (`result_gui`), then build it.


## 2. The first demo (temperature-skeleton)

| # | Item | What it gives | State | Detail |
|---|---|---|---|---|
| 5 | `result_gui` (base phase) | The rig as it really is: machines, data links, failures, alerts | Done 30 Oct 2026 (base phase, R1-R7); improvements next, one at a time, after real use | docs/requirements/result_gui.md |
| 6 | `control_panel` | Start, stop, kill, variant and parameter buttons | Planned | node_agent.md N4, N13, N14 |
| 7 | `ivi_standin` | The IVI side of the story | Planned | scenario README |
| 8 | C++ layer: `fw::App`, `fw.cmake`, C++ app template, `new app --kind vehicle` | Vehicle apps in C++17 | Planned | WORKFLOW `libs/`, `templates/` |
| 9 | `hpc_monitor` (C++) | The HPC app on the VM and Pi | Planned | scenario README |
| 10 | `protorig build` | Builds the C++ apps on each machine | Planned | WORKFLOW `build` |
| 11 | `bootstrap` (`linux.sh`, `windows.ps1`) | One-command machine setup | Planned | WORKFLOW `bootstrap/` |
| 12 | `protorig preflight` | "Is every node up and discovered?" before going live | Planned | WORKFLOW CLI table |
| 13 | Hardware bring-up: VM, then TC397, then Pi | The demo on the real rig | VM done 30 Oct 2026 (two machines live, cable-pull beat); TC397 and Pi next | QUICKSTART |
| 14 | TC397 tracks at most 5 remote participants (rig has about 10) | Without a fix, some apps never see the ECU's data | Decide | `external/tc397/README.md`; design doc risk 1 |
| 15 | TC397 announces itself only to Windows | The VM and Pi may not discover it | Decide | design doc risk 2 |
| 16 | ECU unknowns: liveliness lease, `source_timestamp`, task period | The twin and displays depend on them | Decide | design doc risk 8 |
| 17 | C++ build route on the Pi (ARM) | Connext host package on the Pi, or build on the VM | Decide | WORKFLOW `bootstrap/`; design doc risk 9 |
| 18 | Clock sync across machines | Correct "changed 3 s ago" and latency figures | Decide | node_agent.md N12 decision B; design doc risk 3 |


## 3. Framework, later

| # | Item | What it gives | State | Detail |
|---|---|---|---|---|
| 19 | `test --all` | Scenario tests and full fuzzing in one command | Planned | CLAUDE.md commands |
| 20 | `protorig send` | Publish one sample from the command line | Planned | WORKFLOW CLI table |
| 21 | `protorig gen` | QoS as generated C for Micro and Cert apps | Planned | WORKFLOW `qos/` |


## 4. Waiting for a trigger

| # | Item | What it gives | Trigger | Detail |
|---|---|---|---|---|
| 23 | Firewall automation (`protorig firewall`) | No silent one-way discovery on a new network | A firewall problem at a demo, or a hand-over | WORKFLOW "Deferred on purpose" |
| 24 | Generated Python types (rtiddsgen) instead of hand-written mirrors | No hand-kept mirrors for large customer IDL | Customer IDL arrives, or rtiddsgen 7.7 on every machine | design doc risk 10 |
| 25 | Scenario QoS file | Per-demo QoS tuning (buffers, zero-copy) | The first performance demo | WORKFLOW "Deferred on purpose" |
| 26 | Configurable lost/hang threshold | No false alarms when a demo saturates the network | The first saturation demo | WORKFLOW "Deferred on purpose" |
| 27 | `perf_runner` + GUI widgets (RTI Perftest) | Throughput and latency ceiling across nodes | The first performance demo | WORKFLOW "Planned bricks" |
| 28 | `loadgen` | Realistic multi-topic load from a customer profile | A customer traffic profile | WORKFLOW "Planned bricks" |
| 29 | In-app latency probe in `fw` | Latency histograms inside demo apps | A latency demo | WORKFLOW "Planned bricks" |
| 30 | Machine CPU and memory on the node board | Load per machine | Needed by a demo (no QNX support in `psutil`) | WORKFLOW "Deferred on purpose" |
| 31 | Forwarding app output (`_sys/AppLog`) | See a Pi app's output on Windows, no SSH | Needed during a demo | WORKFLOW "Deferred on purpose" |
| 32 | Cross-compilation: toolchain files, `build --target`, `deploy` | Build on a host, deploy to targets (QNX later) | A target that can't build for itself | WORKFLOW "Cross-compilation support" |
| 37 | `result_gui` tells stand-ins from real machines: a heartbeat or device data from an IP other than the node's `ip:` shows "up · simulated" | No stand-in quietly covers for a missing machine; a wrong `ip:` shows up too | After the first real demo, if `--sim` screens confused anyone | Decided 26 Oct 2026; detection by the participant's announced address |
| 38 | An app that exits 0 on its own (e.g. a window closed) raises a WARNING not named `crash:` | The console stops calling a closed window a crash | If the `crash:` name confuses anyone at a demo | Deferred 26 Oct 2026; changes node_agent N7 |
| 39 | `run <scenario> --app X` takes this machine's node from its IP (else "local") | An app tried alone shows in its own machine box on result_gui | If trying apps alone on rig machines gets confusing | Deferred 26 Oct 2026; changes run U1 |
| 22 | Agents start at boot | No walking to each machine on demo day; with 33 and 35, demo day is one command | After the first rig bring-up (row 13), if starting agents by hand hurts | QUICKSTART stage 2 |
| 33 | `run <scenario>` brings the rig up (today's `--live`); drop the no-agent mode | The everyday command is the shortest; one way to bring the rig up | After the first rig bring-up (row 13), if `--live` feels cumbersome | Usability audit 20 Oct 2026; run.md U4, U11 |
| 34 | Remove `--partial` and `--start-timeout` (`run` keeps waiting and names the missing agent) | Two fewer switches | After the first rig bring-up (row 13) | Usability audit 20 Oct 2026; run.md U11, B8-B10 |
| 35 | `agent start` in the background by default (`--foreground` to debug) | Demo day is the common case | After the first rig bring-up (row 13) | Usability audit 20 Oct 2026; node_agent.md N1a |
| 36 | One home per doc topic: QUICKSTART for running, WORKFLOW for building; the design doc links instead of restating; the adas-failover example moved out of QUICKSTART | Less to read; docs stop drifting apart | Before handing the repo to anyone else | Usability audit 20 Oct 2026 |


## Done

- 30 Oct 2026: `result_gui` base phase (row 5): machines in layers, links Connext would match, lit while data flows, alert banner, captions; heartbeats carry writer rates (R5, overhead measured: ~0.004 % CPU). VM bring-up: Windows and VM live, cable pulled for 10 s and 44 s, back at once both times.
- 26-27 Oct 2026: `run` says once that the rest keeps running (B14); "did you mean" for app names.
- 20 Oct 2026: `check` rules (step 5, row 4): `node_agent` in `run:`, an app twice on a node, app names over 26 characters, variants that would stop data flowing (V8; `Variant.Alert.BestEffort` now reader-only); undefined names in Python.
- 20 Oct 2026: robustness review of `--sim` (B11 widened, B13 approved), a simplify pass, reporting and "simple to use" rules in CLAUDE.md; `run <scenario> --app X` replaces `run --scenario <scenario> --app X`.
- 16 Oct 2026: `run --sim` through agents (step 4b, row 3): one agent per node on this PC, the same start/watch/stop as `--live`.
- 8 Oct 2026: `run <scenario> --live` (step 4a): the whole rig up through its agents with one command (U11, U12); agents skip `node_agent` in `run:` (B31), removed from temperature-skeleton.
- 8 Oct 2026: `protorig agent start|stop|status` (step 3b, row 2): background start, stop through a PID-checked file, status of every node (N1, N1a).
- 7 Oct 2026: `run <scenario>` finds this machine's node from its IP and checks its os/arch (U4, U5, U13; row 1).
- 7 Oct 2026: step 3a, the node agent (B1-B27).
- 5 Oct 2026: steps 1 and 2: fw.App obeys only parameters, launcher watch, `_sys/AppState`.
- Before: `check`, `list`, `new`, `test`, `lock`, `run` (`--app`, `--sim`, `--node`), `tc397_twin`, data contract, QoS, Python `fw`.
