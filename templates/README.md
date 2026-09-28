# templates/ — starter kits

`app_cpp/`, `app_py/`, `app_sim/`, `scenario/`, `external/`. Used only through `./protorig new <kind> <name>`, which copies a kit and replaces `{{name}}` and `{{description}}`.

Templates are copied once, so they stay thin: evolving code belongs in `libs/`. Every template is tested (created, built and run) by the test suite.
