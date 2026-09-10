"""Release codename catalogue.

Every vibe-view minor release carries a "Person's Animal" codename, the same
*form* the vibe-qc and vibe-queue series use, so the three products read as one
family. Each product draws from its own pool of people, so no name is ever
claimed twice; vibe-view's pool is visualization, computer graphics and
crystallographic imaging. See ``docs/codenames.md`` for the policy and the
pool, and ``docs/release_process.md`` for where in a cut the entry is added.

This module is the **single source of truth**. Four surfaces read it:

* ``vibe-view --version`` (:mod:`vibeview.cli`)
* the browser viewer's About box (:mod:`vibeview.app`)
* the Electron desktop About box, via the launcher handshake config
  (``electron/main.js`` reads ``CONFIG.codename``)
* the documentation site's ``{{codename}}`` substitution (``docs/conf.py``,
  which loads this file standalone -- it must therefore keep importing
  nothing beyond the standard library)

Before this module existed the string was pasted into all four by hand, which
is a drift the test suite could not see. ``tests/test_release_codenames.py``
now checks that every surface agrees with the catalogue.

Programmatic API
----------------

``codename_for_version(version)``
    Resolve a version string to its codename, or ``None``.

``codename_for_current()``
    Convenience: the codename of the running ``vibeview.__version__``.

``version_label()``
    The ``"2.15.2 -- Person's Animal"`` string the user-facing surfaces print.
"""

from __future__ import annotations

import re

__all__ = [
    "RELEASE_CODENAMES",
    "codename_for_current",
    "codename_for_version",
    "version_label",
]


# --- Catalogue -------------------------------------------------------------
#
# Keyed by the ``X.Y.Z`` release version. Minor releases (``X.Y.0``) are always
# present; a patch release only needs its own entry if it has a distinct theme
# worth surfacing separately, and otherwise inherits its parent minor's name
# via the fallback in :func:`codename_for_version`. Dev / alpha / beta / rc
# builds inherit the codename of the release they lead up to, after the PEP-440
# suffix is stripped.
#
# When cutting a new MINOR release, add the entry here in the same commit that
# bumps ``pyproject.toml`` and ``src/vibeview/__init__.py``.
RELEASE_CODENAMES: dict[str, str] = {
    "2.15.0": "Roothaan's Roadrunner",  # The name v2.15.2 actually shipped
    # with, recorded here as history rather than
    # as a pool entry. It predates the split and
    # the per-product pools, and it borrows a
    # quantum-chemistry figure already used by
    # vibe-qc ("Roothaan's Raven", v0.1.0). It
    # is in the wild -- in the CLI, the desktop
    # About box and a tagged wheel -- so it
    # stands, by maintainer decision 2026-09-09.
    # Names from v2.16.0 on come from the
    # visualization / graphics /
    # crystallographic-imaging pool in
    # docs/codenames.md.
    "2.16.0": "Sayle's Starling",  # Roger Sayle, RasMol (1992): the first
    # molecular viewer a scientist could simply
    # install and run, free, without the program
    # that produced the data. That is what
    # v2.16.0 is -- vibe-view becoming a product
    # you can adopt on its own, with its own
    # documentation site, release series and
    # procedure. Starling: ordinary, gregarious,
    # and its murmurations are the standard
    # image of structure emerging from many
    # small parts. First name from the
    # visualization pool; approved by the
    # maintainer on 2026-09-09, ahead of the
    # cut, so the artwork brief could be
    # written against it.
    "2.17.0": "Lorensen's Loon",  # William Lorensen: marching cubes (1987)
    # and a founder of VTK. For a release
    # centred on isosurfaces and volume
    # rendering -- his algorithm is what draws
    # every density and orbital surface here,
    # in the toolkit he helped create, so this
    # is the most directly earned name in the
    # pool. A loon dives and surfaces.
    "2.18.0": "Richardson's Robin",  # Jane Richardson: the ribbon diagram
    # (1981). For biomolecular work -- chains,
    # residues, secondary structure -- because
    # the ribbon is still exactly what the
    # viewer draws when a structure section
    # carries secondary structure.
    "2.19.0": "Phong's Pheasant",  # Bui Tuong Phong: the Phong reflection
    # model. For a release about materials and
    # lighting, anchored in the renderer rather
    # than the product -- Phong shading is how
    # a molecular scene is lit.
    "2.20.0": "Levoy's Lemur",  # Marc Levoy: direct volume rendering
    # (1988). For transfer functions -- colour
    # and opacity taken straight from the data
    # with no surface extracted, the other half
    # of the volume story from Lorensen's.
    "3.0.0": "Levinthal's Lynx",  # Cyrus Levinthal: the first interactive
    # molecular graphics system (MIT, 1965), on
    # hardware that had no business doing it.
    # Held for a major version about the
    # interactive viewer itself. A lynx is what
    # you name the thing that sees in the dark.
    #
    # v2.17.0 through v3.0.0 were approved by
    # the maintainer on 2026-09-09 and relayed
    # by the release chat. They are registered,
    # not scheduled: a version resolves to its
    # name whenever it is cut, and nothing here
    # commits to cutting it.
}


