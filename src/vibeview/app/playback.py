"""Playback controllers for one browser viewer instance, and the scrubber.

The scrubber is a filmstrip of small frame thumbnails under the trajectory
slider, and a phase slider with a filmstrip of one period for a vibration.
Thumbnails are rendered server-side, off-screen, in a background task after
a section is activated, so the panel opens at once and the strip fills in;
a click on a thumbnail, or a drag across the strip, seeks.
"""

from __future__ import annotations

import asyncio
import base64
import math
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any

from vibeview.app import helpers as _helpers
from vibeview.qvf import QVFError

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime
    from vibeview.qvf import QVFReader, Section

#: How many frames the trajectory filmstrip shows at most: evenly spaced,
#: the first and the last frame always among them.
FILMSTRIP_FRAMES = 16
#: Steps of one period on the vibration phase scrubber; the filmstrip shows
#: every second step.
VIBRATION_PHASE_STEPS = 16


def filmstrip_frames(n_frames: int, limit: int = FILMSTRIP_FRAMES) -> list[tuple[int, int]]:
    """``(frame, last)`` pairs: each thumbnail's frame and the last frame it stands for.

    At most ``limit`` frames, evenly spaced over the trajectory with the
    first and the last frame always present; the ranges tile the
    trajectory, so the slider's frame always lights one thumbnail.
    """
    if n_frames <= 0:
        return []
    count = min(limit, n_frames)
    if count == 1:
        return [(0, n_frames - 1)]
    frames = sorted({round(i * (n_frames - 1) / (count - 1)) for i in range(count)})
    return [
        (frame, frames[i + 1] - 1 if i + 1 < len(frames) else n_frames - 1)
        for i, frame in enumerate(frames)
    ]


def vibration_phase(step: int, steps: int = VIBRATION_PHASE_STEPS) -> float:
    """The phase, in radians, of scrubber ``step``."""
    return 2.0 * math.pi * (int(step) % steps) / steps


def vibration_step(phase: float, steps: int = VIBRATION_PHASE_STEPS) -> int:
    """The scrubber step nearest to ``phase``."""
    return round(phase / (2.0 * math.pi) * steps) % steps


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def trajectory_filmstrip(
    reader: QVFReader, section: Section, *, limit: int = FILMSTRIP_FRAMES
) -> Generator[dict[str, Any], None, None]:
    """The filmstrip rows of a trajectory or reaction path, one thumbnail per ``next``.

    Each row is ``{"frame", "last", "label", "src"}`` with ``src`` a PNG
    data URL; see :func:`filmstrip_frames` for which frames. Nothing is
    yielded when the archive has no structure section or its atoms do not
    line up with the frames.
    """
    from vibeview.headless import structure_section, structure_thumbnail_writer
    from vibeview.renderers.reaction import ReactionPathRenderer
    from vibeview.renderers.trajectory import TrajectoryRenderer

    renderer: TrajectoryRenderer | ReactionPathRenderer = (
        ReactionPathRenderer(section, reader)
        if section.kind == "reaction.path"
        else TrajectoryRenderer(section, reader)
    )
    renderer.load()
    structure = structure_section(reader)
    if structure is None or len(reader.read_structure(structure.id).atoms) != len(
        renderer.load().atoms
    ):
        return
    with structure_thumbnail_writer(reader) as thumbnail:
        if thumbnail is None:
            return
        for frame, last in filmstrip_frames(renderer.n_frames, limit):
            positions = [pos for _symbol, pos in renderer.get_frame(frame)]
            yield {
                "frame": frame,
                "last": last,
                "label": f"Frame {frame + 1}",
                "src": _data_url(thumbnail(positions)),
            }


