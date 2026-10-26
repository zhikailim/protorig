"""test_links.py — result_gui draws a link exactly where Connext matches (R2, G21, G24).

Writers and readers with QoS that do and don't match are created for real;
result_gui's link rules (links.py) are applied to what discovery reports and
compared, pair by pair, with what each writer actually matched.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
dds = pytest.importorskip("rti.connextdds")
from fw import types as T                    # noqa: E402
from fw.testing import wait_for              # noqa: E402
import links as L                            # noqa: E402

TAG = "lt_"   # every endpoint here is named, so the shared test bus's others are ignored


def _w(p, topic, name, partition=None, **qos):
    pub = dds.Publisher(p, _group(dds.PublisherQos(), partition))
    q = dds.DataWriterQos()
    q.entity_name.name = TAG + name
    _apply(q, **qos)
    return dds.DataWriter(pub, topic, q)


def _r(p, topic, name, partition=None, **qos):
    sub = dds.Subscriber(p, _group(dds.SubscriberQos(), partition))
    q = dds.DataReaderQos()
    q.entity_name.name = TAG + name
    _apply(q, **qos)
    return dds.DataReader(sub, topic, q)


def _group(q, partition):
    if partition:
        q.partition.name = [partition]
    return q


def _apply(q, reliable=None, tl=False, deadline=None, lease=None, exclusive=False):
    if reliable is not None:
        q.reliability.kind = dds.ReliabilityKind.RELIABLE if reliable else dds.ReliabilityKind.BEST_EFFORT
    if tl:
        q.durability.kind = dds.DurabilityKind.TRANSIENT_LOCAL
    if deadline is not None:
        q.deadline.period = dds.Duration.from_seconds(deadline)
    if lease is not None:
        q.liveliness.lease_duration = dds.Duration.from_seconds(lease)
    if exclusive:
        q.ownership.kind = dds.OwnershipKind.EXCLUSIVE


def test_G21_G24_links_agree_with_connext(bus):
    d = bus.domain
    ps = [dds.DomainParticipant(d) for _ in range(5)]
    observer, writers_p, readers_p, typed_w, typed_r = ps
    try:
        pubs_r = dds.PublicationBuiltinTopicData.DataReader.find_all_by_topic(observer.builtin_subscriber, "DCPSPublication")[0]
        subs_r = dds.SubscriptionBuiltinTopicData.DataReader.find_all_by_topic(observer.builtin_subscriber, "DCPSSubscription")[0]
        tw, tr = dds.Topic(writers_p, "Alert", T.Alert), dds.Topic(readers_p, "Alert", T.Alert)
        writers = [
            _w(writers_p, tw, "reliable", reliable=True),
            _w(writers_p, tw, "best_effort", reliable=False),
            _w(writers_p, tw, "transient_local", reliable=True, tl=True),
            _w(writers_p, tw, "deadline_1s", reliable=True, deadline=1),
            _w(writers_p, tw, "partition_a", partition="A", reliable=True),
            _w(writers_p, tw, "exclusive", reliable=True, exclusive=True),
            _w(typed_w, dds.Topic(typed_w, "Alert", T.NodeStatus), "other_type", reliable=True),
        ]
        readers = [
            _r(readers_p, tr, "best_effort", reliable=False),
            _r(readers_p, tr, "reliable", reliable=True),
            _r(readers_p, tr, "transient_local", reliable=True, tl=True),
            _r(readers_p, tr, "deadline_2s", deadline=2),
            _r(readers_p, tr, "deadline_half_s", deadline=0.5),
            _r(readers_p, tr, "partition_a", partition="A"),
            _r(readers_p, tr, "exclusive", exclusive=True),
            _r(readers_p, tr, "lease_5s", lease=5),
            _r(writers_p, tw, "same_participant", reliable=True),        # a reader next to the writers
            _r(typed_r, dds.Topic(typed_r, "Alert", T.NodeStatus), "other_type"),
        ]

        def ours(reader, attr):
            out = {}
            for s, info in reader.read():
                name = getattr(s, attr).name or ""
                if info.valid and name.startswith(TAG):
                    out[name] = s
            return out
        wait_for(lambda: len(ours(pubs_r, "publication_name")) == len(writers)
                 and len(ours(subs_r, "subscription_name")) == len(readers), 15, "discovery of every endpoint")

        # Predicted by result_gui's rules, from discovery data only.
        pub_s, sub_s = ours(pubs_r, "publication_name"), ours(subs_r, "subscription_name")
        name_of = {str(s.key): n for side in (pub_s, sub_s) for n, s in side.items()}
        predicted = {(name_of[w.key], name_of[r.key])
                     for w, r in L.matches([L.endpoint(s) for s in pub_s.values()],
                                           [L.endpoint(s) for s in sub_s.values()])}

        # Matched by Connext. Matching takes a moment; wait until it stops changing.
        def actual():
            out = set()
            for w in writers:
                for h in w.matched_subscriptions:
                    out.add((w.qos.entity_name.name, w.matched_subscription_data(h).subscription_name.name))
            return out
        wait_for(lambda: actual() >= predicted, 10, "Connext to match at least the predicted links")
        import time
        time.sleep(1.0)                      # anything Connext matches beyond the prediction shows up too
        got = actual()
        assert got == predicted, f"only result_gui: {sorted(predicted - got)}\nonly Connext: {sorted(got - predicted)}"
        # The cases that make the test meaningful: some match, some don't, for each rule.
        for pair in [("reliable", "reliable"), ("best_effort", "best_effort"), ("partition_a", "partition_a"),
                     ("exclusive", "exclusive"), ("reliable", "same_participant"), ("other_type", "other_type")]:
            assert tuple(TAG + x for x in pair) in got, pair
        for pair in [("best_effort", "reliable"), ("reliable", "transient_local"), ("reliable", "deadline_2s"),
                     ("reliable", "partition_a"), ("reliable", "exclusive"), ("reliable", "lease_5s"),
                     ("other_type", "reliable"), ("reliable", "other_type")]:
            assert tuple(TAG + x for x in pair) not in got, pair
    finally:
        for p in reversed(ps):
            p.close_contained_entities()
            p.close()


def test_place_by_name_then_by_ip():
    nodes = {"hpc-pi": {"ip": "10.0.0.21"}, "tc397": {"ip": "10.0.0.1", "external": True}}
    P = L.Participant
    assert L.place(P("k", "hpc-pi/hpc_monitor", ("10.0.0.21",)), nodes) == ("hpc-pi", "hpc_monitor")
    assert L.place(P("k", "tc397/tc397_twin", ("10.0.0.99",)), nodes) == ("tc397", "tc397_twin")
    assert L.place(P("k", "", ("10.0.0.1",)), nodes) == ("tc397", None), "the real ECU: by its IP"
    assert L.place(P("k", "whatever", ("10.0.0.21",)), nodes) is None, "only external devices are placed by IP"
    assert L.place(P("k", "test/bus", ("10.0.0.5",)), nodes) is None
    assert L.place(None, nodes) is None
