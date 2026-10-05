"""
fw.types — Python versions of the IDL types in interfaces/.

Why hand-written? Python needs no code generation, so the tooling runs without
rtiddsgen. `protorig check` compares every class here with its IDL, field by
field (type names, member names and types, keys, bounds, extensibility), so
they cannot silently drift. When you add or change IDL, update this file too;
check tells you exactly what is missing or different.

Vehicle apps (C/C++) never use this file; they use rtiddsgen output.
"""
from dataclasses import field
from enum import IntEnum

import rti.idl as idl


def _enum(qualified_name: str):
    """Make an IntEnum an IDL enum with its fully-qualified name.

    For structs, idl.type_name() sets the IDL name. For enums the Python API
    ignores it and uses the class __name__, so we set that instead; otherwise
    alerts::Severity would appear on the wire as plain "Severity".
    """
    def wrap(cls):
        cls.__name__ = qualified_name
        return idl.enum(cls)
    return wrap


# Extensibility: IDL @appendable (and IDL with no annotation, the rtiddsgen
# default) is called `extensible` in the Python API.
APPENDABLE = idl.extensible


# ============================================================================
# interfaces/external/tc397/  (FROZEN: must match the TC397 firmware)
# ============================================================================

@idl.struct(type_annotations=[idl.type_name("std_msgs::msg::Time"), APPENDABLE])
class Time:
    sec: idl.int32 = 0
    nanosec: idl.uint32 = 0


@idl.struct(type_annotations=[idl.type_name("std_msgs::msg::Header"), APPENDABLE])
class Header:
    stamp: Time = field(default_factory=Time)
    frame_id: str = ""


@idl.struct(type_annotations=[idl.type_name("sensor_msgs::msg::Temperature"), APPENDABLE])
class Temperature:
    """Published by the TC397 on "Example Temperature": a temperature in degrees Celsius."""
    header: Header = field(default_factory=Header)
    temperature: float = 0.0     # IDL double
    variance: float = 0.0        # IDL double


# ============================================================================
# interfaces/common/protorig.idl  (framework plumbing, _sys/ topics)
# ============================================================================

@idl.struct(
    type_annotations=[idl.type_name("protorig::NodeStatus"), APPENDABLE],
    member_annotations={
        "node": [idl.key, idl.bound(32)],
        "app": [idl.key, idl.bound(32)],
        "os": [idl.bound(16)],
        "qos_variant": [idl.bound(64)],
    },
)
class NodeStatus:
    node: str = ""
    app: str = ""
    os: str = ""
    qos_variant: str = ""
    seq: idl.uint64 = 0
    cpu_load: idl.float32 = 0.0
    stamp_ns: idl.int64 = 0


@_enum("protorig::Command")
class Command(IntEnum):
    CMD_SET_QOS_VARIANT = 0
    CMD_START_APP = 1
    CMD_STOP_APP = 2
    CMD_KILL_APP = 3
    CMD_SET_PARAM = 4


@idl.struct(
    type_annotations=[idl.type_name("protorig::DemoControl"), APPENDABLE],
    member_annotations={
        "target_node": [idl.key, idl.bound(32)],
        "target_app": [idl.bound(32)],
        "arg": [idl.bound(64)],
    },
)
class DemoControl:
    target_node: str = ""
    target_app: str = ""
    cmd_id: idl.uint64 = 0
    command: Command = Command.CMD_SET_QOS_VARIANT
    arg: str = ""
    value: float = 0.0


@_enum("protorig::AppStateKind")
class AppStateKind(IntEnum):
    APP_NOT_RUNNING = 0
    APP_UNAVAILABLE = 1
    APP_STARTING = 2
    APP_RUNNING = 3
    APP_RESTARTING = 4
    APP_STOPPING = 5
    APP_STOPPED = 6
    APP_KILLED = 7
    APP_CRASHED = 8


@idl.struct(
    type_annotations=[idl.type_name("protorig::AppState"), APPENDABLE],
    member_annotations={
        "node": [idl.key, idl.bound(32)],
        "app": [idl.key, idl.bound(32)],
        "detail": [idl.bound(128)],
        "scenario": [idl.bound(64)],
    },
)
class AppState:
    node: str = ""
    app: str = ""
    state: AppStateKind = AppStateKind.APP_NOT_RUNNING
    exit_code: idl.int32 = 0
    restarts: idl.uint32 = 0
    detail: str = ""
    scenario: str = ""
    changed_at_ns: idl.int64 = 0


# ============================================================================
# interfaces/common/alerts.idl
# ============================================================================

@_enum("alerts::Severity")
class Severity(IntEnum):
    SEVERITY_INFO = 0
    SEVERITY_WARNING = 1
    SEVERITY_CRITICAL = 2


@idl.struct(
    type_annotations=[idl.type_name("alerts::Alert"), APPENDABLE],
    member_annotations={
        "source": [idl.key, idl.bound(64)],
        "alert_id": [idl.key, idl.bound(32)],
        "message": [idl.bound(128)],
    },
)
class Alert:
    source: str = ""
    alert_id: str = ""
    severity: Severity = Severity.SEVERITY_INFO
    message: str = ""
    value: float = 0.0
    stamp_ns: idl.int64 = 0


# Every IDL type -> its Python class. `protorig check` requires one entry per
# struct and enum found in interfaces/.
BY_IDL_NAME = {
    "std_msgs::msg::Time": Time,
    "std_msgs::msg::Header": Header,
    "sensor_msgs::msg::Temperature": Temperature,
    "protorig::NodeStatus": NodeStatus,
    "protorig::Command": Command,
    "protorig::DemoControl": DemoControl,
    "protorig::AppStateKind": AppStateKind,
    "protorig::AppState": AppState,
    "alerts::Severity": Severity,
    "alerts::Alert": Alert,
}
