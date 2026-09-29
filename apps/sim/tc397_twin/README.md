# tc397_twin

Stand-in for the TC397: publishes tire pressure on `Example Temperature`, exactly like the ECU, so a scenario runs with `--sim` when the board isn't there. Never deployed to a real node.

## What it mimics

| Aspect | Real TC397 | Twin |
|---|---|---|
| Topic / type | `Example Temperature` / `sensor_msgs::msg::Temperature` | Same (from `fw/topics.py`) |
| Writer QoS | Best effort, volatile, no deadline (`external/tc397/external.yaml`) | Same: `protorig check` enforces that the default writer QoS for this topic mirrors the ECU |
| Pressure | Dummy values in `temperature` | `temperature` = pressure; `variance` = noise² |
| Rate, unit, tire id | **Not yet confirmed** (see `external/tc397/README.md`) | Configurable; defaults are assumptions: 10 Hz, kPa around 230, empty `frame_id` |

## Arguments

| Argument | Default | Meaning |
|---|---|---|
| `--rate` | 10.0 | Samples per second (must be > 0) |
| `--nominal` | 230.0 | Normal pressure (assumed kPa). Changing it live resets the pressure to it. |
| `--noise` | 0.5 | Random noise, standard deviation |
| `--leak` | 0.0 | Pressure loss per second; set it live to simulate a puncture, 0 to stop |
| `--frame-id` | "" | Value for `header.frame_id` (e.g. a tire id, once the ECU's convention is known) |
| (standard) | | `--node --scenario --domain --qos-variant --verbose`, from fw.App |

Live changes (Control Panel, or a test): `CMD_SET_PARAM` with `leak`, `nominal`, `noise` or `rate`. Non-finite values are rejected by fw.App.

## Topics

| Direction | Topic | Type |
|---|---|---|
| out | `Example Temperature` | `sensor_msgs::msg::Temperature` |

## Behaviour (given / when / then)

| # | Given | When | Then |
|---|---|---|---|
| B1 | Defaults | Running | Publishes at `--rate` (±20%); values within nominal ± 5·noise |
| B2 | `--noise 0` | Running | Every value is exactly `--nominal`; `variance` is 0 |
| B3 | Running at nominal | `leak` set to 20 | Pressure falls at about 20 per second |
| B4 | Leaking | `nominal` set to 250 | Pressure jumps back to 250 (leak continues from there) |
| B5 | Leaking for long | Pressure would go below 0 | Stays at 0, never negative |
| B6 | Any | Any sample | `header.stamp` is within 1 s of the wall clock; `frame_id` = `--frame-id` |
| B7 | `--rate 0` or negative | Start | Refuses to start (exit 2) with a clear message |
| B8 | `rate` set live | e.g. 20 | Publishing rate follows |

## Used in scenarios

- `tire-skeleton` (as `sim:` of the `tc397` node)
