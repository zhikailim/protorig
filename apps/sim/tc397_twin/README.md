# tc397_twin

Stand-in for the TC397: publishes its temperature ramp exactly like the firmware, so every other app can be developed and tested without the board.

It mirrors the firmware and nothing more: no noise, fault or rate options. If the TC397 firmware changes, this twin must change with it (see `external/tc397/README.md`).

## Arguments

| Argument | Default | Meaning |
|---|---|---|
| (standard) | | `--node --scenario --domain --qos-variant --verbose`, from fw.App |

## Topics

| Direction | Topic | Type |
|---|---|---|
| out | `Example Temperature` | `sensor_msgs::msg::Temperature` |
| in | `_sys/DemoControl` | `protorig::DemoControl` (stop and kill only) |

## Requirements (approved 2026-09-29)

Source of truth: the TC397 application source (`TASK(DDS_Subscriber_task)`) and `external/tc397/`.

| # | Requirement | Test |
|---|---|---|
| R1 | Publishes `sensor_msgs::msg::Temperature` on `Example Temperature` | `test_R1_topic_and_type` |
| R2 | Writer QoS the same as the ECU's: reliable, keep-last 1, volatile, no deadline or liveliness lease, reliability heartbeat 250 ms | `test_R2_writer_qos_matches_ecu` (what's visible on the wire; history and heartbeat come from `qos/topics.xml`) |
| R3 | 10 Hz | `test_R3_rate` |
| R4 | `temperature` ramps 10.0 → 15.0 in 0.1 steps, wraps to 10.0; `variance` always 0.1; the ramp restarts at 10.0 when the twin restarts | `test_R4_R5_R6_every_sample_follows_the_firmware`, `test_R4_ramp_wraps`, `test_R4_R6_restart_resets` |
| R5 | `header.frame_id` always `"sensor_frame"` | `test_R4_R5_R6_every_sample_follows_the_firmware` |
| R6 | `header.stamp` starts at 0.100 s and goes up 100 ms per cycle, not wall-clock time; resets on restart | `test_R4_R5_R6_every_sample_follows_the_firmware`, `test_R4_R6_restart_resets` |
| R7 | Obeys Control Panel stop and kill (kill = power loss); sends no `_sys/NodeStatus` heartbeat | `test_R7_no_heartbeat`, `test_R7_stop`, `test_R7_kill` |
| R8 | Publishes only the topics in `external/tc397/external.yaml` | `test_R8_publishes_only_the_ecu_topics` |
| R9 | Participant liveliness lease equals the ECU's (`external.yaml`, value to confirm at bring-up) | `test_R9_lease_matches_ecu` |

**One rule checks R4–R6 on every sample.** Like the firmware, the twin advances the stamp once per cycle. So the stamp tells you which cycle a sample came from (cycle n has stamp n × 0.1 s), and the temperature in cycle n must be `10.0 + ((n - 1) mod 51) / 10`. This works even when the first few samples are missed while discovery completes.

## Known differences from the real ECU (not emulated, on purpose)

- At most 5 remote participants; initial peer only the Windows host. These are rig risks, found in bring-up tests, not in sim.
- UDP only on one interface, 1400-byte messages: no effect on these small samples.
- A failed write on the ECU leaves a 200 ms gap in the stamps; writes don't fail on Linux.
- Timing jitter: Python's timer, not an RTOS task.
- The sample's `source_timestamp` is wall-clock time here, but probably time-since-boot on the ECU. No app may rely on the ECU's source timestamp until that's confirmed.

## Used in scenarios

- `temp-skeleton` (as `sim:` for node `tc397`)
