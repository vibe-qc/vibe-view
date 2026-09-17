"""vibe-view — interactive viewer for QVF and quantum-chemistry data.

Reads .qvf archives (zip with JSON manifest + binary payloads) and
imports common chemistry formats for browser, desktop, and terminal display.

Programmatic entry points
-------------------------

``QVFReader`` accepts a filesystem path, raw zip bytes, or any seekable
binary file-like:

    >>> from vibeview import QVFReader, info, diff, validate
    >>> reader = QVFReader("h2o.qvf")
    >>> print(info(reader)["scf_energy_eh"])
    >>> print(diff("hf.qvf", "pbe.qvf"))
    >>> print(validate("result.qvf"))

``launch_qvf`` boots the interactive Trame server. (v1.5+ adds POV-Ray, Blender, crystal builder, and material presets modules.)
"""

from __future__ import annotations

__version__ = "2.18.0"  # Richardson's Robin -- see vibeview.codenames,
#                        which is the catalogue every surface reads. This
#                        comment is a convenience, and the drift test in
#                        tests/test_release_codenames.py keeps it honest.

from vibeview.api import (
    capture_structure,
    capture_volume,
    diff,
    export_xyz,
    get_structure,
    get_table,
    get_volume,
    has_section,
    info,
    render_terminal,
    sections,
    slice_qvf,
    validate,
)
from vibeview.launcher import launch_qvf
from vibeview.qvf import QVFOpenError, QVFReader, QVFSource

__all__ = [
    "QVFReader",
    "QVFSource",
    "QVFOpenError",
    "launch_qvf",
    "info",
    "sections",
    "has_section",
    "get_structure",
    "export_xyz",
    "diff",
    "get_volume",
    "get_table",
    "validate",
    "capture_structure",
    "capture_volume",
    "render_terminal",
    "slice_qvf",
    "__version__",
]
