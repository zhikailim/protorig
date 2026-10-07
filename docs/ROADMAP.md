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

Status: 7 Oct 2026.


## 1. Next, in build order

| # | Item | What it gives | State | Detail |
|---|---|---|---|---|
| 2 | `protorig agent start\|stop\|status` (step 3b) | One agent per machine, started and stopped by hand; status of the whole rig. Write the stop mechanism into N1 first; reuses the node detection | Ready | node_agent.md N1 |
| 3 | `run --sim` through agents; `run --live` (step 4) | Rehearsal and the real rig work the same way; one command brings the rig up | Approved | run.md U2, U11, U12; node_agent.md N2 |
| 4 | `check` rules (step 5) | Catches variants that would cut off default peers (V8), `node_agent` in `run:`, an app twice on a node, app names over 26 characters. Makes `Variant.Alert.BestEffort` reader-only; removes `node_agent` from temperature-skeleton | Approved | node_agent.md N2, N13 V8 |


## 2. The first demo (temperature-skeleton)

| # | Item | What it gives | State | Detail |
|---|---|---|---|---|
| 5 | `result_gui` | Node board, alerts, the demo's visual | Planned | scenario README; WORKFLOW `apps/` |
| 6 | `control_panel` | Start, stop, kill, variant and parameter buttons | Planned | node_agent.md N4, N13, N14 |
| 7 | `ivi_standin` | The IVI side of the story | Planned | scenario README |
| 8 | C++ layer: `fw::App`, `fw.cmake`, C++ app template, `new app --kind vehicle` | Vehicle apps in C++17 | Planned | WORKFLOW `libs/`, `templates/` |
| 9 | `hpc_monitor` (C++) | The HPC app on the VM and Pi | Planned | scenario README |
| 10 | `protorig build` | Builds the C++ apps on each machine | Planned | WORKFLOW `build` |
| 11 | `bootstrap` (`linux.sh`, `windows.ps1`) | One-command machine setup | Planned | WORKFLOW `bootstrap/` |
| 12 | `protorig preflight` | "Is every node up and discovered?" before going live | Planned | WORKFLOW CLI table |
| 13 | Hardware bring-up: VM, then TC397, then Pi | The demo on the real rig | Planned | QUICKSTART |
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
| 22 | Agents start at boot | No `agent start` on demo day | Planned | QUICKSTART stage 2 |


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


## Done

- 7 Oct 2026: `run <scenario>` finds this machine's node from its IP and checks its os/arch (U4, U5, U13; row 1).
- 7 Oct 2026: step 3a, the node agent (B1-B27).
- 5 Oct 2026: steps 1 and 2: fw.App obeys only parameters, launcher watch, `_sys/AppState`.
- Before: `check`, `list`, `new`, `test`, `lock`, `run` (`--app`, `--sim`, `--node`), `tc397_twin`, data contract, QoS, Python `fw`.
