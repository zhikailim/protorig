# interfaces/ — the data contract (IDL)

- `common/`: types designed for our scenarios. This repo is the source of truth, even when an external node uses them.
- `external/<name>/`: types that originated in another project (e.g. the TC397's ETAS project).

Now: `common/protorig.idl` (framework types, `_sys/` topics), `common/alerts.idl` (`Alert`), `external/tc397/` (the TC397's types, byte for byte).

Rules: IDL file names are unique across the repo; fully-qualified type names are defined once; the module name `fw` is reserved. Python versions live in `libs/py/fw/types.py`; `protorig check` compares them field by field (names, types, bounds, keys, extensibility). Known topics and their types: `libs/py/fw/topics.py`.

Topic naming: see [naming conventions](../docs/WORKFLOW.md#naming-conventions).
