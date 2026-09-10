"""B3: freeze-atom constraints, at the controller layer.

`tests/test_live_opt.py` already pins the worker end (frozen coordinates
survive a relax; all-frozen is refused). What was untested is the UI that
populates `frozen`: the freeze/unfreeze controllers, index invalidation
after a structural edit, and whether the set actually reaches the request.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

XYZ = b"""4
chain
H 0.0 0.0 0.0
H 1.0 0.0 0.0
H 2.0 0.0 0.0
H 3.0 0.0 0.0
"""


def _app(xyz: bytes = XYZ):
    from vibeview.app import create_app
    from vibeview.converters import xyz_to_qvf
    from vibeview.qvf import QVFReader

    p = Path(tempfile.mktemp(suffix=".qvf"))
    p.write_bytes(xyz_to_qvf(xyz).getvalue())
    try:
        return create_app(QVFReader(p))
    finally:
        p.unlink(missing_ok=True)


class TestFreezeSelected:
    def test_nothing_selected_says_so_and_freezes_nothing(self):
        app = _app()
        app.controller.edit_freeze_selected()
        assert list(app.state.frozen_atoms) == []
        assert "Select atoms first" in app.state.status_message

    def test_freezes_the_selection_and_clears_it(self):
        app = _app()
        app.state.edit_selected = [0, 2]
        app.controller.edit_freeze_selected()
        assert list(app.state.frozen_atoms) == [0, 2]
        assert list(app.state.edit_selected) == [], "selection should clear"

    def test_is_additive_across_calls(self):
        app = _app()
        app.state.edit_selected = [0]
        app.controller.edit_freeze_selected()
        app.state.edit_selected = [3]
        app.controller.edit_freeze_selected()
        assert list(app.state.frozen_atoms) == [0, 3]

    def test_refreezing_does_not_duplicate(self):
        app = _app()
        app.state.edit_selected = [1]
        app.controller.edit_freeze_selected()
        app.state.edit_selected = [1]
        app.controller.edit_freeze_selected()
        assert list(app.state.frozen_atoms) == [1]

    def test_out_of_range_selection_is_dropped(self):
        """A stale index must not enter the frozen set — the worker would
        silently hold the wrong atom, or none."""
        app = _app()
        app.state.edit_selected = [1, 99, -1]
        app.controller.edit_freeze_selected()
        assert list(app.state.frozen_atoms) == [1]

    def test_result_is_sorted(self):
        app = _app()
        app.state.edit_selected = [3, 0, 2]
        app.controller.edit_freeze_selected()
        assert list(app.state.frozen_atoms) == sorted(app.state.frozen_atoms)


class TestUnfreeze:
    def test_clears_everything(self):
        app = _app()
        app.state.edit_selected = [0, 1]
        app.controller.edit_freeze_selected()
        app.controller.edit_unfreeze_all()
        assert list(app.state.frozen_atoms) == []
        assert "Unfroze" in app.state.status_message

    def test_is_a_no_op_when_nothing_is_frozen(self):
        app = _app()
        before = app.state.status_message
        app.controller.edit_unfreeze_all()
        assert list(app.state.frozen_atoms) == []
        assert app.state.status_message == before


class TestIndicesInvalidatedByStructuralEdits:
    """Freezing is by index, so anything that reindexes atoms must clear it
    or the constraint silently moves to a different atom."""

    def test_delete_resets_frozen(self):
        app = _app()
        app.state.edit_selected = [3]
        app.controller.edit_freeze_selected()
        assert list(app.state.frozen_atoms) == [3]
        app.state.edit_selected = [0]
        app.controller.edit_delete_selected()
        assert list(app.state.frozen_atoms) == [], (
            "delete reindexes atoms; a stale frozen index now names another atom"
        )

    def test_undo_resets_frozen(self):
        app = _app()
        app.state.edit_selected = [0]
        app.controller.edit_delete_selected()
        app.state.edit_selected = [1]
        app.controller.edit_freeze_selected()
        app.controller.edit_undo()
        assert list(app.state.frozen_atoms) == []


class TestReachesTheRequest:
    def test_frozen_set_is_carried_into_the_wire_request(self):
        """The end-to-end point of B3: what the user froze must arrive in
        the payload the worker reads."""
        from vibeview.live_opt import build_request

        app = _app()
        app.state.edit_selected = [0, 2]
        app.controller.edit_freeze_selected()
        req = build_request(
            ["H"] * 4,
            [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]],
            frozen=list(app.state.frozen_atoms),
        )
        assert req["frozen"] == [0, 2]

    def test_all_frozen_is_refused_before_scheduling(self):
        """The worker raises on an all-frozen request, so the app must never
        send one — and must say why rather than sitting silent.

        Freezing runs the scheduler itself, so enabling auto-optimize first
        means the freeze call is what trips the guard.
        """
        app = _app()
        app.state.live_opt_enabled = True
        app.state.edit_selected = [0, 1, 2, 3]
        app.controller.edit_freeze_selected()
        assert len(app.state.frozen_atoms) == 4
        assert "all atoms frozen" in str(app.state.live_opt_status).lower(), (
            app.state.live_opt_status
        )

    def test_unfreezing_clears_the_refusal(self):
        """Releasing atoms must let the next relax through, not leave the
        structure permanently marked unoptimizable."""
        app = _app()
        app.state.live_opt_enabled = True
        app.state.edit_selected = [0, 1, 2, 3]
        app.controller.edit_freeze_selected()
        assert "all atoms frozen" in str(app.state.live_opt_status).lower()
        app.controller.edit_unfreeze_all()
        assert list(app.state.frozen_atoms) == []


class TestStatusMessageCountsWhatChanged:
    def test_reports_newly_frozen_not_selected(self):
        app = _app()
        app.state.edit_selected = [0]
        app.controller.edit_freeze_selected()
        app.state.edit_selected = [0, 1]
        app.controller.edit_freeze_selected()
        # Only atom 1 is new; saying "2" would overstate the effect.
        assert "Froze 1 atom(s)" in app.state.status_message

    def test_says_already_frozen_when_nothing_changed(self):
        app = _app()
        app.state.edit_selected = [2]
        app.controller.edit_freeze_selected()
        app.state.edit_selected = [2]
        app.controller.edit_freeze_selected()
        assert "Already frozen" in app.state.status_message
        assert list(app.state.frozen_atoms) == [2]

    def test_out_of_range_only_selection_is_not_reported_as_frozen(self):
        app = _app()
        app.state.edit_selected = [99]
        app.controller.edit_freeze_selected()
        assert list(app.state.frozen_atoms) == []
        assert "Already frozen" in app.state.status_message
