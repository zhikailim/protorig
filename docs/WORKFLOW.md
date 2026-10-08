# protorig — workflow and structure

> Status: design agreed on 28 Sep 2026. Folders are created as they are first needed; see "Build progress" in the root README.

## Working in VS Code

`protorig.code-workspace` shows the folders in workflow order with friendly names, without renaming anything on disk (numbered folder names were rejected: the numbers would leak into every path, and inserting a step would mean renumbering). The last entry is the whole repo, so git, terminal, CMake and Python tooling work normally.

## Connext version

**Connext 7.7 (LTS) for all development**: Professional on Windows, the VM and the Pi, and the `rti.connext` Python package (`~=7.7.0`, pinned in `bootstrap/requirements.txt`; `cli/repo.py` holds the same version and a test keeps them in step). `protorig check` warns when a machine has a different version installed. The TC397 keeps its Connext Micro 2.x build; Micro and Pro interoperate over standard RTPS.

## Design principle: when to add a check or tool

Add a check or a tool only when a mistake would be **silent and expensive** (e.g. a QoS profile that stops matching the TC397: no error, just no data, found late on hardware). Otherwise a sentence in the docs is enough. Example of what was deliberately not built: a `variants.yaml` recording how each QoS variant should be applied. The check's warning explains it in one line instead, and variant usage is decided when the Control Panel is built.

## Mental model

The repo has three layers:

| Layer | Folders | Question it answers |
|---|---|---|
| What to run | `scenarios/` | Which machines run which apps, for this demo? |
| What exists | `interfaces/`, `qos/`, `apps/`, `libs/`, `external/` | What data, behaviours and programs are available? |
| How it runs | `protorig` CLI + `cli/`, `bootstrap/`, `templates/`, `tests/`, `build/` | Machinery; set up once |

Apps are bricks; a scenario is the instruction sheet saying which bricks go on which machine.

---

## `scenarios/` — what to run

One folder per demo scenario:

```
scenarios/<name>/
├─ scenario.yaml     # what runs where
├─ README.md         # the story and how to present it
└─ apps/<kind>/<app> # optional: apps only this scenario needs (vehicle | tooling | sim, same rules as shared apps)
```

`scenario.yaml`:

```yaml
# the folder name (scenarios/temperature-skeleton) is the scenario's name
description: HPC failover with over-temperature alerts
domain: 0
nodes:
  windows: { ip: 172.23.100.102, os: windows, run: [result_gui, control_panel, ivi_standin] }
  hpc-vm:  { ip: 172.23.100.110, os: linux,   run: ["hpc_monitor --strength 20"] }
  hpc-pi:  { ip: 172.23.100.121, os: linux,   run: ["hpc_monitor --strength 10"] }
  tc397:   { ip: 172.23.100.101, external: true, sim: tc397_twin }
```

- `ip` is used to generate discovery peers, the single-interface restriction, and preflight pings.
- `run` entries are written like command lines, so the same app can run with different settings.
- `external: true` means nothing is deployed or started there; `sim:` names the twin used with `--sim`.
- App lookup order: `scenarios/<name>/apps/` first, then shared `apps/`.
- Not in the YAML on purpose: languages, topics, QoS details, paths.

`README.md` (created from a template by `./protorig new scenario <name>`):
1. Value proven
2. Story
3. Demo beats (click → what the audience sees → DDS feature responsible)
4. Setup (hardware, cabling, anything outside the repo)
5. Acceptance tests (measurable; these become automated tests)
6. Talking points

The README is written first and is the spec the apps are built from.

---

## `apps/` — the bricks

```
apps/vehicle/<app>/    # C++17 (default) or C (safety), CMake — anything that would run in a vehicle
apps/tooling/<app>/    # Python — GUIs, control panel, stand-ins
apps/sim/<twin>/       # Python — stand-ins for external nodes, never deployed
```

- No per-app config file. A folder with `CMakeLists.txt` is a C or C++ app (by its sources); a folder with `main.py` is a Python app.

### Language policy for vehicle apps
C++17 with the Connext Modern C++ API by default. C when safety requirements call for it: Connext Pro C API, Connext Micro, or Connext Cert (possibly with MISRA C). Never Python.

C support is a requirement, built when first needed (like cross-compiling):
| Piece | C++ (now) | C (when needed) |
|---|---|---|
| Template | `templates/app_cpp/` | `templates/app_c/`, same numbered recipe |
| Helpers | `libs/cpp/fw/` | `libs/c/fw/`: args, participant, heartbeat, clean shutdown in plain C |
| Build | `fw_app()`, rtiddsgen C++ | Same `fw_app()`, detects `.c` sources, rtiddsgen for C (or Micro) |
| Language check | `.cpp` allowed in `vehicle/` | `.c` also allowed; `.py` rejected |
| Tests | Black-box over DDS | Identical |