_DEV_SUFFIX = re.compile(
    r"""
    (?:\.dev\d+ | a\d+ | b\d+ | rc\d+)   # PEP-440 pre-release / dev segments
    (?:\+.*)?$                            # ... and any local version label
    """,
    re.VERBOSE,
)
_LOCAL_SUFFIX = re.compile(r"\+.*$")


def _strip_dev_suffix(version: str) -> str:
    """Reduce a PEP-440 version to the ``X.Y.Z`` release it belongs to.

    ``2.16.0.dev3`` -> ``2.16.0``, ``2.16.0rc1`` -> ``2.16.0``,
    ``2.15.2+local.1`` -> ``2.15.2``.
    """
    return _DEV_SUFFIX.sub("", _LOCAL_SUFFIX.sub("", version.strip()))


def codename_for_version(version: str) -> str | None:
    """Return the codename for a vibe-view release version, or ``None``.

    Resolution order:

    1. Strip any PEP-440 ``.devN`` / ``aN`` / ``bN`` / ``rcN`` suffix and
       local version label, so a dev build inherits the codename of the
       release it leads up to.
    2. Direct lookup of the resulting ``X.Y.Z`` in :data:`RELEASE_CODENAMES`.
    3. Fall back to the parent minor, ``f"{X}.{Y}.0"``, so patch releases
       inherit without needing an entry of their own.
    4. ``None`` when no codename is registered for that minor.

    Examples
    --------
    >>> codename_for_version("2.15.0")
    "Roothaan's Roadrunner"
    >>> codename_for_version("2.15.2")          # patch inherits the minor
    "Roothaan's Roadrunner"
    >>> codename_for_version("2.15.0.dev1")     # dev inherits the release
    "Roothaan's Roadrunner"
    >>> codename_for_version("99.99.99") is None
    True
    """
    stripped = _strip_dev_suffix(version)
    if stripped in RELEASE_CODENAMES:
        return RELEASE_CODENAMES[stripped]
    parts = stripped.split(".")
    if len(parts) >= 2:
        anchor = f"{parts[0]}.{parts[1]}.0"
        if anchor in RELEASE_CODENAMES:
            return RELEASE_CODENAMES[anchor]
    return None


def codename_for_current() -> str | None:
    """Return the codename of the running vibe-view, or ``None``.

    Imported lazily so this module stays importable with nothing but the
    standard library -- ``docs/conf.py`` loads it by path, outside the
    package, in a container where ``vibeview`` is not installed.
    """
    from vibeview import __version__

    return codename_for_version(__version__)


def version_label(version: str | None = None) -> str:
    """Format the version string the user-facing surfaces print.

    ``"2.15.2 — Roothaan's Roadrunner"`` when a codename is registered, and
    the bare version when one is not, so an unnamed dev line never prints a
    dangling em dash.
    """
    if version is None:
        from vibeview import __version__

        version = __version__
    name = codename_for_version(version)
    return f"{version} — {name}" if name else version
