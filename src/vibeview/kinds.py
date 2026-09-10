"""Kind registry — the four-rule partial-support contract (§2.5 of the design doc).

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
    }
)

# ── Deferred kinds: written + schema-defined, viewer renderer pending ──────
# Kinds the writer emits (and the shared QVF schema validates) but the viewer
# does not yet render with a dedicated panel. The producer promoted six kinds
# from reserved to implemented in milestones M15–M19; vibe-view now renders
# phonon_bands + phonon_dos (renderers/phonon.py), equation_of_state
# (renderers/eos.py), volume.potential (the shared volume renderer), volume.rdg
# (NCI; renderers/nci.py), and fermi_surface (reciprocal space;
# renderers/fermi.py) — all six promoted kinds are now rendered, so this set is
# **empty**. It is kept (not deleted) as the structural home for the next writer
# kind that lands without a viewer renderer: adding it here surfaces the section
# as "skipped, not yet rendered" (honest, distinct from "skipped, unsupported")
# instead of tripping the drift guard. Pinned by tests/test_kind_drift.py.
DEFERRED_KINDS: frozenset[str] = frozenset()

# Kinds whose binary .dat blob is lazy-loaded (read from zip only on
# first UI activation, not at file-open time).
LAZY_KINDS: frozenset[str] = frozenset(
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


def classify_section(kind: str) -> tuple[str, str | None]:
    """Return ``(status, detail)`` for a section kind.

    status is one of: ``"rendered"``, ``"skipped"``, ``"error"``.
    detail is ``None`` for rendered sections, or a human-readable
    reason string for skipped/error sections.

    ── Rule 2 + 3: unknown kinds and vendor namespaces.
    """
    if kind in SUPPORTED_KINDS:
        return ("rendered", None)
    # A standalone `bonds` section has no panel of its own, but the reader
    # folds its connectivity into the structure section (read_structure) and
    # the structure renderer draws it. Report it as rendered-via-structure so
    # the banner doesn't mislead the user into thinking their explicit bonds
    # were dropped (audit findings A6-01 / A3-03).
    if kind == "bonds":
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


def is_lazy_kind(kind: str) -> bool:
    """Return True if this kind's binary data should be lazy-loaded."""
    return kind in LAZY_KINDS
