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

The evaluator covers the ``if:`` forms these files use: variable truthiness,
``||``, literal comparisons and regex matches. Anything else raises rather
than evaluating false, so an expression it cannot read fails the suite
instead of quietly reporting that no job runs.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest
import yaml

VIEWER_DIR = Path(__file__).resolve().parents[1]
CI_FILE = VIEWER_DIR / ".gitlab-ci.yml"
DEPLOY_RULES_FILE = VIEWER_DIR / ".gitlab" / "ci" / "deploy-rules.yml"

# Top-level keys that configure the pipeline rather than declare a job.
NOT_JOBS = {"include", "stages", "variables", "workflow", "default", "image"}

# The product jobs this repository owns. Deployment jobs come from the
# external include and are not visible here.
PRODUCT_JOBS = ("lint", "types", "test", "qvf-conformance", "docs-build")

# The jobs that make up the one per-change gate. They must agree on the refs
# they run on, so they all defer to `.rules_product_gate` (#8, #22).
GATE_JOBS = ("lint", "types", "test", "qvf-conformance")


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
#   ref / source          lint  types  test  qvf-conformance  docs-build
#   --------------------  ----  -----  ----  ---------------  ----------
#   merge request          yes    yes   yes        yes         on change
#   release-candidate/*    yes    yes   yes        yes            yes
#   web (manual)           yes    yes   yes        yes            yes
#   main                    no     no    no         no            yes
#   release                 no     no    no         no            yes
GATE = {"lint", "types", "test", "qvf-conformance"}

