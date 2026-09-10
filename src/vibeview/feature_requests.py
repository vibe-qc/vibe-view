"""Feature request helper — maps visualization features to backend needs.

Helps users communicate what vibe-qc backend features they need to
enable specific vibe-view visualizations.
"""

from __future__ import annotations

# Maps desired visualization features to the QVF sections/data needed
VIS_NEEDS: dict[str, dict] = {
    "localized_orbitals": {
        "description": "Localized molecular orbitals (Boys, Pipek-Mezey, etc.)",
        "needs_from_qc": [
            "volume.orbital sections for each localized orbital",
            "wavefunction.gto with the full basis set",
            "Occupations may be 0 or 1 (not fractional)",
        ],
        "alternatives": "Natural orbitals (from density matrix diagonalization)",
    },
    "natural_orbitals": {
        "description": "Natural orbitals from correlated wavefunctions",
        "needs_from_qc": [
            "volume.orbital sections for natural orbitals with occupations",
            "Occupation numbers (may be fractional) in metadata",
        ],
        "alternatives": "Can approximate from NOONs if available",
    },
    "periodic_band_unfolding": {
        "description": "Effective band structure in primitive cell",
        "needs_from_qc": [
            "bands sections with spectral weights for each k-point",
            "Primitive-to-supercell transformation matrix",
        ],
    },
    "electron_localization_function": {
        "description": "ELF isosurfaces for bonding analysis",
        "needs_from_qc": ["volume.elf section"],
        "already_supported": True,
    },
    "spin_density": {
        "description": "Spin density isosurfaces (alpha - beta)",
        "needs_from_qc": ["volume.spin section"],
        "already_supported": True,
    },
    "electrostatic_potential_mapped": {
        "description": "ESP mapped onto electron density isosurface",
        "needs_from_qc": [
            "volume.density section",
            "volume.potential section",
        ],
        "already_supported": True,
    },
    "crystal_orbital_overlap_population": {
        "description": "COOP/COHP analysis for periodic systems",
        "needs_from_qc": [
            "dos.coop or dos.cohp sections with pair labels",
        ],
        "already_supported": True,
    },
    "mayer_bond_orders": {
        "description": "Mayer/Wiberg bond order analysis",
        "needs_from_qc": ["bond_orders section with pair data"],
        "already_supported": True,
    },
    "fermi_surface": {
        "description": "3D Fermi surface rendering",
        "needs_from_qc": [
            "fermi_surface section with band-resolved mesh data",
        ],
        "already_supported": True,
    },
    "phonon_dispersion": {
        "description": "Phonon band structure and DOS",
        "needs_from_qc": [
            "phonon_bands section",
            "phonon_dos section (optional)",
        ],
        "already_supported": True,
    },
}


def what_do_i_need(feature: str) -> str:
    """Return a human-readable description of what vibe-qc must produce.

    Parameters
    ----------
    feature : str
        One of the keys in VIS_NEEDS.

    Returns
    -------
    str
        Formatted description.
    """
    info = VIS_NEEDS.get(feature)
    if info is None:
        similar = [k for k in VIS_NEEDS if feature.lower() in k.lower()]
        lines = [f"Unknown feature: {feature}"]
        if similar:
            lines.append(f"Did you mean: {', '.join(similar)}?")
        lines.append(f"Available features: {', '.join(sorted(VIS_NEEDS))}")
        return "\n".join(lines)

    lines = [
        f"## {feature} — {info['description']}",
        "",
    ]
    if info.get("already_supported"):
        lines.append("✅ Already supported in vibe-view.")
        lines.append("")
        lines.append("Make sure your vibe-qc calculation writes these QVF sections:")
    else:
        lines.append(
            "❌ Not yet supported. To enable this visualization, vibe-qc needs to produce:"
        )

    for need in info.get("needs_from_qc", []):
        lines.append(f"  - {need}")

    if info.get("alternatives"):
        lines.append(f"\nAlternative: {info['alternatives']}")

    return "\n".join(lines)


def list_supported() -> list[str]:
    """List features already supported."""
    return [k for k, v in VIS_NEEDS.items() if v.get("already_supported")]


def list_needed() -> list[str]:
    """List features that need backend work."""
    return [k for k, v in VIS_NEEDS.items() if not v.get("already_supported")]
