# external/tc397

Infineon AURIX TC397 running ETAS AUTOSAR Classic with Connext Micro 2.x. Built and flashed from its own ETAS project, never from this repo.

## What it does today

Publishes dummy tire-pressure samples (the existing sample application).

| Item | Value | Source |
|---|---|---|
| IP | 172.23.100.101 | `known_good_USER_QOS_PROFILES.xml` (initial peer) |
| Topic | `Example Temperature` | `host_sample_Temperature_subscriber.cxx` |
| Type | `sensor_msgs::msg::Temperature` (tire pressure carried in `temperature`) | Temperature.idl |
| Writer QoS | Best effort | Known-good profile is based on `Generic.BestEffort` |
| Discovery | Almost certainly dynamic (DPDE): the host sample uses default dynamic discovery and sets no RTPS object IDs | To confirm |
| Domain | Not in the QoS file; set on the command line (sample default 0) | To confirm |

## Files

- `external.yaml`: the topic, type and writer QoS in machine-readable form; `protorig check` keeps every QoS profile compatible with it.
- `known_good_USER_QOS_PROFILES.xml`: the Windows host profile that is known to receive data from the TC397. Key parts: `allow_interfaces_list` restricted to the host's own IP with `max_interface_count` 1 (multi-NIC Windows hosts otherwise advertise unreachable addresses), and unicast initial peer `4@builtin.udpv4://172.23.100.101`.
- `host_sample_Temperature_subscriber.cxx`: the working host-side subscriber (Traditional C++ API), for reference.

The known-good profile and host sample were built with Connext 7.6.0; protorig uses 7.7 on the host side. Micro 2.x on the ECU interoperates with Pro 7.7 over standard RTPS, but it's worth rebuilding the host sample with 7.7 once, as a quick sanity check.

## Open items

- The ECU's publish rate and pressure unit (needed for its sim twin).

- Which field(s) carry the pressure, and one tire or four (how tires are identified)?
- Confirm the domain ID and the discovery mode.
- The TC397's own initial peer list: does it announce to the VM and Pi, or only to Windows? The 15-minute `rtiddsspy` smoke test from the VM answers this.
- Once you've confirmed the board runs the IDL in `interfaces/external/tc397/` (the host sample receiving data is good evidence), run `./protorig lock tc397` to record it.
