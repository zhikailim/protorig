"""
test_check.py — tests for `protorig check`.

Each test builds a small throwaway repo, breaks one thing on purpose, and
asserts that check reports it (and nothing unrelated). A clean repo must pass.

Run:  python -m pytest -q tests/
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "cli"))

import check  # noqa: E402
import repo   # noqa: E402

GOOD_SCENARIO = {
    "description": "a test scenario",
    "domain": 7,
    "nodes": {
        "win":  {"ip": "10.0.0.2", "os": "windows", "run": ["gui"]},
        "hpc":  {"ip": "10.0.0.3", "os": "linux", "run": ["agent", "monitor --strength 20"]},
        "ecu":  {"ip": "10.0.0.4", "external": True, "sim": "ecu_twin"},
    },
}


def _app(root: Path, kind: str, name: str, language: str, extra: dict | None = None):
    d = root / "apps" / kind / name
    d.mkdir(parents=True)
    (d / ("CMakeLists.txt" if language == "cpp" else "main.py")).write_text("# stub\n", encoding="utf-8")
    (d / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    (d / f"test_{name}.py").write_text("def test_x(): pass\n", encoding="utf-8")
    for fname, content in (extra or {}).items():
        (d / fname).parent.mkdir(parents=True, exist_ok=True)
        (d / fname).write_text(content, encoding="utf-8")
    return d


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A clean, passing repo; tests mutate it. Returns a helper namespace."""
    monkeypatch.setattr(repo, "ROOT", tmp_path)
    # These tests are about the repo, not this machine's installed Connext version.
    import check_contract
    monkeypatch.setattr(check_contract, "installed_connext_python", lambda: repo.CONNEXT_VERSION + ".0")
    _app(tmp_path, "tooling", "gui", "py")
    _app(tmp_path, "vehicle", "agent", "cpp")
    _app(tmp_path, "vehicle", "monitor", "cpp")
    _app(tmp_path, "sim", "ecu_twin", "py")
    (tmp_path / "external" / "ecu").mkdir(parents=True)
    (tmp_path / "external" / "ecu" / "external.yaml").write_text("topics: {}\n", encoding="utf-8")

    class F:
        root = tmp_path

        @staticmethod
        def scenario(data=None, name="demo-a", readme=True, raw=None):
            d = tmp_path / "scenarios" / name
            d.mkdir(parents=True, exist_ok=True)
            text = raw if raw is not None else yaml.safe_dump(data if data is not None else GOOD_SCENARIO)
            (d / "scenario.yaml").write_text(text, encoding="utf-8")
            if readme:
                (d / "README.md").write_text("# story\n", encoding="utf-8")
            return d

        @staticmethod
        def findings():
            return [f for _, fs in check.run_checks() for f in fs]

        @staticmethod
        def errors():
            return [f.message for f in F.findings() if f.severity == check.ERROR]

        @staticmethod
        def warnings():
            return [f.message for f in F.findings() if f.severity == check.WARN]

    return F


def _mutated(**changes):
    """GOOD_SCENARIO with nested changes, e.g. nodes__hpc__ip='x'."""
    import copy
    d = copy.deepcopy(GOOD_SCENARIO)
    for path, value in changes.items():
        keys = path.split("__")
        target = d
        for k in keys[:-1]:
            target = target[k]
        if value is DELETE:
            del target[keys[-1]]
        else:
            target[keys[-1]] = value
    return d


DELETE = object()


# --- the baseline ------------------------------------------------------------

def test_clean_repo_passes(fake):
    fake.scenario()
    assert fake.errors() == []
    assert fake.warnings() == []


def test_empty_repo_passes(fake):
    assert fake.errors() == []


# --- scenario file mistakes: each must produce an error mentioning the cause ---

