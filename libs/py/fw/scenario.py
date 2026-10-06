"""
fw.scenario — reading a scenario's node lists and finding its apps.

Shared by `protorig run` (cli/run.py, through cli/repo.py) and node_agent, so
both start exactly the same thing for the same `run:` entry. Every function takes
the repo root explicitly: the CLI's tests point it at a temporary copy.
"""
from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

APP_KINDS = ("vehicle", "tooling", "sim")   # lookup order; the folder decides the kind


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


def find_apps(root: Path, scenario: str | None = None) -> dict[str, list[App]]:
    """All apps visible to a scenario (its own first, then shared), by name.
    A list per name, so duplicates can be reported rather than hidden."""
    roots = ([(scenario, root / "scenarios" / scenario / "apps")] if scenario else []) + [("shared", root / "apps")]
    found: dict[str, list[App]] = {}
    for scope, base in roots:
        for kind in APP_KINDS:
            kind_dir = base / kind
            if not kind_dir.is_dir():
                continue
            for p in sorted(kind_dir.iterdir()):
                if p.is_dir() and not p.name.startswith("."):
                    found.setdefault(p.name, []).append(App(p.name, kind, p, scope))
    return found


def command_for(app: App) -> tuple[list[str] | None, str]:
    """How to start an app, or why it can't be started yet."""
    if app.language == "python":
        return [sys.executable, str(app.path / "main.py")], ""
    if app.language == "c/c++":
        return None, "C/C++ app, not built yet (needs `protorig build`)"
    return None, "no main.py or CMakeLists.txt in its folder"


def entries(spec: dict, node: str) -> list[tuple[str, list[str]]]:
    """The (app, args) pairs a node's run: list asks for. Raises ValueError with
    a readable reason if the list can't be read."""
    out = []
    run = spec.get("run") or []
    if not isinstance(run, list):
        raise ValueError(f"node '{node}': run: must be a list (run `protorig check`)")
    for item in run:
        try:
            parts = shlex.split(str(item))
        except ValueError as e:
            raise ValueError(f"node '{node}': can't parse run entry {item!r}: {e}") from None
        if parts:
            out.append((parts[0], parts[1:]))
    return out


@dataclass
class Entry:
    """One app a node runs: how to start it, or why it can't be."""
    app: str
    args: list[str] = field(default_factory=list)
    cmd: list[str] | None = None       # None = can't start: see `missing`
    missing: str = ""


def node_entries(root: Path, scenario: str, node: str) -> list[Entry]:
    """What node `node` of `scenario` runs, resolved. Raises ValueError if the
    scenario, the node or its list can't be read (with what to do about it)."""
    f = root / "scenarios" / scenario / "scenario.yaml"
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except UnicodeDecodeError:
        raise ValueError(f"can't read {f}: it isn't UTF-8 text (save it as UTF-8)") from None
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"can't read {f}: {e}".replace("\n", " ")) from None
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, dict) or node not in nodes or not isinstance(nodes[node], dict):
        raise ValueError(f"scenario '{scenario}' has no node '{node}'")
    if nodes[node].get("external"):
        raise ValueError(f"'{node}' is an external node: nothing runs there from this repo")
    apps = find_apps(root, scenario)
    out = []
    for name, args in entries(nodes[node], node):
        hits = apps.get(name) or []
        if not hits:
            out.append(Entry(name, args, missing="no such app (create it with `protorig new app`)"))
            continue
        cmd, why = command_for(hits[0])
        out.append(Entry(name, args, cmd, why))
    return out
