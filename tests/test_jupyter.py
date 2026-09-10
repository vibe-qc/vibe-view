"""Test the Jupyter magic module (Phase E2)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest


class _ScriptCollector(HTMLParser):
    """Collect script elements as the browser-facing HTML tokenizer sees them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.attrs: list[dict[str, str | None]] = []
        self.bodies: list[list[str]] = []
        self._body: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.attrs.append(dict(attrs))
            self._body = []
            self.bodies.append(self._body)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._body = None

    def handle_data(self, data: str) -> None:
        if self._body is not None:
            self._body.append(data)


def _make_qvf(
    sections: list[dict],
    files: dict[str, bytes] | None = None,
    *,
    source: dict | None = None,
) -> Path:
    manifest = {
        "qvf_version": 1,
        "source": source or {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
        "sections": sections,
    }
    with tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as tmp:
        path = Path(tmp.name)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        if files:
            for p, d in files.items():
                zf.writestr(p, d)
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestJupyterMagic:
    def test_html_serializes_archive_text_outside_executable_context(self, tmp_path) -> None:
        """Archive-controlled XYZ text must remain data inside the script."""
        from vibeview.jupyter import qvf_to_html

        symbol = "</script><script>globalThis.vibeviewInjected=true</script>"
        structure = json.dumps(
            {
                "atoms": [
                    {
                        "symbol": symbol,
                        "position": [0, 0, 0],
                        "atomic_number": 1,
                    }
                ],
                "pbc": [False, False, False],
            }
        ).encode()
        sections = [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "sections/s.json",
                        "format": "json",
                        "sha256": _sha256(structure),
                    }
                },
            }
        ]
        source = _make_qvf(sections, {"sections/s.json": structure})
        path = tmp_path / "calc-${globalThis.vibeviewInjected=true}.qvf"
        source.replace(path)

        generated = qvf_to_html(str(path))

        assert "viewer.addModel(`" not in generated
        assert "</script><script>globalThis.vibeviewInjected" not in generated
        assert r"\u003c/script\u003e" in generated

        parsed = _ScriptCollector()
        parsed.feed(generated)
        assert parsed.attrs == [
            {"src": "https://3Dmol.org/build/3Dmol-min.js"},
            {},
        ]
        assert r"\u003c/script\u003e\u003cscript\u003e" in "".join(parsed.bodies[1])

        model_line = next(
            line.strip() for line in generated.splitlines() if "viewer.addModel(" in line
        )
        serialized = model_line.removeprefix("viewer.addModel(").removesuffix(', "xyz");')
        decoded_xyz = json.loads(serialized)
        assert path.name in decoded_xyz
        assert symbol in decoded_xyz

    def test_html_closes_reader_when_structure_read_fails(self, monkeypatch) -> None:
        """A malformed structure must not leave its archive open."""
        import vibeview.qvf as qvf
        from vibeview.jupyter import qvf_to_html

        class StructureReadError(RuntimeError):
            pass

        failure = StructureReadError("structure decode failed")

        class FailingReader:
            instances = []

            def __init__(self, _path) -> None:
                self.close_calls = 0
                self.instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                self.close()

            def read_structure(self):
                raise failure

            def close(self) -> None:
                self.close_calls += 1

        monkeypatch.setattr(qvf, "QVFReader", FailingReader)

        with pytest.raises(StructureReadError, match="structure decode failed") as raised:
            qvf_to_html("broken.qvf")

        assert raised.value is failure
        assert len(FailingReader.instances) == 1
        assert FailingReader.instances[0].close_calls == 1

    def test_sniff_qvf(self) -> None:
        """_sniff_qvf returns correct section list and source info."""
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.jupyter import _sniff_qvf

        structure = json.dumps(
            {
                "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
                "pbc": [False, False, False],
            }
        ).encode()
        secs = [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "sections/s.json",
                        "format": "json",
                        "sha256": _sha256(structure),
                    }
                },
            }
        ]
        path = _make_qvf(secs, {"sections/s.json": structure})
        try:
            info = _sniff_qvf(path)
            assert info["program"] == "vibe-qc"
            assert info["version"] == "0.9.0"
            assert info["calculation"] == "test"
            assert len(info["sections"]) == 1
            assert info["sections"][0]["kind"] == "structure"
        finally:
            path.unlink()

    def test_render_structure(self) -> None:
        """_render_structure returns non-empty PNG bytes."""
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.jupyter import _render_structure

        structure = json.dumps(
            {
                "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
                "pbc": [False, False, False],
            }
        ).encode()
        secs = [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "sections/s.json",
                        "format": "json",
                        "sha256": _sha256(structure),
                    }
                },
            }
        ]
        path = _make_qvf(secs, {"sections/s.json": structure})
        try:
            png = _render_structure(path)
            assert png is not None
            assert len(png) > 100
            assert png[:4] == b"\x89PNG"
        finally:
            path.unlink()

    def test_sniff_no_structure(self) -> None:
        """_sniff_qvf handles files without structure gracefully."""
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.jupyter import _sniff_qvf

        bonds = json.dumps({"pairs": []}).encode()
        secs = [
            {
                "id": "bonds",
                "kind": "bonds",
                "members": {
                    "bonds": {
                        "path": "sections/b.json",
                        "format": "json",
                        "sha256": _sha256(bonds),
                    }
                },
            }
        ]
        path = _make_qvf(secs, {"sections/b.json": bonds})
        try:
            info = _sniff_qvf(path)
            assert len(info["sections"]) == 1
            assert info["sections"][0]["kind"] == "bonds"
        finally:
            path.unlink()


