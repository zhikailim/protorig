# Quick Start - Demo bring up guide

How to take a demo from "machines switched off" to "every app running", for people new to this rig. For the full design behind it, see [WORKFLOW.md](WORKFLOW.md).

> **Status (29 Sep 2026).** Parts of this guide describe commands that are still being built. Each step is marked:
> **✓ available** works today · **◷ planned** designed and approved, not built yet.
> Until the planned parts exist, use `protorig run <scenario> --node <node>` on each machine instead of agents and `--live` (see *Before agents exist* at the end).

---

## The idea in one paragraph

A demo is a **scenario**: a folder under `scenarios/` whose `scenario.yaml` is the single plan of *what runs where*. Each machine in the rig runs one small **agent**, which carries out only its own machine's part of that plan. You start the agents once; after that, one command (or the Control Panel) tells every agent when to start, stop or kill its apps. Commands only ever say *when*, never *what* or *how*: which apps run on which machine, and with which arguments, always comes from `scenario.yaml`.

## Words used in this guide

| Word | Meaning |
|---|---|
| **Scenario** | One demo: `scenarios/<name>/`, with `scenario.yaml` (what runs where) and `README.md` (the story, demo beats and acceptance tests). |
| **Node** | One machine in the scenario, e.g. `windows`, `hpc-vm`, `hpc-pi`. Each has an IP address in `scenario.yaml`. |
| **Managed node** | A machine whose software comes from this repo (Windows, Linux VM, Raspberry Pi). It gets an agent. |
| **External node** | A device whose software is built elsewhere, such as the TC397 ECU (`external: true`). Nothing from this repo runs on it, and it has no agent. For desk runs it has a **sim twin** that stands in for it. |
| **`run:` list** | In `scenario.yaml`, the apps a managed node should run, with their arguments. |
| **Agent** (`node_agent`) | One per managed machine. Starts, stops and kills that machine's apps when asked over DDS, and reports their state. Never starts anything that isn't in its node's `run:` list. |
| **Node board** | The part of `result_gui` that shows every machine and app: green running, grey stopped, red lost or crashed. |

## The two ways to run a scenario

| | On your desk: `--sim` | On the real rig: `--live` |
|---|---|---|
| Hardware needed | None: one PC | Every machine in the scenario, on one switch |
| External nodes | Their sim twins run on your PC | The real devices, powered on |
| Network | Nothing leaves your PC (shared memory only) | Unicast discovery between the nodes' IPs |
| Command | `protorig run <scenario> --sim` | Agents on each machine, then `protorig run <scenario> --live` |
| Use it for | Building, testing, rehearsing the demo | The demo itself |

Rehearse with `--sim` first. The Control Panel, node board and demo beats behave the same in both.

---

## Bringing up the real rig, step by step

### Stage 0: once per machine, well before the demo

1. **Clone the repo** on every managed machine (Windows, VM, Pi). ✓
2. **Set up Python and the Connext license** on each: `bootstrap` (◷). Until then, by hand (✓):
   ```
   python3 -m venv .venv            (Windows: py -3 -m venv .venv)
   .venv/bin/python -m pip install -r bootstrap/requirements.txt
   copy your rti_license.dat into the repo's .local/ folder
   ```
3. **Build the C/C++ apps** on each machine that runs them: `protorig build` (◷).
4. **Check the scenario**: `protorig check` (✓). It must show no errors for your scenario.
5. **Check the IPs**: every managed node's `ip:` in `scenario.yaml` must be that machine's real address on the rig's switch.

### Stage 1: power on the external devices

6. Switch on each external node (e.g. the TC397). It boots its own firmware and starts publishing; there is nothing to start from the repo.

### Stage 2: start one agent per managed machine (◷)

7. On **each** managed machine, once:
   ```
   protorig agent start <scenario> --node <this machine's node name> --background
   ```
   Later this happens automatically at boot.
