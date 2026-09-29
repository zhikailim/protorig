"""
check.py — `protorig check`: static checks, no DDS, runs in seconds.

Each check is a small function that looks at the repo and yields Findings.
Adding a check = writing one function and decorating it with @check.
Later steps add more (data contract, QoS, flash locks) in the same way.

Severity:
  ERROR  something is wrong and will break a build, a run or the demo
  WARN   probably a mistake, but nothing breaks
"""
from __future__ import annotations

import ipaddress
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterator

import repo

ERROR, WARN = "ERROR", "WARN"

NODE_KEYS = {"ip", "os", "arch", "run", "external", "sim"}
SCENARIO_KEYS = {"name", "description", "domain", "nodes"}
KNOWN_OS = {"linux", "windows", "qnx", "android", "none"}
KNOWN_ARCH = {"x86_64", "aarch64", "armv7", "tricore"}


@dataclass
class Finding:
    severity: str
    where: str        # e.g. "scenarios/temperature-skeleton" or "apps/vehicle/foo"
    message: str

    def __str__(self) -> str:
        return f"{self.severity:<5} {self.where}: {self.message}"


CHECKS: list[tuple[str, Callable[[], Iterator[Finding]]]] = []


def check(title: str):
    """Register a check. Checks run in the order they are defined."""
    def wrap(fn):
        CHECKS.append((title, fn))
        return fn
    return wrap


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

@check("Scenario files")
def check_scenarios() -> Iterator[Finding]:
    for s in repo.load_scenarios():
        where = f"scenarios/{s.name}"
        if not repo.SCENARIO_NAME.match(s.name):
            yield Finding(ERROR, where, "folder name must be lowercase letters, digits and '-' (e.g. temperature-skeleton)")
        if s.error:
            yield Finding(ERROR, where, s.error)
            continue
        d = s.data
        for k in sorted(set(d) - SCENARIO_KEYS):
            yield Finding(ERROR, where, f"unknown key '{k}' (allowed: {', '.join(sorted(SCENARIO_KEYS))})")
        if d.get("name") != s.name:
            yield Finding(ERROR, where, f"name: '{d.get('name')}' must match the folder name '{s.name}'")
        if not d.get("description"):
            yield Finding(WARN, where, "no description: it is what `protorig list` shows")
        dom = d.get("domain")
        if not isinstance(dom, int) or isinstance(dom, bool) or not 0 <= dom <= 232:
            yield Finding(ERROR, where, f"domain must be a whole number 0-232, got {dom!r}")
        if not (s.path / "README.md").exists():
            yield Finding(WARN, where, "no README.md: the scenario's story and acceptance tests belong there")
        nodes = d.get("nodes")
        if not isinstance(nodes, dict) or not nodes:
            yield Finding(ERROR, where, "nodes: must list at least one node")
            continue
        yield from _check_nodes(s, nodes)


