# apps/ — the bricks

- `vehicle/<app>/`: C++17 by default, or C when safety requirements call for it (Connext Pro C API, Micro, or Cert). CMake. Anything that would run in a vehicle. Never Python.
- `tooling/<app>/`: Python. GUIs, control panel, stand-ins.
- `sim/<twin>/`: Python stand-ins for external nodes. Never deployed.

A folder with `CMakeLists.txt` is a C or C++ app (by its source files); a folder with `main.py` is a Python app. Every app has a `README.md` and a `test_<app>.py`. Create with `./protorig new app <name> --lang cpp|py`.
