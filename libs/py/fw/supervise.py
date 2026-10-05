"""
fw.supervise — run one app process for a launcher (the test harness,
`protorig run`, and node_agent), the same way everywhere.

What a launcher does with it (node_agent requirements N6, N13, N14):
  - starts the app in its own process group, so a Ctrl-C in the terminal
    reaches only the launcher, which then stops each app politely;
  - collects every line the app prints;
  - tells the app the launcher's process ID (PROTORIG_LAUNCHER_PID). fw.App
    checks it every second and stops cleanly once the launcher is gone, so a
    restarted launcher never finds duplicates of its apps still running;
  - stops it politely (Ctrl-C / Ctrl-Break) or forcibly.

QoS variant switches are NOT handled here any more: the node agent switches a
variant by stopping the app and starting it again with --qos-variant
(node_agent N13). with_variant() builds those arguments. The old "note"
protocol (the app asking its launcher for a restart) is gone.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Callable

LAUNCHER_ENV = "PROTORIG_LAUNCHER_PID"   # the launcher's process ID, watched by fw.App (N6)
EXIT_KILLED = 137                         # an app ended by a kill (like SIGKILL)
STARTUP_GRACE = 5.0                       # apps without a heartbeat: failing sooner = failed start (N13 V4)


def with_variant(args: list[str], variant: str) -> list[str]:
    """args with any --qos-variant removed, then --qos-variant <variant> added
    (nothing added for "" = the default QoS). Used by the agent for N13 V3."""
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


def process_alive(pid: int) -> bool:
    """True if a process with this ID exists on this machine. POSIX and Windows.

    Limits (accepted): a process ID can be reused by a new process after the old
    one ends, and on POSIX an ended process that its parent hasn't collected yet
    (a "zombie") still counts as alive. Launchers are ordinary programs started
    from a shell or service manager, which collect them, so neither matters here."""
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= 0xFFFFFFFF:
        return False                                   # process IDs are 32-bit on every OS we use
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Declared types: a HANDLE is 64-bit on 64-bit Windows; ctypes' default (int) would truncate it.
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        SYNCHRONIZE, QUERY_LIMITED = 0x00100000, 0x1000
        h = k32.OpenProcess(SYNCHRONIZE | QUERY_LIMITED, False, pid)
        if not h:
            return ctypes.get_last_error() == 5       # access denied: it exists, it's just not ours
        try:
            return k32.WaitForSingleObject(h, 0) == 0x102   # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)                                # signal 0: "does it exist?", sends nothing
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                                    # exists, owned by someone else
    except (OSError, OverflowError):
        return False
    return True


def _spawn_flags() -> dict:
    """Each app in its own process group: a Ctrl-C in the terminal reaches only
    the launcher, which then stops every app politely (interrupt())."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class Supervised:
    """One app process, started and watched for a launcher.

    cmd, args       the program and its arguments
    env             its environment
    watch_launcher  tell the app our process ID, so it stops if we die (N6).
                    False = like starting it by hand: nothing is added (env is
                    passed as given).
    on_line         called with every line the app prints

    .lines / .output   everything the app printed
    .proc              the process
    .exited()          True once it has ended
    .returncode        its exit code
    """

    def __init__(self, cmd: list[str], args: list[str], env: dict,
                 watch_launcher: bool = True,
                 on_line: Callable[[str], None] | None = None):
        self._env = dict(env)
        if watch_launcher:
            self._env[LAUNCHER_ENV] = str(os.getpid())
        self._on_line = on_line
        self.args = list(args)
        self.lines: list[str] = []
        self.returncode: int | None = None
        self._ended = threading.Event()
        self.proc = subprocess.Popen(list(cmd) + self.args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", env=self._env,
                                     **_spawn_flags())
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.lines.append(line)
            if self._on_line:
                self._on_line(line)
        self.returncode = self.proc.wait()
        self._ended.set()

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def exited(self) -> bool:
        return self._ended.is_set()

    def ended_within(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for it to end; True if it did."""
        return self._ended.wait(timeout)

    def wait_exit(self, timeout: float = 5.0) -> int:
        if not self._ended.wait(timeout):
            raise AssertionError(f"did not exit within {timeout}s\n--- output ---\n{self.output}")
        return self.returncode

    def interrupt(self) -> None:
        """Polite stop, like Ctrl-C: SIGINT on POSIX, Ctrl-Break on Windows (the
        app's own process group receives it; fw.App handles both)."""
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            except (OSError, ValueError):
                pass

    def kill(self) -> None:
        """Forced stop, at once (works on a hung app)."""
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass

    def terminate(self, grace: float = 5.0) -> int | None:
        """Stop for sure: polite first, forced after `grace` seconds."""
        self.interrupt()
        if not self._ended.wait(grace):
            self.kill()
            self._ended.wait(5)
        return self.returncode
