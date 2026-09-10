"""Test the web serve module."""

from __future__ import annotations

import json
import tempfile
import zipfile
from html import escape
from pathlib import Path


def _make_qvf(
    path: Path,
    *,
    program: str = "vibe-qc",
    version: str = "0.9.0",
    calculation: str = "test",
    section_id: str = "structure",
    section_kind: str = "structure",
) -> None:
    """Create a minimal QVF at the given path."""
    structure = json.dumps(
        {
            "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
            "pbc": [False, False, False],
        }
    ).encode()
    import hashlib

    manifest = {
        "qvf_version": 1,
        "source": {
            "program": program,
            "version": version,
            "calculation": calculation,
        },
        "sections": [
            {
                "id": section_id,
                "kind": section_kind,
                "members": {
                    "structure": {
                        "path": "sections/structure.json",
                        "format": "json",
                        "sha256": hashlib.sha256(structure).hexdigest(),
                    }
                },
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/structure.json", structure)


class TestServeModule:
    def test_browse_dir_generates_html(self) -> None:
        """_browse_dir produces HTML with file listing."""
        from vibeview.serve import _browse_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            _make_qvf(td / "test.qvf")
            html = _browse_dir(td)
            assert "test.qvf" in html
            assert "<table>" in html
            assert "test" in html  # calculation name from source

    def test_browse_dir_empty(self) -> None:
        """Empty directory shows 'No .qvf files found'."""
        from vibeview.serve import _browse_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            html = _browse_dir(td)
            assert "No .qvf files found" in html

    def test_browse_pages_escape_names_paths_and_manifest_text(self) -> None:
        """Filesystem and QVF strings stay text in both listing modes."""
        import os

        import pytest

        from vibeview.serve import _browse_dir, _browse_recursive

        if os.name == "nt":
            pytest.skip("Windows forbids the HTML metacharacters used in filenames")
        payload = '<img src=x onerror="alert(1)"> & "quoted"'
        escaped = escape(payload, quote=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir) / payload
            td.mkdir()
            (td / payload).mkdir()
            nested = td / f"nested-{payload}"
            nested.mkdir()
            _make_qvf(td / f"{payload}.qvf", calculation=payload)
            (td / f"unreadable-{payload}.qvf").write_bytes(b"not a zip")
            _make_qvf(nested / f"nested-{payload}.qvf", calculation=payload)

            folder_html = _browse_dir(td, show_welcome=False)
            recursive_html = _browse_recursive(td)

        for rendered in (folder_html, recursive_html):
            assert payload not in rendered
            assert "<img src=x" not in rendered
            assert escaped in rendered

    def test_detail_and_loading_pages_isolate_every_output_context(self) -> None:
        """QVF metadata, filenames, and inline-script data cannot add markup."""
        from vibeview.serve import _loading_page, _script_string, _view_page

        filename_payload = "viewer & quoted"
        metadata_payload = '</title><script>alert("x")</script><b data-x="y">'
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            filename = f"{filename_payload}.qvf"
            _make_qvf(
                td / filename,
                program=metadata_payload,
                version=metadata_payload,
                calculation=metadata_payload,
                section_id=metadata_payload,
                section_kind=metadata_payload,
            )
            detail_html = _view_page(filename, td)

        assert filename_payload not in detail_html
        assert metadata_payload not in detail_html
        assert escape(filename_payload, quote=True) in detail_html
        # Program, version, calculation, section ID, and section kind all
        # survive as text.  This count prevents one safe metadata field from
        # masking an unrendered or unescaped peer.
        assert detail_html.count(escape(metadata_payload, quote=True)) == 5
        assert detail_html.count("<script>") == 0

        status_url = (
            f'/open-status?file={metadata_payload}&next="quoted"'
            "\\path\n\u2028\u2029"
        )
        loading_html = _loading_page(metadata_payload, status_url)
        assert json.loads(_script_string(status_url)) == status_url
        assert metadata_payload not in loading_html
        assert escape(metadata_payload, quote=True) in loading_html
        assert "</script><script>" not in loading_html
        assert "\\u003c/script\\u003e" in loading_html
        assert "\\u0026next=" in loading_html
        assert "\\u2028\\u2029" in loading_html

    def test_nested_detail_path_cannot_terminate_title(self) -> None:
        """The legacy slash-containing detail route treats its path as text."""
        import os

        import pytest

        from vibeview.serve import _view_page

        if os.name == "nt":
            pytest.skip("Windows forbids the HTML metacharacters used in filenames")
        qvf_rel = "</title><script>alert(1)</script>.qvf"
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            qvf = td / qvf_rel
            qvf.parent.mkdir(parents=True)
            _make_qvf(qvf)
            rendered = _view_page(qvf_rel, td)

        assert qvf_rel not in rendered
        assert escape(qvf_rel, quote=True) in rendered
        assert "</title><script>" not in rendered

    def test_download_disposition_cannot_inject_response_headers(self) -> None:
        from vibeview.serve import _attachment_disposition

        header = _attachment_disposition('result"\r\nX-Injected: yes.qvf')
        assert header.startswith("attachment; filename*=UTF-8''")
        assert "\r" not in header
        assert "\n" not in header
        assert "X-Injected:" not in header
        assert "%22%0D%0AX-Injected%3A%20yes.qvf" in header

    def test_listing_links_round_trip_special_paths(self) -> None:
        """HTML decoding and query parsing recover every special path byte."""
        import urllib.parse
        from html.parser import HTMLParser

        from vibeview.serve import _browse_dir

        class LinkCollector(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.hrefs: list[str] = []

            def handle_starttag(
                self, tag: str, attrs: list[tuple[str, str | None]]
            ) -> None:
                if tag == "a":
                    self.hrefs.extend(
                        value for key, value in attrs if key == "href" and value
                    )

        special = "round & # + % ' café"
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir) / special
            td.mkdir()
            qvf = td / f"{special}.qvf"
            _make_qvf(qvf)
            rendered = _browse_dir(td, show_welcome=False)

        links = LinkCollector()
        links.feed(rendered)
        queries = [
            urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            for href in links.hrefs
        ]
        assert any(query.get("file") == [str(qvf.resolve())] for query in queries)
        assert any(
            query.get("dir") == [str(td.resolve())]
            and query.get("recursive") == ["1"]
            for query in queries
        )

    def test_new_route_never_reflects_paths_or_errors_in_status_line(
        self, monkeypatch
    ) -> None:
        """CR/LF-capable filesystem text cannot split an HTTP response."""
        import http.client
        import socket
        import threading
        import time
        import urllib.parse

        from vibeview import serve
        from vibeview.serve import serve_directory

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            hostile = root / "X-Injected marker"
            hostile.mkdir()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            monkeypatch.setattr(serve, "_dir_writable", lambda _path: False)
            threading.Thread(
                target=serve_directory,
                args=(root,),
                kwargs={"port": port},
                daemon=True,
            ).start()
            for _ in range(50):
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    conn.request(
                        "GET", "/new?dir=" + urllib.parse.quote(str(hostile))
                    )
                    response = conn.getresponse()
                    response.read()
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("server did not start")

            assert response.status == 403
            assert response.reason == "folder is read-only"
            assert "X-Injected" not in str(response.headers)

            monkeypatch.setattr(serve, "_dir_writable", lambda _path: True)

            def fail_create(_path: Path) -> None:
                raise OSError("failed\r\nX-Injected: yes")

            monkeypatch.setattr(serve, "_create_new_qvf", fail_create)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/new")
            response = conn.getresponse()
            response.read()
            assert response.status == 500
            assert response.reason == "could not create a new structure here"
            assert "X-Injected" not in str(response.headers)

    def test_human_size(self) -> None:
        from vibeview.serve import _human_size

        assert _human_size(0) == "0.0 B"
        assert _human_size(1023) == "1023.0 B"
        assert _human_size(1024) == "1.0 KB"
        assert _human_size(1048576) == "1.0 MB"

    def test_browse_dir_absolute_navigation_links(self) -> None:
        """Directory rows, parent, and Home links use /browse?dir=<abs>
        so the user can walk the whole filesystem, not just the launch
        directory."""
        import urllib.parse

        from vibeview.serve import _browse_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir).resolve()
            (td / "runs").mkdir()
            html = _browse_dir(td)
            assert "/browse?dir=" + urllib.parse.quote(str(td / "runs")) in html
            assert "/browse?dir=" + urllib.parse.quote(str(td.parent)) in html  # ⬆ Parent
            assert "/browse?dir=" + urllib.parse.quote(str(Path.home())) in html  # ⌂ Home
            assert str(td) in html  # absolute path shown, not "/"

    def test_serve_directory_dir_query(self) -> None:
        """GET /browse?dir=<other-abs-dir> lists a directory outside the
        launch root."""
        import http.client
        import socket
        import threading
        import time

        from vibeview.serve import serve_directory

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            _make_qvf(Path(other) / "elsewhere.qvf")
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            threading.Thread(
                target=serve_directory,
                args=(Path(root),),
                kwargs={"port": port},
                daemon=True,
            ).start()
            for _ in range(50):
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                    conn.request("GET", "/browse?dir=" + str(Path(other).resolve()))
                    body = conn.getresponse().read().decode()
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("server did not start")
            assert "elsewhere.qvf" in body

    def test_view_page(self) -> None:
        """_view_page generates metadata + download page."""
        import hashlib
        import json
        import tempfile
        import zipfile
        from pathlib import Path

        # Create a minimal QVF in a temp dir
        td = tempfile.mkdtemp()
        dir_path = Path(td)
        structure = json.dumps(
            {
                "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
                "pbc": [False, False, False],
            }
        ).encode()
        manifest = {
            "qvf_version": 1,
            "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
            "sections": [
                {
                    "id": "s",
                    "kind": "structure",
                    "members": {
                        "s": {
                            "path": "s.json",
                            "format": "json",
                            "sha256": hashlib.sha256(structure).hexdigest(),
                        }
                    },
                }
            ],
        }
        qvf_path = dir_path / "test.qvf"
        with zipfile.ZipFile(qvf_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s.json", structure)

        from vibeview.serve import _view_page

        html = _view_page("test.qvf", dir_path)
        assert "test.qvf" in html
        assert "Download" in html
        assert "Sections" in html

        import shutil

        shutil.rmtree(td, ignore_errors=True)


class TestNewStructureAndRecursiveBrowse:
    """The from-scratch entry point and the recursive QVF finder.

    Launching the app without a QVF (exactly the "I want to build an input"
    case) used to dead-end in an empty listing with nothing to click; and
    finding a run's output required knowing which directory it landed in.
    """

    def test_create_new_qvf_is_a_loadable_single_carbon(self) -> None:
        import tempfile

        from vibeview.qvf import QVFReader
        from vibeview.serve import _create_new_qvf, _openable

        root = Path(tempfile.mkdtemp())
        p1 = _create_new_qvf(root)
        p2 = _create_new_qvf(root)  # collision increments, never overwrites
        assert p1.name == "new-structure.qvf"
        assert p2.name == "new-structure-2.qvf"
        assert _openable(p1)
        reader = QVFReader(p1)
        sdata = reader.read_structure()
        assert [a.symbol for a in sdata.atoms] == ["C"]
        assert "builder:" in reader.manifest.source.calculation
        reader.close()

    def test_find_qvfs_recursive_newest_first_skips_junk(self) -> None:
        import tempfile
        import time

        from vibeview.serve import _create_new_qvf, _find_qvfs

        root = Path(tempfile.mkdtemp())
        (root / "deep/nested").mkdir(parents=True)
        oldest = _create_new_qvf(root / "deep/nested")
        time.sleep(0.02)
        newest = _create_new_qvf(root)
        (root / ".hidden").mkdir()
        _create_new_qvf(root / ".hidden")  # hidden dirs are skipped
        (root / "node_modules").mkdir()
        _create_new_qvf(root / "node_modules")  # junk dirs are skipped

        files, truncated = _find_qvfs(root)
        assert not truncated
        assert files == [newest, oldest], "must be newest first, junk excluded"

    def test_http_new_and_recursive_routes(self) -> None:
        """GET /new creates a scratch file and redirects into /open;
        GET /browse?recursive=1 lists nested files with their location."""
        import http.client
        import socket
        import tempfile
        import threading
        import time

        from vibeview.serve import _create_new_qvf, serve_directory

        root = Path(tempfile.mkdtemp())
        (root / "sub").mkdir()
        _create_new_qvf(root / "sub")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        threading.Thread(
            target=serve_directory, args=(root,), kwargs={"port": port}, daemon=True
        ).start()
        for _ in range(50):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/browse")
                body = conn.getresponse().read().decode()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("server did not start")

        assert "New structure" in body
        assert "All QVFs below here" in body

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/new")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 302
        assert "/open?file=" in (resp.getheader("Location") or "")
        assert (root / "new-structure.qvf").exists()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/browse?recursive=1")
        body = conn.getresponse().read().decode()
        assert "sub/" in body, "nested file's location missing"
        assert "new-structure.qvf" in body

    def test_browse_dir_marks_new_structure_read_only(self, monkeypatch) -> None:
        """In a read-only folder the New-structure action is disabled with an
        explanation instead of linking to a write that 500s (a Dock launch
        used to land on `/` and fail with EROFS)."""
        import tempfile

        from vibeview import serve
        from vibeview.serve import _browse_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            monkeypatch.setattr(serve, "_dir_writable", lambda _p: False)
            html = _browse_dir(td)
            assert "New structure (read-only)" in html
            assert "/new?dir=" not in html

    def test_http_new_refuses_read_only_folder(self, monkeypatch) -> None:
        """GET /new on a read-only folder answers 403 before attempting the
        write, and creates nothing."""
        import http.client
        import socket
        import tempfile
        import threading
        import time

        from vibeview import serve
        from vibeview.serve import serve_directory

        root = Path(tempfile.mkdtemp())
        monkeypatch.setattr(serve, "_dir_writable", lambda _p: False)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        threading.Thread(
            target=serve_directory, args=(root,), kwargs={"port": port}, daemon=True
        ).start()
        resp = None
        for _ in range(50):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/new")
                resp = conn.getresponse()
                resp.read()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("server did not start")

        assert resp is not None
        assert resp.status == 403
        assert not (root / "new-structure.qvf").exists()

    def test_find_qvfs_respects_the_time_budget(self) -> None:
        """The desktop app's browse root can be $HOME or even / (dock
        launches have no cwd handshake), where an unbounded walk takes
        minutes while the page shows nothing — the original 'the button
        does nothing' report. A zero budget must return immediately and
        say it was cut off."""
        import time

        from vibeview.serve import _find_qvfs

        t0 = time.monotonic()
        files, truncated = _find_qvfs(Path.home(), time_budget_s=0.0)
        assert time.monotonic() - t0 < 1.0
        assert truncated is True
        assert files == []
