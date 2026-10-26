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


LAYERS = ("cloud", "compute", "edge")      # top to bottom on result_gui's screen (R1)


def load_nodes(root: Path, scenario: str) -> dict[str, dict]:
    """The scenario's nodes, in file order ({} if it has none). Raises ValueError
    with a readable reason if the file can't be read."""
    f = Path(root) / "scenarios" / scenario / "scenario.yaml"
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except UnicodeDecodeError:
        raise ValueError(f"can't read {f}: it isn't UTF-8 text (save it as UTF-8)") from None
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"can't read {f}: {e}".replace("\n", " ")) from None
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, dict):
        return {}
    return {str(n): s for n, s in nodes.items() if isinstance(s, dict)}


def layer_of(spec: dict) -> str:
    """cloud, compute or edge: `layer:` if given, else edge for an external device."""
    layer = spec.get("layer")
    if layer in LAYERS:
        return layer
    return "edge" if spec.get("external") else "compute"


def external_topics(root: Path, node: str) -> list[str]:
    """The topics an external device publishes, from external/<node>/external.yaml."""
    f = Path(root) / "external" / node / "external.yaml"
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        topics = data.get("topics") or {}
        return [str(t) for t in topics] if isinstance(topics, dict) else []
    except (OSError, ValueError, yaml.YAMLError, AttributeError):
        return []


def did_you_mean(name: str, known) -> str:
    """ "; did you mean 'x'?" for a close app name, else "". A close match is
    most likely a typo, or a character copied along (e.g. "tc397_twin:")."""
    import difflib
    close = difflib.get_close_matches(name.strip(" :;,.'\"`"), list(known), n=1, cutoff=0.75)
    return f"; did you mean '{close[0]}'?" if close else ""


def no_such_app(name: str, known) -> str:
    """Why an app can't be started."""
    return ("no such app" + did_you_mean(name, known)) if did_you_mean(name, known) \
        else "no such app yet (create it with `protorig new app`)"


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
    nodes = load_nodes(root, scenario)
    if node not in nodes:
        raise ValueError(f"scenario '{scenario}' has no node '{node}'")
    if nodes[node].get("external"):
        raise ValueError(f"'{node}' is an external node: nothing runs there from this repo")
    apps = find_apps(root, scenario)
    out = []
    for name, args in entries(nodes[node], node):
        hits = apps.get(name) or []
        if not hits:
            out.append(Entry(name, args, missing=no_such_app(name, apps)))
            continue
        cmd, why = command_for(hits[0])
        out.append(Entry(name, args, cmd, why))
    return out
