"""
test_run.py — `protorig run` (requirements U1-U10 and decisions 1-3; see docs/WORKFLOW.md).

Every test works on a copy of the repo whose only scenario is a sample built
here, so no test depends on a real demo's name or contents:

    desk  127.0.0.1   (a real address of this machine)  runs probe, "probe --label 'two words'"-style args
    hpc   203.0.113.9 (a documentation-only address, never local)  runs crasher, a missing app, a C++ app
    ecu   external, sim: tc397_twin (the real twin, copied with the repo)

Live tests start `run` as a real process and watch the apps over DDS.
"""
import os
import random
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "cli"))
sys.path.insert(0, str(REPO / "libs" / "py"))

import discovery  # noqa: E402

SAMPLE = "sample-scenario"
NOT_LOCAL = "203.0.113.9"      # TEST-NET-3: reserved for documentation, never on a real machine

SCENARIO = {
    "description": "sample for protorig run tests",
    "domain": 0,
    "nodes": {
        "desk": {"ip": "127.0.0.1", "os": "linux", "run": ["probe --label 'two words'"]},
        "hpc": {"ip": NOT_LOCAL, "os": "linux", "run": ["crasher", "missing_app", "cpp_thing"]},
        "ecu": {"ip": "203.0.113.10", "external": True, "sim": "tc397_twin"},
    },
}
PROBE = '''import sys
from fw.app import App
app = App("probe", "sample app for run tests")
label = app.arg("--label", "none", "text to print")
print(f"LABEL={label}", flush=True)
sys.exit(app.run())
'''
CRASHER = '''import sys, time
print("crasher: up", flush=True)
time.sleep(1.5)
sys.exit(3)
'''


@pytest.fixture
def rig(tmp_path):
    """A repo copy with only the sample scenario and its apps."""
    root = tmp_path / "repo"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "build", ".venv", ".local"))
    for d in (root / "scenarios").iterdir():
        if d.is_dir():
            shutil.rmtree(d)
    s = root / "scenarios" / SAMPLE
    s.mkdir(parents=True)
    (s / "scenario.yaml").write_text(yaml.safe_dump(SCENARIO))
    (s / "README.md").write_text("# sample\n")
    for name, code in (("probe", PROBE), ("crasher", CRASHER)):
        d = s / "apps" / "tooling" / name
        d.mkdir(parents=True)
        (d / "main.py").write_text(code)
    cpp = root / "apps" / "vehicle" / "cpp_thing"
    cpp.mkdir(parents=True)
    (cpp / "CMakeLists.txt").write_text("# stub\n")
    return root


def protorig(root: Path, *args, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(root / "cli" / "main.py"), *args],
                          capture_output=True, text=True, cwd=root, timeout=timeout)


class RunProcess:
    """`protorig run` as a live process, its output collected on a thread."""

    def __init__(self, root: Path, *args):
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
        self.proc = subprocess.Popen([sys.executable, str(root / "cli" / "main.py"), "run", *args],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=root, **kw)
        self.lines: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def ctrl_c(self, timeout=30) -> int:
        """What a person does in the terminal: Ctrl-C (Ctrl-Break on Windows)."""
        self.proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        return self.proc.wait(timeout=timeout)

    def kill(self):
        """Cleanup after a test, pass or fail: stop run the way a person would
        (Ctrl-C), so it stops its apps; force it only if that fails. Killing run
        outright would leave its apps running (they are in their own process groups)."""
        if self.proc.poll() is None:
            try:
                self.ctrl_c(timeout=20)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                self.proc.kill()
                self.proc.wait()


# --- U10 / U2 / U6 / decisions 1 and 3: the plan, seen through --dry-run ------------

