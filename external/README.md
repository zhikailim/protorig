# external/ — systems built outside this repo

One folder per external node (e.g. `tc397/`): `README.md` (how to build/flash, topic, domain, IP), known-good reference settings, and `flashed.lock` (fingerprints of the IDL the running firmware was built from).

Rule: anything here belongs to another project and is locked to what is actually running. After reflashing, run `./protorig lock <name>`.
