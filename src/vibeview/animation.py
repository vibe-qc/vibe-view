"""Video/animation export — render animated sections to MP4/GIF.

Requires ``ffmpeg`` on PATH for MP4 output.  Falls back to animated GIF
(via Pillow) when ffmpeg is not available.

Used by ``vibe-view animate`` CLI command.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibeview.headless import (
    add_signed_isosurfaces,
    add_structure,
    frame,
    headless_plotter,
    snapshot,
    structure_frame_writer,
)

if TYPE_CHECKING:

    from vibeview.qvf import QVFReader


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _frames_to_mp4(
    frame_dir: Path,
    output: Path,
    fps: float = 10,
    crf: int = 18,
    preset: str = "medium",
) -> bool:
    """Combine numbered PNG frames into an MP4 video using ffmpeg.

    Parameters
    ----------
    crf : Constant Rate Factor (0-51, lower = better quality, 18 is visually lossless).
    preset : x264 speed preset (ultrafast / superfast / veryfast / faster / fast /
             medium / slow / slower / veryslow / placebo).
    """
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-framerate",
                str(fps),
                "-i",
                str(frame_dir / "frame_%04d.png"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                str(crf),
                "-preset",
                preset,
                "-movflags",
                "+faststart",
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",  # ensure even dimensions
                str(output),
            ],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _frames_to_gif(frame_dir: Path, output: Path, fps: float = 10) -> bool:
    """Combine numbered PNG frames into an animated GIF."""
    try:
        from PIL import Image
    except ImportError:
        return False
    frames = sorted(frame_dir.glob("frame_*.png"))
    if not frames:
        return False
    duration = int(1000 / fps)
    with ExitStack() as stack:
        images: list[Image.Image] = []
        for path in frames:
            # Detach the decoded pixels before opening the next PNG, so long
            # animations do not retain one file descriptor per frame.
            with Image.open(path) as source:
                image = source.copy()
                stack.callback(image.close)
            images.append(image)
        images[0].save(
            str(output),
            save_all=True,
            append_images=images[1:],
            duration=duration,
            loop=0,
            optimize=True,
            disposal=2,
        )
    return True


def _frames_to_pngs(frame_dir: Path, output_dir: Path) -> bool:
    """Copy numbered PNG frames to an output directory (individual frames)."""
    frames = sorted(frame_dir.glob("frame_*.png"))
    if not frames:
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    for f in frames:
        dst = output_dir / f.name
        shutil.copy2(f, dst)
    return True


class AnimationEncodeError(RuntimeError):
    """Frames were rendered but could not be written in the requested format."""


# Suffixes the animation writers own; any other suffix is part of the name.
_ANIMATION_SUFFIXES = (".mp4", ".gif")


def _without_animation_suffix(output: Path) -> Path:
    """``output`` without a trailing ``.mp4``/``.gif``, and nothing else.

    ``Path.with_suffix("")`` strips whatever follows the last dot, so an
    output named ``job.opt_trajectory`` became ``job`` and frames landed in
    ``./job/`` (#39-4).
    """
    if output.suffix.lower() in _ANIMATION_SUFFIXES:
        return output.with_suffix("")
    return output


def _encode_frames(frame_dir: Path, output: Path, format: str, fps: float) -> Path:
    """Write rendered ``frame_*.png`` files as ``format``; return what was written.

    ``mp4`` needs ffmpeg and falls back to a GIF without it; ``frames``
    copies the PNGs into a directory. Raises :class:`AnimationEncodeError`
    when nothing could be written (no frames, Pillow missing, ffmpeg
    failing): a path to a file that does not exist used to be returned, and
    the CLI then crashed on it (#39-3).
    """
    if format == "mp4" and _ffmpeg_available():
        if _frames_to_mp4(frame_dir, output, fps=fps) and output.exists():
            return output
        raise AnimationEncodeError(f"ffmpeg could not write {output}")
    if format == "frames":
        out = _without_animation_suffix(output)
        if _frames_to_pngs(frame_dir, out):
            return out
        raise AnimationEncodeError(f"no frames were rendered for {out}")
    if format in ("gif", "mp4"):
        out = _without_animation_suffix(output)
        out = out.with_name(out.name + ".gif")
        if _frames_to_gif(frame_dir, out, fps=fps) and out.exists():
            return out
        raise AnimationEncodeError(
            f"could not write {out}: no frames were rendered, or Pillow is not installed"
        )
    raise ValueError(f"unknown animation format {format!r}; use mp4, gif or frames")


def render_trajectory_video(
    reader: QVFReader,
    output: str | Path,
    *,
    fps: float = 5,
    size: tuple[int, int] = (900, 600),
    format: str = "mp4",
) -> Path | None:
    """Render a geometry-optimisation trajectory as an MP4/GIF video.

    Returns the output path on success, None if no trajectory section exists.
    """
    from vibeview.renderers.trajectory import TrajectoryRenderer

    section = next((s for s in reader.sections if s.kind == "trajectory"), None)
    if section is None:
        return None

    renderer = TrajectoryRenderer(section, reader)
    data = renderer.load()
    n_frames = data.coords.shape[0]
    output = Path(output)

    with tempfile.TemporaryDirectory() as td:
        frame_dir = Path(td)

        with structure_frame_writer(reader, size=size) as write:
            if write is None:
                return None
            for i in range(n_frames):
                write(frame_dir / f"frame_{i:04d}.png", data.coords[i])

        return _encode_frames(frame_dir, output, format, fps)


def render_reaction_video(
    reader: QVFReader,
    output: str | Path,
    *,
    fps: float = 5,
    size: tuple[int, int] = (900, 600),
    format: str = "mp4",
) -> Path | None:
    """Render a ``reaction.path`` (NEB band) as an MP4/GIF video.

    Plays the band image-by-image — reactant → transition state →
    product. Returns the output path on success, ``None`` if the archive
    has no ``reaction.path`` section.
    """
    import numpy as np

    from vibeview.renderers.reaction import ReactionPathRenderer

    section = next((s for s in reader.sections if s.kind == "reaction.path"), None)
    if section is None:
        return None

    renderer = ReactionPathRenderer(section, reader)
    n_frames = renderer.n_frames
    output = Path(output)

    with tempfile.TemporaryDirectory() as td:
        frame_dir = Path(td)

        with structure_frame_writer(reader, size=size) as write:
            if write is None:
                return None
            for i in range(n_frames):
                # get_frame returns [(symbol, position Å), ...] in the same
                # atom order as the structure section, which is what the
                # shared frame writer overrides positions from.
                positions = [np.asarray(pos, dtype=float) for _, pos in renderer.get_frame(i)]
                write(frame_dir / f"frame_{i:04d}.png", positions)

        return _encode_frames(frame_dir, output, format, fps)


def render_vibration_video(
    reader: QVFReader,
    output: str | Path,
    *,
    mode: int = 0,
    n_frames: int = 60,
    fps: float = 15,
    size: tuple[int, int] = (900, 600),
    format: str = "mp4",
    amplitude: float = 1.0,
) -> Path | None:
    """Render a vibrational mode as an MP4/GIF video.

    ``mode`` is the 0-based index of the normal mode to animate.
    ``amplitude`` is the user scale on the shared peak-excursion convention
    (:mod:`vibeview.vibration_amplitude`).
    Returns output path on success, None if no vibrations section.
    """
    import numpy as np

    from vibeview.renderers.vibrations import VibrationsRenderer

    section = next((s for s in reader.sections if s.kind == "vibrations"), None)
    if section is None:
        return None

    renderer = VibrationsRenderer(section, reader)
    data = renderer.load()
    if mode >= len(data.frequencies):
        return None

    output = Path(output)
    from vibeview.vibration_amplitude import normalized_displacement

    disp = normalized_displacement(data.displacements[mode], amplitude)  # [n_atoms, 3]
    orig_positions = [np.array(a.position, dtype=float) for a in data.atoms]

    with tempfile.TemporaryDirectory() as td:
        frame_dir = Path(td)
        with structure_frame_writer(reader, size=size) as write:
            if write is None:
                return None
            for i in range(n_frames):
                phase = 2 * np.pi * i / n_frames
                amplitude = np.sin(phase)
                frame_pos = [
                    orig_positions[atom_idx] + disp[atom_idx] * amplitude
                    for atom_idx in range(len(orig_positions))
                ]
                write(frame_dir / f"frame_{i:04d}.png", frame_pos)

        return _encode_frames(frame_dir, output, format, fps)


def render_orbital_animation(
    reader: QVFReader,
    output: str | Path,
    *,
    fps: float = 3,
    isovalue: float = 0.04,
    size: tuple[int, int] = (900, 600),
    format: str = "mp4",
) -> Path | None:
    """Render an animated sequence of all molecular orbitals as MP4/GIF.

    Evaluates each MO from ``wavefunction.gto`` and renders its isosurface.
    Returns output path on success, None if no wavefunction section.
    """
    from vibeview.renderers.volume import build_isosurface_mesh
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    wf_sec = next((s for s in reader.sections if s.kind == "wavefunction.gto"), None)
    if wf_sec is None:
        return None

    wf_renderer = WavefunctionRenderer(wf_sec, reader)
    # Count the orbitals as every other consumer does, from the coefficient
    # rows. Counting the energies indexed past a matrix with fewer rows (#66).
    n_mo = wf_renderer.n_mo
    if n_mo == 0:
        return None

    output = Path(output)

    with tempfile.TemporaryDirectory() as td:
        frame_dir = Path(td)
        for mo_idx in range(n_mo):
            with headless_plotter(size) as plotter:
                add_structure(plotter, reader)
                grid, values = wf_renderer.evaluate_mo(mo_idx, n_per_dim=60)
                if values is not None:
                    # evaluate_mo returns an Å grid and says so; the contour
                    # helper reads grid.units, so no round trip through bohr.
                    # Two contours: clipping one +isovalue contour at 0
                    # can never yield the negative lobe (all its scalars
                    # are +isovalue) — the old code drew only one phase.
                    add_signed_isosurfaces(
                        plotter,
                        build_isosurface_mesh(values, grid, isovalue),
                        build_isosurface_mesh(values, grid, -isovalue),
                        opacity=0.6,
                        name="mo",
                    )
                frame(plotter)
                snapshot(plotter, frame_dir / f"frame_{mo_idx:04d}.png")

        return _encode_frames(frame_dir, output, format, fps)


def render_turntable(
    plotter: Any,
    output_path: str | Path,
    *,
    num_frames: int = 120,
    duration: float = 4.0,
    fps: float | None = None,
    resolution: tuple[int, int] = (1920, 1080),
    format: str = "mp4",
) -> Path:
    """Render a 360-degree rotating view of the molecule.

    Yields a video file suitable for embedding in presentations.
    Uses PyVista's orbit camera to animate a full rotation.

    Parameters
    ----------
    fps : The effective frame rate for the output video. When provided,
        overrides ``num_frames`` (set to ``int(fps * duration)``).
    """
    if fps is not None:
        num_frames = max(1, int(fps * duration))

    out = Path(output_path)
    # The orbit drives the caller's (often the live viewer's) camera; put it
    # back afterwards instead of leaving it at the last frame's azimuth.
    start_camera = plotter.camera_position
    start_azimuth = plotter.camera.azimuth

    # A real temporary directory, not a dot-directory beside the output
    # that a killed process would leave behind.
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        try:
            for i in range(num_frames):
                plotter.camera.azimuth = (360.0 / num_frames) * i
                plotter.render()
                plotter.screenshot(str(tmp_dir / f"frame_{i:04d}.png"), window_size=resolution)
            fps = num_frames / duration
            return _encode_frames(tmp_dir, out, format, fps)
        finally:
            plotter.camera.azimuth = start_azimuth
            plotter.camera_position = start_camera
