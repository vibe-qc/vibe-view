"""QVF ``excited_states`` sections are read and rendered as a table (#109).

#96 made the kind "deferred": surfaced as "skipped, not yet rendered" rather
than unsupported, with only one state's energy read for NTO labels. Now the
reader returns the typed states, ``vibe-view table`` and the API tabulate
them, the terminal has a pane and the browser a details panel, and the kind
is supported. The table never shows a guessed value: a malformed state is a
reader error.
"""

from __future__ import annotations

import json
import math

import pytest
from click.testing import CliRunner

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from vibeview.cli import main
from vibeview.kinds import classify_section
from vibeview.qvf import QVFError, QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload
from vibeview.tables import extract_table
from vibeview.units import EV_NM, HARTREE_TO_EV

WATER = [
    {"symbol": "O", "position": [0.0, 0.0, 0.117], "atomic_number": 8},
    {"symbol": "H", "position": [0.0, 0.757, -0.469], "atomic_number": 1},
    {"symbol": "H", "position": [0.0, -0.757, -0.469], "atomic_number": 1},
]
MOLECULE = (False, False, False)

STATES = [
    {
        "index": 1,
        "excitation_energy": {"value": 7.4, "units": "ev"},
        "total_energy": {"value": -75.04103841590015, "units": "hartree"},
        "multiplicity": 1,
        "symmetry": "B1",
        "oscillator_strength": 0.0521,
        "rotatory_strength_cgs": 0.0,
        "transition_dipole": [0.536, 0.0, 0.0],
        "transition_magnetic_dipole": [0.0, 0.2134, 0.0],
        "contributions": [{"from_mo": 4, "to_mo": 5, "weight": 0.99}],
        "nto_ref": "nto_s1_hole",
        "density_ref": "tden_s1",
    },
    {
        "index": 2,
        "excitation_energy": {"value": 9.2, "units": "ev"},
        "multiplicity": 1,
        "symmetry": "A2",
        "oscillator_strength": 0.0,
        "contributions": [{"from_mo": 4, "to_mo": 6, "weight": 0.98}],
    },
    {
        "index": 3,
        "excitation_energy": {"value": 9.85, "units": "ev"},
        "multiplicity": 1,
        "symmetry": "A1",
        "oscillator_strength": 0.0846,
        "contributions": [
            {"from_mo": 3, "to_mo": 5, "weight": 0.9},
            {"from_mo": 2, "to_mo": 6, "weight": 0.08, "spin": "alpha"},
        ],
    },
]
META = {"method": "tddft", "reference_state": "S0", "n_states": 3, "tda": False}


def excited_archive(path, states=None, section_extra=None, meta=META):
    builder = QVFArchiveBuilder(program="test", version="0", calculation="tddft")
    builder.add_json("structure", "structure", "structure", structure_payload(WATER, MOLECULE))
    payload = {"states": STATES if states is None else states}
    if meta is not None:
        payload["meta"] = meta
    builder.add_json(
        "excited_states",
        "excited_states",
        "states",
        payload,
        section_extra={"structure_ref": "structure", **(section_extra or {})},
    )
    return builder.write(path)


# ── the reader ─────────────────────────────────────────────────────────────


def test_reader_returns_typed_states(tmp_path):
    with QVFReader(excited_archive(tmp_path / "a.qvf")) as reader:
        data = reader.read_excited_states("excited_states")
    assert [s.index for s in data.states] == [1, 2, 3]
    first = data.states[0]
    assert first.excitation_energy_ev == 7.4
    assert first.wavelength_nm == pytest.approx(EV_NM / 7.4)
    assert first.total_energy_eh == pytest.approx(-75.04103841590015)
    assert (first.multiplicity, first.symmetry) == (1, "B1")
    assert (first.oscillator_strength, first.rotatory_strength_cgs) == (0.0521, 0.0)
    assert first.transition_dipole == (0.536, 0.0, 0.0)
    assert first.transition_magnetic_dipole == (0.0, 0.2134, 0.0)
    assert (first.nto_ref, first.density_ref) == ("nto_s1_hole", "tden_s1")
    assert [(c.from_mo, c.to_mo, c.weight) for c in first.contributions] == [(4, 5, 0.99)]
    second = data.states[1]
    assert second.total_energy_eh is None and second.nto_ref is None
    assert data.states[2].contributions[1].spin == "alpha"
    assert data.meta["method"] == "tddft"
    assert data.structure_ref == "structure"
    assert data.wavefunction_ref is None


