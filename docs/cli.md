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
core install. `open`, `compare`, `serve`, `dashboard` and `desktop` need the
browser stack (`[viewer]`); `tui` needs Textual (`[tui]`). Run a command
whose extra is missing and vibe-view names the extra and the exact install
command rather than failing obscurely.

## Getting started

| Command | Needs | What it does |
|---|---|---|
| `doctor` | core | Diagnose the installation and every optional mode. `--json` for scripts. |
| `demo` | core | Write and validate a bundled water-structure archive. `-o FILE` names it, `--force` replaces it, `--open` launches the browser afterwards (with `--port` and `--no-browser`). Needs no input and no vibe-qc. |
| `examples` | core | Show worked workflows. `--copy DIR` copies the bundled example files; `--force` replaces existing copies. |
| `formats` | core | List the built-in, optional and plugin importers this installation has, with their extensions and availability. `--json` for scripts. |
| `quickstart` | vibe-qc | Run a real calculation and open it. **The only command on this page that requires vibe-qc in the same environment.** |

## Looking at a file

| Command | Needs | What it does |
|---|---|---|
| `open INPUT...` | `[viewer]` | The interactive [browser viewer](browser.md). Loose structure files convert in memory. Several files give a Files dropdown; `--auto-compare` starts in compare mode; `-s ID` activates a section on startup; `--port`, `--host`, `--no-browser`, `--log-file`. |
| `compare A B` | `[viewer]` | `open A B --auto-compare`. |
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
| `validate FILE...` | core | Schema and SHA-256 check of every member. Run it before publishing or forwarding an archive. |
| `diff A B` | core | Energy delta, section overlap, geometry RMSD. `--json`. |
| `batch-compare FILE...` | core | The same across a set, against `-r` as the reference. `--json`. |
| `stats [DIR]` | core | Aggregate statistics over every archive in a directory. |
| `recent` | core | Recently opened files. `-n` limits the list. |
| `vq-features [FEATURE]` | core | Which visualizations a producer has to write what for. No queue needed; it is a static table. |

## Figures, without a display

These are the core install: no `[viewer]` extra, no browser. They do want
an offscreen GL context; [Figures without a display](headless.md) covers
setting one up, and `show` is the fallback when there is none.

| Command | What it does |
|---|---|
| `capture FILE` | Render a section to PNG. `-s ID`, `-o PATH`, `--isovalue`, `--colormap`, `--size WxH`. |
| `batch PATTERNS...` | A PNG gallery from many archives. `--volumes` includes volume sections; `-o DIR`, `--size`. |
| `animate FILE` | A trajectory, reaction path, vibration, orbital sweep or turntable as MP4 or GIF. `-k KIND`, `--mode N`, `--fps`, `-f mp4\|gif\|frames`, `--size`, `--isovalue`. |
| `export FILE` | The structure as `xyz`, `cif`, `cml`, `json` or a `py` input script; meshes as `obj` or `gltf`; scenes for `pov` (POV-Ray) or `blend` (Blender); pages and figures as `html`, `svg` or `pdf`. `-f FORMAT`, `-o PATH`. |
| `capture-selftest` | Prove the offscreen pipeline renders a non-blank image, and report the backend. `-o FILE` keeps the test PNG. Run it first on a new host. |

## Building and reshaping archives

| Command | What it does |
|---|---|
| `import INPUT...` | Convert loose files into persistent archives. One input writes a sibling `.qvf`; several, or a directory searched recursively, write one archive per source into `./vibe-view-imports` or `-o DIR`. `--from FORMAT` forces an importer; `--force` replaces outputs. See [Input formats](formats.md). |
| `slice FILE` | Keep (`-k`) or drop (`-d`) sections by id or kind into a new archive. The way to make a large archive small enough to send. |
| `merge FILE...` | Combine sections from several archives. Structure and provenance come from the first; duplicate ids are renamed. |
| `supercell FILE` | Replicate a periodic structure `--nx --ny --nz` times. |
| `h-add FILE` | Saturate open valences with hydrogens; writes `<stem>_h.qvf` unless `-o`. |

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
port = 8080               # vibe-view open / serve / compare
host = "127.0.0.1"        # localhost only
open_browser = true

[display]
size = "900x600"          # vibe-view batch / capture
representation = "ball_and_stick"   # ball_and_stick, space_filling, sticks_only, wireframe
```

Everything else the viewer remembers lives under the cache directory,
`~/.cache/vibe-view/` (`$XDG_CACHE_HOME`): the browser settings and
per-element colour overrides in `settings.json`, the recent-files list in
`recent.json`, the server log in `vibe-view.log`, and the interpreter the
desktop app last launched from in `interpreter.json`. `uninstall.sh` leaves
all of it in place.

## Exit codes

`0` on success, non-zero on failure. A missing optional dependency is a
failure with a remediation message naming the extra to install, not a
silent degradation. `capture-selftest` is non-zero when the environment
cannot render, which is what makes it usable as a healthcheck.