def test_dry_run_sim_plans_every_node(rig):
    out = protorig(rig, "run", SAMPLE, "--sim", "--dry-run")
    assert out.returncode == 0, out.stdout + out.stderr
    o = out.stdout
    assert "would start 3 app(s)" in o
    assert "desk/probe:" in o and "--node desk" in o and "--scenario sample-scenario" in o
    assert "'two words'" in o                                   # U6: arguments passed through, intact
    assert "hpc/crasher:" in o
    assert "ecu/tc397_twin:" in o and "--node ecu" in o         # U2: the twin stands in for the ECU
    assert "hpc/missing_app: SKIPPED, no such app yet" in o     # decision 1
    assert "hpc/cpp_thing: SKIPPED, C/C++ app, not built yet" in o   # decision 3
    assert "<mask>SHMEM</mask>" in o                            # U3 shown
    assert not (rig / "build").exists(), "a dry run must write nothing"


def test_dry_run_node_shows_discovery_settings(rig):
    out = protorig(rig, "run", SAMPLE, "--node", "desk", "--dry-run")
    assert out.returncode == 0, out.stdout + out.stderr
    o = out.stdout
    assert "would start 1 app(s)" in o and "desk/probe" in o and "hpc/" not in o and "ecu/" not in o
    assert "<element>127.0.0.1</element>" in o                  # U4: bound to its own IP
    assert f"builtin.udpv4://{NOT_LOCAL}" in o and "builtin.udpv4://203.0.113.10" in o
    assert "<multicast_receive_addresses/>" in o


def test_app_arguments_go_to_the_app(rig):
    """Everything after --app <name> is the app's, even words that look like run's own."""
    o = protorig(rig, "run", "--scenario", SAMPLE, "--dry-run", "--app", "probe", "--label", "hello", "--sim", "x").stdout
    assert "would start 1 app(s)" in o and "--label hello --sim x" in o, o


def test_domain_override(rig):
    o = protorig(rig, "run", SAMPLE, "--sim", "--domain", "42", "--dry-run").stdout
    assert o.count("--domain 42") == 3


# --- U5 and argument errors: refused clearly, never a traceback ----------------------

@pytest.mark.parametrize("args,expected", [
    ([SAMPLE, "--node", "hpc"], f"doesn't have {NOT_LOCAL}"),      # U5: wrong machine
    ([SAMPLE, "--node", "ecu"], "is an external node"),
    ([SAMPLE, "--node", "nope"], "has no node 'nope'"),
    (["no-such-scenario", "--sim"], "no scenario 'no-such-scenario'"),
    ([SAMPLE], "choose one"),
    ([SAMPLE, "--sim", "--node", "desk"], "choose one"),
    (["--sim"], "which scenario?"),
    ([SAMPLE, "--sim", "--bogus"], "unknown argument"),
    (["--dry-run", "--app", "missing_app"], "SKIPPED, no such app yet"),
    (["--app", "missing_app"], "nothing to start"),
    (["--bogus", "--app", "probe"], "run's options go before --app"),
])
def test_refusals(rig, args, expected):
    out = protorig(rig, "run", *args)
    assert expected in out.stdout, out.stdout + out.stderr
    assert "Traceback" not in out.stdout + out.stderr
    if "SKIPPED" not in expected:
        assert out.returncode == 1


def test_node_run_writes_nothing_when_refused(rig):
    protorig(rig, "run", SAMPLE, "--node", "hpc")
    assert not (rig / "build" / SAMPLE / "hpc").exists()


# --- the generated discovery settings are valid for real Connext ------------------------

def _connext():
    dds = pytest.importorskip("rti.connextdds")
    if not os.environ.get("RTI_LICENSE_FILE"):
        pytest.skip("needs a Connext license")
    return dds


