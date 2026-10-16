"""Kind registry — the four-rule partial-support contract.

Rule 1
    Supported kinds are declared explicitly in a single constant.
Rule 2
    Unknown kinds are classified as "skipped, unsupported"; kinds the writer
    emits but the viewer doesn't render yet are an explicit subset
    (DEFERRED_KINDS) surfaced as "skipped, not yet rendered".
Rule 3
    Vendor-namespace sections (``x_<vendor>.*``) get the vendor name extracted.
Rule 4
    Implemented in qvf.py (sha256 verify-before-use guard).
"""

from __future__ import annotations

# ── Rule 1: single explicit registry ──────────────────────────────────────
SUPPORTED_KINDS: frozenset[str] = frozenset(
    {
        "structure",
        "volume.density",
        "volume.orbital",
        "volume.spin",
        "volume.elf",
        "volume.difference",
        "volume.generic",
        "volume.potential",
        "volume.rdg",
        "basis.ao",
        "bands",
        "spectra.ir",
        "spectra.uvvis",
        "spectra.raman",
        "spectra.ecd",
        "spectra.vcd",
        "spectra.generic",
        "trajectory",
        "reaction.path",
        "reaction.waypoints",
        "scan.surface",
        "vibrations",
        "atom_properties",
        "wavefunction.gto",
        "citations",
        "scf_history",
        "structure.symmetry",
        "spectra.nmr",
        "spectra.epr",
        "dos.total",
        "dos.projected",
        "phonon_bands",
        "phonon_dos",
        "equation_of_state",
        "fermi_surface",
        "bond_orders",
        "topology.qtaim",
        "dos.coop",
        "dos.cohp",
        "run.record",
        "job.spec",
        # Descriptive kinds (qvf 0.2): a scheme relating the structures, and a
        # picture of the archive. Both show in the details panel; neither is
        # chemistry the 3D scene draws.
        "reaction.scheme",
        "preview",
    }
)

#: Supported kinds that describe the archive rather than hold computed data:
#: the qvf reference writers and vibe-sketch emit them, the vibe-qc writer
#: does not (tests/test_kind_drift.py).
DESCRIPTIVE_KINDS: frozenset[str] = frozenset({"reaction.scheme", "preview"})

# ── Deferred kinds: written + schema-defined, viewer renderer pending ──────
# Kinds the writer emits and the shared QVF schema validates, but which
# have no dedicated viewer panel. Referenced excited-state energies are read
# for natural transition orbitals; the state table itself is not yet rendered.
# Keep that distinction visible instead of reporting the section as unsupported.
DEFERRED_KINDS: frozenset[str] = frozenset({"excited_states"})

# Volumetric kinds: a scalar field on a grid, drawn as an isosurface. The
# reader decodes their binary payload on first use, not at file-open time.
VOLUME_KINDS: frozenset[str] = frozenset(
    {
        "volume.density",
        "volume.orbital",
        "volume.spin",
        "volume.elf",
        "volume.difference",
        "volume.generic",
        "volume.potential",
        "volume.rdg",
        "basis.ao",
    }
)

# Kinds with no panel of their own whose content the structure view draws:
# the reader folds a standalone ``bonds`` section into ``read_structure`` and
# the structure renderer draws it. Reported as rendered "via structure", and
# never a reason to refuse a ``critical`` section.
RENDERED_VIA_STRUCTURE: frozenset[str] = frozenset({"bonds"})


# Stored volume kinds that are signed real fields and draw a +/- lobe pair.
# One list for the browser and the terminal, which used to decide by kind in
# one and by "any negative value" in the other (#38-18).
SIGNED_VOLUME_KINDS: frozenset[str] = frozenset(
    {
        "volume.difference",
        "volume.spin",
        "volume.orbital",
        "volume.potential",
        "basis.ao",
    }
)


def classify_section(kind: str) -> tuple[str, str | None]:
    """Return ``(status, detail)`` for a section kind.

    ``status`` is ``"rendered"`` or ``"skipped"``; a section that fails to
    read is reported as ``"error"`` by the caller, which knows about the
    archive, not by this kind-only lookup. ``detail`` is None for a kind with
    its own panel, ``"via structure"`` for :data:`RENDERED_VIA_STRUCTURE`,
    and the human-readable reason for a skipped kind.

    ── Rule 2 + 3: unknown kinds and vendor namespaces.
    """
    if kind in SUPPORTED_KINDS:
        return ("rendered", None)
    # A standalone `bonds` section has no panel of its own, but the reader
    # folds its connectivity into the structure section (read_structure) and
    # the structure renderer draws it. Report it as rendered-via-structure so
    # the banner doesn't mislead the user into thinking their explicit bonds
    # were dropped (audit findings A6-01 / A3-03).
    if kind in RENDERED_VIA_STRUCTURE:
        return ("rendered", "via structure")
    # Rule 2 (deferred subset): a kind the writer emits and the schema accepts
    # but the viewer has no renderer for yet. Honest, distinct from the generic
    # "unsupported" below so the banner doesn't imply the kind is unhandleable.
    if kind in DEFERRED_KINDS:
        return ("skipped", "not yet rendered")
    # Rule 3: vendor-namespace sections
    if kind.startswith("x_"):
        # kind = "x_<vendor>.<rest>" → extract vendor name
        prefix = kind.split(".", 1)[0]  # "x_<vendor>"
        parts = prefix.split("_", 1)
        vendor = parts[1] if len(parts) > 1 else "unknown"
        return ("skipped", f"vendor namespace ({vendor})")
    return ("skipped", "unsupported")
