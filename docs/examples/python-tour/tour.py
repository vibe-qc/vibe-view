# Each step imports what it uses, so the steps read on their own.
# ruff: noqa: E402
"""A runnable tour of vibe-view's building, inspection and preparation APIs.

Run it from any directory with the viewer's Python:

    python tour.py [OUTPUT_DIR]

It needs only the core install; the solvent-excluded surface also needs the
``surfaces`` extra and is skipped without it. Every step prints what it found
and the files it writes go to OUTPUT_DIR (default: ./tour-output).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from vibeview.converters import xyz_to_qvf
from vibeview.qvf import QVFReader

out = Path(sys.argv[1] if len(sys.argv) > 1 else "tour-output")
out.mkdir(parents=True, exist_ok=True)

# A water dimer with one hydrogen bond, in angstrom.
DIMER = """6
water dimer charge=0 multiplicity=1
O   -1.551007  -0.114520   0.000000
H   -1.934259   0.762503   0.000000
H   -0.599677   0.040712   0.000000
O    1.350625   0.111469   0.000000
H    1.680398  -0.373741  -0.758561
H    1.680398  -0.373741   0.758561
"""
reader = QVFReader(xyz_to_qvf(DIMER.encode()))
structure = reader.read_structure()
print(f"1. Opened a water dimer with {len(structure.atoms)} atoms")

# 2. Selections and measurements (one-based atom numbers in expressions).
from vibeview.molecular_selection import positions, select
from vibeview.selection_measurements import best_fit_plane, centroid, selection_rmsd

donor = sorted(select(structure, "index 1-3"))
acceptor = sorted(select(structure, "index 4-6"))
xyz = positions(structure)
plane = best_fit_plane(xyz[donor])
print(f"2. Donor water centroid {np.round(centroid(xyz[donor]), 3)} Å, "
      f"plane residual {plane.rms_distance:.2e} Å, "
      f"fitted O-H-H RMSD between the waters "
      f"{selection_rmsd(xyz[donor], xyz[acceptor], superpose=True):.3f} Å")

# 3. Geometric interactions.
from vibeview.interactions import find_interactions

for row in find_interactions(structure, selection="index 1-3", partner="index 4-6",
                             kinds=("hbond", "contact")):
    print(f"3. {row.kind}: atoms {[i + 1 for i in row.atoms]}, {row.distance:.3f} Å")

# 4. Molecular surfaces: area in Å², enclosed volume in Å³.
from vibeview.molecular_surfaces import build_surface

for kind in ("vdw", "sas", "ses"):
    try:
        surface = build_surface(structure, kind=kind, probe=1.4, spacing=0.2)
    except (ImportError, ValueError) as exc:
        print(f"4. {kind.upper()}: skipped ({exc})")
        continue
    print(f"4. {kind.upper()}: area {surface.area:.1f} Å², volume {surface.volume:.1f} Å³")

# 5. Exact geometry edits on the open reader (zero-based indices), saved as QVF.
from vibeview.api import export_geometry_qvf, set_bond, set_geometry_parameter

set_geometry_parameter(reader, [0, 1], 1.00, side="smaller")      # O1-H2 to 1.00 Å
set_geometry_parameter(reader, [1, 0, 2], 104.5, side="smaller")  # H-O-H angle
set_bond(reader, 0, 1, 1)
edited = reader.read_structure()
oh = np.linalg.norm(np.subtract(edited.atoms[1].position, edited.atoms[0].position))
saved = export_geometry_qvf(reader, out / "dimer-edited.qvf")
print(f"5. O1-H2 is now {oh:.4f} Å; saved {Path(saved).name}")

# 6. Z-matrix round trip.
from vibeview.zmatrix import from_cartesian, parse_zmatrix

symbols = [atom.symbol for atom in edited.atoms]
matrix = from_cartesian(symbols, positions(edited))
block = matrix.text("gaussian")
_, back = parse_zmatrix(block, "gaussian").cartesian()
rmsd = selection_rmsd(back, positions(edited), superpose=True)
(out / "dimer.zmat").write_text(block)
print(f"6. Gaussian Z-matrix written; round-trip RMSD after alignment {rmsd:.1e} Å")

# 7. A replayable two-model scene with alignment and a command line.
from vibeview.model_scene import ModelScene
from vibeview.scene_commands import SceneCommands

scene = ModelScene()
scene.add("reference", structure)
scene.add("edited", edited)
scene.transform("edited", np.eye(3), np.array([0.0, 0.0, 5.0]))
commands = SceneCommands(scene=scene)
commands.execute('align edited "model edited and element O" "model reference and element O"')
result = commands.execute('measure rmsd "model reference" "model edited"')
(out / "scene-script.json").write_text(scene.script())
restored = ModelScene.replay((out / "scene-script.json").read_text())
print(f"7. RMSD after aligning the oxygens: {result['rmsd']:.3f} Å; "
      f"replayed models {sorted(restored.models)}")

# 8. Spectrum broadening: unit-area line shapes keep each stick's strength.
from vibeview.spectrum import broaden_lines

sticks = np.array([1595.0, 3657.0, 3756.0])   # cm-1
strengths = np.array([70.0, 2.0, 45.0])       # km/mol
grid = np.linspace(0.0, 6000.0, 60001)
envelope = broaden_lines(sticks, strengths, grid, fwhm=10.0, lineshape="gaussian")
area = float(np.sum(envelope) * (grid[1] - grid[0]))
print(f"8. Gaussian envelope area {area:.2f} for total stick strength {strengths.sum():.2f}")

# 9. Native inputs for other programs, as previewed in the browser.
from vibeview.input_writers import InputSettings, write_input

bundle = write_input(edited, InputSettings("xtb", task="optimization"))
for name, text in bundle.files.items():
    (out / f"xtb-{name}").write_text(text)
print(f"9. xtb bundle: {sorted(bundle.files)}; command: {' '.join(bundle.command)}")

# 10. The same coord file opens again as a structure.
from vibeview.converters import convert_to_qvf

reopened = QVFReader(convert_to_qvf(out / "xtb-coord", format_name="turbomole"))
print(f"10. Reopened the xtb coord file: {len(reopened.read_structure().atoms)} atoms")
print(f"Done; files are in {out}")
