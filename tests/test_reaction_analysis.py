"""Synthetic endpoint contracts; these numbers are not chemistry validation (#164)."""

from __future__ import annotations

import copy
import json
import zipfile

import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload
from vibeview.reaction_analysis import (
    CONVERSION,
    HARTREE_TO_KJ_MOL,
    build_analysis,
    digest,
    endpoint_svg,
    replay_analysis,
    table_csv,
)
from vibeview.renderers.reaction_scheme import ReactionSchemeRenderer


def settings(core=0):
    return {
        "method": "dlpno-ccsd(t)",
        "basis": "cc-pvdz",
        "aux_basis": "cc-pvdz-ri",
        "reference_contract": "real_restricted_cc",
        "frozen_core_policy": "chemical",
        "frozen_core_count": core,
        "threshold_preset": "tight",
        "thresholds": {"tcut_pno": 1e-7, "tcut_pairs": 1e-5},
        "threshold_unsupported": [],
        "threshold_inactive": [],
        "solver": {"conv_tol_residual": 1e-8, "max_iter": 150},
        "triples_mode": "t1",
        "triples_algorithm": "rotated-occupied compatibility correction",
        "dispersion": None,
        "scf": {"conv_tol_energy": 1e-12, "conv_tol_grad": 1e-9},
    }


def endpoint_builder(n, energy):
    b = QVFArchiveBuilder(program="vibe-qc", version="0.18.4")
    atoms = [{"symbol": "H", "atomic_number": 1, "position": [i * 2.0, 0.0, 0.0]} for i in range(n)]
    b.add_json(
        "geometry",
        "structure",
        "structure",
        structure_payload(atoms, [False] * 3),
        section_extra={"charge": 0, "multiplicity": 1, "run_ref": "calculation"},
    )
    b.add_json(
        "calculation",
        "run.record",
        "files",
        {},
        section_extra={"program": "vibe-qc", "program_version": "0.18.4", "sequence": 1},
    )
    b.add_bytes(
        "calculation",
        "run.record",
        "log",
        b"Synthetic test calculation.\n",
        media_type="text/plain",
    )
    b.set_root(
        "energies",
        {
            "method": "dlpno-ccsd(t)",
            "total": {"value": energy, "units": "hartree"},
            "components": {
                "correlation": {"value": -0.1, "units": "hartree"},
                "x_reference": {"value": energy + 0.1, "units": "hartree"},
                "x_ccsd_correlation": {"value": -0.09, "units": "hartree"},
                "x_triples": {"value": -0.01, "units": "hartree"},
            },
        },
    )
    b.set_provenance(
        {
            "method": "rhf",
            "basis": "cc-pvdz",
            "scf_converged": True,
            "scf_energy": {"value": energy + 0.1, "units": "hartree"},
            "run_status": "converged",
            "correlation_converged": True,
            "result_structure_ref": "geometry",
            "result_run_ref": "calculation",
            "correlation_settings": settings(),
            "verdict": {
                "reference_usable": True,
                "usable": False,
                "internal_stability": "verified_stable",
                "external_stability": "unstable",
                "reference_contract": "real_restricted_cc",
            },
        }
    )
    return b


@pytest.fixture
def campaign(tmp_path):
    scheme = QVFArchiveBuilder(program="vibe-sketch", version="0.6.2")
    config = {"scheme_section": "scheme", "step": "addition", "endpoints": {}}
    for species, n, energy in [("a", 2, -1.0), ("b", 2, -2.0), ("product", 4, -3.05)]:
        b = endpoint_builder(n, energy)
        path = b.write(tmp_path / f"{species}.qvf")
        scheme.add_json(
            species,
            "structure",
            "structure",
            json.loads(b._members["sections/geometry/structure.json"]),
            section_extra={"charge": 0, "multiplicity": 1},
        )
        config["endpoints"][species] = {
            "path": path.name,
            "structure": "geometry",
            "quantity": "correlated_total",
            "method": "dlpno-ccsd(t)",
            "property": "/energies/total",
            "review": {
                "status": "accepted",
                "source_sha256": digest(path.read_bytes()),
                "note": "Synthetic contract fixture only; not scientific acceptance",
            },
        }
    scheme.add_json(
        "scheme",
        "reaction.scheme",
        "scheme",
        {
            "steps": [
                {
                    "id": "addition",
                    "reactants": [{"structure_ref": "a"}, {"structure_ref": "b"}],
                    "products": [{"structure_ref": "product"}],
                }
            ]
        },
    )
    path = scheme.write(tmp_path / "scheme.qvf")
    return path, config, tmp_path / "analysis.qvf"


def build(campaign):
    scheme, config, output = campaign
    return build_analysis(scheme, config, output, base=scheme.parent)


def rewrite(path, mutate):
    """Edit fixture manifests deliberately, retaining member checksum validation."""
    with zipfile.ZipFile(path) as archive:
        contents = {n: archive.read(n) for n in archive.namelist()}
    manifest = json.loads(contents.pop("manifest.json"))
    mutate(manifest, contents)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in contents.items():
            archive.writestr(name, data)