SCENARIO_BREAKS = [
    ("duplicate IP",           _mutated(nodes__hpc__ip="10.0.0.2"),            "already used"),
    ("invalid IP",             _mutated(nodes__hpc__ip="10.0.0.300"),          "not a valid IPv4"),
    ("hostname instead of IP", _mutated(nodes__hpc__ip="hpc.local"),           "not a valid IPv4"),
    ("loopback IP",            _mutated(nodes__hpc__ip="127.0.0.1"),           "can't be reached"),
    ("missing IP",             _mutated(nodes__hpc__ip=DELETE),                "not a valid IPv4"),
    ("unknown app",            _mutated(nodes__hpc__run=["agent", "ghost"]),   "'ghost' not found"),
    ("sim twin in run:",       _mutated(nodes__hpc__run=["ecu_twin"]),         "is a sim twin"),
    ("non-sim as sim:",        _mutated(nodes__ecu__sim="gui"),                "must name an app in apps/sim"),
    ("missing sim app",        _mutated(nodes__ecu__sim="nope_twin"),          "'nope_twin' not found"),
    ("external with run:",     _mutated(nodes__ecu__run=["gui"]),              "external nodes run nothing"),
    ("sim: on managed node",   _mutated(nodes__hpc__sim="ecu_twin"),           "only for external nodes"),
    ("run: not a list",        _mutated(nodes__hpc__run="agent"),              "run: must be a list"),
    ("typo in node key",       _mutated(nodes__hpc__rn=["agent"]),             "unknown key 'rn'"),
    ("typo in scenario key",   _mutated(domian=3),                             "unknown key 'domian'"),
    ("unknown os",             _mutated(nodes__hpc__os="ubuntu"),              "os: must be one of"),
    ("domain out of range",    _mutated(domain=300),                           "domain must be"),
    ("domain as text",         _mutated(domain="0"),                           "domain must be"),
    ("domain as bool",         _mutated(domain=True),                          "domain must be"),
    ("no nodes",               _mutated(nodes={}),                             "at least one node"),
    ("node not a mapping",     _mutated(nodes__hpc="10.0.0.3"),                "must be a mapping"),
    ("bad node name",          _mutated(nodes={"HPC_1": {"ip": "10.0.0.9", "os": "linux", "run": ["agent"]}}),
                                                                               "node names must be"),
    ("bad app name in run",    _mutated(nodes__hpc__run=["Agent"]),            "not a valid app name"),
    ("unbalanced quote",       _mutated(nodes__hpc__run=['monitor --label "x']), "can't be parsed"),
]


@pytest.mark.parametrize("label,data,expected", SCENARIO_BREAKS, ids=[b[0] for b in SCENARIO_BREAKS])
def test_scenario_break_is_reported(fake, label, data, expected):
    fake.scenario(data)
    errors = fake.errors()
    assert any(expected in e for e in errors), f"{label}: expected '{expected}' in {errors}"


@pytest.mark.parametrize("run", [[], DELETE])
def test_node_with_no_demo_apps_is_only_a_warning(fake, run):
    """N2: a managed machine may run nothing but its node agent."""
    fake.scenario(_mutated(nodes__hpc__run=run))
    assert not any("hpc" in e for e in fake.errors()), fake.errors()
    assert any("no demo apps in run: yet" in w for w in fake.warnings())


def test_scenario_not_utf8_is_reported_not_crashing(fake):
    d = fake.root / "scenarios" / "demo-a"
    d.mkdir(parents=True)
    (d / "scenario.yaml").write_bytes("description: Größe\nnodes: {}\n".encode("cp1252"))   # saved as "ANSI"
    assert any("isn't UTF-8" in e for e in fake.errors())


def test_invalid_yaml_is_reported_not_crashing(fake):
    fake.scenario(raw="description: x\nnodes: [unclosed\n")
    assert any("not valid YAML" in e for e in fake.errors())


def test_yaml_that_is_a_list_is_reported(fake):
    fake.scenario(raw="- just\n- a list\n")
    assert any("must be a mapping" in e for e in fake.errors())


def test_missing_scenario_yaml(fake):
    (fake.root / "scenarios" / "demo-a").mkdir(parents=True)
    assert any("missing scenario.yaml" in e for e in fake.errors())


def test_bad_scenario_folder_name(fake):
    fake.scenario(GOOD_SCENARIO, name="Demo_A")
    assert any("folder name must be" in e for e in fake.errors())


# --- warnings: suspicious but not broken ---------------------------------------

