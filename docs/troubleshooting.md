# Troubleshooting

## Start here

```sh
vibe-view doctor
```

`doctor` checks the core install and every optional mode, and prints the
exact command that would fix each thing it finds missing. `--json` makes it
scriptable. Most of what follows is `doctor` telling you something in more
detail.

## A section, table or orbital is missing

```sh
vibe-view info calculation.qvf
vibe-view table calculation.qvf
vibe-view formats
```

Use the section inventory to distinguish a data limitation from an install
problem:

| What you observe | What to do |
|---|---|
| The demo shows only a structure | Expected: the demo is geometry, not a quantum-chemistry calculation. |
| An XYZ or PDB has no density, orbitals or spectrum | Importing geometry cannot create those results. Obtain a computed QVF, a cube field, or a supported wavefunction dataset. |
| `Section 'density' not found` | Use one of the IDs the message lists (or `info` shows); `volume.density` is a kind, and its ID need not be `density`. |
| `table` reports no tabulatable sections | It lists scientific tables, not atom coordinates. Export XYZ or use `get_structure` for the geometry. |
| A section is listed as `skipped, not yet rendered` | QVF defines the kind, but this vibe-view has no panel for it yet, usually because the file is newer than the viewer; update vibe-view. The rest of the file is unaffected. |
| A section is listed as `skipped, vendor namespace (X)` | An `x_<vendor>.*` section belongs to one program and is not portable by design. A [renderer plugin](python_api.md#writing-a-renderer-plugin) can draw it. |
| A section is listed as `skipped, unsupported` | Its kind is not in the QVF version vibe-view reads; check the file's producer and `qvf_version` with `vibe-view info`. Changing the view or installing a browser does not add a renderer. |
| `section '…' is marked critical but its kind '…' is not supported by vibe-view; refusing to open` | The producer said the file is misleading without that section, so vibe-view refuses it rather than show part of it. Open it in a program that renders that kind, or ask the producer whether the section must be critical. |
| An orbital is present but incomplete | Read the evaluator's message about angular momentum or periodicity; see [wavefunction limits](terminal.md#orbitals-on-demand). |

## Opening ORCA results

The [ORCA tutorial](orca.md) describes the complete workflow. For a failed or
unexpected import, first check which files share the calculation basename:

| What you observe | What to do |
|---|---|
| Opening a property file requests `orca_2json` | A matching GBW was discovered. Put the matching ORCA utility on `PATH`, set `ORCA_2JSON`, or create a current orbital JSON export as described in the tutorial. |
| The property and orbital geometries or charge/multiplicity disagree | Use results from the same calculation and final geometry. Renaming files does not make them compatible. |
| A Compound wrapper output lists several calculations | Open one step's result file, or import the results directory to create separate QVFs. |
| A browser upload contains only properties | Uploads are isolated files. Import the full calculation locally, then upload its combined QVF. |
| CASSCF orbitals are visible but density, ELF or NCI is unavailable | The export does not establish the required correlated density representation. Use an ORCA-exported density cube for that field. |
| MCD strengths differ from a thermally averaged spectrum | The importer preserves raw orientation sticks. It does not reproduce `orca_mapspc` thermal averaging. |
| An ORCA result changed on disk but the view did not | Converted sources are not watched. Restart the viewer or regenerate the QVF and open the updated archive. |

## A mode will not start, or "No module named …"

vibe-view splits its dependencies into extras so a capture-only host does
not have to install a web server. A missing mode is almost always a missing
extra. Run these commands from the standalone vibe-view repository root,
with its environment activated:

| Symptom | Missing | Fix |
|---|---|---|
| `open`, `serve`, `compare`, `desktop` fail to start | `[viewer]` | `python -m pip install -e '.[viewer]'` |
| `tui` fails; `show` works | `[tui]` | `python -m pip install -e '.[tui]'` |
| A structure format is not recognised | `[ase]` | `python -m pip install -e '.[ase]'` |
| SMILES construction is absent | `[smiles]` | `python -m pip install -e '.[smiles]'` |
| `%vibeview` is not a magic | `[jupyter]` | Install it in the environment running the **kernel** |
| The vq panel or `from-vq` is unavailable | `[queue]` | See [Queue integration](queue.md); it is not a plain pip install |
| **Sketch** puts an install command in the status bar | `[sketch]` | Install vibe-sketch from its checkout, then `python -m pip install -e '.[sketch]'` |
| Build-from-name, auto-optimize, re-localize or container submission are greyed out | vibe-qc | These are producer features; the viewer works without them |

From a source checkout, `./scripts/install.sh --extras all` is usually
simpler than chasing them one at a time. `pip install vibeview` itself does
not resolve yet: the distribution is not published, so install from the
checkout as [Installation](installation.md) describes.

## The browser viewer

**The page is blank.** Look at the terminal the server runs in. The banner
printed at open lists every section and its status; a file whose sections
are all *skipped, unsupported* renders an empty scene, and that is the
viewer being honest rather than broken. If the sections say *rendered* and
the page is still blank, it is client-side: the viewer uses Vue and a
WebSocket, and a very strict ad-blocker or corporate proxy can break the
handshake. Allow the local page, or try another browser profile.

**"port 8080 is already in use".** `open` checks the port before launching
and refuses when something already listens there, most often a vibe-view you
left running in another terminal: servers run until <kbd>Ctrl</kbd>+<kbd>C</kbd>
and have no idle timeout. Stop the other one, or start on a free port:

```sh
lsof -nP -iTCP:8080 -sTCP:LISTEN     # what owns the port
vibe-view open water.qvf --port 8090
```

Stop the identified server with **Ctrl+C** in the terminal where you
started it; avoid terminating other sessions by command-name pattern.

The guard exists because without it `open` would connect to the existing
listener, announce *ready*, and open your browser at the **stale** server
while the new one died unseen.

**The structure shows but the mouse does nothing.** Almost always a stale
server, not a rendering bug. A browser tab can outlive the server behind it
(you stopped it, the machine slept, a relaunch could not bind the port); the
tab keeps the last rendered frame, but its WebSocket is gone. Stop any stale
servers and use the tab a fresh launch opens.

**"server did not bind within 10 s".** The server entered its main loop but
never bound the socket. Either the port is held in a state the pre-check
misses (pick another with `--port`), or an import failed at startup: look at
the stderr just above the warning for a `ModuleNotFoundError`, and reinstall
if you find one.

**Firefox on macOS 15 says "Unable to connect" to 127.0.0.1.** macOS 15
added a system-level *Local Network* permission that is denied to Firefox
by default, while Safari and Chrome were grandfathered in. Grant it under
**System Settings → Privacy & Security → Local Network**, or use another
browser. Nothing on the server side needs changing; vibe-view binds to
localhost, and the permission belongs to the browser.

**Ready is printed but the browser cannot connect for a few seconds.** The
*ready* line is printed once the port is bound, and the automatic browser
open waits for it. If you pass `--no-browser` and open the URL by hand
immediately, you can still race the bind on a slow machine; refresh once.

**A large orbital set makes the sidebar slow.** Orbital *metadata* is read
eagerly and the payloads lazily, so dozens of `volume.orbital` sections stay
fast until you click them. A producer with many orbitals should write one
`wavefunction.gto` section instead; the viewer evaluates orbitals from it on
demand.

## Validation says `NOT CHECKED`

`vibe-view validate` uses the optional qvf reference validator. A core,
`modes` or `all` installation alone does not provide it. Install the qvf
Python package from the release pinned by `QVF_TAG`, then enable the
viewer’s `validation` extra in the same environment; see
[strict validation](qvf.md#strict-validation).

`NOT CHECKED` is a missing capability, not a verdict on the archive. The
command exits unsuccessfully so scripts cannot mistake an unperformed check
for a pass. In Python the missing dependency raises
`ValidationUnavailableError`; a completed validation returns a report whose
`valid` field carries the verdict. Opening a file successfully is not a
substitute for strict validation of every payload.

## `error, sha256 mismatch` or `ManifestValidationError`

A section's payload does not match the digest its own manifest declares.
The viewer will not render it; it never draws from bytes that failed their
integrity check. `vibe-view validate FILE.qvf` names every affected
section. The file was truncated, corrupted in transfer or edited after it
was written; re-fetch it from the producer. There is nothing to repair on
this side.

A manifest that fails schema validation is a different message. It means
the producer wrote something the vendored schema for that `qvf_version`
does not accept. Ask the producer to validate against the normative schema
in the [qvf repository](https://github.com/vibe-qc/qvf);
[The QVF format](qvf.md) explains which schema governs which file.

## Headless capture fails

```sh
vibe-view capture-selftest
```

exercises the whole offscreen pipeline and reports which stage failed. Two
recurring causes on a fresh host:

* **No GL stack.** PyVista and VTK need a GL and X stack even for offscreen
  rendering. On a bare Debian-family container that means at least
  `libgl1`, `libglx-mesa0`, `libxrender1`, `libxext6`, `libsm6` and
  `libglib2.0-0`, plus `xvfb` and `xauth` to run under `xvfb-run`. That is
  exactly what vibe-view's own CI installs.
* **No display.** Set `PYVISTA_OFF_SCREEN=True`, and run under `xvfb-run -a`
  when the host has no display server.

If you cannot get a GL context at all, `vibe-view show` and
`vibeview.render_terminal` need none. See [Figures without a
display](headless.md).

## Nothing works over SSH

That is the case the terminal surfaces exist for:

```sh
vibe-view show FILE.qvf     # one frame, core install only, no GL
vibe-view tui FILE.qvf      # interactive, needs [tui], no GL
```

Neither needs a display server, an OpenGL context or X forwarding. If the
braille comes out as boxes, add `--mode half`. If you want the *browser*
against a remote file, tunnel the port instead of binding to `0.0.0.0`; the
[browser page](browser.md#launch-options) has the command and the warning.

## `vibe-view desktop` will not launch

The `vibe-view desktop` CLI runs the Electron source from a **source
checkout**; the Python wheel does not carry it. A self-contained Apple
Silicon app instead runs its bundled interpreter. Check which route you
are using before repairing an environment; see [desktop package status](desktop.md#what-is-available-today).

For the source-backed launcher:

* Electron downloads itself on first use; `./scripts/install.sh
  --with-electron` fetches it up front.
* The desktop options need the `modes`, `viewer`, `all` or `test` profile.
  `core` and `tui` are non-desktop profiles.
* If the setup screen keeps appearing, the app cannot see your environment.
  Run `vibe-view desktop` once from it; the interpreter is recorded from
  then on.
* If a source app belongs to an older checkout, the launcher reports the
  path. Review it, then pass `--adopt-desktop` explicitly.
* Quit every desktop window before `update-desktop.sh` or
  `reinstall.sh --desktop`.
* On Linux, never launch as root, and install the GTK3 runtime the
  [desktop page](desktop.md#linux) lists.

## The installation is in a strange state

```sh
./scripts/reinstall.sh              # rebuild the environment, leave Git alone
./scripts/reinstall.sh --desktop    # also refresh Electron and the source app
./scripts/uninstall.sh --dry-run    # exactly what a removal would take
```

Reinstall is transactional: the previous environment survives until the new
one installs and verifies, so a failed reinstall leaves you where you
started. Uninstall never touches your archives, settings, recents or logs.

Two refusals are deliberate. The installer refuses an existing `.venv` it
did not create (use `--force` for its own, `--adopt-legacy` with proof for a
pre-marker one), and every lifecycle script refuses to run under `sudo`.

## Reporting a bug

Issues go to [the viewer tracker](https://github.com/vibe-qc/vibe-view/issues).
Include:

```sh
vibe-view doctor --json
vibe-view --version
vibe-view info THE-FILE.qvf --json      # if a specific file is involved
```

and the file itself, or a minimal one that reproduces the problem.
`doctor --json` carries the interpreter, the version and every capability
row, which is most of what a first triage pass would otherwise have to ask
for. A wrong number *inside* an archive is the producer's bug, and belongs
with the producer; [Contributing](contributing.md) has the table of where
each kind of report goes.
