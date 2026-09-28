# libs/ — shared code (`fw`)

- `cpp/fw/`: C++17, POSIX only (portable to QNX). `fw::App`: standard args, participant, QoS loading, heartbeat, clean shutdown. Plus `fw.cmake` (`fw_app()`).
- `c/fw/` (when C support is built): the same jobs as `fw::App`, in plain C, for safety-critical vehicle apps.
- `py/fw/`: Python. Built: `types.py` (Python versions of every IDL type, checked against the IDL), `topics.py` (known topics and their types). Planned: `app.py`, `testing.py` (test helpers), `widgets/` (GUI parts).

Goes here only when two or more apps need it. Used in place, never copied. A shortcut, never a wall: the underlying Connext objects are always reachable.
