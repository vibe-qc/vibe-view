# Quickstart

Five minutes, no calculation of your own, no vibe-qc.

## 1. Check the installation

```sh
vibe-view doctor
```

Every optional mode gets a row saying whether it is ready and, when it is
not, the command that would fix it. A `Core install: ready` line is enough to
continue.

## 2. Make a file to look at

```sh
vibe-view demo
```

`demo` writes a bundled water structure as a real QVF archive and validates
it. It needs neither vibe-qc nor an input file of your own. When it finishes
it prints the browser, desktop, terminal and headless next steps for the file
it just wrote.

`-o FILE` chooses the output path; `--force` replaces an existing demo.

:::{note}
`vibe-view quickstart` is a different command. It runs a real vibe-qc
calculation and then opens it, so it requires vibe-qc in the same
environment. `demo` is the one that works on a bare viewer install.
:::

## 3. Look at it

```sh
vibe-view demo --open        # write it and launch the browser in one step
```


```{figure} images/00-demo.png
:alt: The built-in water demo open in vibe-view: one red oxygen and two white hydrogen atoms, with the structure sidebar and display controls.
:width: 100%

The built-in demo after opening Structure and choosing the light background.
```

Or pick the surface that suits where you are:

```sh
vibe-view open vibe-view-demo.qvf      # browser          — needs the [viewer] extra
vibe-view desktop vibe-view-demo.qvf   # native window    — source checkout + Electron
vibe-view tui vibe-view-demo.qvf       # interactive TUI  — needs the [tui] extra
vibe-view show vibe-view-demo.qvf      # one frame of braille, then exit — needs nothing
```

`show` is the one to remember for a login node: it renders the real 3-D scene
as Unicode braille with no display server, no OpenGL and no X forwarding.


```{figure} images/show-00-demo.svg
:alt: The actual output of vibe-view show for the built-in water demo: coloured Unicode braille atoms and bonds with oxygen and hydrogen labels.
:width: 100%

The same water structure rendered by `vibe-view show`, directly from its terminal output.
```

## 4. Ask it questions without opening anything

```sh
vibe-view info vibe-view-demo.qvf              # metadata and section sizes
vibe-view info vibe-view-demo.qvf --json       # ... for a script
vibe-view validate vibe-view-demo.qvf          # SHA-256 integrity check of every section
vibe-view table vibe-view-demo.qvf             # dump tabular sections
```

## Already have data?

Pick the shortest route:

| Goal | Command |
|---|---|
| Open a QVF or a supported loose file in a browser | `vibe-view open INPUT` |
| Work over SSH with no display | `vibe-view show FILE.qvf` or `vibe-view tui FILE.qvf` |
| Use a native window from the source checkout | `vibe-view desktop INPUT` |
| See which loose formats this installation supports | `vibe-view formats` |
| Convert a loose file into a persistent QVF | `vibe-view import INPUT` |
| Render a figure with no display at all | `vibe-view capture INPUT -o figure.png` |
| Compare two calculations | `vibe-view diff a.qvf b.qvf` or `vibe-view compare a.qvf b.qvf` |

`vibe-view open` accepts loose structure files directly — you do not have to
convert first. Use `import` when you want a persistent QVF to keep.

## A figure, headlessly

No display server needed; this is the core install, and it is what a CI job or
a batch pipeline would run.

```sh
vibe-view capture vibe-view-demo.qvf -o structure.png
vibe-view batch *.qvf --volumes -o gallery/
vibe-view export vibe-view-demo.qvf -o molecule.html      # standalone 3-D page
```

## From Python

```python
from vibeview import info, validate, capture_structure, render_terminal

data = info("vibe-view-demo.qvf")
print(data["scf_energy_eh"])

capture_structure("vibe-view-demo.qvf", "structure.png")

# Same renderers, text instead of a PNG — and no OpenGL context needed,
# so this one works on a headless compute node.
print(render_terminal("vibe-view-demo.qvf", size=(80, 24)))
```

The full surface is in the [Python SDK reference](python_api.md).

## Where to go next

* [A tour of the viewer](walkthrough.md) — panel by panel, with screenshots.
* [What vibe-view can show you](capabilities.md) — every section kind and the
  surface that renders it.
* [The browser viewer](browser.md), [terminal mode](terminal.md) and
  [figures without a display](headless.md) — the reference for each surface.
* [CLI reference](cli.md) — every command, grouped by task.
* [The QVF format](qvf.md) — what is in the archive you just opened.
* [Troubleshooting](troubleshooting.md) — when a mode will not start.

Run `vibe-view examples` to see worked workflows, and
`vibe-view examples --copy DIR` to drop the bundled example files somewhere
you can edit them.
