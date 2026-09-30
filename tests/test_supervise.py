"""
test_supervise.py — the launcher's restart rules (libs/py/fw/supervise.py, S1-S7).

Pure logic, no processes or DDS: every launcher (test harness, protorig run,
node_agent) makes the same decision for the same exit.
"""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs" / "py"))

from fw.supervise import (EXIT_KILLED, EXIT_RESTART, STARTUP_GRACE,  # noqa: E402
                          after_exit, read_note, with_variant)

BASE = ["--node", "pi", "--domain", "0"]
V = "Variant.Temperature.LongHistory"


# --- with_variant ---------------------------------------------------------------------

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


# --- the note -------------------------------------------------------------------------

def test_note_is_read_then_deleted(tmp_path):
    n = tmp_path / "restart.note"
    n.write_text(V + "\n")
    assert read_note(n) == V and not n.exists()


@pytest.mark.parametrize("content,expected", [("\n", ""), ("", ""), (f"  {V}  \nextra\n", V)])
def test_note_contents(tmp_path, content, expected):
    n = tmp_path / "restart.note"
    n.write_text(content)
    assert read_note(n) == expected


def test_missing_or_garbage_note(tmp_path):
    assert read_note(tmp_path / "nope") is None
    bad = tmp_path / "bad.note"
    bad.write_bytes(b"\xff\xfe\x00garbage")
    assert read_note(bad) is None


# --- after_exit -----------------------------------------------------------------------

def test_restart_request_switches(tmp_path):                                  # S1, S4
    n = tmp_path / "restart.note"
    n.write_text(V)
    d = after_exit(EXIT_RESTART, 30, BASE, "normal", None, n)
    assert d.restart and d.args == BASE + ["--qos-variant", V] and d.phase == "switched"


def test_restart_request_without_note_keeps_arguments(tmp_path):              # S6d
    d = after_exit(EXIT_RESTART, 30, BASE, "normal", None, tmp_path / "missing")
    assert d.restart and d.args == BASE and d.phase == "normal" and "missing" in d.message


def test_failed_start_after_switch_rolls_back(tmp_path):                     # S6b
    new = BASE + ["--qos-variant", V]
    d = after_exit(1, 0.5, new, "switched", BASE, tmp_path / "n")
    assert d.restart and d.args == BASE and d.phase == "rollback"


def test_failed_rollback_gives_up(tmp_path):                                  # S6c
    d = after_exit(1, 0.5, BASE, "rollback", BASE, tmp_path / "n")
    assert not d.restart and "giving up" in d.message


@pytest.mark.parametrize("code,uptime,phase", [
    (0, 0.5, "switched"),                 # stopped on purpose right after a switch
    (EXIT_KILLED, 0.5, "switched"),       # killed on purpose
    (1, STARTUP_GRACE + 1, "switched"),   # ran fine for a while, then crashed: not a failed start
    (1, 0.5, "normal"),                   # crashed with no switch involved: not ours to handle
    (0, 100, "normal"),
])
def test_other_exits_end_the_app(tmp_path, code, uptime, phase):
    d = after_exit(code, uptime, BASE, phase, BASE, tmp_path / "n")
    assert not d.restart


def test_after_exit_fuzz(tmp_path):
    """Random exits in random states: never crashes, never restarts forever
    (a rollback that fails is always the end), args always a list when restarting."""
    rnd = random.Random(5)
    for _ in range(3000):
        n = tmp_path / "n"
        if rnd.random() < 0.5:
            n.write_text(rnd.choice(["", V, "\n\n", "x" * 200]))
        elif n.exists():
            n.unlink()
        d = after_exit(rnd.choice([0, 1, 2, 3, EXIT_RESTART, EXIT_KILLED, -9, 255]),
                       rnd.uniform(0, 10), list(BASE), rnd.choice(["normal", "switched", "rollback"]),
                       rnd.choice([None, list(BASE)]), n)
        if d.restart:
            assert isinstance(d.args, list)
        if d.phase == "rollback":
            assert d.restart


# --- S8: the note is written all or nothing; S9: nothing left behind -----------

from fw.supervise import new_note_path, write_note  # noqa: E402


def test_note_written_whole_and_no_temp_left(tmp_path):
    n = tmp_path / "restart.note"
    n.write_text("half-writ")                                   # garbage from before
    write_note(n, V)
    assert read_note(n) == V
    assert not list(tmp_path.glob("*.tmp")), "the temporary file must be renamed into place"


def test_note_never_half_written(tmp_path, monkeypatch):
    """If the final step fails (e.g. killed mid-write), the note is absent, never
    partial: the launcher then restarts unchanged (S6d) instead of reading garbage."""
    import os
    n = tmp_path / "restart.note"
    def boom(*a, **k):
        raise OSError("killed here")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_note(n, V)
    assert not n.exists()


def test_unwritable_note_raises(tmp_path):
    with pytest.raises(OSError):
        write_note(tmp_path / "no" / "such" / "restart.note", V)


def test_note_paths_are_unique():
    paths = {new_note_path("same_app") for _ in range(50)}
    assert len(paths) == 50 and all(p.parent.is_dir() for p in paths)
    for p in paths:
        p.parent.rmdir()