Known item: Micro and Cert are configured in code, not by loading QoS XML at runtime. For those apps, `./protorig gen` must generate QoS and peer settings as C source from the same `qos/` files and `scenario.yaml`, instead of `node_qos.xml`.
- Every app has a `README.md`: what it does, its arguments, topics in and out.

Shared apps used by most scenarios:
- `node_agent` (Python tooling, one per managed machine, never listed in `run:`): publishes `NodeStatus` every second and carries out `DemoControl` commands (start, stop or kill apps) on its own machine (node_agent N1-N14).
- `result_gui` (Python, Windows): the audience's main screen. Listens only: live plots, alert banner, node status board.
- `control_panel` (Python, Windows): publishes `DemoControl`.

Life of a brick:
1. `./protorig new app <name> --kind tooling|sim|vehicle` copies a template that already joins the domain, loads QoS, heartbeats and shuts down cleanly.
2. The logic and tests are written from a spec, and reviewed as a diff.
3. `./protorig check` enforces the rules (only C or C++ under `vehicle/`, known IDL types only).
4. `./protorig build`, then list the app in a scenario's `run:`.

---

## `interfaces/` — the data contract

```
interfaces/
├─ common/              # types we design; this repo is the source of truth
│  ├─ protorig.idl      # protorig::NodeStatus, protorig::DemoControl (framework, _sys/ topics)
│  └─ alerts.idl        # alerts::Alert
└─ external/
   └─ tc397/            # types that originated in another project
      ├─ Temperature.idl, Header.idl, Time.idl
      └─ README.md      # topic name, domain, field meanings
```

- Where a type goes depends on where it **originates**:
  - Types designed for our demos go in `common/`, even if an external node such as the TC397 uses them. The external project copies the IDL from here.
  - Types that came from another project go in `interfaces/external/<name>/`.
- C and C++: `./protorig build` runs rtiddsgen (via `fw_app()`) into `build/`; an app's `CMakeLists.txt` names the IDL it needs.
- Python: hand-written mirrors in `libs/py/`. A contract test compares them field by field against the IDL.

---

## `external/` — systems built outside this repo

```
external/<name>/
├─ README.md                  # how to build/flash it, topic, domain, IP, open questions
├─ external.yaml              # machine-readable: topics it publishes, type, and the QoS its writer offers
├─ known_good_*.xml, …        # reference settings and samples
└─ flashed.lock               # generated by `protorig lock`: which IDL the running firmware was built from
```

Rule: anything under `external/` belongs to another project. It is locked to what is actually running, not "never changes".

### Changing the IDL of an external node (e.g. TC397)

`flashed.lock` holds the SHA-256 fingerprint of every IDL file the flashed firmware was built from, plus the date. A fingerprint is a short code computed from a file's exact contents; any change, even one character, gives a different code. The lock file is always generated, never hand-edited.

1. Edit the IDL (in `common/` or `interfaces/external/<name>/`).
2. `./protorig check` now warns: *"<file> changed since <name> was flashed — regenerate and reflash, then run `./protorig lock <name>`"*.
3. Regenerate the types in the external project (e.g. ETAS) from the repo's IDL, rebuild and flash.
4. Run `./protorig lock <name>` to record the new fingerprints. The warning disappears.

`protorig lock <name>` locks every `.idl` in `interfaces/external/<name>/`, plus anything locked before, plus `--add <file>` (e.g. common IDL the node also uses). `--dry-run` shows what would be locked. Check also reports: a locked file that was deleted (ERROR), and a new IDL file for the node that isn't locked yet (WARN).

Why: a mismatch between the repo and the firmware makes data silently stop arriving. The lock turns that into a visible warning.

---

## `qos/` — how data behaves

```
qos/
├─ base.xml       # participant defaults shared by every node (no IPs)
├─ topics.xml     # behaviour per topic, assigned by topic name (topic_filter)
└─ variants.xml   # alternative behaviours the Control Panel can switch to
```

Libraries: `protorig_base::Base` (base.xml), `protorig::Topics` (topics.xml, the default profile), `protorig_variants::Variant.*` (variants.xml, each based on `protorig::Topics`).

