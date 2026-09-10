"""Tests for the terminal scene layer, plots, text panes and the CLI.

Rasterizer and braille-packing tests live in test_tui_raster.py; the app's
own interaction tests live in test_tui_app.py.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vibeview.qvf import QVFReader
from vibeview.tui import plots
from vibeview.tui import scene as scenelib
from vibeview.tui import show as showmod

# ── lattice ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pbc", "n_edges"),
    [((True, True, True), 12), ((True, True, False), 4), ((True, False, False), 1)],
)
def test_cell_edges_only_span_periodic_axes(pbc, n_edges):
    """Synthesized lattice columns are bookkeeping, never cell edges.

    A 2D slab's third column is a 30-bohr normal that is numerically
    indistinguishable from a real vacuum gap — pbc is the only signal, so
    drawing by rank would put a box around every slab.
    """
    lattice = np.diag([3.0, 4.0, 30.0])
    starts, ends = scenelib._cell_edges(lattice, pbc)
    assert len(starts) == n_edges
    if not pbc[2]:
        assert np.allclose(starts[:, 2], 0.0) and np.allclose(ends[:, 2], 0.0)


def test_cell_edges_shear_with_a_hexagonal_lattice():
    lattice = np.array([[4.92, 0.0, 0.0], [2.46, 4.26, 0.0], [0.0, 0.0, 7.94]])
    starts, ends = scenelib._cell_edges(lattice, (True, True, False))
    edges = ends - starts
    assert any(np.allclose(e, lattice[1], atol=1e-9) for e in edges)


# ── QVF integration ───────────────────────────────────────────────────────


def test_structure_scene_from_archive(sample_qvf):
    reader = QVFReader(sample_qvf)
    scene = scenelib.structure_scene(reader)
    assert len(scene.positions) == 3
    # Oxygen must be bigger than hydrogen and not the same colour.
    assert scene.radii[0] > scene.radii[1]
    assert tuple(scene.colors[0]) != tuple(scene.colors[1])
    assert len(scene.bond_starts) == 2


def test_representations_change_atom_and_bond_size(sample_qvf):
    reader = QVFReader(sample_qvf)
    ball = scenelib.structure_scene(reader, representation="ball_and_stick")
    fill = scenelib.structure_scene(reader, representation="spacefill")
    assert fill.radii[0] > ball.radii[0]
    assert len(fill.bond_starts) == 0


def test_volume_meshes_produce_signed_lobes(sample_qvf):
    """Marching cubes runs through VTK's compute filter — no GL context."""
    reader = QVFReader(sample_qvf)
    meshes = scenelib.volume_meshes(reader, "density", isovalue=0.2)
    assert meshes, "expected an isosurface from the seeded density peak"
    for mesh in meshes:
        assert mesh.faces.max() < len(mesh.vertices), "face indices must stay in range"
        assert len(mesh.normals) == len(mesh.vertices)


def test_evaluated_wavefunction_meshes_keep_angstrom_coordinates():
    """On-demand MO grids are already in A, unlike stored volume grids.

    Feeding that grid through the stored-volume bohr conversion shrinks and
    shifts the orbital away from its atoms.  A real natural orbital pins both
    signed lobes and the world-coordinate convention.
    """
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    path = Path(__file__).resolve().parent / "data" / "h2_natural_orbitals.qvf"
    reader = QVFReader(path)
    renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
    grid, values = renderer.evaluate_mo(1, n_per_dim=28)

    meshes = scenelib.sampled_field_meshes(
        grid,
        values,
        isovalue=0.05,
        signed=True,
        grid_units="angstrom",
    )

    assert len(meshes) == 2, "the antibonding natural orbital has both signs"
    vertices = np.vstack([mesh.vertices for mesh in meshes])
    assert vertices[:, 2].min() < -1.0
    assert vertices[:, 2].max() > 2.0
    for mesh in meshes:
        assert mesh.faces.max() < len(mesh.vertices)
        assert len(mesh.normals) == len(mesh.vertices)


def test_sampled_field_replication_ignores_nonperiodic_slab_axis():
    """A requested z replica must not send a 2D field into vacuum."""
    from vibeview.qvf import GridData

    shape = (16, 16, 16)
    step = 0.2
    grid = GridData(
        origin=np.zeros(3),
        voxel_vectors=np.diag([step, step, step]),
        shape=shape,
    )
    x, y, z = np.meshgrid(
        *(np.arange(n) * step for n in shape), indexing="ij"
    )
    values = np.exp(-((x - 1.5) ** 2 + (y - 1.5) ** 2 + (z - 1.5) ** 2))
    lattice = np.diag([3.0, 3.0, 30.0])

    def meshes(replication):
        return scenelib.sampled_field_meshes(
            grid,
            values,
            isovalue=0.5,
            signed=False,
            replication=replication,
            pbc=(True, True, False),
            lattice_vectors=lattice,
        )

    base = meshes((1, 1, 1))
    z_requested = meshes((1, 1, 2))
    x_requested = meshes((2, 1, 1))

    assert len(base) == len(z_requested) == len(x_requested) == 1
    assert len(z_requested[0].vertices) == len(base[0].vertices)
    assert z_requested[0].vertices[:, 2].max() == pytest.approx(
        base[0].vertices[:, 2].max()
    )
    assert len(x_requested[0].vertices) == 2 * len(base[0].vertices)


