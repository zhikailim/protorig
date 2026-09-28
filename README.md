# protorig

A framework and workflow for building **visual, distributed RTI Connext DDS demos, POCs and test benches** quickly, across mixed hardware: Windows, Linux VMs, Raspberry Pis, and external nodes such as automotive ECUs.

Apps are reusable bricks. A **scenario** is a short instruction sheet saying which bricks run on which machine.

## The three layers

| Layer | Folders | Question it answers |
|---|---|---|
| What to run | `scenarios/` | Which machines run which apps, for this scenario? |
| What exists | `interfaces/`, `qos/`, `apps/`, `libs/`, `external/` | What data, behaviours and programs are available? |
| How it runs | `protorig` CLI + `cli/`, `bootstrap/`, `templates/`, `tests/` | Machinery; set up once |

## Quick start (once built)

```bash
git clone https://github.com/zhikailim/protorig && cd protorig
./bootstrap/linux.sh              # Windows: .\bootstrap\windows.ps1
./protorig check
./protorig run tire-skeleton --sim
```

`prz` is the short alias for `protorig`, set up by bootstrap.

## Documentation

- [docs/WORKFLOW.md](docs/WORKFLOW.md): the full design, commands and workflows.
- [Naming conventions](docs/WORKFLOW.md#naming-conventions): topic names, folder terms. Read this before adding topics or folders.

## Ground rules

- **Vehicle code** (anything that would run in a vehicle, drone or device) is C++17 with the Connext Modern C++ API, or C with Connext Micro. **Tooling** (GUIs, control panel, sim twins, tests) may be Python.
- **Generated files live only in `build/`** and are never edited.
- **Nothing machine-specific in the repo:** paths and licenses go in the git-ignored `.local/`.
- **Everything is tested,** including deliberate breakage, before it's handed over.

## Build progress

- [x] Design agreed ([docs/WORKFLOW.md](docs/WORKFLOW.md))
- [x] Repo skeleton
- [ ] `./protorig check`: static checks
- [ ] Data contract (`interfaces/`) and QoS (`qos/`)
- [ ] `libs/` (`fw`) and `templates/`
- [ ] Shared apps: `node_agent`, `result_gui`, `control_panel`, `tc397_twin`
- [ ] First scenario: `tire-skeleton`
- [ ] `bootstrap/`, `gen`, `build`, `run`, `preflight`
- [ ] Hardware bring-up: VM → TC397 → Pi
