# external/ — systems built outside this repo

One folder per external node (e.g. `tc397/`): `README.md` (how to build/flash, topic, domain, IP), `external.yaml` (topics it publishes and the QoS its writer offers; `check` uses it to keep every QoS profile compatible), known-good reference settings, and `flashed.lock` (generated: fingerprints of the IDL the running firmware was built from).

Rule: anything here belongs to another project and is locked to what is actually running. After reflashing, run `./protorig lock <name>`.
