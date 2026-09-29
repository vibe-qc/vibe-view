"""Keep the complete strict source inventory explicit as modules are added.

Every production module belongs in tool.mypy.files. The empty deferred set
records that the original type ramp is complete; missing or duplicate targets
must not silently shrink coverage.
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