Generated by `./protorig run` (never edited): `build/<scenario>/<node>/node_qos.xml` holds that node's discovery settings (see *Running* below), in one profile, `protorig_node::Participant`, based on `protorig_base::Base`. `run` passes its path to each app in `PROTORIG_NODE_QOS`. Connext does not support including one QoS file from another, so apps load a **list**: `base.xml;topics.xml;variants.xml;node_qos.xml` (fw::App sets this; nothing auto-loads `USER_QOS_PROFILES.xml`).

Verified against real Connext: each topic gets the intended QoS in every profile, and variants inherit the topics they don't override.

- Apps contain no QoS code. They create readers and writers with defaults and get the right behaviour for that topic from the XML. Changing behaviour means editing XML and restarting; nothing is rebuilt.
- Choosing a variant from the Control Panel makes `node_agent` restart the app with that variant, because some QoS settings cannot change on a live entity.
- `./protorig check` asks Connext for the effective QoS of every profile and applies the DDS request/offer rules:
  - **ERROR** if any profile's readers of an external topic would not match what the external node offers (`external/<name>/external.yaml`). For the TC397 (reliable, volatile, no deadline, no liveliness lease), readers must not request a finite deadline, a finite liveliness lease, a stronger durability, or different ownership.
  - **ERROR** if the default writer for an external topic doesn't mirror the external node's offer, so sim twins behave like the hardware.
  - **ERROR** if our own topics' writer and reader don't match within a profile.
  - **WARN** if a variant only works when applied to every node at once (e.g. `Variant.Alert.BestEffort`: its best-effort writer can't feed default reliable readers).
  - A test checks the checker: for several reader settings, its verdict must equal whether real Connext matches on the wire.
- XML comments may not contain `--`; check reports invalid XML.

---

## Naming conventions

These show up on screen in Admin Console, in front of customers, so they are part of the demo.

### Topic names
| Kind | Rule | Examples |
|---|---|---|
| Scenario data (what the demo is about) | Plain, meaningful names, no prefix | `Alert`, `CabinTemperature`, `BrakeCommand` |
| Framework plumbing (behind the scenes) | `_sys/` prefix; the underscore sorts them away from scenario topics | `_sys/NodeStatus`, `_sys/DemoControl` |
| External systems | Whatever the external system uses; cannot change without reflashing | `Example Temperature` (TC397) |

- In DDS a topic name is a plain string; `/` has no special meaning.
- A topic name (the channel) is separate from a type name (the data's shape, from the IDL module, e.g. `protorig::NodeStatus`). DDS matches them independently.
- Framework types live in IDL module `protorig` and travel only on `_sys/` topics; `protorig check` enforces both directions. (Module `sys` was avoided: it would shadow Python's `sys` if types were ever generated for Python.)
- The list of known topics and their types is `libs/py/fw/topics.py`; check cross-checks it against the IDL, `qos/topics.xml` and `external/*/external.yaml`.
- Held in reserve, not the default: moving plumbing to its own domain so customers see only scenario data. The cost is a second participant in `node_agent`, the Control Panel and the Result GUI.

### Folders and files
| Term | Means |
|---|---|
| scenario | One demo you present: `scenarios/<name>/` (not `demos/`, which suggested code) |
| app | A program, in `apps/` (or `scenarios/<name>/apps/` if only one scenario needs it) |
| external | Anything built outside this repo, e.g. the TC397 (not "ECU": the Pi is not an ECU) |
| node | One machine in a scenario (`windows`, `hpc-vm`, `hpc-pi`, `tc397`) |

---

## The `protorig` CLI

```
protorig/
├─ protorig    # launcher for Linux / VM / Pi (bash)
├─ protorig.ps1    # launcher for Windows (PowerShell)
├─ protorig.cmd    # launcher for Windows cmd.exe, and fallback if PowerShell blocks scripts
└─ cli/        # the real program (Python), one file per verb
```

Launchers find the repo's Python environment and the Connext license, then hand over to `cli/main.py`, so every verb behaves the same on every OS. Run from the repo root: `./protorig <verb>` (Linux) or `.\protorig.ps1 <verb>` (Windows). Bootstrap also sets up the short alias `prz` for daily typing (not `pr`, which is a standard Linux command). Docs always use the full name.

| Command | What it does |
|---|---|
| `./protorig list` | Lists scenarios with their one-line descriptions |
| `./protorig check <scenario>` | Validates YAML, language policy, data contract, and flash locks |
| `./protorig build [<app>]` | Generates types with rtiddsgen and builds the C/C++ apps for this machine |
| `./protorig run <scenario> --sim` | Whole scenario on one machine, with twins for external nodes; nothing reaches the network |
| `./protorig run <scenario> [--node <node>]` | This machine's part of the rig: the node found from this machine's IP (run.md U13), or named with `--node` |
| `./protorig run <scenario> --live [--partial]` | The whole rig through its agents, from any machine in the scenario: waits for every agent, starts everything, shows each app's state; Ctrl-C stops every app, the agents stay up (run.md U11, U12) |
| `./protorig agent start\|stop\|status <scenario>` | Start (`--background` to keep it after the terminal closes) or stop this machine's node agent; `status` shows every node's agent and apps (node_agent N1, N1a) |
| `./protorig preflight <scenario>` | Every real node up and discovered? |
| `./protorig new app\|scenario <name>` | Create from a template (`--kind`, `--desc`, `--scenario`) |
| `./protorig lock <external> [--add IDL] [--dry-run]` | Record the IDL fingerprints of what was just flashed |
| `./protorig run [--scenario S] [--domain N] --app <app> [its arguments]` | Just one app on this machine, for trying it out; everything after the app's name goes to the app |
| `./protorig run ... --dry-run` | Show what would start (commands, discovery settings); start nothing |
| `./protorig send <Topic> '<json>'` | Publish one sample from the command line |
| `./protorig test [<app>] [--all] [-k <name>]` | Run automated tests (all, one app's, or matching a name); `--all` adds scenario tests and full fuzzing |
| `./protorig test --helpers` | List every test helper with a one-liner and example |
| `./protorig build --target <t>` | Cross-compile for another target (deferred) |
| `./protorig deploy <node>` | Push built binaries and configs to a node over SSH (deferred) |

### Running (`protorig run`, requirements U1–U10)

The numbered requirements (U1–U13, with status) are in [requirements/run.md](requirements/run.md); this is the summary.

| Mode | Starts | Discovery settings it generates |
|---|---|---|
| `--sim` | every node's apps on this machine, plus each external node's `sim:` twin; each app gets its node's name (`--node hpc-pi`) | shared memory only: nothing reaches the network, so a sim run on domain 0 can't mix with the real rig; Admin Console on the same PC still sees it |
| `<scenario>` alone, or `--node <node>` | this machine's node's apps: the one node whose IP this machine has (U13), or the one named; refuses unless this machine has the node's IP, os and arch (starting anyway would silently discover nothing) | every other node's IP as a unicast peer (`N@`, N = its apps + 1, at least 4), DDS bound to this node's own IP (the known-good profile's lesson), no multicast |
| `--app <app>` | one app, node name `local` | none: Connext defaults |

- Apps that don't exist yet, or C/C++ apps not built yet, are listed and skipped: build and run incrementally.
- Every app is run by `fw.supervise.Supervised` (the same code the tests use) and stops by itself if `run` dies; a crash is reported loudly and the others keep running, with no automatic restart; `run` exits 1 if any app failed. QoS-variant switches are the node agent's job (node_agent N13), not `run`'s.
- One console: each line prefixed with `node/app`; Connext's license banner shown once.
- Each app runs in its own process group, so Ctrl-C reaches only `run`, which stops every app politely (SIGINT, or Ctrl-Break on Windows) and forces any that haven't stopped after 10 s. If `run` itself is killed outright, its apps keep running until the S7 fix is built (with `node_agent`); after that they stop themselves within about 1 s.

---

## `build/`

All generated output: rtiddsgen code, per-node QoS, binaries. Git-ignored. Safe to delete any time; `./protorig run` and `./protorig build` recreate it.

---

## `libs/` — shared code

```
libs/cpp/fw/   # C++17, POSIX only: app.hpp (startup, participant, shutdown), heartbeat, log, fw.cmake
libs/py/fw/    # Python: types.py (IDL mirrors), app.py, widgets/ (live plot, alert banner, node board)
```
- Goes here only when two or more apps need it. Used in place by apps, never copied, no version numbers.
- `fw::App` handles standard args (`--node --scenario --verbose`), loads only the generated `node_qos.xml`, names the participant `<node>/<app>`, starts the heartbeat, and shuts down cleanly on Ctrl-C or a kill command.
- It is a shortcut, never a wall: `app.participant()` and the readers/writers are real Connext objects.

### How an app is assembled

| Piece | Source | Automatic? | Edited by hand? |
|---|---|---|---|
| Data types | rtiddsgen from `interfaces/*.idl`, into `build/` | Every build | Never |
| Helpers | `libs/cpp/fw/`, via the include path | Every build | Only to improve the shared library |
| `main.cpp` | Our template (`./protorig new app`), not rtiddsgen's example | Once | Yes: the app's logic |

An app's `CMakeLists.txt` is one call:
```cmake
fw_app(speed_controller SOURCES src/main.cpp IDL common/vehicle.idl)
```

### Guards against clashing with rtiddsgen output
| Risk | Guard |
|---|---|
| Two IDL files with the same file name → one generated header overwrites the other | `./protorig check`: IDL file names unique repo-wide |
| Same type defined twice → duplicate symbols | `./protorig check`: fully-qualified type names unique |
| An IDL module named `fw` | `./protorig check`: `fw` is reserved |
| A stray `USER_QOS_PROFILES.xml` auto-loaded by Connext | `fw::App` ignores auto-loaded files; the generated file is named `node_qos.xml` |

rtiddsgen is never run with `-example`; only type code is generated.

---

## `templates/` — starter kits

Built: `app_py/` (used for both tooling apps and sim twins: they're the same Python skeleton; `--kind` only decides the folder) and `scenario/`. Planned: `app_cpp/` (with the C++ step). An `external/` kit waits until a second external node exists (YAGNI).

`./protorig new app <name> [--kind tooling|sim|vehicle] [--desc "..."] [--scenario <s>]` and `./protorig new scenario <name> [--desc "..."]` copy a kit and replace `{{name}}` and `{{description}}` in file names and contents. Nothing else.

- Templates are copied once, so they stay thin; evolving code lives in `libs/`.
- Every template works immediately (joins the domain, heartbeats).
- The test suite creates, builds and runs a throwaway item from each template.
- The C++ template's `main.cpp` is a numbered recipe: args → readers/writers → `on_data` → `every` → `run`.

---

## `build`

`./protorig build` is plain CMake. A root `CMakeLists.txt` loads `fw.cmake`, finds Connext via `NDDSHOME`, and includes every folder under `apps/vehicle/` and `scenarios/*/apps/` that has a `CMakeLists.txt`.
- Incremental; rtiddsgen reruns only when an IDL file changes.
- Output per platform: `build/linux-x64/`, `build/linux-arm64/`, `build/windows-x64/`.
- `./protorig build <app>` builds one app; `--target qnx-aarch64` cross-compiles later.
- Works with VS Code's CMake extension.

## Example: a new app from nothing

1. Define the data in `interfaces/common/*.idl`.
2. Add Python mirrors (`./protorig check` fails until they match).
3. Add QoS entries in `qos/topics.xml` (`./protorig check` warns if missing).
4. `./protorig new app <name> --kind vehicle`.
5. Fill in `CMakeLists.txt` (IDL line) and `main.cpp` (logic).
6. `./protorig build`.
7. Try it alone: `./protorig run --app <name>`, watch with `rtiddsspy`, poke it with `./protorig send <Topic> '<json>'`.
8. Automated test next to the app, including fuzzing: `./protorig test <name>`.
9. Add it to a scenario's `run:` and `./protorig run <scenario> --sim`.
10. On hardware: `git pull`, `./protorig build`, `./protorig run <scenario>` (the same on every machine).

---

## Getting software onto nodes

Default: every OS-level node (Windows, VM, Linux Pi) has its own clone and builds natively: `git pull && ./protorig build`. For editing on a target, use VS Code Remote-SSH.

When a target can't build for itself (QNX, no arm64 host tools, customer machines), build on a development host and push down:
```
./protorig build --target qnx-aarch64      # on the development host (e.g. the VM)
./protorig deploy <node>                   # copies binaries + node_qos.xml + scenario files over SSH, never source
```

### Cross-compilation support (requirement now; implementation deferred)
The scaffolding must not block cross-compiling on a development host and deploying to a target. The hooks:

| Piece | Where | Portable? |
|---|---|---|
| Toolchain definitions (compiler, flags, Connext target arch), paths only as variables | `libs/cpp/cmake/toolchains/<target>.cmake` | Yes, in the repo |
| Where toolchains are installed on this machine (env scripts, sysroots, compiler prefixes) | `.local/machine.yaml` (git-ignored; bootstrap fills in what it detects) | No, per machine |
| Which target a node needs | Optional `arch:` per node in `scenario.yaml` (default x86_64); `os` + `arch` pick the target | Yes |
| Build output never collides between targets | `build/<target>/` | Yes |

Status:
- Built now: per-target build folders; `fw_app` and `libs/cpp` are POSIX-only with no host-specific paths; `.local/machine.yaml` format; the optional `arch:` field.
- Deferred: the toolchain files themselves, `--target`, and `./protorig deploy`.

---

## `third_party/` — external source we build but never edit

Different from `external/` (systems built outside this repo). Pinned as git submodules. Not created yet: added with the first entry.
- Rule: wrap, don't modify. Upgrading is a submodule bump.
- First entry: RTI Perftest (v4.3, EPL-1.0).

## Planned bricks

Priority and state of all planned work, these bricks and the deferred items below included: [ROADMAP.md](ROADMAP.md).

- **`perf_runner` + GUI widgets:** runs RTI Perftest across nodes with presets (`raw-udp`, `dds-udp`, `dds-shm`, `zero-copy`) and N-to-M plans from a scenario's `perf.yaml`: assigns publisher/subscriber IDs and counts, `-latencyTest` only on publisher 0, sweeps sizes, merges CSV. Guards: participants-per-host limit in the peer settings; `dds-shm` rejected across hosts. Perftest has no topic-name option, so parallel groups use separate domains. Measures the ceiling.
- **`loadgen`:** our own C++ brick for realistic load: many topics per process, each with its own rate, size and QoS, from a scenario's `traffic.yaml`; per-topic latency and loss. Only as realistic as its profile, so prefer customer data (architecture docs or recorded traffic).
- **In-app latency probe in `fw`:** timestamps in samples, histograms, round-trips where possible so no clock sync is needed.

## Deferred on purpose (decided, not built)

Each can be added later without changing anything already built (most are a new topic).

- **Machine CPU and memory** (`_sys/MachineStatus`, keyed by node, published by `node_agent` once a second): would show under each machine on the node board. Needs the `psutil` package, which doesn't support QNX. Deferred for simplicity (29 Sep 2026).
- **Forwarding app output** (`_sys/AppLog`, one sample per line, best effort, recent lines only): lets Windows show what a Pi app prints, with no SSH. `node_agent` already captures every line (N8), so this is one extra publish. Deferred (30 Sep 2026). Structured results such as Perftest's go on their own topic from the app itself (see `perf_runner` above).
- **File transfer between nodes:** not planned; use a shared folder or `scp`.
- **Scenario QoS file** (`scenarios/<name>/qos.xml`, loaded after the repo's QoS): lets a scenario tune topic QoS and provide a participant profile that the generated discovery profile builds on (UDP buffer sizes, message size, zero-copy), so performance demos aren't limited to the shared defaults. Trigger: the first performance demo (e.g. video streaming). Deferred (30 Sep 2026).
- **Firewall automation** (`protorig firewall`, also run by bootstrap step 6): on Windows, one inbound rule by port (UDP, the scenario domain's range, e.g. 7400-7649 for domain 0) from `LocalSubnet` on all profiles, so it covers Python, C++ apps and RTI tools and needs no change at a new venue; removes block rules left when someone clicked Cancel on Windows' "allow access" prompt (a block rule beats any allow rule); `--remove` undoes it. On Linux, prints the `ufw`/`firewalld` line if one is active. Plus a no-admin check in `agent status` and `run --live` when a node is missing, naming the cause (rule missing, block rule, IT policy ignoring local rules). Why it matters: a blocked Windows PC is silent and one-way (the Pi sees Windows, Windows sees nothing). Until then: a manual step (the same rule by hand, or every firewall profile off). Trigger: a firewall problem at a demo, or a hand-over to someone new to the rig. Deferred (7 Oct 2026).
- **Configurable "lost / hang" threshold** (default 3 s), via the scenario QoS file: demos that saturate the network on purpose raise it so dropped best-effort heartbeats don't raise false alarms. The agent's hang check already reads the threshold from the heartbeat topic's QoS, so this is configuration, not code. Trigger: the first saturation demo. Deferred (30 Sep 2026).

## Scope
Built for demos and POCs, prototype up to pilot. Not production: no security, `node_agent` remote kill is a demo convenience, `fw::App` trades control for speed. Production concerns (security, safety, deployment) can be added later as separate layers.
- Open decision: a neutral CLI name (e.g. `./rig`) instead of `./protorig`, so it reads naturally for POCs and test benches.

---

## Where this is enforced
- The naming section is linked from the root `README.md` and `interfaces/README.md`.
- `./protorig check` enforces the topic-name rule: plumbing apps may only use `_sys/` topics, and scenario topics may not start with `_`.

## `bootstrap/` — one-time machine setup

`bootstrap/linux.sh` (VM, Pi) and `bootstrap/windows.ps1`, plus pinned `requirements.txt`. Safe to re-run; doubles as a health check.

From scratch: `git clone` → `./bootstrap/linux.sh` (or `.\bootstrap\windows.ps1`) → `./protorig check` → `./protorig run <scenario> --sim`.

1. Checks tools (Python 3.10+, CMake, compiler, git); prints install commands, installs only with consent.
2. Creates `.venv/` in the repo with the pinned Python packages; nothing system-wide.
3. Finds Connext (`NDDSHOME` or usual locations); checks rtiddsgen and this platform's libraries.
4. Finds the license, proves it by creating a participant, warns 30 days before expiry.
5. Identifies which scenario node this machine is, by IP.
6. Windows: opens the firewall for DDS ports on the subnet (asks for admin).
7. Runs `./protorig check`.

Rule: nothing machine-specific in the repo. Discoveries go to git-ignored `.local/` (`machine.yaml`), read by the launchers.

To verify: whether the Connext 7.7 bundle includes rtiddsgen for an arm64 Linux host. If not, Pi binaries are cross-built on the VM and deployed.

## `tests/` — proving it works

Tests live next to what they test; `./protorig test` runs them all:
```
tests/                              # the framework (templates, libs, CLI, data contract)
apps/<kind>/<app>/test_<app>.py     # each app on its own (+ conftest.py for its fixtures)
scenarios/<name>/test_<name>.py     # each scenario, from its README's acceptance tests
```

| Level | Proves | Where it runs |
|---|---|---|
| 1. Static checks (`./protorig check`) | Rules: YAML, language policy, naming, IDL ↔ Python match, unique IDL names, flash locks, QoS compatible with external nodes | Anywhere, seconds, no DDS |
| 2. Framework tests | Templates create/build/heartbeat; generated `node_qos.xml` correct; `fw` helpers | Anywhere |
| 3. App tests | The app does what its header comment promises | Anywhere |
| 4. Scenario tests | README acceptance criteria, with injected faults, in `--sim` | Anywhere |
| 5. On the rig | The same scenario tests with `--live`, after `./protorig preflight` | Your hardware |

`./protorig test` = levels 1–3 (~1 min, run before every hand-over). `./protorig test --all` adds levels 4 and full fuzzing.

### How a test works
- pytest is the runner (discovery, fixtures, readable asserts, isolation, parametrise, selection). It knows nothing about DDS.
- `libs/py/fw/testing.py` is the DDS toolbox, called from tests.
- The pytest process itself joins DDS (`bus` fixture, isolated domain, never the live one). The `app` fixture starts the **built** app as a separate process, whatever its language (C or C++ binary, or Python script). The test talks to it only over DDS: black-box.
- So the same tests work for any language, and later against real hardware, including external nodes like the TC397.
- Pattern: **listen before acting** (`listen` → `send` → `wait_for` → `assert`); otherwise samples written before the reader exists are missed, and the test passes or fails for the wrong reason (e.g. only because of a history QoS).

### What's built (Python)
- `libs/py/fw/app.py` (`fw.App`): standard args; domain from `--domain` or the scenario; QoS loaded as the file list, `--qos-variant` selects a `Variant.*` profile; participant `<node>/<app>`; `reader(topic)` / `writer(topic)` by name (types from `fw/topics.py`, QoS from `qos/`); `on_data` (`with_info=True` also passes Connext's SampleInfo, e.g. to identify the sender), `every` (returns a Timer whose `.period` can change live), `on_stop` (runs before DDS closes), `run`; 1 Hz heartbeat (`heartbeat=False` for sim twins of external nodes that send none), disposed on a clean stop; `participant_qos=fn` adjusts the participant QoS before creation; logs the liveliness lease in effect at start-up; obeys only `CMD_SET_PARAM` addressed to it (with `on_param` callbacks; non-finite values rejected); sim twins opt in to stop and kill with `obeys=` (named exactly, never `*`); start, stop, kill and variant switches are the node agent's job (see *Who does what with commands* below); stops by itself if its launcher dies. Logs incompatible-QoS events with the policy (e.g. `Durability`). Errors in callbacks are logged, never fatal. `--help` lists app arguments too; unknown arguments exit 2.
- `libs/py/fw/scenario.py`: a node's `run:` entries and where each app is, shared by `protorig run` and `node_agent` so both start the same thing.
- `libs/py/fw/agentfiles.py`: the `agent.pid` / `agent.stop` / `agent.log` files through which `protorig agent stop` reaches an agent on the same machine; process start times, so a reused process ID is never stopped or killed (N1a).
- `libs/py/fw/supervise.py`: `Supervised` runs one app process for a launcher (own process group, output lines, polite or forced stop, launcher ID for the app to watch), so the test harness, `protorig run` and `node_agent` behave identically; plus `with_variant` and `process_alive`.
- `libs/py/fw/testing.py`: `Bus` (isolated test domain 60-99, below the OS's temporary-port range; repo QoS), `send`, `listen` / `Mailbox`, `collect`, `command`, `make_sample`, `wait_for`, `AppLauncher` / `RunningApp` (`start_app(name, *args, node=, folder=, supervised=, env=)`; `supervised=False` = started by hand, no launcher ID; waits for the first heartbeat, or with `wait_heartbeat=False` for the `running` log line; `.output`, `.wait_exit`, `.interrupt`, `.terminate`).
- Root `conftest.py`: fixtures `bus` (session) and `start_app` (stops every app it started). Skips with the reason when Connext or a license is missing.
- `./protorig test [app ...] [-k expr]`: runs check, then pytest on `tests/`, `apps/`, `scenarios/` (or the named apps). Non-zero if either fails.

#### Who does what with commands (node_agent N4, N13, N14)

The rule: **the node agent owns the process, the app owns its behaviour.**

| Command | Handled by |
|---|---|
| start, stop, kill | the node agent of that machine |
| switch QoS variant | the node agent: polite stop, then start again with `--qos-variant` (rollback once if the new variant doesn't report in within 15 s) |
| set a parameter | the app itself, live |

- By default `fw.App` obeys only `CMD_SET_PARAM`. Anything else naming the app is ignored with one log line saying who handles it; commands for other apps are ignored silently.
- A sim twin has no agent (like the hardware it stands in for), so it opts in to stop and kill: `App(..., obeys={CMD_STOP_APP, CMD_KILL_APP})`. Those are obeyed only when its node is named exactly, never `*`.
- Every launcher passes its process ID in `PROTORIG_LAUNCHER_PID`; `fw.App` stops cleanly within about 1 s once that process is gone, so a restarted launcher never finds duplicates. Apps started by hand have no launcher ID.
- A clean stop disposes the app's heartbeat ("stopped on purpose"); a kill or a crash doesn't, so readers see "lost".

(Replaced on 5 Oct 2026: the earlier "note protocol" S1–S9, in which the app asked its launcher for a restart with exit code 75 and a note file.)

Every heartbeat carries the variant actually in effect, so the Control Panel shows what happened, not what it asked for.

### Helpers (`fw/testing.py`)
| Group | Helpers |
|---|---|
| Setup (fixtures) | `bus`, `app`, `scenario` |
| Data | `send(topic, {...})` (waits for a match first; rejects misspelt fields), `listen(topic)` → `.all() .last() .count()`, `collect(topic, seconds)` |
| Waiting | `wait_for(condition, timeout)`: never fixed sleeps where avoidable |
| Observing DDS | `matched(topic)`, `discovered()`, `deadline_missed(topic)`, `liveliness_lost(app)` |
| Control and faults | `kill(app)`, `restart(app)`, `use_variant(app, name)`, `network(loss=, delay_ms=)` (Linux only) |
| Measuring | `rate(topic)`, `latency(from_topic, to_topic)` |
| Fuzzing | `edge_values(Type)`: hostile values for every field, derived from the IDL |

Tests never change QoS on an app's own entities; `use_variant` restarts the app with a variant, the same path as the Control Panel. The test's own readers/writers can override QoS to test deliberate mismatches.

Implementation note from the prototype: closing order matters (stop thread → detach condition from WaitSet → close reader → close participant), or Connext refuses to close.

### Writing a test for a new app
1. The template already created `test_<app>.py` with 3 standard tests: starts and heartbeats; clean shutdown on Ctrl-C and kill command; survives garbage input.
2. Write the behaviours as a **given / when / then** table from the spec. **This is what gets reviewed.**
3. One test per row, using the helpers.
4. Fuzz cases: input (edge values), timing (bursts, silence), lifecycle (kill/restart, late join).
5. `./protorig test <app>`. On failure, ask: is the app wrong, or the table?
6. Prove the tests can fail: break the app deliberately, one change at a time; each break must fail a test (later possibly `./protorig test --mutate`).
7. Commit the tests with the app.

Optional: C/C++ unit tests (e.g. GoogleTest for C++) next to an app, only for tricky internal logic; `./protorig test` runs them too.

### Discovering what to call
1. The test template is a recipe (commented examples of the common patterns).
2. `docs/TESTING.md`: a cookbook by intent ("check a rate", "check failover"…), starting with the four pytest ideas needed: test functions, `assert`, fixtures, `parametrize`.
3. `./protorig test --helpers`: every helper with a one-liner and example, generated from docstrings.
4. VS Code autocompletion and hover docs, with the repo's `.venv` selected.
5. Existing tests as examples.

---
