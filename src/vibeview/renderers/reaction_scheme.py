"""reaction.scheme renderer — the steps of a drawn reaction scheme as a table.

A `reaction.scheme` section (QVF spec § 5.14) relates the archive's
`structure` sections as the reactants, reagents and products of each step,
with the kind of arrow, the conditions written at it and the yield. It is
the chemistry of a scheme, not its drawing, so the panel lists the steps as
equations: each species by its compound label and name (spec § 5.1,
"Chemical identity"), else by its section id. Nothing is drawn in the 3D
scene; the structures themselves open from their own sidebar rows.
"""

from __future__ import annotations

import base64
import html
import json
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import banner, data_table, heading, note, panel_document

if TYPE_CHECKING:
    from vibeview.qvf import (
        QVFReader,
        ReactionParticipant,
        ReactionSchemeData,
        ReactionStep,
        Section,
    )

#: How each arrow is drawn between the two sides of a step.
ARROW_SYMBOLS: dict[str, str] = {
    "forward": "→",
    "equilibrium": "⇌",
    "retrosynthetic": "⇒",
    "resonance": "↔",
}


def species_text(reader: QVFReader, structure_id: str) -> str:
    """A species as a scheme shows it: ``name (label)``, either alone, else its id."""
    identity = reader.structure_identity(structure_id)
    if identity.name and identity.label:
        return f"{identity.name} ({identity.label})"
    return identity.name or identity.label or structure_id


def _side(reader: QVFReader, participants: list[ReactionParticipant]) -> str:
    parts = []
    for participant in participants:
        text = species_text(reader, participant.structure_ref)
        if participant.coefficient != 1.0:
            text = f"{participant.coefficient:g} × {text}"
        parts.append(text)
    return " + ".join(parts)


def step_equation(reader: QVFReader, step: ReactionStep) -> str:
    """The step as drawn: ``reactants → products``.

    A retrosynthetic arrow points from the target, the step's products, to
    its precursors, so that step reads ``products ⇒ reactants`` (spec § 5.14:
    roles are chemical, the arrow is how the step is drawn).
    """
    arrow = ARROW_SYMBOLS.get(step.arrow, "→")
    left, right = _side(reader, step.reactants), _side(reader, step.products)
    if step.arrow == "retrosynthetic":
        left, right = right, left
    return f"{left} {arrow} {right}"


def step_yield(step: ReactionStep) -> str | None:
    """The yield as printed, else the percentage, else None."""
    if step.yield_text:
        return step.yield_text
    if step.yield_percent is not None:
        return f"{step.yield_percent:g}%"
    return None


def scheme_species(scheme: ReactionSchemeData) -> list[str]:
    """Every structure the scheme names, in order of first appearance."""
    seen: list[str] = []
    for step in scheme.steps:
        for participant in (*step.reactants, *step.reagents, *step.products):
            if participant.structure_ref not in seen:
                seen.append(participant.structure_ref)
    return seen


class ReactionSchemeRenderer(BaseRenderer):
    """Step table for a reaction.scheme section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: ReactionSchemeData | None = None

    def load(self) -> ReactionSchemeData:
        if self._data is None:
            self._data = self.reader.read_reaction_scheme(self.section_id)
        return self._data

    @property
    def title(self) -> str:
        """The scheme's own label (spec § 5.14), else a generic title."""
        label = (self.section.model_extra or {}).get("label")
        return label if isinstance(label, str) and label.strip() else "Reaction Scheme"

    def render_to_html(self) -> str:
        scheme = self.load()
        rows = [
            {
                "Step": step.label or step.id,
                "Equation": step_equation(self.reader, step),
                "Reagents": _side(self.reader, step.reagents) or None,
                "Conditions": "; ".join(step.conditions) or None,
                "Yield": step_yield(step),
            }
            for step in scheme.steps
        ]
        species_rows = []
        for structure_id in scheme_species(scheme):
            identity = self.reader.structure_identity(structure_id)
            species_rows.append(
                {
                    "Label": identity.label,
                    "Name": identity.name,
                    "Section": structure_id,
                    "Charge": identity.charge,
                    "Multiplicity": identity.multiplicity,
                    "SMILES": identity.smiles,
                }
            )
        parts = [
            heading(self.title),
            note(
                f"{len(scheme.steps)} step(s). Select a structure in the sidebar to show it "
                "in the 3D view."
            ),
            data_table(["Step", "Equation", "Reagents", "Conditions", "Yield"], rows),
            data_table(
                ["Label", "Name", "Section", "Charge", "Multiplicity", "SMILES"],
                species_rows,
                caption="Species",
            ),
        ]
        raw = self.reader.read_json_member(self.section_id, "scheme")
        for step in raw["steps"]:
            if any(key in step for key in ("energies", "x_vibeview", "x_vibesketch")):
                parts.append(self._analysis_html(step["id"]))
        return panel_document("".join(parts))

    def _analysis_html(self, step_id: str) -> str:
        from vibeview.qvf import QVFError
        from vibeview.reaction_analysis import endpoint_svg, replay_analysis

        try:
            result = replay_analysis(self.reader, self.section_id, step_id)
        except (QVFError, ValueError, KeyError, TypeError) as exc:
            return banner(f"Reaction analysis unavailable: {exc}", "failed")
        rows = [
            [
                t["structure_ref"],
                f"{t['coefficient']:+g}",
                t["energy_eh"],
                t["coefficient"] * t["energy_eh"],
                t["source"]["sha256"][:12],
            ]
            for t in result["terms"]
        ]
        svg = endpoint_svg(result)
        image = base64.b64encode(svg.encode()).decode()
        return "".join(
            [
                heading("Correlated electronic endpoint analysis"),
                note(
                    "Recomputed from embedded QVF sources. Scientific acceptance is the recorded "
                    "user review, separate from producer convergence. "
                    "RHF orbitals remain reference "
                    "properties; this result does not establish a minimum or a barrier."
                ),
                data_table(
                    [
                        "Species",
                        "Signed coefficient",
                        "Total / Eh",
                        "Contribution / Eh",
                        "SHA256 prefix",
                    ],
                    rows,
                    numeric_format=".12f",
                ),
                heading(
                    f"ΔE = {result['delta_eh']:+.12f} Eh = {result['delta_kj_mol']:+.6f} kJ/mol"
                ),
                f'<img alt="Disconnected reaction endpoint energy levels" '
                f'style="width:100%;max-width:900px" src="data:image/svg+xml;base64,{image}">',
                note(
                    "Export the table and SVG with vibe-view reaction export; "
                    "expand the recorded selections below to inspect the analysis."
                ),
                "<details><summary>Selections, method, acceptance and source provenance"
                "</summary><pre>",
                html.escape(json.dumps(result, indent=2)),
                "</pre></details>",
            ]
        )
