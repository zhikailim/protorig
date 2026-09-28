"""
check_contract.py — checks for the data contract, topics, QoS and external nodes.

Registered into `protorig check` (see check.py). Where Connext itself can give
the answer (effective QoS of a profile, the IDL of a Python type) we ask it,
rather than re-implementing its rules. If the Connext Python package isn't
installed yet, those checks are skipped with a warning, never a crash.
"""
from __future__ import annotations

import hashlib
import importlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

import yaml

import idl
import repo
from check import ERROR, WARN, Finding, check

RESERVED_MODULES = {"fw"}
INFINITE = 2147483647          # Connext's "infinite" duration, in seconds
QOS_FILES = ("base.xml", "topics.xml", "variants.xml")
DEFAULT_PROFILE = "protorig::Topics"
VARIANT_LIBRARY = "protorig_variants"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def rel(p: Path) -> str:
    return str(p.relative_to(repo.ROOT))


def load_idl() -> tuple[dict, dict, list[Finding]]:
    """All IDL types in interfaces/, resolved together.

    Returns (types, origin file per type, findings for unreadable files).
    """
    types, origin, findings = {}, {}, []
    base = repo.ROOT / "interfaces"
    for f in sorted(base.rglob("*.idl")) if base.is_dir() else []:
        try:
            parsed, notes = idl.parse(f.read_text(encoding="utf-8", errors="replace"), qualify=False)
        except idl.IdlError as e:
            findings.append(Finding(ERROR, rel(f), f"can't read IDL: {e}"))
            continue
        for n in notes:
            findings.append(Finding(WARN, rel(f), n))
        for name, spec in parsed.items():
            if name in origin:
                findings.append(Finding(ERROR, rel(f), f"type {name} is also defined in {origin[name]}; "
                                                        "define each type once and #include it"))
                continue
            types[name] = spec
            origin[name] = rel(f)
    idl.resolve(types)
    return types, origin, findings


def import_fw(module: str):
    """Import fw.<module> from libs/py, freshly (tests swap the repo root)."""
    libs = str(repo.ROOT / "libs" / "py")
    if libs not in sys.path:
        sys.path.insert(0, libs)
    for name in [m for m in sys.modules if m == "fw" or m.startswith("fw.")]:
        del sys.modules[name]
    return importlib.import_module(f"fw.{module}")


def has_idl() -> bool:
    base = repo.ROOT / "interfaces"
    return base.is_dir() and any(base.rglob("*.idl"))


def connext():
    try:
        import rti.connextdds as dds          # noqa: F401
        return dds
    except ImportError:
        return None


def external_specs() -> dict[str, dict]:
    """external/<name>/external.yaml, parsed (unreadable ones are reported by a check)."""
    out = {}
    base = repo.ROOT / "external"
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        f = d / "external.yaml"
        if d.is_dir() and f.exists():
            try:
                out[d.name] = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                out[d.name] = None
    return out


# ---------------------------------------------------------------------------
# 1. IDL files
# ---------------------------------------------------------------------------

@check("Data contract (IDL)")
def check_idl() -> Iterator[Finding]:
    base = repo.ROOT / "interfaces"
    files = sorted(base.rglob("*.idl")) if base.is_dir() else []
    seen: dict[str, Path] = {}
    for f in files:
        key = f.name.lower()
        if key in seen:
            yield Finding(ERROR, rel(f), f"same file name as {rel(seen[key])}: rtiddsgen would generate "
                                         "clashing headers; IDL file names must be unique")
        seen[key] = f
    types, origin, findings = load_idl()
    yield from findings
    for name in types:
        top = name.split("::")[0]
        if top in RESERVED_MODULES:
            yield Finding(ERROR, origin[name], f"module '{top}' is reserved for the framework helpers (fw::App)")
        spec = types[name]
        if spec["kind"] == "struct":
            for mname, mtype, _ in spec["members"]:
                if "::" in mtype and mtype not in types:
                    yield Finding(ERROR, origin[name], f"{name}.{mname} uses unknown type {mtype}")