WARNING_CASES = [
    ("no description",   _mutated(description=DELETE),              "no description"),
    ("mixed subnets",    _mutated(nodes__hpc__ip="10.0.9.3"),        "different /24 subnets"),
    ("unknown arch",     _mutated(nodes__hpc__arch="riscv"),         "arch: 'riscv'"),
    ("no sim twin",      _mutated(nodes__ecu__sim=DELETE),           "no sim: twin"),
    ("duplicate run",    _mutated(nodes__hpc__run=["agent", "agent"]), "listed twice"),
    ("leftover name:",   _mutated(name="other"),                     "the folder name is the scenario's name"),
]


@pytest.mark.parametrize("label,data,expected", WARNING_CASES, ids=[w[0] for w in WARNING_CASES])
def test_warning_is_reported_but_not_error(fake, label, data, expected):
    fake.scenario(data)
    assert any(expected in w for w in fake.warnings()), fake.warnings()
    assert fake.errors() == []


def test_missing_readme_warns(fake):
    fake.scenario(readme=False)
    assert any("no README.md" in w for w in fake.warnings())


def test_missing_external_folder_warns(fake):
    import shutil
    shutil.rmtree(fake.root / "external" / "ecu")
    fake.scenario()
    assert any("external/ecu/" in w for w in fake.warnings())


# --- app lookup: scenario-local apps ------------------------------------------

def test_scenario_local_app_is_found(fake):
    d = fake.scenario(_mutated(nodes__hpc__run=["agent", "special"]))
    local = d / "apps" / "vehicle" / "special"
    local.mkdir(parents=True)
    (local / "CMakeLists.txt").write_text("# stub\n", encoding="utf-8")
    assert fake.errors() == []


def test_same_name_in_two_kinds_is_ambiguous(fake):
    _app(fake.root, "tooling", "agent", "py")          # agent now in vehicle/ AND tooling/
    fake.scenario()
    assert any("more than one kind" in e for e in fake.errors())


# --- language policy -----------------------------------------------------------

