"""The details panel must not run untrusted archive content.

The panel is a ``srcdoc`` iframe fed by several renderers, and much of
what it shows comes straight out of a QVF: run logs, embedded
attachments, citation text. It used to be mounted with
``sandbox="allow-scripts"`` unconditionally, so any escaping slip
anywhere in that chain would have been executable rather than inert.

The bottom panel is now *two* channels, and which one a renderer writes
to decides whether its output can run:

* ``properties_html`` — archive text and tables. Empty sandbox,
  hardcoded. There is no state key that can turn scripts on here, so no
  future text renderer can acquire them by accident.
* ``chart_html`` — renderer-built Plotly figures (SCF convergence,
  COOP/COHP, the orbital energy diagram). ``allow-scripts``, hardcoded,
  like the sibling bands / phonon / EOS / spectra panels.

That replaces an earlier arrangement where one shared iframe bound a
``properties_allow_scripts`` flag: it worked, but it rested on every
activator remembering to drop the grant, and the two chart activators
that never asked for it rendered blank. These tests pin the split: text
panels cannot be scripted, chart panels are, and archive strings that
reach a scripted panel go through Plotly's escaping.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")


def _qvf_with_run_record() -> Path:
    structure = json.dumps(
        {
            "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
            "pbc": [False, False, False],
        }
    ).encode()
    inp, log = b"! input\n", b"SCF converged.\n"
    scf = json.dumps(
        {
            "iterations": [
                {"iter": 1, "energy_eh": -2.8, "diis_error": 1e-1},
                {"iter": 2, "energy_eh": -2.86, "diis_error": 1e-4},
            ]
        }
    ).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "provenance": {"run_status": "converged"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "s.json",
                        "format": "json",
                        "sha256": hashlib.sha256(structure).hexdigest(),
                    }
                },
            },
            {
                "id": "rec",
                "kind": "run.record",
                "program": "vibe-qc",
                "sequence": 1,
                "members": {
                    "input": {
                        "path": "r/i.txt",
                        "format": "binary",
                        "sha256": hashlib.sha256(inp).hexdigest(),
                    },
                    "log": {
                        "path": "r/l.txt",
                        "format": "binary",
                        "sha256": hashlib.sha256(log).hexdigest(),
                    },
                },
            },
            {
                "id": "scf0",
                "kind": "scf_history",
                "members": {
                    "iterations": {
                        "path": "scf.json",
                        "format": "json",
                        "sha256": hashlib.sha256(scf).hexdigest(),
                    }
                },
            },
        ],
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)  # noqa: SIM115
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", structure)
        zf.writestr("r/i.txt", inp)
        zf.writestr("r/l.txt", log)
        zf.writestr("scf.json", scf)
    return Path(tmp.name)


def test_text_and_chart_panels_use_separate_channels():
    """Archive text goes to the unscripted channel, charts to the scripted
    one — and neither leaks into the other across an activation."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _qvf_with_run_record()
    app = create_app(QVFReader(path))
    state, ctrl = app.state, app.controller
    try:
        ctrl.activate_section("rec")
        assert state.properties_html, "the run record panel did not render"
        assert not state.chart_html, (
            "archive text reached the scripted chart channel"
        )

        ctrl.activate_section("scf0")
        assert state.chart_html, (
            "the SCF convergence chart did not render — this is the blank "
            "panel the channel split fixed"
        )
        assert "plotly" in state.chart_html.lower(), (
            "the SCF panel is no longer a Plotly figure; if it became static "
            "markup it belongs on properties_html instead"
        )
        assert not state.properties_html, (
            "the run record's text survived into the next section's panel"
        )
    finally:
        path.unlink(missing_ok=True)


