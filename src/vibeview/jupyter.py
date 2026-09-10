"""Jupyter notebook integration for vibe-view (v2.1).

Provides IPython magic commands and helper functions for embedding
vibe-view visualizations in Jupyter notebooks.
"""

from __future__ import annotations

import html
import io
import json
import shlex
import tempfile
from pathlib import Path
from typing import Any

_SUPPORTED_STRUCTURE_STYLES = (
    "ball_and_stick",
    "space_filling",
    "sticks_only",
    "wireframe",
    "cartoon",
)

_MAGIC_USAGE = """Usage: %vibeview <file.qvf> [options]

Options:
  --capture KIND   Render density, orbital, spin, elf, or a volume.* kind
  --table KIND     Show atom_properties, wavefunction.gto, or vibrations
  --mo             Show the wavefunction.gto molecular-orbital table
  --scf            Show the SCF convergence chart
  --bands          Show the band-structure chart
  --help           Show this help
"""

_CAPTURE_KINDS = {
    "density": "volume.density",
    "orbital": "volume.orbital",
    "spin": "volume.spin",
    "elf": "volume.elf",
}


def qvf_to_html(qvf_path: str, width: int = 800, height: int = 600) -> str:
    """Generate an HTML snippet that embeds a 3D molecule viewer for a QVF file.

    Uses py3Dmol or an iframe-based approach for embedding in notebooks.

    Parameters
    ----------
    qvf_path : str
        Path to the .qvf file.
    width, height : int
        iframe dimensions.

    Returns
    -------
    str
        HTML string for IPython display.
    """
    from vibeview.qvf import QVFReader

    with QVFReader(qvf_path) as reader:
        sdata = reader.read_structure()

        # Build XYZ for py3Dmol or similar
        xyz_lines = [str(len(sdata.atoms)), f"vibe-view: {Path(qvf_path).name}"]
        for a in sdata.atoms:
            x, y, z = a.position
            xyz_lines.append(f"{a.symbol:<2} {x:10.6f} {y:10.6f} {z:10.6f}")
        xyz = "\n".join(xyz_lines)
        # JSON produces a quoted JavaScript string instead of a template literal,
        # so archive-controlled ``${...}`` text cannot execute. Escape the HTML
        # parser sentinels after serialization too: ``</script>`` terminates an
        # inline script before the JavaScript parser ever sees the quoted string.
        xyz_json = (
            json.dumps(xyz).replace("&", r"\u0026").replace("<", r"\u003c").replace(">", r"\u003e")
        )

        rendered_html = f"""<div style="width:{width}px; height:{height}px; position:relative;">
    <script src="https://3Dmol.org/build/3Dmol-min.js"></script>
    <div id="vibe-viewer-{hash(qvf_path) & 0xFFFF:04x}" style="width:100%;height:100%;"></div>
    <script>
    (function() {{
        let viewer = $3Dmol.createViewer("vibe-viewer-{hash(qvf_path) & 0xFFFF:04x}", {{
            backgroundColor: "white"
        }});
        viewer.addModel({xyz_json}, "xyz");
        viewer.setStyle({{}}, {{stick: {{radius: 0.15}}, sphere: {{scale: 0.3}}}});
        viewer.zoomTo();
        viewer.render();
    }})();
    </script></div>"""
    return rendered_html


def qvf_to_image(
    qvf_path: str, width: int = 800, height: int = 600, style: str = "ball_and_stick"
) -> bytes:
    """Render a structure as a PNG image for inline notebook display.

    Parameters
    ----------
    qvf_path : str
    width, height : int
    style : {"ball_and_stick", "space_filling", "sticks_only",
             "wireframe", "cartoon"}
        Structure representation passed to the renderer.

    Returns
    -------
    bytes
        PNG image data.

    Raises
    ------
    ValueError
        If *style* is not a supported structure representation.
    """
    if style not in _SUPPORTED_STRUCTURE_STYLES:
        choices = ", ".join(_SUPPORTED_STRUCTURE_STYLES)
        raise ValueError(f"Unsupported style {style!r}; expected one of: {choices}")

    import pyvista as pv

    from vibeview.qvf import QVFReader
    from vibeview.renderers.structure import StructureRenderer

    with QVFReader(qvf_path) as reader:
        sec = next((s for s in reader.sections if s.kind == "structure"), None)
        if sec is None:
            raise ValueError("No structure section in QVF")

        plotter = pv.Plotter(off_screen=True, window_size=[width, height])
        try:
            renderer = StructureRenderer(sec, reader)
            renderer.add_to_plotter(plotter, representation=style)
            plotter.view_isometric()
            plotter.render()
            img = plotter.screenshot(return_img=True)
        finally:
            plotter.close()

    buf = io.BytesIO()
    from PIL import Image as _Image

    _Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def export_notebook_cell(
    qvf_path: str,
    include_structure: bool = True,
    include_density: bool = False,
    include_orbitals: bool = False,
) -> str:
    """Generate a Jupyter notebook code cell that loads and visualizes a QVF.

    Returns Python code as a string that can be pasted into a notebook.
    """
    lines = [
        "# vibe-view notebook cell — auto-generated",
        "from vibeview import QVFReader, info, get_structure",
        f"reader = QVFReader({qvf_path!r})",
        "print('Sections:', [s.kind for s in reader.sections])",
    ]
    if include_structure:
        lines.extend(
            [
                "from vibeview.jupyter import qvf_to_html",
                "from IPython.display import HTML",
                f"display(HTML(qvf_to_html({qvf_path!r})))",
            ]
        )
    if include_density:
        lines.extend(
            [
                "from vibeview import get_volume",
                "density_section = next((s for s in reader.sections "
                "if s.kind == 'volume.density'), None)",
                "if density_section is None:",
                "    print('No density volume found')",
                "else:",
                "    density = get_volume(reader, density_section.id)",
                "    if density is None:",
                "        print(f'Density volume {density_section.id!r} is unavailable')",
                "    else:",
                "        shape = density['data_shape']",
                "        data_min = density['data_min']",
                "        data_max = density['data_max']",
                "        print(f'Density grid: {shape}, range: {data_min:.3f} to {data_max:.3f}')",
            ]
        )
    if include_orbitals:
        lines.extend(
            [
                "# Orbital data available in wavefunction.gto sections",
                "# See vibe-view tutorial section on orbital visualization",
            ]
        )
    return "\n".join(lines)