def test_reader_converts_the_declared_energy_units(tmp_path):
    states = [
        {
            "index": 1,
            "excitation_energy": {"value": 0.25, "units": "hartree"},
            "total_energy": {"value": -2041.0, "units": "eV"},
        }
    ]
    with QVFReader(excited_archive(tmp_path / "a.qvf", states=states)) as reader:
        state = reader.read_excited_states("excited_states").states[0]
    assert state.excitation_energy_ev == pytest.approx(0.25 * HARTREE_TO_EV)
    assert state.total_energy_eh == pytest.approx(-2041.0 / HARTREE_TO_EV)


def test_reader_keeps_the_section_level_references(tmp_path):
    path = excited_archive(tmp_path / "a.qvf", section_extra={"wavefunction_ref": "wf"})
    with QVFReader(path) as reader:
        assert reader.read_excited_states("excited_states").wavefunction_ref == "wf"


def test_a_section_without_meta_or_states_reads_empty(tmp_path):
    with QVFReader(excited_archive(tmp_path / "a.qvf", states=[], meta=None)) as reader:
        data = reader.read_excited_states("excited_states")
    assert data.states == [] and data.meta == {}


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"excitation_energy": {"value": 1.0, "units": "ev"}}, "index must be an integer"),
        ({"index": 0, "excitation_energy": {"value": 1.0, "units": "ev"}}, "at least 1"),
        ({"index": 1}, "payload is not a JSON object"),
        ({"index": 1, "excitation_energy": {"value": 1.0, "units": "kJ"}}, "not an energy unit"),
        ({"index": 1, "excitation_energy": {"value": "1", "units": "ev"}}, "finite number"),
        ({"index": 1, "excitation_energy": {"value": 1.0, "units": "ev"},
          "transition_dipole": [0.0, 1.0]}, "three components"),
        ({"index": 1, "excitation_energy": {"value": 1.0, "units": "ev"},
          "multiplicity": 0}, "multiplicity must be at least 1"),
        ({"index": 1, "excitation_energy": {"value": 1.0, "units": "ev"},
          "contributions": {"from_mo": 1}}, "contributions must be a list"),
        ({"index": 1, "excitation_energy": {"value": 1.0, "units": "ev"},
          "contributions": [{"from_mo": 1, "to_mo": 2, "spin": "up"}]}, "alpha"),
        ({"index": 1, "excitation_energy": {"value": 1.0, "units": "ev"},
          "symmetry": 7}, "symmetry must be a string"),
    ],
)
def test_a_malformed_state_is_a_reader_error(tmp_path, state, reason):
    with (
        QVFReader(excited_archive(tmp_path / "a.qvf", states=[state])) as reader,
        pytest.raises(QVFError, match=reason),
    ):
        reader.read_excited_states("excited_states")


def test_repeated_indices_are_refused(tmp_path):
    states = [dict(STATES[0]), {**STATES[1], "index": 1}]
    with (
        QVFReader(excited_archive(tmp_path / "a.qvf", states=states)) as reader,
        pytest.raises(QVFError, match="unique"),
    ):
        reader.read_excited_states("excited_states")


def test_the_wrong_kind_is_refused(tmp_path):
    with (
        QVFReader(excited_archive(tmp_path / "a.qvf")) as reader,
        pytest.raises(QVFError, match="not excited_states"),
    ):
        reader.read_excited_states("structure")


# ── the table, the CLI and the kind registry ───────────────────────────────


def test_extract_table_tabulates_the_states(tmp_path):
    with QVFReader(excited_archive(tmp_path / "a.qvf")) as reader:
        columns, rows = extract_table(reader, "excited_states")
    assert columns[:3] == ["state", "energy_ev", "wavelength_nm"]
    assert rows[0][:3] == [1, 7.4, round(EV_NM / 7.4, 1)]
    row = dict(zip(columns, rows[2], strict=True))
    assert row["contributions"] == "3→5 (0.90); 2→6 a (0.08)"
    assert row["nto_ref"] is None
    assert dict(zip(columns, rows[0], strict=True))["nto_ref"] == "nto_s1_hole"


