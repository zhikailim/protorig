"""
Root conftest.py — pytest loads this automatically for every test in the repo.

It makes libs/py importable and provides the fixtures from fw.testing to every
app and scenario test:

  bus        one test participant on an isolated domain, shared by the session
  start_app  start apps by name; every app it started is stopped after the test

Without the Connext Python package or a license, tests that use these fixtures
are skipped (with the reason) instead of failing.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "libs" / "py"))

# Templates contain test_{{name}}.py skeletons, not real tests; generated files aren't tests either.
collect_ignore_glob = ["templates/*", "build/*", ".venv/*"]


def _find_license() -> str | None:
    """RTI_LICENSE_FILE if set, else rti_license.dat in .local/ (git-ignored), the
    home folder or the current folder. A file found is exported as
    RTI_LICENSE_FILE, so the apps the tests start (child processes) find it too."""
    if os.environ.get("RTI_LICENSE_FILE"):
        return os.environ["RTI_LICENSE_FILE"]
    for folder in (ROOT / ".local", Path.home(), Path.cwd()):
        f = folder / "rti_license.dat"
        if f.exists():
            os.environ["RTI_LICENSE_FILE"] = str(f)
            return str(f)
    return None


def _skip_reason() -> str | None:
    try:
        import rti.connextdds  # noqa: F401
    except ImportError:
        return "needs the Connext Python package (run bootstrap)"
    if _find_license() is None:
        return ("needs a Connext license: put rti_license.dat in the repo's .local/ folder "
                "(git-ignored), or set RTI_LICENSE_FILE")
    return None


_find_license()          # before any test creates a participant


@pytest.fixture(scope="session")
def bus():
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)
    from fw.testing import Bus
    b = Bus()
    yield b
    b.close()


@pytest.fixture
def start_app(bus):
    from fw.testing import AppLauncher
    launcher = AppLauncher(bus)
    yield launcher
    launcher.stop_all()          # always, even when the test failed
    bus.close_listeners()
