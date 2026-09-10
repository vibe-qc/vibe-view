"""The `vibe-view[queue]` extra: declared correctly, and absent gracefully.

vq (vibe-queue) is the one optional dependency that is not installable from a
package index, and the extra named a distribution that does not exist until
2026-09-08 -- so `pip install vibeview[queue]` could not resolve even with a
vibe-queue checkout already installed. These guard the metadata and the seven
lazy import sites that have to survive its absence.
"""

from __future__ import annotations

import ast
import importlib.util
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
_EXTRAS = _PYPROJECT["project"]["optional-dependencies"]


def test_the_queue_extra_names_the_distribution_vibe_queue_actually_ships():
    """vibe-queue's pyproject declares `name = "vq"`; `vibe-queue` is the
    repository directory, not a package. Requiring the latter makes the extra
    unresolvable against anything, forever."""
    assert _EXTRAS["queue"] == ["vq>=0.25"], (
        "the queue extra must require the `vq` distribution; if vibe-queue "
        "renames, change it there first and follow here"
    )


def test_the_queue_extra_stays_out_of_all():
    """vq is on no index, so `pip install vibeview[all]` would fail to
    resolve if `all` pulled it. Deliberate -- keep it that way."""
    flat = " ".join(_EXTRAS["all"])
    assert "queue" not in flat and "vq" not in flat.split()


def test_every_vq_import_site_is_lazy():
    """A module-scope `import vq` would make the whole viewer need the extra."""
    offenders = []
    for name in ("app.py", "cli.py"):
        tree = ast.parse((_ROOT / "src" / "vibeview" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module != "vq" and not node.module.startswith("vq."):
                continue
            if node.col_offset == 0:
                offenders.append(f"{name}:{node.lineno} imports {node.module} at module scope")
    assert not offenders, "\n".join(offenders)


def test_the_queue_capability_is_visible_to_doctor():
    """queue was the only declared extra with no capability row, so the one
    integration with an awkward install was the one doctor said nothing
    about."""
    from vibeview.onboarding import doctor_report

    capabilities = doctor_report()["capabilities"]
    assert "queue" in capabilities
    row = capabilities["queue"]
    assert row["required"] is False
    assert row["available"] is (importlib.util.find_spec("vq") is not None)


def test_a_missing_queue_never_makes_the_install_unhealthy():
    from vibeview.onboarding import doctor_report

    report = doctor_report()
    assert report["healthy"] is True, "an optional extra must not gate health"


@pytest.mark.skipif(
    importlib.util.find_spec("vq") is not None,
    reason="vq is installed; the missing-extra path cannot run",
)
def test_doctor_tells_the_user_how_to_get_the_queue():
    from vibeview.onboarding import doctor_report

    row = doctor_report()["capabilities"]["queue"]
    assert row["missing"] == ["vq"]
    assert "queue" in row["install_hint"]
    # vq is on no index, so the extra alone is not actionable.
    assert row["note"] and "vibe-queue" in row["note"]


def test_the_remediation_names_both_halves():
    """Every other extra surfaces install_hint(). The queue sites said only
    "vq is not installed", and the CLI pointed at `pip install -e vibe-queue/`
    -- a monorepo-relative path that resolves to nothing since the split."""
    from vibeview.install_hints import queue_missing_message

    message = queue_missing_message()
    assert "vibe-queue" in message
    assert "vibeview[queue]" in message or "[queue]" in message
    assert "pip install -e vibe-queue/" not in message

    contextual = queue_missing_message("fetch jobs")
    assert "cannot fetch jobs" in contextual


def _vq_import_guards():
    """Yield (file, try-lineno, caught-exception-names) per guarded vq import.

    A "site" is the *innermost* ``try`` guarding the import, not every
    enclosing one -- and it is one ``try``, not one import statement: the
    queue overview imports ``vq.config`` and ``vq.overview`` together under a
    single guard, so eight import statements form seven sites.
    """
    for name in ("app.py", "cli.py"):
        tree = ast.parse((_ROOT / "src" / "vibeview" / name).read_text(encoding="utf-8"))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        seen: dict[int, list[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module != "vq" and not node.module.startswith("vq."):
                continue
            # Walk up to the nearest enclosing Try whose *body* contains us.
            child, cur = node, parents.get(node)
            while cur is not None and not (
                isinstance(cur, ast.Try) and child in cur.body
            ):
                child, cur = cur, parents.get(cur)
            if cur is None:
                seen[node.lineno] = []  # unguarded; reported by the caller
                continue
            caught: list[str] = []
            for handler in cur.handlers:
                if isinstance(handler.type, ast.Name):
                    caught.append(handler.type.id)
                elif isinstance(handler.type, ast.Tuple):
                    caught.extend(
                        e.id for e in handler.type.elts if isinstance(e, ast.Name)
                    )
                elif handler.type is None:
                    caught.append("BaseException")
            seen[cur.lineno] = caught
        for lineno, caught in sorted(seen.items()):
            yield name, lineno, caught


def test_all_seven_vq_sites_survive_a_missing_queue():
    """Each guarded site must catch ImportError (or a broader class); an
    unguarded one takes the whole viewer down when the extra is absent."""
    sites = list(_vq_import_guards())
    assert len(sites) == 7, f"expected 7 guarded vq import sites, found {len(sites)}"
    unguarded = [
        f"{name}:{lineno} catches {caught or 'nothing'}"
        for name, lineno, caught in sites
        if not ({"ImportError", "Exception", "BaseException"} & set(caught))
    ]
    assert not unguarded, "\n".join(unguarded)


def test_every_vq_import_site_has_a_remediation_path():
    """Every other extra surfaces install_hint() when it is missing. The
    queue sites said only "vq is not installed"."""
    sources = {
        name: (_ROOT / "src" / "vibeview" / name).read_text(encoding="utf-8")
        for name in ("app.py", "cli.py")
    }
    mentions = sum(src.count("queue_missing_message(") for src in sources.values())
    assert mentions >= 7, (
        f"only {mentions} queue_missing_message call sites; every ImportError "
        "path from a `from vq...` import must tell the user how to fix it"
    )
