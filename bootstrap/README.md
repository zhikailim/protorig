# bootstrap/ — one-time machine setup

`linux.sh` (VM, Pi) and `windows.ps1`. Checks tools, creates `.venv/`, finds Connext and the license, identifies which node this machine is, and (Windows) opens the firewall. Safe to re-run; doubles as a health check.

Machine-specific findings go into the git-ignored `.local/`, never into the repo.
