#!/usr/bin/env python
"""Compare flat vs camera-facing (billboard) atom labels. Roadmap D5.

    python docs/bench_label_orientation.py [outdir]

Renders the same three labels from an off-axis camera twice — once with
the old XY-plane placement, once billboarded — and reports lit pixels for
each, writing `label_flat.png` and `label_billboard.png` for eyeballing.

**Look at the images.** Lit-pixel count is a weak proxy and is reported
only as a summary: `pv.Text3D` is *extruded*, so edge-on text is still a
visible slab occupying plenty of pixels. A browser-side attempt to measure
this by counting label ink before and after a rotation reported the
un-billboarded labels retaining *more* ink, which says nothing about
whether the text could be read. Legibility is a visual property; the
render is the evidence and the count is a regression tripwire.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

# Off to the side and slightly above: the ordinary viewpoint that the
# fixed XY-plane placement renders as skewed, squashed text.
CAMERA = ((0.0, -14.0, 3.0), (3.0, 0.0, 0.0), (0.0, 0.0, 1.0))


def main(argv: list[str]) -> int:
    import numpy as np
    import pyvista as pv

    from vibeview.renderers.structure import build_label_mesh

    outdir = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    outdir.mkdir(parents=True, exist_ok=True)

    anchors = [np.array([0.0, 0.0, 0.0]), np.array([3.0, 0.0, 0.0]),
               np.array([6.0, 0.0, 0.0])]
    strings = ["O1", "H2", "H3"]
    position, focal_point, view_up = CAMERA

    for tag, camera in (("flat", None), ("billboard", CAMERA)):
        plotter = pv.Plotter(off_screen=True, window_size=(760, 300))
        plotter.set_background("#101018")
        mesh = build_label_mesh(anchors, strings, camera=camera, scale=0.9)
        if mesh is None:
            print(f"{tag}: nothing to draw")
            continue
        plotter.add_mesh(mesh, color="white", lighting=False)
        plotter.camera.position = position
        plotter.camera.focal_point = focal_point
        plotter.camera.up = view_up
        out = outdir / f"label_{tag}.png"
        plotter.screenshot(str(out))
        img = np.asarray(plotter.screenshot(return_img=True))
        lit = int((img.sum(axis=2) > 300).sum())
        total = img.shape[0] * img.shape[1]
        print(f"{tag:10s} {lit:>7,} lit px of {total:,}   -> {out}")

    print("\nOpen both PNGs. The flat labels are skewed and squashed from "
          "this viewpoint; the billboarded ones read head-on.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
