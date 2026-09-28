# tests/ — framework tests

Tests of the framework itself (templates, `libs/`, CLI, data contract). App tests live next to each app; scenario tests next to each scenario. `./protorig test` runs them all (until that verb exists: `python -m pytest -q tests/`).

- `test_check.py`: every `check` rule, each broken on purpose in a throwaway repo; garbage-YAML fuzzing; the launchers end to end.
