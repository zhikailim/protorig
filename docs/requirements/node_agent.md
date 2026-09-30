# node_agent and run --live: requirements

Status: UNDER REVIEW (30 Sep 2026). Nothing here is built yet.
- N1-N3, N5-N8: approved in detail.
- N4: REVISED and approved 30 Sep 2026 (the agent owns the process, the app
  owns its behaviour).
- N9-N10: moved to run.md as U11-U12.
- N11-N12: approved in outline, detail review pending.
- N13 (variant switching, replaces the note protocol S1-S9) and N14 (what an
  app obeys): new, under review.
The bring-up flow these requirements produce is described in
[../QUICKSTART.md](../QUICKSTART.md).

Purpose: one small agent on each managed machine starts, stops and kills that
machine's apps when asked over DDS, and reports their state, so the whole rig
can be brought up and controlled from one machine.


## N1. One agent per managed machine  [approved]

- Managed nodes only (not `external: true`): today Windows, VM, Pi.
- Commands, run on that machine:

      protorig agent start  <scenario> --node <node> [--background] [--domain N]
      protorig agent stop   <scenario> --node <node>
      protorig agent status <scenario> --node <node>

- `start` checks: scenario readable, node exists and is managed, this machine
  has the node's IP (wrong-machine guard), no agent for this node already heard
  on DDS (listens about 3 s; a duplicate refuses). Then it writes the node's
  discovery settings (as `run --node`) and runs the agent.
- After starting, the agent waits for commands; it starts no apps by itself.
- Foreground by default (Ctrl-C stops the agent and its apps). `--background`:
  the command returns at once; output goes to `build/<scenario>/<node>/agent.log`.
- `stop`: stops a background agent politely with its apps and waits until it
  has gone; works on Windows via a stop-request file.
