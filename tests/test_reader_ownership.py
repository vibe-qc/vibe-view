"""Owned readers close on every exit; borrowed readers remain usable."""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace

import pytest

from vibeview import api
from vibeview.launcher import launch_qvf
from vibeview.qvf import QVFError, QVFOpenError, QVFReader


@pytest.fixture
def observed_readers(monkeypatch):
    """Retain actual readers so garbage collection cannot hide a leaked handle."""
    seen = []
    original_init = QVFReader.__init__
    original_close = QVFReader.close

    def tracked_init(reader, source):
        original_init(reader, source)
        seen.append(reader)

    monkeypatch.setattr(QVFReader, "__init__", tracked_init)
    yield seen
    for reader in seen:
        original_close(reader)


@pytest.fixture(params=[False, True], ids=["owned", "borrowed"])
def source_case(request, water_qvf, observed_readers):
    borrowed = request.param
    return (QVFReader(water_qvf) if borrowed else water_qvf), borrowed


def assert_lifetime(reader, borrowed):
    if borrowed:
        assert reader._zf.read("manifest.json")
    else:
        assert reader._zf.fp is None
        with pytest.raises(ValueError, match="closed"):
            reader._zf.read("manifest.json")


@pytest.mark.parametrize("fails", [False, True], ids=["return", "raise"])
@pytest.mark.parametrize(
    "kind", ["path", "str", "bytes", "bytearray", "memoryview", "stream", "file", "reader"]
)
def test_closing_reader_source_types(water_qvf, kind, fails):
    data = water_qvf.read_bytes()
    source = {
        "path": lambda: water_qvf,
        "str": lambda: str(water_qvf),
        "bytes": lambda: data,
        "bytearray": lambda: bytearray(data),
        "memoryview": lambda: memoryview(data),
        "stream": lambda: io.BytesIO(data),
        "file": lambda: water_qvf.open("rb"),
        "reader": lambda: QVFReader(water_qvf),
    }[kind]()
    try:
        raised = False
        try:
            with api.closing_reader(source) as reader:
                assert reader.read_structure().atoms
                if kind == "reader":
                    assert reader is source
                if fails:
                    raise RuntimeError("work failed")
        except RuntimeError as exc:
            raised = True
            assert fails and str(exc) == "work failed"
        assert raised is fails
        assert_lifetime(reader, kind == "reader")
        if kind in {"stream", "file"}:
            assert not source.closed
            source.seek(0)
            assert source.read(2) == b"PK"
    finally:
        if kind in {"stream", "file", "reader"}:
            source.close()


def test_diff_second_open_failure(source_case, observed_readers):
    source, borrowed = source_case
    with pytest.raises(QVFOpenError):
        api.diff(source, b"not a zip")
    assert len(observed_readers) == 1
    assert_lifetime(observed_readers[0], borrowed)


