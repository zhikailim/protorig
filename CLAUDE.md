# CLAUDE.md - protorig

Visual, distributed RTI Connext DDS demos, POCs and test benches for customer pre-sales.
Ground rules: root `README.md`. Full design: `docs/WORKFLOW.md` (read the section for the area you change, see the map below).

## Goals (in priority order)

1. **Impact.** Every decision serves how strongly the demo proves Connext value to the audience. If a feature, app or beat does not make the demo more impactful, cut it.
2. **Simple.** Fewest apps, topics and beats that tell the story. One clear moment beats many small ones.
3. **Never fails on stage.** Not production quality, but it must run reliably every time: clean start, clean stop, no crashes, no hangs.

## Commands

Run from the repo root. Linux: `./protorig <verb>`. Windows: `.\protorig.ps1 <verb>` (or `protorig.cmd`).

| Command | Use |
|---|---|
| `./protorig check` | Static checks; must pass before any hand-over |
| `./protorig test [<app>] [-k <name>]` | Check, then tests (all or one app) |
| `./protorig test --all` | Adds scenario tests and full fuzzing (planned, not built yet) |
| `./protorig run <scenario> --sim` | Whole scenario on one machine, twins for external nodes |
| `./protorig run --app <app> [args]` | One app alone, for trying it out |
| `./protorig new app <name> --kind tooling\|sim` / `new scenario <name>` | Create from a template (`--kind vehicle` needs the C++ template, not built yet) |

`./protorig --help` marks verbs not built yet (today: `build`, `preflight`, `send`, `gen`, `deploy`).
DDS tests need `rti_license.dat` in `.local/` (git-ignored) or `RTI_LICENSE_FILE`; without it they skip. A skip is not a pass.

## Workflow and gates

One scenario = one demo, in `scenarios/<name>/`. Do not skip a gate. The reviewer approves at each gate.

| Stage | Output | Gate |
|---|---|---|
| 1 Conception | `scenarios/<name>/concept.md` (guide: `docs/CONCEPT.md`) | G0 |
| 2 Spec | `scenarios/<name>/README.md`: value, story, beats, setup, acceptance tests, talking points | G1 |
| 3 Data and QoS | IDL in `interfaces/`, Python mirrors, `qos/topics.xml` | G2 |
| 4 Apps | Per app: README behaviour table (given/when/then) first, then code and tests | G3 per app |
| 5 Scenario test | `scenarios/<name>/test_<name>.py` from the acceptance tests, fuzzed, `--sim` | - |
| 6 Rig | Preflight, live run, presenter dry run | G4 |

- Plan first and wait for approval, then do it. Exception: a change you can describe in one sentence (typo, rename, one-line fix) can be done directly; report it afterwards.
- Approved docs are the source of truth. Do not change them without asking.
- Spikes (stage 1 feasibility checks) need light approval only: state question, method, fallback, wait for "go".
- Requirements docs (`docs/requirements/*.md`) use numbered IDs with status; keep that style.
- Never commit or push without being asked.

## Self-check before every review

Before asking the reviewer to review anything, check it yourself and fix what you find:

- **Logic:** consistent? Missing steps? Wrong assumptions about Connext behaviour?
- **Traceability:** challenge -> beat -> proof point -> acceptance test -> test code line up.
- **Impact:** does each part make the demo more convincing? Remove what does not.
- **Simplicity:** can anything be removed without weakening the story?
- **Fits the repo:** naming conventions, folder rules, `./protorig check` passes.
- **Runs:** for code, `./protorig test` passes, with no DDS tests skipped.

Then present: what is ready for review, what you checked and fixed, open questions. Show evidence: the commands you ran and their key output, not just "tests pass". Short.

## Coding rules

- **QoS lives in `qos/` XML only.** Pro apps load it at runtime and contain no QoS code (readers and writers by topic name via `fw.App` / `fw::App`). Micro and Cert apps get it as generated C source (`protorig gen`, not built yet), never as hand-typed values.
- **A new topic needs all of:** IDL in `interfaces/`, Python mirror in `libs/py/fw/types.py`, entry in `libs/py/fw/topics.py`, entry in `qos/topics.xml`.
- **Topic names:** scenario data plain (`Alert`); framework plumbing `_sys/` (`_sys/NodeStatus`).
- **Generated code** only in `build/`, never edited. rtiddsgen generates types only, never `-example` (its `USER_QOS_PROFILES.xml` would silently override `qos/`).
- **`libs/`** only for code two or more apps need.
- **Every app** has a short `README.md` (arguments, topics in and out, behaviour table) and a `test_<app>.py`.
- **Tests are black-box over DDS:** listen before acting, `wait_for` instead of sleeps, and break the app on purpose to prove each test can fail.
- **Callbacks never kill the app:** log and continue. Close DDS in order: stop thread, detach condition from WaitSet, close reader, close participant.
- **Nothing machine-specific committed:** no IPs in code (only `scenario.yaml`), no paths (only `.local/`), never licenses.

## Reliability (the demo must not fail)

- Apps start in any order and survive others restarting (discovery, no startup order).
- Bad input or missing data never crashes an app: clamp, ignore, log.
- Timings and rates in `qos/` XML or app arguments, never hard-coded.
- Fuzz before calling anything done: edge values, bursts, silence, kill/restart, late join.
- Report results against the acceptance tests.

## Gotchas

- Changing an immutable QoS policy needs new DDS entities: the app must restart, it cannot switch live.
- XML comments in `qos/` may not contain `--`; check reports the file as invalid.
- The TC397 tracks at most 5 remote participants: more nodes and some never see it (`external/tc397/README.md`).
- Changing IDL used by an external node needs a reflash and `./protorig lock <name>`, or its data silently stops arriving.

## Where to read in `docs/WORKFLOW.md`

| Changing | Read section |
|---|---|
| `scenarios/` | `scenarios/` - what to run |
| `apps/`, language choice | `apps/` - the bricks |
| `interfaces/`, `external/` | `interfaces/`; `external/` (changing IDL of an external node) |
| `qos/` | `qos/` - how data behaves |
| Topic or folder names | Naming conventions |
| `libs/`, build | `libs/` - shared code; `build` |
| Tests | `tests/` - proving it works |

## Other rules (not in the README)

- Demos are visual. Native GUIs; web front ends only for control-panel functions.
- Vehicle code stays POSIX only, no Linux-specific APIs (QNX later).

## Writing style

- **Comments:** short. One line saying why, not what. Comment every key DDS/QoS decision.
- **READMEs:** short. What it does, how to run it. Under one screen where possible.
- **Docs and messages:** concise. Tables and bullets over prose.
