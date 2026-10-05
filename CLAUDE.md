# CLAUDE.md - protorig

Visual, distributed RTI Connext DDS demos, POCs and test benches for customer pre-sales.
Design and commands: `docs/WORKFLOW.md`. Ground rules: root `README.md`. Follow both; this file adds how to work.

## Goals (in priority order)

1. **Impact.** Every decision serves how strongly the demo proves Connext value to the audience. If a feature, app or beat does not make the demo more impactful, cut it.
2. **Simple.** Fewest apps, topics and beats that tell the story. One clear moment beats many small ones.
3. **Never fails on stage.** Not production quality, but it must run reliably every time: clean start, clean stop, no crashes, no hangs.

## Workflow and gates

One scenario = one demo, in `scenarios/<name>/` (create with `./protorig new scenario <name>`).
Do not skip a gate. The reviewer approves at each gate.

| Stage | Output | Gate |
|---|---|---|
| 1 Conception | `scenarios/<name>/concept.md` (guide: `docs/CONCEPT.md`) | G0 |
| 2 Spec | `scenarios/<name>/README.md`: value, story, beats, setup, acceptance tests, talking points | G1 |
| 3 Data and QoS | IDL in `interfaces/`, Python mirrors, `qos/topics.xml` | G2 |
| 4 Apps | Per app: README behaviour table (given/when/then) first, then code and tests | G3 per app |
| 5 Scenario test | `scenarios/<name>/test_<name>.py` from the acceptance tests, fuzzed, `--sim` | - |
| 6 Rig | `./protorig preflight`, live run, presenter dry run | G4 |

- Approved docs are the source of truth. Do not change them without asking.
- Spikes (stage 1 feasibility checks) need light approval only: state question, method, fallback, wait for "go".
- Requirements docs (`docs/requirements/*.md`) use numbered IDs with status; keep that style.

## Self-check before every review

Before asking the reviewer to review anything, check it yourself and fix what you find:

- **Logic:** consistent? Missing steps? Wrong assumptions about Connext behaviour?
- **Traceability:** challenge -> beat -> proof point -> acceptance test -> test code line up.
- **Impact:** does each part make the demo more convincing? Remove what does not.
- **Simplicity:** can anything be removed without weakening the story?
- **Fits the repo:** naming conventions, folder rules, `./protorig check` passes.
- **Runs:** for code, `./protorig test` passes (and `--all` for scenarios).

Then present: what is ready for review, what you checked and fixed, open questions. Short.

Every step is reviewable: plan first, wait for approval, then do it. Never commit or push without being asked.

## Reliability (the demo must not fail)

- Apps start in any order and survive others restarting (discovery, no startup order).
- Bad input or missing data never crashes an app: clamp, ignore, log.
- No IPs or paths in code: IPs only in `scenario.yaml`, machine paths only in `.local/`.
- Timings, rates and QoS in `qos/` XML or app arguments, never hard-coded.
- Prove tests can fail (break the app on purpose), then fuzz: edge values, bursts, silence, kill/restart, late join.
- Report results against the acceptance tests.

## Tech rules (summary; README and WORKFLOW.md win on conflict)

- Connext 7.7 LTS for all new work. External nodes keep their own build (e.g. TC397 on Micro).
- Vehicle apps: C++17 Modern C++ API by default, or C when safety calls for it. CMake. POSIX only (QNX later). Never Python.
- Tooling and sim twins: Python.
- Demos are visual. Native GUIs; web front ends only for control-panel functions.

## Writing style

- **Comments:** short. One line saying why, not what. Comment every key DDS/QoS decision.
- **READMEs:** short. What it does, how to run it. Under one screen where possible.
- **Docs and messages:** concise. Tables and bullets over prose.
