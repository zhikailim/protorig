@echo off
rem protorig.cmd - launcher for Windows cmd.exe, and a fallback for PowerShell
rem when its execution policy blocks protorig.ps1. Same behaviour.
setlocal
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PY=%ROOT%\.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
)

if defined PYTHONPATH (
    set "PYTHONPATH=%ROOT%\libs\py;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%ROOT%\libs\py"
)

rem Text files and output are UTF-8 on every OS (Windows defaults to its old code page).
set "PYTHONUTF8=1"

%PY% "%ROOT%\cli\main.py" %*
exit /b %ERRORLEVEL%
