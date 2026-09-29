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
./bootstrap/linux.sh              # Windows: .\bootstrap\windows.ps1  (or protorig.cmd if scripts are blocked)
./protorig check
./protorig run temperature-skeleton --sim
```

`prz` is the short alias for `protorig`, set up by bootstrap.

## Working in VS Code

Open `protorig.code-workspace` (File → Open Workspace from File) to see the folders in workflow order: scenarios → interfaces → qos → apps → libs → external → templates → tests, with the whole repo last. GitHub's web view always sorts alphabetically; the table above is the guide there.

When you open the workspace, VS Code offers to install the recommended extensions (Python, C/C++, CMake, Remote-SSH, YAML, and two for diagrams). To see a README with its diagrams, open the preview beside the text with `Ctrl+K V`: `mermaid` blocks need the Markdown Preview Mermaid Support extension; `.drawio.svg` files are edited with Draw.io Integration and show in the preview like any image.

## Documentation

- [docs/WORKFLOW.md](docs/WORKFLOW.md): the full design, commands and workflows.
- [Naming conventions](docs/WORKFLOW.md#naming-conventions): topic names, folder terms. Read this before adding topics or folders.

## Ground rules

- **Connext 7.7 (LTS)** for all development, on every machine (see [docs/WORKFLOW.md](docs/WORKFLOW.md#connext-version)).
- **Vehicle code** (anything that would run in a vehicle, drone or device) is C++17 with the Connext Modern C++ API by default, or C (Connext Pro C API, Micro, or Cert) when safety requirements call for it. **Tooling** (GUIs, control panel, sim twins, tests) may be Python. Never Python in vehicle code.
- **Generated files live only in `build/`** and are never edited.
- **Nothing machine-specific in the repo:** paths and licenses go in the git-ignored `.local/`.
- **Everything is tested,** including deliberate breakage, before it's handed over.

## Build progress

- [x] Design agreed ([docs/WORKFLOW.md](docs/WORKFLOW.md))
- [x] Repo skeleton
- [x] `./protorig check`: static checks (scenarios, apps, language policy, repo hygiene); `./protorig list`
- [x] First scenario defined: `temperature-skeleton` (its apps are not built yet, so `check` lists them as errors: that is the to-do list)
- [x] Data contract (`interfaces/`), Python types (`libs/py/fw`), QoS (`qos/`), flash locks (`./protorig lock`), with checks verified against real Connext
- [x] Python side of `libs/` (`fw.app`, `fw.testing`), Python app and scenario templates, `./protorig new` and `./protorig test`
- [ ] C++ side of `libs/` (`fw::App`, `fw.cmake`) and the C++ app template
- [x] `tc397_twin`: the TC397's temperature ramp, requirements R1–R9 from the ECU source
- [ ] Shared apps: `node_agent`, `result_gui`, `control_panel`
- [ ] `bootstrap/`, `gen`, `build`, `run`, `preflight`
- [ ] Hardware bring-up: VM → TC397 → Pi
