# node_agent and run --live: requirements

Status: ALL APPROVED (5 Oct 2026). Built so far (step 1 of 5, 5 Oct 2026): the
fw.App side of N3 (heartbeat disposed on a clean stop), N6 (launcher watch),
N11 addition 1 (participant named node/app, Python) and N14; the note protocol
is removed. Step 3a (5 Oct 2026): the agent itself, N3-N8 and N11-N13, as
behaviour rows B1-B27 in apps/tooling/node_agent/README.md. Not built yet: the
`protorig agent` command (N1, step 3b), --sim through agents (N2, step 4).
- N1-N3, N5-N8: approved in detail.
- N4: REVISED and approved 30 Sep 2026 (the agent owns the process, the app
  owns its behaviour).
- N9-N10: moved to run.md as U11-U12.
- N11: approved 5 Oct 2026.
- N12: approved 5 Oct 2026.
- N13 (variant switching, replaces the note protocol S1-S9): approved 30 Sep 2026.
- N14 (what an app obeys): approved 30 Sep 2026.
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

## N11. Never stop the sender  [approved 5 Oct 2026, not built]

Why: the Control Panel is itself started by Windows' agent; a "stop *" it
sends must not stop it mid-demo.

- How the sender is known: every sample carries its writer's handle; Connext
  gives that writer's participant name, `<node>/<app>`. No IDL change (the
  same lookup names senders in logs and alerts, N4 and N7).
- A. A STOP or KILL (named, or via `*`) skips the sender when the sender is
  `<this node>/<app>` and that app is in this node's list; the rest of the
  command still happens; logged "not stopping <app>: it sent the command".
  The same app name on another node is not protected. Unambiguous because
  node and app names can't contain "/" (`check`).
- B. A variant switch of the sender is refused (it would restart it). START
  and SET_PARAM are unaffected.
- C. Unknown sender (no name, not `node/app`, or already gone): acted on
  normally, logged as sender "unknown". Safe because every agent-managed app
  is named `node/app` (addition 1), so an unknown sender is never one of them.
- D. `run --live` (participant `<node>/run-live`) is never protected: "-" is
  not allowed in app names, so its "stop *" stops everything.
- Addition 1: every app, in every language (fw.App, C++ fw::App), names its
  participant `<node>/<app>`; a test checks it.
- Addition 2: the agent looks up the sender as soon as it takes the sample,
  before the command is queued (N4), so a sender that exits right after
  sending is still recognised.
- Accepted: a second copy of an app started by hand shares the name, so a
  "stop *" it sends also spares the agent's copy. Visible and logged, rare,
  harmless; not worth comparing process IDs.
- Not authentication: a participant's name is self-declared (N5).
- For the Control Panel review: `*` means every app, GUIs included; "reset
  the demo, keep the screens up" = one named command per demo app.

## N12. _sys/AppState  [approved 5 Oct 2026, not built]

How every agent tells the rig what its apps are doing; read by `result_gui`,
the Control Panel and `run --live`.

- One row per (node, app), keyed by both; the agent's own row is app
  `node_agent`. Published only on change (no periodic traffic).
- States and when the agent sets them:

  | State | Set when | Leaves when |
  |---|---|---|
  | NOT_RUNNING | agent starts, for every app in its list | START |
  | UNAVAILABLE | code missing or not built (detail says which) | rechecked at each START |
  | STARTING | process launched | first heartbeat (or the "running" line, for apps without one) → RUNNING |
  | RUNNING | as above | stop, kill, switch, or it ends |
  | RESTARTING | variant switch or rollback in progress (N13) | new variant confirmed → RUNNING; gave up → CRASHED |
  | STOPPING | polite stop sent | it ends → STOPPED; 10 s → KILLED |
  | STOPPED | ended after a stop command; or ended on its own with exit 0 (detail "ended on its own (exit 0)"; N7 raises the WARNING) | START |
  | KILLED | kill command, or forced after 10 s | START |
  | CRASHED | ended unasked with an error code, or a rollback gave up | START |

  Still STARTING after 15 s: WARNING alert `start:<app>`.
