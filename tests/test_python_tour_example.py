"""The manual's runnable Python tour (docs/examples/python-tour) keeps working."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TOUR = Path(__file__).resolve().parents[1] / "docs/examples/python-tour/tour.py"


def test_python_tour_runs_and_writes_its_files(tmp_path):
    env = {**os.environ, "PYVISTA_OFF_SCREEN": "True"}
    result = subprocess.run([sys.executable, str(TOUR), str(tmp_path / "out")],
                            capture_output=True, text=True, env=env, cwd=tmp_path,
                            timeout=300, check=False)
    assert result.returncode == 0, result.stderr
    assert "hbond: atoms [1, 3, 4]" in result.stdout
    assert "O1-H2 is now 1.0000 Å" in result.stdout
    assert "Gaussian envelope area 117.00" in result.stdout
    assert "Reopened the xtb coord file: 6 atoms" in result.stdout
    written = {p.name for p in (tmp_path / "out").iterdir()}
    assert {"dimer-edited.qvf", "dimer.zmat", "scene-script.json", "xtb-coord"} <= written
