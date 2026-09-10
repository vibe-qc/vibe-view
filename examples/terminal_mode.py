#!/usr/bin/env python3
"""Terminal-mode examples — render a QVF as text, with no display server.

Runs through the whole surface:

1. One-line render via the public SDK (``render_terminal``)
2. Picking a section, representation, and colour scheme
3. Stored orbital/density isosurfaces at a chosen isovalue
4. Periodic systems: supercell replication
5. Charts — bands, DOS, spectra, SCF convergence
6. Text panes — provenance, geometry, MO listing, citations
7. Animated kinds — trajectory frames and normal modes
8. Sweeping every section in an archive

None of this needs OpenGL, a display server, or X forwarding: the rasterizer
is pure numpy, so every example here works over a bare SSH connection to a
compute node. Only the *interactive* viewer (``vibe-view tui``) needs the
``[tui]`` extra; everything in this file runs on the core install.

Usage:

    python examples/terminal_mode.py path/to/calculation.qvf
    python examples/terminal_mode.py path/to/calculation.qvf --plain
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Character grid every example renders into. Small enough to stay readable
# in a docs page; pass your real terminal size for interactive use.
SIZE = (78, 20)


def rule(title: str) -> None:
    print(f"\n\033[1m── {title} " + "─" * max(0, SIZE[0] - len(title) - 4) + "\033[0m")


def example_1_one_liner(qvf: str, plain: bool) -> None:
    """The shortest path: hand it a file, get back printable text."""
    from vibeview import render_terminal

    rule("1. One-line render (defaults to the structure section)")
    print(render_terminal(qvf, size=SIZE, plain=plain))


def example_2_representations(qvf: str, plain: bool) -> None:
    """Representation and colour scheme, same knobs as the GUI."""
    from vibeview import render_terminal

    for representation in ("ball_and_stick", "licorice", "spacefill"):
        rule(f"2. representation={representation}")
        print(render_terminal(qvf, size=SIZE, plain=plain, representation=representation))

    # Biomolecular colouring falls back to CPK when the archive carries no
    # chains or B-factors, so this is safe on any structure.
    rule("2b. colour_mode=chain")
    print(render_terminal(qvf, size=SIZE, plain=plain, color_mode="chain"))


def example_3_orbitals(qvf: str, plain: bool) -> None:
    """Stored fields. Signed data get both lobes; unsigned get one surface."""
    from vibeview import QVFReader, render_terminal
    from vibeview.tui.scene import VOLUME_KINDS

    reader = QVFReader(qvf)
    volumes = [s.id for s in reader.sections if s.kind in VOLUME_KINDS]
    if not volumes:
        print("\n(no volume sections in this archive — skipping orbitals)")
        return

    for isovalue in (0.02, 0.05):
        rule(f"3. {volumes[0]} at isovalue={isovalue}")
        print(
            render_terminal(
                reader, volumes[0], size=SIZE, plain=plain, isovalue=isovalue,
                representation="licorice",
            )
        )
    reader.close()


def example_4_periodic(qvf: str, plain: bool) -> None:
    """Supercells. Only pbc-flagged axes replicate — a slab's synthesized
    normal is bookkeeping for the integrals, not a direction to repeat in."""
    from vibeview import QVFReader, render_terminal

    reader = QVFReader(qvf)
    structure = reader.read_structure()
    if not any(structure.pbc):
        print("\n(molecular archive — skipping the supercell example)")
        reader.close()
        return

    rule(f"4. 2x2 supercell (pbc={structure.pbc}, dim={structure.dim})")
    print(render_terminal(reader, size=SIZE, plain=plain, replication=(2, 2, 1)))
    reader.close()


def example_5_charts(qvf: str, plain: bool) -> None:
    """Chart-shaped kinds go through the same call — no separate plotting API."""
    from vibeview import QVFReader, render_terminal
    from vibeview.tui import plots

    reader = QVFReader(qvf)
    charted = [s for s in reader.sections if s.kind in plots.PLOT_BUILDERS]
    if not charted:
        print("\n(no chartable sections in this archive)")
        reader.close()
        return

    for section in charted:
        rule(f"5. {section.id} ({section.kind})")
        print(render_terminal(reader, section.id, size=SIZE, plain=plain))

    # A reaction path or trajectory is geometric *and* chartable; `chart=True`
    # picks the energy profile over the geometry.
    for section in charted:
        if section.kind in plots.DUAL_KINDS:
            rule(f"5b. {section.id} energy profile (chart=True)")
            print(render_terminal(reader, section.id, size=SIZE, plain=plain, chart=True))
    reader.close()


def example_6_text_panes(qvf: str) -> None:
    """Kinds that are tables, not pictures. These return Rich markup, so
    they print through a Console rather than straight to stdout."""
    from rich.console import Console

    from vibeview import QVFReader
    from vibeview.tui import panes

    reader = QVFReader(qvf)
    console = Console(width=SIZE[0])

    rule("6. Archive overview — provenance, lifecycle, section inventory")
    console.print(panes.overview(reader))

    rule("6b. Geometry table")
    console.print(panes.structure_table(reader))

    for section in reader.sections:
        if section.kind == "wavefunction.gto":
            rule("6c. Molecular orbitals (energies, occupations, HOMO-LUMO gap)")
            console.print(panes.wavefunction(reader, section.id))
            console.print(
                "[dim]Interactive isosurfaces: run `vibe-view tui`, select this "
                "wavefunction, then use Up/Down + Enter (or n/p); D renders "
                "total density when the archive declares electron occupations; S is "
                "available for unrestricted sets.[/]"
            )
        elif section.kind == "citations":
            rule("6d. Citations — paste straight into your bibliography")
            console.print(panes.citations(reader, section.id))
    reader.close()


def example_7_animation(qvf: str, plain: bool) -> None:
    """Frames of a trajectory, reaction path, or normal mode."""
    from vibeview import QVFReader, render_terminal

    reader = QVFReader(qvf)
    animated = [
        s for s in reader.sections if s.kind in {"trajectory", "reaction.path", "vibrations"}
    ]
    if not animated:
        print("\n(nothing animated in this archive)")
        reader.close()
        return

    section = animated[0]
    for frame in (0, 3, 6):
        rule(f"7. {section.id} ({section.kind}) frame {frame}")
        print(render_terminal(reader, section.id, size=SIZE, plain=plain, frame=frame))
    reader.close()


def example_8_sweep(qvf: str, plain: bool) -> None:
    """Every graphable section, in one call — the `--all` flag's engine."""
    from vibeview.tui import show as show_mod

    rule("8. Sweep of every graphable section")
    print(show_mod.show_all(qvf, size=(SIZE[0], 14), plain=plain))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("qvf", type=Path, help="path to a .qvf archive")
    parser.add_argument(
        "--plain", action="store_true", help="drop ANSI colour (for logs and pipes)"
    )
    args = parser.parse_args(argv)

    if not args.qvf.exists():
        print(f"no such file: {args.qvf}", file=sys.stderr)
        return 1

    qvf = str(args.qvf)
    example_1_one_liner(qvf, args.plain)
    example_2_representations(qvf, args.plain)
    example_3_orbitals(qvf, args.plain)
    example_4_periodic(qvf, args.plain)
    example_5_charts(qvf, args.plain)
    example_6_text_panes(qvf)
    example_7_animation(qvf, args.plain)
    example_8_sweep(qvf, args.plain)

    print("\nDone. For the interactive viewer:  vibe-view tui", qvf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
