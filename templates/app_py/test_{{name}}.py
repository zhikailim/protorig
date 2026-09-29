"""
Tests for {{name}}. Run: ./protorig test {{name}}

The app runs as a separate process; these tests talk to it only over DDS
(fixtures `bus` and `start_app` come from the repo's root conftest.py).

The three standard tests below work as-is for every app. Add behaviour tests
from the app's given/when/then table; pattern: LISTEN, then ACT, then CHECK.
"""
from fw import types as T
from fw.app import EXIT_KILLED
from fw.testing import wait_for


def test_starts_and_heartbeats(bus, start_app):
    app = start_app("{{name}}")                       # fails if no heartbeat within 10 s
    beats = bus.listen("_sys/NodeStatus")
    wait_for(lambda: sum(1 for b in beats.all() if b.app == "{{name}}") >= 2, 4, "two more heartbeats")
    assert not app.exited()


def test_clean_stop(bus, start_app):
    app = start_app("{{name}}")
    bus.command(app, T.Command.CMD_STOP_APP)
    assert app.wait_exit(5) == 0
    assert "stopped" in app.output

    app2 = start_app("{{name}}")                      # Ctrl-C must also stop it cleanly
    app2.interrupt()
    assert app2.wait_exit(5) in (0, -15, 1)            # 0 on Linux/macOS; Windows can't send Ctrl-C to a child


def test_kill_command(bus, start_app):
    app = start_app("{{name}}")
    bus.command(app, T.Command.CMD_KILL_APP)
    assert app.wait_exit(5) == EXIT_KILLED


# --- behaviour tests: one per row of the given/when/then table -----------------
# def test_B1_...(bus, start_app):
#     out = bus.listen("Alert")                        # 1. listen first
#     app = start_app("{{name}}", "--threshold", "14.5")
#     bus.send("Example Temperature", {"temperature": 15.0})   # 2. act
#     wait_for(lambda: out.count() > 0, 2, "an alert") # 3. check
#     assert out.last().alert_id == "TEMP_HIGH"
