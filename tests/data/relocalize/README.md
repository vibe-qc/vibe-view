# Relocalization protocol fixtures

`h2.request.json` and `h2.result.json` are copied from the MPL-2.0 vibe-qc
repository at commit `0830147b8a09f2263901960101ab571fa5e96957`, under
`tests/data/relocalize/`. The request contains the complete occupied H2/STO-3G
subspace and exact QVF shell conventions. The result is a numerical payload,
without its JSONL event envelope. Its backend version records the original
fixture generator's environment, not a minimum supported release.

Regenerate numerical fixtures using that repository's
`tests/data/relocalize/generate.py` and a compiled native core. The viewer tests
construct a small QVF archive from these values; no SCF or backend import is
needed for protocol and controller tests.

To also exercise a separately installed backend, set
`VIBEQC_RELOCALIZE_PYTHON` to its Python executable and run:

```sh
PYVISTA_OFF_SCREEN=True python -m pytest tests/test_relocalize.py -q
```

The viewer environment can remain free of vibe-qc. The optional integration
test launches the configured executable with `-I -m vibeqc_relocalize`.
