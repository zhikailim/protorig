"""
test_contract.py — tests for the data-contract, topic, flash-lock and QoS checks,
and for `protorig lock`.

Each test copies the real interfaces/, libs/, qos/ and external/ into a
throwaway repo, breaks one thing, and asserts check reports it.

The last group runs REAL Connext: it confirms that the checker's
"would these match?" verdict agrees with what DDS actually does on the wire.
Those tests need a Connext license (RTI_LICENSE_FILE) and are skipped without one.
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "cli"))

import check           # noqa: E402
import check_contract  # noqa: E402
import lock            # noqa: E402
import repo            # noqa: E402

EXPECTED_BASELINE_WARNINGS = {"not locked", "Variant.Alert.BestEffort changes 'Alert' incompatibly"}


@pytest.fixture
def real(tmp_path, monkeypatch):
    """A copy of the real contract (no scenarios, no apps). Returns helpers."""
    for d in ("interfaces", "libs", "qos", "external"):
        shutil.copytree(REPO / d, tmp_path / d, ignore=shutil.ignore_patterns("__pycache__", "flashed.lock"))
    monkeypatch.setattr(repo, "ROOT", tmp_path)
    monkeypatch.setattr(check_contract, "installed_connext_python", lambda: repo.CONNEXT_VERSION + ".0")

    class R:
        root = tmp_path

        @staticmethod
        def edit(path, old, new, count=1):
            p = tmp_path / path
            text = p.read_text()
            assert old in text, f"test setup: '{old}' not in {path}"
            p.write_text(text.replace(old, new, count))

        @staticmethod
        def write(path, text):
            p = tmp_path / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

        @staticmethod
        def findings():
            return [f for _, fs in check.run_checks() for f in fs]

        @staticmethod
        def errors():
            return [str(f) for f in R.findings() if f.severity == check.ERROR]

        @staticmethod
        def warnings():
            return [str(f) for f in R.findings() if f.severity == check.WARN]

        @staticmethod
        def lock(name="tc397", add=None, dry_run=False):
            return lock.main(argparse.Namespace(name=name, add=add, dry_run=dry_run))

    return R


def _only_expected_warnings(warnings):
    return [w for w in warnings if not any(e in w for e in EXPECTED_BASELINE_WARNINGS)]


# --- baseline ---------------------------------------------------------------------

def test_real_contract_is_clean(real):
    assert real.errors() == []
    assert _only_expected_warnings(real.warnings()) == []


# --- IDL files ----------------------------------------------------------------------

def test_duplicate_idl_file_name(real):
    real.write("interfaces/common/time.idl", "module other { struct T { long x; }; };\n")
    assert any("IDL file names must be unique" in e for e in real.errors())


def test_duplicate_type_definition(real):
    real.write("interfaces/common/copy.idl", "module alerts { struct Alert { long x; }; };\n")
    assert any("alerts::Alert is also defined" in e for e in real.errors())


def test_reserved_module_fw(real):
    real.write("interfaces/common/bad.idl", "module fw { struct App { long x; }; };\n")
    assert any("module 'fw' is reserved" in e for e in real.errors())


def test_unknown_member_type(real):
    real.edit("interfaces/common/alerts.idl", "Severity         severity;", "alerts::Level    severity;")
    assert any("unknown type alerts::Level" in e for e in real.errors())


def test_unreadable_idl(real):
    real.write("interfaces/common/broken.idl", "module x { struct Y { long a; \n")
    assert any("can't read IDL" in e for e in real.errors())


def test_unsupported_construct_is_noted_not_crashing(real):
    real.write("interfaces/common/extra.idl", "module extra { typedef long Id; struct S { long a; }; };\n")
    real.edit("libs/py/fw/types.py", 'BY_IDL_NAME = {', 'BY_IDL_NAME = {\n    "extra::S": ExtraS,')
    real.edit("libs/py/fw/types.py", "# Every IDL type", '@idl.struct(type_annotations=[idl.type_name("extra::S"), APPENDABLE])\nclass ExtraS:\n    a: idl.int32 = 0\n\n\n# Every IDL type')
    assert any("'typedef' is not contract-checked" in w for w in real.warnings())
    assert real.errors() == []


# --- Python types must match the IDL (the silent-failure class of bugs) --------------

MIRROR_BREAKS = [
    ("renamed member",   "    temperature: float = 0.0     # IDL double", "    pressure: float = 0.0",           "differs from"),
    ("changed bound",    '"message": [idl.bound(128)]',                  '"message": [idl.bound(64)]',         "differs from"),
    ("dropped @key",     '"alert_id": [idl.key, idl.bound(32)]',          '"alert_id": [idl.bound(32)]',        "differs from"),
    ("signed/unsigned",  "    nanosec: idl.uint32 = 0",                   "    nanosec: idl.int32 = 0",          "differs from"),
    ("float vs double",  "    value: float = 0.0\n    stamp_ns",           "    value: idl.float32 = 0.0\n    stamp_ns", "differs from"),
    ("reordered enum",   "    SEVERITY_WARNING = 1\n    SEVERITY_CRITICAL = 2",
                         "    SEVERITY_CRITICAL = 1\n    SEVERITY_WARNING = 2",                                        "differs from"),
    ("extensibility",    'idl.type_name("alerts::Alert"), APPENDABLE',    'idl.type_name("alerts::Alert"), idl.final', "extensibility"),
    ("wrong type name",  'idl.type_name("protorig::NodeStatus")',         'idl.type_name("protorig::Status")',  "named 'protorig::Status'"),
    ("missing mirror",   '    "alerts::Alert": Alert,\n',                 "",                                    "no Python version of alerts::Alert"),
]


@pytest.mark.parametrize("label,old,new,expected", MIRROR_BREAKS, ids=[m[0] for m in MIRROR_BREAKS])
def test_mirror_drift_is_caught(real, label, old, new, expected):
    real.edit("libs/py/fw/types.py", old, new)
    errs = real.errors()
    assert any(expected in e for e in errs), f"{label}: {errs}"


def test_mirror_without_idl(real):
    real.edit("libs/py/fw/types.py", 'BY_IDL_NAME = {', 'BY_IDL_NAME = {\n    "ghost::Type": Alert,')
    assert any("ghost::Type has a Python version but no IDL" in e for e in real.errors())


def test_mirror_import_error_is_reported(real):
    real.edit("libs/py/fw/types.py", "import rti.idl as idl", "import rti.idl as idl\nraise RuntimeError('broken on purpose')")
    assert any("can't be imported" in e for e in real.errors())


# --- topics -------------------------------------------------------------------------

TOPIC_BREAKS = [
    ("unknown type",            '"Alert": "alerts::Alert"',        '"Alert": "alerts::Alarm"',         "no IDL file defines", "error"),
    ("wrong underscore prefix", '"_sys/NodeStatus"',               '"_internal/NodeStatus"',           "only '_' prefix allowed is _sys/", "error"),
    ("_sys with scenario type", '"Alert": "alerts::Alert"',        '"_sys/Alert": "alerts::Alert"',    "_sys/ topics carry framework types", "error"),
    ("framework type, no _sys", '"_sys/DemoControl"',              '"DemoControl"',                    "name it _sys/", "error"),
    ("external renamed",        '"Example Temperature":',          '"Example Temp":',                  "isn't in TOPICS", "error"),
]


@pytest.mark.parametrize("label,old,new,expected,sev", TOPIC_BREAKS, ids=[t[0] for t in TOPIC_BREAKS])
def test_topic_break_is_caught(real, label, old, new, expected, sev):
    real.edit("libs/py/fw/topics.py", old, new)
    found = real.errors() if sev == "error" else real.warnings()
    assert any(expected in f for f in found), f"{label}: {found}"


def test_external_type_mismatch(real):
    real.edit("external/tc397/external.yaml", "type: sensor_msgs::msg::Temperature", "type: sensor_msgs::msg::Pressure")
    assert any("external/tc397 says type sensor_msgs::msg::Pressure" in e for e in real.errors())


def test_topic_without_qos_warns(real):
    real.edit("libs/py/fw/topics.py", '"Alert": "alerts::Alert",', '"Alert": "alerts::Alert",\n    "Alert2": "alerts::Alert",')
    assert any("no writer QoS for topic 'Alert2'" in w for w in real.warnings())


def test_qos_for_unknown_topic_warns(real):
    real.edit("qos/topics.xml", '<datawriter_qos topic_filter="Alert">', '<datawriter_qos topic_filter="Alrt">')
    w = real.warnings()
    assert any("writer QoS for 'Alrt', which is not a known topic" in x for x in w), w


# --- flash locks and `protorig lock` ---------------------------------------------------

def test_lock_then_clean(real):
    assert real.lock() == 0
    assert (real.root / "external/tc397/flashed.lock").exists()
    assert not any("not locked" in w for w in real.warnings())


def test_edit_after_lock_warns(real):
    real.lock()
    real.edit("interfaces/external/tc397/Temperature.idl", "double variance;", "double  variance;")
    assert any("Temperature.idl changed since tc397 was flashed" in w for w in real.warnings())


def test_relock_clears_warning(real):
    real.lock()
    real.edit("interfaces/external/tc397/Temperature.idl", "double variance;", "double  variance;")
    real.lock()
    assert not any("changed since" in w for w in real.warnings())


def test_deleted_locked_file_is_error(real):
    real.lock()
    (real.root / "interfaces/external/tc397/Time.idl").unlink()
    assert any("was flashed but no longer exists" in e for e in real.errors())


def test_new_idl_not_in_lock_warns(real):
    real.lock()
    real.write("interfaces/external/tc397/Extra.idl", "module tcx { struct E { long a; }; };\n")
    assert any("Extra.idl is not in the flash lock" in w for w in real.warnings())


def test_lock_add_common_idl_is_kept_on_relock(real):
    assert real.lock(add=["interfaces/common/alerts.idl"]) == 0
    assert real.lock() == 0                                      # relock without --add
    text = (real.root / "external/tc397/flashed.lock").read_text()
    assert "interfaces/common/alerts.idl" in text


def test_lock_add_outside_repo_is_refused(real):
    assert real.lock(add=["/etc/passwd"]) == 1
    assert not (real.root / "external/tc397/flashed.lock").exists()


def test_lock_dry_run_writes_nothing(real):
    assert real.lock(dry_run=True) == 0
    assert not (real.root / "external/tc397/flashed.lock").exists()


def test_lock_unknown_external(real):
    assert real.lock(name="nope") == 1


def test_corrupt_lock_is_reported(real):
    real.write("external/tc397/flashed.lock", "files: [unclosed\n")
    assert any("unreadable" in e for e in real.errors())


# --- QoS: the silent "never matches the ECU" class of bugs ---------------------------------

READER = '<datareader_qos topic_filter="Example Temperature">\n        <reliability><kind>BEST_EFFORT_RELIABILITY_QOS</kind></reliability>'

QOS_BREAKS = [
    ("reliable reader of the ECU",
     READER, READER.replace("BEST_EFFORT", "RELIABLE"), "reader wants RELIABLE"),
    ("finite deadline on ECU reader",
     READER, READER + "\n        <deadline><period><sec>1</sec><nanosec>0</nanosec></period></deadline>", "1 s deadline"),
    ("finite liveliness on ECU reader",
     READER, READER + "\n        <liveliness><lease_duration><sec>5</sec><nanosec>0</nanosec></lease_duration></liveliness>", "liveliness lease"),
    ("transient-local ECU reader",
     READER + "\n        <durability><kind>VOLATILE_DURABILITY_QOS</kind></durability>",
     READER + "\n        <durability><kind>TRANSIENT_LOCAL_DURABILITY_QOS</kind></durability>", "TRANSIENT_LOCAL durability"),
    ("exclusive ownership on ECU reader",
     READER, READER + "\n        <ownership><kind>EXCLUSIVE_OWNERSHIP_QOS</kind></ownership>", "ownership differs"),
    ("twin writer not mirroring the ECU",
     '<datawriter_qos topic_filter="Example Temperature">\n        <reliability><kind>BEST_EFFORT_RELIABILITY_QOS</kind>',
     '<datawriter_qos topic_filter="Example Temperature">\n        <reliability><kind>RELIABLE_RELIABILITY_QOS</kind>', "must mirror what tc397 offers"),
    ("own topic mismatch",
     '<datawriter_qos topic_filter="Alert">\n        <reliability><kind>RELIABLE_RELIABILITY_QOS</kind>',
     '<datawriter_qos topic_filter="Alert">\n        <reliability><kind>BEST_EFFORT_RELIABILITY_QOS</kind>', "'Alert' writer and reader don't match"),
    ("heartbeat deadline too tight",
     "<deadline><period><sec>3</sec><nanosec>0</nanosec></period></deadline>",
     "<deadline><period><sec>1</sec><nanosec>0</nanosec></period></deadline>", "'_sys/NodeStatus' writer and reader don't match"),
]


@pytest.mark.parametrize("label,old,new,expected", QOS_BREAKS, ids=[q[0] for q in QOS_BREAKS])
def test_qos_break_is_caught(real, label, old, new, expected):
    real.edit("qos/topics.xml", old, new)
    errs = real.errors()
    assert any(expected in e for e in errs), f"{label}: {errs}"


def test_bad_variant_is_caught(real):
    real.edit("qos/variants.xml", "</qos_library>", '''  <qos_profile name="Variant.TirePressure.Reliable" base_name="protorig::Topics">
      <datareader_qos topic_filter="Example Temperature">
        <reliability><kind>RELIABLE_RELIABILITY_QOS</kind></reliability>
      </datareader_qos>
    </qos_profile>
  </qos_library>''')
    assert any("Variant.TirePressure.Reliable: readers of 'Example Temperature' would never match tc397" in e
               for e in real.errors())


def test_variant_naming_warns(real):
    real.edit("qos/variants.xml", 'name="Variant.TirePressure.LongHistory"', 'name="LongHistory"')
    assert any("should start with 'Variant.'" in w for w in real.warnings())


def test_xml_comment_with_double_dash(real):
    real.edit("qos/topics.xml", "so sim runs behave like hardware", "so --sim behaves like hardware")
    assert any("not valid XML" in e and "'--'" in e for e in real.errors())


def test_ecu_offering_reliable_would_allow_reliable_reader(real):
    """If the firmware changes to RELIABLE, a reliable reader becomes fine: the check follows external.yaml."""
    real.edit("external/tc397/external.yaml", "reliability: BEST_EFFORT", "reliability: RELIABLE")
    real.edit("qos/topics.xml", READER, READER.replace("BEST_EFFORT", "RELIABLE"))
    real.edit("qos/topics.xml",
              '<datawriter_qos topic_filter="Example Temperature">\n        <reliability><kind>BEST_EFFORT_RELIABILITY_QOS</kind>',
              '<datawriter_qos topic_filter="Example Temperature">\n        <reliability><kind>RELIABLE_RELIABILITY_QOS</kind>')
    assert not any("would never match" in e for e in real.errors())


# --- LIVE: the checker's verdict must agree with real Connext ---------------------------------

def _connext_or_skip():
    dds = pytest.importorskip("rti.connextdds")
    if not os.environ.get("RTI_LICENSE_FILE") and not (Path.home() / "rti_license.dat").exists():
        pytest.skip("needs a Connext license (RTI_LICENSE_FILE)")
    return dds


def _writer_qos_from(dds, offered: dict):
    q = dds.DataWriterQos()
    q.reliability.kind = getattr(dds.ReliabilityKind, offered["reliability"])
    q.durability.kind = getattr(dds.DurabilityKind, offered["durability"])
    return q


LIVE_READERS = {
    "default profile":        {},
    "reliable reader":        {"reliability": "RELIABLE"},
    "1 s deadline":           {"deadline": 1},
    "5 s liveliness lease":   {"liveliness": 5},
    "transient-local reader": {"durability": "TRANSIENT_LOCAL"},
}


@pytest.mark.parametrize("label", list(LIVE_READERS))
def test_checker_agrees_with_connext(label):
    """Publish like the TC397 does; subscribe with a reader; compare 'matched on the wire'
    with the checker's incompatibilities() verdict."""
    dds = _connext_or_skip()
    sys.path.insert(0, str(REPO / "libs" / "py"))
    from fw.types import Temperature

    import yaml
    offered = check_contract._offer_from_yaml(
        yaml.safe_load((REPO / "external/tc397/external.yaml").read_text())["topics"]["Example Temperature"]["writer"])

    provider = check_contract.load_qos_provider(dds)
    provider.default_profile = check_contract.DEFAULT_PROFILE
    rq = provider.get_topic_datareader_qos("Example Temperature")
    change = LIVE_READERS[label]
    if "reliability" in change:
        rq.reliability.kind = dds.ReliabilityKind.RELIABLE
    if "deadline" in change:
        rq.deadline.period = dds.Duration(change["deadline"])
    if "liveliness" in change:
        rq.liveliness.lease_duration = dds.Duration(change["liveliness"])
    if "durability" in change:
        rq.durability.kind = dds.DurabilityKind.TRANSIENT_LOCAL

    predicted_ok = not check_contract.incompatibilities(offered, check_contract._qos_dict(rq))

    import random
    p = dds.DomainParticipant(random.randint(150, 199))
    try:
        topic = dds.Topic(p, "Example Temperature", Temperature)
        w = dds.DataWriter(dds.Publisher(p), topic, _writer_qos_from(dds, offered))
        r = dds.DataReader(dds.Subscriber(p), topic, rq)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and w.publication_matched_status.current_count == 0:
            time.sleep(0.05)
        matched = w.publication_matched_status.current_count > 0
        if matched:
            sample = Temperature(temperature=31.5)
            got = []
            end = time.monotonic() + 2
            while time.monotonic() < end and not got:
                w.write(sample)
                time.sleep(0.05)
                got = [s.temperature for s in r.take_data()]
            assert got and got[0] == 31.5, "matched but no data arrived"
        assert matched == predicted_ok, f"{label}: Connext matched={matched}, checker predicted {predicted_ok}"
    finally:
        p.close()


