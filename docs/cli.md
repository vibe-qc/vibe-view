# CLI reference

`vibe-view --help` and `vibe-view COMMAND --help` are authoritative for the
flags. This page groups the commands by what you are trying to do, names
the extra each one needs, and calls out the ones with a caveat.

```sh
vibe-view --version      # version, codename, Python, PyVista and VTK
vibe-view --help
```

The product and the command are spelled `vibe-view`; the Python
distribution and import package are `vibeview`. Most commands run on the
core install. `open`, `compare`, `serve` and `desktop` need the browser
stack (`[viewer]`); `tui` needs Textual (`[tui]`). Run a command
whose extra is missing and vibe-view names the extra and the exact install
command rather than failing obscurely.

## Getting started

| Command | Needs | What it does |
|---|---|---|
| `doctor` | core | Diagnose the installation and every optional mode. `--json` for scripts. |
| `demo` | core | Write and validate a bundled water-structure archive. `-o FILE` names it, `--force` replaces it, `--open` launches the browser afterwards (with `--port`, `--host` and `--no-browser`). Needs no input and no vibe-qc. |
| `examples` | core | Show worked workflows. `--copy DIR` copies the bundled example files; `--force` replaces existing copies. |
| `formats` | core | List the built-in, optional and plugin importers this installation has, with their extensions and availability. `--json` for scripts. |
| `quickstart` | vibe-qc | Run a real calculation and open it. **The only command on this page that requires vibe-qc in the same environment.** |

## Looking at a file

| Command | Needs | What it does |
|---|---|---|
| `open INPUT...` | `[viewer]` | The interactive [browser viewer](browser.md). Loose structure files convert in memory. Several files give a Files dropdown; `--auto-compare` starts in compare mode; `-s ID` activates a section on startup; `--port`, `--host`, `--no-browser`, `--log-file`. |
| `compare A B` | `[viewer]` | `open A B --auto-compare`, including `-s ID`. |
| `desktop [FILE]` | `[viewer]` + source checkout | A native window; see [The desktop app](desktop.md). `--port`, `--no-sandbox` (Linux, last resort). |
| `tui FILE` | `[tui]` | The interactive [terminal viewer](terminal.md). `--mode braille\|half`. |
| `show FILE` | core | One frame as terminal braille, then exit. `-s ID`, `--all`, `--info`, `--size`, `--mode`, `--plain`, `--representation`, `--color-by`, `--rotate`, `--isovalue`, `--replicate`, `--labels`, `--frame`, `--chart`. |
| `serve [DIR]` | `[viewer]` | A web file browser over a directory of archives. |
| `dashboard PATTERNS...` | core | A grid preview of many archives, as an image or, with `--html`, a page. `--cols`, `--cell-size`. |

Do not pass a directory to `open`. Use `serve DIR` for a browsable listing,
or `import DIR` to convert its contents.

## Asking questions

| Command | Needs | What it does |
|---|---|---|
| `info FILE` | core | Metadata and section sizes. `--short` is one line per archive, for a loop; `--json` for scripts. |
| `table FILE` | core | Tabular sections as `table`, `csv` or `json`. With no `--kind`, lists what the file offers; the kinds are `vibrations`, `atom_properties` and `wavefunction.gto`. |
| `validate FILE...` | core | Schema and SHA-256 check of every member, and that each binary member's bytes fill its declared `dtype` and `shape`. Run it before publishing or forwarding an archive. |
| `diff A B` | core | Energy delta (including zero), section overlap, geometry RMSD, or why the RMSD was skipped (an unreadable structure, no atoms, differing atom counts). `--json` reports every error as JSON on stdout: an archive that cannot be opened as `{"error", "file", "path"}`, any other error as `{"error"}`. |
| `batch-compare FILE...` | core | The same across a set, against `-r` as the reference. `--json`. |
| `stats [DIR]` | core | Aggregate statistics over every archive in a directory. Files it cannot read, a dangling symlink included, are counted and named apart. |
| `recent` | core | Recently opened files. `-n` limits the list. |
| `vq-features [FEATURE]` | core | Which visualizations a producer has to write what for. No queue needed; it is a static table. |

Energy comparisons and file-browser pages distinguish a physical zero from
missing metadata. Equal energies produce zero deltas in all JSON units and
an explicit equal-energy result in text; missing energies remain `null` in
JSON and use a placeholder in file-browser pages.

## Figures, without a display

These are the core install: no `[viewer]` extra, no browser. They do want
an offscreen GL context for PNGs and animation. Data and vector exports do
not need one. [Figures without a display](headless.md) covers
setting one up, and `show` is the fallback when there is none.

| Command | What it does |
|---|---|
| `capture FILE` | Render a section to PNG. `-s ID`, `-o PATH`, `--isovalue`, `--colormap`, `--size WxH`. |
| `batch PATTERNS...` | A PNG gallery from many archives. `--volumes` includes volume sections; `-o DIR`, `--size`. |
| `animate FILE` | A trajectory, reaction path, vibration, orbital sweep or turntable as MP4 or GIF. `-k KIND` (`auto` picks the first of trajectory, reaction, vibration, orbital, then a turntable of the structure), `--mode N`, `--fps`, `-f mp4\|gif\|frames`, `--size`, `--isovalue`. |
| `export FILE` | The structure as `xyz`, `cif`, `cml`, `json` or a `py` input script; meshes as `obj` or `gltf`; scenes for `pov` (POV-Ray) or `blend` (Blender); pages and figures as `html`, `svg` or `pdf`. `-f FORMAT`, `-o PATH`. |
| `capture-selftest` | Prove the offscreen pipeline renders a non-blank image, and report the backend. `-o FILE` keeps the test PNG. Run it first on a new host. |