@pytest.mark.parametrize("kind", ["sim", "node"])
def test_generated_settings_load_in_connext(tmp_path, kind):
    dds = _connext()
    text = discovery.sim_qos() if kind == "sim" else discovery.node_qos(SCENARIO["nodes"], "desk")
    ElementTree.fromstring(text.encode())                        # well-formed XML (no "--" in comments)
    f = tmp_path / "node_qos.xml"
    f.write_text(text)
    q = REPO / "qos"
    p = dds.QosProvider(";".join(str(x) for x in (q / "base.xml", q / "topics.xml", q / "variants.xml", f)))
    pq = p.participant_qos_from_profile("protorig_node::Participant")
    assert pq.discovery_config.participant_liveliness_lease_duration == dds.Duration.from_seconds(10)  # Base inherited
    peers = list(pq.discovery.initial_peers)
    if kind == "sim":
        assert peers == ["builtin.shmem://"]
    else:
        assert f"4@builtin.udpv4://{NOT_LOCAL}" in peers and "4@builtin.udpv4://203.0.113.10" in peers
        assert not any("127.0.0.1" in x for x in peers)          # not itself
    dds.DomainParticipant(170, pq).close()                        # Connext accepts it


def test_peer_index_grows_with_apps():
    assert discovery.peer_index({"run": ["a"]}) == 4
    assert discovery.peer_index({"run": list("abcdefg")}) == 8
    assert discovery.peer_index({"run": "not a list"}) == 4


@pytest.mark.parametrize("ip,local", [("127.0.0.1", True), (NOT_LOCAL, False), ("", False),
                                      ("999.1.1.1", False), ("not an ip", False), (None, False),
                                      ("0.0.0.0", False), ("224.0.0.1", False), ("127.0.0.1 ", False)])
def test_is_local_ip(ip, local):
    assert discovery.is_local_ip(ip) is local


# --- live: U2, U3, U7, U8, U9, decision 2 -----------------------------------------------

def test_sim_live(rig, bus):
    """The whole sample on this machine: data flows, nothing uses the network, a
    crash is reported while the rest keep running, a variant switch restarts the
    probe through run, and Ctrl-C stops everything."""
    from fw import types as T
    from fw.testing import wait_for
    temps = bus.listen("Example Temperature")
    beats = bus.listen("_sys/NodeStatus")
    run = RunProcess(rig, SAMPLE, "--sim", "--domain", str(bus.domain))
    try:
        wait_for(lambda: temps.count() >= 5, 15, "the twin's data")
        wait_for(lambda: any(b.app == "probe" and b.node == "desk" for b in beats.all()), 15,
                 "the probe's heartbeat as node desk")                                  # U2
        wait_for(lambda: "hpc/crasher" in run.output and "CRASHED (exit 3)" in run.output, 10, "the crash report")
        assert "the other apps keep running" in run.output                              # decision 2
        n = temps.count()
        wait_for(lambda: temps.count() >= n + 10, 5, "the twin to keep publishing after the crash")

        # U3: every app participant advertises shared memory only: nothing reaches the network.
        p = bus.participant
        names = {}
        for h in p.discovered_participants():
            d = p.discovered_participant_data(h)
            names[d.participant_name.name] = {str(l.kind) for l in d.default_unicast_locators}
        for who in ("desk/probe", "ecu/tc397_twin"):
            assert names.get(who) == {"LocatorKind.SHMEM"}, names

        # U8: output prefixed per app; U6: arguments arrived intact.
        assert any(l.startswith("desk/probe") and "LABEL=two words" in l for l in run.lines), run.output
        assert run.output.count("[RTI LICENSE]") <= 1                                   # banner once

        # U7: a QoS variant switch restarts the probe through run.
        bus.command(type("A", (), {"name": "probe", "node": "desk"})(), T.Command.CMD_SET_QOS_VARIANT,
                    arg="Variant.Temperature.LongHistory")
        wait_for(lambda: any(b.app == "probe" and b.qos_variant == "Variant.Temperature.LongHistory"
                             for b in beats.all()), 15, "a heartbeat on the new variant")
        assert "[run] switching QoS variant" in run.output

        code = run.ctrl_c()                                                              # U9
        assert "stopping every app" in run.output
        assert "desk/probe stopped (exit 0)" in run.output and "ecu/tc397_twin stopped (exit 0)" in run.output
        assert code == 1          # the crasher failed, so run reports failure
    finally:
        run.kill()


