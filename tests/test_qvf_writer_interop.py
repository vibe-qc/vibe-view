"""vibe-view opens what qvf-writer writes, and qvf's reader accepts what vibe-view writes.

vibe-sketch writes QVF through qvf-writer and reads it back through qvf's
``qvf_reader``, so these two directions are vibe-view's half of the vibe-sketch
hand-off. Before #88 the bundled manifest schema predated qvf v0.2.0 and
refused the root ``byte_order`` and ``metadata`` and every member ``size``,
so no qvf-writer 0.2 archive, and nothing vibe-sketch saves, would open.

Three guards, from the cheapest to the strongest:

* ``tests/data/qvf_writer_0.2.0.qvf`` was written by qvf-writer 0.2.0 with the
  calls vibe-sketch makes (``_vibe_sketch_like_archive``). It opens in every
  lane, with no qvf checkout.
* With the qvf checkout at ``QVF_TAG`` (``tests/_qvf_corpus.py`` finds it), the
  same archive is written fresh by that release's writer, so a qvf release
  that adds a field shows up here as a failing test once ``QVF_TAG`` moves.
* vibe-view's own writers must pass that release's ``validate_qvf``, with the
  schema enforced, which is what vibe-sketch's reader applies.

Regenerate the fixture with qvf-writer on ``PYTHONPATH``::

    python tests/test_qvf_writer_interop.py --write-fixture
"""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

_DATA = Path(__file__).resolve().parent / "data"
FIXTURE = _DATA / "qvf_writer_0.2.0.qvf"

# Hydroxide: a charged species, so the provenance charge has to survive.
_HYDROXIDE = [
    {"symbol": "O", "position": [0.0, 0.0, 0.0]},
    {"symbol": "H", "position": [0.0, 0.0, 0.97]},
]


def _vibe_sketch_like_archive(qvf_writer: Any) -> bytes:
    """An archive written with the qvf-writer calls vibe-sketch makes.

    ``vibesketch.document.qvf``: a non-critical ``x_vibesketch`` extension, a
    vendor document section with a JSON and a binary member, one structure with
    explicit bonds, provenance charge and multiplicity, an empty calculation,
    and the root ``metadata`` block. The metadata is fixed instead of the
    writer's automatic time stamp and random uuid, so the fixture is
    reproducible.
    """
    import numpy as np

    writer = qvf_writer.QvfWriter(program="vibe-sketch", version="0.1.0", calculation="")
    writer.set_extensions({"x_vibesketch": {"version": "1", "critical": False}})
    writer.set_metadata(
        title="hydroxide",
        created_utc="2026-10-14T00:00:00Z",
        uuid="00000000-0000-4000-8000-000000000088",
    )
    writer.set_provenance(charge=-1, multiplicity=1)
    writer.add_vendor_section(
        "x_vibesketch.document",
        json_members={"document": {"schema": "vibe-sketch/1", "species": ["m1"]}},
        binary_members={"thumbnail": (np.arange(16, dtype=np.uint8), "uint8")},
        label="vibe-sketch document",
        section_id="vibesketch_document",
    )
    writer.add_structure(
        _HYDROXIDE, bonds=[{"i": 0, "j": 1, "order": 1.0}], section_id="structure_m1"
    )
    return bytes(writer.to_bytes())


def _manifest(data: bytes | Path) -> dict[str, Any]:
    source = data if isinstance(data, Path) else io.BytesIO(data)
    with zipfile.ZipFile(source) as archive:
        return json.loads(archive.read("manifest.json"))


def _assert_opens_as_hydroxide(source: bytes | Path) -> None:
    from vibeview.qvf import QVFReader

    with QVFReader(source) as reader:
        kinds = {section.id: section.kind for section in reader.sections}
        assert kinds == {
            "vibesketch_document": "x_vibesketch.document",
            "structure_m1": "structure",
        }
        structure = reader.read_structure("structure_m1")
        assert [atom.symbol for atom in structure.atoms] == ["O", "H"]
        assert structure.bonds is not None and len(structure.bonds) == 1
        assert reader.provenance["charge"] == -1
        assert reader.provenance["multiplicity"] == 1


def test_the_fixture_carries_the_fields_the_old_schema_refused() -> None:
    """Otherwise the next test would prove nothing about #88."""
    manifest = _manifest(FIXTURE)
    assert manifest["byte_order"] == "little"
    assert manifest["metadata"]["uuid"] == "00000000-0000-4000-8000-000000000088"
    members = [m for s in manifest["sections"] for m in s["members"].values()]
    assert members and all("size" in member for member in members)


