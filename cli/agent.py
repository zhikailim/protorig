"""
agent.py — `protorig agent start|stop|status` (node_agent requirements N1, N1a).

    protorig agent start  <scenario> [--node N] [--background] [--domain D]
    protorig agent stop   <scenario> [--node N] [--force]
    protorig agent status <scenario> [--node N] [--domain D]

Without --node, start and stop act for this machine's node, found from its IP
(run.md U13, the same rule as `protorig run`); status reports every managed
node. The agent itself is apps/tooling/node_agent; this file only starts it,
asks it to stop (through build/<scenario>/<node>/agent.stop, see
fw.agentfiles), and listens to it over DDS.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time

import discovery
import repo
from run import RunError, load_scenario, this_node

sys.path.insert(0, str(repo.ROOT / "libs" / "py"))
from fw.agentfiles import AgentFiles, kill_process   # noqa: E402
from fw.app import DEFAULT_PROFILE, NODE_PROFILE, NODE_QOS_ENV, QOS_FILES   # noqa: E402
from fw.supervise import LAUNCHER_ENV, process_alive   # noqa: E402

AGENT_MAIN = repo.ROOT / "apps" / "tooling" / "node_agent" / "main.py"
LISTEN = 3.0            # seconds to listen for agents on DDS (N1)
UP_TIMEOUT = 15.0       # start --background: the agent's first heartbeat within this (N1a step 1)
STOP_GRACE = 10.0       # the agent's default --stop-grace
STOP_EXTRA = 5.0        # stop waits STOP_GRACE + this (N1a step 5)
POLL = 0.2


# ------------------------------------------------------------------ DDS: listening only

class Listener:
    """A quiet participant (no heartbeat, writes nothing) that reads agents'
    heartbeats and their app rows, with a node's generated discovery settings."""

    def __init__(self, domain: int, settings, name: str):
        import rti.connextdds as dds
        from fw import types as T
        self._dds = dds
        files = [str(repo.ROOT / "qos" / f) for f in QOS_FILES if (repo.ROOT / "qos" / f).exists()] + [str(settings)]
        prov = dds.QosProvider(";".join(files))
        prov.default_profile = DEFAULT_PROFILE
        pqos = prov.participant_qos_from_profile(NODE_PROFILE)
        pqos.participant_name.name = name
        self.participant = dds.DomainParticipant(domain, pqos)
        sub = dds.Subscriber(self.participant)
        self._hb = dds.DataReader(sub, dds.Topic(self.participant, "_sys/NodeStatus", T.NodeStatus),
                                  prov.get_topic_datareader_qos("_sys/NodeStatus"))
        self._rows = dds.DataReader(sub, dds.Topic(self.participant, "_sys/AppState", T.AppState),
                                    prov.get_topic_datareader_qos("_sys/AppState"))

    def _alive(self, reader):
        alive = self._dds.InstanceState.ALIVE
        return [d for d, info in reader.read() if info.valid and info.state.instance_state == alive]

    def agents(self) -> set[str]:
        """Nodes whose agent is heartbeating now."""
        return {d.node for d in self._alive(self._hb) if d.app == "node_agent"}

    def app_rows(self, node: str) -> list:
        """The current rows of a node's apps (latest per app; a dead or stopped
        agent's rows are left out)."""
        latest = {}
        for d in self._alive(self._rows):
            if d.node == node and d.app != "node_agent":
                latest[d.app] = d
        return [latest[k] for k in sorted(latest)]

    def close(self) -> None:
        """Readers first, then the participant (the order Connext requires)."""
        for r in (self._hb, self._rows):
            try:
                r.close()
            except self._dds.Error:
                pass
        self.participant.close()


