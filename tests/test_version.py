"""The Connext version is stated in two places; they must agree."""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "cli"))
import repo  # noqa: E402


def test_requirements_pin_matches_cli():
    text = (REPO / "bootstrap" / "requirements.txt").read_text()
    m = re.search(r"^rti\.connext~=(\d+\.\d+)\.\d+", text, re.M)
    assert m, "rti.connext pin missing from bootstrap/requirements.txt"
    assert m.group(1) == repo.CONNEXT_VERSION


def test_version_check_warns_on_mismatch(monkeypatch):
    import check  # noqa: F401  (registers checks)
    import check_contract
    monkeypatch.setattr(check_contract, "installed_connext_python", lambda: "7.6.0")
    msgs = [f.message for f in check_contract.check_connext_version()]
    assert any("is 7.6.0, but protorig uses 7.7" in m for m in msgs)
    monkeypatch.setattr(check_contract, "installed_connext_python", lambda: "7.7.0.1")
    assert list(check_contract.check_connext_version()) == []
    monkeypatch.setattr(check_contract, "installed_connext_python", lambda: "7.70.0")
    assert list(check_contract.check_connext_version()), "7.70 must not pass as 7.7"
