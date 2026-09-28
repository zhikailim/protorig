# apps/ — the bricks

- `vehicle/<app>/`: C++17, CMake. Anything that would run in a vehicle. No Python.
- `tooling/<app>/`: Python. GUIs, control panel, stand-ins.
- `sim/<twin>/`: Python stand-ins for external nodes. Never deployed.

A folder with `CMakeLists.txt` is a C++ app; a folder with `main.py` is a Python app. Every app has a `README.md` and a `test_<app>.py`. Create with `./protorig new app <name> --lang cpp|py`.