def test_python_types_work_on_the_wire():
    """Every framework topic: publish one sample with the Python type and receive it."""
    dds = _connext_or_skip()
    sys.path.insert(0, str(REPO / "libs" / "py"))
    from fw import topics, types
    import random
    provider = check_contract.load_qos_provider(dds)
    provider.default_profile = check_contract.DEFAULT_PROFILE
    p = dds.DomainParticipant(random.randint(150, 199))
    try:
        samples = {
            "Alert": types.Alert(source="hpc-vm/hpc_monitor", alert_id="LOW_PRESSURE_FL",
                                 severity=types.Severity.SEVERITY_WARNING, message="FL low", value=28.5),
            "_sys/NodeStatus": types.NodeStatus(node="hpc-pi", app="node_agent", os="linux", seq=7, cpu_load=0.25),
            "_sys/DemoControl": types.DemoControl(target_node="*", cmd_id=1, command=types.Command.CMD_KILL_APP,
                                                  target_app="hpc_monitor"),
        }
        for name, sample in samples.items():
            t = dds.Topic(p, name, topics.type_of(name))
            w = dds.DataWriter(dds.Publisher(p), t, provider.get_topic_datawriter_qos(name))
            r = dds.DataReader(dds.Subscriber(p), t, provider.get_topic_datareader_qos(name))
            end = time.monotonic() + 3
            while time.monotonic() < end and w.publication_matched_status.current_count == 0:
                time.sleep(0.05)
            assert w.publication_matched_status.current_count > 0, f"{name}: writer and reader never matched"
            w.write(sample)
            got = []
            end = time.monotonic() + 2
            while time.monotonic() < end and not got:
                time.sleep(0.05)
                got = r.take_data()
            assert got and got[0] == sample, f"{name}: sent {sample}, got {got}"
    finally:
        p.close()
