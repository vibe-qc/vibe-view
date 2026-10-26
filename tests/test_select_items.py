"""Vuetify select ``items`` must reach the browser as a list.

trame reads a Python list attribute as ``(state_name, default)``: a bare
``items=["gif", "mp4"]`` renders ``:items="gif"`` (a state variable that
defaults to ``"mp4"``) and a list of dicts renders a dict literal. The
Move control of the measure-then-set flow (#124) rendered empty that way.
Static choices therefore use the ``("<name>_options", [...])`` form.
"""

from __future__ import annotations

import ast
from pathlib import Path

import vibeview

SRC = Path(vibeview.__file__).parent


def test_no_widget_passes_a_bare_list_as_items():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "items" and not isinstance(kw.value, (ast.Tuple, ast.Constant)):
                    offenders.append(f"{path.relative_to(SRC)}:{kw.value.lineno}")
    assert offenders == []


def test_geometry_side_select_renders_its_three_choices():
    from trame.app import get_server
    from trame.widgets import html

    from vibeview.app.controls import _build_controls

    server = get_server("select-items-test", client_type="vue3")
    with html.Div(trame_server=server) as root:
        _build_controls(server, server.controller)
    assert ':items="geometry_parameter_side_options"' in root.html
    assert [o["value"] for o in server.state.geometry_parameter_side_options] == [
        "smaller",
        "other",
        "last",
    ]
    assert server.state.edit_bond_order_options == [1, 1.5, 2, 3]