@pytest.fixture
def periodic_qvf(tmp_path):
    """A graphene-like cell whose bond list crosses the periodic boundary."""
    import hashlib

    lattice = [[4.92, 0.0, 0.0], [2.46, 4.26084499, 0.0], [0.0, 0.0, 7.93765816]]
    positions = [[0.0, 0.0, 4.0], [1.23, 0.71, 4.0], [2.46, 0.0, 4.0], [3.69, 0.71, 4.0]]
    structure = json.dumps(
        {
            "atoms": [
                {"symbol": "C", "position": p, "atomic_number": 6} for p in positions
            ],
            "pbc": [True, True, False],
            "dim": 2,
            "lattice_vectors": lattice,
            # (0,3) is the wrapped pair: 3.69 Å apart directly, 1.42 Å to the
            # nearest image.
            "bonds": [[0, 1, 1.5], [1, 2, 1.5], [0, 3, 1.5]],
        }
    ).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "test", "version": "0", "calculation": "tui_test"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "sections/structure.json",
                        "format": "json",
                        "sha256": hashlib.sha256(structure).hexdigest(),
                    }
                },
            }
        ],
    }
    path = tmp_path / "periodic.qvf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/structure.json", structure)
    return path


def test_periodic_bonds_use_the_minimum_image(periodic_qvf):
    """A wrapped bond is drawn to the nearest image, not across the cell.

    The in-cell index pair for a bond that crosses a face has a direct
    separation of a whole lattice vector. Drawing those endpoints stretches
    a stick across the box; deleting them by a length cutoff loses real
    bonds. Neither is acceptable, so the renderer must re-image.
    """
    reader = QVFReader(periodic_qvf)
    scene = scenelib.structure_scene(reader)
    lengths = np.linalg.norm(scene.bond_ends - scene.bond_starts, axis=1)
    # Four, not three. The fixture's own comment calls (0,3) a wrapped pair
    # at 1.42 A to the nearest image, and inference used to miss it because
    # it searched raw Cartesian space, where those atoms sit 3.69 A apart.
    # This asserted 3 while that real bond was absent.
    #
    # Note the fixture's `bonds` key never took effect: explicit bonds come
    # from a `bonds` member or a `kind: bonds` section, not from a key in the
    # structure JSON body, so this has always exercised inference rather than
    # the list it appears to supply.
    assert len(lengths) == 4
    assert lengths.max() < 2.0, f"a wrapped bond was drawn at full length: {lengths}"


def test_replication_multiplies_atoms_and_cell(periodic_qvf):
    reader = QVFReader(periodic_qvf)
    single = scenelib.structure_scene(reader)
    super_cell = scenelib.structure_scene(reader, replication=(2, 2, 1))
    assert len(super_cell.positions) == 4 * len(single.positions)
    assert len(super_cell.line_starts) == 4 * len(single.line_starts)


def test_replication_ignores_non_periodic_axes(periodic_qvf):
    """Replicating along a synthesized axis would duplicate into vacuum."""
    reader = QVFReader(periodic_qvf)
    scene = scenelib.structure_scene(reader, replication=(1, 1, 4))
    assert len(scene.positions) == 4


# ── plots ─────────────────────────────────────────────────────────────────


def test_log_axis_does_not_clip_the_converging_tail():
    """Padding a log axis in linear space inverts it and eats the tail.

    A decaying |ΔE| is exactly the data whose smallest values matter most;
    if the limits are computed before the transform, the bottom of the
    curve silently leaves the plot.
    """
    plot = plots.Plot(60, 20, ylog=True)
    y = np.array([5e-2, 1e-3, 4e-6, 2e-9, 5e-13])
    plot.add(np.arange(len(y), dtype=float), y)
    (_xlo, _xhi), (ylo, yhi) = plot._data_range()
    assert ylo < np.log10(y.min())
    assert yhi > np.log10(y.max())

    grid = plot.render()
    text = grid.to_plain()
    # Content must reach both the top and the bottom of the data area.
    rows_with_content = [i for i, line in enumerate(text.splitlines()) if line.strip()]
    assert max(rows_with_content) - min(rows_with_content) > 10


