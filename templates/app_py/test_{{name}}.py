"""
Tests for {{name}}. Run: ./protorig test {{name}}

The app runs as a separate process; these tests talk to it only over DDS
(fixtures `bus` and `start_app` come from the repo's root conftest.py).

The three standard tests below work as-is for every app. Add behaviour tests
from the app's given/when/then table; pattern: LISTEN, then ACT, then CHECK.
"""
import time

from fw import types as T
from fw.testing import wait_for


def test_starts_and_heartbeats(bus, start_app):
    app = start_app("{{name}}")                       # fails if no heartbeat within 10 s
    beats = bus.listen("_sys/NodeStatus")
    wait_for(lambda: sum(1 for b in beats.all() if b.app == "{{name}}") >= 2, 4, "two more heartbeats")
    assert not app.exited()


def test_clean_stop(bus, start_app):
    """Ctrl-C (how `protorig run` and the node agent stop an app) ends it cleanly."""
    app = start_app("{{name}}")
    app.interrupt()
    assert app.wait_exit(5) == 0
    assert "stopped" in app.output


def test_leaves_start_stop_kill_to_the_agent(bus, start_app):
    """Stop, kill and QoS-variant commands are the node agent's job (N14):
    sent to the app itself, they are ignored and it keeps running."""
    app = start_app("{{name}}")
    for cmd in (T.Command.CMD_STOP_APP, T.Command.CMD_KILL_APP, T.Command.CMD_SET_QOS_VARIANT):
        bus.command(app, cmd)
    wait_for(lambda: app.output.count("ignoring ") >= 3, 3, "three 'ignoring' log lines")
    time.sleep(0.5)
    assert not app.exited()


# --- behaviour tests: one per row of the given/when/then table -----------------
# def test_B1_...(bus, start_app):
#     out = bus.listen("Alert")                        # 1. listen first
#     app = start_app("{{name}}", "--threshold", "14.5")
#     bus.send("Example Temperature", {"temperature": 15.0}, to=app)   # 2. act (to=: wait for THIS app)
#     wait_for(lambda: out.count() > 0, 2, "an alert") # 3. check
#     assert out.last().alert_id == "TEMP_HIGH"