def test_node_live_and_clean_exit(rig, bus):
    """--node on this machine's own address: its apps start with the generated
    settings, bound to that address; Ctrl-C with no crash exits 0."""
    from fw.testing import wait_for
    beats = bus.listen("_sys/NodeStatus")
    run = RunProcess(rig, SAMPLE, "--node", "desk", "--domain", str(bus.domain))
    try:
        wait_for(lambda: any(b.app == "probe" and b.node == "desk" for b in beats.all()), 15, "the probe")
        settings = rig / "build" / SAMPLE / "desk" / "node_qos.xml"
        assert settings.exists() and "127.0.0.1" in settings.read_text()
        assert "discovery: settings from node_qos.xml" in run.output
        locs = set()
        for h in bus.participant.discovered_participants():
            d = bus.participant.discovered_participant_data(h)
            if d.participant_name.name == "desk/probe":
                locs = {(str(l.kind), tuple(l.address)[-4:]) for l in d.default_unicast_locators}
        assert ("LocatorKind.UDPv4", (127, 0, 0, 1)) in locs
        assert all(k == "LocatorKind.SHMEM" or a == (127, 0, 0, 1) for k, a in locs), locs
        assert run.ctrl_c() == 0
    finally:
        run.kill()


def test_app_mode(rig, bus):
    """U1: one app, its own arguments passed through, node 'local'."""
    from fw.testing import wait_for
    beats = bus.listen("_sys/NodeStatus")
    run = RunProcess(rig, "--scenario", SAMPLE, "--domain", str(bus.domain), "--app", "probe", "--label", "hello")
    try:
        wait_for(lambda: any(b.app == "probe" and b.node == "local" for b in beats.all()), 15, "the probe")
        wait_for(lambda: "LABEL=hello" in run.output, 5, "its argument")
        assert "discovery: Connext defaults" in run.output
        assert run.ctrl_c() == 0
    finally:
        run.kill()


# --- fuzz: broken scenario files and odd arguments never crash run --------------------

GARBAGE = [
    "", "- a list\n", "nodes: 3\n", "nodes: {a: 1}\n", "nodes: {a: {run: 5}}\n",
    "nodes: {a: {ip: 127.0.0.1, run: ['x \"unclosed']}}\n", "nodes: [unclosed\n", "\x00\x01\x02",
    "nodes: {a: {external: true}}\n", "nodes: {a: {ip: 127.0.0.1, run: [null, 7, '']}}\n",
    "nodes: {a: {ip: [1, 2], run: [probe]}}\n", "domain: x\nnodes: {a: {ip: 127.0.0.1}}\n",
]


@pytest.mark.parametrize("text", GARBAGE)
@pytest.mark.parametrize("mode", [["--sim"], ["--node", "a"]])
def test_fuzz_scenario_files(rig, text, mode):
    (rig / "scenarios" / SAMPLE / "scenario.yaml").write_text(text)
    out = protorig(rig, "run", SAMPLE, *mode, "--dry-run")
    assert "Traceback" not in out.stdout + out.stderr, out.stdout + out.stderr


def test_fuzz_arguments(rig):
    rnd = random.Random(11)
    words = [SAMPLE, "--sim", "--node", "desk", "hpc", "--app", "probe", "--domain", "7", "-1", "x",
             "--dry-run", "--scenario", "", "--label", "ñ", "--", "--sim=1"]
    for _ in range(60):
        args = [rnd.choice(words) for _ in range(rnd.randint(0, 6))]
        if "--dry-run" not in args:
            args.append("--dry-run")          # never start apps from the fuzz loop
        out = protorig(rig, "run", *args, timeout=30)
        assert "Traceback" not in out.stdout + out.stderr, (args, out.stdout + out.stderr)
