# tests/ — framework tests

Tests of the framework itself (templates, `libs/`, CLI, data contract). App tests live next to each app; scenario tests next to each scenario. `./protorig test` runs them all (until that verb exists: `python -m pytest -q tests/`).

- `test_check.py`: scenario, app and hygiene rules, each broken on purpose in a throwaway repo; garbage-YAML fuzzing; the launchers end to end.
- `test_contract.py`: IDL, Python types, topics, flash locks, `protorig lock`, QoS compatibility, each broken on purpose. **Live** (needs a Connext license): the checker's match/no-match verdict equals real Connext's, and every framework topic round-trips on the wire.
