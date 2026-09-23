"""Keep the strict type ramp explicit as source modules are added (#8).

The initial ramp checked only that configured files existed. Adding a new
module, or removing an existing target, could therefore silently shrink the
coverage. This inventory lists the remaining debt; promote a module by adding
it to tool.mypy.files and removing it here in the same change.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Existing debt only; empty since every source module joined the gate. A new
# production module belongs in the strict gate, not here.
DEFERRED_SOURCE: set[str] = set()


def test_every_source_module_is_gated_or_explicitly_deferred() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    targets = config["tool"]["mypy"]["files"]
    assert len(targets) == len(set(targets)), "duplicate strict-type targets"
    source = {path.relative_to(ROOT).as_posix() for path in (ROOT / "src").rglob("*.py")}
    ungated = source - set(targets)
    assert ungated == DEFERRED_SOURCE, (
        f"Source missing from the strict gate: {sorted(ungated - DEFERRED_SOURCE)}; "
        f"remove promoted/deleted modules from DEFERRED_SOURCE: "
        f"{sorted(DEFERRED_SOURCE - ungated)}"
    )
