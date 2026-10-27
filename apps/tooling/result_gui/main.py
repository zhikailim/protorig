"""
result_gui — the "under the hood" screen: the rig as it really is, where the
data goes, and what happens when something fails (docs/requirements/result_gui.md).

Topics in : _sys/NodeStatus (with each app's writer rates), _sys/AppState, Alert,
            the topics of each external device (external/<node>/external.yaml),
            Connext's discovery data (for links)
Topics out: none but its heartbeat
Arguments : --silence (seconds, default 2), plus the standard ones

Machines in their layers, up / lost / not seen yet, app states, captions (R1,
R3); links between writers and readers, lit while data flows (R2, R5); the
alert banner (R4). DDS is read here only; model.py works out what to show,
window.py draws it.

Run alone:  ./protorig run <scenario> --app result_gui
"""
import sys
import time

import rti.connextdds as dds

from fw.app import DDS_ERRORS, ROOT, App, log
from fw.gui import run_gui
from fw.scenario import entries, external_topics, layer_of, load_nodes
from fw.topics import TOPICS
import links as L
from model import Model

POLL = 0.25          # how often heartbeats and app rows are read (seconds)
LINKS_EVERY = 1.0    # how often links are worked out again from discovery (seconds)

app = App("result_gui", "The rig as it really is: machines, data links, failures, alerts")
silence = app.arg("--silence", 2.0, "seconds without data before an external device shows 'no data'")


def scenario_nodes() -> dict:
    nodes = {}
    if app.scenario and not app.help_requested:
        for name, spec in load_nodes(ROOT, app.scenario).items():
            nodes[name] = {"layer": layer_of(spec), "external": bool(spec.get("external")),
                           "run": [a for a, _ in entries(spec, name)], "ip": spec.get("ip")}
    return nodes


try:
    nodes = scenario_nodes()
except ValueError as e:
    log(app.who, "ERROR", str(e))
    app.stop("can't read the scenario")
    app.run()
    sys.exit(2)
model = Model(nodes, silence)


def state_of(info) -> str:
    s = info.state.instance_state
    if s == dds.InstanceState.ALIVE:
        return "alive"
    return "stopped" if s == dds.InstanceState.NOT_ALIVE_DISPOSED else "lost"


def keys(reader, info):
    """(node, app) of an instance, also for a sample without data (a dispose)."""
    if info.valid:
        return None
    try:
        k = reader.key_value(info.instance_handle)
        return k.node, k.app
    except DDS_ERRORS:                     # the instance is gone already
        return None


if not app.help_requested:
    # Heartbeats and app rows are read (not taken) every POLL: each instance's
    # state is the point (alive, lost after its 3 s lease, disposed), not each sample.
    beats = app.reader("_sys/NodeStatus")
    rows = app.reader("_sys/AppState")
    alerts = app.reader("Alert")       # R4: active alerts are ALIVE instances; cleared = disposed

    def poll():
        now = time.monotonic()
        for d, info in beats.read():
            key = (d.node, d.app) if info.valid else keys(beats, info)
            if key:
                model.heartbeat(*key, d.seq if info.valid else None, state_of(info), now,
                                str(info.publication_handle),
                                {r.topic: r.per_s for r in d.rates} if info.valid else None)
        for d, info in rows.read():
            key = (d.node, d.app) if info.valid else keys(rows, info)
            if not key:
                continue
            if info.valid and state_of(info) == "alive":
                model.app_row(*key, d.state.name, d.detail, now)
            else:                          # the agent stopped or went away: its rows are gone
                model.app_row(*key, None, "", now)
        for d, info in alerts.read():
            if info.valid:
                key = (d.source, d.alert_id)
            else:
                try:
                    k = alerts.key_value(info.instance_handle)
                    key = (k.source, k.alert_id)
                except DDS_ERRORS:             # the instance is gone already
                    continue
            st = state_of(info)
            if info.valid and st != "stopped":
                model.alert(*key, int(d.severity), d.message, st, now)
            elif st == "stopped":
                model.alert(*key, 0, "", None, now)
        for line in model.tick(now):
            log(app.who, "INFO", line)
    app.every(POLL, poll)

    # External devices send no heartbeat: their data arriving is the sign of life (R5).
    by_topic: dict[str, list[str]] = {}
    for n, spec in nodes.items():
        if spec["external"]:
            for t in external_topics(ROOT, n):
                by_topic.setdefault(t, []).append(n)
    for topic, owners in by_topic.items():
        if topic not in TOPICS:
            log(app.who, "WARN", f"{', '.join(owners)}: topic '{topic}' isn't in fw/topics.py; can't watch it")
            continue
        app.on_data(app.reader(topic),
                    lambda _s, owners=owners: [model.data(n, time.monotonic()) for n in owners])

    # Links (R2): Connext's own discovery data, read with its built-in readers. Every
    # participant already receives it, so this puts nothing extra on the network.
    builtin = app.participant.builtin_subscriber
    pubs = dds.PublicationBuiltinTopicData.DataReader.find_all_by_topic(builtin, "DCPSPublication")[0]
    subs = dds.SubscriptionBuiltinTopicData.DataReader.find_all_by_topic(builtin, "DCPSSubscription")[0]

    def alive(reader) -> list:
        return [L.endpoint(d) for d, info in reader.read()
                if info.valid and info.state.instance_state == dds.InstanceState.ALIVE]

    def find_links():
        who = {}
        for h in app.participant.discovered_participants():
            try:
                p = L.participant(app.participant.discovered_participant_data(h))
                who[p.key] = L.place(p, nodes)
            except DDS_ERRORS:                 # it left meanwhile
                pass
        found = [(w.topic, who.get(w.participant), who.get(r.participant))
                 for w, r in L.matches(alive(pubs), alive(subs))]
        model.links([(t, w, r) for t, w, r in found if w and r])
    app.every(LINKS_EVERY, find_links)


def make_window():
    from window import Window        # imported here: Qt only once the app is set up (fw.gui)
    return Window(app.scenario, model)


sys.exit(run_gui(app, make_window))