def test_log_axis_tick_labels_are_distinct():
    plot = plots.Plot(60, 20, ylog=True)
    plot.add(np.arange(5, dtype=float), np.array([1e-1, 1e-3, 1e-5, 1e-7, 1e-9]))
    lines = plot.render().to_plain().splitlines()
    labels = [line.split("│")[0].strip() for line in lines if "│" in line]
    labels = [label for label in labels if label]
    assert len(set(labels)) == len(labels), f"duplicate y ticks: {labels}"


def test_plot_ylabel_does_not_overwrite_the_top_tick():
    plot = plots.Plot(60, 12, ylabel="residual", title="t")
    plot.add(np.arange(5, dtype=float), np.arange(5, dtype=float))
    lines = plot.render().to_plain().splitlines()
    assert lines[0].startswith("residual")
    assert "│" in lines[1]


def test_plot_renders_within_its_grid():
    plot = plots.Plot(40, 10, title="x", xlabel="x", ylabel="y")
    plot.add(np.linspace(0, 1, 50), np.sin(np.linspace(0, 6, 50)))
    grid = plot.render()
    assert grid.cols == 40
    assert grid.rows == 10


def test_scf_history_plot_from_archive(tmp_path):
    import hashlib

    payload = json.dumps(
        {
            "iterations": [
                {"iter": i + 1, "energy_eh": -76.0 + 10.0**-i, "delta_e": 10.0**-i}
                for i in range(8)
            ]
        }
    ).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "test", "version": "0", "calculation": "tui_test"},
        "sections": [
            {
                "id": "scf",
                "kind": "scf_history",
                "members": {
                    "iterations": {
                        "path": "sections/scf.json",
                        "format": "json",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                },
            }
        ],
    }
    path = tmp_path / "scf.qvf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/scf.json", payload)

    reader = QVFReader(path)
    grid = plots.scf_history_plot(reader, "scf", 70, 18).render()
    assert "SCF convergence" in grid.to_plain()


def _spectrum_qvf(tmp_path, kind, freqs, intens):
    import hashlib

    payload = json.dumps({"frequencies": list(freqs), "intensities": list(intens)}).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "test", "version": "0", "calculation": "tui_test"},
        "sections": [
            {
                "id": "sp",
                "kind": kind,
                "members": {
                    "spectrum": {
                        "path": "sections/sp.json",
                        "format": "json",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                },
            }
        ],
    }
    path = tmp_path / f"{kind.replace('.', '_')}.qvf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/sp.json", payload)
    return path


@pytest.mark.parametrize(
    "kind, xlabel",
    [
        ("spectra.ir", "Frequency (cm⁻¹)"),
        ("spectra.raman", "Raman shift (cm⁻¹)"),
        ("spectra.uvvis", "Energy (eV)"),
        ("spectra.ecd", "Energy (eV)"),
        ("spectra.vcd", "Frequency (cm⁻¹)"),
    ],
)
def test_spectra_plot_axis_label_follows_the_kind(tmp_path, kind, xlabel):
    """Regression: the terminal chart guessed the X unit from the data
    magnitude (`uvvis: max < 2000 → "nm"`), so an eV-valued UV-Vis
    spectrum was plotted in eV but labelled nm, and ECD fell through to
    the cm⁻¹ default. The axis identity is per-kind, shared with the
    Plotly renderer styles."""
    freqs = [400.0, 1600.0, 3100.0] if "cm" in xlabel else [4.005, 8.205, 10.273]
    path = _spectrum_qvf(tmp_path, kind, freqs, [1.0, 2.0, 0.5])
    plot = plots.spectra_plot(QVFReader(path), "sp", 80, 20)
    assert plot.xlabel == xlabel
    assert "nm" not in plot.xlabel

    # The terminal chart plots the QVF `frequencies` member unconverted,
    # so its axis label must stay in lockstep with the browser registry.
    from vibeview.renderers.spectra import _STYLES

    assert plot.xlabel == _STYLES[kind].x_label


def test_uvvis_terminal_envelope_is_not_cm1_broad(tmp_path):
    """Regression: the Lorentzian width floor was 4.0 — a cm⁻¹-scale
    constant — applied to eV data, so a 4–10 eV UV-Vis spectrum drew a
    γ=4 eV envelope spanning −8..22 eV. The floor is the kind's
    native-unit gamma; the envelope must stay near the data range."""
    freqs = [4.005, 8.205, 9.076, 9.499, 10.273]
    path = _spectrum_qvf(tmp_path, "spectra.uvvis", freqs, [0.1, 0.05, 0.02, 0.3, 0.01])
    plot = plots.spectra_plot(QVFReader(path), "sp", 100, 26)
    envelope = plot.series[0]
    assert "eV" in envelope.label
    assert envelope.x.min() > 0.0, "envelope leaks far below the data range"
    assert envelope.x.max() < 12.0, "envelope leaks far above the data range"


# ── show / CLI ────────────────────────────────────────────────────────────