def test_a_qvf_writer_0_2_archive_opens() -> None:
    _assert_opens_as_hydroxide(FIXTURE)


# ── against the qvf release at QVF_TAG ──────────────────────────────────────


@pytest.fixture
def qvf_toolkit(monkeypatch) -> tuple[Any, Any]:
    """``qvf_writer`` and ``qvf_reader`` from the qvf checkout at ``QVF_TAG``.

    Skips without the checkout. In the lane that promises the corpus
    (``VIBEVIEW_REQUIRE_QVF_CORPUS``), a missing toolkit is an error instead.
    """
    from tests._qvf_corpus import qvf_corpus_dir

    corpus = qvf_corpus_dir()
    python_dir = corpus.parent.parent / "python" if corpus is not None else None
    if python_dir is None or not (python_dir / "qvf_writer.py").is_file():
        if os.environ.get("VIBEVIEW_REQUIRE_QVF_CORPUS"):
            raise RuntimeError(
                "VIBEVIEW_REQUIRE_QVF_CORPUS is set, but the qvf checkout has no "
                f"python/qvf_writer.py (corpus: {corpus})"
            )
        pytest.skip("qvf checkout unavailable; set QVF_CORPUS or check out qvf")
    pytest.importorskip("jsonschema", reason="qvf_reader enforces the schema only with it")
    monkeypatch.syspath_prepend(str(python_dir))
    for name in ("qvf_writer", "qvf_reader"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module("qvf_writer"), importlib.import_module("qvf_reader")


def test_the_qvf_release_writer_output_opens(qvf_toolkit) -> None:
    qvf_writer, _ = qvf_toolkit
    _assert_opens_as_hydroxide(_vibe_sketch_like_archive(qvf_writer))


def _vibe_view_archives() -> dict[str, bytes]:
    from vibeview import converters

    water = b"3\nwater\nO 0 0 0.1173\nH 0 0.7572 -0.4692\nH 0 -0.7572 -0.4692\n"
    pdb = (
        b"HETATM    1  O   HOH A   1       0.000   0.000   0.117  1.00  0.00           O\n"
        b"HETATM    2  H1  HOH A   1       0.000   0.757  -0.469  1.00  0.00           H\n"
        b"HETATM    3  H2  HOH A   1       0.000  -0.757  -0.469  1.00  0.00           H\n"
        b"END\n"
    )
    cif = (
        b"data_NaCl\n_cell_length_a 5.64\n_cell_length_b 5.64\n_cell_length_c 5.64\n"
        b"_cell_angle_alpha 90\n_cell_angle_beta 90\n_cell_angle_gamma 90\n"
        b"loop_\n_atom_site_label\n_atom_site_type_symbol\n"
        b"_atom_site_fract_x\n_atom_site_fract_y\n_atom_site_fract_z\n"
        b"Na1 Na 0 0 0\nCl1 Cl 0.5 0.5 0.5\n"
    )
    return {
        "xyz": converters.xyz_to_qvf(water).getvalue(),
        "pdb": converters.pdb_to_qvf(pdb).getvalue(),
        "cif": converters.cif_to_qvf(cif).getvalue(),
        "builder": converters.atoms_to_qvf(
            [(8, 0.0, 0.0, 0.0), (1, 0.0, 0.0, 0.97)], label="hydroxyl"
        ).getvalue(),
    }


@pytest.mark.parametrize("producer", ["xyz", "pdb", "cif", "builder"])
def test_vibe_view_archives_pass_the_qvf_release_validator(qvf_toolkit, producer, tmp_path):
    """The reverse direction: what vibe-view writes, qvf's reference reader accepts."""
    _, qvf_reader = qvf_toolkit
    path = tmp_path / f"{producer}.qvf"
    path.write_bytes(_vibe_view_archives()[producer])
    report = qvf_reader.validate_qvf(str(path))
    assert report["ok"], report["errors"]


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-fixture"]:
        raise SystemExit("usage: python tests/test_qvf_writer_interop.py --write-fixture")
    import qvf_writer

    FIXTURE.write_bytes(_vibe_sketch_like_archive(qvf_writer))
    print(f"wrote {FIXTURE.name} with qvf-writer {qvf_writer.TOOLKIT_VERSION}")
