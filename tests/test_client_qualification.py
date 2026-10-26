"""The client performance qualification (#114): its verdicts, its limits, the
candidate job that runs it, and the renderer string the About dialog shows.

The harness drives a real browser, which this suite does not; the candidate
job runs it, and ``docs/client_performance.md`` records its measurements.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cq():
    spec = importlib.util.spec_from_file_location(
        "client_qualification", ROOT / "scripts" / "client_qualification.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def thresholds(cq):
    return json.loads(cq.THRESHOLDS.read_text(encoding="utf-8"))


class TestRendererClass:
    @pytest.mark.parametrize(
        "renderer",
        [
            "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (LLVM 10.0.0)), SwiftShader driver)",
            "llvmpipe (LLVM 17.0.6, 256 bits)",
            "ANGLE (Microsoft, Microsoft Basic Render Driver Direct3D11 vs_5_0 ps_5_0)",
            "Mesa softpipe",
        ],
    )
    def test_software_rasterisers(self, cq, renderer):
        assert cq.renderer_class(renderer) == "software"

    @pytest.mark.parametrize(
        "renderer",
        [
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M4 Pro, Unspecified Version)",
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "Mesa Intel(R) UHD Graphics 620 (KBL GT2)",
        ],
    )
    def test_gpus(self, cq, renderer):
        assert cq.renderer_class(renderer) == "gpu"


class TestSummary:
    def test_drops_the_warm_up_steps(self, cq):
        result = cq.summary([100.0, 90.0, 80.0] + [2.0] * 10)
        assert result["median_ms"] == 2.0 and result["steps"] == 10.0
        assert result["fps"] == pytest.approx(500.0)

    def test_p95_is_a_high_step(self, cq):
        result = cq.summary([1.0] * 19 + [9.0] * 2)
        assert result["p95_ms"] == 9.0

    def test_no_steps(self, cq):
        assert cq.summary([]) is None


class TestVerdicts:
    def _all_pass(self, cq):
        return {
            case: ({"median_ms": 1.0, "p95_ms": 2.0, "fps": 1000.0, "steps": 50.0}
                   if case.startswith("drag.") else {"seconds": 0.5})
            for case in cq.CASES
        }

    def test_everything_inside_its_limits_passes(self, cq, thresholds):
        verdicts = cq.evaluate(self._all_pass(cq), thresholds, "gpu")
        assert [v["case"] for v in verdicts] == list(cq.CASES)
        assert all(v["ok"] for v in verdicts)

    def test_a_slow_drag_fails_on_its_p95(self, cq, thresholds):
        cases = self._all_pass(cq)
        cases["drag.structure"]["p95_ms"] = 40.0
        failed = [v["case"] for v in cq.evaluate(cases, thresholds, "gpu") if not v["ok"]]
        assert failed == ["drag.structure"]

    def test_a_case_that_did_not_run_fails(self, cq, thresholds):
        cases = self._all_pass(cq)
        cases["switch.volume"] = {"error": "TimeoutError: no scene"}
        del cases["drag.outlines"]
        failed = {v["case"] for v in cq.evaluate(cases, thresholds, "gpu") if not v["ok"]}
        assert failed == {"switch.volume", "drag.outlines"}

    def test_a_null_limit_reports_without_gating(self, cq, thresholds):
        cases = self._all_pass(cq)
        cases["drag.labels"] = {"median_ms": 900.0, "p95_ms": 1200.0, "fps": 1.1, "steps": 5.0}
        verdicts = {v["case"]: v for v in cq.evaluate(cases, thresholds, "software")}
        assert verdicts["drag.labels"]["limit"] is None and verdicts["drag.labels"]["ok"]
        assert verdicts["drag.labels"]["value"] == 1200.0


class TestThresholds:
    def test_every_case_has_a_limit_in_both_classes(self, cq, thresholds):
        assert thresholds["version"] == 1
        assert set(thresholds["classes"]) == {"gpu", "software"}
        for env_class in ("gpu", "software"):
            for case in cq.CASES:
                cq.limit_for(thresholds, env_class, case)  # resolves, or raises

    def test_the_gpu_bar_is_interactive(self, cq, thresholds):
        for case in cq.CASES:
            limit = cq.limit_for(thresholds, "gpu", case)
            assert limit is not None, case
            if case.startswith("drag."):
                assert limit <= 33.4, (case, limit)

    def test_software_drags_are_looser_than_gpu(self, cq, thresholds):
        for case in cq.CASES:
            software = cq.limit_for(thresholds, "software", case)
            gpu = cq.limit_for(thresholds, "gpu", case)
            # Scene switches are independently measured server work; their
            # software bound need not inherit the GPU's historical allowance.
            if case.startswith("drag."):
                assert software is None or software >= gpu, case
            else:
                assert software is not None and software > 0, case


class TestScenes:
    def test_the_molecule_has_150_atoms(self, cq):
        lines = cq.molecule_xyz().decode().splitlines()
        assert lines[0] == "150" and len(lines) == 152

    def test_the_density_archive_opens(self, cq, tmp_path):
        from vibeview.qvf import QVFReader

        with QVFReader(cq.density_archive(tmp_path / "d.qvf", n=16)) as reader:
            section = reader.get_section("density")
            assert section.kind == "volume.density"
            assert (section.model_extra or {}).get("label") == "Electron Density"


class TestCandidateJob:
    @pytest.fixture(scope="class")
    def job(self):
        yaml = pytest.importorskip("yaml")

        class Loader(yaml.SafeLoader):
            pass

        Loader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))
        config = yaml.load((ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"), Loader=Loader)
        return config["client-performance"]

    def test_it_runs_the_qualification_as_a_gate_and_keeps_the_result(self, job):
        script = " ".join(job["script"])
        assert "scripts/client_qualification.py" in script and "--gate" in script
        assert "playwright install --with-deps chromium" in script
        assert "test-results/client-performance.json" in job["artifacts"]["paths"]

    def test_it_gates_3x_candidates_and_reports_on_2x(self, job):
        import re

        rules = job["rules"]
        assert [rule["allow_failure"] for rule in rules] == [False, True]

        def first_rule(branch):
            for rule in rules:
                literal = rule["if"].split("=~", 1)[1].strip()
                assert literal.startswith("/") and literal.endswith("/"), literal
                if re.search(literal[1:-1].replace("\\/", "/"), branch):
                    return rule
            return None

        assert first_rule("release-candidate/v3.0.0")["allow_failure"] is False
        assert first_rule("release-candidate/v2.20.20")["allow_failure"] is True
        assert first_rule("main") is None


@pytest.fixture(scope="module")
def served():
    pytest.importorskip("trame")
    from tests._served import served_viewer

    return served_viewer()


class TestAboutDialog:
    def test_it_shows_the_browser_graphics(self, served):
        assert "Browser graphics: {{ client_graphics || 'unknown' }}" in served
        assert "window.vvGraphicsInfo = probe();" in served
        assert "state.set('client_graphics', window.vvGraphicsInfo)" in served

    def test_the_state_key_starts_empty(self):
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        app = create_app(QVFReader(ROOT / "tests" / "data" / "h2_natural_orbitals.qvf"))
        assert app.state.client_graphics == ""
