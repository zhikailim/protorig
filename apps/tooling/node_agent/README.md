# node_agent

One per managed machine. Starts, stops and kills that machine's apps when asked
over DDS, and reports their state. Requirements: `docs/requirements/node_agent.md`
(N1-N14). Rig tooling (Python), never shipped in a vehicle.

Run (until `protorig agent` exists, step 3b):
`python main.py --scenario <scenario> --node <node>`

## Arguments

| Argument | Default | Meaning |
|---|---|---|
| `--scenario`, `--node` | required | which scenario, and which node of it this machine is |
| `--stop-grace` | 10 | seconds an app gets to stop politely before it is forced (N4, N6) |
| `--start-timeout` | 15 | seconds for a start or variant switch to report in (N12, N13 V4) |
| `--fail-window` | 5 | apps without a heartbeat: failing within this = failed start (N13 V4) |
| (standard) | | `--domain --verbose`, from fw.App |

## Topics

| Direction | Topic | Use |
|---|---|---|
| in | `_sys/DemoControl` | commands (N4) |
| in | `_sys/NodeStatus` | its apps' heartbeats (RUNNING, hang, variant) and other agents (N12 rule 2) |
| out | `_sys/NodeStatus` | its own heartbeat (N3) |
| out | `_sys/AppState` | one row per app, plus its own (N12) |
| out | `Alert` | source `<node>/node_agent` (N7) |

## Behaviour

"app" = an app in this node's `run:` list. "sender" = the command's writer, as `node/app`.

| # | Given | When | Then | Req |
|---|---|---|---|---|
| B1 | agent starts | | rows: each app NOT_RUNNING (UNAVAILABLE + reason if missing or not built), own row RUNNING; stale alerts of its apps cleared; no app started | N4, N5, N7, N12 |
| B2 | app NOT_RUNNING, STOPPED, KILLED or CRASHED | START app (or `*`) | STARTING, then RUNNING at its first heartbeat (or "running" line); its alerts cleared | N4, N12 |
| B3 | app STARTING, RUNNING or RESTARTING | START | ignored, logged "already running" | N4 |
| B4 | app UNAVAILABLE | START | refused, logged with the reason | N4 |
| B5 | app starting, running or restarting | STOP app (or `*`) | any switch cancelled; STOPPING, Ctrl-C; ends: STOPPED "stopped by <sender>"; not ended after stop-grace: forced, KILLED "forced after 10 s" | N4, N6 |
| B6 | app running, stopping or restarting | KILL app (or `*`) | forced at once (even mid-stop or mid-switch), KILLED "killed by <sender>"; CRITICAL `crash:<app>` "simulated crash, by <sender>" | N4, N7 |
| B7 | | a command with a `cmd_id` seen in the last 1,000 | ignored | N4 |
| B8 | | target node not this one; SET_PARAM | ignored silently | N4, N14 |
| B9 | | target `node_agent`, not in the list, or not a plain name | refused, logged with sender and reason | N4, N5 |
| B10 | sender is `<this node>/<app>` | STOP or KILL reaching it (named or `*`) | sender skipped, logged; the rest done | N11 |
| B11 | sender is `<this node>/<app>` | variant switch of the sender | refused | N11 |
| B12 | sender unknown (no name, not `node/app`, gone) | any command | acted on; logged as sender "unknown" | N11 |
| B13 | app running | it exits by itself with an error | CRASHED (exit code, last output line); CRITICAL `crash:<app>` with that line; not restarted; others carry on | N6, N7 |
| B14 | app running | it exits by itself with 0 | STOPPED "ended on its own (exit 0)"; WARNING `crash:<app>` | N7, N12 |
| B15 | app has heartbeated | silent past the heartbeat QoS's threshold, process alive | detail "not responding"; WARNING `hang:<app>`; both cleared when it recovers | N7, N12 |
| B16 | app STARTING | no heartbeat (or "running" line) within start-timeout | stays STARTING; WARNING `start:<app>`; cleared when RUNNING | N12 |
| B17 | app RUNNING | SET_QOS_VARIANT X | RESTARTING "switching to X, by <sender>"; polite stop; start with `--qos-variant X`; RUNNING when its heartbeat reports X within start-timeout; restarts +1 | N13 V3, V4 |
| B18 | | variant already in use / not in the QoS files / `*` / app not RUNNING | ignored / refused + WARNING `variant:<app>` / refused / refused | N13 V1, V2 |
| B19 | switch under way | new variant exits, hangs or reports another variant (no-heartbeat apps: exits with an error within fail-window) | started once more on the previous variant; WARNING `variant:<app>` "rolled back"; if that fails too: CRASHED, CRITICAL `crash:<app>`, no more tries | N13 V4 |
| B20 | app was switched, then stopped | START | starts as its `run:` entry is written | N13 V5 |
| B21 | | a row's detail is over 128 bytes (any language) | trimmed at a character boundary, ending "…"; the row is still published | N12 rule 5 |
| B22 | | another agent heartbeats for this node | the one with the higher participant ID stops its apps and exits; both raise CRITICAL `agents:<node>` "two agents for <node>" | N12 rule 2 |
| B23 | | scenario.yaml changes | logged "restart the agent to use it"; list unchanged | N5 |
| B24 | | any app prints | line shown as `<node>/<app> \| text`; agent's own as `<node>/node_agent`; license banner once | N8 |
| B25 | an app floods output | | the agent and the app never block; "N lines dropped" shown | N8 |
| B26 | | Ctrl-C (or a stop request) | every app stopped (grace, then forced); alerts cleared; rows and heartbeat disposed; exit 0 | N8 |
| B27 | | the agent is killed | its apps stop within about 1 s | N6 |

Not here: the `protorig agent` command (N1, step 3b); a display's "agent lost" (N12 rule 1, result_gui).
