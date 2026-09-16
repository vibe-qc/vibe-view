# CODEX.md

Codex reads [`AGENTS.md`](AGENTS.md) on its own. This file exists so anyone
looking for Codex guidance finds the pointer: all project rules live in
`AGENTS.md`.

## Codex notes

- **Tests run off-screen.** Set `PYVISTA_OFF_SCREEN=True`. The served-browser
  tests need Chrome and skip without it; that is expected, not a failure to
  fix.
- **A network-restricted sandbox may not install `.[test]`.** VTK, trame and
  PyVista are large wheels. If they cannot install, say so and name the tests
  you could not run, rather than reporting the change as tested.
- **Codex sessions share the maintainer's git identity** with every other
  agent. In a shared checkout, stage files by name and leave changes you didn't
  make alone (`AGENTS.md`, "Working alongside other sessions").
