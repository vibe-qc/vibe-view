# vibe-view

**Open a quantum-chemistry result and look at it.** In a browser, a native
desktop window, a terminal over SSH, a headless figure pipeline, or Python.

vibe-view reads [QVF](qvf.md) calculation archives and a set of common
structure and volume formats. It is a standalone tool: installing it does not
install or build [vibe-qc](https://vibe-qc.com/docs/). vibe-qc is one QVF
producer among others, and none of them is required to run the viewer.

:::{tip}
This site documents **vibe-view {{release}}**, *{{codename}}*.
:::


```{figure} images/00-demo.png
:alt: The vibe-view browser displaying its built-in water molecule, with a section list and interactive structure controls.
:width: 100%

Open the built-in water demo in the [quickstart](quickstart.md).
```

## Start here

::::{grid} 1 1 2 2
:gutter: 2

:::{grid-item-card} {octicon}`download` Install
:link: installation
:link-type: doc

Python 3.11+, one script, no compiler. Which optional extra buys you what.
:::

:::{grid-item-card} {octicon}`rocket` Quickstart
:link: quickstart
:link-type: doc

Five minutes from a fresh checkout to a rendered molecule, with no
calculation of your own to hand.
:::

:::{grid-item-card} {octicon}`eye` A tour of the viewer
:link: walkthrough
:link-type: doc

Panel by panel: structures, densities, orbitals, spectra, bands, and the
same file in the terminal and from Python.
:::

:::{grid-item-card} {octicon}`terminal` CLI reference
:link: cli
:link-type: doc

Every command, grouped by what you are trying to do.
:::

::::

## Four ways to look at the same file

vibe-view is one renderer stack behind four front ends. Every one of them
reads the same archive and draws it with the same code, so a figure captured
headlessly on a compute node matches what the browser shows.

| Surface | Command | Needs | Good for |
|---|---|---|---|
| [**Browser**](browser.md) | `vibe-view open FILE` | `[viewer]` extra | Interactive exploration, [editing](editing.md), the [vq job panel](jobs.md) |
| [**Desktop**](desktop.md) | `vibe-view desktop FILE` | `[viewer]` + Electron, source checkout | A native window with the OS menu bar |
| [**Terminal**](terminal.md) | `vibe-view tui FILE` / `vibe-view show FILE` | `[tui]` extra (`show` needs nothing) | A login node with no display server, GL or X forwarding |
| [**Headless**](headless.md) | `vibe-view capture` / `batch` / `export` | Core install only | Figures for a paper, CI artifacts, batch pipelines |
| [**Python**](python_api.md) | `from vibeview import ...` | Core install only | Notebooks, scripts, anything programmatic |

`vibe-view show` is worth singling out: it renders a real 3-D view, atoms,
bonds and isosurfaces, as Unicode braille, needs no OpenGL context at all,
and therefore works anywhere a shell does.

## Documentation

```{toctree}
:maxdepth: 2
:caption: Getting started

installation
quickstart
walkthrough
capabilities
```

```{toctree}
:maxdepth: 2
:caption: The surfaces

browser
editing
terminal
desktop
headless
```

```{toctree}
:maxdepth: 2
:caption: Data and formats

formats
biomolecules
qvf
```

```{toctree}
:maxdepth: 2
:caption: Reference

cli
python_api
jobs
queue
troubleshooting
```

```{toctree}
:maxdepth: 1
:caption: The project

codenames
release_process
changelog
contributing
internal
```

## Where things live

vibe-view is one of five repositories split out of the private `vibe-qc`
monorepo on 2026-09-08. Each is independently installable and separately
versioned.

| Project | What it is | Documentation |
|---|---|---|
| **vibe-view** | This viewer | you are here |
| **vibe-qc** | The quantum-chemistry program; one QVF producer | [vibe-qc.com/docs/](https://vibe-qc.com/docs/) |
| **vibe-queue** (`vq`) | Job scheduler; the [`[queue]` extra](queue.md) talks to it | [its repository](https://github.com/vibe-qc/vibe-queue) |
| **qvf** | The normative QVF specification and conformance corpus | [its repository](https://github.com/vibe-qc/qvf) |

Source: [github.com/vibe-qc/vibe-view](https://github.com/vibe-qc/vibe-view) ·
Issues: [project 35](https://github.com/vibe-qc/vibe-view/issues) ·
Licence: MPL 2.0
