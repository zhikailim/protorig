# protorig run: requirements

Status (30 Sep 2026):
- U1-U10 and D1-D3: approved 29 Sep 2026, built and tested.
- B1-B7: added while building; approved 30 Sep 2026.
- U2 (changed): approved 30 Sep 2026 as part of node_agent (N2); not built yet.
- U11 (--live): moved here from node_agent (N9); approved 30 Sep 2026, not built.
- U12 (where --live runs): moved here from node_agent (N10); simpler rule
  proposed 30 Sep 2026, awaiting approval.
- U7 (restart rules): will change if node_agent N13/N14 are approved; `run`
  then does no variant switching (only agents do).

What it is for: one command starts a scenario, or a single app. On a desk it
runs the whole scenario on one machine; on the rig, each machine runs its own
part (or --live brings every machine up through its agent).


## Approved and built (29 Sep 2026)

U1. `protorig run [--scenario S] [--domain N] --app <app> [its arguments]`
    starts one app on this machine, for trying it out. Domain: `--domain`
    if given, else the scenario's if `--scenario` is given, else 0.

U2. `protorig run <scenario> --sim` starts every node's apps on this
    machine, plus each external node's twin (e.g. `tc397_twin` in place of
    the TC397). Each app is started with its node's name (`--node hpc-pi`),
    so the node board shows the real layout.
    CHANGED 30 Sep 2026 (N2, option A), not built yet: `--sim` starts one
    agent per managed node (named after the node) and the external nodes'
    twins directly, then tells every agent "start *". Ctrl-C stops
    everything, agents included.

U3. `--sim` stays on this machine: nothing reaches the network, so a sim run
    on domain 0 can't mix with a powered-on TC397 or the rig. Admin Console
    on the same PC still sees everything.
    (Built as shared memory only: see B7, approved.)

U4. `protorig run <scenario> --node <node>` starts only that node's apps.
    It first writes that node's discovery settings from scenario.yaml: the
    other nodes' IPs as initial peers, DDS restricted to this node's own IP.

U5. Wrong-machine guard: if this machine doesn't have the node's IP, `run`
    refuses with a clear message. External nodes and unknown node names are
    refused too.

U6. Arguments in `run:` entries (e.g. `"hpc_monitor --strength 20"`) are
    passed through unchanged.

U7. Every app follows the restart rules S1-S9 (docs/WORKFLOW.md), with one
    implementation shared with the test launcher.

U8. Output: every line from every app, prefixed `node/app`, in one console.
    Every restart, crash and exit code is reported.

U9. Ctrl-C stops every app cleanly, waits for them, and reports how each
    ended.

U10. `--dry-run` prints exactly what would start (commands, nodes, discovery
     settings) and starts nothing (writes nothing either).

D1. Apps that don't exist yet are listed and skipped; the rest start.

D2. An app that crashes is reported loudly; the others keep running; it is
    not restarted automatically.

D3. C/C++ apps not built yet are listed as "not built" and skipped.


## Added while building (29-30 Sep 2026): approved 30 Sep 2026

B1. Argument order for `--app`: run's own options go BEFORE `--app <name>`;
    everything after the app's name goes to the app unchanged, e.g.
    `protorig run --domain 5 --app my_gui --threshold 14.5`.
    Why: an app argument (`hello` in `--label hello`) was being read as a
    scenario name.

B2. Connext's license banner is shown once per run, not once per app.
    Why: four lines per app cluttered the console.

B3. Exit code: `run` exits 1 if any app failed (crashed, or couldn't
    start), 0 otherwise.
    Why: scripts (and later `preflight`) can tell.

B4. Peer list detail: each peer machine is announced to N participants,
    N = its number of apps + 1, at least 4 (Connext's "N@" peer prefix; the
    known-good TC397 host profile used 4).

B5. Stopping detail: an app still running 10 s after a polite stop is
    forced.

B6. Each app runs in its own process group, so a Ctrl-C in the terminal
    reaches only `run`, which then stops each app politely (Ctrl-C on Linux,
    Ctrl-Break on Windows). Needed for clean stops on Windows.

B7. U3 was approved as "shared memory and loopback"; it was built as shared
    memory ONLY (no network transport at all). Why: nothing more is needed
    for apps on one machine, and it guarantees nothing reaches any network;
    Admin Console on the same PC uses shared memory by default.


## --live (moved from node_agent N9, N10)

U11. [approved 30 Sep 2026, not built]
     `protorig run <scenario> --live` brings the whole rig up through its
     agents:
     1. Waits for every managed node's agent, printing who is still
        missing; lists external nodes once as "power it on". External
        nodes are checked by discovering their writers (e.g. the TC397's
        `Example Temperature`); informational only, never blocks.
     2. Checks every agent runs the same scenario (from the `scenario`
        field in `_sys/AppState`); refuses otherwise, saying which node
        runs what and how to restart it.
     3. Sends "start *" to each agent, one command per node. A node whose
        apps don't start is sent the same command again after 3 s, with
        the same `cmd_id` (so an agent that already acted ignores it).
     4. Prints a line in its own terminal as each app changes state (read
        from `_sys/AppState` over DDS; nothing is published back), flags
        apps UNAVAILABLE or not RUNNING within 20 s, then keeps printing
        changes until Ctrl-C. Also printed: alerts as they arrive, and
        "<node> agent LOST" / "<node> agent back".
     5. Ctrl-C sends "stop *" to every agent, waits up to 15 s for every
        app to be STOPPED, and reports; the agents stay up. A second Ctrl-C
        exits at once without waiting for the report.
     Also:
     - Waits until every agent is there; `--partial` starts on whichever
       agents are present.
     - Running `--live` again while the demo runs reattaches: shows the
       running apps; Ctrl-C stops them.
     - While waiting, the message says an agent may be missing because it
       isn't started, is unreachable, or runs a scenario on another domain.
     - Exit code: 0 = everything started and stopped cleanly; 1 = some app
       failed; 2 = could not start (agents missing, scenario mismatch,
       wrong machine).

U12. [proposed 30 Sep 2026, awaiting approval]
     `--live` runs from a machine that is in the scenario, found by its IP:
     - one node has this machine's IP: use that node;
     - several nodes share this IP: use the first and say so;
     - several nodes with different IPs of this machine: refuse, listing
       them;
     - none: refuse, exit 2.
     It uses that node's discovery settings, so it can reach every node,
     written to its own file `build/<scenario>/<node>/live_qos.xml` (never
     the agent's). Participant name `<node>/run-live`.
