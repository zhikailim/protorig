"""
fw.topics — every topic name the framework knows, with its IDL type.

Tooling (GUIs, `protorig send`, tests) looks topics up here. `protorig check`
cross-checks this list against qos/topics.xml (every topic has a behaviour),
interfaces/ (every type exists) and external/*/external.yaml (external topics
keep the name and type the firmware uses).

Naming (docs/WORKFLOW.md): scenario data gets plain names; framework plumbing
gets the _sys/ prefix; external systems keep whatever name they use.
"""

TOPICS = {
    # external systems (names fixed by their firmware)
    "Example Temperature": "sensor_msgs::msg::Temperature",   # TC397 tire pressure
    # scenario data
    "Alert": "alerts::Alert",
    # framework plumbing
    "_sys/NodeStatus": "protorig::NodeStatus",
    "_sys/DemoControl": "protorig::DemoControl",
}


def type_of(topic: str):
    """The Python class for a topic, e.g. type_of("Alert") -> fw.types.Alert."""
    from fw import types
    try:
        return types.BY_IDL_NAME[TOPICS[topic]]
    except KeyError:
        raise KeyError(f"unknown topic '{topic}'; known topics: {', '.join(sorted(TOPICS))}") from None
