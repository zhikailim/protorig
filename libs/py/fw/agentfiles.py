"""
fw.agentfiles — the files through which `protorig agent stop` reaches a node
agent on the same machine (node_agent requirement N1a).

    build/<scenario>/<node>/agent.pid    written by the agent: {"pid": N, "started": T}
    build/<scenario>/<node>/agent.stop   written by `agent stop`: the PID to stop
    build/<scenario>/<node>/agent.log    the agent's output when started --background

Why files: `stop` must work the same on Windows and Linux (Windows can't send a
polite stop to a detached process), and must need local access to the machine
(a DDS stop command would let anyone on the network stop an agent).

Why a start time next to the PID: process IDs are reused (quickly, on Windows).
A PID alone could make `stop --force` kill an unrelated program after the agent
died. The start time tells "still the agent" from "same number, other process".

Shared by the agent (apps/tooling/node_agent) and the CLI (cli/agent.py). Every
reader tolerates missing, empty, huge or garbled files: they are hints, never
trusted blindly, and a bad file must never crash either side.
"""
from __future__ import annotations

import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from fw.supervise import process_alive

MAX_FILE = 4096           # bytes read from any of these files; a bigger one is garbage


def process_started(pid: int) -> int | None:
    """When process `pid` started, as an opaque number only good for comparing
    (Linux: clock ticks since boot; Windows: creation time in 100 ns units).
    None if it isn't running, isn't readable, or this OS has no way to tell."""
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= 0xFFFFFFFF:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k32.WaitForSingleObject.restype = wintypes.DWORD
        h = k32.OpenProcess(0x00100000 | 0x1000, False, pid)    # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            # An ended process stays readable while anyone holds a handle to it:
            # only a running one (wait times out) counts.
            if k32.WaitForSingleObject(h, 0) != 0x102:
                return None
            t = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
                return None
            return (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
        finally:
            k32.CloseHandle(h)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        # Field 22 (starttime). The name in field 2 may hold spaces and ")", so
        # count from the LAST ")": field 3 is the first one after it.
        return int(stat[stat.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None                                      # gone, or no /proc (not Linux)


def kill_process(pid: int) -> None:
    """End a process at once (no clean-up in it): SIGKILL, TerminateProcess on Windows."""
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


@dataclass
class PidRecord:
    pid: int
    started: int | None          # None: written where the start time couldn't be read


def _read_small(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_FILE + 1)
    except OSError:
        return None
    if len(raw) > MAX_FILE:
        return None
    return raw.decode("utf-8", errors="replace")


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temporary name, then rename: a reader sees the old file or the
    new one, never half of it. Windows refuses the rename while the target is
    open elsewhere for a moment, so try a few times."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            import time
            time.sleep(0.05)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass                                             # e.g. briefly locked on Windows: harmless leftover


class AgentFiles:
    """The pid, stop and log files of one node's agent."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.pid_file = self.folder / "agent.pid"
        self.stop_file = self.folder / "agent.stop"
        self.log_file = self.folder / "agent.log"

    @classmethod
    def of(cls, root: Path, scenario: str, node: str) -> "AgentFiles":
        return cls(Path(root) / "build" / scenario / node)

    # --- agent.pid ------------------------------------------------------------

    def claim(self, pid: int | None = None) -> None:
        """The agent: record its PID and start time (N1a step 2)."""
        pid = os.getpid() if pid is None else pid
        self.folder.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.pid_file, json.dumps({"pid": pid, "started": process_started(pid)}))

    def release(self, pid: int | None = None) -> None:
        """The agent, stopping cleanly: remove its files, but agent.pid only if it
        still names this process (a newer agent may have taken over)."""
        pid = os.getpid() if pid is None else pid
        rec = self.read_pid()
        if rec is not None and rec.pid == pid:
            _remove(self.pid_file)
        _remove(self.stop_file)

    def read_pid(self) -> PidRecord | None:
        text = _read_small(self.pid_file)
        if not text:
            return None
        try:
            data = json.loads(text)
            pid, started = data["pid"], data.get("started")
        except (ValueError, TypeError, KeyError):
            return None
        if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= 0xFFFFFFFF:
            return None
        if not isinstance(started, int) or isinstance(started, bool):
            started = None
        return PidRecord(pid, started)

    def identity(self, rec: PidRecord | None) -> str:
        """Is the process in agent.pid still that agent?
        "agent": running, same start time.  "gone": not running, or its PID now
        belongs to another process.  "unknown": running, but the start time
        can't be compared (another OS, or not readable)."""
        if rec is None or not process_alive(rec.pid):
            return "gone"
        if rec.started is None:
            return "unknown"
        now = process_started(rec.pid)
        if now is None:
            return "unknown"
        return "agent" if now == rec.started else "gone"

    # --- agent.stop -----------------------------------------------------------

    def request_stop(self, pid: int) -> None:
        """`agent stop`: ask the agent with this PID to stop (N1a step 3)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.stop_file, f"{pid}\n")

    def stop_request(self) -> int | None:
        """The PID in agent.stop; -1 if the file exists but holds no valid PID
        (so the agent can delete it); None if there is no file."""
        if not self.stop_file.exists():
            return None
        text = _read_small(self.stop_file)
        try:
            pid = int((text or "").strip())
        except ValueError:
            return -1
        return pid if 0 < pid <= 0xFFFFFFFF else -1

    def clear_stop(self) -> None:
        _remove(self.stop_file)

    def clear_stale(self) -> None:
        """No agent runs here any more: remove what an earlier one left."""
        _remove(self.pid_file)
        _remove(self.stop_file)

    def log_tail(self, lines: int = 15) -> list[str]:
        try:
            with open(self.log_file, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 16384))
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        return text.splitlines()[-lines:]