def test_show_renders_a_structure(sample_qvf):
    text = showmod.show(sample_qvf, size=(60, 16), plain=True)
    assert text.strip(), "structure render came out empty"
    assert len(text.splitlines()) == 16


def test_show_defaults_to_the_structure_section(sample_qvf):
    reader = QVFReader(sample_qvf)
    assert showmod.default_section(reader) == "structure"


@pytest.fixture
def citations_qvf(tmp_path):
    """An archive whose only section has no graphical form."""
    import hashlib

    payload = b"@article{x, title={y}}"
    manifest = {
        "qvf_version": 1,
        "source": {"program": "test", "version": "0", "calculation": "tui_test"},
        "sections": [
            {
                "id": "cite",
                "kind": "citations",
                "members": {
                    "references": {
                        "path": "sections/refs.bib",
                        "format": "binary",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                },
            }
        ],
    }
    path = tmp_path / "cite.qvf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/refs.bib", payload)
    return path


def test_show_explains_itself_for_a_non_graphical_kind(citations_qvf):
    text = showmod.show(citations_qvf, "cite", size=(60, 10), plain=True)
    assert "no graphical form" in text


def test_show_all_covers_every_graphable_section(sample_qvf):
    text = showmod.show_all(sample_qvf, size=(50, 12), plain=True)
    assert "structure" in text
    assert "density" in text


def test_show_does_not_need_textual(monkeypatch):
    """`vibe-view show` must work on a core install.

    Guards the module split: the kind sets live in `scene`, not `app`, so
    importing `show` never drags in the [tui] extra.
    """
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "textual", None)
    module = importlib.reload(importlib.import_module("vibeview.tui.show"))
    assert hasattr(module, "show")


def test_cli_representation_choices_match_the_scene_table():
    """The click.Choice is duplicated to keep PyVista off the --help path."""
    from vibeview.cli import _TUI_REPRESENTATIONS

    assert set(_TUI_REPRESENTATIONS) == set(scenelib.REPRESENTATIONS)


def test_cli_show_command(sample_qvf):
    from click.testing import CliRunner

    from vibeview.cli import main

    result = CliRunner().invoke(main, ["show", str(sample_qvf), "--size", "50x12", "--plain"])
    assert result.exit_code == 0, result.output
    assert result.output.strip()


def test_cli_show_info(sample_qvf):
    from click.testing import CliRunner

    from vibeview.cli import main

    result = CliRunner().invoke(main, ["show", str(sample_qvf), "--info", "--plain"])
    assert result.exit_code == 0, result.output
    assert "structure" in result.output


# ── panes ─────────────────────────────────────────────────────────────────


def test_overview_lists_every_section(sample_qvf):
    from vibeview.tui import panes

    text = panes.overview(QVFReader(sample_qvf))
    for section in QVFReader(sample_qvf).sections:
        assert section.id in text


def test_structure_table_reports_coordinates(sample_qvf):
    from vibeview.tui import panes

    text = panes.structure_table(QVFReader(sample_qvf))
    assert "0.11730" in text or "0.1173" in text


def test_panes_escape_markup_from_the_archive():
    """A residue name containing `[bold]` must not become markup."""
    from vibeview.tui.panes import _kv

    assert "\\[bold]" in _kv("key", "[bold]danger")


# ── dual-form kinds ───────────────────────────────────────────────────────


def test_dual_kinds_are_reachable_as_charts():
    """A reaction path renders as geometry *and* as an energy profile.

    Both kinds sit in GEOMETRIC_KINDS, so the 3D dispatch claims them first
    and their plot builders would be dead code without an explicit opt-in.
    """
    assert set(plots.PLOT_BUILDERS) >= plots.DUAL_KINDS
    assert scenelib.GEOMETRIC_KINDS >= plots.DUAL_KINDS


def test_trajectory_energy_plot_survives_a_missing_energy_list(sample_qvf, monkeypatch):
    """No energies is a titled empty plot, never None — the dispatch calls
    .render() on whatever comes back."""

    class _Stub:
        energies = None

    reader = QVFReader(sample_qvf)
    monkeypatch.setattr(reader, "read_trajectory", lambda _sid: _Stub())
    plot = plots.trajectory_energy_plot(reader, "whatever", 40, 10)
    assert plot.render() is not None




# ── public SDK ────────────────────────────────────────────────────────────


def test_render_terminal_is_exported_alongside_the_capture_functions():
    """Terminal rendering is the text sibling of capture_* and belongs in
    the same public surface — a caller shouldn't have to reach into
    vibeview.tui to get it."""
    import vibeview

    assert "render_terminal" in vibeview.__all__
    assert callable(vibeview.render_terminal)


def test_render_terminal_renders_and_defaults_to_the_structure(sample_qvf):
    import vibeview

    text = vibeview.render_terminal(sample_qvf, size=(60, 16), plain=True)
    assert text.strip()
    assert len(text.splitlines()) == 16


