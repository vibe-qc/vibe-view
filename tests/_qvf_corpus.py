"""Locating the QVF conformance corpus, and refusing to skip without it.

The corpus lives in the ``qvf`` repository, which since the 2026-09 split is
no longer a directory inside this checkout. Tests that read a real producer
archive skip without it — and a skip passes forever, so the CI lane that
promises the corpus sets ``VIBEVIEW_REQUIRE_QVF_CORPUS=1`` and a missing
corpus becomes a hard error instead. Without that, a rename in the clone step
of ``.gitlab-ci.yml`` would silently retire this coverage in a green pipeline.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()


def qvf_corpus_dir() -> Path | None:
    """Return the corpus directory, or None.

    Order: ``$QVF_CORPUS`` (what CI sets after cloning qvf at its pinned tag),
    a flat sibling ``../qvf`` checkout, the pre-split ``../qvf-writer``
    location, then the per-chat clone layout ``../../qvf/<clone>/`` the
    maintainer's machines use.
    """
    cands: list[Path] = []
    env = os.environ.get("QVF_CORPUS")
    if env:
        cands.append(Path(env))
    cands.append(_HERE.parents[2] / "qvf" / "conformance" / "corpus")
    cands.append(_HERE.parents[2] / "qvf-writer" / "conformance" / "corpus")
    if len(_HERE.parents) > 3:
        cands.extend(sorted((_HERE.parents[3] / "qvf").glob("*/conformance/corpus")))
    return next((c for c in cands if c.is_dir()), None)


def requires_qvf_corpus(*members: str):
    """Mark tests needing corpus archives; refuse to skip in a strict lane.

    Returns a ``skipif`` mark when the corpus is genuinely absent. When
    ``VIBEVIEW_REQUIRE_QVF_CORPUS`` is set, a missing corpus is a broken lane
    rather than an environment to tiptoe around, and this raises at import
    time so the run goes red instead of quietly green.
    """
    corpus = qvf_corpus_dir()
    missing = [name for name in members if not (corpus and (corpus / name).is_file())]
    if missing and os.environ.get("VIBEVIEW_REQUIRE_QVF_CORPUS"):
        detail = f"missing {', '.join(missing)}" if corpus else "corpus not found"
        raise RuntimeError(
            "VIBEVIEW_REQUIRE_QVF_CORPUS is set but the QVF conformance corpus "
            f"is unusable: {detail} "
            f"(QVF_CORPUS={os.environ.get('QVF_CORPUS') or 'unset'}). Clone the "
            "qvf repository at QVF_TAG and point QVF_CORPUS at its "
            "conformance/corpus directory."
        )
    return pytest.mark.skipif(
        bool(missing),
        reason="QVF conformance corpus unavailable; set QVF_CORPUS or check out qvf",
    )
