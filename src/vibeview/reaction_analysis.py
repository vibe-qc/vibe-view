"""Reproducible endpoint analysis using QVF §3.6, §5.8 and §5.14 (#164).

Only explicitly selected correlated electronic totals are aggregated. A user's
hash-bound review is additional to, never a replacement for, producer checks.
Source archives are embedded unchanged so reopening repeats the calculation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any

from vibeview import __version__
from vibeview.archive import write_archive
from vibeview.qvf import QVFReader, ReactionStep
from vibeview.qvf_writer import QVFArchiveBuilder
from vibeview.units import HARTREE_TO_EV, HARTREE_TO_KCAL_MOL

# QVF's CODATA 2018 rounded Hartree/kcal factor; thermochemical kcal = 4.184 kJ.
HARTREE_TO_KJ_MOL = HARTREE_TO_KCAL_MOL * 4.184
CONVERSION = {
    "hartree_to_kj_mol": HARTREE_TO_KJ_MOL,
    "definition": "QVF CODATA 2018: 627.509474 kcal/mol per Eh; 4.184 kJ/kcal",
}
ENERGY_FACTORS = {
    "hartree": 1.0,
    "Eh": 1.0,
    "Hartree": 1.0,
    "ev": HARTREE_TO_EV,
    "eV": HARTREE_TO_EV,
    "kj_per_mol": HARTREE_TO_KJ_MOL,
    "kcal_per_mol": HARTREE_TO_KCAL_MOL,
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{context}: expected a finite number")
    return float(value)


def quantity_eh(value: Any) -> float:
    if not isinstance(value, dict) or value.get("units") not in ENERGY_FACTORS:
        raise ValueError("selected energy needs explicit supported energy units")
    return number(value.get("value"), "energy") / ENERGY_FACTORS[value["units"]]


def inspect_endpoint(reader: QVFReader) -> dict[str, Any]:
    """Expose quantities without interpreting SCF, components or thermal values as totals."""
    m = reader.manifest_json()
    return {
        "source": m.get("source"),
        "energies": m.get("energies"),
        "scf_reference_energy": reader.provenance.get("scf_energy"),
        "thermochemistry": m.get("thermochemistry"),
        "provenance": reader.provenance,
        "sections": [
            {"id": s.id, "kind": s.kind, "identity": (s.model_extra or {})} for s in reader.sections
        ],
    }


def _selected_energy(
    reader: QVFReader, selection: dict[str, Any]
) -> tuple[float, str, dict[str, Any]]:
    if selection.get("quantity") != "correlated_total":
        raise ValueError(
            "choose quantity=correlated_total; SCF, components and thermal energies differ"
        )
    energies = reader.manifest_json().get("energies")
    if not isinstance(energies, dict):
        raise ValueError("QVF has no root energies: correlated total unavailable; no SCF fallback")
    pointer = selection.get("property")
    if pointer == "/energies/total":
        record = energies
    elif isinstance(pointer, str) and pointer.startswith("/energies/by_method/"):
        parts = pointer.split("/")
        try:
            if len(parts) != 5 or parts[4] != "total" or not parts[3].isdigit():
                raise ValueError
            record = energies["by_method"][int(parts[3])]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ValueError("select an exact /energies/by_method/N/total property") from None
    else:
        raise ValueError("select /energies/total or /energies/by_method/N/total explicitly")
    method = record.get("method")
    if method != selection.get("method") or method != "dlpno-ccsd(t)":
        raise ValueError("selected energy method must explicitly match dlpno-ccsd(t)")
    total = quantity_eh(record.get("total"))
    components = record.get("components", {})
    keys = ("x_reference", "x_ccsd_correlation", "x_triples", "correlation")
    if not isinstance(components, dict) or not all(k in components for k in keys):
        raise ValueError("selected correlated total lacks reference/CCSD/triples components")
    reference, ccsd, triples, correlation = [quantity_eh(components[k]) for k in keys]
    if (
        abs(math.fsum([reference, ccsd, triples]) - total) > 1e-8
        or abs(math.fsum([ccsd, triples]) - correlation) > 1e-8
        or abs(quantity_eh(reader.provenance.get("scf_energy")) - reference) > 1e-8
    ):
        raise ValueError("correlated total/components/SCF reference are inconsistent")
    # Duplicate representations of the same result must agree, never choose a convenient one.
    for other in [energies, *energies.get("by_method", [])]:
        if other.get("method") == method and abs(quantity_eh(other.get("total")) - total) > 1e-8:
            raise ValueError("conflicting correlated totals for the selected method")
    return total, method, dict(record["total"])


def _method(reader: QVFReader, method: str) -> dict[str, Any]:
    p = reader.provenance
    if p.get("scf_converged") is not True or p.get("correlation_converged") is not True:
        raise ValueError("SCF and correlation must both be explicitly converged")
    if p.get("run_status") != "converged":
        raise ValueError("source run is missing, unfinished or rejected")
    verdict = p.get("verdict", {})
    if not isinstance(verdict, dict):
        raise ValueError("source reference verdict is malformed")
    if (
        verdict.get("reference_usable") is not True
        or verdict.get("reference_contract") != "real_restricted_cc"
        or verdict.get("internal_stability") != "verified_stable"
        or verdict.get("external_stability") not in {"verified_stable", "unstable"}
    ):
        raise ValueError("source lacks an accepted real_restricted_cc reference contract")
    settings = p.get("correlation_settings")
    if not isinstance(settings, dict) or not settings:
        raise ValueError("resolved correlation_settings missing; method compatibility is unknown")
    # Frozen-core counts vary with composition. The common policy must still match.
    required = {
        "method",
        "basis",
        "aux_basis",
        "reference_contract",
        "frozen_core_policy",
        "frozen_core_count",
        "threshold_preset",
        "thresholds",
        "threshold_unsupported",
        "threshold_inactive",
        "triples_mode",
        "triples_algorithm",
        "dispersion",
        "scf",
        "solver",
    }
    if (
        not required <= settings.keys()
        or not settings["thresholds"]
        or not settings["scf"]
        or not settings["solver"]
    ):
        raise ValueError("incomplete resolved correlation settings")
    if (
        settings["method"] != method
        or settings["basis"] != p.get("basis")
        or settings["reference_contract"] != verdict["reference_contract"]
    ):
        raise ValueError("resolved method conflicts with result provenance")
    settings = json.loads(json.dumps(settings))
    count = settings.pop("frozen_core_count", None)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("resolved frozen_core_count missing")
    if settings.get("frozen_core_policy") not in {
        "chemical",
        "orca-6.1-table-2.69-count-only-default",
    }:
        raise ValueError("explicit chemical frozen-core policy required")
    source = reader.manifest_json()["source"]
    if not source.get("version"):
        raise ValueError("source runtime version missing")
    return {
        "program": source["program"],
        "program_version": source["version"],
        "specification": {
            "method": method,
            "basis": p.get("basis"),
            "reference_contract": verdict["reference_contract"],
            "correlation_settings": settings,
        },
    }


def _participants(step: ReactionStep) -> dict[str, float]:
    if step.arrow == "resonance":
        raise ValueError("a resonance arrow is not an endpoint reaction")
    result: dict[str, float] = {}
    for sign, participants in ((-1, step.reactants), (1, step.products)):
        for part in participants:
            if part.structure_ref in result:
                raise ValueError("duplicate species across reaction roles")
            result[part.structure_ref] = sign * number(part.coefficient, "coefficient")
    return result


def _composition(reader: QVFReader, section: str) -> Counter[str]:
    structure = reader.read_structure(section)
    if any(structure.pbc):
        raise ValueError("endpoint analysis currently requires isolated molecular structures")
    return Counter(atom.symbol for atom in structure.atoms)


def _connectivity(reader: QVFReader, section: str) -> dict[str, Any]:
    from vibeview.bonds import structure_bonds

    structure = reader.read_structure(section)
    return {
        "symbols": [a.symbol for a in structure.atoms],
        "bonds": sorted(sorted([i, j]) for i, j, _, _ in structure_bonds(structure)),
    }


def _result_binding(reader: QVFReader, selection: dict[str, Any]) -> str:
    provenance = reader.provenance
    structure = selection["structure"]
    run = provenance.get("result_run_ref")
    if provenance.get("result_structure_ref") != structure or not isinstance(run, str):
        raise ValueError("selected structure is not explicitly bound to the current result energy")
    if (
        reader.get_section(run).kind != "run.record"
        or (reader.get_section(structure).model_extra or {}).get("run_ref") != run
    ):
        raise ValueError("result structure/run association is missing or inconsistent")
    records = [s for s in reader.sections if s.kind == "run.record"]
    sequence = (reader.get_section(run).model_extra or {}).get("sequence")
    sequences = [(s.model_extra or {}).get("sequence") for s in records]
    if any(isinstance(n, int) for n in sequences):
        if not isinstance(sequence, int) or sequence != max(
            n for n in sequences if isinstance(n, int)
        ):
            raise ValueError("root energy is not associated with the latest run")
        if sum(n == sequence for n in sequences) != 1:
            raise ValueError("ambiguous latest run sequence")
    elif len(records) != 1:
        raise ValueError("multiple runs without explicit sequence ordering")
    return run


def _review(selection: dict[str, Any], sha: str) -> None:
    review = selection.get("review", {})
    if not isinstance(review, dict):
        raise ValueError("source review must be an object")
    if (
        review.get("status") != "accepted"
        or review.get("source_sha256") != sha
        or not isinstance(review.get("note"), str)
        or not review["note"].strip()
    ):
        raise ValueError(
            "each source requires an explicit accepted review, note and matching SHA256"
        )


def _endpoint(data: bytes, selection: dict[str, Any]) -> dict[str, Any]:
    sha = digest(data)
    _review(selection, sha)
    with QVFReader(data) as reader:
        # Verify every member, including unselected scientific evidence, before accepting it.
        for section in reader.sections:
            for member in section.members.values():
                reader._verify_and_read(member)
        value, method, source_energy = _selected_energy(reader, selection)
        run = _result_binding(reader, selection)
        identity = reader.structure_identity(selection["structure"])
        charge, multiplicity = identity.charge, identity.multiplicity
        # The explicit result binding makes root state unambiguous even when
        # other input/drawing structures were preserved in the same container.
        for key, stated in (("charge", charge), ("multiplicity", multiplicity)):
            root_value = reader.provenance.get(key)
            if root_value is not None and (
                isinstance(root_value, bool) or not isinstance(root_value, int)
            ):
                raise ValueError(f"source {key} is not an integer")
            if stated is not None and root_value is not None and stated != root_value:
                raise ValueError(f"source section and result {key} disagree")
        if charge is None:
            charge = reader.provenance.get("charge")
        if multiplicity is None:
            multiplicity = reader.provenance.get("multiplicity")
        if charge is None or multiplicity is None or multiplicity < 1:
            raise ValueError("source structure charge/multiplicity missing or ambiguous")
        return {
            "energy_eh": value,
            "source_run": run,
            "source_energy": source_energy,
            "energies": reader.manifest_json()["energies"],
            "connectivity": _connectivity(reader, selection["structure"]),
            "method": _method(reader, method),
            "sha256": sha,
            "composition": dict(_composition(reader, selection["structure"])),
            "charge": charge,
            "multiplicity": multiplicity,
            "provenance": reader.provenance,
            "structure": reader.read_json_member(selection["structure"], "structure"),
        }


def _aggregate(terms: list[dict[str, Any]], endpoints: list[dict[str, Any]]) -> float:
    if not terms or len(terms) != len(endpoints):
        raise ValueError("missing reaction species")
    methods = [e["method"] for e in endpoints]
    if any(method != methods[0] for method in methods[1:]):
        raise ValueError("incompatible methods, runtime versions or resolved settings")
    elements: dict[str, float] = {}
    charge = 0.0
    for term, endpoint in zip(terms, endpoints, strict=True):
        coefficient = number(term["coefficient"], "coefficient")
        charge += coefficient * endpoint["charge"]
        for symbol, count in endpoint["composition"].items():
            elements[symbol] = elements.get(symbol, 0.0) + coefficient * count
    if abs(charge) > 1e-8 or any(abs(v) > 1e-8 for v in elements.values()):
        raise ValueError("reaction atoms or charge are not balanced")
    return math.fsum(
        t["coefficient"] * e["energy_eh"] for t, e in zip(terms, endpoints, strict=True)
    )


def _attachment(builder: QVFArchiveBuilder, run: str, data: bytes, filename: str) -> None:
    # QVF run.record permits opaque binary attachments, including unchanged source QVFs.
    builder.add_bytes(
        run,
        "run.record",
        "attachment.source_qvf",
        data,
        media_type="application/vnd.qvf+zip",
        section_extra={"program": "vibe-view", "program_version": __version__},
    )
    builder.add_bytes(
        run,
        "run.record",
        "log",
        b"Source QVF retained byte-for-byte for endpoint analysis replay.\n",
        media_type="text/plain",
    )
    builder.add_json(
        run,
        "run.record",
        "files",
        {
            "attachment.source_qvf": {
                "filename": filename,
                "media_type": "application/vnd.qvf+zip",
                "truncated": False,
            }
        },
    )


def build_analysis(scheme_path: Path, config: dict[str, Any], output: Path, *, base: Path) -> Path:
    """Build a QVF from a scheme and a selection/review-only configuration.

    The config contains step/section IDs and endpoint paths, selectors and
    hash-bound review decisions. Every scientific value comes from source QVF.
    """
    scheme_bytes = scheme_path.read_bytes()
    builder = QVFArchiveBuilder(calculation="correlated electronic endpoint reaction analysis")
    with QVFReader(scheme_bytes) as scheme:
        data = scheme.read_reaction_scheme(config["scheme_section"])
        chosen = [s for s in data.steps if s.id == config["step"]]
        if len(chosen) != 1:
            raise ValueError("select exactly one existing reaction step")
        step = chosen[0]
        coefficients = _participants(step)
        selections = config["endpoints"]
        if set(selections) != set(coefficients):
            raise ValueError("endpoint selections must cover every reactant/product exactly once")
        reserved = {"reaction", "source_scheme"} | {
            f"endpoint_{i}" for i in range(len(coefficients))
        }
        if reserved.intersection(coefficients):
            raise ValueError("scheme species IDs conflict with analysis section IDs")
        terms, endpoints, saved_selections = [], [], {}
        for index, (species, coefficient) in enumerate(coefficients.items()):
            selection = dict(selections[species])
            if set(selection) != {"path", "structure", "quantity", "method", "property", "review"}:
                raise ValueError("endpoint configuration must contain only path/selectors/review")
            path = (base / selection.pop("path")).resolve()
            if path == output.resolve() or scheme_path.resolve() == output.resolve():
                raise ValueError("analysis output must not overwrite an input archive")
            source_bytes = path.read_bytes()
            endpoint = _endpoint(source_bytes, selection)
            if _composition(scheme, species) != Counter(endpoint["composition"]):
                raise ValueError(f"{species}: source structure does not match scheme composition")
            if _connectivity(scheme, species) != endpoint["connectivity"]:
                raise ValueError(f"{species}: atom order/connectivity differs from scheme")
            identity = scheme.structure_identity(species)
            if (
                identity.charge != endpoint["charge"]
                or identity.multiplicity != endpoint["multiplicity"]
            ):
                raise ValueError(f"{species}: source charge/spin differs from scheme")
            run = f"endpoint_{index}"
            builder.add_json(
                species,
                "structure",
                "structure",
                endpoint["structure"],
                section_extra={
                    "name": identity.name or species,
                    "charge": endpoint["charge"],
                    "multiplicity": endpoint["multiplicity"],
                },
            )
            _attachment(builder, run, source_bytes, path.name)
            terms.append(
                {
                    "structure_ref": species,
                    "coefficient": coefficient,
                    "energy_eh": endpoint["energy_eh"],
                    "method": endpoint["method"],
                    "run_ref": run,
                    "source": {
                        "name": path.name,
                        "sha256": endpoint["sha256"],
                        "parser": f"vibe-view/{__version__}",
                        "locations": {"energy_eh": "manifest.json#" + selection["property"]},
                    },
                }
            )
            selection["run"] = endpoint["source_run"]
            selection["source_energy"] = endpoint["source_energy"]
            selection["source_to_hartree"] = (
                1.0 / ENERGY_FACTORS[endpoint["source_energy"]["units"]]
            )
            saved_selections[species] = selection
            endpoints.append(endpoint)
        delta = _aggregate(terms, endpoints)
        energies = {
            "version": 1,
            "terms": terms,
            "deltas": {"energy": {"value_eh": delta}},
            "x_vibeview": {
                "version": 1,
                "quantity": "correlated_total",
                "conversion": CONVERSION,
                "selections": saved_selections,
                "source_scheme": {
                    "sha256": digest(scheme_bytes),
                    "section": config["scheme_section"],
                    "step": step.id,
                },
            },
        }
        saved_step = {
            "id": step.id,
            "reactants": [
                {"structure_ref": k, "coefficient": -v} for k, v in coefficients.items() if v < 0
            ],
            "products": [
                {"structure_ref": k, "coefficient": v} for k, v in coefficients.items() if v > 0
            ],
            "arrow": step.arrow,
            "x_vibeview": {"energies": energies},
        }
        builder.add_json("reaction", "reaction.scheme", "scheme", {"steps": [saved_step]})
        _attachment(builder, "source_scheme", scheme_bytes, scheme_path.name)
        builder.set_root("extensions", {"x_vibeview": {"version": "1", "critical": False}})
    # Validate/recompute in memory before writing. A malformed selection never leaves a result.
    with QVFReader(builder.to_bytesio()) as reader:
        replay_analysis(reader, "reaction", step.id)
    return write_archive(output, builder.manifest, builder.members())


def stored_energies(step: dict[str, Any]) -> Any:
    """One unambiguous carrier; v0.6 writes only the documented vendor extension."""
    if any(not isinstance(step.get(k, {}), dict) for k in ("x_vibeview", "x_vibesketch")):
        raise ValueError("malformed reaction energy extension")
    carriers = [
        step.get("energies"),
        step.get("x_vibeview", {}).get("energies"),
        step.get("x_vibesketch", {}).get("energies"),
    ]
    present = [v for v in carriers if v is not None]
    if len(present) > 1:
        raise ValueError("ambiguous reaction energy carriers; select one analysis")
    return present[0] if present else None


def replay_analysis(reader: QVFReader, section: str, step_id: str) -> dict[str, Any]:
    """Recompute embedded evidence; malformed vendor data is an explicit refusal."""
    try:
        return _replay_analysis(reader, section, step_id)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f"malformed saved reaction analysis: {exc}") from exc


def _replay_analysis(reader: QVFReader, section: str, step_id: str) -> dict[str, Any]:
    """Recompute from embedded sources, compare all saved scientific values and selections."""
    steps = reader.read_reaction_scheme(section).steps
    selected = [s for s in steps if s.id == step_id]
    if len(selected) != 1:
        raise ValueError("select one reaction step")
    step = selected[0]
    raw = reader.read_json_member(section, "scheme")
    stored = stored_energies(next(s for s in raw["steps"] if s["id"] == step_id))
    if not isinstance(stored, dict) or stored.get("version") != 1:
        raise ValueError("no supported saved endpoint energies")
    meta = stored.get("x_vibeview", {})
    if (
        meta.get("version") != 1
        or meta.get("quantity") != "correlated_total"
        or meta.get("conversion") != CONVERSION
    ):
        raise ValueError("analysis lacks supported selection/review/conversion metadata")
    coefficients = _participants(step)
    original = reader.read_run_record_attachment_bytes("source_scheme", "attachment.source_qvf")
    scheme_selection = meta.get("source_scheme", {})
    if original is None or digest(original) != scheme_selection.get("sha256"):
        raise ValueError("original reaction scheme is missing or its hash changed")
    with QVFReader(original) as original_reader:
        original_steps = original_reader.read_reaction_scheme(scheme_selection["section"]).steps
        original_step = [s for s in original_steps if s.id == scheme_selection.get("step")]
        if (
            len(original_step) != 1
            or original_step[0].id != step_id
            or _participants(original_step[0]) != coefficients
        ):
            raise ValueError("saved participants differ from the original reaction scheme")
    terms = stored["terms"]
    refs = [t["structure_ref"] for t in terms]
    if len(set(refs)) != len(refs) or set(refs) != set(coefficients):
        raise ValueError("missing or duplicate reaction terms")
    if set(meta["selections"]) != set(coefficients):
        raise ValueError("missing or duplicate endpoint selections")
    endpoints = []
    for term in terms:
        species = term["structure_ref"]
        if term["coefficient"] != coefficients[species]:
            raise ValueError("signed coefficient disagrees with reaction scheme")
        source = reader.read_run_record_attachment_bytes(term["run_ref"], "attachment.source_qvf")
        if source is None:
            raise ValueError("embedded source QVF is missing or too large")
        selection = meta["selections"][species]
        endpoint = _endpoint(source, selection)
        if (
            selection.get("source_energy") != endpoint["source_energy"]
            or selection.get("source_to_hartree")
            != 1.0 / ENERGY_FACTORS[endpoint["source_energy"]["units"]]
        ):
            raise ValueError("saved source units or normalization differs from source")
        if selection.get("run") != endpoint["source_run"]:
            raise ValueError("saved source run selector differs from current result")
        identity = reader.structure_identity(species)
        if (
            identity.charge != endpoint["charge"]
            or identity.multiplicity != endpoint["multiplicity"]
        ):
            raise ValueError("saved endpoint charge/multiplicity differs from source")
        if (
            term["source"]["sha256"] != endpoint["sha256"]
            or term["source"]["locations"]["energy_eh"] != "manifest.json#" + selection["property"]
            or term["method"] != endpoint["method"]
            or number(term["energy_eh"], "saved energy") != endpoint["energy_eh"]
        ):
            raise ValueError("saved source/hash/property/method/energy disagrees with source QVF")
        if reader.read_json_member(species, "structure") != endpoint["structure"]:
            raise ValueError("saved endpoint structure differs from its source")
        endpoints.append(endpoint)
    delta = _aggregate(terms, endpoints)
    if abs(number(stored["deltas"]["energy"]["value_eh"], "saved delta") - delta) > 1e-10:
        raise ValueError("saved reaction delta disagrees with recomputed sum")
    return {
        "step": step_id,
        "quantity": "correlated_total",
        "delta_eh": delta,
        "delta_kj_mol": delta * HARTREE_TO_KJ_MOL,
        "conversion": CONVERSION,
        "terms": terms,
        "labels": {
            t["structure_ref"]: reader.structure_identity(t["structure_ref"]).name
            or t["structure_ref"]
            for t in terms
        },
        "selections": meta["selections"],
        "provenance": [e["provenance"] for e in endpoints],
        "endpoint_energies": [e["energies"] for e in endpoints],
        "interpretation": (
            "Electronic endpoint difference; no transition state, barrier or trajectory"
        ),
    }


def table_csv(result: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "species",
            "coefficient",
            "correlated_total_Eh",
            "contribution_Eh",
            "source_QVF",
            "sha256",
            "source_structure",
            "source_run",
            "source_units",
            "source_to_hartree",
            "property",
            "method",
            "review",
            "hartree_to_kj_mol",
        ]
    )
    for t in result["terms"]:
        s = result["selections"][t["structure_ref"]]
        writer.writerow(
            [
                t["structure_ref"],
                t["coefficient"],
                t["energy_eh"],
                t["coefficient"] * t["energy_eh"],
                t["source"]["name"],
                t["source"]["sha256"],
                s["structure"],
                s["run"],
                s["source_energy"]["units"],
                s["source_to_hartree"],
                s["property"],
                json.dumps(t["method"], sort_keys=True),
                json.dumps(s["review"]),
                HARTREE_TO_KJ_MOL,
            ]
        )
    writer.writerow(["delta", "", "", result["delta_eh"]])
    return output.getvalue()


def endpoint_svg(result: dict[str, Any]) -> str:
    """Two disconnected endpoint levels; SVG metadata retains the full analysis."""
    from matplotlib.backends.backend_svg import FigureCanvasSVG
    from matplotlib.figure import Figure

    delta = number(result["delta_kj_mol"], "reaction energy")
    labels = [
        " + ".join(
            f"{abs(t['coefficient']):g} {result['labels'][t['structure_ref']]}"
            for t in result["terms"]
            if t["coefficient"] * sign > 0
        )
        for sign in (-1, 1)
    ]
    labels = [textwrap.fill(label, width=35) for label in labels]
    figure = Figure(figsize=(9, 4.8), layout="constrained")
    FigureCanvasSVG(figure)
    ax = figure.subplots()
    ax.hlines([0, delta], [0.7, 1.7], [1.3, 2.3], color="#225d89", linewidth=3)
    pad = max(abs(delta) * 0.06, 0.1)
    ax.text(1, pad, "0 (reactant sum)", ha="center")
    ax.text(2, delta + pad, f"{delta:+.6f}", ha="center")
    ax.set_xticks([1, 2], labels)
    ax.set_xlim(0.5, 2.5)
    ax.set_ylim(min(0, delta) - 3 * pad, max(0, delta) + 5 * pad)
    ax.set_ylabel("Correlated electronic energy / kJ mol⁻¹")
    ax.set_title("Reaction endpoints — no computed barrier or trajectory")
    ax.spines[["top", "right"]].set_visible(False)
    figure.supxlabel(f"1 Eh = {HARTREE_TO_KJ_MOL:.9f} kJ/mol", fontsize=9)
    output = io.StringIO()
    figure.savefig(
        output,
        format="svg",
        metadata={
            "Description": json.dumps(result, sort_keys=True),
            "Date": None,
            "Creator": f"vibe-view/{__version__}",
        },
    )
    return output.getvalue()
