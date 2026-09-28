"""
new.py — `protorig new app|scenario <name>`: create something from a template.

Copies a folder from templates/ and replaces {{name}} and {{description}} in
file names and contents. Nothing else: open the template to see exactly what
you'll get. Templates are copied once, so they stay thin; code that evolves
belongs in libs/.

  protorig new app result_gui --kind tooling --desc "The audience's screen"
  protorig new app tc397_twin --kind sim     --desc "Stand-in for the TC397"
  protorig new scenario brake-failover       --desc "HPC failover with brake commands"
"""
from __future__ import annotations

import shutil
from pathlib import Path

import repo

TEMPLATES = {"tooling": "app_py", "sim": "app_py", "vehicle": "app_cpp"}


def _fill(text: str, name: str, desc: str) -> str:
    return text.replace("{{name}}", name).replace("{{description}}", desc)


def _copy(template: Path, dest: Path, name: str, desc: str) -> list[Path]:
    made = []
    dest.mkdir(parents=True)
    for src in sorted(template.rglob("*")):
        if src.is_dir() or "__pycache__" in src.parts:
            continue
        out = dest / _fill(str(src.relative_to(template)), name, desc)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_fill(src.read_text(encoding="utf-8"), name, desc), encoding="utf-8")
        made.append(out)
    return made


def new_app(name: str, kind: str, desc: str, scenario: str | None) -> int:
    if not repo.APP_NAME.match(name):
        print(f"protorig new: '{name}' is not a valid app name (lowercase letters, digits, '_', e.g. hpc_monitor)")
        return 1
    existing = [a for a in repo.all_app_dirs() if a.name == name]
    if existing:
        print(f"protorig new: app '{name}' already exists at {existing[0].path.relative_to(repo.ROOT)}")
        return 1
    template = repo.ROOT / "templates" / TEMPLATES[kind]
    if not template.is_dir():
        print(f"protorig new: no template for {kind} apps yet ({template.relative_to(repo.ROOT)}); "
              "C/C++ apps arrive with the C++ step")
        return 1
    if scenario and not (repo.ROOT / "scenarios" / scenario).is_dir():
        print(f"protorig new: no scenario '{scenario}'")
        return 1
    base = repo.ROOT / "scenarios" / scenario / "apps" if scenario else repo.ROOT / "apps"
    dest = base / kind / name
    made = _copy(template, dest, name, desc)
    print(f"Created {dest.relative_to(repo.ROOT)}/")
    for f in made:
        print(f"  {f.relative_to(dest)}")
    print("\nNext:\n"
          f"  1. Fill in the header comment in main.py (topics in/out) and the README's behaviour table.\n"
          f"  2. Write the logic in main.py, following its numbered steps.\n"
          f"  3. ./protorig test {name}\n"
          f"  4. List '{name}' in a scenario's run: (or sim: for a twin).")
    return 0


def new_scenario(name: str, desc: str) -> int:
    if not repo.SCENARIO_NAME.match(name):
        print(f"protorig new: '{name}' is not a valid scenario name (lowercase letters, digits, '-', e.g. brake-failover)")
        return 1
    dest = repo.ROOT / "scenarios" / name
    if dest.exists():
        print(f"protorig new: scenarios/{name} already exists")
        return 1
    _copy(repo.ROOT / "templates" / "scenario", dest, name, desc)
    print(f"Created scenarios/{name}/ (scenario.yaml, README.md)\n\nNext:\n"
          "  1. Write the README first: value, story, demo beats, acceptance tests.\n"
          "  2. Set the nodes, IPs and apps in scenario.yaml.\n"
          "  3. ./protorig check")
    return 0


def main(args) -> int:
    desc = (args.desc or "TODO: one-line description").replace("\n", " ").strip()
    if args.what == "app":
        return new_app(args.name, args.kind, desc, args.scenario)
    return new_scenario(args.name, desc)