@pytest.mark.parametrize("second_borrowed", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_diff_both_reader_lifetimes(
    source_case, observed_readers, water_qvf, monkeypatch, second_borrowed, fails
):
    source, borrowed = source_case
    second = QVFReader(water_qvf) if second_borrowed else water_qvf
    if fails:
        def fail(reader):
            raise RuntimeError("comparison failed")

        monkeypatch.setattr(QVFReader, "scf_energy_eh", fail)
        with pytest.raises(RuntimeError, match="comparison failed"):
            api.diff(source, second)
    else:
        assert api.diff(source, second)["geo_rmsd_a"] == pytest.approx(0.0)
    assert len(observed_readers) == 2
    first_reader = source if borrowed else next(r for r in observed_readers if r is not second)
    second_reader = second if second_borrowed else observed_readers[-1]
    assert_lifetime(first_reader, borrowed)
    assert_lifetime(second_reader, second_borrowed)


@pytest.mark.parametrize("operation", ["structure", "volume", "terminal"])
def test_lazy_import_failure_closes_only_owned(
    source_case, observed_readers, monkeypatch, tmp_path, operation
):
    source, borrowed = source_case
    module = "vibeview.tui.show" if operation == "terminal" else "vibeview.capture"
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(ImportError):
        if operation == "structure":
            api.capture_structure(source, tmp_path / "capture.png")
        elif operation == "volume":
            api.capture_volume(source, "density", tmp_path / "capture.png")
        else:
            api.render_terminal(source)
    assert len(observed_readers) == 1
    assert_lifetime(observed_readers[0], borrowed)


@pytest.mark.parametrize("operation", ["structure", "volume", "terminal", "table"])
@pytest.mark.parametrize("fails", [False, True])
def test_delegated_work_preserves_results_and_lifetimes(
    source_case, observed_readers, monkeypatch, tmp_path, operation, fails
):
    source, borrowed = source_case
    calls = []

    def work(reader, *args, **kwargs):
        assert reader._zf.read("manifest.json")
        calls.append((reader, args, kwargs))
        if fails:
            raise RuntimeError("render failed")
        if operation == "terminal":
            return SimpleNamespace(to_plain=lambda: "plain", to_ansi=lambda: "ansi")
        if operation == "table":
            return ["energy"], [[0.0]]
        return False

    monkeypatch.setitem(sys.modules, "vibeview.capture", SimpleNamespace(
        capture_structure=work, capture_volume=work,
    ))
    monkeypatch.setitem(sys.modules, "vibeview.tui.show", SimpleNamespace(
        default_section=lambda reader: "structure", render_section=work,
    ))
    monkeypatch.setitem(sys.modules, "vibeview.tables", SimpleNamespace(extract_table=work))

    def call():
        if operation == "structure":
            return api.capture_structure(source, tmp_path / "out.png", size=(32, 24))
        if operation == "volume":
            return api.capture_volume(source, "density", tmp_path / "out.png", isovalue=0.1)
        if operation == "terminal":
            return api.render_terminal(source, size=(32, 24), plain=True, mode="ascii")
        return api.get_table(source, "scf")

    if fails:
        with pytest.raises(RuntimeError, match="render failed"):
            call()
    else:
        expected = {"structure": False, "volume": False, "terminal": "plain",
                    "table": (["energy"], [[0.0]])}
        assert call() == expected[operation]
    assert len(calls) == 1
    expected_args = {
        "structure": ((tmp_path / "out.png",), {"size": (32, 24)}),
        "volume": (("density", tmp_path / "out.png"), {"isovalue": 0.1}),
        "terminal": (("structure", 32, 24), {"mode": "ascii"}),
        "table": (("scf",), {}),
    }
    assert calls[0][1:] == expected_args[operation]
    assert_lifetime(observed_readers[0], borrowed)


@pytest.mark.parametrize("operation", [
    "info", "sections", "has_section", "get_structure", "export_xyz", "get_volume", "validate",
])
def test_inspection_success(source_case, observed_readers, operation):
    source, borrowed = source_case
    if operation in {"has_section", "get_volume"}:
        result = getattr(api, operation)(source, "absent")
        assert result is (False if operation == "has_section" else None)
    else:
        assert getattr(api, operation)(source)
    assert len(observed_readers) == 1
    assert_lifetime(observed_readers[0], borrowed)


def test_validation_error_boundary(source_case, observed_readers, monkeypatch):
    source, borrowed = source_case

    def fail(*args):
        raise QVFError("member failure")

    monkeypatch.setattr(QVFReader, "_verify_and_read", fail)
    assert api.validate(source) == {"valid": False, "error": "member failure"}
    assert_lifetime(observed_readers[0], borrowed)
    with pytest.raises(QVFOpenError):
        api.validate(b"invalid")


@pytest.mark.parametrize("fails", [False, True])
def test_slice_ownership_and_error_boundary(
    source_case, observed_readers, monkeypatch, tmp_path, fails
):
    from vibeview import archive

    source, borrowed = source_case
    output = tmp_path / "slice.qvf"
    if fails:
        output.write_bytes(b"existing destination")

        def fail(*args):
            raise QVFError("invalid output")

        monkeypatch.setattr(archive, "write_archive", fail)
        with pytest.raises(ValueError, match="Invalid slice: invalid output") as exc:
            api.slice_qvf(source, output, keep=["structure"])
        assert isinstance(exc.value.__cause__, QVFError)
        assert output.read_bytes() == b"existing destination"
    else:
        assert api.slice_qvf(source, output, keep=["structure"]) == output
        with QVFReader(output) as sliced:
            assert sliced.read_structure().atoms
    assert_lifetime(observed_readers[0], borrowed)


@pytest.mark.parametrize("failure", [None, "banner", "create", "serve"])
def test_launcher_owned_and_borrowed(
    source_case, observed_readers, monkeypatch, failure
):
    source, borrowed = source_case
    calls = []

    def stage(name, value):
        calls.append(name)
        assert observed_readers[0]._zf.read("manifest.json")
        if failure == name:
            raise RuntimeError(name)
        return value

    def serve(app, **kwargs):
        assert app == "app"
        assert kwargs == {"host": "127.0.0.1", "port": 9123}
        stage("serve", None)

    monkeypatch.setitem(sys.modules, "vibeview.app", SimpleNamespace(
        create_app=lambda reader: stage("create", "app"), serve=serve,
    ))
    monkeypatch.setitem(sys.modules, "vibeview.banner", SimpleNamespace(
        print_banner=lambda reader: stage("banner", None),
    ))
    if failure:
        with pytest.raises(RuntimeError, match=failure):
            launch_qvf(source, port=9123, open_browser=False)
    else:
        assert launch_qvf(source, port=9123, open_browser=False) is None
        assert calls == ["banner", "create", "serve"]
    assert len(observed_readers) == 1
    assert_lifetime(observed_readers[0], borrowed)


def test_launcher_missing_viewer_does_not_open_source(
    source_case, observed_readers, monkeypatch
):
    source, borrowed = source_case
    monkeypatch.setitem(sys.modules, "vibeview.app", None)
    with pytest.raises(ImportError):
        launch_qvf(source, open_browser=False)
    assert len(observed_readers) == int(borrowed)
    if borrowed:
        assert_lifetime(source, True)
