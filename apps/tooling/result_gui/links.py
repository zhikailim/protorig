"""result_gui's links: which writer would reach which reader (R2), worked out
from Connext's discovery data with the rules in fw.qos_match (the ones
`protorig check` uses). Pure functions on plain records: main.py converts
discovery samples with endpoint() and participant(); tests compare the result
with what Connext actually matched.
"""
from __future__ import annotations

from dataclasses import dataclass

from fw.qos_match import incompatibilities, kind_name, partitions_overlap, qos_dict


@dataclass(frozen=True)
class Endpoint:
    key: str                   # the writer's or reader's own key
    participant: str           # its participant's key
    topic: str
    type_name: str
    qos: dict                  # fw.qos_match.qos_dict()
    partitions: tuple[str, ...]


@dataclass(frozen=True)
class Participant:
    key: str
    name: str                  # "node/app" for every protorig process
    ips: tuple[str, ...]       # its UDPv4 addresses


def endpoint(d) -> Endpoint:
    """A DCPSPublication or DCPSSubscription sample as an Endpoint."""
    return Endpoint(str(d.key), str(d.participant_key), d.topic_name, d.type_name,
                    qos_dict(d), tuple(d.partition.name))


def participant(d) -> Participant:
    """A discovered participant's data. The UDPv4 address carries the IPv4 in its last 4 bytes."""
    ips = tuple(".".join(str(b) for b in list(loc.address)[-4:])
                for loc in d.default_unicast_locators if kind_name(loc.kind) == "UDPv4")
    return Participant(str(d.key), d.participant_name.name or "", ips)


def matches(pubs: list[Endpoint], subs: list[Endpoint]) -> list[tuple[Endpoint, Endpoint]]:
    """Every (writer, reader) pair Connext would match: same topic and type,
    a shared partition, and QoS the writer offers at least as strongly as asked."""
    by_topic: dict[str, list[Endpoint]] = {}
    for s in subs:
        by_topic.setdefault(s.topic, []).append(s)
    return [(w, r) for w in pubs for r in by_topic.get(w.topic, [])
            if w.type_name == r.type_name and partitions_overlap(list(w.partitions), list(r.partitions))
            and not incompatibilities(w.qos, r.qos)]


def place(p: Participant | None, nodes: dict[str, dict]) -> tuple[str, str | None] | None:
    """(node, app) for a participant: from its "node/app" name; else, for a
    device whose name we don't control (the real TC397), the external node whose
    ip: is its address (app None: the device itself). None: not part of the scenario."""
    if p is None:
        return None
    node, _, app = p.name.partition("/")
    if node in nodes and app:
        return node, app
    for n, spec in nodes.items():
        if spec.get("external") and str(spec.get("ip", "")) in p.ips:
            return n, None
    return None
