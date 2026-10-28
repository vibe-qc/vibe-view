"""Custom QVF charges retain their producer's definition and atom alignment."""
import numpy as np
import pytest

from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload
from vibeview.renderers.atom_properties import AtomPropertiesRenderer


def archive(values):
    builder = QVFArchiveBuilder()
    builder.add_json("structure", "structure", "structure", structure_payload(
        [{"symbol": "He", "atomic_number": 2, "position": [0, 0, 0]}], [False] * 3
    ))
    builder.add_json("charges", "atom_properties", "meta", {
        "custom.xtb_charge": {"kind": "scalar", "units": "e",
                              "description": "GFN0-xTB partial charge"}
    }, section_extra={"structure_ref": "structure"})
    builder.add_binary("charges", "atom_properties", "custom.xtb_charge", np.asarray(values))
    return builder.to_bytesio()


def test_custom_charge_has_honest_table_label():
    with QVFReader(archive([0.])) as reader:
        rendered = AtomPropertiesRenderer(reader.get_section("charges"), reader).render_to_html()
        assert "GFN0-xTB partial charge" in rendered
        assert "Mulliken" not in rendered and "No charge data" not in rendered


@pytest.mark.parametrize("values", [[0., 1.], [float("nan")]])
def test_custom_charge_rejects_wrong_shape_or_nonfinite(values):
    with (
        QVFReader(archive(values)) as reader,
        pytest.raises(ValueError, match="one finite charge per atom"),
    ):
        AtomPropertiesRenderer(reader.get_section("charges"), reader).render_to_html()