def test_render_terminal_explains_a_non_graphical_section(citations_qvf):
    """Returns prose rather than raising, so a caller sweeping every section
    never has to pre-filter by kind."""
    import vibeview

    text = vibeview.render_terminal(citations_qvf, "cite", size=(50, 8), plain=True)
    assert "no graphical form" in text


def test_render_terminal_accepts_an_open_reader_without_closing_it(sample_qvf):
    """A caller-owned reader must survive the call — the api layer only
    closes readers it opened itself."""
    import vibeview

    reader = QVFReader(sample_qvf)
    vibeview.render_terminal(reader, size=(40, 10), plain=True)
    assert reader.read_structure().atoms, "reader was closed out from under the caller"
    reader.close()


# ── examples ──────────────────────────────────────────────────────────────


def test_terminal_mode_example_runs(sample_qvf, capsys):
    """The shipped example must actually run.

    It branches on what the archive contains (volumes, periodicity, charts,
    animation), so running it against the molecular fixture exercises every
    "this archive has none of those" path as well as the render paths.
    """
    import importlib.util

    example = Path(__file__).resolve().parents[1] / "examples" / "terminal_mode.py"
    assert example.exists(), "examples/terminal_mode.py is referenced by the docs"

    spec = importlib.util.spec_from_file_location("terminal_mode_example", example)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main([str(sample_qvf), "--plain"]) == 0
    out = capsys.readouterr().out
    assert "One-line render" in out
    assert "Archive overview" in out


def test_terminal_mode_example_reports_a_missing_file(tmp_path, capsys):
    import importlib.util

    example = Path(__file__).resolve().parents[1] / "examples" / "terminal_mode.py"
    spec = importlib.util.spec_from_file_location("terminal_mode_example", example)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main([str(tmp_path / "nope.qvf"), "--plain"]) == 1


def test_secondary_structure_colouring_survives_repeated_residue_numbers():
    """Residue numbers repeat, so the label match must key on the CA atom.

    PDB numbering is four columns wide and wraps at 9999, which is why
    QVFReader.chains() groups by contiguous run rather than by number. A
    lookup keyed on residue_seq would merge two unrelated residues that
    happen to share one, and mis-colour whichever came second.
    """

    class _Structure:
        """Two distinct residues in one chain, both numbered 1."""

        atoms = [
            type("A", (), {"atomic_number": 6, "atom_name": "CA"})(),
            type("A", (), {"atomic_number": 6, "atom_name": "CB"})(),
            type("A", (), {"atomic_number": 7, "atom_name": "CA"})(),
            type("A", (), {"atomic_number": 7, "atom_name": "CB"})(),
        ]

        def chains(self):
            return {"A": [(1, [0, 1]), (1, [2, 3])]}

        def chain_ids(self):
            return ["A"]

        def ca_residues(self, chain_id=None):
            return [("A", 1, 0), ("A", 1, 2)]

        def secondary_structure(self, chain_id=None):
            return ["H", "E"]

    colors = scenelib._atom_colors(_Structure.atoms, "secondary", _Structure())
    helix, sheet = tuple(colors[0]), tuple(colors[2])
    assert helix != sheet, "both residues took the same label"
    assert tuple(colors[1]) == helix, "residue 1's second atom lost its label"
    assert tuple(colors[3]) == sheet, "residue 2's second atom lost its label"


# ── frontier orbitals ─────────────────────────────────────────────────────


def _mo_table(
    energies,
    occupations,
    orbital_kind="canonical",
    n_electrons=None,
    occupation_semantics=None,
):
    """Render the MO listing for a stub restricted wavefunction."""
    from vibeview.tui import panes

    class _Data:
        spin = "restricted"
        n_ao = len(energies)
        shells = []
        pure = True
        symmetry_labels = None
        alpha_energies = alpha_occupations = None
        beta_energies = beta_occupations = symmetry_labels_beta = None

    # Set after the class body: a class block does not close over the
    # enclosing function's locals, so referencing `orbital_kind` inside it
    # raises NameError.
    _Data.orbital_kind = orbital_kind
    _Data.occupation_semantics = occupation_semantics
    _Data.energies = np.asarray(energies, dtype=float)
    _Data.occupations = np.asarray(occupations, dtype=float)

    class _Reader:
        provenance = {"n_electrons": n_electrons} if n_electrons is not None else {}

        def read_wavefunction_gto(self, _sid):
            return _Data()

    return panes.wavefunction(_Reader(), "wf")


def _marker_rows(text):
    """Table rows only. The 'HOMO-LUMO gap' caption contains both words, so
    counting markers over the whole pane double-counts every block."""
    import re

    plain = re.sub(r"\[/?[^]]*\]", "", text)
    return [ln for ln in plain.splitlines() if re.match(r"^\d+\s", ln.strip())]