class TestNotebookCellExport:
    def test_density_cell_is_valid_python(self) -> None:
        """The optional density block must remain a compilable code cell."""
        from vibeview.jupyter import export_notebook_cell

        code = export_notebook_cell(
            "/tmp/example.qvf",
            include_structure=False,
            include_density=True,
        )

        compile(code, "<vibe-view notebook cell>", "exec")

    def test_density_cell_executes_current_volume_api(self, sample_qvf, capsys) -> None:
        """Generated code consumes the summary dict returned by get_volume."""
        from vibeview.jupyter import export_notebook_cell

        code = export_notebook_cell(
            str(sample_qvf),
            include_structure=False,
            include_density=True,
        )
        namespace: dict[str, object] = {}

        try:
            exec(compile(code, "<vibe-view notebook cell>", "exec"), namespace)
        finally:
            reader = namespace.get("reader")
            if reader is not None:
                reader.close()

        output = capsys.readouterr().out
        assert "Density grid: [40, 40, 40], range:" in output

    def test_density_cell_handles_archive_without_density(self, capsys) -> None:
        """The optional block remains useful when no density was exported."""
        from vibeview.jupyter import export_notebook_cell

        path = _make_qvf([])
        namespace: dict[str, object] = {}
        try:
            code = export_notebook_cell(
                str(path),
                include_structure=False,
                include_density=True,
            )
            exec(compile(code, "<vibe-view notebook cell>", "exec"), namespace)
        finally:
            reader = namespace.get("reader")
            if reader is not None:
                reader.close()
            path.unlink()

        assert "No density volume found" in capsys.readouterr().out


class TestInlineImage:
    @pytest.mark.parametrize(
        "style",
        [
            "ball_and_stick",
            "space_filling",
            "sticks_only",
            "wireframe",
            "cartoon",
        ],
    )
    def test_style_reaches_structure_renderer(self, sample_qvf, monkeypatch, style) -> None:
        """qvf_to_image forwards its public style to the render plan."""
        from vibeview.jupyter import qvf_to_image
        from vibeview.renderers.structure import StructureRenderer

        original = StructureRenderer.add_to_plotter
        seen: list[str | None] = []

        def spy(renderer, plotter, *args, **kwargs):
            seen.append(kwargs.get("representation"))
            return original(renderer, plotter, *args, **kwargs)

        monkeypatch.setattr(StructureRenderer, "add_to_plotter", spy)

        png = qvf_to_image(str(sample_qvf), width=160, height=120, style=style)

        assert png.startswith(b"\x89PNG")
        assert seen == [style]

    def test_unknown_style_fails_explicitly(self, sample_qvf) -> None:
        """Unknown renderer styles must not silently produce an empty image."""
        from vibeview.jupyter import qvf_to_image

        with pytest.raises(ValueError, match="Unsupported style.*ball_and_stick"):
            qvf_to_image(str(sample_qvf), style="licorice")

    def test_plotter_construction_failure_closes_reader(self, monkeypatch) -> None:
        """A renderer setup failure must not leave its archive open."""
        import pyvista as pv

        import vibeview.qvf as qvf
        from vibeview.jupyter import qvf_to_image

        class PlotterConstructionError(RuntimeError):
            pass

        failure = PlotterConstructionError("plotter setup failed")

        class StructureSection:
            kind = "structure"

        class TrackingReader:
            instances = []

            def __init__(self, _path) -> None:
                self.sections = [StructureSection()]
                self.close_calls = 0
                self.instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                self.close()

            def close(self) -> None:
                self.close_calls += 1

        def fail_plotter(*_args, **_kwargs):
            raise failure

        monkeypatch.setattr(qvf, "QVFReader", TrackingReader)
        monkeypatch.setattr(pv, "Plotter", fail_plotter)

        with pytest.raises(PlotterConstructionError, match="plotter setup failed") as raised:
            qvf_to_image("structure.qvf")

        assert raised.value is failure
        assert len(TrackingReader.instances) == 1
        assert TrackingReader.instances[0].close_calls == 1

    def test_render_failure_closes_reader_and_plotter(self, monkeypatch) -> None:
        """Every successfully constructed plotter must close with its reader."""
        import pyvista as pv

        import vibeview.qvf as qvf
        import vibeview.renderers.structure as structure
        from vibeview.jupyter import qvf_to_image

        class RenderError(RuntimeError):
            pass

        failure = RenderError("ball_and_stick render failed")

        class StructureSection:
            kind = "structure"

        class TrackingReader:
            instances = []

            def __init__(self, _path) -> None:
                self.sections = [StructureSection()]
                self.close_calls = 0
                self.instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                self.close()

            def close(self) -> None:
                self.close_calls += 1

        class TrackingPlotter:
            instances = []

            def __init__(self, *_args, **_kwargs) -> None:
                self.close_calls = 0
                self.instances.append(self)

            def close(self) -> None:
                self.close_calls += 1

        class FailingRenderer:
            def __init__(self, _section, _reader) -> None:
                pass

            def add_to_plotter(self, _plotter, *, representation) -> None:
                assert representation == "ball_and_stick"
                raise failure

        monkeypatch.setattr(qvf, "QVFReader", TrackingReader)
        monkeypatch.setattr(pv, "Plotter", TrackingPlotter)
        monkeypatch.setattr(structure, "StructureRenderer", FailingRenderer)

        with pytest.raises(RenderError, match="ball_and_stick render failed") as raised:
            qvf_to_image("structure.qvf")

        assert raised.value is failure
        assert len(TrackingReader.instances) == 1
        assert TrackingReader.instances[0].close_calls == 1
        assert len(TrackingPlotter.instances) == 1
        assert TrackingPlotter.instances[0].close_calls == 1


