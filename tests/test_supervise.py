"""
test_supervise.py — libs/py/fw/supervise.py: building variant arguments,
telling whether a process is alive, and running one app process.

No DDS here: these run without Connext.
"""
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs" / "py"))

from fw.supervise import LAUNCHER_ENV, Supervised, process_alive, with_variant  # noqa: E402

BASE = ["--node", "pi", "--domain", "0"]
V = "Variant.Temperature.LongHistory"


# --- with_variant (used by the agent for N13 V3) --------------------------------------

@pytest.mark.parametrize("args,variant,expected", [
    (BASE, V, BASE + ["--qos-variant", V]),
    (BASE + ["--qos-variant", "Old"], V, BASE + ["--qos-variant", V]),
    (BASE + ["--qos-variant=Old"], V, BASE + ["--qos-variant", V]),
    (["--qos-variant", "Old"] + BASE, "", BASE),                       # "" = back to default
    (BASE + ["--threshold", "14.5"], V, BASE + ["--threshold", "14.5", "--qos-variant", V]),
])
def test_with_variant(args, variant, expected):
    assert with_variant(args, variant) == expected


def test_with_variant_fuzz():
    """Random argument lists: the other arguments survive in order, and exactly
    one --qos-variant (or none, for default) remains."""
    rnd = random.Random(3)
    words = ["--node", "pi", "--qos-variant", "--qos-variant=X", "Y", "--domain", "7", "", "--x"]
    for _ in range(2000):
        args = [rnd.choice(words) for _ in range(rnd.randint(0, 8))]
        variant = rnd.choice(["", V, "A.B"])
        out = with_variant(args, variant)
        assert out.count("--qos-variant") == (1 if variant else 0)
        assert not any(a.startswith("--qos-variant=") for a in out)
        if variant:
            assert out[-2:] == ["--qos-variant", variant]


# --- process_alive (the launcher watch, N6) --------------------------------------------

def test_process_alive_for_this_process():
    assert process_alive(os.getpid())


def test_process_not_alive_once_ended_and_collected():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()                                        # collected: no zombie left behind
    assert not process_alive(p.pid)


@pytest.mark.parametrize("pid", [0, -1, -12345, 2**32 - 1, 2**32, 2**62, "123", None, 1.5, True])
def test_process_alive_rejects_nonsense(pid):
    assert process_alive(pid) is False


# --- Supervised ----------------------------------------------------------------------

def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def wait_for(cond, timeout, what):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def test_tells_the_app_its_launcher():
    s = Supervised(_py(f"import os; print(os.environ.get('{LAUNCHER_ENV}', 'none'))"), [], dict(os.environ))
    assert s.wait_exit(10) == 0
    assert s.lines == [str(os.getpid())]


def test_by_hand_adds_no_launcher():
    env = {k: v for k, v in os.environ.items() if k != LAUNCHER_ENV}
    s = Supervised(_py(f"import os; print(os.environ.get('{LAUNCHER_ENV}', 'none'))"), [], env,
                   watch_launcher=False)
    assert s.wait_exit(10) == 0
    assert s.lines == ["none"]


def test_own_id_replaces_an_inherited_one():
    env = dict(os.environ, **{LAUNCHER_ENV: "999"})      # e.g. inherited from an outer launcher
    s = Supervised(_py(f"import os; print(os.environ.get('{LAUNCHER_ENV}'))"), [], env)
    assert s.wait_exit(10) == 0
    assert s.lines == [str(os.getpid())]


def test_output_and_exit_code():
    s = Supervised(_py("import sys; print('a'); print('b'); sys.exit(3)"), [], dict(os.environ))
    assert s.wait_exit(10) == 3 and s.lines == ["a", "b"] and s.exited()


def test_interrupt_is_polite():
    code = ("import signal, sys, time\n"
            "signal.signal(signal.SIGINT, lambda *a: sys.exit(0))\n"
            "if hasattr(signal, 'SIGBREAK'): signal.signal(signal.SIGBREAK, lambda *a: sys.exit(0))\n"
            "print('ready', flush=True)\n"
            "for _ in range(300): time.sleep(0.1)\n")
    # Short sleeps, like fw.App's 0.2 s loop: on Windows a Ctrl-Break handler only
    # runs once Python regains control, and one long sleep(30) would hold it off.
    s = Supervised(_py(code), [], dict(os.environ))
    wait_for(lambda: "ready" in s.lines, 10, "the child to start")
    s.interrupt()
    assert s.wait_exit(5) == 0


def test_terminate_forces_a_process_that_ignores_ctrl_c():
    code = ("import signal, time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "if hasattr(signal, 'SIGBREAK'): signal.signal(signal.SIGBREAK, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(30)\n")
    s = Supervised(_py(code), [], dict(os.environ))
    wait_for(lambda: "ready" in s.lines, 10, "the child to start")
    s.terminate(grace=0.5)
    assert s.exited() and s.returncode != 0