def test_only_the_highest_occupied_orbital_is_labelled_homo():
    """Every occupied orbital was labelled HOMO.

    The marker was chosen inside the loop that was still advancing the HOMO
    index, so at row i the running index always equalled i. Only the last
    occupied orbital is the highest occupied one.
    """
    rows = _marker_rows(
        _mo_table([-20.6, -11.3, -1.4, -0.87, -0.44, 0.135, 0.24], [2, 2, 2, 2, 2, 0, 0])
    )
    assert sum("HOMO" in r for r in rows) == 1, "exactly one orbital is the HOMO"
    assert sum("LUMO" in r for r in rows) == 1, "exactly one orbital is the LUMO"

    # ...and it is the *last* occupied row, not the first.
    assert "-0.440000" in next(r for r in rows if "HOMO" in r)
    assert "+0.135000" in next(r for r in rows if "LUMO" in r)


def test_frontier_orbitals_survive_a_hole_in_the_occupations():
    """The LUMO is the lowest virtual *above* the HOMO.

    Taking the first zero-occupancy row instead would place the LUMO below
    the HOMO for a non-aufbau occupation and invert the reported gap.
    """
    text = _mo_table([-1.0, -0.8, -0.6, -0.4, 0.2], [2, 2, 0, 2, 0])
    rows = _marker_rows(text)
    assert sum("HOMO" in r for r in rows) == 1
    assert sum("LUMO" in r for r in rows) == 1
    assert "-0.400000" in next(r for r in rows if "HOMO" in r)
    assert "+0.200000" in next(r for r in rows if "LUMO" in r)
    # The gap must therefore be positive.
    import re as _re

    gap = next(ln for ln in _re.sub(r"\[/?[^]]*\]", "", text).splitlines() if "gap" in ln)
    assert "-" not in gap.split("gap")[1], gap


def test_an_all_virtual_block_labels_no_homo():
    rows = _marker_rows(_mo_table([0.1, 0.2, 0.3], [0, 0, 0]))
    assert not any("HOMO" in r for r in rows)
    assert sum("LUMO" in r for r in rows) == 1


def test_natural_orbitals_get_a_hono_not_a_homo():
    """Natural orbitals do have a highest occupied one, under its own name.

    Pulay and Hamilton, J. Chem. Phys. 88, 4926 (1988), classify natural
    orbitals by occupancy into doubly occupied, fractionally occupied and
    virtual, so a frontier is meaningful. It is not a HOMO though: a natural
    orbital's eigenvalue is an occupation number, not an orbital energy, and
    the Koopmans reading that "HOMO" carries does not apply.
    """
    text = _mo_table(
        [-1.0, -0.8, -0.4, 0.1, 0.3],
        [2.0, 1.96, 1.02, 0.98, 0.04],
        orbital_kind="natural",
        n_electrons=6,
        occupation_semantics="electron_occupation",
    )
    rows = _marker_rows(text)
    assert sum("HONO" in r for r in rows) == 1
    assert sum("LUNO" in r for r in rows) == 1
    assert not any("HOMO" in r or "LUMO" in r for r in rows)
    # No eV gap: subtracting two occupation numbers is not an energy.
    assert "gap" not in text
    assert "occupancy" in text


def test_natural_orbital_pane_reports_the_active_space_window():
    """The count of genuinely fractional orbitals, with the cut named.

    Pulay's 0.02-1.98 window is called "somewhat arbitrary" in the paper
    itself, so it is shown to the reader rather than used to decide silently.
    """
    text = _mo_table(
        [-1.0, -0.8, -0.4, 0.1],
        [2.0, 1.96, 1.02, 0.001],
        orbital_kind="natural",
        n_electrons=5,
        occupation_semantics="electron_occupation",
    )
    assert "Pulay 1988" in text
    assert "2 of 4" in text, "1.96 and 1.02 are inside the window; 2.0 and 0.001 are not"


def test_smeared_canonical_occupations_get_no_frontier_labels():
    """Fractional occupations on *canonical* orbitals mean an ensemble.

    There the frontier is a Fermi level, not an orbital index, so naming one
    would be a category error.
    """
    text = _mo_table([-1.0, -0.8, -0.4, 0.1, 0.3], [2.0, 2.0, 1.3, 0.7, 0.0])
    rows = _marker_rows(text)
    assert not any("HOMO" in r or "LUMO" in r for r in rows)
    assert "Fermi level" in text
    assert "gap" not in text


def test_localized_orbitals_get_no_frontier_labels():
    """Even with clean integer occupations, "highest" needs an ordering."""
    text = _mo_table([-1.0, -0.8, -0.4, 0.1, 0.3], [2, 2, 2, 0, 0], orbital_kind="localized")
    rows = _marker_rows(text)
    assert not any("HOMO" in r or "LUMO" in r for r in rows)
    assert "not energy ordered" in text


