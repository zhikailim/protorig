"""
{{name}} — {{description}}

Topics in : (fill in)
Topics out: (fill in)
Arguments : --node --scenario --domain --qos-variant --verbose (standard, from fw.App)
            plus any added with app.arg() below

Run alone:  ./protorig run --app {{name}}      (or: python main.py --help)
"""
import sys

from fw.app import App
from fw import types as T  # noqa: F401  (the IDL types: T.Alert, T.Temperature, ...)

# 1. Standard start-up. fw.App handles: arguments, domain, QoS from qos/ (no QoS
#    code here), participant named "<node>/{{name}}", 1 Hz heartbeat on
#    _sys/NodeStatus, Control Panel commands (stop / kill / QoS variant /
#    parameters), incompatible-QoS warnings, clean shutdown on Ctrl-C.
app = App("{{name}}", "{{description}}")

# 2. App-specific arguments (they appear in --help; the Control Panel can change them live).
# threshold = app.arg("--threshold", 28.0, "alert below this pressure (kPa)")

# 3. Readers and writers, by topic name (known topics: libs/py/fw/topics.py).
# tires  = app.reader("Example Temperature")
# alerts = app.writer("Alert")

# 4. React to incoming data.
# def on_tire(sample: T.Temperature):
#     if sample.temperature < app.params["threshold"]:
#         alerts.write(T.Alert(source=app.who, alert_id="LOW_PRESSURE",
#                              severity=T.Severity.SEVERITY_WARNING,
#                              message="Tire pressure low", value=sample.temperature))
# app.on_data(tires, on_tire)

# 5. Periodic work.
# app.every(0.1, lambda: ...)

# 6. Run until Ctrl-C or a stop/kill command, then clean up.
sys.exit(app.run())
