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


def _skip_reason() -> str | None:
    try:
        import rti.connextdds  # noqa: F401
    except ImportError:
        return "needs the Connext Python package (run bootstrap)"
    if not (os.environ.get("RTI_LICENSE_FILE")
            or any((p / "rti_license.dat").exists() for p in (Path.cwd(), Path.home(), ROOT / ".local"))):
        return "needs a Connext license (set RTI_LICENSE_FILE)"
    return None


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