MATRIX: tuple[tuple[Context, set[str]], ...] = (
    (
        Context("merge request touching the docs", MERGE_REQUEST, DOCS_CHANGE),
        GATE | {"docs-build"},
    ),
    (
        Context("merge request touching only code", MERGE_REQUEST, CODE_CHANGE),
        set(GATE),
    ),
    (
        Context("release candidate", _push("release-candidate/v2.19.0")),
        GATE | {"docs-build"},
    ),
    (
        Context("manual web run on main", {**_push("main"), "CI_PIPELINE_SOURCE": "web"}),
        GATE | {"docs-build"},
    ),
    (
        Context(
            "manual web run on release",
            {**_push("release"), "CI_PIPELINE_SOURCE": "web"},
        ),
        GATE | {"docs-build"},
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
    if " || " in expression:
        return any(evaluate_if(part, variables) for part in expression.split(" || "))
    if re.fullmatch(r"\$\w+", expression):
        return bool(variables.get(expression[1:], ""))
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
    # GitLab's recursive **/ also matches zero directories; fnmatch alone
    # misses a top-level docs/index.md for docs/**/*.
    return any(
        fnmatch(path, pattern) or fnmatch(path, pattern.replace("**/", ""))
        for pattern in patterns
        for path in changes
    )


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


def test_the_static_analysis_jobs_run_the_configured_tools() -> None:
    """The point of #8 is that a new finding fails the pipeline, so the jobs
    have to invoke the tools in the mode `pyproject.toml` configures: `ruff
    check` over the whole checkout, and a bare `mypy` that picks up the
    `files` ramp. A `|| true`, a `--exit-zero` or a narrowed path list would
    leave the jobs green and the gate imaginary. The path list is the
    documented trap: `tests/ src/ scripts/` left `electron/desktop-launcher.py`
    and the `docs/*.py` scripts unlinted, and two sessions measuring the
    two scopes read a phantom regression off the difference."""
    document = load_ci()
    assert document["lint"]["script"] == ["ruff check ."]
    assert document["types"]["script"] == ["mypy"]


def test_the_type_ramp_names_files_that_exist() -> None:
    """`[tool.mypy].files` is the enforced set. A path that no longer exists
    fails the `types` job with a confusing error and, worse, a module renamed
    out of the list silently stops being checked."""
    pyproject = tomllib.loads((VIEWER_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    ramp = pyproject["tool"]["mypy"]["files"]
    assert ramp, "the type ramp is empty, so the `types` job checks nothing"
    missing = [path for path in ramp if not (VIEWER_DIR / path).is_file()]
    assert not missing, f"[tool.mypy].files names paths that do not exist: {missing}"


def test_the_gate_jobs_share_one_rule_list() -> None:
    """`lint`, `types`, `test` and `qvf-conformance` are one gate. Four
    hand-maintained lists would let them drift apart, which is how a ref gets
    half a gate."""
    document = load_ci()
    for job in GATE_JOBS:
        assert document[job].get("rules") == [
            _Reference([".rules_product_gate", "rules"])
        ], f"{job} no longer defers to .rules_product_gate"


# These are actual site inputs: package metadata supplies the docs extra and
# version; autodoc imports the public API and package re-exports, and qvf loads
# both schemas at import time. The docs glob also owns conf/theme/static assets.
@pytest.mark.parametrize(
    "path",
    (
        "pyproject.toml",
        ".gitlab/ci/deploy-rules.yml",
        "docs/index.md",
        "docs/conf.py",
        "docs/Makefile",
        "docs/_static/custom.css",
        "docs/_templates/autosummary/module.rst",
        "scripts/build_site.sh",
        "src/vibeview/__init__.py",
        "src/vibeview/api.py",
        "src/vibeview/qvf.py",
        "src/vibeview/kinds.py",
        "src/vibeview/codenames.py",
        "src/vibeview/launcher.py",
        "src/vibeview/schema.json",
        "src/vibeview/schema_v2.json",
    ),
)
def test_docs_input_only_merge_request_builds_the_site(path: str) -> None:
    assert jobs_for(Context(path, MERGE_REQUEST, frozenset({path}))) == GATE | {
        "docs-build"
    }


def test_every_autosummary_module_triggers_a_docs_build() -> None:
    source = (VIEWER_DIR / "docs" / "python_api.md").read_text(encoding="utf-8")
    modules = re.findall(r"^   (vibeview\.[a-z_]+)$", source, re.MULTILINE)
    assert modules, "the documented API module list must not silently disappear"
    for module in modules:
        path = "src/" + module.replace(".", "/") + ".py"
        assert "docs-build" in jobs_for(Context(path, MERGE_REQUEST, frozenset({path})))


@pytest.mark.parametrize("path", ("src/vibeview/app.py", "tests/test_controller_state.py"))
def test_unrelated_code_only_merge_request_keeps_the_docs_filter(path: str) -> None:
    assert jobs_for(Context(path, MERGE_REQUEST, frozenset({path}))) == GATE


@pytest.mark.parametrize(
    ("variables", "enabled"),
    (
        ({}, False),
        ({"VIBE_VIEW_CI_PROJECT": "example/deployment"}, True),
        ({"VIBE_VIEW_CI_REF": "main"}, True),
        ({"VIBE_VIEW_CI_FILE": "/viewer.yml"}, True),
        (
            {
                "VIBE_VIEW_CI_PROJECT": "example/deployment",
                "VIBE_VIEW_CI_REF": "main",
                "VIBE_VIEW_CI_FILE": "/viewer.yml",
            },
            True,
        ),
    ),
)
def test_deploy_override_is_admitted_only_with_the_external_job(
    variables: dict[str, str], enabled: bool
) -> None:
    includes = load_ci()["include"]
    assert len(includes) == 2
    external, local = includes
    assert "project" in external
    assert local["local"] == "/.gitlab/ci/deploy-rules.yml"
    context = Context("operator configuration", variables)
    assert job_runs(external["rules"], context) is enabled
    assert job_runs(local["rules"], context) is enabled
    # Loading neither leaves portable CI without a script-less deploy job;
    # loading both merges the local rules after the real external job.
    assert "docs-deploy" not in load_ci()


@pytest.mark.parametrize(
    ("branch", "source", "expected"),
    (
        ("main", "push", None),
        ("release", "push", "on_success"),
        ("release-candidate/vNext", "push", None),
        ("main", "web", "manual"),
        ("release", "web", "manual"),
        ("feature/topic", "web", "manual"),
        ("", "merge_request_event", None),
        ("release", "schedule", "on_success"),
    ),
)
def test_product_deploy_rules_preserve_manual_web_on_every_ref(
    branch: str, source: str, expected: str | None
) -> None:
    override = yaml.safe_load(DEPLOY_RULES_FILE.read_text(encoding="utf-8"))
    assert set(override) == {"docs-deploy"}
    # Commands/environment must remain owned by the configured include.
    assert set(override["docs-deploy"]) == {"rules"}
    variables = {"CI_COMMIT_BRANCH": branch, "CI_PIPELINE_SOURCE": source}
    selected = next(
        (
            rule
            for rule in override["docs-deploy"]["rules"]
            if evaluate_if(rule["if"], variables)
        ),
        None,
    )
    actual = selected.get("when", "on_success") if selected else None
    assert actual == expected
    if expected == "manual":
        assert selected is not None and selected["allow_failure"] is True
