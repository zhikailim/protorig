"""
tc397_twin — Stand-in for the TC397: publishes tire pressure on 'Example Temperature' exactly like the ECU

Topics in : none
Topics out: Example Temperature (sensor_msgs::msg::Temperature); pressure in `temperature`
Arguments : --rate --nominal --noise --leak --frame-id  (see README.md)
            plus the standard ones from fw.App

Used as the `sim:` twin of the tc397 node: `protorig run <scenario> --sim` starts
it instead of expecting the real board. Its writer QoS comes from qos/topics.xml,
which `protorig check` keeps identical to what the real ECU offers.

Defaults for rate, unit and frame_id are ASSUMPTIONS until the real board's
values are confirmed (external/tc397/README.md, open items).
"""
import math
import random
import sys
import time

from fw.app import App
from fw import types as T

# 1. Standard start-up (args, domain, QoS, heartbeat, commands, clean shutdown).
app = App("tc397_twin", "Stand-in for the TC397: publishes tire pressure on 'Example Temperature'")

# 2. Arguments. All numeric ones can be changed live with CMD_SET_PARAM.
rate = app.arg("--rate", 10.0, "samples per second (assumed; real ECU rate unconfirmed)")
nominal = app.arg("--nominal", 230.0, "normal pressure (assumed kPa); setting it live resets the pressure")
app.arg("--noise", 0.5, "random noise, standard deviation")
app.arg("--leak", 0.0, "pressure loss per second (simulate a puncture; 0 = none)")
frame_id = app.arg("--frame-id", "", "header.frame_id value (tire id convention unconfirmed)")

if not rate > 0:
    print(f"tc397_twin: --rate must be > 0 (got {rate})", file=sys.stderr)
    sys.exit(2)

# 3. The one writer: same topic, type and (via qos/) QoS as the real ECU.
out = app.writer("Example Temperature")

state = {"pressure": nominal, "last": time.monotonic()}


def publish() -> None:
    """One sample: apply the leak since last time, add noise, publish."""
    now = time.monotonic()
    dt, state["last"] = now - state["last"], now
    state["pressure"] = max(0.0, state["pressure"] - app.params["leak"] * dt)    # B3, B5: never negative
    noise = app.params["noise"]
    value = state["pressure"] + (random.gauss(0.0, noise) if noise > 0 else 0.0)
    ns = time.time_ns()
    s = T.Temperature()
    s.header.stamp.sec, s.header.stamp.nanosec = ns // 1_000_000_000, ns % 1_000_000_000
    s.header.frame_id = frame_id
    s.temperature = max(0.0, value)
    s.variance = noise * noise
    out.write(s)


# 5. Periodic work, and live parameter changes.
timer = app.every(1.0 / rate, publish)


def on_rate(r: float) -> None:
    if r > 0:
        timer.period = 1.0 / r                                        # B8
    else:
        app.params["rate"] = 1.0 / timer.period                        # keep the old rate
        print(f"tc397_twin: ignored rate {r}: must be > 0", flush=True)


app.on_param("rate", on_rate)
app.on_param("nominal", lambda v: state.update(pressure=max(0.0, v)))  # B4: jump back to nominal

# 6. Run until stopped.
sys.exit(app.run())