def test_the_cli_lists_and_writes_the_table(tmp_path):
    path = str(excited_archive(tmp_path / "a.qvf"))
    listed = CliRunner().invoke(main, ["table", path])
    assert listed.exit_code == 0 and "excited_states" in listed.output

    as_json = CliRunner().invoke(
        main, ["table", path, "--kind", "excited_states", "--format", "json"]
    )
    assert as_json.exit_code == 0, as_json.output
    rows = json.loads(as_json.output)
    assert [r["state"] for r in rows] == [1, 2, 3]
    assert rows[0]["symmetry"] == "B1" and rows[0]["oscillator_strength"] == 0.0521

    as_csv = CliRunner().invoke(
        main, ["table", path, "--kind", "excited_states", "--format", "csv"]
    )
    assert as_csv.exit_code == 0
    assert as_csv.output.splitlines()[0].startswith("state,energy_ev,wavelength_nm")


def test_the_kind_is_rendered_not_deferred():
    assert classify_section("excited_states") == ("rendered", None)


# ── the terminal pane ──────────────────────────────────────────────────────


def test_the_terminal_pane_tabulates_the_states(tmp_path):
    from vibeview.tui import panes

    with QVFReader(excited_archive(tmp_path / "a.qvf")) as reader:
        text = panes.excited_states(reader, "excited_states")
    assert "Excited states" in text
    assert "tddft" in text and "S0" in text
    assert "7.400" in text and f"{EV_NM / 7.4:.1f}" in text
    assert "4→5 (0.99)" in text
    assert "NTO nto_s1_hole" in text and "density tden_s1" in text


# ── the browser panel ──────────────────────────────────────────────────────


@pytest.fixture
def plotters(monkeypatch):
    import vibeview.app.helpers as helpers

    made = []
    create = helpers.new_plotter

    def record(*args, **kwargs):
        plotter = create(*args, **kwargs)
        made.append(plotter)
        return plotter

    monkeypatch.setattr(helpers, "new_plotter", record)
    yield made
    for plotter in made:
        plotter.close()


@pytest.fixture
def open_app(plotters):
    readers: list[QVFReader] = []

    def _open(path):
        from vibeview.app import create_app

        reader = QVFReader(path)
        readers.append(reader)
        server = create_app([reader])
        server.controller.view_update = lambda *a, **k: None
        return server, reader, plotters[-1]

    yield _open
    for reader in readers:
        reader.close()


def test_the_browser_panel_shows_the_table(tmp_path, open_app):
    app, _reader, _plotter = open_app(excited_archive(tmp_path / "a.qvf"))
    app.controller.activate_section("excited_states")
    assert app.state.properties_title == "Excited States (TD-DFT)"
    html = app.state.properties_html
    for text in ("B1", "A2", "A1", "4→5 (0.99)", "nto_s1_hole", "Reference state", "S0"):
        assert text in html, text
    assert f"{EV_NM / 7.4:.1f}" in html
    assert app.state.status_message.startswith("Loaded excited states")


def test_the_sidebar_titles_the_section(tmp_path, open_app):
    app, _reader, _plotter = open_app(excited_archive(tmp_path / "a.qvf"))
    entries = app.state.to_dict().get("sidebar_entries") or app.state.to_dict().get(
        "section_entries"
    )
    titles = {e.get("id"): e.get("title") for e in entries} if entries else {}
    assert titles.get("excited_states", "Excited States").startswith("Excited States")


# ── the corpus archive ─────────────────────────────────────────────────────


@requires_qvf_corpus("tddft.qvf")
def test_the_corpus_tddft_archive_tabulates():
    corpus = qvf_corpus_dir()
    assert corpus is not None
    path = corpus / "tddft.qvf"
    with QVFReader(path) as reader:
        data = reader.read_excited_states("excited_states")
        columns, rows = extract_table(reader, "excited_states")
    assert [s.index for s in data.states] == [1, 2, 3]
    assert data.wavefunction_ref == "wf"
    assert data.states[0].nto_ref == "nto_s1_hole"
    # f = (2/3) ΔE |μ|² in atomic units, as the corpus description promises.
    first = data.states[0]
    assert first.transition_dipole is not None
    mu2 = sum(c * c for c in first.transition_dipole)
    assert first.oscillator_strength == pytest.approx(
        (2 / 3) * (first.excitation_energy_ev / HARTREE_TO_EV) * mu2, rel=1e-2
    )
    assert len(rows) == 3 and not any(math.isnan(r[1]) for r in rows)
    result = CliRunner().invoke(main, ["table", str(path), "--kind", "excited_states"])
    assert result.exit_code == 0, result.output
    assert "B1" in result.output
