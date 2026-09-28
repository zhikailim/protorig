# libs/ — shared code (`fw`)

- `cpp/fw/`: C++17, POSIX only (portable to QNX). `fw::App`: standard args, participant, QoS loading, heartbeat, clean shutdown. Plus `fw.cmake` (`fw_app()`).
- `py/fw/`: Python. `types.py` (IDL mirrors), `app.py`, `testing.py` (test helpers), `widgets/` (GUI parts).

Goes here only when two or more apps need it. Used in place, never copied. A shortcut, never a wall: the underlying Connext objects are always reachable.