8. Each agent:
   - refuses if this machine doesn't have the node's IP (you're on the wrong machine), or if an agent for that node is already running;
   - writes this machine's discovery settings (the other nodes as peers, DDS bound to this machine's IP);
   - reads **only its own node's `run:` list**;
   - starts heartbeating, and reports every listed app as `NOT_RUNNING`.

   **No apps are running yet.** The agents are waiting.
9. Check from any machine: `protorig agent status <scenario> --node <node>` says whether that node's agent is alive and what it runs.

### Stage 3: bring the demo up, from one machine (◷)

10. On any machine in the scenario (usually Windows):
    ```
    protorig run <scenario> --live
    ```
11. `run --live` waits until it hears every managed node's agent, and lists the external nodes as "power it on".
12. It sends every agent "start `*`" (everything on your list).
13. Each agent starts **its own** apps, on its own machine, with the arguments from `scenario.yaml`.
14. Each app starts heartbeating; `run --live` prints each one as it comes up, and the node board turns green row by row. The demo is running.

### Stage 4: during the demo (◷)

15. Use the Control Panel. Every button is a command to one agent (one machine) or to all of them (`*`):

    | Command | Effect |
    |---|---|
    | start `<app>` or `*` | Starts it (or everything on that machine's list) |
    | stop `<app>` or `*` | Polite stop; forced after 10 s if it doesn't stop |
    | kill `<app>` or `*` | Immediate, as if it crashed; the agent stays up and can start it again |

    QoS variant switches and parameters go to the apps themselves, not to the agents.

### Stage 5: shutting down

16. Press **Ctrl-C** in the `run --live` window. Every agent stops its apps cleanly. The agents stay up, ready for the next `run --live`.
17. To stop an agent too: `protorig agent stop <scenario> --node <node>` on that machine.

---

## Rehearsing on your desk (`--sim`)

```
protorig run <scenario> --sim
```

This does stages 2, 3 and the external devices in one go, on one PC. It starts one agent per managed node (each named after its node), a sim twin for each external node, then "start `*`" to every agent. Everything stays on your PC. Stage 4 works exactly as on the rig. Ctrl-C stops everything, agents included.

Today (✓), `--sim` starts the apps directly without agents; the Control Panel's per-app commands work, per-machine ones arrive with the agents (◷).

## What the node board tells you

| Board shows | Meaning | What to do |
|---|---|---|
| Machine green, apps grey | Agent alive, apps not started (or stopped on purpose) | `run --live`, or start them from the Control Panel |
| App red, `CRASHED (exit N)` | The app ended on its own with an error | See the agent's console or `build/<scenario>/<node>/agent.log`; start it again |
| App red, `KILLED` | Killed by a command | Start it again when the beat is over |
| Machine red, "lost" | No heartbeat for 3 s: agent crashed, machine off, or cable out | Check the machine and cable; start the agent again |
| App `RUNNING` but no heartbeat | Process alive but not working (hung, or can't reach DDS) | Stop and start it; check its log |

## When something doesn't come up

| Symptom | Likely cause | Fix |
|---|---|---|
| `agent start` refuses: "this machine doesn't have <ip>" | Wrong machine, or the IP in `scenario.yaml` is wrong | Run it on the right machine, or fix the IP |
| `agent start` refuses: "an agent for <node> is already running" | Started twice | `agent status` to see it; `agent stop` first if you want a fresh one |
| `run --live` waits for a node forever | That machine's agent isn't running, or can't be reached | `agent status` for that node; check cable, IP, firewall |
| An app shows `UNAVAILABLE` | The app doesn't exist yet, or a C/C++ app isn't built on that machine | Create it, or `protorig build` on that machine |
| A command is refused in an agent's log | The app isn't in that node's `run:` list, or it's `node_agent` itself | Correct the command, or add the app to `scenario.yaml` and restart the agent |
| No data from an external device | Device off, not flashed, or not reachable | See `external/<device>/README.md` (e.g. the TC397's rig risks) |

## Before agents exist (✓ today)

Until agents and `--live` are built, bring the rig up by hand, one command per machine:

```
protorig run <scenario> --node <this machine's node name>
```

It starts that machine's apps directly (same wrong-machine check and discovery settings), shows their output with a `node/app` prefix, and stops them all on Ctrl-C.

---

## EXAMPLE: `adas-failover`

A made-up demo with several apps per machine. `sensor_fusion` runs as the primary on the VM and as the backup on the Pi.

```yaml
nodes:
  windows: { ip: 172.23.100.102, run: [result_gui, control_panel, ivi_standin] }
  hpc-vm:  { ip: 172.23.100.110, run: ["sensor_fusion --role primary", path_planner, "logger --dir /data"] }
  hpc-pi:  { ip: 172.23.100.121, run: ["sensor_fusion --role backup", brake_controller, "logger --dir /home/pi/logs"] }
  tc397:   { ip: 172.23.100.101, external: true, sim: tc397_twin }
```

**What each agent reads: its own row, nothing else**

| Agent | May start (with exactly these arguments) |
|---|---|
| `windows/node_agent` | `result_gui`, `control_panel`, `ivi_standin` |
| `hpc-vm/node_agent` | `sensor_fusion --role primary`, `path_planner`, `logger --dir /data` |
| `hpc-pi/node_agent` | `sensor_fusion --role backup`, `brake_controller`, `logger --dir /home/pi/logs` |
| TC397 | no agent: runs its firmware |

The same app can appear on several machines with different arguments; each agent only knows its own version.

**Bring-up**

1. *Stage 0:* the repo is cloned and the C++ apps built on the VM and the Pi.
2. *Stage 1:* power on the TC397. It starts publishing sensor data.
3. *Stage 2:* one agent per machine:
   ```
   Windows> protorig agent start adas-failover --node windows --background
   VM$      protorig agent start adas-failover --node hpc-vm  --background
   Pi$      protorig agent start adas-failover --node hpc-pi  --background
   ```
   The node board shows three machines and 9 app rows, all grey (`NOT_RUNNING`).
4. *Stage 3:* on Windows, `protorig run adas-failover --live`. It hears `windows ✓, hpc-vm ✓, hpc-pi ✓` (and `tc397: external, power it on`), then sends "start `*`" to each agent:
   - Windows starts `result_gui`, `control_panel`, `ivi_standin`;
   - the VM starts `sensor_fusion --role primary`, `path_planner`, `logger --dir /data`;
   - the Pi starts `sensor_fusion --role backup`, `brake_controller`, `logger --dir /home/pi/logs`.

   The node board turns green row by row; the TC397 feeds both `sensor_fusion`s, the primary drives `path_planner`, and `brake_controller` acts.

**Demo beats, all from the Control Panel**

| Beat | Command (node · action) | What the audience sees |
|---|---|---|
| Failure | `hpc-vm` · kill `*` | The VM's three apps turn red (`KILLED`); the Pi's backup `sensor_fusion` takes over. The VM's agent stays up. |
| Recovery | `hpc-vm` · start `*` | The VM's apps come back, with their original arguments |
| Clean stop | `*` · stop `logger` | Both loggers turn grey (stopped on purpose); everything else carries on |
| A mistake, refused | `hpc-vm` · start `brake_controller` | Nothing starts; the VM's agent logs "not in hpc-vm's run: list" (only the Pi may run it) |
| Stop one machine's apps from the Control Panel | `windows` · stop `*` | `result_gui` and `ivi_standin` stop; the Control Panel keeps running, because it sent the command |

**Refused, whatever the sender**

| Command | Why |
|---|---|
| `hpc-pi` · start `logger --dir /tmp` | Commands carry names only; arguments come from `scenario.yaml` |
| `hpc-pi` · start `/bin/sh` | Not an app name |
| `hpc-vm` · stop `node_agent` | An agent never acts on itself; stop it on its own machine |

**Shutting down:** Ctrl-C in the `run --live` window. All 9 apps stop cleanly; the agents stay up.

**On your desk:** `protorig run adas-failover --sim` starts three agents named `windows`, `hpc-vm` and `hpc-pi` plus the TC397 twin, all on one PC, and every beat above works the same way.
