"""Multi-QVF dashboard — render a grid of structure previews.

Useful for screening calculations, comparing conformers, or
visualizing a reaction coordinate at a glance.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import numpy as np

# Confidence levels whose names are safe to put on a thumbnail. "low" is the
# namer's own signal that it could not assemble a well-formed name — it is what
# thymine returns ("1-amino,1-methyl methanamino-"). "medium" is not a hedge:
# the compositional inorganic rules report it for names as solid as "sodium
# chloride", so gating on "high" alone would silently drop every mineral.
_TRUSTED_CONFIDENCE = frozenset({"high", "medium"})


def _iupac_label(structure: Any) -> str | None:
    """IUPAC name for an already-read structure, or None if not trustworthy.

    Returns None when ``vibeqc_naming`` is unavailable, when naming fails, or
    when the namer reports low confidence. Callers fall back to the filename,
    so a None here loses no information.
    """
    try:
        from vibeqc_naming import name_from_atoms_detailed
    except ImportError:
        return None
    try:
        atoms = [
            (a.atomic_number, float(a.position[0]), float(a.position[1]), float(a.position[2]))
            for a in structure.atoms
        ]
        result = name_from_atoms_detailed(atoms)
    except Exception:  # noqa: BLE001 — naming is decorative; never fail a render
        return None
    if not result.name or result.confidence.value not in _TRUSTED_CONFIDENCE:
        return None
    return result.name


def render_dashboard(
    qvf_paths: list[str],
    output_path: str,
    *,
    cols: int = 3,
    cell_size: int = 300,
    style: str = "ball_and_stick",
    labels: bool = True,
) -> str:
    """Render a grid of structure previews from multiple QVF files.

    Produces a single PNG image with a grid layout of structure
    thumbnails, suitable for quick visual screening.

    Parameters
    ----------
    qvf_paths : list of str
        Paths to .qvf files.
    output_path : str
        Output PNG path.
    cols : int
        Number of columns in the grid.
    cell_size : int
        Width/height of each cell in pixels.
    style : str
        Rendering style for structures.
    labels : bool
        Whether to caption each thumbnail. The caption is the molecule's IUPAC
        name when one can be determined confidently, with the filename beneath
        it; otherwise the filename alone.

    Returns
    -------
    str
        Path to the generated image.
    """
    import pyvista as pv
    from PIL import Image, ImageDraw

    from vibeview.qvf import QVFReader
    from vibeview.renderers.structure import StructureRenderer

    n = len(qvf_paths)
    if n == 0:
        img = Image.new("RGB", (cell_size, cell_size), (240, 240, 240))
        img.save(output_path)
        return output_path

    rows = (n + cols - 1) // cols
    cell_w, cell_h = cell_size, cell_size
    # Each row reserves its own caption strip below the thumbnail. Sharing one
    # strip across all rows drew every row's captions at the same y, so only
    # the last row's text survived.
    label_h = 34 if labels else 0
    row_pitch = cell_h + label_h
    canvas_w = cols * cell_w
    canvas_h = rows * row_pitch

    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    for idx, qvf_path in enumerate(qvf_paths):
        row = idx // cols
        col = idx % cols

        # Render structure thumbnail
        try:
            reader = QVFReader(qvf_path)
            sec = next((s for s in reader.sections if s.kind == "structure"), None)
            if sec is None:
                reader.close()
                continue

            name = _iupac_label(reader.read_structure()) if labels else None

            plotter = pv.Plotter(off_screen=True, window_size=(cell_w, cell_h))
            try:
                renderer = StructureRenderer(sec, reader)
                renderer.add_to_plotter(plotter)
                plotter.view_isometric()
                plotter.render()
                thumb = plotter.screenshot(return_img=True)
            finally:
                plotter.close()
                reader.close()

            thumb_img = Image.fromarray(thumb).resize((cell_w - 4, cell_h - 4))

            # Paste into canvas with a border
            x = col * cell_w + 2
            y = row * row_pitch + 2
            # Draw border
            draw.rectangle(
                [x - 2, y - 2, x + cell_w - 2, y + cell_h - 2], outline=(200, 200, 200), width=1
            )
            canvas.paste(thumb_img, (x, y))

            # Caption: IUPAC name over filename, or filename alone.
            if labels:
                stem = Path(qvf_path).stem
                label_x = col * cell_w + 5
                label_y = row * row_pitch + cell_h + 4
                if name:
                    draw.text((label_x, label_y), name[:34], fill=(20, 20, 20))
                    draw.text((label_x, label_y + 14), stem[:34], fill=(130, 130, 130))
                else:
                    draw.text((label_x, label_y), stem[:34], fill=(50, 50, 50))

        except Exception as e:
            # Draw error placeholder
            x = col * cell_w
            y = row * row_pitch
            draw.rectangle(
                [x, y, x + cell_w, y + cell_h], fill=(255, 200, 200), outline=(200, 0, 0)
            )
            draw.text((x + 5, y + 5), f"Error: {e}"[:40], fill=(200, 0, 0))

    canvas.save(output_path)
    return output_path


def dashboard_to_html(
    qvf_paths: list[str],
    cols: int = 3,
    cell_size: int = 300,
) -> str:
    """Generate an HTML page with an interactive grid of 3D viewers.

    Each cell uses 3Dmol.js for a rotatable structure preview.

    Returns
    -------
    str
        Complete HTML document.
    """
    from html import escape

    from vibeview.qvf import QVFReader

    cells_html = []
    for idx, qvf_path in enumerate(qvf_paths):
        try:
            reader = QVFReader(qvf_path)
            sdata = reader.read_structure()
            stem = Path(qvf_path).stem
            name = _iupac_label(sdata)

            # The comment line is fixed rather than interpolated: it lands
            # inside a JS template literal, where a backtick or ${ in a
            # filename would break out of the string.
            xyz_lines = [str(len(sdata.atoms)), "structure"]
            for a in sdata.atoms:
                x, y, z = a.position
                xyz_lines.append(f"{a.symbol:<2} {x:10.6f} {y:10.6f} {z:10.6f}")
            xyz = "\n".join(xyz_lines)

            if name:
                label_html = (
                    f'<div class="name">{escape(name)}</div>'
                    f'<div class="file">{escape(stem)}</div>'
                )
            else:
                label_html = f'<div class="name">{escape(stem)}</div>'

            viewer_id = f"dv-{idx}"
            cells_html.append(f"""
            <div class="cell">
                <div id="{viewer_id}" style="width:{cell_size}px;height:{cell_size}px;"></div>
                <div class="label">{label_html}</div>
            </div>
            <script>
            (function() {{
                let v = $3Dmol.createViewer("{viewer_id}", {{backgroundColor:"white"}});
                v.addModel(`{xyz}`, "xyz");
                v.setStyle({{}}, {{stick:{{radius:0.15}}, sphere:{{scale:0.3}}}});
                v.zoomTo();
                v.render();
            }})();
            </script>
            """)
            reader.close()
        except Exception:
            cells_html.append(f'<div class="cell error">{escape(Path(qvf_path).name)}</div>')

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>vibe-view Dashboard</title>
<style>
body {{ font-family: sans-serif; margin: 20px; background: #f5f5f5; }}
.grid {{ display: flex; flex-wrap: wrap; gap: 16px; justify-content: center; }}
.cell {{ border: 1px solid #ddd; border-radius: 8px; overflow: hidden; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.label {{ padding: 8px; text-align: center; background: #fafafa; }}
.name {{ font-size: 13px; color: #222; }}
.file {{ font-size: 11px; color: #999; margin-top: 2px; }}
.error {{ padding: 20px; color: #c33; }}
h1 {{ text-align: center; }}
</style>
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
</head><body>
<h1>vibe-view Dashboard — {len(qvf_paths)} files</h1>
<div class="grid">
{"".join(cells_html)}
</div>
</body></html>"""

    return html