def _qvf_with_coop() -> Path:
    """A dos.coop archive — the other Plotly panel on the shared channel."""
    import numpy as np

    structure = json.dumps(
        {
            "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
            "pbc": [True, True, True],
        }
    ).encode()
    energies = np.linspace(-5.0, 5.0, 20).astype(np.float64)
    projections = np.zeros((2, 20), dtype=np.float64)
    integrated = np.zeros(2, dtype=np.float64)
    meta = json.dumps({"pair_labels": ["A-B", "A-C"]}).encode()

    def _bin(name, arr, shape):
        raw = arr.tobytes()
        return name, raw, {
            "path": f"coop/{name}.bin",
            "format": "binary",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "dtype": "float64",
            "shape": shape,
        }

    members: dict[str, dict] = {}
    blobs: dict[str, bytes] = {}
    for name, raw, spec in (
        _bin("energies", energies, [20]),
        _bin("projections", projections, [2, 20]),
        _bin("integrated", integrated, [2]),
    ):
        members[name] = spec
        blobs[spec["path"]] = raw
    members["meta"] = {
        "path": "coop/meta.json",
        "format": "json",
        "sha256": hashlib.sha256(meta).hexdigest(),
    }
    blobs["coop/meta.json"] = meta

    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "s.json",
                        "format": "json",
                        "sha256": hashlib.sha256(structure).hexdigest(),
                    }
                },
            },
            {"id": "coop0", "kind": "dos.coop", "members": members},
        ],
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)  # noqa: SIM115
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", structure)
        for path, raw in blobs.items():
            zf.writestr(path, raw)
    return Path(tmp.name)