# ---------------------------------------------------------------------------
# 2. Python mirrors match the IDL
# ---------------------------------------------------------------------------

EXT_NAMES = {"ExtensibilityKind.FINAL": "final", "ExtensibilityKind.EXTENSIBLE": "appendable",
             "ExtensibilityKind.MUTABLE": "mutable"}


@check("Python types match the IDL")
def check_mirrors() -> Iterator[Finding]:
    where = "libs/py/fw/types.py"
    if not (repo.ROOT / "libs" / "py" / "fw" / "types.py").exists():
        if has_idl():
            yield Finding(ERROR, where, "missing: the Python versions of the IDL types")
        return
    if connext() is None:
        yield Finding(WARN, where, "skipped: the Connext Python package isn't installed (run bootstrap)")
        return
    import rti.idl as rti_idl
    try:
        types_mod = import_fw("types")
    except Exception as e:
        yield Finding(ERROR, where, f"can't be imported: {type(e).__name__}: {e}")
        return
    mirrors = getattr(types_mod, "BY_IDL_NAME", {})
    idl_types, origin, _ = load_idl()
    for name in sorted(set(idl_types) - set(mirrors)):
        yield Finding(ERROR, where, f"no Python version of {name} (from {origin[name]}); add it and list it in BY_IDL_NAME")
    for name in sorted(set(mirrors) - set(idl_types)):
        yield Finding(ERROR, where, f"{name} has a Python version but no IDL; remove it or add the IDL")
    for name in sorted(set(mirrors) & set(idl_types)):
        try:
            dyn = rti_idl.get_type_support(mirrors[name]).dynamic_type
        except Exception as e:
            yield Finding(ERROR, where, f"{name}: not a valid IDL type ({e})")
            continue
        if dyn.name != name:
            yield Finding(ERROR, where, f"{name}: Python type is named '{dyn.name}' on the wire; "
                                        "names must match exactly or nodes won't match")
            continue
        printed, _ = idl.parse(str(dyn))
        got, want = printed.get(name), idl_types[name]
        if got is None:
            yield Finding(ERROR, where, f"{name}: can't compare (unexpected printed form)")
            continue
        if got["members"] != want["members"]:
            yield Finding(ERROR, where, f"{name} differs from {origin[name]}:\n"
                                        f"             IDL:    {_fmt(want)}\n"
                                        f"             Python: {_fmt(got)}")
        if want["kind"] == "struct":
            ext = EXT_NAMES.get(str(dyn.extensibility_kind), str(dyn.extensibility_kind))
            if ext != want["ext"]:
                yield Finding(ERROR, where, f"{name}: extensibility is {ext} in Python but {want['ext']} in the IDL")


def _fmt(spec: dict) -> str:
    if spec["kind"] == "enum":
        return ", ".join(spec["members"])
    return ", ".join(f"{'@key ' if k else ''}{t} {n}" for n, t, k in spec["members"])


# ---------------------------------------------------------------------------
# 3. Topics: registry, naming, QoS coverage
# ---------------------------------------------------------------------------

def qos_topic_filters(profile_file: Path, profile_name: str) -> dict[str, set[str]]:
    """topic_filter values per entity kind in one profile of one XML file."""
    out = {"datawriter_qos": set(), "datareader_qos": set()}
    lib, _, prof = profile_name.partition("::")
    root = ET.parse(profile_file).getroot()
    for L in root.findall("qos_library"):
        if L.get("name") != lib:
            continue
        for P in L.findall("qos_profile"):
            if P.get("name") == prof:
                for kind in out:
                    for e in P.findall(kind):
                        if e.get("topic_filter"):
                            out[kind].add(e.get("topic_filter"))
    return out


