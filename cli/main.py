"""
main.py — entry point for the protorig CLI. Launched by ./protorig,
protorig.ps1 or protorig.cmd; never needs to be run directly.

Each verb lives in its own module. Verbs that aren't built yet are listed, so
`protorig --help` always shows the full planned command set.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # so `import repo` works

try:
    import yaml  # noqa: F401  (needed by every verb)
except ImportError:
    print("protorig: the PyYAML package is missing. Run bootstrap first "
          "(./bootstrap/linux.sh or .\\bootstrap\\windows.ps1), or: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

PLANNED = {
    "gen": "Generate per-node QoS and peer settings into build/",
    "build": "Build C/C++ apps (runs rtiddsgen)",
    "run": "Run a scenario (--sim, or --node <node>) or one app (--app)",
    "preflight": "Check every real node is up and discovered",
    "new": "Create a scenario, app or external node from a template",
    "send": "Publish one sample from the command line",
    "test": "Run automated tests",
    "deploy": "Push built binaries and configs to a node (cross-compile targets)",
}


def cmd_list(args) -> int:
    """List scenarios with their one-line descriptions."""
    import repo
    scenarios = repo.load_scenarios()
    if not scenarios:
        print("No scenarios yet. Create one with: protorig new scenario <name>")
        return 0
    width = max(len(s.name) for s in scenarios)
    for s in scenarios:
        desc = s.error and f"(unreadable: {s.error})" or s.data.get("description", "")
        print(f"{s.name:<{width}}  {desc}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="protorig",
        description="Build, run and test distributed Connext DDS scenarios. See docs/WORKFLOW.md.")
    sub = p.add_subparsers(dest="verb", metavar="<command>")

    c = sub.add_parser("check", help="Static checks: scenarios, apps, language policy, data contract, QoS, locks, repo hygiene")
    c.add_argument("-q", "--quiet", action="store_true", help="show errors only")
    sub.add_parser("list", help="List scenarios")
    lk = sub.add_parser("lock", help="Record the IDL an external node was just flashed with")
    lk.add_argument("name", help="external node, e.g. tc397")
    lk.add_argument("--add", action="append", metavar="IDL", help="also lock this IDL file (repeatable)")
    lk.add_argument("--dry-run", action="store_true", help="show what would be locked, write nothing")
    for verb, text in PLANNED.items():
        sub.add_parser(verb, help=f"{text} (not built yet)", add_help=False)

    args, _ = p.parse_known_args(argv)
    if args.verb is None:
        p.print_help()
        return 0
    if args.verb == "check":
        import check
        return check.main(args)
    if args.verb == "list":
        return cmd_list(args)
    if args.verb == "lock":
        import lock
        return lock.main(args)
    print(f"protorig {args.verb}: not built yet. It's planned; see docs/WORKFLOW.md.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
