# templates/ — starter kits

`app_py/` (tooling apps and sim twins: `main.py` with a numbered recipe, `README.md` with a behaviour table, `test_<name>.py` with three standard tests that pass unedited) and `scenario/` (`scenario.yaml`, six-section `README.md`, stage 1 `concept.md`). `app_cpp/` arrives with the C++ step. Used only through `./protorig new app|scenario <name>`, which copies a kit and replaces `{{name}}` and `{{description}}`.

Templates are copied once, so they stay thin: evolving code belongs in `libs/`. Every template is tested (created, built and run) by the test suite.
