# interfaces/external/tc397

The TC397's data types, copied **byte for byte** from the ETAS / Connext Micro project. They must match what is compiled into the firmware: type names, module nesting, member names and order, and extensibility. A mismatch doesn't raise an error; data just stops arriving.

- `Temperature.idl` (includes `Header.idl`, which includes `Time.idl`): `sensor_msgs::msg::Temperature`, published on topic `Example Temperature`. The tire pressure is carried in the `temperature` field.
- These are ROS 2 message definitions, but the topic name isn't ROS-mangled (`rt/...`), so this is plain DDS.
- No extensibility annotation: Pro and Micro both use the rtiddsgen default (appendable). Keep it that way.

Changing them means regenerating and reflashing the ECU. `external/tc397/flashed.lock` records the fingerprints of what is on the board; `protorig check` warns when these files drift from it. After reflashing: `./protorig lock tc397`.