def wait_for(cond, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while True:
        if cond():
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(POLL)


# ------------------------------------------------------------------ helpers

def domain_of(args, data: dict) -> int:
    d = args.domain if args.domain is not None else data.get("domain", 0)
    if not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 232:    # Connext's domain range
        raise RunError(f"domain {d!r} is not a DDS domain (0 to 232)")
    return d


BANNER = ("[RTI LICENSE]", "Expires on", "Please contact support@rti.com")


def flush_c_output() -> None:
    """Flush the C runtime's output buffers (fflush(NULL)). Connext's C core prints
    through them; on Windows they reach the terminal only at exit otherwise, long
    after the capture below has ended. Best effort: never fails."""
    import ctypes
    names = ("ucrtbase", "msvcrt") if os.name == "nt" else (None,)
    for name in names:
        try:
            lib = ctypes.CDLL(name) if name is None else getattr(ctypes.cdll, name)
            lib.fflush(None)
        except (OSError, AttributeError):
            pass


def without_banner(fn):
    """Run fn() with this process's own output captured at the OS level (Connext's
    C core prints straight to it), then pass on everything except Connext's
    evaluation-license banner, as `run` does for apps. Its error messages still show."""
    sys.stdout.flush()
    with tempfile.TemporaryFile() as tmp:
        saved = os.dup(1)
        os.dup2(tmp.fileno(), 1)
        try:
            return fn()
        finally:
            flush_c_output()
            os.dup2(saved, 1)
            os.close(saved)
            tmp.seek(0)
            for line in tmp.read().decode("utf-8", errors="replace").splitlines():
                if line.strip() and not line.startswith(BANNER):
                    print(line, flush=True)


def open_listener(domain: int, settings, name: str) -> "Listener":
    try:
        return without_banner(lambda: Listener(domain, settings, name))
    except Exception as e:                    # Connext raises several kinds; never a traceback
        raise RunError(f"couldn't join DDS domain {domain}: {type(e).__name__}: {e}") from None


def write_settings(files: AgentFiles, data: dict, node: str, name: str):
    """This node's generated discovery settings (as `run`), in its build folder."""
    path = files.folder / name
    files.folder.mkdir(parents=True, exist_ok=True)
    path.write_text(discovery.node_qos(data["nodes"], node), encoding="utf-8")
    return path


def agent_env(settings) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo.ROOT / "libs" / "py") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"                   # its log and console take any language (as `run`)
    env["PYTHONIOENCODING"] = "utf-8"
    env[NODE_QOS_ENV] = str(settings)
    env.pop(LAUNCHER_ENV, None)               # an agent outlives the command that started it (N1a step 2)
    return env


