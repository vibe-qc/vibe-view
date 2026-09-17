"""The CI rule matrix is a table, not an accident (#22).

``test`` and ``qvf-conformance`` carried no ``rules:`` of their own, so they
fell through to the ``workflow:`` admission list and ran on every ref it
admits. On ``release`` that meant re-running, against an identical tree, the
gate the ``release-candidate/*`` branch had just passed at the same SHA — the
duplicate observed after v2.16.2 — and on ``main`` it meant a full suite on
every landing, which release preparation had to push with ``ci.skip`` to avoid.

Nothing checked which jobs ran where, which is how the executable rules drifted
from the delivery model in ``docs/roadmap.md`` and ``docs/release_process.md``
without anyone noticing. These tests are that check: :data:`MATRIX` below is the
specification, and the rest of the file evaluates ``.gitlab-ci.yml`` against it
the way GitLab would.

The evaluator covers only the ``if:`` forms this file uses — ``$VAR ==
"literal"`` and ``$VAR =~ /regex/``. Anything else raises rather than
evaluating false, so an expression it cannot read fails the suite instead of
quietly reporting that no job runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest
import yaml

VIEWER_DIR = Path(__file__).resolve().parents[1]
CI_FILE = VIEWER_DIR / ".gitlab-ci.yml"

# Top-level keys that configure the pipeline rather than declare a job.
NOT_JOBS = {"include", "stages", "variables", "workflow", "default", "image"}

# The product jobs this repository owns. Deployment jobs come from the
# external include and are not visible here.
PRODUCT_JOBS = ("test", "qvf-conformance", "docs-build")


# --------------------------------------------------------------------------
# The specification.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """The pipeline variables a rule can see."""

    name: str
    variables: dict[str, str]
    changes: frozenset[str] = frozenset()


def _push(branch: str) -> dict[str, str]:
    return {"CI_COMMIT_BRANCH": branch, "CI_PIPELINE_SOURCE": "push"}


# A merge request pipeline has no CI_COMMIT_BRANCH at all; a web pipeline is a
# push-like pipeline on a ref, with the source overridden.
MERGE_REQUEST = {"CI_PIPELINE_SOURCE": "merge_request_event"}

# Paths that stand for "the change touches the site" and "it does not".
DOCS_CHANGE = frozenset({"docs/manual/bands.md"})
CODE_CHANGE = frozenset({"src/vibeview/renderers/volume.py"})

# ref / source -> the jobs that must run, and only those.
#
#   ref / source          test  qvf-conformance  docs-build
#   --------------------  ----  ---------------  ----------
#   merge request          yes        yes         on change
#   release-candidate/*    yes        yes            yes
#   web (manual)           yes        yes            yes
#   main                    no         no            yes
#   release                 no         no            yes
MATRIX: tuple[tuple[Context, set[str]], ...] = (
    (
        Context("merge request touching the docs", MERGE_REQUEST, DOCS_CHANGE),
        {"test", "qvf-conformance", "docs-build"},
    ),
    (
        Context("merge request touching only code", MERGE_REQUEST, CODE_CHANGE),
        {"test", "qvf-conformance"},
    ),
    (
        Context("release candidate", _push("release-candidate/v2.19.0")),
        {"test", "qvf-conformance", "docs-build"},
    ),
    (
        Context("manual web run on main", {**_push("main"), "CI_PIPELINE_SOURCE": "web"}),
        {"test", "qvf-conformance", "docs-build"},
    ),
    (
        Context(
            "manual web run on release",
            {**_push("release"), "CI_PIPELINE_SOURCE": "web"},
        ),
        {"test", "qvf-conformance", "docs-build"},
    ),
    (Context("push to main", _push("main")), {"docs-build"}),
    (Context("push to release", _push("release")), {"docs-build"}),
    # `workflow:` admits main and release by branch name, not by source, so a
    # scheduled or API-triggered pipeline on either behaves like a push to it.
    # That is cheap and harmless now that the gate jobs name their own refs;
    # before, it was another way to draw the full suite.
    (
        Context("scheduled run on main", {**_push("main"), "CI_PIPELINE_SOURCE": "schedule"}),
        {"docs-build"},
    ),
)

# Refs that get no pipeline at all.
UNADMITTED = (
    Context("push to a feature branch", _push("fix/22-ci-rule-matrix")),
    Context("push to a personal branch", _push("wip")),
    Context(
        "scheduled run on a feature branch",
        {**_push("wip"), "CI_PIPELINE_SOURCE": "schedule"},
    ),
)


# --------------------------------------------------------------------------
# Enough of GitLab's rule engine to evaluate this file.
# --------------------------------------------------------------------------


@dataclass
class _Reference:
    """An unresolved ``!reference [.job, key]``."""

    path: list[str] = field(default_factory=list)


class _CILoader(yaml.SafeLoader):
    """SafeLoader that keeps ``!reference`` tags instead of rejecting them."""


_CILoader.add_constructor(
    "!reference",
    lambda loader, node: _Reference(loader.construct_sequence(node)),
)


def load_ci() -> dict[str, Any]:
    return yaml.load(CI_FILE.read_text(encoding="utf-8"), Loader=_CILoader)


def _resolve(rules: list[Any], document: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ``!reference`` entries, the way GitLab splices them in."""
    resolved: list[dict[str, Any]] = []
    for entry in rules:
        if isinstance(entry, _Reference):
            target: Any = document
            for step in entry.path:
                target = target[step]
            resolved.extend(_resolve(target, document))
        else:
            resolved.append(entry)
    return resolved


