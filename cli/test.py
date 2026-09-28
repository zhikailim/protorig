"""
test.py — `protorig test [app ...] [-k expr] [--all]`: run the automated tests.

1. Runs `protorig check` (static rules) and reports its result.
2. Runs pytest on the framework tests, every app's tests and every scenario's
   tests (or just the named apps), using the repo's Python.

The exit code is non-zero if either failed.
"""
from __future__ import annotations

import subprocess
import sys

import repo


def main(args) -> int:
    import check
    results = check.run_checks()
    errors = sum(1 for _, fs in results for f in fs if f.severity == check.ERROR)
    print(f"protorig check: {'OK' if not errors else f'{errors} error(s) (run protorig check for details)'}\n")

    if args.apps:
        paths = []
        for name in args.apps:
            hits = [a for a in repo.all_app_dirs() if a.name == name]
            if not hits:
                print(f"protorig test: no app named '{name}'")
                return 1
            paths.append(str(hits[0].path))
    else:
        paths = [str(p) for p in (repo.ROOT / "tests", repo.ROOT / "apps", repo.ROOT / "scenarios") if p.is_dir()]

    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(repo.ROOT), *paths]
    if args.k:
        cmd += ["-k", args.k]
    rc = subprocess.call(cmd, cwd=repo.ROOT)
    if rc == 5:              # pytest: no tests collected
        print("protorig test: no tests found")
        rc = 0
    return 1 if (rc or errors) else 0
