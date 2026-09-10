# Security Policy

vibe-view takes security seriously. This document describes how to report a
vulnerability and what is in scope.

See also: [CONTRIBUTING.md](CONTRIBUTING.md) for non-security bugs.

## Supported versions

vibe-view is pre-1.0 in policy terms even at 2.x: only the latest commit on
`main` receives security fixes, and nothing is backported. A supported-version
table will appear here once the release line is declared stable.

## Reporting a vulnerability

Please email **mpei@vibe-qc.com** directly. Do not open a public GitLab issue
for security-relevant reports — that includes any bug you believe could be
exploited for code execution, data leakage, or resource exhaustion beyond what
the test suite would surface.

What to include in your report:

- A description of the issue and its potential impact.
- Steps to reproduce, ideally with a minimal `.qvf` archive or structure file.
- The version and commit you observed it on (`vibe-view --version` and
  `git rev-parse HEAD`).

We aim to acknowledge your report within **72 hours** and will coordinate a
disclosure timeline with you privately. Public disclosure happens after a fix
is available on `main`, unless the reporter requests otherwise.

### Encrypting your report (optional, recommended for sensitive details)

If your report contains exploit details, proof-of-concept code, or anything
you would rather not transmit in cleartext, encrypt it to the project author's
PGP key.

**Fingerprint** — `CC6D 30BB DF96 F694 C615  FBDE 4CD5 65CF 26B1 E7E5`

(no-space form for `gpg` and URLs:
`CC6D30BBDF96F694C615FBDE4CD565CF26B1E7E5`)

**Get the key:**

```sh
curl -O https://vibe-qc.com/docs/_static/pgp/mpei.asc
gpg --import mpei.asc
```

```sh
gpg --keyserver hkps://keys.openpgp.org \
    --recv-keys CC6D30BBDF96F694C615FBDE4CD565CF26B1E7E5
```

After importing, **always verify the fingerprint matches the canonical value
above** before trusting the key — paste-jacking and MITM at fetch time are
real concerns. The fingerprint is a hash; a tampered-with key produces a
different one.

```sh
gpg --fingerprint CC6D30BBDF96F694C615FBDE4CD565CF26B1E7E5
# Should print: CC6D 30BB DF96 F694 C615  FBDE 4CD5 65CF 26B1 E7E5
```

**Encrypt and send:**

```sh
gpg --encrypt --armor --recipient mpei@vibe-qc.com \
    --output report.asc report.txt
# Then attach report.asc to an email to mpei@vibe-qc.com
```

The same fingerprint is published in vibe-qc's `SECURITY.md`. If the two ever
disagree, that is itself a security signal worth flagging — email the address
above (unencrypted is fine for that meta-report).

## Scope

vibe-view's threat model is **untrusted input files**. It opens archives and
structure files that a user did not write and may not trust, so parser and
extraction bugs matter more here than anywhere else in the toolset.

**In scope** — code under this repository:

- `src/vibeview/qvf.py` — the QVF reader. Zip extraction, manifest schema
  validation, member `sha256` verification, path traversal in member paths.
- `src/vibeview/importers.py`, `input_parser.py`, `converters.py` — the
  hand-rolled xyz/cif/pdb/mol2/gro/sdf/cube/gjf readers.
- `src/vibeview/app.py`, `export_html.py` — HTML/attribute injection into the
  served viewer or an exported page.
- `src/vibeview/cli.py` and `scripts/*.sh` — argument and path handling, the
  lifecycle scripts' privilege and ownership checks.
- `electron/` — anything that widens the renderer's access to the host.

Reports about a **crafted `.qvf` or structure file** are always welcome, and a
minimal reproducing archive is the most useful thing you can attach.

**Out of scope** — bugs in upstream dependencies should be reported to those
projects directly:

- [VTK](https://gitlab.kitware.com/vtk/vtk) and
  [PyVista](https://github.com/pyvista/pyvista) — rendering and mesh handling.
- [trame](https://github.com/Kitware/trame) — the web-viewer server stack.
- [Electron](https://github.com/electron/electron) — the desktop runtime.
- [ASE](https://gitlab.com/ase/ase) — the optional long-tail format reader.
- [RDKit](https://github.com/rdkit/rdkit) — the optional SMILES builder.

The **QVF format specification** itself lives in
[vibe-qc/qvf](https://github.com/vibe-qc/qvf); a weakness in the format
rather than in this reader belongs there. If you are unsure whether a finding
is in scope, email it to the address above and we will triage together.
