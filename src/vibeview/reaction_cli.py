"""CLI for explicit, reproducible QVF endpoint analysis (#164)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click


@click.group()
def reaction() -> None:
    """Inspect, select and replay correlated electronic reaction endpoints."""


@reaction.command("inspect")
@click.argument("archive", type=click.Path(exists=True, path_type=Path))
def inspect_command(archive: Path) -> None:
    """List QVF structures, quantities, units, convergence and provenance as JSON."""
    from vibeview.qvf import QVFError, QVFReader
    from vibeview.reaction_analysis import digest, inspect_endpoint

    try:
        with QVFReader(archive) as reader:
            result = inspect_endpoint(reader)
        result["sha256"] = digest(archive.read_bytes())
        click.echo(json.dumps(result, indent=2, allow_nan=False))
    except (QVFError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@reaction.command("build")
@click.argument("scheme", type=click.Path(exists=True, path_type=Path))
@click.argument("selections", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(path_type=Path))
def build_command(scheme: Path, selections: Path, output: Path) -> None:
    """Save analysis QVF from a scheme and a selection/review JSON (no scientific values)."""
    from vibeview.qvf import QVFError
    from vibeview.reaction_analysis import build_analysis

    if output.exists():
        raise click.ClickException("output exists; choose a new analysis filename")
    try:
        config = json.loads(selections.read_text())
        build_analysis(scheme, config, output, base=selections.parent)
        click.echo(str(output))
    except (QVFError, ValueError, OSError, KeyError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc


@reaction.command("export")
@click.argument("archive", type=click.Path(exists=True, path_type=Path))
@click.option("--section", default="reaction", show_default=True)
@click.option("--step", required=True)
@click.option("--csv", "csv_path", type=click.Path(path_type=Path))
@click.option(
    "--figure", type=click.Path(path_type=Path), help="Endpoint-only SVG with provenance."
)
def export_command(
    archive: Path, section: str, step: str, csv_path: Path | None, figure: Path | None
) -> None:
    """Recompute saved analysis from embedded QVF sources; export JSON, CSV and SVG."""
    from vibeview.qvf import QVFError, QVFReader
    from vibeview.reaction_analysis import endpoint_svg, replay_analysis, table_csv

    if figure and figure.suffix.lower() != ".svg":
        raise click.ClickException("endpoint figure output must use .svg")
    if csv_path and figure and csv_path.resolve() == figure.resolve():
        raise click.ClickException("table and figure need different output paths")
    for path in (csv_path, figure):
        if path and path.exists():
            raise click.ClickException(f"output exists: {path}; choose a new filename")
    try:
        with QVFReader(archive) as reader:
            result: dict[str, Any] = replay_analysis(reader, section, step)
        if csv_path:
            csv_path.write_text(table_csv(result))
        if figure:
            figure.write_text(endpoint_svg(result))
        click.echo(json.dumps(result, indent=2, allow_nan=False))
    except (QVFError, ValueError, OSError, KeyError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
