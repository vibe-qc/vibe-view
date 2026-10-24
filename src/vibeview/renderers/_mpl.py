"""matplotlib for the renderers: one backend choice, one PNG encoder.

Five renderer modules each called ``matplotlib.use("Agg")`` at import time,
a global side effect repeated wherever any of them was imported, and each
wrote its own ``BytesIO`` / ``savefig`` / ``close`` block at a dpi of its
own. Rasterising a figure is done here, once, and the backend is chosen
once, lazily, the first time a figure is needed.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from matplotlib.figure import Figure

PNG_DPI = 120


def agg_pyplot() -> Any:
    """``matplotlib.pyplot`` on the headless Agg backend.

    The backend is selected on first use, not at import, so importing a
    renderer never reconfigures matplotlib for the whole process, and a
    notebook or GUI process that already chose a backend keeps it.
    """
    import matplotlib

    if matplotlib.get_backend().lower() != "agg":
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def figure_to_png(fig: Figure, *, dpi: int = PNG_DPI) -> bytes:
    """Rasterise ``fig`` to PNG bytes and release it."""
    buffer = io.BytesIO()
    try:
        fig.savefig(buffer, format="png", dpi=dpi)
    finally:
        agg_pyplot().close(fig)
    return buffer.getvalue()
