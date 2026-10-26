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
        # The transition density of an excited state (qvf 0.2, spec § 5.2): a
        # signed field with the members of a density, drawn like a
        # difference density (#115).
        "volume.transition_density",
        # The Laplacian of the density, ∇²ρ (qvf 0.6, spec § 5.2): a signed
        # scalar grid with the members of a density (#154).
        "volume.laplacian",
        # A Cartesian vector per grid point (qvf 0.6, spec § 5.2), drawn as
        # arrows rather than an isosurface, so not a VOLUME_KINDS member (#156).
        "volume.vector",
        # A complex scalar per grid point (qvf 0.6, spec § 5.2): drawn as its
        # real or imaginary part, or |ψ| coloured by phase; not a VOLUME_KINDS
        # member, whose paths assume a real array (#157).
        "volume.complex",
        # n scalar fields on one grid with an axis (qvf 0.6, spec § 5.2),
        # stepped like trajectory frames; not a VOLUME_KINDS member (#158).
        "volume.series",
        # Energy derivatives (qvf 0.6, spec § 5.10–5.11): force arrows and a
        # Hessian frequency table (#159).
        "forces",
        "hessian",
        # Property tables (qvf 0.6, spec § 5.13), with a dipole arrow for the
        # electric properties (#161).
        "properties.electric",
        "properties.elastic",
        "properties.dielectric",
        # Crystalline orbitals at k-points (qvf 0.6, spec § 5.3.1), evaluated
        # on demand like wavefunction.gto (#162).
        "wavefunction.bloch",
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
        # The states of a TD-DFT (or similar) calculation, tabulated in a
        # details panel (#109).
        "excited_states",
    }
)

#: Supported kinds that describe the archive rather than hold computed data:
#: the qvf reference writers and vibe-sketch emit them, the vibe-qc writer
#: does not (tests/test_kind_drift.py).
DESCRIPTIVE_KINDS: frozenset[str] = frozenset({"reaction.scheme", "preview"})

# ── Deferred kinds: written + schema-defined, viewer renderer pending ──────
# Kinds the shared QVF schema defines, but which have no viewer panel yet:
# surfaced as "skipped, not yet rendered" instead of "unsupported" (#96).
# The excited-state table left this list with #109; the qvf 0.6 kinds below
# joined it (#153); volume.laplacian (#154), volume.vector (#156),
# volume.complex (#157), volume.series (#158), forces and hessian (#159),
# the properties kinds (#161) and wavefunction.bloch (#162) have since left
# it, so it is empty again; the category stays for the next new kind.
# tests/test_kind_drift.py ties every schema kind to a list.
DEFERRED_KINDS: frozenset[str] = frozenset()

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
        "volume.transition_density",
        "volume.laplacian",
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
        "volume.transition_density",
        "volume.laplacian",
        "basis.ao",
    }
)


def classify_section(kind: str) -> tuple[str, str | None]:
    """Return ``(status, detail)`` for a section kind.

    ``status`` is ``"rendered"`` or ``"skipped"``; a section that fails to
    read is reported as ``"error"`` by the caller, which knows about the
    archive, not by this kind-only lookup. ``detail`` is None for a kind with
    its own panel, ``"via structure"`` for :data:`RENDERED_VIA_STRUCTURE`,
    ``"via plugin"`` for a kind an installed renderer plugin claims, and the
    human-readable reason for a skipped kind.

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
    # An installed `vibeview.renderers` plugin renders this kind (#113). After
    # the built-ins, which a plugin cannot claim, and before the skipped
    # categories: a vendor kind is the usual thing a plugin renders.
    from vibeview.renderer_plugins import PLUGIN_DETAIL, plugin_kinds

    if kind in plugin_kinds():
        return ("rendered", PLUGIN_DETAIL)
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
