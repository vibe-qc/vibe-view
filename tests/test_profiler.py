"""The in-process profiler (`vibeview.profiler`)."""

from __future__ import annotations


class TestProfiler:
    def test_profiler_basic(self):
        from vibeview.profiler import profiler

        profiler.reset()
        with profiler.measure("test_op"):
            _ = sum(range(1000))
        stats = profiler.get_stats()
        assert len(stats) >= 1
        assert stats[0]["name"] == "test_op"

    def test_profiler_summary(self):
        from vibeview.profiler import profiler

        profiler.reset()
        with profiler.measure("op_a"):
            pass
        summary = profiler.get_summary()
        assert "op_a" in summary
