"""The documentation toolchain lives in package metadata, not in CI (#19).

``.docs_deps`` in ``.gitlab-ci.yml`` used to pin the whole Sphinx toolchain
inline, so the requirement existed in no package metadata: the only way to
render the site locally was to read the CI file and retype ten pins, and
``docs/Makefile`` and ``scripts/build_site.sh`` both assumed ``sphinx-build``
was already on ``PATH`` without saying how it got there.

Four of those pins — ``numpy``, ``click``, ``pydantic`` and ``jsonschema`` —
were already ``[project.dependencies]``, repeated only because the docs job did
not install the package. A core floor that moved in ``pyproject.toml`` would
have left the old one standing here, unnoticed.

These tests are the coupling that replaces the retyping. They follow the
pattern ``tests/test_release_artifacts.py`` established for the ``[release]``
extra.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parents[1]
CONF_PY = VIEWER_DIR / "docs" / "conf.py"
CI_FILE = VIEWER_DIR / ".gitlab-ci.yml"

# The install line every consumer must name, so a reader of any one of them
# learns the whole setup.
DOCS_INSTALL = "-e '.[docs]'"

# Third-party Sphinx extension module -> the distribution that provides it.
# ``sphinx.ext.*`` ships inside Sphinx itself. A new extension in conf.py that
# is in neither place fails the coupling test below rather than the docs build.
EXTENSION_DISTRIBUTIONS = {
    "myst_parser": "myst-parser",
    "sphinx_copybutton": "sphinx-copybutton",
    "sphinx_design": "sphinx-design",
}

# MyST extension -> the distribution it needs on top of myst-parser.
MYST_EXTENSION_DISTRIBUTIONS = {"linkify": "linkify-it-py"}


def _docs_extra() -> list[str]:
    data = tomllib.loads((VIEWER_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]["docs"]


def _core_dependencies() -> list[str]:
    data = tomllib.loads((VIEWER_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


def _requirement_name(requirement: str) -> str:
    """The distribution name from a PEP 508 requirement string."""
    return re.split(r"[<>=!~\[;\s]", requirement, maxsplit=1)[0].strip()


def _conf_assignment(name: str):
    """The literal value assigned to ``name`` at conf.py's top level.

    Read rather than imported: importing conf.py runs the codename loader and
    the release lookup, neither of which a metadata test should need.
    """
    tree = ast.parse(CONF_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"docs/conf.py assigns no top-level {name!r}")


def _declared() -> set[str]:
    return {_requirement_name(r) for r in _docs_extra()}


def test_the_docs_extra_declares_every_extension_conf_py_enables() -> None:
    """Adding an extension to conf.py must also declare what provides it."""
    declared = _declared()
    unknown, missing = [], []
    for extension in _conf_assignment("extensions"):
        if extension == "sphinx" or extension.startswith("sphinx."):
            distribution = "sphinx"
        elif extension in EXTENSION_DISTRIBUTIONS:
            distribution = EXTENSION_DISTRIBUTIONS[extension]
        else:
            unknown.append(extension)
            continue
        if distribution not in declared:
            missing.append((extension, distribution))

    assert not unknown, (
        f"docs/conf.py enables {unknown}, which this test cannot map to a "
        "distribution; add it to EXTENSION_DISTRIBUTIONS and to the [docs] "
        "extra, so a docs build is one `pip install` away"
    )
    assert not missing, (
        f"{missing} are enabled in docs/conf.py but not declared in the [docs] "
        f"extra; `pip install {DOCS_INSTALL}` would not be enough to render the site"
    )


def test_the_docs_extra_declares_the_theme_and_myst_extensions() -> None:
    """html_theme and myst_enable_extensions carry requirements of their own."""
    declared = _declared()
    theme = _conf_assignment("html_theme")
    assert theme in declared, (
        f"docs/conf.py sets html_theme = {theme!r}, which the [docs] extra does "
        "not declare; the build falls back to alabaster and the site changes"
    )
    for myst_extension, distribution in MYST_EXTENSION_DISTRIBUTIONS.items():
        if myst_extension in _conf_assignment("myst_enable_extensions"):
            assert distribution in declared, (
                f"myst_enable_extensions includes {myst_extension!r}, which needs "
                f"{distribution!r}; the [docs] extra does not declare it"
            )


def test_the_docs_extra_repeats_no_core_dependency() -> None:
    """The four repeated core pins were the actual drift risk.

    They are declared once, in [project.dependencies]. The editable install the
    docs job performs brings them, so a copy here could only ever go stale.
    """
    core = {_requirement_name(r) for r in _core_dependencies()}
    repeated = sorted(_declared() & core)
    assert not repeated, (
        f"{repeated} are already [project.dependencies]; repeating them in the "
        "[docs] extra re-pins a floor that the core list owns"
    )


def test_every_docs_requirement_is_version_pinned() -> None:
    """The published site is a release artifact; an unpinned major can change
    how it renders between two builds of the same commit."""
    unpinned = [
        r
        for r in _docs_extra()
        if _requirement_name(r) != "vibeview" and not re.search(r"[<>=~]", r)
    ]
    assert not unpinned, f"unpinned docs requirements: {unpinned}"


def test_ci_installs_the_extra_rather_than_repeating_its_pins() -> None:
    """The pins lived in .gitlab-ci.yml and nowhere else. If they come back
    there, the extra has quietly stopped being the source of truth."""
    ci = CI_FILE.read_text(encoding="utf-8")
    assert DOCS_INSTALL in ci, (
        f".gitlab-ci.yml no longer installs the docs extra ({DOCS_INSTALL})"
    )
    for requirement in _docs_extra():
        name = _requirement_name(requirement)
        if name == "vibeview":
            continue
        assert f"'{name}>=" not in ci, (
            f"{name!r} is pinned in .gitlab-ci.yml again; it belongs in the "
            "[docs] extra so the pin lives with the tree that needs it"
        )


def test_the_site_builders_say_where_sphinx_build_comes_from() -> None:
    """Neither said, which is why the CI file was the only answer."""
    for path in (
        VIEWER_DIR / "docs" / "Makefile",
        VIEWER_DIR / "scripts" / "build_site.sh",
        VIEWER_DIR / "CONTRIBUTING.md",
        VIEWER_DIR / "README.md",
    ):
        assert DOCS_INSTALL in path.read_text(encoding="utf-8"), (
            f"{path.name} does not name `pip install {DOCS_INSTALL}`; a reader "
            "who starts there still has to find the toolchain somewhere else"
        )