def test_occupancy_is_read_as_an_integer_not_a_margin():
    """Occupied is a yes/no, resolved by nearest integer.

    Float round-trip through the archive's JSON can land 2.0 on 1.9999999,
    which is occupied; a genuine 0.4 is not. A noise-floor test would call
    both occupied.
    """
    rows = _marker_rows(_mo_table([-1.0, -0.5, 0.2], [2.0, 1.9999999, 0.0000001]))
    assert sum("HOMO" in r for r in rows) == 1
    assert "-0.500000" in next(r for r in rows if "HOMO" in r)
    assert "+0.200000" in next(r for r in rows if "LUMO" in r)


def test_transition_weights_are_not_read_as_occupancies():
    """`orbital_kind: "natural"` covers NTOs too, and they are not occupancies.

    Ground-state natural orbitals of the 1-RDM and natural *transition*
    orbitals both carry that value, but an NTO's numbers are transition
    weights summing to ~1 regardless of how many electrons the molecule has.
    Rounding the leading one to 1 and calling it the HONO is precisely the
    category error the frontier logic exists to prevent, so the claim is made
    only when the occupations account for the system's electrons.
    """
    # Numbers taken from a real TDDFT archive's wf_nto_S1_hole section.
    text = _mo_table(
        [0.0, 0.0, 0.0, 0.0],
        [1.0, 0.0043, 2.7e-16, 1.7e-16],
        orbital_kind="natural",
        n_electrons=16,
        occupation_semantics="transition_weight",
    )
    rows = _marker_rows(text)
    assert not any("HONO" in r or "LUNO" in r for r in rows)
    assert "transition weights" in text
    # Pulay's window classifies occupancies; counting weights inside it would
    # repeat the same error one line down.
    assert "Pulay" not in text


def test_no_frontier_claimed_without_an_electron_count():
    """An unmarked legacy natural section stays ambiguous regardless of sum."""
    text = _mo_table([-1.0, -0.5, 0.2], [1.98, 1.90, 0.12], orbital_kind="natural")
    assert not any("HONO" in r for r in _marker_rows(text))
    assert "does not declare" in text


# ── the committed natural-orbital fixture ─────────────────────────────────


_NATURAL_QVF = Path(__file__).resolve().parent / "data" / "h2_natural_orbitals.qvf"


def test_hono_path_against_computed_natural_orbitals():
    """The HONO path, pinned against a real archive rather than a stub.

    The fixture is a genuine CASSCF(2e,2o)/6-31G run on H2 at 2.4 A,
    regenerated by examples/vibe_view/showcase_natural_orbitals.py. Its
    occupations (1.8854 / 0.1146, summing to the 2 electrons) are eigenvalues
    of the converged density matrix, not numbers written by this test.

    It exists because the ground-state branch otherwise has no real data
    behind it: every "natural" section found in the wild is a natural
    *transition* orbital, whose weights mean something else entirely.
    """
    from vibeview.tui import panes

    assert _NATURAL_QVF.exists(), "committed fixture is missing"
    reader = QVFReader(_NATURAL_QVF)
    from dataclasses import replace

    reader.set_wavefunction_overlay(
        "wf",
        replace(
            reader.read_wavefunction_gto("wf"),
            occupation_semantics="electron_occupation",
        ),
    )
    text = panes.wavefunction(reader, "wf")
    rows = _marker_rows(text)

    assert sum("HONO" in r for r in rows) == 1
    assert sum("LUNO" in r for r in rows) == 1
    assert not any("HOMO" in r or "LUMO" in r for r in rows)
    # Frontier occupancies, not an eV gap.
    assert "1.8854 / 0.1146" in text
    assert "2 of 4" in text and "Pulay" in text
    # A diagonalizer's numerical zero must not print as "-0.000".
    assert "-0.000" not in text


def test_committed_natural_orbital_fixture_is_self_consistent():
    """Its occupations must sum to the electron count it declares.

    The binary fixture predates explicit occupation semantics. Its physical
    sum remains useful data, but the viewer must not use the sum alone to
    distinguish occupations from transition weights.
    """
    reader = QVFReader(_NATURAL_QVF)
    data = reader.read_wavefunction_gto("wf")
    assert data.orbital_kind == "natural"
    assert data.occupation_semantics is None
    total = float(np.asarray(data.occupations).sum())
    assert total == pytest.approx(reader.provenance["n_electrons"], abs=1e-6)


