"""
repo.py — where things are in a protorig repo, and how to load them.

Everything the CLI knows about the layout lives here, so the layout can change
in one place. Paths are resolved from the repo root, never the current folder.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Connext version used for all development (7.7 LTS). The pin in
# bootstrap/requirements.txt must agree; tests/test_version.py checks it.
CONNEXT_VERSION = "7.7"

# App kinds, in lookup order. The folder an app sits in decides its kind.
APP_KINDS = ("vehicle", "tooling", "sim")

SCENARIO_NAME = re.compile(r"^[a-z][a-z0-9-]*$")      # e.g. tire-skeleton
APP_NAME = re.compile(r"^[a-z][a-z0-9_]*$")           # e.g. hpc_monitor
NODE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")          # e.g. hpc-pi


@dataclass
class App:
    """One app folder, found on disk."""
    name: str
    kind: str                  # vehicle | tooling | sim
    path: Path
    scope: str                 # "shared" or the scenario name

    @property
    def language(self) -> str | None:
        """C/C++ if it has CMakeLists.txt, Python if it has main.py."""
        if (self.path / "CMakeLists.txt").exists():
            return "c/c++"
        if (self.path / "main.py").exists():
            return "python"
        return None


@dataclass
class Scenario:
    name: str
    path: Path
    data: dict = field(default_factory=dict)
    error: str | None = None   # set if scenario.yaml could not be read


def app_roots(scenario: str | None = None) -> list[tuple[str, Path]]:
    """Folders apps can live in, most specific first.

    A scenario's own apps (scenarios/<name>/apps/<kind>/) take precedence over
    the shared ones (apps/<kind>/).
    """
    roots = []
    if scenario:
        roots.append((scenario, ROOT / "scenarios" / scenario / "apps"))
    roots.append(("shared", ROOT / "apps"))
    return roots


def find_apps(scenario: str | None = None) -> dict[str, list[App]]:
    """All apps visible to a scenario (or only shared ones), by name.

    Returns a list per name so duplicates can be reported rather than hidden.
    """
    found: dict[str, list[App]] = {}
    for scope, base in app_roots(scenario):
        for kind in APP_KINDS:
            kind_dir = base / kind
            if not kind_dir.is_dir():
                continue
            for p in sorted(kind_dir.iterdir()):
                if p.is_dir() and not p.name.startswith("."):
                    found.setdefault(p.name, []).append(App(p.name, kind, p, scope))
    return found


def all_app_dirs() -> list[App]:
    """Every app folder in the repo: shared and scenario-local."""
    apps = []
    for scope, base in [("shared", ROOT / "apps")] + [
        (s.name, s / "apps") for s in sorted((ROOT / "scenarios").glob("*")) if s.is_dir()
    ]:
        for kind in APP_KINDS:
            kind_dir = base / kind
            if kind_dir.is_dir():
                for p in sorted(kind_dir.iterdir()):
                    if p.is_dir() and not p.name.startswith("."):
                        apps.append(App(p.name, kind, p, scope))
    return apps


def load_scenarios() -> list[Scenario]:
    """Every scenarios/<name>/scenario.yaml, parsed. Read errors are kept, not raised."""
    out = []
    base = ROOT / "scenarios"
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        s = Scenario(d.name, d)
        f = d / "scenario.yaml"
        if not f.exists():
            s.error = "missing scenario.yaml"
        else:
            try:
                s.data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                if not isinstance(s.data, dict):
                    s.error = "scenario.yaml must be a mapping (key: value pairs)"
                    s.data = {}
            except yaml.YAMLError as e:
                s.error = f"scenario.yaml is not valid YAML: {e}".replace("\n", " ")
        out.append(s)
    return out