def _check_nodes(s: repo.Scenario, nodes: dict) -> Iterator[Finding]:
    apps = repo.find_apps(s.name)
    seen_ips: dict[str, str] = {}
    subnets = set()
    for node, spec in nodes.items():
        where = f"scenarios/{s.name} node '{node}'"
        if not repo.NODE_NAME.match(str(node)):
            yield Finding(ERROR, where, "node names must be lowercase letters, digits and '-' (e.g. hpc-pi)")
        if not isinstance(spec, dict):
            yield Finding(ERROR, where, "must be a mapping like { ip: ..., os: ..., run: [...] }")
            continue
        for k in sorted(set(spec) - NODE_KEYS):
            yield Finding(ERROR, where, f"unknown key '{k}' (allowed: {', '.join(sorted(NODE_KEYS))})")

        # --- address -------------------------------------------------------
        ip = str(spec.get("ip", ""))
        try:
            addr = ipaddress.IPv4Address(ip)
            subnets.add(ipaddress.IPv4Network(f"{ip}/24", strict=False))
            if ip in seen_ips:
                yield Finding(ERROR, where, f"IP {ip} is already used by node '{seen_ips[ip]}'")
            seen_ips[ip] = node
            if addr.is_loopback or addr.is_unspecified:
                yield Finding(ERROR, where, f"IP {ip} can't be reached by other nodes")
        except ValueError:
            yield Finding(ERROR, where, f"ip: '{ip}' is not a valid IPv4 address")

        external = spec.get("external") is True
        os_ = spec.get("os", "none" if external else None)
        if not isinstance(os_, str) or os_ not in KNOWN_OS:
            yield Finding(ERROR, where, f"os: must be one of {', '.join(sorted(KNOWN_OS))}, got {os_!r}")
        arch = spec.get("arch", "x86_64")
        if not isinstance(arch, str) or arch not in KNOWN_ARCH:
            yield Finding(WARN, where, f"arch: '{arch}' is not one of {', '.join(sorted(KNOWN_ARCH))}; "
                                       "cross-compiling will need a toolchain for it")

        # --- external nodes: nothing runs there, optional sim twin ---------
        if external:
            if spec.get("run"):
                yield Finding(ERROR, where, "external nodes run nothing from this repo; remove run:")
            sim = spec.get("sim")
            if sim is None:
                yield Finding(WARN, where, "no sim: twin, so --sim runs will have nothing publishing for this node")
            else:
                yield from _resolve(apps, str(sim), where, want_kind="sim")
            if not (repo.ROOT / "external" / str(node)).is_dir():
                yield Finding(WARN, where, f"no external/{node}/ folder with its build/flash notes")
            continue
        if "sim" in spec:
            yield Finding(ERROR, where, "sim: is only for external nodes")

        # --- managed nodes: the apps they run --------------------------------
        run = spec.get("run")
        if not isinstance(run, list) or not run:
            yield Finding(ERROR, where, "run: must list at least one app (or mark the node external: true)")
            continue
        seen_entries = set()
        for entry in run:
            try:
                parts = shlex.split(str(entry))
            except ValueError as e:
                yield Finding(ERROR, where, f"run entry '{entry}' can't be parsed: {e}")
                continue
            if not parts:
                yield Finding(ERROR, where, "empty run entry")
                continue
            if str(entry) in seen_entries:
                yield Finding(WARN, where, f"'{entry}' is listed twice")
            seen_entries.add(str(entry))
            yield from _resolve(apps, parts[0], where, not_kind="sim")

    if len(subnets) > 1:
        yield Finding(WARN, f"scenarios/{s.name}",
                      "nodes are on different /24 subnets: " + ", ".join(sorted(map(str, subnets)))
                      + " (unicast discovery still works if routed, but this is often a typo)")


def _resolve(apps, name: str, where: str, want_kind: str | None = None,
             not_kind: str | None = None) -> Iterator[Finding]:
    """An app name used by a scenario must exist exactly once, of the right kind."""
    if not repo.APP_NAME.match(name):
        yield Finding(ERROR, where, f"'{name}' is not a valid app name (lowercase letters, digits, '_')")
        return
    found = apps.get(name, [])
    if not found:
        kind_hint = f"apps/{want_kind}/" if want_kind else "apps/vehicle|tooling/"
        yield Finding(ERROR, where, f"app '{name}' not found (looked in the scenario's apps/ and {kind_hint})")
        return
    # Scenario-local apps shadow shared ones on purpose; only same-scope duplicates are ambiguous.
    top_scope = found[0].scope
    same_scope = [a for a in found if a.scope == top_scope]
    if len(same_scope) > 1:
        kinds = ", ".join(f"{a.kind}/" for a in same_scope)
        yield Finding(ERROR, where, f"app '{name}' exists in more than one kind ({kinds}); names must be unique")
        return
    app = found[0]
    if want_kind and app.kind != want_kind:
        yield Finding(ERROR, where, f"'{name}' is a {app.kind} app; sim: must name an app in apps/sim/")
    if not_kind and app.kind == not_kind:
        yield Finding(ERROR, where, f"'{name}' is a sim twin; twins are started through an external node's sim:, not run:")


