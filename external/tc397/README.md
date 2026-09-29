# external/tc397

Infineon AURIX TC397 running ETAS AUTOSAR Classic with Connext Micro 2.x. Built and flashed from its own ETAS project, never from this repo.

## What it does today

Publishes a temperature ramp (the existing sample application, `Temperature_publisher.c`, driven by `TASK(DDS_Subscriber_task)`). All values below are confirmed from the application source unless marked otherwise.

| Item | Value | Source |
|---|---|---|
| IP | 172.23.100.101 /24 | interface table (`0xAC176465`) |
| Domain | 0 | `publisher_main_w_args(0, ...)` |
| Discovery | Dynamic (DPDE); initial peer `1@_udp://172.23.100.102` (Windows host only) | `publisher_main_w_args` call |
| Topic | `Example Temperature` | publisher source |
| Type | `sensor_msgs::msg::Temperature` | `interfaces/external/tc397/Temperature.idl` |
| Writer QoS | RELIABLE, VOLATILE, KEEP_LAST 1, no deadline, no liveliness lease; heartbeat 250 ms | `USE_RELIABLE_QOS` is defined |
| Data: `temperature` | Ramp 10.0 → 15.0 °C in 0.1 steps, then wraps to 10.0 (about 5 s per cycle at 10 Hz) | `deci_celsius / 10.0` |
| Data: `variance` | Always 0.1 | publisher source |
| Data: `header.frame_id` | Always `"sensor_frame"` | publisher source |
| Data: `header.stamp` | First sample 0.100 s, then +100 ms per task run, even when a write fails (not wall-clock time; resets with the board) | publisher source |
| Rate | 10 Hz, assuming the OS task runs every 100 ms (the stamp step suggests so) | **To confirm** in the OS configuration |
| Limits | `remote_participant_allocation = 5`, `max_message_size = 1400` | publisher source |
| Participant liveliness lease | Not set, so Connext Micro's default (believed 100 s) | **To confirm** in Admin Console |

The temperature only advances after a successful write, but the stamp advances every run. So a failed write shows up at readers as a 200 ms gap in the stamp, with no temperature value skipped.

## Rig risks (read before bring-up)

1. **At most 5 remote participants.** The ECU can track only 5 other participants. `temp-skeleton` has about 7 (2 per HPC, 3 on Windows). Participants beyond the fifth may never see the ECU's data. Options: raise the allocation in the firmware, or run fewer apps that read the ECU.
2. **The ECU announces itself only to Windows (.102).** The VM and Pi find the ECU only if *they* list it as an initial peer (they do, via the scenario) and the ECU accepts them. Unproven: the 15-minute `rtiddsspy` smoke test from the VM answers it.

## Files

- `external.yaml`: the topic, type and writer QoS in machine-readable form; `protorig check` keeps every QoS profile compatible with it.
- Sim twin: `apps/sim/tc397_twin/` (requirements R1–R9 in its README). Change it whenever the firmware changes.
- `known_good_USER_QOS_PROFILES.xml`: the Windows host profile that is known to receive data from the TC397. Key parts: `allow_interfaces_list` restricted to the host's own IP with `max_interface_count` 1 (multi-NIC Windows hosts otherwise advertise unreachable addresses), and unicast initial peer `4@builtin.udpv4://172.23.100.101`.
- `host_sample_Temperature_subscriber.cxx`: the working host-side subscriber (Traditional C++ API), for reference.

The known-good profile and host sample were built with Connext 7.6.0; protorig uses 7.7 on the host side. Micro 2.x on the ECU interoperates with Pro 7.7 over standard RTPS, but it's worth rebuilding the host sample with 7.7 once, as a quick sanity check.

## Open items

- Confirm the task period (10 Hz) in the OS configuration.
- Confirm the participant liveliness lease (Admin Console); update `external.yaml` if it isn't 100 s.
- Check what the ECU puts in each sample's `source_timestamp` (probably time since boot). Until then, no app relies on it.
- Run the `rtiddsspy` smoke test from the VM and the Pi (risk 2).
- Decide how to handle the 5-participant limit (risk 1).
- Once you've confirmed the board runs the IDL in `interfaces/external/tc397/` (the host sample receiving data is good evidence), run `./protorig lock tc397` to record it.