def test_coop_panel_is_on_the_chart_channel():
    """COOP/COHP is Plotly too — it renders blank on the text channel."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _qvf_with_coop()
    app = create_app(QVFReader(path))
    state, ctrl = app.state, app.controller
    try:
        ctrl.activate_section("coop0")
        assert state.chart_html, f"COOP did not render: {state.status_message}"
        assert state.chart_title == "COOP"
        assert not state.properties_html
    finally:
        path.unlink(missing_ok=True)


def test_the_text_panel_cannot_be_granted_scripts():
    """The two iframes hardcode their sandbox.

    The point of the split is that no state key decides whether the
    archive-text panel runs scripts: a renderer that needs them has to
    write to a different channel, mounted in a different iframe. A
    conditional binding here would restore the failure mode the split
    removed (a grant that every activator must remember to drop, and
    that the chart activators forgot to request).
    """
    import inspect
    import re

    from vibeview import app as app_module

    src = inspect.getsource(app_module)

    def _sandbox_after(srcdoc: str) -> str:
        m = re.search(
            re.escape(f'srcdoc=("{srcdoc}",)') + r".*?sandbox=(.*?)\n",
            src,
            re.DOTALL,
        )
        assert m, f"no iframe found for {srcdoc}"
        return m.group(1).strip().rstrip(",")

    assert _sandbox_after("properties_html") == '""', (
        "the archive-text panel no longer hardcodes an empty sandbox"
    )
    assert _sandbox_after("chart_html") == '"allow-scripts"', (
        "the chart panel no longer hardcodes its script grant"
    )
    assert "properties_allow_scripts" not in src, (
        "a per-panel script grant is back on the shared text channel"
    )


class TestAttachmentDownload:
    """Binary attachments: downloadable, never interpreted.

    Attachments are arbitrary bytes by spec. The panel lists them and
    never puts their payload in the DOM; this download path is the only
    thing that touches the bytes, and it hands them straight to a
    browser save.
    """

    def _settled(self, tmp_path):
        import pytest

        vibeqc = pytest.importorskip("vibeqc")
        from vibeqc.qvf_job import run_container

        mol = vibeqc.Molecule(
            [vibeqc.Atom(1, [0.0, 0.0, 0.0]), vibeqc.Atom(1, [0.0, 0.0, 1.4])],
            0,
            1,
        )
        path = vibeqc.write_pending_qvf(
            mol, tmp_path / "job", method="rhf", basis="sto-3g"
        )
        run_container(path, perf_log=True, structured_log=True)
        return path

    def test_download_is_byte_exact_and_never_html(self, tmp_path):
        import asyncio
        import base64

        asyncio.set_event_loop(asyncio.new_event_loop())
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        reader = QVFReader(self._settled(tmp_path))
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        record_id = reader.run_record_sections()[-1].id
        ctrl.activate_section(record_id)

        roles = {a["role"] for a in state.run_record_attachments}
        assert "attachment.perf" in roles, roles

        ctrl.download_run_attachment(["attachment.perf"])  # UI sends [a.role]
        assert state.export_data.startswith(
            "data:application/octet-stream;base64,"
        ), "must force a save, never a renderable media type"
        payload = base64.b64decode(state.export_data.split(",", 1)[1])
        assert payload == reader.read_run_record_attachment_bytes(
            record_id, "attachment.perf"
        )
        assert state.export_filename == "job.perf"

    def test_unknown_and_non_attachment_roles_are_refused(self, tmp_path):
        import asyncio

        asyncio.set_event_loop(asyncio.new_event_loop())
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        reader = QVFReader(self._settled(tmp_path))
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        record_id = reader.run_record_sections()[-1].id
        ctrl.activate_section(record_id)

        state.export_data = ""
        ctrl.download_run_attachment("attachment.does-not-exist")
        assert state.export_data == ""
        assert "Unknown attachment" in state.status_message

        # The log is a member but not an attachment: the download path
        # must not become a general member-exfiltration API.
        assert reader.read_run_record_attachment_bytes(record_id, "log") is None

    def test_producer_filename_cannot_escape_a_basename(self):
        """The files index is producer-supplied and ends up in a browser
        "save as", so a crafted archive must not propose a path."""
        from vibeview.qvf import _safe_basename

        assert _safe_basename("../../.bashrc", "fb") == ".bashrc"
        assert _safe_basename("/etc/passwd", "fb") == "passwd"
        assert _safe_basename("..\\..\\evil.exe", "fb") == "evil.exe"
        for empty in ("", None, ".", ".."):
            assert _safe_basename(empty, "fb") == "fb"


class TestScriptedPanelsResistArchiveInjection:
    """The plotly panels keep ``allow-scripts`` — prove that is safe.

    The plotly panels legitimately need scripts: Plotly is JavaScript.
    That is bands, phonon, EOS and spectra, each on its own state key,
    plus the shared ``chart_html`` channel (SCF convergence, COOP/COHP,
    orbital energies). They also interpolate archive-supplied strings
    — a section ``label``, the manifest's ``source`` — into the figure.
    That combination is only safe because Plotly serialises those
    strings into JSON with ``<`` and ``/`` escaped, so a
    ``</script><script>`` payload cannot break out of the data block.

    Verified end to end in a browser (payload executes in neither the
    top window nor the frame). This test pins the property at the
    renderer level so a future change that interpolates archive text
    into panel *markup* rather than through Plotly gets caught here.
    """

    _PAYLOAD = "</script><script>window.__pwned=1</script>"

    def _spectra_qvf(self) -> Path:
        spec = json.dumps(
            {"frequencies": [500.0, 1500.0], "intensities": [10.0, 40.0]}
        ).encode()
        manifest = {
            "qvf_version": 1,
            "source": {
                "program": self._PAYLOAD,
                "version": "0",
                "calculation": self._PAYLOAD,
            },
            "sections": [
                {
                    "id": "gen0",
                    "kind": "spectra.generic",
                    "label": self._PAYLOAD,
                    "members": {
                        "spectrum": {
                            "path": "s.json",
                            "format": "json",
                            "sha256": hashlib.sha256(spec).hexdigest(),
                        }
                    },
                }
            ],
        }
        tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)  # noqa: SIM115
        with zipfile.ZipFile(tmp, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s.json", spec)
        return Path(tmp.name)

    def test_archive_label_cannot_break_out_of_the_plotly_payload(self):
        from vibeview.qvf import QVFReader
        from vibeview.renderers.spectra import SpectraRenderer

        path = self._spectra_qvf()
        try:
            reader = QVFReader(path)
            html = SpectraRenderer(reader.get_section("gen0"), reader).render_to_html()
            # The label is allowed through — escaped. What must never
            # appear is a real closing tag followed by a real opening one.
            assert "</script><script>" not in html, (
                "an archive label broke out of the Plotly data block into "
                "executable markup"
            )
            assert "\\u003c" in html, (
                "Plotly no longer escapes '<' — the assumption this panel's "
                "allow-scripts grant rests on has changed"
            )
        finally:
            path.unlink(missing_ok=True)