def test_python_in_vehicle_app_is_rejected(fake):
    (fake.root / "apps" / "vehicle" / "agent" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    assert any("Python is not allowed" in e for e in fake.errors())


def test_python_hidden_in_subfolder_is_rejected(fake):
    sub = fake.root / "apps" / "vehicle" / "agent" / "src" / "deep"
    sub.mkdir(parents=True)
    (sub / "sneaky.py").write_text("x = 1\n", encoding="utf-8")
    assert any("sneaky.py" in e for e in fake.errors())


def test_tests_next_to_vehicle_app_are_allowed(fake):
    (fake.root / "apps" / "vehicle" / "agent" / "conftest.py").write_text("\n", encoding="utf-8")
    assert fake.errors() == []


def test_c_vehicle_app_is_allowed(fake):
    _app(fake.root, "vehicle", "brake_ctrl", "cpp", {"src/main.c": "int main(void){return 0;}\n"})
    assert fake.errors() == []


def test_vehicle_app_without_cmake_is_rejected(fake):
    (fake.root / "apps" / "vehicle" / "agent" / "CMakeLists.txt").unlink()
    assert any("need a CMakeLists.txt" in e for e in fake.errors())


def test_tooling_app_without_main_py_is_rejected(fake):
    (fake.root / "apps" / "tooling" / "gui" / "main.py").unlink()
    assert any("need a main.py" in e for e in fake.errors())


def test_bad_app_folder_name(fake):
    _app(fake.root, "tooling", "Result-GUI", "py")
    assert any("app folder names must be" in e for e in fake.errors())


def test_app_without_readme_or_tests_warns(fake):
    d = _app(fake.root, "tooling", "bare", "py")
    (d / "README.md").unlink()
    (d / "test_bare.py").unlink()
    w = fake.warnings()
    assert any("no README.md" in x for x in w) and any("no test_bare.py" in x for x in w)


# --- repo hygiene ---------------------------------------------------------------

@pytest.mark.parametrize("path", ["rti_license.dat", "external/tc397/license.dat", ".local/machine.yaml",
                                  "build/linux-x64/app", ".venv/bin/python"])
def test_tracked_forbidden_files_are_rejected(fake, path):
    subprocess.run(["git", "init", "-q", str(fake.root)], check=True)
    f = fake.root / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("secret\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(fake.root), "add", "-f", path], check=True)
    assert any(path in str(x) for x in fake.findings() if x.severity == check.ERROR)


def test_ignored_license_is_fine(fake):
    """A license that is present but git-ignored is exactly right."""
    subprocess.run(["git", "init", "-q", str(fake.root)], check=True)
    (fake.root / ".gitignore").write_text("*.dat\n", encoding="utf-8")
    (fake.root / "rti_license.dat").write_text("secret\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(fake.root), "add", "."], check=True)
    assert fake.errors() == []


# --- robustness: a crashing check becomes a finding, never a traceback ------------

def test_crashing_check_is_reported(fake, monkeypatch):
    def boom():
        raise RuntimeError("kaboom")
        yield  # pragma: no cover
    monkeypatch.setattr(check, "CHECKS", [("Exploding check", boom)])
    assert any("check crashed" in e and "kaboom" in e for e in fake.errors())


# --- fuzz: random garbage in scenario.yaml must never crash check ------------------

GARBAGE = [
    "", "~", "42", "'text'", "[]", "{}", "nodes: 5", "nodes: [1, 2]", "nodes: {a: }",
    "nodes: {a: {ip: [1,2]}}", "nodes: {a: {run: 5}}", "nodes: {a: {run: [null]}}",
    "nodes: {a: {run: [[x]]}}", "nodes: {a: {ip: 10.0.0.1, os: linux, run: ['']}}",
    "nodes: {a: {external: yes, sim: [x]}}", "nodes: {7: {ip: 1.2.3.4}}", "domain: .nan",
    "name: [a]", "description: {x: 1}", "\x00\x01", "nodes: {a: {ip: 999999999999}}",
    "nodes: {a: {arch: {x: 1}, ip: 10.0.0.1, os: linux, run: [a]}}",
    "nodes: {a: {os: [linux], ip: 10.0.0.1, run: [a]}}", "nodes: {a: {os: {x: 1}, ip: 10.0.0.1, run: [a]}}",
    "nodes: {a: {external: true, sim: {x: 1}, ip: 10.0.0.1}}",
]


@pytest.mark.parametrize("raw", GARBAGE)
def test_garbage_never_crashes(fake, raw):
    fake.scenario(raw=raw)
    findings = fake.findings()
    assert not any("check crashed" in f.message for f in findings), [str(f) for f in findings]


# --- end to end: the real launcher on the real repo ---------------------------------

# The launcher for this OS: Windows can't run the bash script `protorig` directly.
LAUNCHER = str(REPO / ("protorig.cmd" if os.name == "nt" else "protorig"))

def test_launcher_help_and_list():
    out = subprocess.run([LAUNCHER, "list"], capture_output=True, text=True, encoding="utf-8")
    # Every scenario folder is listed, whatever the scenarios are called (no demo names here).
    real = [d.name for d in (REPO / "scenarios").iterdir() if (d / "scenario.yaml").exists()]
    assert out.returncode == 0 and all(name in out.stdout for name in real), out.stdout
    out = subprocess.run([LAUNCHER, "--help"], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0 and "check" in out.stdout


def test_launcher_check_exit_code_matches_findings():
    out = subprocess.run([LAUNCHER, "check"], capture_output=True, text=True, encoding="utf-8")
    has_errors = "error(s)" in out.stdout
    assert out.returncode == (1 if has_errors else 0), out.stdout


def test_planned_verb_says_not_built():
    out = subprocess.run([LAUNCHER, "deploy", "hpc-pi"], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 3 and "not built yet" in out.stdout


def test_test_command_explains_missing_packages(monkeypatch, capsys):
    """`protorig test` on a Python without pytest says how to set up .venv, instead of a bare error."""
    import importlib.util
    import types
    import test as test_cmd
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda m, *a: None if m == "pytest" else real(m, *a))
    monkeypatch.setattr(test_cmd.subprocess, "call", lambda *a, **k: 0)   # if the guard is missing: fail fast, don't recurse
    assert test_cmd.main(types.SimpleNamespace(apps=[], k=None)) == 2
    out = capsys.readouterr().out
    assert "lacks: pytest" in out and "-m venv .venv" in out
