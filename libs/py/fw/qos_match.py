"""
fw.qos_match — would a DataReader match a DataWriter? The DDS request/offer
rules on the QoS this repo uses, plus topic, type and partition.

Shared by `protorig check` (QoS in qos/ and external.yaml can't silently stop
data flowing) and result_gui (draws a link only where Connext would match).
tests/test_contract.py and result_gui's tests compare it with Connext itself.
"""
from __future__ import annotations

INFINITE = 2147483647          # Connext's "infinite" duration, in seconds
DURABILITY_RANK = {"VOLATILE": 0, "TRANSIENT_LOCAL": 1, "TRANSIENT": 2, "PERSISTENT": 3}
LIVELINESS_RANK = {"AUTOMATIC": 0, "MANUAL_BY_PARTICIPANT": 1, "MANUAL_BY_TOPIC": 2}


def kind_name(kind) -> str:
    return str(kind).split(".")[-1]


def secs(duration) -> float:
    return INFINITE if duration.sec >= INFINITE else duration.sec + duration.nanosec / 1e9


def qos_dict(q) -> dict:
    return {"reliability": kind_name(q.reliability.kind), "durability": kind_name(q.durability.kind),
            "deadline": secs(q.deadline.period), "liveliness_kind": kind_name(q.liveliness.kind),
            "liveliness_lease": secs(q.liveliness.lease_duration), "ownership": kind_name(q.ownership.kind)}


def incompatibilities(offered: dict, requested: dict) -> list[str]:
    """Why a reader requesting `requested` would NOT match a writer offering `offered`
    (DDS request/offer rules). Empty list = compatible."""
    why = []
    if requested["reliability"] == "RELIABLE" and offered["reliability"] != "RELIABLE":
        why.append("reader wants RELIABLE, writer offers BEST_EFFORT")
    if DURABILITY_RANK.get(requested["durability"], 0) > DURABILITY_RANK.get(offered["durability"], 0):
        why.append(f"reader wants {requested['durability']} durability, writer offers {offered['durability']}")
    if requested["deadline"] < offered["deadline"]:
        why.append(f"reader wants a {fmt_s(requested['deadline'])} deadline, writer offers {fmt_s(offered['deadline'])}")
    if LIVELINESS_RANK.get(requested["liveliness_kind"], 0) > LIVELINESS_RANK.get(offered["liveliness_kind"], 0):
        why.append(f"reader wants {requested['liveliness_kind']} liveliness, writer offers {offered['liveliness_kind']}")
    if requested["liveliness_lease"] < offered["liveliness_lease"]:
        why.append(f"reader wants a {fmt_s(requested['liveliness_lease'])} liveliness lease, "
                   f"writer offers {fmt_s(offered['liveliness_lease'])}")
    if requested["ownership"] != offered["ownership"]:
        why.append(f"ownership differs ({requested['ownership']} vs {offered['ownership']})")
    return why


def fmt_s(v: float) -> str:
    return "infinite" if v >= INFINITE else f"{v:g} s"


def partitions_overlap(a: list[str], b: list[str]) -> bool:
    """Writer and reader partitions (no partition = the default one, ""). Exact
    names only: this repo uses no wildcards."""
    return bool(set(a or [""]) & set(b or [""]))