def _capture_section(path: Path, section_kind: str) -> bytes | None:
    """Render the first matching volume section into an in-memory PNG."""
    from vibeview.capture import capture_volume
    from vibeview.qvf import QVFReader
    from vibeview.viewer_defaults import ViewerState

    with QVFReader(path) as reader:
        section = next((s for s in reader.sections if s.kind == section_kind), None)
        if section is None:
            return None
        viewer_state = ViewerState.from_manifest(reader.viewer_defaults)
        hints = viewer_state.get_volume_hints(section.id, kind=section.kind)
        with tempfile.TemporaryDirectory(prefix="vibeview-jupyter-") as tmpdir:
            output = Path(tmpdir) / "capture.png"
            if not capture_volume(
                reader,
                section.id,
                output,
                isovalue=hints.isovalue,
                colormap=hints.colormap,
                opacity=hints.opacity,
                replication=viewer_state.replication,
            ):
                raise RuntimeError(f"could not render section {section.id!r}")
            return output.read_bytes()


def _table_data(path: Path, kind: str) -> tuple[list[str], list[list[Any]]]:
    """Extract a currently supported QVF table by section kind."""
    from vibeview.qvf import QVFReader
    from vibeview.tables import extract_table

    with QVFReader(path) as reader:
        return extract_table(reader, kind)


def _scf_chart(path: Path) -> str | None:
    """Return the first SCF-history chart as self-contained HTML."""
    from vibeview.capture import capture_scf_history
    from vibeview.qvf import QVFReader

    with QVFReader(path) as reader:
        if not any(s.kind == "scf_history" for s in reader.sections):
            return None
        with tempfile.TemporaryDirectory(prefix="vibeview-jupyter-") as tmpdir:
            output = Path(tmpdir) / "scf.html"
            if not capture_scf_history(reader, output):
                raise RuntimeError("could not render the scf_history section")
            return output.read_text()


def _bands_image(path: Path) -> bytes | None:
    """Return the first band-structure chart as PNG bytes."""
    from vibeview.capture import capture_bands
    from vibeview.qvf import QVFReader

    with QVFReader(path) as reader:
        if not any(s.kind == "bands" for s in reader.sections):
            return None
        with tempfile.TemporaryDirectory(prefix="vibeview-jupyter-") as tmpdir:
            output = Path(tmpdir) / "bands.png"
            if not capture_bands(reader, output):
                raise RuntimeError("could not render the bands section")
            return output.read_bytes()


