"""The command line and the terminal find structures by kind (#90).

``vibe-view export`` of a vibe-sketch document failed with "section not
found: 'structure'". Every command that needs one structure now takes the
first structure (QVF spec § 4.1), ``export --section`` and ``capture
--section`` take another, ``info`` names the species by label and name
(§ 5.1), and the terminal viewer opens on, draws and tabulates the
structure that is selected.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from tests.test_issue90_reader import SCHEME, species_archive
from vibeview.cli import main
from vibeview.qvf import QVFReader


def _xyz_symbols(text: str) -> list[str]:
    lines = text.splitlines()
    return [line.split()[0] for line in lines[2 : 2 + int(lines[0])]]


@pytest.fixture
def sketch(tmp_path):
    return species_archive(tmp_path / "sketch.qvf", identity=False)


def test_export_takes_the_first_structure(tmp_path, sketch):
    out = tmp_path / "out.xyz"
    result = CliRunner().invoke(main, ["export", str(sketch), "-f", "xyz", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert _xyz_symbols(out.read_text()) == ["O", "H", "H"]


def test_export_follows_auto_open(tmp_path):
    path = species_archive(tmp_path / "a.qvf", viewer_defaults={"auto_open": ["structure_m3"]})
    out = tmp_path / "out.xyz"
    result = CliRunner().invoke(main, ["export", str(path), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert _xyz_symbols(out.read_text()) == ["O", "H"]


def test_export_section_picks_another_structure(tmp_path, sketch):
    out = tmp_path / "out.xyz"
    result = CliRunner().invoke(
        main, ["export", str(sketch), "--section", "structure_m2", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert _xyz_symbols(out.read_text()) == ["N", "H", "H", "H", "H"]


@pytest.mark.parametrize(("section", "message"), [("nope", "not found"), ("scheme", "not a")])
def test_export_section_refuses_what_is_not_a_structure(tmp_path, section, message):
    path = species_archive(tmp_path / "a.qvf", scheme=SCHEME)
    result = CliRunner().invoke(
        main, ["export", str(path), "-s", section, "-o", str(tmp_path / "o.xyz")]
    )
    assert result.exit_code != 0
    assert message in result.output
    assert not (tmp_path / "o.xyz").exists()


def test_a_python_input_carries_the_structures_own_charge(tmp_path):
    path = species_archive(tmp_path / "a.qvf")
    out = tmp_path / "ammonium.py"
    result = CliRunner().invoke(
        main, ["export", str(path), "-f", "py", "-s", "structure_m2", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "charge=1, multiplicity=1" in out.read_text()


def test_capture_draws_the_section_it_names(tmp_path, monkeypatch, sketch):
    import vibeview.capture as capture

    drawn: list[str | None] = []

    def fake_capture(reader, out, **_kwargs):
        drawn.append(reader.shown_structure_id)
        out.write_bytes(b"png")
        return True

    monkeypatch.setattr(capture, "capture_structure", fake_capture)
    runner = CliRunner()
    for args in ([], ["--section", "structure_m3"]):
        result = runner.invoke(
            main, ["capture", str(sketch), *args, "-o", str(tmp_path / "x.png")]
        )
        assert result.exit_code == 0, result.output
    assert drawn == ["structure_m1", "structure_m3"]


def test_capture_renders_a_document_without_a_structure_id(tmp_path, sketch):
    out = tmp_path / "sketch.png"
    result = CliRunner().invoke(main, ["capture", str(sketch), "-o", str(out), "--size", "120x90"])
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"\x89PNG")


def test_info_names_the_species(tmp_path):
    path = species_archive(tmp_path / "a.qvf", scheme=SCHEME, preview=True)
    runner = CliRunner()
    text = runner.invoke(main, ["info", str(path)])
    assert text.exit_code == 0, text.output
    assert "1 · water" in text.output and "2 · ammonium" in text.output
    assert "Structures           3 (first: structure_m1)" in text.output
    assert "Proton shuffle" in text.output

    data = json.loads(runner.invoke(main, ["info", str(path), "--json"]).output)
    assert data["first_structure"] == "structure_m1"
    by_id = {s["id"]: s for s in data["sections"]}
    assert by_id["structure_m2"]["label"] == "2"
    assert by_id["structure_m2"]["name"] == "ammonium"
    assert by_id["structure_m2"]["charge"] == 1
    assert by_id["structure_m2"]["smiles"] == "[NH4+]"
    assert "charge" not in by_id["structure_m3"]


def test_the_banner_knows_the_descriptive_kinds(tmp_path):
    from vibeview.banner import format_banner

    path = species_archive(tmp_path / "a.qvf", scheme=SCHEME, preview=True)
    with QVFReader(path) as reader:
        banner = format_banner(reader)
    assert "reaction.scheme" in banner and "preview" in banner
    assert "unsupported" not in banner


@requires_qvf_corpus("multi_structure.qvf", "reaction_scheme.qvf", "job_set.qvf")
@pytest.mark.parametrize(
    ("name", "first", "symbols"),
    [
        ("multi_structure.qvf", "h2o", None),
        ("reaction_scheme.qvf", "structure_s1", ["C", "H", "H", "H", "H"]),
        ("job_set.qvf", "structure_s6", None),
    ],
)
def test_corpus_archives_export_their_first_structure(tmp_path, name, first, symbols):
    corpus = qvf_corpus_dir()
    assert corpus is not None
    out = tmp_path / "out.xyz"
    result = CliRunner().invoke(main, ["export", str(corpus / name), "-o", str(out)])
    assert result.exit_code == 0, result.output
    with QVFReader(corpus / name) as reader:
        assert reader.first_structure_id == first
        expected = [a.symbol for a in reader.read_structure(first).atoms]
    assert _xyz_symbols(out.read_text()) == (symbols or expected)


# ── the terminal ───────────────────────────────────────────────────────────


def test_terminal_panes_name_the_structure_and_list_the_scheme(tmp_path):
    from vibeview.tui import panes

    path = species_archive(tmp_path / "a.qvf", scheme=SCHEME)
    with QVFReader(path) as reader:
        table = panes.structure_table(reader)
        other = panes.structure_table(reader, structure_id="structure_m2")
        scheme = panes.reaction_scheme(reader, "scheme")
    assert "Structure · 1 · water" in table
    assert "Structure · 2 · ammonium" in other and "[NH4+]" in other
    assert "2 × ammonium (2) ⇌ water (1)" in scheme
    assert "THF; 2 h" in scheme and "87%" in scheme


def test_show_info_names_the_species(tmp_path):
    path = species_archive(tmp_path / "a.qvf", scheme=SCHEME)
    result = CliRunner().invoke(main, ["show", str(path), "--info", "--plain"])
    assert result.exit_code == 0, result.output
    assert "2 · ammonium" in result.output and "Proton shuffle" in result.output


def test_show_draws_the_structure_section_it_is_given(tmp_path, sketch):
    from vibeview.tui import scene as scenelib
    from vibeview.tui.show import default_section

    with QVFReader(sketch) as reader:
        assert default_section(reader) == "structure_m1"
        assert len(scenelib.structure_scene(reader).positions) == 3
        assert len(scenelib.structure_scene(reader, structure_id="structure_m2").positions) == 5


def test_the_terminal_viewer_opens_on_and_switches_structures(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from vibeview.tui.app import VibeViewTUI

    path = species_archive(
        tmp_path / "a.qvf", viewer_defaults={"auto_open": ["structure_m2"]}
    )

    async def body() -> tuple[str, int, str | None, int]:
        app = VibeViewTUI(path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            opened = app.current[0]
            opened_atoms = len(app._structure_scene().positions)
            app.selected = next(
                i for i, entry in enumerate(app.entries) if entry[0] == "structure_m3"
            )
            app._on_selection_changed()
            await pilot.pause()
            return opened, opened_atoms, app.reader.shown_structure_id, len(
                app._structure_scene().positions
            )

    opened, opened_atoms, shown, atoms = asyncio.run(body())
    assert (opened, opened_atoms) == ("structure_m2", 5)
    assert (shown, atoms) == ("structure_m3", 2)
