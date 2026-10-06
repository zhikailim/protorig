# protorig.ps1 — launcher for Windows PowerShell.
#
# Finds the repo's Python (the .venv made by bootstrap, else the py launcher or
# python on PATH), makes the shared Python libs importable, and hands over to
# cli/main.py. If PowerShell blocks scripts ("running scripts is disabled"),
# use protorig.cmd instead: same behaviour, no execution policy involved.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

$Venv = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $Venv) {
    $Py = @($Venv)
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $Py = @("py", "-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Py = @("python")
} else {
    Write-Error "protorig: Python 3 not found. Run .\bootstrap\windows.ps1 first."
    exit 2
}

# Text files and output are UTF-8 on every OS (Windows defaults to its old code page).
$env:PYTHONUTF8 = "1"

$LibsPy = Join-Path $Root "libs\py"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$LibsPy;$env:PYTHONPATH" } else { $LibsPy }

$Exe = $Py[0]
$PreArgs = @()
if ($Py.Length -gt 1) { $PreArgs = $Py[1..($Py.Length - 1)] }
& $Exe @PreArgs (Join-Path $Root "cli\main.py") @args
exit $LASTEXITCODE
