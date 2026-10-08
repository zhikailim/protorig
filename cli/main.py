"""
main.py — entry point for the protorig CLI. Launched by ./protorig,
protorig.ps1 or protorig.cmd; never needs to be run directly.

Each verb lives in its own module. Verbs that aren't built yet are listed, so
`protorig --help` always shows the full planned command set.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # so `import repo` works

# The Connext license: the repo's .local/rti_license.dat (git-ignored, the documented
# place) unless RTI_LICENSE_FILE already names one. Exported, so every agent and app
# that protorig starts finds it too, on every OS.
_LICENSE = Path(__file__).resolve().parent.parent / ".local" / "rti_license.dat"
if not os.environ.get("RTI_LICENSE_FILE") and _LICENSE.is_file():
    os.environ["RTI_LICENSE_FILE"] = str(_LICENSE)

try:
    import yaml  # noqa: F401  (needed by every verb)
except ImportError:
    print("protorig: the PyYAML package is missing. Run bootstrap first "
          "(./bootstrap/linux.sh or .\\bootstrap\\windows.ps1), or: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

PLANNED = {
    "gen": "Generate discovery settings as C source for Micro/Cert apps (run writes the XML ones)",
    "build": "Build C/C++ apps (runs rtiddsgen)",
    "preflight": "Check every real node is up and discovered",
    "send": "Publish one sample from the command line",
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
    nw = sub.add_parser("new", help="Create an app or scenario from a template")
    nw.add_argument("what", choices=["app", "scenario"])
    nw.add_argument("name")
    nw.add_argument("--kind", choices=["tooling", "sim", "vehicle"], default="tooling",
                    help="app kind (default: tooling); decides the folder and language")
    nw.add_argument("--desc", help="one-line description")
    nw.add_argument("--scenario", help="create the app inside this scenario (scenario-only app)")
    ts = sub.add_parser("test", help="Run check, then the automated tests (all, or named apps)")
    ts.add_argument("apps", nargs="*", help="only these apps' tests")
    ts.add_argument("-k", help="only tests whose name matches this expression")
    rn = sub.add_parser("run", help="Run a scenario (--sim), this machine's part of it, or one app (--app)",
                        description="Start a scenario on this machine (--sim), this machine's part of the "
                                    "real rig (just the scenario: the node is found from this machine's IP), "
                                    "or one app: run's options first, then --app <name>, "
                                    "then the app's own arguments, e.g. "
                                    "protorig run --domain 5 --app my_gui --threshold 14.5. Ctrl-C stops everything.")
    rn.add_argument("scenario_name", nargs="?", metavar="scenario", help="the scenario (alone: this machine's part of it)")
    rn.add_argument("--sim", action="store_true", help="the whole scenario on this machine, twins for external nodes; "
                                                       "nothing reaches the network")
    rn.add_argument("--node", help="name the node yourself (only needed on a machine with the IPs of two nodes)")
    rn.add_argument("--live", action="store_true",
                    help="bring the whole rig up through its node agents; Ctrl-C stops every app (agents stay up)")
    rn.add_argument("--partial", action="store_true", help="with --live: go ahead with whichever agents are there")
    rn.add_argument("--start-timeout", type=float, metavar="SECONDS",
                    help="with --live: flag apps not RUNNING after this long (default 20)")
    rn.add_argument("--app", help="just this app; everything after its name goes to the app")
    rn.add_argument("--scenario", dest="scenario_opt", metavar="SCENARIO", help="with --app: use this scenario's domain")
    rn.add_argument("--domain", type=int, help="override the DDS domain")
    rn.add_argument("--dry-run", action="store_true", help="show what would start, start nothing")
    ag = sub.add_parser("agent", help="Start, stop or check the node agents (one per rig machine)",
                        description="Start or stop this machine's node agent, or check every node's agent. "
                                    "The node is found from this machine's IP unless --node names it.")
    ag.add_argument("action", choices=["start", "stop", "status"])
    ag.add_argument("scenario")
    ag.add_argument("--node", help="name the node yourself (status: only that node)")
    ag.add_argument("--background", action="store_true",
                    help="start: keep running after this terminal closes; returns once the agent is up")
    ag.add_argument("--force", action="store_true", help="stop: kill the agent at once")
    ag.add_argument("--domain", type=int, help="override the DDS domain")
    for verb, text in PLANNED.items():
        sub.add_parser(verb, help=f"{text} (not built yet)", add_help=False)

    argv = list(sys.argv[1:] if argv is None else argv)
    app_args: list[str] = []
    if argv[:1] == ["run"]:
        # `run --app <name> ...`: everything after the app's name belongs to the app,
        # untouched (so an app argument can never be mistaken for a run option).
        for i, a in enumerate(argv):
            if a == "--app" and i + 1 < len(argv):
                argv, app_args = argv[:i + 2], argv[i + 2:]
                break
            if a.startswith("--app="):
                argv, app_args = argv[:i + 1], argv[i + 1:]
                break
    args, extra = p.parse_known_args(argv)
    if args.verb is None:
        p.print_help()
        return 0
    if args.verb == "check":
        import check
        return check.main(args)
    if args.verb == "list":
        return cmd_list(args)
    if args.verb == "new":
        import new
        return new.main(args)
    if args.verb == "test":
        import test
        return test.main(args)
    if args.verb == "run":
        import run
        args.scenario = args.scenario_name or args.scenario_opt
        return run.main(args, extra, app_args)
    if args.verb == "agent":
        import agent
        return agent.main(args, extra)
    if args.verb == "lock":
        import lock
        return lock.main(args)
    print(f"protorig {args.verb}: not built yet. It's planned; see docs/WORKFLOW.md.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
