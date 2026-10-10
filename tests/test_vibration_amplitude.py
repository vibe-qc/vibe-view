"""One vibration-amplitude convention in both viewers (vibe-view#49, audit #38-28).

The maintainer decision: every mode is drawn with its largest atomic
excursion at ``DEFAULT_PEAK_EXCURSION_ANGSTROM`` times a user amplitude
scale (1.0 by default), in the terminal and in the browser alike. The raw
displacement data is not modified.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import build_qvf
from vibeview.qvf import QVFReader
from vibeview.vibration_amplitude import (
    DEFAULT_PEAK_EXCURSION_ANGSTROM,
    normalized_displacement,
    peak_excursion,
)

_ATOMS = [
    {"symbol": "C", "atomic_number": 6, "position": [0.0, 0.0, 0.0]},
    {"symbol": "O", "atomic_number": 8, "position": [1.2, 0.0, 0.0]},
    {"symbol": "H", "atomic_number": 1, "position": [-1.0, 0.5, 0.0]},
]


def _archive(tmp_path: Path, disp: np.ndarray) -> Path:
    disp = np.asarray(disp, dtype="<f8")
    meta = {"atoms": _ATOMS, "frequencies": [1000.0 + 100 * i for i in range(len(disp))]}
    members = {
        "displacements": ("binary", "d.bin", disp.tobytes(), "float64", list(disp.shape)),
        "metadata": ("json", "m.json", json.dumps(meta).encode()),
    }
    return build_qvf(tmp_path / "vib.qvf", [("vib", "vibrations", members)])


# Mode 0: a small raw vector (peak 0.02 Å); mode 1: a large one (peak 0.25 Å)
# whose largest *atomic* excursion is diagonal, not along one axis.
_DISP = np.zeros((3, 3, 3))
_DISP[0, 1, 0] = 0.02
_DISP[1, 2] = [0.15, 0.2, 0.0]  # |d| = 0.25
_DISP[1, 0, 0] = -0.1
# Mode 2 is all zeros.


def _max_excursion(coords: np.ndarray, base: np.ndarray) -> float:
    return float(np.linalg.norm(coords - base, axis=-1).max())


def _tui_peak(reader: QVFReader, mode: int, scale: float = 1.0) -> float:
    from vibeview.tui import scene as scenelib

    coords, _freq = scenelib.vibration_frames(reader, "vib", mode, scale=scale)
    base = np.array([a["position"] for a in _ATOMS])
    return _max_excursion(coords, base)


def _browser_peak(reader: QVFReader, mode: int, amplitude: float = 1.0) -> float:
    from vibeview.renderers.vibrations import VibrationsRenderer

    renderer = VibrationsRenderer(reader.get_section("vib"), reader)
    base = np.array([a["position"] for a in _ATOMS])
    peaks = []
    for phase in np.linspace(0.0, 2 * np.pi, 33):
        pos = np.array([p for _s, p in renderer.displace_atoms(mode, amplitude, phase=phase)])
        peaks.append(_max_excursion(pos, base))
    return max(peaks)


@pytest.mark.parametrize("mode", [0, 1])
def test_terminal_and_browser_draw_the_same_peak_excursion(tmp_path, mode):
    """#38-28: raw peak 0.02 Å gave 0.6 Å in the terminal and 0.02 Å in the
    browser. Both now draw DEFAULT_PEAK_EXCURSION_ANGSTROM."""
    with QVFReader(_archive(tmp_path, _DISP)) as reader:
        tui = _tui_peak(reader, mode)
        browser = _browser_peak(reader, mode)
    assert tui == pytest.approx(DEFAULT_PEAK_EXCURSION_ANGSTROM, rel=0.02)
    assert browser == pytest.approx(DEFAULT_PEAK_EXCURSION_ANGSTROM, rel=1e-6)


def test_browser_amplitude_control_is_the_multiplier(tmp_path):
    with QVFReader(_archive(tmp_path, _DISP)) as reader:
        assert _browser_peak(reader, 1, amplitude=2.5) == pytest.approx(peak_excursion(2.5))
        assert _browser_peak(reader, 1, amplitude=0.0) == pytest.approx(0.0)
        assert _tui_peak(reader, 1, scale=2.5) == pytest.approx(peak_excursion(2.5), rel=0.02)


def test_zero_mode_is_safe(tmp_path):
    with QVFReader(_archive(tmp_path, _DISP)) as reader:
        assert _browser_peak(reader, 2) == 0.0
        assert _tui_peak(reader, 2) == 0.0
    assert not np.any(normalized_displacement(np.zeros((2, 3))))
    assert not np.any(normalized_displacement(np.full((2, 3), np.nan)))


def test_largest_atomic_excursion_is_a_vector_norm():
    out = normalized_displacement(_DISP[1])
    assert float(np.linalg.norm(out, axis=1).max()) == pytest.approx(
        DEFAULT_PEAK_EXCURSION_ANGSTROM
    )


def test_raw_displacements_are_unchanged(tmp_path):
    with QVFReader(_archive(tmp_path, _DISP)) as reader:
        from vibeview.renderers.vibrations import VibrationsRenderer
        from vibeview.tui import scene as scenelib

        renderer = VibrationsRenderer(reader.get_section("vib"), reader)
        renderer.displace_atoms(1, 1.0, phase=0.3)
        scenelib.vibration_frames(reader, "vib", 1)
        np.testing.assert_array_equal(reader.read_vibrations("vib").displacements, _DISP)


def test_browser_shows_the_effective_scale():
    """The state carries the default peak excursion, and the served label
    multiplies it by the slider, so the user reads the effective scale."""
    from pathlib import Path

    from tests._served import served_viewer
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader
    from vibeview.vibration_amplitude import DEFAULT_PEAK_EXCURSION_ANGSTROM

    reader = QVFReader(Path(__file__).parent / "data" / "h2_natural_orbitals.qvf")
    try:
        assert create_app(reader).state.vibration_peak_default == DEFAULT_PEAK_EXCURSION_ANGSTROM
    finally:
        reader.close()
    assert "vibration_peak_default * vibration_amplitude" in served_viewer()
