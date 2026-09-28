# interfaces/ — the data contract (IDL)

- `common/`: types designed for our scenarios. This repo is the source of truth, even when an external node uses them.
- `external/<name>/`: types that originated in another project (e.g. the TC397's ETAS project).

Rules: IDL file names are unique across the repo; fully-qualified type names are defined once; the module name `fw` is reserved. Python mirrors live in `libs/py/fw/types.py` and are contract-tested against these files.

Topic naming: see [naming conventions](../docs/WORKFLOW.md#naming-conventions).
