# qos/ — how data behaves

- `base.xml`: participant defaults shared by every node (no IP addresses).
- `topics.xml`: behaviour per topic, assigned by topic name. Apps contain no QoS code.
- `variants.xml`: alternatives the Control Panel can switch to (the app restarts with the variant).

Libraries: `protorig_base`, `protorig` (profile `Topics` is the default), `protorig_variants` (profiles named `Variant.*`).

Per-node network settings (interface restriction, peers) are generated into `build/` as `node_qos.xml`, never hand-written. Apps load the files as a list (Connext has no include for QoS files).

`./protorig check` asks Connext for each profile's effective QoS and rejects anything that would stop matching an external node (see `external/*/external.yaml`), or where our own writers and readers wouldn't match. XML comments may not contain `--`.
