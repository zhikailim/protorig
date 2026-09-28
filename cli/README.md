# cli/ — the protorig command

The real program behind the `protorig` / `protorig.ps1` launchers at the repo root, in Python, one module per verb. Built so far: `check`, `list`. Planned verbs show in `protorig --help` and answer "not built yet".

- `main.py`: argument parsing and dispatch.
- `repo.py`: where things are (scenarios, apps and their kinds), and loading them. The only file that knows the layout.
- `check.py`: static checks. Each check is one function decorated with `@check`; findings are ERROR (breaks something) or WARN (probably a mistake). A crashing check becomes a finding, never a traceback.

Launchers at the repo root: `protorig` (Linux), `protorig.ps1` (PowerShell), `protorig.cmd` (cmd, or when PowerShell blocks scripts). They pick `.venv` Python if bootstrap made one, else the system Python, and add `libs/py` to the import path.