def refresh(campaign, species="a"):
    scheme, config, _ = campaign
    path = scheme.parent / config["endpoints"][species]["path"]
    config["endpoints"][species]["review"]["source_sha256"] = digest(path.read_bytes())
    return path


def test_self_contained_roundtrip_and_exports(campaign):
    path = build(campaign)
    # Original files need not be accessible to reproduce the archived analysis.
    for species in campaign[1]["endpoints"]:
        (path.parent / f"{species}.qvf").unlink()
    with QVFReader(path) as reader:
        result = replay_analysis(reader, "reaction", "addition")
        assert result["delta_eh"] == pytest.approx(-0.05)
        assert result["delta_kj_mol"] == pytest.approx(-0.05 * HARTREE_TO_KJ_MOL)
        assert [t["coefficient"] for t in result["terms"]] == [-1, -1, 1]
        assert result["conversion"] == CONVERSION
        assert "manifest.json#/energies/total" in table_csv(
            result
        ) or "/energies/total" in table_csv(result)
        svg = endpoint_svg(result)
        assert "source_sha256" in svg and "no computed barrier" in svg
        panel = ReactionSchemeRenderer(reader.get_section("reaction"), reader).render_to_html()
        assert "ΔE = -0.050000000000" in panel and "<details>" in panel
        assert "analysis unavailable" not in panel
        assert "run" in result["selections"]["a"]


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda m: m.pop("energies"), "no root energies"),
        (lambda m: m["energies"].update(method="rhf"), "method must"),
        (lambda m: m["energies"].update(method="mace"), "method must"),
        (lambda m: m["energies"]["total"].update(units="kelvin"), "energy units"),
        (lambda m: m["provenance"].update(correlation_converged=False), "both be explicitly"),
        (lambda m: m["provenance"].update(run_status="failed"), "unfinished or rejected"),
        (lambda m: m["provenance"]["verdict"].update(reference_usable=False), "reference contract"),
        (lambda m: m["provenance"].pop("correlation_settings"), "settings missing"),
        (lambda m: m["provenance"]["correlation_settings"].pop("scf"), "incomplete resolved"),
        (lambda m: m["provenance"].update(result_structure_ref="other"), "not explicitly bound"),
        (
            lambda m: m["energies"].update(
                by_method=[
                    {"method": "dlpno-ccsd(t)", "total": {"value": -9.0, "units": "hartree"}}
                ]
            ),
            "conflicting",
        ),
    ],
)
def test_refuse_invalid_or_rejected_source(campaign, mutation, match):
    source = campaign[0].parent / "a.qvf"
    rewrite(source, lambda m, _: mutation(m))
    refresh(campaign)
    with pytest.raises(ValueError, match=match):
        build(campaign)
    assert not campaign[2].exists()


def test_refuse_missing_species_or_review_and_wrong_property(campaign):
    original = copy.deepcopy(campaign[1])
    campaign[1]["endpoints"].pop("a")
    with pytest.raises(ValueError, match="every reactant"):
        build(campaign)
    campaign[1].update(copy.deepcopy(original))
    campaign[1]["endpoints"]["a"]["review"]["status"] = "rejected"
    with pytest.raises(ValueError, match="accepted review"):
        build(campaign)
    campaign[1].update(copy.deepcopy(original))
    campaign[1]["endpoints"]["a"]["property"] = "/energies/components/correlation"
    with pytest.raises(ValueError, match="select /energies/total"):
        build(campaign)


def test_refuse_mixed_settings_and_charge(campaign):
    source = campaign[0].parent / "a.qvf"
    rewrite(
        source,
        lambda m, _: m["provenance"]["correlation_settings"].update(threshold_preset="normal"),
    )
    refresh(campaign)
    with pytest.raises(ValueError, match="incompatible methods"):
        build(campaign)


def test_replay_detects_tampered_delta_and_carrier_conflict(campaign):
    path = build(campaign)

    def mutate(m, contents):
        section = next(s for s in m["sections"] if s["id"] == "reaction")
        member = section["members"]["scheme"]
        payload = json.loads(contents[member["path"]])
        payload["steps"][0]["x_vibeview"]["energies"]["deltas"]["energy"]["value_eh"] = 42
        data = json.dumps(payload).encode()
        contents[member["path"]] = data
        member["sha256"] = digest(data)

    rewrite(path, mutate)
    with QVFReader(path) as reader, pytest.raises(ValueError, match="saved reaction delta"):
        replay_analysis(reader, "reaction", "addition")