def detached() -> dict:
    """N1a step 1: the agent keeps running when this terminal closes. Windows: its
    own hidden console (CREATE_NO_WINDOW), which its apps share, so it can still
    stop them politely with Ctrl-Break; POSIX: its own session, no terminal."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000}
    return {"start_new_session": True}


def say(text: str) -> None:
    print(f"protorig agent: {text}", flush=True)


# ------------------------------------------------------------------ start

def start(args, data: dict, node: str) -> int:
    files = AgentFiles.of(repo.ROOT, args.scenario, node)
    rec = files.read_pid()
    if files.identity(rec) != "gone":                                   # N1a step 7
        raise RunError(f"an agent for {node} is already running (process {rec.pid}). "
                       f"Stop it first: protorig agent stop {args.scenario}")
    files.clear_stale()                                                 # leftovers of an agent that died
    settings = write_settings(files, data, node, "node_qos.xml")
    domain = domain_of(args, data)

    listener = open_listener(domain, settings, f"{node}/agent_start")
    try:
        if wait_for(lambda: node in listener.agents(), LISTEN):         # N1: one agent per node, rig-wide
            raise RunError(f"an agent for {node} is already running on the rig (its heartbeat was heard on "
                           f"domain {domain}): only one may run. Stop that one first")
        cmd = [sys.executable, str(AGENT_MAIN), "--scenario", args.scenario, "--node", node, "--domain", str(domain)]
        env = agent_env(settings)
        if not args.background:
            listener.close()
            listener = None
            return foreground(cmd, env, node)
        return background(cmd, env, node, files, listener)
    finally:
        if listener is not None:
            listener.close()


def foreground(cmd: list[str], env: dict, node: str) -> int:
    """The agent in this terminal: Ctrl-C reaches it (same terminal) and it stops
    its apps; this command only waits for it."""
    say(f"agent for {node} runs in this terminal; Ctrl-C stops it and its apps")
    for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, signal.SIG_IGN)
    proc = subprocess.Popen(cmd, env=env)
    return proc.wait()


def background(cmd: list[str], env: dict, node: str, files: AgentFiles, listener: Listener) -> int:
    """N1a step 1: return only once the agent's first heartbeat is seen.

    The agent's process ID is the one it writes to agent.pid, never Popen's: on
    Windows a virtual environment's python.exe is a small launcher that starts
    the real Python as a child, so Popen's ID is the launcher's."""
    with open(files.log_file, "wb") as log_out:
        proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=log_out,
                                stderr=subprocess.STDOUT, **detached())
    end = time.monotonic() + UP_TIMEOUT
    while time.monotonic() < end:
        if node in listener.agents():
            rec = files.read_pid()
            say(f"agent for {node} is up (process {rec.pid if rec else proc.pid}, log: {files.log_file})")
            return 0
        if proc.poll() is not None:
            return failed_start(files, f"the agent ended at once (exit {proc.returncode})")
        time.sleep(POLL)
    take_down(proc, files)                                              # not up: take it down again
    return failed_start(files, f"no heartbeat from the agent within {UP_TIMEOUT:g} s; stopped it again")


def take_down(proc: subprocess.Popen, files: AgentFiles) -> None:
    """Stop an agent that never came up: politely through agent.stop if it wrote
    its PID, else (or if that fails) by force, the launcher included."""
    rec = files.read_pid()
    if rec is not None and files.identity(rec) == "agent":
        files.request_stop(rec.pid)
        if wait_for(lambda: not process_alive(rec.pid), STOP_GRACE + STOP_EXTRA):
            files.release(rec.pid)
        else:
            kill_process(rec.pid)
            files.release(rec.pid)
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def failed_start(files: AgentFiles, why: str) -> int:
    say(f"{why}. Last lines of {files.log_file}:")
    for line in files.log_tail():
        print(f"    {line}")
    return 1


# ------------------------------------------------------------------ stop

def stop(args, node: str) -> int:
    files = AgentFiles.of(repo.ROOT, args.scenario, node)
    rec = files.read_pid()
    ident = files.identity(rec)
    if ident == "gone":                                                 # N1a step 3
        files.clear_stale()
        say(f"no agent running for {node}")
        return 0
    if args.force:                                                      # N1a step 6
        if ident != "agent":
            raise RunError(f"can't confirm process {rec.pid} is still the agent (its start time can't be "
                           "read here), so it isn't killed. End it by hand if you're sure")
        kill_process(rec.pid)
        wait_for(lambda: not process_alive(rec.pid), 5.0)
        files.release(rec.pid)
        say(f"killed the agent for {node} (process {rec.pid}); its apps stop themselves within about 1 s")
        return 0
    files.request_stop(rec.pid)                                         # N1a steps 3-5
    say(f"asked the agent for {node} (process {rec.pid}) to stop; waiting for it to stop its apps ...")
    limit = STOP_GRACE + STOP_EXTRA
    end = time.monotonic() + limit
    next_note = time.monotonic() + 5.0
    while time.monotonic() < end:
        if not process_alive(rec.pid):
            files.release(rec.pid)
            say(f"agent for {node} stopped")
            return 0
        if time.monotonic() >= next_note:
            say("still stopping ...")
            next_note += 5.0
        time.sleep(POLL)
    say(f"the agent for {node} didn't stop within {limit:g} s. "
        f"`protorig agent stop {args.scenario} --force` kills it")
    return 1


# ------------------------------------------------------------------ status

def status(args, data: dict) -> int:
    managed = [n for n, s in data["nodes"].items() if isinstance(s, dict) and not s.get("external")]
    if args.node is not None and args.node not in managed:
        raise RunError(f"scenario '{args.scenario}' has no managed node '{args.node}' (managed: {', '.join(managed)})")
    targets = [args.node] if args.node else managed
    me = this_node(args.scenario, data, None, prog="agent")             # this machine's settings (N1)
    files = AgentFiles.of(repo.ROOT, args.scenario, me)
    settings = write_settings(files, data, me, "status_qos.xml")       # never the agent's own file
    listener = open_listener(domain_of(args, data), settings, f"{me}/agent_status")
    try:
        if wait_for(lambda: set(targets) <= listener.agents(), LISTEN):
            time.sleep(0.5)                                             # their rows arrive just after
        alive = listener.agents()
        for n in targets:
            if n not in alive:
                print(f"{n}: agent MISSING (not started, unreachable, or on another domain)")
                continue
            rows = listener.app_rows(n)
            apps = ", ".join(f"{r.app} {r.state.name.removeprefix('APP_')}" + (f" ({r.detail})" if r.detail else "")
                             for r in rows) or "none"
            print(f"{n}: agent alive; apps: {apps}")
        return 0 if all(n in alive for n in targets) else 1
    finally:
        listener.close()


# ------------------------------------------------------------------ entry point

def main(args, extra: list[str]) -> int:
    try:
        if extra:
            raise RunError(f"unknown argument(s): {' '.join(extra)}")
        if args.background and args.action != "start":
            raise RunError("--background is for start")
        if args.force and args.action != "stop":
            raise RunError("--force is for stop")
        data = load_scenario(args.scenario)
        if args.action == "status":
            return status(args, data)
        node = this_node(args.scenario, data, args.node, prog="agent")
        return start(args, data, node) if args.action == "start" else stop(args, node)
    except RunError as e:
        say(str(e))
        return 1