Without `-o`, a command that transforms one archive (`capture`, `animate`,
`export`, `slice`, `h-add`, `supercell`) writes its result beside that
archive, named `<stem>_<what>.<ext>`; `--help` shows the exact name. Commands
that gather several inputs (`batch`, `merge`, `dashboard`, `import` with more
than one source) write in the working directory.

## Building and reshaping archives

| Command | What it does |
|---|---|
| `import INPUT...` | Convert loose files into persistent archives. One input writes a sibling `.qvf`; several, or a directory searched recursively, write one archive per source into `./vibe-view-imports` or `-o DIR`. `--from FORMAT` forces an importer; `--force` replaces outputs. See [Input formats](formats.md). |
| `slice FILE` | Keep (`-k`) or drop (`-d`) sections by id or kind into a new archive. Preserves metadata and validates the output before replacing it; removed viewer hints are pruned and dangling section references are refused. |
| `merge FILE...` | Combine sections from several archives. Root metadata and viewer defaults come from the first. All sections are retained; duplicate ids and their QVF-defined references are remapped together. Member hashes and the output manifest are checked before atomic replacement, including when writing over an input. |
| `supercell FILE` | Replicate a periodic structure `--nx --ny --nz` times, each at least 1, along periodic axes only: a slab cannot be replicated along its vacuum axis. Writes the structure only and names what it leaves behind. |
| `h-add FILE` | Saturate open valences with hydrogens; writes `<stem>_h.qvf` beside the input unless `-o`. Other sections and the provenance describe the input geometry and are not carried over; the command names them. |

`slice` and `merge` accept legacy version-2 archives but write QVF version 1,
following the current format specification. Both check the output before replacing
an existing file, including section references stored inside wavefunction bases.

## Queue

| Command | Needs | What it does |
|---|---|---|
| `from-vq JOB...` | `[viewer]`, `vq` | Fetch finished jobs' archives into `-o DIR` (`./vq-fetched` by default) and open them. |

Without the extra it prints the exact install remediation and exits 1. The
queue panel inside the viewer is described in [Jobs and live
results](jobs.md), and the extra's peculiarities in
[Queue integration](queue.md).

## Configuration

```sh
vibe-view config --path      # where the file is
vibe-view config --init      # write it with the defaults
```

The file is `~/.config/vibe-view/config.toml` (`$XDG_CONFIG_HOME` is
honoured) and carries two tables:

```toml
[server]
port = 8080               # open, compare, serve, from-vq and demo --open
host = "127.0.0.1"        # localhost only
open_browser = true

[display]
size = "900x600"          # vibe-view batch / capture, unless --size is given
representation = "ball_and_stick"   # batch / capture structure images: ball_and_stick, space_filling, sticks_only, wireframe
```

Everything else the viewer remembers lives under the cache directory,
`~/.cache/vibe-view/` (`$XDG_CACHE_HOME`): the browser settings and
per-element colour overrides in `settings.json`, the recent-files list in
`recent.json`, the server log in `vibe-view.log`, the interpreter the
desktop app last launched from in `interpreter.json`, and the auto-saved
session of a viewer opened without a file in `auto-session.json`.
`uninstall.sh` leaves all of it in place.

## Exit codes

`0` on success, non-zero on failure. A missing optional dependency is a
failure with a remediation message naming the extra to install, not a
silent degradation. `capture-selftest` is non-zero when the environment
cannot render, which is what makes it usable as a healthcheck.

## Errors and debugging

A command reports what it cannot do as one line on stderr,
`Error: <reason>`, and exits with status 1. A usage mistake (an unknown
flag, a missing argument) is click's usage message with status 2.

Every command also runs inside one last boundary. An error that no command
anticipated, which would otherwise have printed a Python traceback, is
reported as `Error: <type>: <message>` (for example
`Error: ZeroDivisionError: float division by zero`), again with status 1.
Any file the command had opened is already closed at that point. Such a
line means vibe-view met something it did not expect, so it is worth a bug
report with the file that caused it.

A message longer than 2000 characters is cut there, with a note of how much
`--debug` would show. Text output, error lines included, shows control
characters from an archive as escapes such as `\x1b`, so a crafted string
cannot recolour, clear or retitle your terminal; `--json` output keeps the
original strings, escaped by JSON.

To see the full traceback instead, for that bug report or while developing,
pass `--debug` before the command or set `VIBEVIEW_DEBUG=1`:

```sh
vibe-view --debug info broken.qvf
VIBEVIEW_DEBUG=1 vibe-view info broken.qvf
```

`VIBEVIEW_DEBUG` is off when unset, empty, `0`, `false`, `no` or `off`.

With `--json`, a command that reports its errors as JSON reports this one
the same way: `info --json` prints `{"error": "<type>: <message>"}` on
stdout, like its error for a file it cannot open. Other `--json` commands
keep errors on stderr, so their stdout is JSON or empty.

Ctrl+C prints `Aborted!` and exits with status 1, and a closed output pipe
(`vibe-view info x.qvf | head -1`) ends the command quietly; neither is
reported as an error. An explicit exit status is kept.
