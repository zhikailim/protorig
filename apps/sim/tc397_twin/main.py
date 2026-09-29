"""
tc397_twin — Stand-in for the TC397: publishes its temperature ramp exactly like the firmware.

Topics in : _sys/DemoControl: stop and kill only ("kill" acts like pulling the ECU's
            power); QoS-variant and parameter commands are ignored, as the ECU can't do them
Topics out: Example Temperature (sensor_msgs::msg::Temperature), nothing else
Arguments : standard only (--node --scenario --domain --qos-variant --verbose).
            No app options on purpose: the twin mirrors the firmware, nothing more.

Requirements R1-R9: README.md. Source of truth: the TC397 application source,
TASK(DDS_Subscriber_task) in Temperature_subscriber.c, and external/tc397/.

Run alone:  ./protorig run --app tc397_twin      (or: python main.py --help)
"""
import sys

import rti.connextdds as dds
import yaml

from fw.app import App, ROOT
from fw import types as T

EXTERNAL = ROOT / "external" / "tc397" / "external.yaml"
PERIOD = 0.1                     # R3: the ECU's OS task runs every 100 ms (10 Hz)
STEP_NS = 100_000_000            # R6: the firmware adds 100 ms to the stamp per run
DECI_MIN, DECI_MAX = 100, 150    # R4: 10.0 .. 15.0 degC, in tenths (firmware's deci_celsius)


def ecu_lease_seconds() -> float:
    """R9: the ECU's participant liveliness lease, from external/tc397/external.yaml."""
    spec = yaml.safe_load(EXTERNAL.read_text(encoding="utf-8")) or {}
    return float(spec["participant"]["liveliness_lease"])


def copy_ecu_participant(qos: dds.DomainParticipantQos) -> None:
    """R9: readers must notice a dead twin exactly as slowly as a dead ECU."""
    lease = ecu_lease_seconds()
    qos.discovery_config.participant_liveliness_lease_duration = dds.Duration.from_seconds(lease)
    # The assert period must stay below the lease; the ECU's is unknown, so keep
    # Connext's usual ratio (30 s for a 100 s lease).
    qos.discovery_config.participant_liveliness_assert_period = dds.Duration.from_seconds(lease * 0.3)


# 1. Start-up. No heartbeat (R7, R8): the real ECU publishes no _sys/NodeStatus,
#    so neither does its twin. It obeys only stop / kill (R7): the real ECU can't
#    switch QoS variant or change parameters, so the twin mustn't either (R2).
app = App("tc397_twin", "Stand-in for the TC397: publishes its temperature ramp exactly like the firmware",
          heartbeat=False, participant_qos=copy_ecu_participant,
          obeys={T.Command.CMD_STOP_APP, T.Command.CMD_KILL_APP})

# 2. The one writer (R1). Its QoS (R2: reliable, keep-last 1, volatile,
#    250 ms heartbeat) comes from qos/topics.xml, which `protorig check` keeps
#    identical to what the ECU offers.
out = app.writer("Example Temperature")

# 3. The firmware's state: static variables that start again on every reset (R4, R6).
sample = T.Temperature()
sample.header.frame_id = "sensor_frame"      # R5
deci_celsius = DECI_MIN


def publish() -> None:
    """One run of the ECU's task, line for line (R4, R5, R6)."""
    global deci_celsius
    # R6: the stamp advances BEFORE the write, so the first sample carries 0.100 s.
    stamp = sample.header.stamp
    stamp.nanosec += STEP_NS
    if stamp.nanosec >= 1_000_000_000:
        stamp.sec += 1
        stamp.nanosec -= 1_000_000_000
    # R4: same arithmetic as the C code (integer tenths / 10.0), so values are bit-identical.
    sample.temperature = deci_celsius / 10.0
    sample.variance = 0.1
    out.write(sample)
    # R4: the firmware advances the value only after a successful write; write()
    # raises on failure here, so reaching this line means it succeeded.
    deci_celsius = DECI_MIN if deci_celsius + 1 > DECI_MAX else deci_celsius + 1


# 4. Periodic work: one sample every 100 ms (R3).
app.every(PERIOD, publish)

# 5. Run until Ctrl-C or a stop/kill command.
sys.exit(app.run())
