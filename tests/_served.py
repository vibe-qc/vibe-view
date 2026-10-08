"""The browser viewer's page as the client receives it, for DOM contracts.

Trame sends the layout as a Vue template (``trame__template_main``) and
each ``client.Script`` as a state value the template references with
``<trame-script :script="trame__inline_script_N" />``. :func:`served_document`
splices the scripts back into the template, so a test can assert on the
markup and script a browser actually gets rather than on the Python source
that builds them (#41-29). The served-DOM lane in ``test_design_refresh.py``
goes one step further and runs the page in Chrome.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

_SCRIPT_REF = re.compile(r'<trame-script :script="(trame__inline_script_\d+)" />')
_FIXTURE = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"


def served_document(server: Any) -> str:
    """The main template with every inline script inlined in place."""
    template = server.state["trame__template_main"]
    return _SCRIPT_REF.sub(
        lambda m: f"<script>{server.state[m.group(1)]}</script>", template
    )


@functools.lru_cache(maxsize=1)
def served_viewer() -> str:
    """The served document for a small molecular archive, built once.

    Built on a trame server of its own. ``create_app`` uses trame's default
    server, so building a page there mid-run would rebind the controllers a
    module- or class-scoped app fixture elsewhere is still driving, and that
    fixture's next call would act on this throwaway page's plotter.
    """
    import trame.app

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    default_get_server = trame.app.get_server

    def own_server(name: Any = None, *args: Any, **kwargs: Any) -> Any:
        return default_get_server(name or "vibeview-served-document", *args, **kwargs)

    reader = QVFReader(_FIXTURE)
    trame.app.get_server = own_server
    try:
        return served_document(create_app(reader))
    finally:
        trame.app.get_server = default_get_server
        reader.close()
