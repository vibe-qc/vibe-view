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

from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import data_table, heading, note, panel_document

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
        return panel_document("".join(parts))
