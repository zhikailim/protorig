# tests/ — framework tests

Tests of the framework itself (templates, `libs/`, CLI, data contract). App tests live next to each app; scenario tests next to each scenario. `./protorig test` runs check, then all of them.

- `test_check.py`: scenario, app and hygiene rules, each broken on purpose in a throwaway repo; garbage-YAML fuzzing; the launchers end to end.
- `test_python_layer.py`: `fw.app` driven over DDS through a probe app (commands, variant restart, parameters, incompatible-QoS warning, error containment), garbage-command fuzzing, and templates: a freshly generated app must pass its own tests unedited.
- `test_contract.py`: IDL, Python types, topics, flash locks, `protorig lock`, QoS compatibility, each broken on purpose. **Live** (needs a Connext license): the checker's match/no-match verdict equals real Connext's, and every framework topic round-trips on the wire.