@check("Topics")
def check_topics() -> Iterator[Finding]:
    where = "libs/py/fw/topics.py"
    if not (repo.ROOT / "libs" / "py" / "fw" / "topics.py").exists():
        if has_idl():
            yield Finding(ERROR, where, "missing: the list of known topics and their types")
        return
    try:
        topics = import_fw("topics").TOPICS
    except Exception as e:
        yield Finding(ERROR, where, f"can't be imported: {type(e).__name__}: {e}")
        return
    idl_types, _, _ = load_idl()
    externals = external_specs()
    external_topics = {t: (n, spec) for n, s in externals.items() if isinstance(s, dict)
                       for t, spec in (s.get("topics") or {}).items()}
    for topic, type_name in topics.items():
        if type_name not in idl_types:
            yield Finding(ERROR, where, f"topic '{topic}' uses type {type_name}, which no IDL file defines")
        # naming conventions (docs/WORKFLOW.md)
        if topic.startswith("_") and not topic.startswith("_sys/"):
            yield Finding(ERROR, where, f"topic '{topic}': the only '_' prefix allowed is _sys/ (framework plumbing)")
        if topic.startswith("_sys/") and not type_name.startswith("protorig::"):
            yield Finding(ERROR, where, f"topic '{topic}': _sys/ topics carry framework types (protorig::...)")
        if type_name.startswith("protorig::") and not topic.startswith("_sys/"):
            yield Finding(ERROR, where, f"topic '{topic}' carries framework type {type_name}; name it _sys/...")
    for topic, (node, spec) in external_topics.items():
        if topic not in topics:
            yield Finding(ERROR, where, f"external/{node} publishes '{topic}', but it isn't in TOPICS")
        elif isinstance(spec, dict) and spec.get("type") != topics[topic]:
            yield Finding(ERROR, where, f"topic '{topic}': external/{node} says type {spec.get('type')}, "
                                        f"TOPICS says {topics[topic]}")
    qos_file = repo.ROOT / "qos" / "topics.xml"
    if not qos_file.exists():
        return
    try:
        filters = qos_topic_filters(qos_file, DEFAULT_PROFILE)
    except ET.ParseError:
        return          # reported by the QoS check
    for kind, label in (("datawriter_qos", "writer"), ("datareader_qos", "reader")):
        for topic in sorted(set(topics) - filters[kind]):
            yield Finding(WARN, "qos/topics.xml", f"no {label} QoS for topic '{topic}': it will use Connext defaults")
        for f in sorted(filters[kind] - set(topics)):
            if not any(ch in f for ch in "*?["):
                yield Finding(WARN, "qos/topics.xml", f"{label} QoS for '{f}', which is not a known topic "
                                                      "(typo, or add it to fw/topics.py)")


# ---------------------------------------------------------------------------
# 4. External nodes: description and flash lock
# ---------------------------------------------------------------------------

def lock_path(node: str) -> Path:
    return repo.ROOT / "external" / node / "flashed.lock"


