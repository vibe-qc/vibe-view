"""renderers._html is the one document, stylesheet and escaper for panels."""

from __future__ import annotations

import base64

from vibeview.renderers import _html


def test_panel_document_carries_the_shared_stylesheet() -> None:
    doc = _html.panel_document("<p>x</p>", extra_css=".x{color:red}")
    assert doc.startswith("<!doctype html>")
    assert _html.PANEL_CSS in doc
    assert ".x{color:red}" in doc
    assert doc.endswith("<p>x</p></body></html>")


def test_cell_escapes_and_formats() -> None:
    assert _html.cell("<b>") == "&lt;b&gt;"
    assert _html.cell(1.23456) == "1.2346"
    assert _html.cell(1.23456, numeric_format=".1f") == "1.2"
    assert _html.cell(None) == _html.MISSING
    assert _html.cell([1.0, "a<"]) == "1.0000, a&lt;"
    assert _html.cell(True) == "yes"
    assert _html.cell(7) == "7"


def test_kv_table_and_block_values() -> None:
    table = _html.kv_table([("k<", "v"), ("multi", "a\nb")])
    assert "<table class='meta'>" in table
    assert "<td>k&lt;</td><td>v</td>" in table
    assert "<pre>a\nb</pre>" in table
    assert _html.kv_table([]) == ""
    assert "<pre>plain</pre>" in _html.kv_table([("k", "plain")], block_values=True)


def test_data_table_from_mappings_and_sequences() -> None:
    rows = [{"a": 1.0, "b": "x<"}, {"c": 3}, {"a": 2.5}]
    table = _html.data_table(["a", "b"], rows, caption="C<")
    assert "<caption>C&lt;</caption>" in table
    assert table.count("<tr>") == 3  # header + two rows; the row without a/b is skipped
    assert "<td>x&lt;</td>" in table
    assert f"<td>{_html.MISSING}</td>" in table
    assert _html.data_table(["a"], [{"z": 1}]) == ""
    seq = _html.data_table(["Atom", "q"], [[1, 0.5]], footer=["Total", 0.5])
    assert "<tfoot><tr><td>Total</td><td>0.5000</td></tr></tfoot>" in seq


def test_kv_strip_banner_empty_and_csv() -> None:
    strip = _html.kv_strip({"nuclei": "H, C", "ref": None, "n": 3})
    assert "<span>nuclei:</span> <strong>H, C</strong>" in strip
    assert "ref" not in strip
    assert _html.kv_strip({"a": None}) == ""
    assert _html.banner("x<y", "warn") == "<div class='banner warn'>x&lt;y</div>"
    empty = _html.empty_panel("Nothing <here>")
    assert "<p class='empty'>Nothing &lt;here&gt;</p>" in empty
    link = _html.csv_download_link("a,b\n1,2\n", 'x"y.csv')
    assert base64.b64encode(b"a,b\n1,2\n").decode() in link
    assert "download='x&quot;y.csv'" in link


def test_block_helpers() -> None:
    assert _html.heading("a<b") == "<h2>a&lt;b</h2>"
    assert _html.elide("x<") == "<div class='elide'>x&lt;</div>"
    assert _html.note("n<") == "<p class='note'>n&lt;</p>"
    assert _html.preformatted("<pre>") == "<pre>&lt;pre&gt;</pre>"
    block = _html.details_block("s<", "<b>ready</b>")
    assert block == "<details><summary>s&lt;</summary><b>ready</b></details>"


def test_every_table_panel_uses_the_shared_document() -> None:
    import inspect

    from vibeview.renderers import (
        atom_properties,
        bond_orders,
        citations,
        epr,
        job_spec,
        nmr,
        qtaim,
        run_record,
        symmetry,
    )

    for module in (
        atom_properties,
        bond_orders,
        citations,
        epr,
        job_spec,
        nmr,
        qtaim,
        run_record,
        symmetry,
    ):
        source = inspect.getsource(module)
        assert "<!doctype html>" not in source, module.__name__
        assert "border-collapse" not in source, module.__name__
        assert "panel_document(" in source, module.__name__