- `status`: listens about 3 s for heartbeats from that node; prints the agent's
  state and its running apps; exit 0 if alive, 1 if not. Works from any rig
  machine (no wrong-machine guard; uses this machine's own settings).
- One scenario per agent; scenario's domain unless `--domain` is given.
- Code: `apps/tooling/node_agent/` (Python on fw.App: rig tooling, never
  shipped in a vehicle); participant `<node>/node_agent`.

## N2. The agent is part of the framework, not the story  [approved]

- `run:` lists name only demo apps; every managed node gets an agent
  automatically. Remove `node_agent` from `temperature-skeleton`.
- `check`: error if `node_agent` appears in a `run:` list.
- Node board shows every managed machine through its agent, even with no apps.
- `run --node` (no agent) and `run --app` unchanged.
- `run --sim` also starts one agent per managed node (named after the node),
  starts external nodes' twins directly, then tells every agent "start *";
  Ctrl-C stops everything, agents included. Everything stays on this machine.

## N3. Heartbeat  [approved]

- Standard fw.App heartbeat on `_sys/NodeStatus`, 1 Hz, app `node_agent`,
  `qos_variant` empty.
- Silence for 3 s = lost (existing deadline and liveliness QoS).
- Every app (not just the agent) disposes its heartbeat instance on a clean
  stop, so readers see "stopped on purpose" (disposed), distinct from "lost"
  (no writers). Change in fw.App; C++ fw::App follows the same rule.
- Not included: machine CPU and memory (deferred; see WORKFLOW.md).

## N4. Commands the agent obeys  [revised, approved 30 Sep 2026]

Rule: the agent owns the process, the app owns its behaviour.

| Command | Handled by | Why |
|---|---|---|
| `CMD_START_APP`, `CMD_STOP_APP`, `CMD_KILL_APP` | the node's agent | starting and ending processes; kill works on hung apps; alerts name the sender |
| `CMD_SET_QOS_VARIANT` | the node's agent (N13) | a variant switch is a restart with a different argument |
| `CMD_SET_PARAM` | the app itself (N14) | only the app holds the value; the change is live, no restart |

- Addressing is the same for every command: `target_node` = the node (or
  `*`), `target_app` = the app's name (or `*`, start/stop/kill only). The
  command type decides who acts, so the Control Panel never has to choose a
  route. (Changed from the approved N4, which used an empty `target_app` plus
  the app name in `arg`; the new form matches the IDL's own comments.)
- Sim twins have no agent (like the hardware they stand in for) and obey
  stop and kill themselves (N14), only when named exactly (their node and
  their app name), never `*`: a "stop all" leaves the twin running, as it
  leaves the real ECU running on the rig.
- `CMD_START_APP`: start with the `run:` entry's arguments; already running is
  ignored and logged; UNAVAILABLE refused.
- `CMD_STOP_APP`: polite stop, forced after 10 s.
- `CMD_KILL_APP`: immediate forced stop (works on hung apps). Nothing is
  said on the network, like a crash.
- `CMD_SET_PARAM`: not the agent's; ignored silently (the app acts on it).
- A command is acted on once per `cmd_id`. Senders make ids unique (time in ns
  plus a random part); the agent remembers the last 1,000 ids.
- One at a time, in arrival order, but the agent never waits on a result
  before taking the next command, so a kill is never stuck behind a stop:
  - KILL of an app that is STOPPING or RESTARTING: immediate; cancels the
    stop or switch;
  - STOP during a variant switch: cancels the switch, stops the app;
  - START of an app that is STARTING or RESTARTING: ignored ("already
    running");
  - variant switch of an app that isn't RUNNING: refused (N13 V2).
- The agent never acts on itself
  (`node_agent` refused). Every command is logged with its sender, accepted
  or refused, with the reason.

## N5. Only what the node's run: list names  [approved]

- Commands carry an app name only; arguments always come from the `run:`
  entry. Code comes from the local repo (scenario's apps first, then shared).
- Refused before lookup: anything not a plain app name, `node_agent`, names not
  in this node's list. Sim twins are never started by agents.
- The list is read once at agent start; if scenario.yaml changes, the agent
  logs "restart the agent to use it" and keeps the old list.
- `check`: the same app listed twice on one node is an error.
- Not authentication: it limits what a request can do.

## N6. Supervision  [approved]

- Shared supervisor (`fw.supervise.Supervised`), as `run` and the tests.
- Variant switches by the agent itself (N13). (Was: the note protocol S1-S9,
  replaced if N13 is approved.)
- Crash: CRASHED, alert (N7), no automatic restart; other apps carry on.
- Stop: polite, forced after 10 s (then KILLED, "forced after 10 s").
- If the agent dies, its apps notice (launcher process ID, checked each second
  by fw.App) and stop cleanly, so a restarted agent never creates duplicates.

## N7. Alerts  [approved]

- Decided by who asked, not by exit code. On the scenario's `Alert` topic,
  source `<node>/node_agent`, one alert per app and kind.
- Any end the agent didn't ask for: CRITICAL `crash:<app>` for an error code,
  WARNING for exit 0 ("ended on its own"); message includes the last output
  line, trimmed to fit.
- Kill command: CRITICAL `crash:<app>` marked as a simulated crash, naming the
  sender. Stop command: no alert.
- Hang: WARNING `hang:<app>` when an app that has heartbeated goes silent while
  its process is alive; the threshold is read from the heartbeat topic's QoS
  (not a fixed number). Apps that never heartbeat can't be judged.
- Variant switch failed but rolled back: WARNING `variant:<app>`.
- Alerts clear when the app starts or recovers, and when the agent stops
  cleanly; a starting agent clears its node's stale alerts.
- `check` limits app names to 26 characters (alert ids stay unique).
- The agent's own death is reported by `result_gui` ("machine lost").

## N8. Local console and log (not sent over DDS)  [approved]

- Foreground: one console, every app line prefixed `node/app`, the agent's own
  lines prefixed `node/node_agent` (commands with sender, state changes,
  alerts), license banner once.
- Background: the same lines to `agent.log`, started fresh at each start.
- Output never blocks an app: bounded buffer; if the console can't keep up,
  lines are dropped and "N lines dropped" is printed.
- Stopping (Ctrl-C or `agent stop`): all apps asked to stop, forced after
  10 s; then alerts cleared, heartbeat and app states disposed; exit.

## N9, N10. Moved

`run <scenario> --live` is a mode of the run command, so its requirements now
live with run's: U11 and U12 in [run.md](run.md).

## N11. Never stop the sender  [outline approved]

The agent never stops the app that sent the command (identified from the
sample's writer: its participant name).

## N12. _sys/AppState  [outline approved; detail review pending]

- One sample per (node, app): state (NOT_RUNNING, STARTING, RUNNING,
  RESTARTING, STOPPING, STOPPED, KILLED, CRASHED, UNAVAILABLE), exit code,
  restart count, detail, plus:
  - who caused a command-driven change (e.g. "killed by windows/control_panel");
  - the time the state last changed (the display measures "how long ago"
    against its own clock);
  - the scenario the agent runs (the display flags a mismatch).
- Keyed by node + app; reliable; latest state kept for late joiners. The
  agent's own row: app `node_agent`. Disposed on a clean stop.
- Type in `interfaces/common/protorig.idl`, checked like the others.

## N13. Switching QoS variant  [new, under review]

Replaces the note protocol (S1-S9 in docs/WORKFLOW.md): the agent receives
the request itself, so the app never has to pass a message to its launcher.

- V1. `CMD_SET_QOS_VARIANT`, `target_app` = one app (not `*`), `arg` = the
  variant (`""` = back to the default).
- V2. Checks, before anything is stopped:
  - the app is in this node's list and RUNNING, else refused;
  - the variant already in use: ignored and logged;
  - a variant that is not a profile in the QoS files: refused, WARNING alert
    `variant:<app>`.
- V3. The switch: AppState RESTARTING ("switching to X, by <sender>"); polite
  stop (forced after 10 s); start again with the `run:` entry's arguments,
  `--qos-variant X` replacing any given there.
- V4. The new variant fails (exits with an error within 5 s): started once
  more on the previous variant, WARNING alert `variant:<app>` ("rolled
  back"). If that fails within 5 s too: CRASHED, CRITICAL alert, no more
  tries.
- V5. A variant lasts until the app is stopped: a later `CMD_START_APP`
  uses the `run:` entry as written.
- V6. Every heartbeat carries the variant in effect (as today), so the
  Control Panel shows what happened, not what it asked for.
- Without an agent (`run --node`, `run --app`, hand-started): no variant
  switching; the app ignores the command.

## N14. What an app obeys (fw.App and fw::App)  [new, under review]

- By default an app obeys only `CMD_SET_PARAM` addressed to it; it reads
  `--qos-variant` once, at start. Every other command is ignored.
- Opt-in `obeys={CMD_STOP_APP, CMD_KILL_APP}`: for sim twins only (they have
  no agent, like their hardware). `tc397_twin` already declares it; its
  requirements R1-R9 don't change.
- Removed from fw.App: the variant-switch code, `PROTORIG_RESTART_FILE`,
  exit code 75 and the note functions (`write_note`, `read_note`,
  `new_note_path`). The rollback decision stays in `fw.supervise`, used by the
  agent.
- Kept: the launcher watch (N6: an app stops if its launcher dies).
- The C++ `fw::App` and the Micro C layer follow the same rule.
- Tests reworked: the Python-layer tests that switch variants or stop a probe
  by command move to agent tests or use Ctrl-C.
