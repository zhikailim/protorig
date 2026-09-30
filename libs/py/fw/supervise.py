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


def new_note_path(app: str) -> Path:
    """S2/S9: a fresh, unique note path for one app launch, in its own new
    temporary folder. Unique by construction, so two apps (or two launchers)
    can never share a note. The launcher removes the folder when the app ends
    for good (Supervised(..., own_note_dir=True))."""
    import tempfile
    return Path(tempfile.mkdtemp(prefix=f"protorig-{app}-")) / "restart.note"


def write_note(path, variant: str) -> None:
    """S8: the app's side. All or nothing: write a temporary file next to the
    note, then rename it into place. A rename is atomic on Windows and POSIX,
    so the launcher sees the whole note or none (and none is covered by S6d),
    never a half-written variant name. Raises OSError if it can't be written (S6a)."""
    import os
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(variant + "\n", encoding="utf-8")
    os.replace(tmp, path)


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


# ---------------------------------------------------------------------------
# Supervised: one app process, restarted by the rules above. Shared by the test
# harness (fw.testing) and `protorig run`, so both behave identically (U7).
# ---------------------------------------------------------------------------

import os as _os
import signal as _signal
import subprocess as _subprocess
import threading as _threading
import time as _time
from typing import Callable as _Callable


def _spawn_flags() -> dict:
    """Start each app in its own process group, so the launcher decides how and
    when it stops: a Ctrl-C in the terminal reaches only the launcher, which then
    stops every app politely (interrupt()). Same behaviour on Windows and POSIX."""
    if _os.name == "nt":
        return {"creationflags": _subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class Supervised:
    """One app process, supervised: restarted on a QoS-variant switch (S1-S7).

    cmd, args   the program and its arguments (args change on a variant switch)
    env         its environment; RESTART_ENV is set here when note is given
    note        the restart-note path, or None = unsupervised (like a person
                starting it by hand: it refuses variant switches, rule S3)
    on_line     called with every line the app prints (default: kept in .lines only)
    on_event    called with every launcher decision ("switching QoS variant ...")

    .lines / .output   everything the app printed, across restarts, plus
                       "[launcher] ..." lines for what the launcher did
    .proc              the current process
    .restarts          how many times it was restarted
    .exited()          True once it has ended for good (not during a restart)
    .returncode        its final exit code
    """

    def __init__(self, cmd: list[str], args: list[str], env: dict, note: Path | None,
                 on_line: _Callable[[str], None] | None = None,
                 on_event: _Callable[[str], None] | None = None,
                 own_note_dir: bool = False):
        self._cmd, self._args, self._note = list(cmd), list(args), note
        self._own_note_dir = own_note_dir and note is not None   # S9: remove its folder at the end
        self._env = dict(env)
        self._env.pop(RESTART_ENV, None)
        if note is not None:
            self._env[RESTART_ENV] = str(note)
        self._on_line, self._on_event = on_line, on_event
        self.lines: list[str] = []
        self.restarts = 0
        self.returncode: int | None = None
        self._stopping = False
        self._ended = _threading.Event()
        self._lock = _threading.Lock()
        self.proc = self._spawn(self._args)
        _threading.Thread(target=self._supervise, daemon=True).start()

    def _spawn(self, args: list[str]) -> _subprocess.Popen:
        proc = _subprocess.Popen(self._cmd + args, stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", env=self._env, **_spawn_flags())
        _threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        return proc

    def _read(self, proc):
        for line in proc.stdout:
            line = line.rstrip("\n")
            self.lines.append(line)
            if self._on_line:
                self._on_line(line)

    def _event(self, msg: str):
        self.lines.append(f"[launcher] {msg}")
        if self._on_event:
            self._on_event(msg)

    def _supervise(self):
        args, phase, previous = self._args, "normal", None
        while True:
            started = _time.monotonic()
            code = self.proc.wait()
            with self._lock:
                if self._stopping or self._note is None:       # stopped by the launcher, or unsupervised
                    break
                d = after_exit(code, _time.monotonic() - started, args, phase, previous, self._note)
                if d.message:
                    self._event(d.message)
                if not d.restart:
                    break
                previous = args if d.phase == "switched" else previous
                args, phase = d.args, d.phase
                self.restarts += 1
                self.proc = self._spawn(args)
        self.returncode = code
        if self._own_note_dir:                       # S9: leave nothing behind
            import shutil
            shutil.rmtree(self._note.parent, ignore_errors=True)
        self._ended.set()

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def exited(self) -> bool:
        return self._ended.is_set()

    def ended_within(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for it to end for good; True if it did."""
        return self._ended.wait(timeout)

    def wait_exit(self, timeout: float = 5.0) -> int:
        if not self._ended.wait(timeout):
            raise AssertionError(f"did not exit within {timeout}s\n--- output ---\n{self.output}")
        return self.returncode

    def interrupt(self) -> None:
        """Polite stop, like Ctrl-C: SIGINT on POSIX, Ctrl-Break on Windows (the
        app's own process group receives it; fw.App handles both). No restart."""
        with self._lock:
            self._stopping = True
            proc = self.proc
        if proc.poll() is None:
            try:
                proc.send_signal(_signal.CTRL_BREAK_EVENT if _os.name == "nt" else _signal.SIGINT)
            except (OSError, ValueError):
                pass

    def terminate(self, grace: float = 5.0) -> int | None:
        """Stop for sure: polite first, then forced after `grace` seconds. No restart."""
        self.interrupt()
        proc = self.proc
        try:
            proc.wait(timeout=grace)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        self._ended.wait(5)
        return self.returncode
