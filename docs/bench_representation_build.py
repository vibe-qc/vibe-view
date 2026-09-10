#!/usr/bin/env python
"""Scene-build cost per representation, for one structure.

Answers "how long before the user sees anything", which for a biomolecule
is the whole question: the Trame server binds its port only once the first
render has completed, so a slow build does not read as a slow load, it
reads as a hang.

    python docs/bench_representation_build.py protein.qvf

Both representations are timed in one process against one reader, so the
numbers are directly comparable. Cell counts are not: a tube emits
triangle strips and a glyphed sphere emits triangles, so one "cell" means
different amounts of geometry in each row. Use
``docs/bench_cartoon_payload.py`` for wire payload.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

REPRESENTATIONS = ("cartoon", "ball_and_stick", "space_filling", "sticks_only")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    import pyvista as pv

    from vibeview.qvf import QVFReader
    from vibeview.renderers.structure import StructureRenderer

    path = Path(argv[1])
    reader = QVFReader(path)
    section = next(
        (s for s in reader.sections if s.kind == "structure"), None
    )
    if section is None:
        print(f"{path.name}: no structure section")
        return 1

    structure = reader.read_structure()
    print(f"{path.name}: {len(structure.atoms):,} atoms", end="")
    if structure.has_residues:
        trace = structure.backbone_trace()
        print(f", {len(structure.chains()):,} chains, {len(trace):,} CA")
    else:
        print(" (no residue metadata — cartoon will fall back)")

    for representation in REPRESENTATIONS:
        renderer = StructureRenderer(section, reader)
        plotter = pv.Plotter(off_screen=True)
        start = time.perf_counter()
        renderer.add_to_plotter(plotter, representation=representation)
        elapsed = time.perf_counter() - start
        cells = 0
        for actor in plotter.actors.values():
            # Not every actor carries a dataset (axes widgets, for one).
            with contextlib.suppress(Exception):
                cells += actor.mapper.dataset.n_cells
        print(
            f"  {representation:15s} {elapsed:8.2f}s "
            f"{len(plotter.actors):3d} actors {cells:>12,} cells"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
