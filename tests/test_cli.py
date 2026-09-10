"""Tests for the vibe-view CLI entry point."""

from __future__ import annotations

import ast
import hashlib
import json
import socket
import tempfile
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from vibeview.cli import _port_in_use, main

_CORPUS = qvf_corpus_dir()
_SLAB_2D_QVF = (
    _CORPUS / "structure_slab_2d.qvf" if _CORPUS else Path("/nonexistent.qvf")
)
_REQUIRES_CORPUS = requires_qvf_corpus("structure_slab_2d.qvf")


def _listening_socket() -> tuple[socket.socket, int]:
    """Bind+listen an ephemeral port; return the socket (keep it open) and port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def _structure_qvf(
    path: Path,
    *,
    pbc: list[bool] | None = None,
    lattice_vectors: list[list[float]] | None = None,
    provenance: dict[str, object] | None = None,
) -> None:
    """Write a minimal structure-only .qvf (water) to ``path``."""
    structure: dict[str, object] = {
        "atoms": [
            {"symbol": "O", "position": [0, 0, 0], "atomic_number": 8},
            {"symbol": "H", "position": [0.96, 0, 0], "atomic_number": 1},
            {"symbol": "H", "position": [-0.24, 0.93, 0], "atomic_number": 1},
        ]
    }
    if pbc is not None:
        structure["pbc"] = pbc
    if lattice_vectors is not None:
        structure["lattice_vectors"] = lattice_vectors
    body = json.dumps(structure).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.13", "calculation": "water"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "s.json",
                        "format": "json",
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                },
            }
        ],
    }
    if provenance is not None:
        manifest["provenance"] = provenance
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", body)


class TestCLIHelp:
    def test_main_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "vibe-view" in result.output
        assert "open" in result.output

    def test_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "vibe-view" in result.output

    def test_open_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["open", "--help"])
        assert result.exit_code == 0
        assert "INPUT_FILES" in result.output
        assert "--port" in result.output
        assert "--host" in result.output
        assert "--no-browser" in result.output

    def test_open_missing_file(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["open", "/nonexistent/file.qvf"])
        assert result.exit_code != 0
        assert "Error" in result.output or "does not exist" in result.output

    def test_open_wrong_extension(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"not a qvf")
            path = Path(f.name)
        try:
            runner = CliRunner()
            result = runner.invoke(main, ["open", str(path)])
            # Should fail because either extension check or no manifest.json
            assert result.exit_code != 0
        finally:
            path.unlink()

    @pytest.mark.parametrize(
        ("suffix", "content"),
        [
            (".py", "not a valid Python input )\n"),
            (".cube", "comment\ncomment\n0\n1 1 0 0\n1 0 1 0\n1 0 0 1\n"),
        ],
    )
    def test_open_malformed_loose_input_is_actionable(
        self,
        tmp_path: Path,
        suffix: str,
        content: str,
    ) -> None:
        source = tmp_path / f"broken{suffix}"
        source.write_text(content, encoding="utf-8")
        sock, port = _listening_socket()
        sock.close()

        result = CliRunner().invoke(
            main,
            ["open", str(source), "--port", str(port), "--no-browser"],
        )

        assert result.exit_code == 1
        assert f"Error opening {source}" in result.output
        assert result.exception is not None

    def test_open_closes_earlier_readers_when_later_input_fails(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from vibeview.qvf import QVFReader

        valid = tmp_path / "valid.qvf"
        broken = tmp_path / "broken.py"
        _structure_qvf(valid)
        broken.write_text("not a valid Python input )\n", encoding="utf-8")
        closed: list[QVFReader] = []
        original_close = QVFReader.close

        def record_close(reader: QVFReader) -> None:
            closed.append(reader)
            original_close(reader)

        monkeypatch.setattr(QVFReader, "close", record_close)
        sock, port = _listening_socket()
        sock.close()

        result = CliRunner().invoke(
            main,
            [
                "open",
                str(valid),
                str(broken),
                "--port",
                str(port),
                "--no-browser",
            ],
        )

        assert result.exit_code == 1
        assert len(closed) == 1


class TestPortCollision:
    """`vibe-view open` on an occupied port must fail loudly, not race the
    announce thread into opening the browser against a stale server."""

    def test_port_in_use_true_when_listening(self) -> None:
        s, port = _listening_socket()
        try:
            assert _port_in_use("127.0.0.1", port) is True
        finally:
            s.close()

    def test_port_in_use_false_when_free(self) -> None:
        s, port = _listening_socket()
        s.close()  # release it — now nothing is listening
        assert _port_in_use("127.0.0.1", port) is False

    def test_open_aborts_on_busy_port(self) -> None:
        s, port = _listening_socket()
        with tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as f:
            f.write(b"placeholder")  # never read: abort fires before QVFReader
            qvf = Path(f.name)
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as lf:
            logp = Path(lf.name)
        try:
            result = CliRunner().invoke(
                main,
                ["open", str(qvf), "--port", str(port), "--no-browser", "--log-file", str(logp)],
            )
            assert result.exit_code == 1
            assert "in use" in result.output
            # Must NOT have reached the misleading "ready"/startup announcement.
            assert "starting up" not in result.output
            assert "ready at" not in result.output
        finally:
            s.close()
            qvf.unlink(missing_ok=True)
            logp.unlink(missing_ok=True)


class TestBatch:
    """`vibe-view batch` renders a PNG gallery offscreen (Phase D3)."""

    def test_render_structure_png(self, tmp_path: Path) -> None:
        from vibeview.batch import render_structure_png
        from vibeview.qvf import QVFReader

        q = tmp_path / "a.qvf"
        _structure_qvf(q)
        png = tmp_path / "a.png"
        assert render_structure_png(QVFReader(q), png, size=(200, 150)) is True
        assert png.exists() and png.stat().st_size > 0

    def test_batch_cli_renders_gallery(self, tmp_path: Path) -> None:
        q1, q2 = tmp_path / "a.qvf", tmp_path / "b.qvf"
        _structure_qvf(q1)
        _structure_qvf(q2)
        out = tmp_path / "gallery"
        result = CliRunner().invoke(
            main, ["batch", str(q1), str(q2), "-o", str(out), "--size", "200x150"]
        )
        assert result.exit_code == 0, result.output
        assert (out / "a.png").exists()
        assert (out / "b.png").exists()

    def test_batch_no_match_fails(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["batch", str(tmp_path / "nope_*.qvf")])
        assert result.exit_code != 0
        assert "no .qvf files matched" in result.output

    def test_batch_bad_size_fails(self, tmp_path: Path) -> None:
        q = tmp_path / "a.qvf"
        _structure_qvf(q)
        result = CliRunner().invoke(main, ["batch", str(q), "--size", "nope"])
        assert result.exit_code != 0
        assert "invalid --size" in result.output


class TestTable:
    """`vibe-view table` dumps tabular data; `tables.extract_table` is the core
    (Phase E1). Uses the committed example files, skipping if absent."""

    # These used to point at gitignored example archives, so the whole
    # class skipped in CI and could only fail on a developer machine. The
    # synthesized `tables_qvf` fixture carries the same kinds (vibrations,
    # atom_properties with both charge flavours, wavefunction.gto) and four
    # atoms, so the table shapes below are unchanged.
    @pytest.fixture(autouse=True)
    def _archive(self, tables_qvf):
        self.H2CO = tables_qvf
        self.WATER = tables_qvf

    def _reader(self, path: Path):
        from vibeview.qvf import QVFReader

        return QVFReader(path)

    def test_extract_vibrations(self) -> None:
        from vibeview.tables import extract_table

        cols, rows = extract_table(self._reader(self.H2CO), "vibrations")
        assert cols == ["mode", "frequency_cm-1"]
        assert rows and len(rows[0]) == 2

    def test_extract_atom_properties(self) -> None:
        from vibeview.tables import extract_table

        cols, rows = extract_table(self._reader(self.H2CO), "atom_properties")
        assert "mulliken" in cols and "loewdin" in cols
        assert len(rows) == 4  # H2CO has 4 atoms

    def test_extract_mo_table(self) -> None:
        from vibeview.tables import extract_table

        cols, rows = extract_table(self._reader(self.WATER), "wavefunction.gto")
        assert "energy_eh" in cols and "occupation" in cols
        assert rows

    def test_extract_unsupported_kind_raises(self) -> None:
        from vibeview.tables import extract_table

        with pytest.raises(ValueError):
            extract_table(self._reader(self.WATER), "structure")

    def test_table_cli_csv(self) -> None:
        self._reader(self.H2CO)  # skip if absent
        result = CliRunner().invoke(
            main, ["table", str(self.H2CO), "--kind", "atom_properties", "--format", "csv"]
        )
        assert result.exit_code == 0, result.output
        assert "mulliken" in result.output and "," in result.output

    def test_table_cli_json(self) -> None:
        self._reader(self.H2CO)
        result = CliRunner().invoke(
            main, ["table", str(self.H2CO), "--kind", "vibrations", "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list) and "frequency_cm-1" in data[0]

    def test_table_cli_lists_kinds(self) -> None:
        self._reader(self.H2CO)
        result = CliRunner().invoke(main, ["table", str(self.H2CO)])
        assert result.exit_code == 0
        assert "Tabulatable sections" in result.output

    def test_table_cli_bad_kind_fails(self) -> None:
        self._reader(self.WATER)
        result = CliRunner().invoke(main, ["table", str(self.WATER), "--kind", "structure"])
        assert result.exit_code != 0


class TestInfo:
    def test_info_cli(self, tmp_path: Path) -> None:
        q = tmp_path / "a.qvf"
        _structure_qvf(q)
        result = CliRunner().invoke(main, ["info", str(q)])
        assert result.exit_code == 0
        assert "QVF version" in result.output
        assert "Sections" in result.output

    def test_info_json(self, tmp_path: Path) -> None:
        q = tmp_path / "a.qvf"
        _structure_qvf(q)
        result = CliRunner().invoke(main, ["info", str(q), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["program"] == "vibe-qc"
        assert data["qvf_version"] == 1
        assert isinstance(data["sections"], list)

    def test_info_missing_file(self) -> None:
        result = CliRunner().invoke(main, ["info", "/nonexistent/path.qvf"])
        assert result.exit_code != 0


class TestExport:
    def test_export_xyz(self, tmp_path: Path) -> None:
        q = tmp_path / "a.qvf"
        _structure_qvf(q)
        out = tmp_path / "out.xyz"
        result = CliRunner().invoke(main, ["export", str(q), "-f", "xyz", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text()
        assert "vibe-view export" in content

    def test_export_cif(self, tmp_path: Path) -> None:
        q = tmp_path / "a.qvf"
        _structure_qvf(q)
        out = tmp_path / "out.cif"
        result = CliRunner().invoke(main, ["export", str(q), "-f", "cif", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        assert "data_vibe-view" in out.read_text()

    def test_export_py_molecule_stays_molecular(self, tmp_path: Path) -> None:
        q = tmp_path / "water.qvf"
        _structure_qvf(q)
        out = tmp_path / "water.py"

        result = CliRunner().invoke(main, ["export", str(q), "-f", "py", "-o", str(out)])

        assert result.exit_code == 0, result.output
        script = out.read_text()
        assert "mol = Molecule([" in script
        assert "PeriodicSystem(" not in script
        compile(script, str(out), "exec")

    @_REQUIRES_CORPUS

    def test_export_py_slab_preserves_cell_and_dimension(self, tmp_path: Path) -> None:
        out = tmp_path / "slab.py"

        result = CliRunner().invoke(
            main, ["export", str(_SLAB_2D_QVF), "-f", "py", "-o", str(out)]
        )

        assert result.exit_code == 0, result.output
        script = out.read_text()
        tree = ast.parse(script)
        periodic_call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "PeriodicSystem"
        )
        assert ast.literal_eval(periodic_call.args[0]) == 2
        assert "cell = np.array([\n\n])" not in script
        assert "Molecule([" not in script

    def test_export_py_slab_preserves_charge_and_multiplicity(
        self, tmp_path: Path
    ) -> None:
        from vibeview.input_parser import parse_input_source

        q = tmp_path / "charged-slab.qvf"
        _structure_qvf(
            q,
            pbc=[True, True, False],
            lattice_vectors=[
                [4.0, 0.0, 0.0],
                [0.0, 4.0, 0.0],
                [0.0, 0.0, 15.0],
            ],
            provenance={
                "basis": "sto-3g",
                "method": "uks",
                "functional": "pbe",
                "charge": -1,
                "multiplicity": 2,
            },
        )
        out = tmp_path / "charged-slab.py"

        result = CliRunner().invoke(
            main, ["export", str(q), "-f", "py", "-o", str(out)]
        )

        assert result.exit_code == 0, result.output
        parsed = parse_input_source(out.read_text())
        assert parsed.is_periodic
        assert parsed.dimensionality == 2
        assert parsed.method == "uks"
        assert parsed.charge == -1
        assert parsed.multiplicity == 2

    def test_export_py_rejects_non_prefix_pbc_clearly(self, tmp_path: Path) -> None:
        q = tmp_path / "xz-periodic.qvf"
        _structure_qvf(
            q,
            pbc=[True, False, True],
            lattice_vectors=[
                [5.0, 0.0, 0.0],
                [0.0, 20.0, 0.0],
                [0.0, 0.0, 5.0],
            ],
        )
        out = tmp_path / "xz-periodic.py"

        result = CliRunner().invoke(main, ["export", str(q), "-f", "py", "-o", str(out)])

        assert result.exit_code != 0
        assert "Cannot export vibe-qc input" in result.output
        assert "pbc" in result.output.lower()
        assert not out.exists()


class TestDiff:
    def test_diff_cli(self, tmp_path: Path) -> None:
        qa = tmp_path / "a.qvf"
        qb = tmp_path / "b.qvf"
        _structure_qvf(qa)
        _structure_qvf(qb)
        result = CliRunner().invoke(main, ["diff", str(qa), str(qb)])
        assert result.exit_code == 0
        assert "File A" in result.output

    def test_diff_json(self, tmp_path: Path) -> None:
        qa = tmp_path / "a.qvf"
        qb = tmp_path / "b.qvf"
        _structure_qvf(qa)
        _structure_qvf(qb)
        result = CliRunner().invoke(main, ["diff", str(qa), str(qb), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "file_a" in data
        assert "kinds_common" in data


class TestValidate:
    def test_validate_ok(self, tmp_path: Path) -> None:
        q = tmp_path / "a.qvf"
        _structure_qvf(q)
        result = CliRunner().invoke(main, ["validate", str(q)])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_validate_multiple(self, tmp_path: Path) -> None:
        qa = tmp_path / "a.qvf"
        qb = tmp_path / "b.qvf"
        _structure_qvf(qa)
        _structure_qvf(qb)
        result = CliRunner().invoke(main, ["validate", str(qa), str(qb)])
        assert result.exit_code == 0
        assert "All 2 file(s) valid" in result.output

    def test_validate_missing(self) -> None:
        result = CliRunner().invoke(main, ["validate", "/nonexistent.qvf"])
        assert result.exit_code != 0


class TestStats:
    def test_stats_empty(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["stats", str(tmp_path)])
        assert result.exit_code == 0
        assert "No .qvf files" in result.output

    def test_stats_with_qvf(self, tmp_path: Path) -> None:
        q = tmp_path / "a.qvf"
        _structure_qvf(q)
        result = CliRunner().invoke(main, ["stats", str(tmp_path)])
        assert result.exit_code == 0
        assert "Files:" in result.output
        assert "Converged" in result.output


class TestCommandRegistry:
    """A duplicate ``@main.command("x")`` is invisible at runtime.

    Click keeps one entry per name and Python keeps one module-level
    function per name, so a second registration silently shadows the first
    — no warning, no error, just dead code. `capture-selftest` was
    registered twice for three weeks (the ~97-line healthcheck added in
    v0.15.23 was unreachable because an older definition further down the
    file won). Counting the decorators in the source and comparing against
    what click actually resolved is the cheapest way to see it.
    """

    @staticmethod
    def _decorated_names() -> tuple[int, list[str]]:
        import re

        from vibeview import cli as cli_mod

        source = Path(cli_mod.__file__).read_text()
        total = len(re.findall(r"^@main\.command\(", source, flags=re.MULTILINE))
        names = re.findall(r"^@main\.command\(\s*[\"']([^\"']+)[\"']", source, flags=re.MULTILINE)
        return total, names

    def test_no_shadowed_command_registration(self) -> None:
        import click

        total, names = self._decorated_names()
        with click.Context(main) as ctx:
            registered = main.list_commands(ctx)
        assert total == len(registered), (
            f"{total} @main.command decorators but {len(registered)} commands registered — "
            f"a duplicate registration is shadowing one. Decorated names not registered "
            f"or registered twice: {sorted({n for n in names if names.count(n) > 1})}"
        )
        assert sorted(names) == sorted(registered), (
            "decorated command names differ from the ones click resolved: "
            f"{sorted(set(names) ^ set(registered))}"
        )

    def test_no_duplicate_command_functions(self) -> None:
        """The function names collide too, so the module loses the first one."""
        import re

        from vibeview import cli as cli_mod

        source = Path(cli_mod.__file__).read_text()
        funcs = re.findall(r"^def (\w+)\(", source, flags=re.MULTILINE)
        dupes = sorted({f for f in funcs if funcs.count(f) > 1})
        assert not dupes, f"module-level functions defined more than once: {dupes}"
