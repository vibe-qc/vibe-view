"""``table --kind vibrations`` shows every per-mode quantity the file has (#150)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from tests.conftest import _water_structure_bytes, build_qvf
from vibeview.qvf import QVFError, QVFReader
from vibeview.tables import extract_table

_EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "examples"
_FREQS = [1600.0, 3650.0, 3750.0]


def _water(
    tmp_path: Path, meta: dict[str, Any], spectra: dict[str, dict[str, Any]] | None = None
) -> Path:
    disp = np.zeros((3, 3, 3), dtype=np.float64).tobytes()
    vib_meta = json.dumps({"frequencies": _FREQS, **meta}).encode()
    sections: list[Any] = [
        ("structure", "structure",
         {"structure": ("json", "sections/structure.json", _water_structure_bytes())}),
        ("vib", "vibrations", {
            "metadata": ("json", "vib/meta.json", vib_meta),
            "displacements": ("binary", "vib/disp.bin", disp, "float64", [3, 3, 3]),
        }),
    ]
    for kind, payload in (spectra or {}).items():
        sid = kind.replace(".", "_")
        sections.append((sid, kind, {
            "spectrum": ("json", f"spectra/{sid}.json", json.dumps(payload).encode()),
        }))
    return build_qvf(tmp_path / "w.qvf", sections)


def _table(path: Path) -> tuple[list[str], list[list[Any]]]:
    with QVFReader(path) as reader:
        return extract_table(reader, "vibrations")


def test_section_fields_become_columns_with_units(tmp_path: Path) -> None:
    columns, rows = _table(_water(tmp_path, {
        "ir_intensities": [50.0, 2.0, 20.0],
        "raman_activities": [5.0, 90.0, 30.0],
        "reduced_masses": [1.08, 1.04, 1.08],
        "force_constants": [1.6, 8.1, 8.9],
        "symmetry_labels": ["A1", "A1", "B2"],
    }))
    assert columns == [
        "mode", "frequency_cm-1", "symmetry", "ir_intensity_km/mol",
        "raman_activity_angstrom^4/amu", "reduced_mass_amu", "force_constant_mdyn/angstrom",
    ]
    assert rows[2] == [3, 3750.0, "B2", 20.0, 30.0, 1.08, 8.9]


def test_a_bare_section_keeps_two_columns(tmp_path: Path) -> None:
    columns, rows = _table(_water(tmp_path, {}))
    assert columns == ["mode", "frequency_cm-1"]
    assert rows[0] == [1, 1600.0]


def test_the_section_value_wins_over_a_companion_spectrum(tmp_path: Path) -> None:
    columns, rows = _table(_water(
        tmp_path, {"ir_intensities": [1.0, 2.0, 3.0]},
        {"spectra.ir": {"frequencies": _FREQS, "intensities": [9.0, 9.0, 9.0]}},
    ))
    assert columns[-1] == "ir_intensity_km/mol"
    assert [row[-1] for row in rows] == [1.0, 2.0, 3.0]


def test_a_companion_uses_its_declared_unit(tmp_path: Path) -> None:
    columns, rows = _table(_water(tmp_path, {}, {
        "spectra.raman": {"frequencies": _FREQS, "intensities": [1.0, 2.0, 3.0],
                          "y_units": "angstrom^4/amu"},
    }))
    assert columns[-1] == "raman_activity_angstrom^4/amu"
    assert rows[1][-1] == 2.0


def test_peaks_assign_a_short_spectrum_by_mode_index(tmp_path: Path) -> None:
    columns, rows = _table(_water(tmp_path, {}, {
        "spectra.vcd": {
            "frequencies": [3650.0], "intensities": [-4.0],
            "peaks": [{"x": 3650.0, "y": -4.0, "assignment": {"mode_index": 1}}],
        },
    }))
    assert columns[-1] == "vcd_rotatory_strength"
    assert [row[-1] for row in rows] == [None, -4.0, None]


def test_a_short_spectrum_without_peaks_matches_exact_frequencies(tmp_path: Path) -> None:
    _columns, rows = _table(_water(tmp_path, {}, {
        "spectra.vcd": {"frequencies": [3750.0, 1234.5], "intensities": [7.0, 1.0]},
    }))
    assert [row[-1] for row in rows] == [None, None, 7.0]


def test_a_spectrum_that_matches_no_mode_adds_no_column(tmp_path: Path) -> None:
    columns, _rows = _table(_water(tmp_path, {}, {
        "spectra.vcd": {"frequencies": [1234.5], "intensities": [1.0]},
    }))
    assert columns == ["mode", "frequency_cm-1"]


@pytest.mark.parametrize(
    ("meta", "message"),
    [
        ({"reduced_masses": [1.0, 1.0]}, "reduced_masses has 2 entries for 3 modes"),
        ({"ir_intensities": 5.0}, "ir_intensities must be a flat list"),
        ({"symmetry_labels": ["A1", 2, "B2"]}, "symmetry_labels must be a list of strings"),
    ],
)
def test_malformed_per_mode_fields_are_refused(
    tmp_path: Path, meta: dict[str, Any], message: str
) -> None:
    with QVFReader(_water(tmp_path, meta)) as reader, pytest.raises(QVFError, match=message):
        reader.read_vibrations("vib")


def test_orca_example_joins_ir_raman_and_the_real_mode_vcd() -> None:
    columns, rows = _table(_EXAMPLES / "orca-raman-vcd" / "peroxide.qvf")
    assert columns == [
        "mode", "frequency_cm-1", "ir_intensity_km/mol",
        "raman_activity_angstrom^4/amu", "vcd_rotatory_strength_1e-44esu^2cm^2",
    ]
    assert rows[0][-1] is None  # ORCA's VCD table lists only the real modes
    assert rows[6][1:] == [328.83, 185.5128, 6.7666, 194.95]
    assert rows[11][-1] == 28.83


@requires_qvf_corpus("vibrations.qvf")
def test_conformance_corpus_vibrations() -> None:
    corpus = qvf_corpus_dir()
    assert corpus is not None
    columns, rows = _table(corpus / "vibrations.qvf")
    assert columns == [
        "mode", "frequency_cm-1", "symmetry", "ir_intensity_km/mol", "reduced_mass_amu",
    ]
    assert rows[0] == [1, -893.0, "A2''", 512.0, 1.17]


# ── #151: the browser's mode picker agrees with the table ───────────────────


def _picker(path: Path) -> list[str]:
    import types

    from vibeview.app import _activate_vibrations

    with QVFReader(path) as reader:
        state = types.SimpleNamespace()
        _activate_vibrations(reader, state, reader.get_section("vib"))
    return [item["title"] for item in state.vibration_mode_items]


@pytest.mark.parametrize(
    ("frequency", "intensities", "unit", "title"),
    [
        (-893.0, [512.0], "km/mol", "Mode 1 — 893.0i cm⁻¹ (imaginary, IR 512.0 km/mol)"),
        (-893.0, None, None, "Mode 1 — 893.0i cm⁻¹ (imaginary)"),
        (0.0, [0.0], "km/mol", "Mode 1 — 0.0 cm⁻¹ (trans/rot)"),
        (1608.0, [47.49], "km/mol", "Mode 1 — 1608.0 cm⁻¹ (IR 47.5 km/mol)"),
        (1608.0, [None], "km/mol", "Mode 1 — 1608.0 cm⁻¹"),
        (1608.0, None, None, "Mode 1 — 1608.0 cm⁻¹"),
    ],
)
def test_vibration_mode_title(
    frequency: float, intensities: list[float | None] | None, unit: str | None, title: str
) -> None:
    from vibeview.app.helpers import vibration_mode_title

    assert vibration_mode_title(0, frequency, intensities, unit) == title


def test_picker_uses_the_sections_own_ir_intensities(tmp_path: Path) -> None:
    titles = _picker(_water(tmp_path, {"ir_intensities": [50.0, 2.0, 20.0]}))
    assert titles[0] == "Mode 1 — 1600.0 cm⁻¹ (IR 50.0 km/mol)"


def test_picker_joins_a_short_ir_spectrum(tmp_path: Path) -> None:
    titles = _picker(_water(tmp_path, {}, {
        "spectra.ir": {"frequencies": [3750.0], "intensities": [20.0]},
    }))
    assert titles == [
        "Mode 1 — 1600.0 cm⁻¹", "Mode 2 — 3650.0 cm⁻¹", "Mode 3 — 3750.0 cm⁻¹ (IR 20.0 km/mol)",
    ]


@requires_qvf_corpus("vibrations.qvf")
def test_picker_marks_the_corpus_transition_state_mode_imaginary() -> None:
    corpus = qvf_corpus_dir()
    assert corpus is not None
    titles = _picker(corpus / "vibrations.qvf")
    assert titles[0] == "Mode 1 — 893.0i cm⁻¹ (imaginary, IR 512.0 km/mol)"