def _table_display(columns: list[str], rows: list[list[Any]]) -> Any:
    """Build a bounded, escaped IPython HTML table without requiring pandas."""
    from IPython.display import HTML

    def cell(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    head = "".join(f"<th>{cell(column)}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(value)}</td>" for value in row) + "</tr>" for row in rows[:50]
    )
    if len(rows) > 50:
        body += f'<tr><td colspan="{max(len(columns), 1)}">{len(rows) - 50} more rows</td></tr>'
    return HTML(
        '<table class="dataframe" style="font-size:12px">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


def vibeview_magic(line: str) -> None:
    """Inspect and render a QVF inline through the documented ``%vibeview`` magic."""
    from IPython.display import HTML, Image, display

    try:
        parts = shlex.split(line)
    except ValueError as exc:
        print(f"Argument error: {exc}")
        return
    if not parts:
        print(_MAGIC_USAGE)
        return
    if parts == ["--help"]:
        print(_MAGIC_USAGE)
        return

    path = Path(parts[0]).expanduser()
    if not path.exists():
        print(f"File not found: {path}")
        return
    if path.suffix.lower() != ".qvf":
        print(f"Expected .qvf file, got: {path}")
        return

    capture_kind: str | None = None
    table_kind: str | None = None
    show_mo = False
    show_scf = False
    show_bands = False
    args = parts[1:]
    index = 0
    while index < len(args):
        option = args[index]
        if option in ("--capture", "--table"):
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                print(f"{option} requires a value")
                return
            value = args[index + 1]
            if option == "--capture":
                capture_kind = _CAPTURE_KINDS.get(
                    value,
                    value if value.startswith("volume.") else f"volume.{value}",
                )
            else:
                table_kind = value
            index += 2
        elif option == "--mo":
            show_mo = True
            index += 1
        elif option == "--scf":
            show_scf = True
            index += 1
        elif option == "--bands":
            show_bands = True
            index += 1
        elif option == "--help":
            print(_MAGIC_USAGE)
            return
        else:
            print(f"Unknown option: {option}")
            return

    try:
        summary = _sniff_qvf(path)
    except Exception as exc:
        print(f"Could not open QVF: {exc}")
        return

    calculation = html.escape(str(summary.get("calculation") or path.name))
    program = html.escape(str(summary.get("program") or "unknown"))
    version = html.escape(str(summary.get("version") or "unknown"))
    sections = summary.get("sections", [])
    display(
        HTML(
            f"<h2>{calculation}</h2>"
            f"<p><strong>{program} {version}</strong> &middot; "
            f"{len(sections)} sections</p>"
        )
    )

    if capture_kind:
        try:
            png = _capture_section(path, capture_kind)
        except Exception as exc:
            print(f"Capture error: {exc}")
            return
        if png is None:
            print(f"No {capture_kind} section found.")
        else:
            display(Image(png))
        return

    if table_kind or show_mo:
        kind = "wavefunction.gto" if show_mo else str(table_kind)
        try:
            columns, rows = _table_data(path, kind)
        except Exception as exc:
            label = "MO table" if show_mo else "Table"
            print(f"{label} error: {exc}")
        else:
            display(_table_display(columns, rows))
        return

    if show_scf:
        try:
            chart = _scf_chart(path)
        except Exception as exc:
            print(f"SCF chart error: {exc}")
            return
        if chart is None:
            print("No scf_history section found.")
        else:
            display(HTML(chart))
        return

    if show_bands:
        try:
            image = _bands_image(path)
        except Exception as exc:
            print(f"Bands chart error: {exc}")
            return
        if image is None:
            print("No bands section found.")
        else:
            display(Image(image))
        return

    if any(section.get("kind") == "structure" for section in sections):
        try:
            png = _render_structure(path)
        except Exception as exc:
            print(f"Structure render error: {exc}")
        else:
            if png:
                display(Image(png))

    section_table = _table_display(
        ["ID", "Kind"],
        [[section.get("id", ""), section.get("kind", "")] for section in sections],
    )
    display(HTML(f"<h3>Sections</h3>{section_table.data}"))

    provenance = []
    for label, key in (
        ("Method", "method"),
        ("Functional", "functional"),
        ("Basis", "basis"),
    ):
        if summary.get(key):
            provenance.append(f"{label}: {html.escape(str(summary[key]))}")
    if summary.get("scf_converged") is not None:
        status = "yes" if summary["scf_converged"] else "no"
        provenance.append(f"SCF converged: {status}")
    if summary.get("scf_energy_eh") is not None:
        try:
            energy = f"{float(summary['scf_energy_eh']):.8f}"
        except (TypeError, ValueError):
            energy = html.escape(str(summary["scf_energy_eh"]))
        provenance.append(f"Energy: {energy} Eh")
    if provenance:
        display(HTML("<p><em>" + "<br>".join(provenance) + "</em></p>"))


def _vibeview_cell_magic(line: str, cell: str) -> None:
    """Backward-compatible ``%%vibeview`` wrapper around the line magic."""
    vibeview_magic(line.strip() or cell.strip())


def load_ipython_extension(ipython: Any) -> None:
    """Register ``%vibeview`` when IPython loads ``vibeview.jupyter``."""
    ipython.register_magic_function(vibeview_magic, "line", "vibeview")
    ipython.register_magic_function(_vibeview_cell_magic, "cell", "vibeview")


def register_ipython_magic(ipython: Any | None = None) -> bool:
    """Register the line and cell magics manually when IPython is available."""
    try:
        from IPython import get_ipython
    except ImportError:
        return False

    shell = ipython if ipython is not None else get_ipython()
    if shell is None:
        return False
    load_ipython_extension(shell)
    return True


# ── Backward-compatible exports ────────────────────────────────────────


def _sniff_qvf(qvf_path):
    """Return a dict of {program, version, calculation, sections} for a QVF file.

    Compat wrapper used by test suite and legacy callers.
    """
    from vibeview.api import info

    return info(str(qvf_path))


def _render_structure(qvf_path):
    """Return PNG bytes for the structure in a QVF file.

    Compat wrapper used by test suite and legacy callers.
    """
    return qvf_to_image(str(qvf_path))