class TestIPythonMagic:
    @staticmethod
    def _shell():
        from IPython.core.interactiveshell import InteractiveShell

        shell = InteractiveShell()
        shell.run_line_magic("load_ext", "vibeview.jupyter")
        return shell

    def test_load_ext_registers_line_magic(self) -> None:
        shell = self._shell()

        assert callable(shell.find_line_magic("vibeview"))
        assert callable(shell.find_cell_magic("vibeview"))

    def test_manual_registration_uses_same_line_magic(self) -> None:
        from IPython.core.interactiveshell import InteractiveShell

        from vibeview.jupyter import register_ipython_magic, vibeview_magic

        shell = InteractiveShell()

        assert register_ipython_magic(shell) is True
        assert shell.find_line_magic("vibeview") is vibeview_magic

    def test_default_view_displays_header_structure_and_sections(
        self, sample_qvf, monkeypatch
    ) -> None:
        import vibeview.jupyter as jupyter

        displayed = []
        monkeypatch.setattr(jupyter, "_render_structure", lambda _path: b"\x89PNG")
        monkeypatch.setattr("IPython.display.display", displayed.append)

        self._shell().run_line_magic("vibeview", str(sample_qvf))

        assert [type(item).__name__ for item in displayed] == [
            "HTML",
            "Image",
            "HTML",
        ]

    def test_default_view_handles_structureless_qvf(self, monkeypatch) -> None:
        path = _make_qvf([])
        displayed = []
        monkeypatch.setattr("IPython.display.display", displayed.append)
        try:
            self._shell().run_line_magic("vibeview", str(path))
        finally:
            path.unlink()

        assert [type(item).__name__ for item in displayed] == ["HTML", "HTML"]
        assert "Sections" in displayed[-1].data

    def test_default_view_treats_source_metadata_as_text(self, monkeypatch) -> None:
        path = _make_qvf(
            [],
            source={
                "program": "<img src=x onerror=alert(1)>",
                "version": "![bad](https://example.invalid/x)",
                "calculation": "<script>alert(1)</script>",
            },
        )
        displayed = []
        monkeypatch.setattr("IPython.display.display", displayed.append)
        try:
            self._shell().run_line_magic("vibeview", str(path))
        finally:
            path.unlink()

        header = displayed[0].data
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in header
        assert "&lt;img src=x onerror=alert(1)&gt;" in header
        assert "<script>" not in header
        assert "<img src=" not in header

    def test_density_capture_runs_current_capture_api(self, sample_qvf, monkeypatch) -> None:
        displayed = []
        monkeypatch.setattr("IPython.display.display", displayed.append)

        self._shell().run_line_magic("vibeview", f"{sample_qvf} --capture density")

        assert [type(item).__name__ for item in displayed] == ["HTML", "Image"]
        assert displayed[-1].data.startswith(b"\x89PNG")

    def test_table_option_runs_current_table_api(self, tables_qvf, monkeypatch) -> None:
        displayed = []
        monkeypatch.setattr("IPython.display.display", displayed.append)

        self._shell().run_line_magic(
            "vibeview",
            f"{tables_qvf} --table atom_properties",
        )

        assert [type(item).__name__ for item in displayed] == ["HTML", "HTML"]
        assert "mulliken" in displayed[-1].data

    def test_scf_option_runs_current_capture_api(self, water_qvf, monkeypatch) -> None:
        displayed = []
        monkeypatch.setattr("IPython.display.display", displayed.append)

        self._shell().run_line_magic("vibeview", f"{water_qvf} --scf")

        assert [type(item).__name__ for item in displayed] == ["HTML", "HTML"]
        assert "plotly" in displayed[-1].data.lower()

    def test_capture_uses_canonical_kind_defaults(self, sample_qvf, monkeypatch) -> None:
        from vibeview.jupyter import _capture_section

        with tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as destination:
            path = Path(destination.name)
        with zipfile.ZipFile(sample_qvf) as source_zip:
            manifest = json.loads(source_zip.read("manifest.json"))
            manifest["sections"][1]["kind"] = "volume.difference"
            manifest.pop("viewer_defaults", None)
            members = {
                name: source_zip.read(name)
                for name in source_zip.namelist()
                if name != "manifest.json"
            }
        with zipfile.ZipFile(path, "w") as output_zip:
            output_zip.writestr("manifest.json", json.dumps(manifest))
            for name, data in members.items():
                output_zip.writestr(name, data)

        captured = {}

        def fake_capture(reader, section_id, output, **kwargs):
            captured.update(section_id=section_id, **kwargs)
            Path(output).write_bytes(b"\x89PNG")
            return True

        monkeypatch.setattr("vibeview.capture.capture_volume", fake_capture)
        try:
            png = _capture_section(path, "volume.difference")
        finally:
            path.unlink()

        assert png == b"\x89PNG"
        assert captured == {
            "section_id": "density",
            "isovalue": 0.005,
            "colormap": "RdBu",
            "opacity": 0.6,
            "replication": (1, 1, 1),
        }

    @pytest.mark.parametrize(
        ("arguments", "helper_name", "helper_args", "helper_result"),
        [
            (
                "--table atom_properties",
                "_table_data",
                ("atom_properties",),
                (["atom"], [[1]]),
            ),
            ("--mo", "_table_data", ("wavefunction.gto",), (["mo"], [[1]])),
            ("--scf", "_scf_chart", (), "<div>SCF</div>"),
            ("--bands", "_bands_image", (), b"\x89PNG"),
            ("--capture density", "_capture_section", ("volume.density",), b"\x89PNG"),
        ],
    )
    def test_documented_option_dispatch(
        self,
        sample_qvf,
        monkeypatch,
        arguments,
        helper_name,
        helper_args,
        helper_result,
    ) -> None:
        import vibeview.jupyter as jupyter

        calls = []
        displayed = []

        def helper(path, *args):
            calls.append((Path(path), args))
            return helper_result

        monkeypatch.setattr(jupyter, helper_name, helper)
        monkeypatch.setattr("IPython.display.display", displayed.append)

        self._shell().run_line_magic(
            "vibeview",
            f"{sample_qvf} {arguments}",
        )

        assert calls == [(sample_qvf, helper_args)]
        assert len(displayed) == 2

    @pytest.mark.parametrize(
        ("line", "message"),
        [
            ("", "Usage: %vibeview"),
            ("--help", "--capture KIND"),
            ("{path} --table", "--table requires a value"),
            ("{path} --unknown", "Unknown option: --unknown"),
        ],
    )
    def test_invalid_input_reports_clear_error(self, sample_qvf, capsys, line, message) -> None:
        rendered_line = line.format(path=sample_qvf)

        self._shell().run_line_magic("vibeview", rendered_line)

        assert message in capsys.readouterr().out

    def test_missing_file_reports_clear_error(self, tmp_path, capsys) -> None:
        missing = tmp_path / "missing.qvf"

        self._shell().run_line_magic("vibeview", str(missing))

        assert f"File not found: {missing}" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("arguments", "helper_name", "message"),
        [
            ("--capture density", "_capture_section", "Capture error: broken"),
            ("--scf", "_scf_chart", "SCF chart error: broken"),
            ("--bands", "_bands_image", "Bands chart error: broken"),
            ("", "_render_structure", "Structure render error: broken"),
        ],
    )
    def test_renderer_failure_is_reported_without_traceback(
        self,
        sample_qvf,
        monkeypatch,
        capsys,
        arguments,
        helper_name,
        message,
    ) -> None:
        import vibeview.jupyter as jupyter

        def fail(*_args, **_kwargs):
            raise RuntimeError("broken")

        monkeypatch.setattr(jupyter, helper_name, fail)
        self._shell().run_line_magic("vibeview", f"{sample_qvf} {arguments}")

        assert message in capsys.readouterr().out