def vibration_filmstrip(
    reader: QVFReader,
    section: Section,
    mode: int,
    amplitude: float,
    *,
    steps: int = VIBRATION_PHASE_STEPS,
) -> Generator[dict[str, Any], None, None]:
    """The filmstrip rows of one normal mode across a period, every second step.

    Each row is ``{"step", "last", "label", "src"}``; the displacement is the
    one the viewer draws (:meth:`VibrationsRenderer.displace_atoms`).
    """
    from vibeview.headless import structure_section, structure_thumbnail_writer
    from vibeview.renderers.vibrations import VibrationsRenderer

    renderer = VibrationsRenderer(section, reader)
    renderer.load()
    if not 0 <= mode < renderer.n_modes:
        return
    structure = structure_section(reader)
    if structure is None or len(reader.read_structure(structure.id).atoms) != len(
        renderer.load().atoms
    ):
        return
    with structure_thumbnail_writer(reader) as thumbnail:
        if thumbnail is None:
            return
        for step in range(0, steps, 2):
            atoms = renderer.displace_atoms(mode, amplitude, phase=vibration_phase(step, steps))
            yield {
                "step": step,
                "last": min(step + 1, steps - 1),
                "label": f"Phase {step}/{steps}",
                "src": _data_url(thumbnail([pos for _symbol, pos in atoms])),
            }


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
                phase = float(self.viewer.server.state.vibration_phase)
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
                    self.viewer.server.state.vibration_phase_step = vibration_step(phase)
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
            # Pause freezes the motion at the scrubber step nearest the phase
            # the loop reached, so the slider and the scene agree; it used to
            # snap back to the rest geometry.
            self._freeze_vibration(vibration_step(float(self.viewer.server.state.vibration_phase)))

    def on_vibration_changed(self, mode: int, amplitude: float) -> None:
        state = self.viewer.server.state
        state.vibration_mode = mode
        state.vibration_amplitude = amplitude
        _helpers._render_vibration_atoms(
            self.viewer.reader, self.viewer.plotter, state, phase=float(state.vibration_phase)
        )
        self.refresh_vibration_filmstrip()

    # ── the scrubber ───────────────────────────────────────────────────

    def seek_energy_profile(self, fraction: Any) -> None:
        """Jump to the frame under a click on the energy profile.

        ``fraction`` is the click's x as a fraction of the image width; the
        renderer that drew the image maps it through the axes it laid out.
        """
        section = self._active_section(("trajectory", "reaction.path"))
        if section is None:
            return
        renderer = _helpers._frame_plot_renderer(self.viewer.reader, section)
        try:
            frame = renderer.frame_at_fraction(float(fraction))
        except (TypeError, ValueError, QVFError):
            return
        if frame is not None:
            self.set_trajectory_frame(int(frame))

    def vibration_seek(self, step: Any) -> None:
        """Freeze the mode at scrubber ``step`` of its period.

        The phase slider and a filmstrip click both land here; playback
        stops, and the frozen phase is the one the slider shows.
        """
        self.viewer.server.state.vibration_playing = False
        self._freeze_vibration(int(step) % VIBRATION_PHASE_STEPS)

    def vibration_step_phase(self, delta: Any) -> None:
        """Move the frozen phase by ``delta`` scrubber steps, wrapping (the [ and ] keys).

        The delta is applied here, to the server's step, so quick repeated
        presses accumulate instead of racing the client's copy of the state.
        """
        state = self.viewer.server.state
        self.vibration_seek(int(state.vibration_phase_step) + int(delta))

    def vibration_step_mode(self, delta: Any) -> None:
        """Select the previous or next normal mode (the < and > keys), clamped."""
        state = self.viewer.server.state
        n_modes = int(state.vibration_n_modes)
        if n_modes <= 0:
            return
        mode = min(max(int(state.vibration_mode) + int(delta), 0), n_modes - 1)
        if mode != int(state.vibration_mode):
            self.on_vibration_changed(mode, float(state.vibration_amplitude))

    def _freeze_vibration(self, step: int) -> None:
        state = self.viewer.server.state
        state.vibration_phase_step = step
        state.vibration_phase = vibration_phase(step)
        _helpers._render_vibration_atoms(
            self.viewer.reader, self.viewer.plotter, state, phase=float(state.vibration_phase)
        )

    def cancel_filmstrips(self) -> None:
        """Supersede any filmstrip still rendering, on a section switch."""
        self.viewer._filmstrip_epoch += 1
        self.viewer._vib_filmstrip_epoch += 1
        for name in ("_filmstrip_task", "_vib_filmstrip_task"):
            task = getattr(self.viewer, name)
            if task is not None:
                task.cancel()
                setattr(self.viewer, name, None)

    def _active_section(self, kinds: tuple[str, ...]) -> Section | None:
        section_id = self.viewer.server.state.selected_section
        section = next((s for s in self.viewer.reader.sections if s.id == section_id), None)
        return section if section is not None and section.kind in kinds else None

    def refresh_trajectory_filmstrip(self) -> None:
        """Render the active trajectory's filmstrip in the background."""
        state = self.viewer.server.state
        self.viewer._filmstrip_epoch += 1
        state.trajectory_thumbnails = []
        section = self._active_section(("trajectory", "reaction.path"))
        if section is None or state.trajectory_n_frames <= 0:
            return
        self.viewer._filmstrip_task = _helpers._spawn_task(
            self._fill_filmstrip(
                "trajectory_thumbnails",
                trajectory_filmstrip(self.viewer.reader, section),
                self.viewer._filmstrip_epoch,
                lambda: self.viewer._filmstrip_epoch,
            )
        )

    def refresh_vibration_filmstrip(self) -> None:
        """Render the current mode's period filmstrip in the background."""
        state = self.viewer.server.state
        self.viewer._vib_filmstrip_epoch += 1
        state.vibration_thumbnails = []
        section = self._active_section(("vibrations",))
        if section is None or state.vibration_n_modes <= 0:
            return
        self.viewer._vib_filmstrip_task = _helpers._spawn_task(
            self._fill_filmstrip(
                "vibration_thumbnails",
                vibration_filmstrip(
                    self.viewer.reader,
                    section,
                    int(state.vibration_mode),
                    float(state.vibration_amplitude),
                ),
                self.viewer._vib_filmstrip_epoch,
                lambda: self.viewer._vib_filmstrip_epoch,
            )
        )

    async def _fill_filmstrip(
        self,
        key: str,
        rows: Generator[dict[str, Any], None, None],
        epoch: int,
        current: Callable[[], int],
    ) -> None:
        """Pull ``rows`` one thumbnail at a time into ``state[key]``.

        Each ``next`` renders one thumbnail synchronously; between two the
        loop yields, and a newer request (``epoch`` behind ``current()``)
        ends the strip where it is. Closing the generator releases its
        plotter.
        """
        state = self.viewer.server.state
        shown: list[dict[str, Any]] = []
        try:
            while epoch == current():
                try:
                    row = next(rows)
                except StopIteration:
                    return
                except QVFError as exc:
                    state.status_message = f"Filmstrip unavailable: {exc}"
                    return
                shown.append(row)
                setattr(state, key, list(shown))
                state.flush()
                await asyncio.sleep(0)
        finally:
            rows.close()
