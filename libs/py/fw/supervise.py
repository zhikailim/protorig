"""
fw.supervise — the restart rules every protorig launcher follows (S1-S7).

Why: switching QoS variant needs new DDS entities, so the app must restart.
An app can't restart itself portably (Windows has no real exec), so it asks
its launcher instead:

    app                                   launcher (test harness, protorig run, node_agent)
    ---                                   --------
                                          sets PROTORIG_RESTART_FILE=<path>, starts app
    gets CMD_SET_QOS_VARIANT "X"
    writes "X" to <path>, closes DDS,
    exits with EXIT_RESTART (75)    ---->  after_exit(): reads the note, starts the
                                           app again with --qos-variant X

The launcher and the app are always on the same machine; nothing here crosses
the network. The protocol is language-neutral (an environment variable, a
file and an exit code), so C/C++ apps follow it too.

This module holds only decisions (no processes), so every launcher behaves the
same and the rules can be unit-tested. See tests/test_supervise.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RESTART_ENV = "PROTORIG_RESTART_FILE"   # where the app writes the requested variant
EXIT_RESTART = 75                        # "restart me" (EX_TEMPFAIL: try again)
EXIT_KILLED = 137                        # CMD_KILL_APP, like SIGKILL
STARTUP_GRACE = 5.0                      # a run shorter than this that fails = failed start (S6b)


def with_variant(args: list[str], variant: str) -> list[str]:
    """args with any --qos-variant removed, then --qos-variant <variant> added
    (nothing added for "" = the default QoS)."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--qos-variant":
            skip = True
            continue
        if a.startswith("--qos-variant="):
            continue
        out.append(a)
    return out + (["--qos-variant", variant] if variant else [])


def read_note(path: Path) -> str | None:
    """The variant the app asked for ("" = default), or None if missing/unreadable (S6d).
    The note is deleted once read, so a stale one is never reused."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        Path(path).unlink()
    except OSError:
        pass
    lines = text.splitlines()
    return lines[0].strip() if lines else ""


@dataclass
class Decision:
    restart: bool                 # start the app again?
    args: list[str] | None        # with these arguments
    message: str                  # one line for the launcher's log ("" = nothing to say)
    phase: str = "normal"         # the next run is: normal | switched | rollback


def after_exit(code: int, uptime: float, args: list[str], phase: str,
               previous_args: list[str] | None, note: Path) -> Decision:
    """What a launcher does when its app exits.

    code          the app's exit code
    uptime        seconds the app ran
    args          the arguments it ran with
    phase         what this run was: "normal", "switched" (first run on a new
                  variant) or "rollback" (back on the old variant after a failed switch)
    previous_args the arguments before the switch (for a rollback), else None
    note          the path given to the app in PROTORIG_RESTART_FILE
    """
    if code == EXIT_RESTART:
        variant = read_note(note)
        if variant is None:                                          # S6d
            return Decision(True, list(args), "restart requested but the note is missing or "
                            "unreadable: restarting with the same arguments", "normal")
        return Decision(True, with_variant(args, variant),
                        f"switching QoS variant to '{variant or 'default'}': restarting", "switched")

    failed_start = code not in (0, EXIT_KILLED) and uptime < STARTUP_GRACE
    if failed_start and phase == "switched" and previous_args is not None:   # S6b
        return Decision(True, list(previous_args),
                        f"the new QoS variant failed to start (exit {code}): rolling back", "rollback")
    if failed_start and phase == "rollback":                                  # S6c
        return Decision(False, None, f"the rollback failed too (exit {code}): giving up, app is down")
    return Decision(False, None, "")                                          # ended
