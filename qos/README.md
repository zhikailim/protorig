# qos/ — how data behaves

- `base.xml`: participant defaults shared by every node (no IP addresses).
- `topics.xml`: behaviour per topic, assigned by topic name. Apps contain no QoS code.
- `variants.xml`: alternatives the Control Panel can switch to (the app restarts with the variant).

Per-node network settings (interface restriction, peers) are generated into `build/` as `node_qos.xml`, never hand-written. `./protorig check` rejects variants incompatible with external nodes.