def sha256(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


@check("External nodes and flash locks")
def check_externals() -> Iterator[Finding]:
    base = repo.ROOT / "external"
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        if not d.is_dir():
            continue
        where = f"external/{d.name}"
        f = d / "external.yaml"
        if not f.exists():
            yield Finding(WARN, where, "no external.yaml: its topics and offered QoS can't be checked")
        else:
            try:
                spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                for topic, t in (spec.get("topics") or {}).items():
                    w = (t or {}).get("writer") or {}
                    for k in sorted(set(w) - {"reliability", "durability", "deadline", "liveliness_lease", "ownership"}):
                        yield Finding(ERROR, where, f"external.yaml topic '{topic}': unknown writer key '{k}'")
            except (yaml.YAMLError, AttributeError) as e:
                yield Finding(ERROR, where, f"external.yaml can't be read: {e}".replace("\n", " "))

        idl_dir = repo.ROOT / "interfaces" / "external" / d.name
        lock = lock_path(d.name)
        if not lock.exists():
            if idl_dir.is_dir() and any(idl_dir.glob("*.idl")):
                yield Finding(WARN, where, f"not locked: once the board is confirmed to run these IDL files, "
                                           f"run `protorig lock {d.name}`")
            continue
        try:
            data = yaml.safe_load(lock.read_text(encoding="utf-8")) or {}
            files = data.get("files") or {}
        except (yaml.YAMLError, AttributeError):
            yield Finding(ERROR, rel(lock), "unreadable; regenerate it with `protorig lock`")
            continue
        for path, digest in files.items():
            p = repo.ROOT / path
            if not p.exists():
                yield Finding(ERROR, where, f"{path} was flashed but no longer exists in the repo")
            elif sha256(p) != digest:
                yield Finding(WARN, where, f"{path} changed since {d.name} was flashed ({data.get('locked', '?')}): "
                                           f"regenerate and reflash, then run `protorig lock {d.name}`")
        if idl_dir.is_dir():
            for p in sorted(idl_dir.glob("*.idl")):
                if rel(p) not in files:
                    yield Finding(WARN, where, f"{rel(p)} is not in the flash lock; run `protorig lock {d.name}` "
                                               "once the board runs it")


# ---------------------------------------------------------------------------
# 5. QoS: valid XML, loads in Connext, compatible with external writers
# ---------------------------------------------------------------------------

DURABILITY_RANK = {"VOLATILE": 0, "TRANSIENT_LOCAL": 1, "TRANSIENT": 2, "PERSISTENT": 3}
LIVELINESS_RANK = {"AUTOMATIC": 0, "MANUAL_BY_PARTICIPANT": 1, "MANUAL_BY_TOPIC": 2}


def _name(kind) -> str:
    return str(kind).split(".")[-1]


def _secs(duration) -> float:
    return INFINITE if duration.sec >= INFINITE else duration.sec + duration.nanosec / 1e9


def _offer_from_yaml(w: dict) -> dict:
    def dur(v):
        return INFINITE if v in (None, "infinite", "inf") else float(v)
    return {"reliability": str(w.get("reliability", "BEST_EFFORT")).upper(),
            "durability": str(w.get("durability", "VOLATILE")).upper(),
            "deadline": dur(w.get("deadline")),
            "liveliness_kind": "AUTOMATIC",
            "liveliness_lease": dur(w.get("liveliness_lease")),
            "ownership": str(w.get("ownership", "SHARED")).upper()}


def _qos_dict(q) -> dict:
    return {"reliability": _name(q.reliability.kind), "durability": _name(q.durability.kind),
            "deadline": _secs(q.deadline.period), "liveliness_kind": _name(q.liveliness.kind),
            "liveliness_lease": _secs(q.liveliness.lease_duration), "ownership": _name(q.ownership.kind)}


def incompatibilities(offered: dict, requested: dict) -> list[str]:
    """Why a reader requesting `requested` would NOT match a writer offering `offered`
    (DDS request/offer rules). Empty list = compatible."""
    why = []
    if requested["reliability"] == "RELIABLE" and offered["reliability"] != "RELIABLE":
        why.append("reader wants RELIABLE, writer offers BEST_EFFORT")
    if DURABILITY_RANK.get(requested["durability"], 0) > DURABILITY_RANK.get(offered["durability"], 0):
        why.append(f"reader wants {requested['durability']} durability, writer offers {offered['durability']}")
    if requested["deadline"] < offered["deadline"]:
        why.append(f"reader wants a {_fmt_s(requested['deadline'])} deadline, writer offers {_fmt_s(offered['deadline'])}")
    if LIVELINESS_RANK.get(requested["liveliness_kind"], 0) > LIVELINESS_RANK.get(offered["liveliness_kind"], 0):
        why.append(f"reader wants {requested['liveliness_kind']} liveliness, writer offers {offered['liveliness_kind']}")
    if requested["liveliness_lease"] < offered["liveliness_lease"]:
        why.append(f"reader wants a {_fmt_s(requested['liveliness_lease'])} liveliness lease, "
                   f"writer offers {_fmt_s(offered['liveliness_lease'])}")
    if requested["ownership"] != offered["ownership"]:
        why.append(f"ownership differs ({requested['ownership']} vs {offered['ownership']})")
    return why


def _fmt_s(v: float) -> str:
    return "infinite" if v >= INFINITE else f"{v:g} s"


def load_qos_provider(dds):
    files = [str(repo.ROOT / "qos" / f) for f in QOS_FILES if (repo.ROOT / "qos" / f).exists()]
    return dds.QosProvider(";".join(files))


@check("QoS")
def check_qos() -> Iterator[Finding]:
    qdir = repo.ROOT / "qos"
    present = [f for f in QOS_FILES if (qdir / f).exists()]
    if not present:
        return
    for f in present:
        try:
            ET.parse(qdir / f)
        except ET.ParseError as e:
            yield Finding(ERROR, f"qos/{f}", f"not valid XML: {e} (note: XML comments may not contain '--')")
            return
    if "topics.xml" not in present:
        yield Finding(ERROR, "qos/", "topics.xml is missing: it holds the default profile")
        return
    dds = connext()
    if dds is None:
        yield Finding(WARN, "qos/", "compatibility checks skipped: the Connext Python package isn't installed (run bootstrap)")
        return
    try:
        provider = load_qos_provider(dds)
    except Exception as e:
        yield Finding(ERROR, "qos/", f"Connext refuses to load the QoS files: {e}")
        return
    try:
        variants = list(provider.qos_profiles(VARIANT_LIBRARY)) if VARIANT_LIBRARY in provider.qos_profile_libraries else []
    except Exception:
        variants = []
    for v in variants:
        if not v.startswith("Variant."):
            yield Finding(WARN, "qos/variants.xml", f"profile '{v}': variant names should start with 'Variant.'")
    profiles = [DEFAULT_PROFILE] + [f"{VARIANT_LIBRARY}::{v}" for v in variants]

    try:
        topics = import_fw("topics").TOPICS
    except Exception:
        topics = {}

    def effective(profile: str, topic: str):
        provider.default_profile = profile
        return _qos_dict(provider.get_topic_datawriter_qos(topic)), _qos_dict(provider.get_topic_datareader_qos(topic))

    try:
        effective(DEFAULT_PROFILE, "x")
    except Exception as e:
        yield Finding(ERROR, "qos/topics.xml", f"profile {DEFAULT_PROFILE} can't be used: {e}")
        return

    # (a) every profile must keep matching what external nodes offer, and the
    #     default writer (used by sim twins) must offer exactly what the real node does.
    for node, spec in external_specs().items():
        if not isinstance(spec, dict):
            continue
        for topic, t in (spec.get("topics") or {}).items():
            offered = _offer_from_yaml((t or {}).get("writer") or {})
            for prof in profiles:
                _, reader = effective(prof, topic)
                for why in incompatibilities(offered, reader):
                    yield Finding(ERROR, "qos/", f"{prof}: readers of '{topic}' would never match {node}: {why}")
            twin_writer, _ = effective(DEFAULT_PROFILE, topic)
            diffs = [k for k in offered if twin_writer[k] != offered[k]]
            if diffs:
                yield Finding(ERROR, "qos/topics.xml", f"writer QoS for '{topic}' must mirror what {node} offers "
                              f"(so the sim twin behaves like the real node); differs in: {', '.join(diffs)}")

    # (b) within each profile, our own writers and readers must match each other;
    #     across the default and a variant they should too, or the variant only
    #     works when applied to every node at once.
    external_topics = {t for s in external_specs().values() if isinstance(s, dict) for t in (s.get("topics") or {})}
    for topic in topics:
        if topic in external_topics:
            continue
        for prof in profiles:
            w, r = effective(prof, topic)
            for why in incompatibilities(w, r):
                yield Finding(ERROR, "qos/", f"{prof}: '{topic}' writer and reader don't match: {why}")
        base_w, base_r = effective(DEFAULT_PROFILE, topic)
        for prof in profiles[1:]:
            w, r = effective(prof, topic)
            mixed = incompatibilities(w, base_r) + incompatibilities(base_w, r)
            if mixed:
                yield Finding(WARN, "qos/variants.xml", f"{prof.split('::')[1]} changes '{topic}' incompatibly "
                              f"({mixed[0]}): apply it to every node at once (target '*'), "
                              "or nodes on the default profile stop receiving")