def test_natural_orbital_surface_rows_use_occupations_not_fake_energies():
    """The QVF zero energy vector is a placeholder, not an NO spectrum."""
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    reader = QVFReader(_NATURAL_QVF)
    from dataclasses import replace

    reader.set_wavefunction_overlay(
        "wf",
        replace(
            reader.read_wavefunction_gto("wf"),
            occupation_semantics="electron_occupation",
        ),
    )
    rows = WavefunctionRenderer(reader.get_section("wf"), reader).mo_table()

    assert all(row["energy_eh"] is None for row in rows)
    assert all("Eh" not in row["title"] for row in rows)
    assert sum("HONO" in row["title"] for row in rows) == 1
    assert sum("LUNO" in row["title"] for row in rows) == 1


def test_fractional_canonical_surface_rows_do_not_invent_a_homo():
    """A smeared canonical set has a Fermi level, not a frontier orbital."""
    from dataclasses import replace

    from vibeview.renderers.wavefunction import WavefunctionRenderer

    reader = QVFReader(_NATURAL_QVF)
    source = reader.read_wavefunction_gto("wf")
    occupations = np.asarray(source.occupations, dtype=float).copy()
    occupations[:2] = (1.6, 0.4)
    reader.set_wavefunction_overlay(
        "wf",
        replace(source, orbital_kind="canonical", occupations=occupations),
    )

    rows = WavefunctionRenderer(reader.get_section("wf"), reader).mo_table()
    assert not any("HOMO" in row["title"] or "LUMO" in row["title"] for row in rows)


# ── biomolecular colouring and the backbone trace ─────────────────────────


class _Bio:
    """Two chains of alpha carbons, far apart, with known secondary structure."""

    has_residues = True

    def chain_ids(self):
        return ["A", "B"]

    def backbone_trace(self, chain_id=None):
        if chain_id == "A":
            return np.array([[0.0, 0, 0], [3.8, 0, 0], [7.6, 0, 0]])
        if chain_id == "B":
            # 100 A away: a bond drawn to this chain would be unmistakable.
            return np.array([[100.0, 0, 0], [103.8, 0, 0]])
        return np.vstack([self.backbone_trace("A"), self.backbone_trace("B")])

    def secondary_structure(self, chain_id=None):
        return {"A": ["H", "H", "E"], "B": ["C", "C"]}.get(chain_id, [])


def test_backbone_never_bonds_one_chain_to_the_next():
    """Consecutive trace points are joined, so chains must be traced apart.

    A single trace over a flat CA list would draw a stick from one chain's
    C-terminus to the next chain's N-terminus, inventing a covalent bond
    across a gap that can be the width of the box.
    """
    scene = scenelib._backbone_scene(_Bio(), "secondary")
    assert scene is not None
    assert len(scene.positions) == 5, "three CAs in A, two in B"
    assert len(scene.bond_starts) == 3, "2 bonds inside A, 1 inside B, none bridging"
    lengths = np.linalg.norm(scene.bond_ends - scene.bond_starts, axis=1)
    assert lengths.max() < 10.0, f"a chain-bridging bond was drawn: {lengths}"


def test_backbone_colours_by_secondary_structure():
    scene = scenelib._backbone_scene(_Bio(), "secondary")
    helix, sheet, coil = tuple(scene.colors[0]), tuple(scene.colors[2]), tuple(scene.colors[3])
    assert helix != sheet and sheet != coil
    assert helix == tuple(float(c) for c in scenelib._SS_COLORS["H"])


def test_backbone_declines_when_there_is_nothing_to_trace(sample_qvf):
    """A plain molecule has no alpha carbons; say so rather than show nothing."""
    reader = QVFReader(sample_qvf)
    scene = scenelib.structure_scene(reader, representation="backbone")
    assert len(scene.positions) == 3, "should fall back to all atoms"
    assert any("no backbone" in note for note in scene.color_notes)


def test_constant_b_factors_fall_back_instead_of_one_flat_colour():
    """A column of identical b-factors carries no information.

    Many files write a constant 0.00 rather than omitting the field.
    Scaling it anyway paints every atom the colour at one end of the ramp,
    which says nothing and reads as a rendering fault. Found on a real
    167k-atom protein whose b-factors were all zero.
    """
    atoms = [
        type("A", (), {"atomic_number": z, "b_factor": 0.0, "atom_name": None})()
        for z in (6, 7, 8)
    ]
    notes: list[str] = []
    colors = scenelib._atom_colors(atoms, "bfactor", None, notes)
    assert len({tuple(c) for c in colors}) > 1, "one flat colour for the whole system"
    assert any("b-factor" in n for n in notes), "the substitution must be reported"


def test_varying_b_factors_still_produce_a_ramp():
    atoms = [
        type("A", (), {"atomic_number": 6, "b_factor": b, "atom_name": None})()
        for b in (10.0, 50.0, 90.0)
    ]
    notes: list[str] = []
    colors = scenelib._atom_colors(atoms, "bfactor", None, notes)
    assert notes == []
    assert colors[0][2] > colors[2][2], "low b-factor at the blue end"
    assert colors[2][0] > colors[0][0], "high b-factor at the red end"