def test_cli_and_pinned_qvf_validation(campaign):
    from qvf.validate import validate_qvf

    config_path = campaign[0].parent / "selections.json"
    config_path.write_text(json.dumps(campaign[1]))
    runner = CliRunner()
    result = runner.invoke(
        main, ["reaction", "build", str(campaign[0]), str(config_path), "-o", str(campaign[2])]
    )
    assert result.exit_code == 0, result.output
    report = validate_qvf(campaign[2])
    assert report["ok"], report
    result = runner.invoke(main, ["reaction", "export", str(campaign[2]), "--step", "addition"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["delta_eh"] == pytest.approx(-0.05)


def mutate_scheme(path, mutation):
    def apply(m, contents):
        member = next(s for s in m["sections"] if s["kind"] == "reaction.scheme")["members"][
            "scheme"
        ]
        payload = json.loads(contents[member["path"]])
        mutation(payload["steps"][0])
        data = json.dumps(payload).encode()
        member["sha256"] = digest(data)
        contents[member["path"]] = data

    rewrite(path, apply)


def test_balance_and_nonunit_coefficients(campaign):
    mutate_scheme(campaign[0], lambda step: step["reactants"][0].update(coefficient=2))
    with pytest.raises(ValueError, match="not balanced"):
        build(campaign)
    mutate_scheme(
        campaign[0],
        lambda step: [
            p.update(coefficient=2) for role in ("reactants", "products") for p in step[role]
        ],
    )
    with QVFReader(build(campaign)) as reader:
        assert replay_analysis(reader, "reaction", "addition")["delta_eh"] == pytest.approx(-0.1)


def test_units_normalized_and_by_method_explicit(campaign):
    from vibeview.units import HARTREE_TO_EV

    source = campaign[0].parent / "a.qvf"

    def mutate(m, _):
        m["energies"]["total"] = {"value": -HARTREE_TO_EV, "units": "ev"}
        m["energies"]["by_method"] = [
            {
                "method": "dlpno-ccsd(t)",
                "total": {"value": -1.0, "units": "hartree"},
                "components": copy.deepcopy(m["energies"]["components"]),
            }
        ]

    rewrite(source, mutate)
    refresh(campaign)
    with QVFReader(build(campaign)) as reader:
        result = replay_analysis(reader, "reaction", "addition")
        assert result["delta_eh"] == pytest.approx(-0.05)
        assert result["selections"]["a"]["source_to_hartree"] == 1.0 / HARTREE_TO_EV
    campaign[1]["endpoints"]["a"]["property"] = "/energies/by_method/0/total"
    with QVFReader(build(campaign)) as reader:
        result = replay_analysis(reader, "reaction", "addition")
        assert result["selections"]["a"]["source_to_hartree"] == 1.0


def test_refuse_stale_review_and_different_state(campaign):
    source = campaign[0].parent / "a.qvf"
    rewrite(source, lambda m, _: m["sections"][0].update(multiplicity=3))
    with pytest.raises(ValueError, match="accepted review"):
        build(campaign)
    refresh(campaign)
    with pytest.raises(ValueError, match="charge/spin"):
        build(campaign)


def test_refuse_prior_run_and_structure_binding(campaign):
    source = campaign[0].parent / "a.qvf"

    def mutate(m, _):
        prior = copy.deepcopy(m["sections"][1])
        prior.update(id="later", sequence=2)
        m["sections"].append(prior)

    rewrite(source, mutate)
    refresh(campaign)
    with pytest.raises(ValueError, match="latest run"):
        build(campaign)


def test_refuse_multiple_carriers_and_saved_state(campaign):
    path = build(campaign)
    mutate_scheme(path, lambda step: step.update(x_vibesketch={"energies": {"delta_hartree": 5}}))
    with QVFReader(path) as reader:
        with pytest.raises(ValueError, match="ambiguous reaction energy carriers"):
            replay_analysis(reader, "reaction", "addition")
        panel = ReactionSchemeRenderer(reader.get_section("reaction"), reader).render_to_html()
        assert "Reaction analysis unavailable" in panel
    path = build(campaign)
    rewrite(path, lambda m, _: m["sections"][0].update(charge=1))
    with QVFReader(path) as reader, pytest.raises(ValueError, match="charge/multiplicity"):
        replay_analysis(reader, "reaction", "addition")


def test_refuse_inconsistent_same_formula_connectivity(campaign):
    source = campaign[0].parent / "a.qvf"

    def mutate(m, contents):
        member = m["sections"][0]["members"]["structure"]
        payload = json.loads(contents[member["path"]])
        payload["atoms"][1]["position"] = [0.74, 0, 0]
        data = json.dumps(payload).encode()
        contents[member["path"]] = data
        member["sha256"] = digest(data)

    rewrite(source, mutate)
    refresh(campaign)
    with pytest.raises(ValueError, match="atom order/connectivity"):
        build(campaign)


def test_component_bookkeeping_and_cli_corruption(campaign):
    source = campaign[0].parent / "a.qvf"
    rewrite(source, lambda m, _: m["energies"]["components"]["x_triples"].update(value=-0.5))
    refresh(campaign)
    with pytest.raises(ValueError, match="inconsistent"):
        build(campaign)
    source.write_bytes(b"not a QVF")
    result = CliRunner().invoke(main, ["reaction", "inspect", str(source)])
    assert result.exit_code != 0 and "Error:" in result.output


def test_malformed_saved_extension_is_refused(campaign):
    path = build(campaign)
    mutate_scheme(path, lambda step: step.update(x_vibeview=[]))
    with QVFReader(path) as reader:
        with pytest.raises(ValueError, match="malformed"):
            replay_analysis(reader, "reaction", "addition")
        assert (
            "analysis unavailable"
            in ReactionSchemeRenderer(reader.get_section("reaction"), reader).render_to_html()
        )
