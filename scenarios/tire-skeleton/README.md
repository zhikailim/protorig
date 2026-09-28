# tire-skeleton

The walking skeleton: the thinnest slice that touches every real piece of hardware, with almost no demo logic. Its job is to prove the plumbing before any real scenario is built on this rig.

## 1. Value proven

- Connext Micro on an AUTOSAR Classic ECU (TC397) interoperates with Connext Professional on Linux and Windows, over a plain managed switch, with no gateway.
- Nodes join and leave without restarting anything else (discovery, liveliness).

## 2. Story

A zonal ECU streams tire pressure. Two HPCs watch it, and raise an alert when a tire runs low. The driver's screen (IVI) and an engineering dashboard show pressures, alerts and the health of every node.

## 3. Demo beats

| # | You do | Audience sees | DDS feature |
|---|---|---|---|
| 1 | Power up the rig | Every node turns green on the node board; pressures plotting live | Discovery |
| 2 | Drop a tire's pressure (twin, or ECU when supported) | Alert on the dashboard and the IVI stand-in | Pub/sub, keyed alerts |
| 3 | Pull the Pi's network cable | Pi turns red within the liveliness period; everything else carries on | Liveliness |
| 4 | Plug it back in | Pi turns green again, no restarts | Automatic rediscovery |
| 5 | Toggle a QoS variant in the Control Panel | Behaviour changes on screen | QoS in XML, no code change |

## 4. Setup

- Nodes and IPs: see `scenario.yaml`. All on one subnet, one managed switch, wired Ethernet only.
- TC397: flashed from the ETAS project with the existing tire-pressure sample; see `external/tc397/`.
- The Linux VM uses **bridged** networking on the Windows master.
- Switch: unicast discovery is used, so IGMP snooping doesn't matter.

## 5. Acceptance tests

1. All endpoints discover each other: TC397, both HPCs, and the three Windows apps.
2. Pulling the Pi's cable turns it red within the liveliness period; reconnecting turns it green with no restarts.
3. The Control Panel toggle visibly changes behaviour on every node.
4. 30-minute soak with no unexplained sample loss.
5. The whole scenario also runs with `--sim` on one machine.

## 6. Talking points

- *"Does the ECU need a gateway to talk to Linux?"* No: Connext Micro speaks standard DDS/RTPS on the wire.
- *"What happens when a node fails?"* Liveliness detects it; nobody else needs to change (beat 3).
- *"How much code to change behaviour?"* None: QoS lives in XML (beat 5).