- Fields: `node`, `app` (keys); `state`; `exit_code` (meaningful in STOPPED,
  KILLED, CRASHED); `restarts` (variant switches and rollbacks since the last
  START); `detail` (one line, e.g. "killed by windows/control_panel", "forced
  after 10 s", "switching to LongHistory", "not built", the last output line
  on a crash, "not responding"); `scenario` (the agent's scenario, used by
  `run --live`, U11); `changed_at` (agent's clock).
- Delivery: reliable, latest row kept for late joiners. Disposed on a clean
  agent stop.
- Robustness:
  1. A node's rows show "unknown: agent lost" as soon as its agent's
     heartbeat is lost (3 s), not when the DDS writer goes (participant
     lease, 10 s). Applies to `result_gui` and `run --live`.
  2. Two agents for one node: an agent keeps listening for another agent
     heartbeat claiming its node, for as long as it runs. If it hears one,
     the agent whose participant ID compares higher stops its apps and
     exits; both raise CRITICAL "two agents for <node>".
  3. A hung app (N7) stays RUNNING with detail "not responding", cleared when
     it recovers. No new state.
  4. The process exit is final: CRASHED, STOPPED and KILLED come from the
     exit, never from a missing heartbeat. A heartbeat only moves STARTING
     to RUNNING or confirms a variant (N13 V4).
  5. `detail` is trimmed to fit at a character boundary, ending in "…".
- Clocks: every rig machine's clock is synced to Windows (bootstrap step);
  the display compares each heartbeat's send time with its arrival time and
  warns "<node> clock off by N s" above 1 s (result_gui requirement).
- Not covered: apps started by hand have no row; the display labels their
  heartbeats "unmanaged".
- New states are only ever added at the end of the list; `check`'s contract
  lock catches a reorder. Type in `interfaces/common/protorig.idl`.

## N13. Switching QoS variant  [approved 30 Sep 2026, not built]

Replaces the note protocol (S1-S9 in docs/WORKFLOW.md): the agent receives
the request itself, so the app never has to pass a message to its launcher.

- V1. `CMD_SET_QOS_VARIANT`, `target_app` = one app (not `*`), `arg` = the
  variant (`""` = back to the default).
- V2. Checks, before anything is stopped:
  - the app is in this node's list and RUNNING, else refused;
  - the variant already in use: ignored and logged;
  - a variant that is not a profile in the QoS files: refused, WARNING alert
    `variant:<app>`. The agent reads the same QoS file list as the app
    (repo, scenario and node files), so a scenario's own variants are known.
- V3. The switch: AppState RESTARTING ("switching to X, by <sender>"); polite
  stop (forced after 10 s); start again with the `run:` entry's arguments,
  `--qos-variant X` replacing any given there.
- V4. Success = the app's first heartbeat REPORTING THE NEW VARIANT arrives
  within 15 s (room for the Pi's slower start). Anything else (exits, hangs
  while starting, reports another variant) is a failure: started once more
  on the previous variant, WARNING alert `variant:<app>` ("rolled back"). If
  the rollback doesn't succeed by the same test: CRASHED, CRITICAL alert, no
  more tries. Apps that never heartbeat: failure = exits with an error
  within 5 s.
- V5. A variant lasts until the app is stopped: a later `CMD_START_APP`
  uses the `run:` entry as written.
- V6. Every heartbeat carries the variant in effect (as today), so the
  Control Panel shows what happened, not what it asked for.
- V7. The restart gap is expected, not a fault: the switched app's data
  pauses for a few seconds and its history starts empty (e.g. a trend plot
  restarts). While RESTARTING the agent raises no hang alert and the
  display shows "switching", not "lost". The typical gap is measured by the
  tests on a desk and on the Pi at bring-up.
- V8. A variant must never stop data flowing. A switch changes one app
  only; its peers keep the default QoS. `protorig check` therefore requires,
  for every topic a variant touches: variant writer compatible with the
  default reader, and default writer compatible with the variant reader
  (plus the existing rule against external nodes' writers).
  `Variant.Alert.BestEffort` changes to reader-side only (as written, its
  writer side would stop every alert); the demo beat switches the alert
  reader (`result_gui`).
- Without an agent (`run --node`, `run --app`, hand-started): no variant
  switching; the app ignores the command.

## N14. What an app obeys (fw.App and fw::App)  [approved 30 Sep 2026, not built]

- By default an app obeys only `CMD_SET_PARAM` addressed to it (its node or
  `*`, and its own app name; `target_app` `*` is ignored, since parameter
  names differ per app). The value changes live and `on_param` is called; an
  unknown parameter name is logged and ignored.
- It reads `--qos-variant` once, at start (unknown variant: clear message,
  exit; the agent's V4 then rolls back).
- Every other command is ignored. Commands for other apps: silently. A
  command naming this app that isn't its to handle: one log line saying who
  handles it (e.g. "variant switching needs an agent: restart with
  --qos-variant X").
- Opt-in `obeys={CMD_STOP_APP, CMD_KILL_APP}`: for sim twins (they have no
  agent, like their hardware). fw.App obeys opt-in commands only when they
  name the app exactly, never `*` (N4). `tc397_twin` already declares it; its
  requirements R1-R9 don't change. No `check` rule: a managed app that opted
  in would just be stopped twice, which is harmless.
- Removed from fw.App: the variant-switch code, `PROTORIG_RESTART_FILE`,
  exit code 75, and `write_note`, `read_note`, `new_note_path`. The rollback
  decision stays in `fw.supervise`, used by the agent (N13 V4).
- Kept: the launcher watch (N6: an app stops within about 1 s if its
  launcher dies).
- The C++ `fw::App` and the Micro C layer follow the same rules.
- Tests: variant-switch tests move to the agent's tests; tests that stop a
  probe by command use Ctrl-C; the note tests are deleted; the twin's R7
  tests stay, plus a new one: the twin ignores stop and kill sent to `*`.