_EQUALITY = re.compile(r'^\$(?P<var>\w+)\s*(?P<op>==|!=)\s*"(?P<value>[^"]*)"$')
_MATCH = re.compile(r"^\$(?P<var>\w+)\s*(?P<op>=~|!~)\s*/(?P<pattern>.*)/$")


def evaluate_if(expression: str, variables: dict[str, str]) -> bool:
    """Evaluate one ``if:`` expression. Unknown forms raise."""
    expression = expression.strip()
    equality = _EQUALITY.match(expression)
    if equality:
        actual = variables.get(equality["var"], "")
        equal = actual == equality["value"]
        return equal if equality["op"] == "==" else not equal
    match = _MATCH.match(expression)
    if match:
        # GitLab's =~ is a search, not a full match, and the pattern is written
        # with YAML-escaped slashes.
        found = re.search(match["pattern"], variables.get(match["var"], "")) is not None
        return found if match["op"] == "=~" else not found
    raise AssertionError(
        f"tests/test_ci_rules.py cannot evaluate {expression!r}. Teach it the "
        "new form rather than leaving the matrix unchecked."
    )


def _changes_match(patterns: list[str], changes: frozenset[str]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns for path in changes)


def job_runs(rules: list[dict[str, Any]], context: Context) -> bool:
    """Whether a job with these rules runs. First matching rule decides."""
    for rule in rules:
        if "if" in rule and not evaluate_if(rule["if"], context.variables):
            continue
        if "changes" in rule and not _changes_match(rule["changes"], context.changes):
            continue
        return rule.get("when", "on_success") != "never"
    return False


def jobs_for(context: Context) -> set[str]:
    """The product jobs a pipeline in this context would contain."""
    document = load_ci()
    if not job_runs(_resolve(document["workflow"]["rules"], document), context):
        return set()
    running = set()
    for name, body in document.items():
        if name in NOT_JOBS or name.startswith(".") or not isinstance(body, dict):
            continue
        rules = _resolve(body.get("rules", []), document)
        # A job with no rules of its own runs wherever the pipeline exists --
        # that inheritance is the defect this file guards against, so model it
        # faithfully rather than treating a missing list as "never".
        if not rules or job_runs(rules, context):
            running.add(name)
    return running


# --------------------------------------------------------------------------
# The tests.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("context", "expected"), MATRIX, ids=[context.name for context, _ in MATRIX]
)
def test_matrix(context: Context, expected: set[str]) -> None:
    assert jobs_for(context) == expected


@pytest.mark.parametrize("context", UNADMITTED, ids=[c.name for c in UNADMITTED])
def test_unadmitted_refs_get_no_pipeline(context: Context) -> None:
    assert jobs_for(context) == set()


def test_release_does_not_repeat_the_candidate_gate() -> None:
    """The defect itself: `release` fast-forwards onto a commit a candidate
    pipeline has already passed, so re-running the gate re-proves an identical
    tree."""
    candidate = jobs_for(Context("candidate", _push("release-candidate/v2.19.0")))
    release = jobs_for(Context("release", _push("release")))
    repeated = (candidate & release) - {"docs-build"}
    assert not repeated, (
        f"{sorted(repeated)} run on both the candidate branch and `release`. "
        "docs-build is the only job that may, because the deployment jobs in "
        "the external include publish the public/ it produces."
    )


def test_release_still_builds_the_site_to_deploy() -> None:
    """Removing the duplicate gate must not strand the deployment jobs: they
    publish `public/`, and only docs-build produces it."""
    assert "docs-build" in jobs_for(Context("release", _push("release")))


@pytest.mark.parametrize("job", PRODUCT_JOBS)
def test_every_product_job_states_its_own_refs(job: str) -> None:
    """Without a `rules:` key a job inherits the admission list, which is how
    `release` picked up the whole candidate gate."""
    document = load_ci()
    assert document[job].get("rules"), (
        f"{job} has no rules of its own, so it runs on every ref "
        "`workflow:` admits -- including `release`."
    )


@pytest.mark.parametrize(
    "context",
    [context for context, _ in MATRIX],
    ids=[context.name for context, _ in MATRIX],
)
def test_no_admitted_ref_yields_an_empty_pipeline(context: Context) -> None:
    """GitLab fails pipeline creation when every job is filtered out, so a ref
    `workflow:` admits must keep at least one job. Narrowing a rule until some
    admitted ref runs nothing turns a green push into a pipeline-creation
    error, which is a worse failure than the duplicate it replaced."""
    assert jobs_for(context), f"{context.name} is admitted but would run no jobs"


def test_the_gate_jobs_share_one_rule_list() -> None:
    """`test` and `qvf-conformance` are one gate. Two hand-maintained lists
    would let them drift apart, which is how a ref gets half a gate."""
    document = load_ci()
    for job in ("test", "qvf-conformance"):
        assert document[job].get("rules") == [
            _Reference([".rules_product_gate", "rules"])
        ], f"{job} no longer defers to .rules_product_gate"
