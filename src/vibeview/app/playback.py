"""Playback controllers for one browser viewer instance."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from vibeview.app import helpers as _helpers

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime


class PlaybackControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    def set_trajectory_frame(self, frame: int) -> None:
        state = self.viewer.server.state
        state.trajectory_frame = frame
        _helpers._update_trajectory_plot(self.viewer.reader, state)
        _helpers._render_trajectory_atoms(self.viewer.reader, self.viewer.plotter, state)
        _helpers._update_reaction_label(self.viewer.reader, state)

    def toggle_play(self) -> None:
        state = self.viewer.server.state
        state.trajectory_playing = not state.trajectory_playing

    def step_frame(self, delta: int) -> None:
        state = self.viewer.server.state
        n_frames = state.trajectory_n_frames
        if n_frames > 0:
            new_frame = state.trajectory_frame + delta
            if new_frame < 0:
                new_frame = n_frames - 1
            elif new_frame >= n_frames:
                new_frame = 0
            state.trajectory_frame = new_frame
            _helpers._update_trajectory_plot(self.viewer.reader, state)
            _helpers._render_trajectory_atoms(self.viewer.reader, self.viewer.plotter, state)
            _helpers._update_reaction_label(self.viewer.reader, state)

    def _on_play_toggle(self, trajectory_playing: bool, **kwargs: Any) -> None:
        self.viewer._anim_epoch += 1
        if trajectory_playing:
            my_epoch = self.viewer._anim_epoch

            async def _loop() -> None:
                while self.viewer.server.state.trajectory_playing:
                    await asyncio.sleep(0.2)
                    # A section switch / reset can flip playing off and rebuild
                    # the scene during the sleep. `.cancel()` only fires at the
                    # next await, so without this guard the current iteration
                    # would render + flush one stale frame onto the reset scene
                    # (audit L3, one-frame flicker). Bail if superseded.
                    if (
                        my_epoch != self.viewer._anim_epoch
                        or not self.viewer.server.state.trajectory_playing
                    ):
                        return
                    frame = self.viewer.server.state.trajectory_frame
                    n = self.viewer.server.state.trajectory_n_frames or 1
                    self.viewer.server.state.trajectory_frame = (frame + 1) % n
                    _helpers._update_trajectory_plot(self.viewer.reader, self.viewer.server.state)
                    _helpers._render_trajectory_atoms(
                        self.viewer.reader, self.viewer.plotter, self.viewer.server.state
                    )
                    _helpers._update_reaction_label(self.viewer.reader, self.viewer.server.state)
                    self.viewer.server.state.flush()

            self.viewer._anim_task = _helpers._spawn_task(_loop())
        else:
            if self.viewer._anim_task is not None:
                self.viewer._anim_task.cancel()
                self.viewer._anim_task = None

    def vibration_play_toggle(self) -> None:
        self.viewer.server.state.vibration_playing = not self.viewer.server.state.vibration_playing

    def _on_vibration_play_toggle(self, vibration_playing: bool, **kwargs: Any) -> None:
        self.viewer._vib_epoch += 1
        if vibration_playing:
            my_epoch = self.viewer._vib_epoch

            async def _vib_loop() -> None:
                import math

                phase = 0.0
                while self.viewer.server.state.vibration_playing:
                    await asyncio.sleep(0.05)
                    # Bail if a section switch / reset superseded us during the
                    # sleep, before painting a stale frame (audit L3).
                    if (
                        my_epoch != self.viewer._vib_epoch
                        or not self.viewer.server.state.vibration_playing
                    ):
                        return
                    phase = (phase + 0.25) % (2 * math.pi)
                    self.viewer.server.state.vibration_phase = phase
                    _helpers._render_vibration_atoms(
                        self.viewer.reader,
                        self.viewer.plotter,
                        self.viewer.server.state,
                        phase=phase,
                    )
                    self.viewer.server.state.flush()

            self.viewer._vib_anim_task = _helpers._spawn_task(_vib_loop())
        elif self.viewer._vib_anim_task is not None:
            self.viewer._vib_anim_task.cancel()
            self.viewer._vib_anim_task = None
            # Snap back to phase 0 (rest position)
            self.viewer.server.state.vibration_phase = 0.0
            _helpers._render_vibration_atoms(
                self.viewer.reader, self.viewer.plotter, self.viewer.server.state, phase=0.0
            )

    def on_vibration_changed(self, mode: int, amplitude: float) -> None:
        state = self.viewer.server.state
        state.vibration_mode = mode
        state.vibration_amplitude = amplitude
        _helpers._render_vibration_atoms(self.viewer.reader, self.viewer.plotter, state)