# ---------------------------------------------------------------------------
# Apps and the language policy
# ---------------------------------------------------------------------------

@check("Apps and language policy")
def check_apps() -> Iterator[Finding]:
    for app in repo.all_app_dirs():
        where = str(app.path.relative_to(repo.ROOT))
        if not repo.APP_NAME.match(app.name):
            yield Finding(ERROR, where, "app folder names must be lowercase letters, digits and '_' (e.g. hpc_monitor)")
        py_files = [p for p in app.path.rglob("*.py") if not p.name.startswith("test_") and p.name != "conftest.py"]
        if app.kind == "vehicle":
            # Vehicle code: C or C++ only. Tests next to the app may be Python (they are tooling).
            if py_files:
                names = ", ".join(str(p.relative_to(app.path)) for p in py_files)
                yield Finding(ERROR, where, f"Python is not allowed in vehicle apps (found {names}); use C++ or C")
            if not (app.path / "CMakeLists.txt").exists():
                yield Finding(ERROR, where, "vehicle apps need a CMakeLists.txt")
        else:
            if not (app.path / "main.py").exists():
                yield Finding(ERROR, where, f"{app.kind} apps need a main.py")
        if not (app.path / "README.md").exists():
            yield Finding(WARN, where, "no README.md (what it does, arguments, topics in and out)")
        if not any(app.path.glob("test_*.py")):
            yield Finding(WARN, where, f"no test_{app.name}.py")


# ---------------------------------------------------------------------------
# Data contract, topics, external nodes, QoS (registered in that order)
# ---------------------------------------------------------------------------

import check_contract  # noqa: E402,F401  (import registers its checks)


# ---------------------------------------------------------------------------
# Repo hygiene
# ---------------------------------------------------------------------------

@check("Repo hygiene")
def check_hygiene() -> Iterator[Finding]:
    # Licenses and machine-specific files must never be committed.
    try:
        tracked = subprocess.run(["git", "-C", str(repo.ROOT), "ls-files"], capture_output=True,
                                 text=True, check=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        tracked = [p.relative_to(repo.ROOT).as_posix() for p in repo.ROOT.rglob("*")
                   if p.is_file() and ".git" not in p.parts]
    for f in tracked:
        low = f.lower()
        if "rti_license" in low or low.endswith(".dat"):
            yield Finding(ERROR, f, "looks like a license file; licenses must never be in the repo (keep it in .local/ or outside)")
        if low.startswith(".local/") or low.startswith("build/") or low.startswith(".venv/"):
            yield Finding(ERROR, f, "machine-specific or generated file is tracked by git; it should be ignored")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_checks() -> list[tuple[str, list[Finding]]]:
    results = []
    for title, fn in CHECKS:
        try:
            results.append((title, list(fn())))
        except Exception as e:  # a crashing check is itself a finding, never a traceback
            results.append((title, [Finding(ERROR, "protorig", f"check crashed: {type(e).__name__}: {e}")]))
    return results


def main(args) -> int:
    results = run_checks()
    errors = warnings = 0
    for title, findings in results:
        errs = [f for f in findings if f.severity == ERROR]
        warns = [f for f in findings if f.severity == WARN]
        errors += len(errs)
        warnings += len(warns)
        mark = "FAIL" if errs else ("warn" if warns else " ok ")
        print(f"[{mark}] {title}")
        for f in errs + (warns if not args.quiet else []):
            print(f"       {f}")
    print()
    if errors:
        print(f"{errors} error(s), {warnings} warning(s)")
        return 1
    print(f"All checks passed ({warnings} warning(s))" if warnings else "All checks passed")
    return 0
