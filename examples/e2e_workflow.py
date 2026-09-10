#!/usr/bin/env python3
"""End-to-end vibe-view workflow example.

Demonstrates the complete pipeline:
1. Run a vibe-qc calculation (or use a pre-existing .qvf)
2. Open the .qvf with vibe-view's SDK
3. Inspect sections, extract structure, export formats
4. Render a structure screenshot
5. Compare two QVF files
6. Batch-compare a directory of QVFs
7. Generate a dashboard grid
"""

from __future__ import annotations

from pathlib import Path


def workflow_example(qvf_path: str, output_dir: str = "e2e_output"):
    """Run a complete vibe-view workflow on a QVF file."""
    out = Path(output_dir)
    out.mkdir(exist_ok=True)

    # ── 1. Open and inspect ──
    from vibeview import QVFReader, has_section, info, sections

    print("=" * 60)
    print(f"Opening: {qvf_path}")
    reader = QVFReader(qvf_path)

    # Print basic info
    inf = info(reader)
    print(f"  Source: {inf.get('program', '?')} {inf.get('version', '?')}")
    print(f"  Calculation: {inf.get('calculation', '?')}")
    if inf.get("scf_energy_eh"):
        print(f"  SCF Energy: {inf['scf_energy_eh']:.8f} Eh")
    print(f"  QVF version: {inf.get('qvf_version', '?')}")

    # List sections
    secs = sections(reader)
    print(f"\n  Sections ({len(secs)}):")
    for s in secs:
        print(f"    {s['id']:<20s} {s['kind']}")

    # ── 2. Extract structure ──
    from vibeview import export_xyz, get_structure

    if has_section(reader, "structure"):
        sdata = get_structure(reader)
        print(f"\n  Structure: {len(sdata['atoms'])} atoms")
        for a in sdata["atoms"][:3]:
            print(f"    {a['symbol']} @ {a['position']}")

        # Export XYZ
        xyz = export_xyz(reader)
        xyz_path = out / "structure.xyz"
        xyz_path.write_text(xyz)
        print(f"  Exported XYZ -> {xyz_path}")
    else:
        print("\n  No structure section found.")

    # ── 3. Export SVG structure diagram ──
    from vibeview.export_svg import export_svg

    svg_path = out / "structure.svg"
    svg = export_svg(reader, str(svg_path))
    print(f"  Exported SVG -> {svg_path} ({len(svg)} chars)")

    # ── 4. Render structure screenshot (if PyVista + off-screen) ──
    if has_section(reader, "structure"):
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview import capture_structure

        png_path = out / "structure.png"
        ok = capture_structure(reader, png_path, size=(800, 600))
        if ok:
            print(f"  Rendered PNG -> {png_path}")
        else:
            print("  Structure capture skipped (no structure section).")

    # ── 5. Validate ──
    from vibeview import validate

    v = validate(reader)
    print(f"\n  Validation: {'PASS' if v.get('valid') else 'FAIL'}")
    if not v.get("valid"):
        print(f"    Error: {v.get('error', 'unknown')}")

    reader.close()
    print("\nDone!")


def compare_workflow(qvf_a: str, qvf_b: str):
    """Compare two QVF files."""
    from vibeview import diff

    print("=" * 60)
    print(f"Comparing: {Path(qvf_a).name} vs {Path(qvf_b).name}")
    result = diff(qvf_a, qvf_b)

    if result.get("delta_e_eh") is not None:
        delta_kcal = result["delta_e_eh"] * 627.509
        print(f"  Energy delta: {delta_kcal:.2f} kcal/mol")
        print(f"  Energy delta: {result['delta_e_eh']:.8f} Eh")
    if result.get("geo_rmsd_a") is not None:
        print(f"  RMSD: {result['geo_rmsd_a']:.4f} A")
    if result.get("kinds_only_a"):
        print(f"  Sections only in A: {result['kinds_only_a']}")
    if result.get("kinds_only_b"):
        print(f"  Sections only in B: {result['kinds_only_b']}")
    print(f"  Common sections: {result.get('kinds_common', [])}")


def batch_workflow(directory: str):
    """Batch-compare all QVFs in a directory."""
    from vibeview.batch_compare import batch_compare_to_table, compare_batch

    paths = sorted(Path(directory).glob("*.qvf"))
    if not paths:
        print(f"No .qvf files found in {directory}")
        return

    print("=" * 60)
    print(f"Batch comparing {len(paths)} files in {directory}")
    results = compare_batch([str(p) for p in paths])
    print(results["summary"])
    print()
    print(batch_compare_to_table(results))


def dashboard_workflow(directory: str, output: str = "dashboard.png"):
    """Generate a dashboard grid from all QVFs in a directory."""
    from vibeview.dashboard import render_dashboard

    paths = sorted(Path(directory).glob("*.qvf"))
    if not paths:
        print(f"No .qvf files found in {directory}")
        return

    print("=" * 60)
    print(f"Generating dashboard from {len(paths)} files")
    render_dashboard([str(p) for p in paths], output, cols=4)
    print(f"  Dashboard -> {output}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python e2e_workflow.py <path_to.qvf> [second.qvf] [directory/]")
        print()
        print("Examples:")
        print("  python e2e_workflow.py water.qvf")
        print("  python e2e_workflow.py hf.qvf pbe.qvf")
        print("  python e2e_workflow.py ref.qvf other.qvf calculations/")
        sys.exit(1)

    workflow_example(sys.argv[1])

    if len(sys.argv) > 2:
        compare_workflow(sys.argv[1], sys.argv[2])

    if len(sys.argv) > 3:
        batch_workflow(sys.argv[3])
        dashboard_workflow(sys.argv[3])
