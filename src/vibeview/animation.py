"""Video/animation export — render animated sections to MP4/GIF.

Requires ``ffmpeg`` on PATH for MP4 output.  Falls back to animated GIF
(via Pillow) when ffmpeg is not available.

Used by ``vibe-view animate`` CLI command.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

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
    images = [Image.open(f) for f in frames]
    duration = int(1000 / fps)
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


def _render_structure_frame(
    reader: QVFReader,
    path: Path,
    size=(900, 600),
    representation="ball_and_stick",
    positions=None,
) -> bool:
    """Render one structure frame to a PNG. Returns True on success.

    ``positions`` (``[n_atoms, 3]`` Å, optional) overrides the stored
    geometry — the per-frame hook for trajectory/vibration videos. A
    fresh ``StructureRenderer`` re-reads the archive, so mutating a
    previously loaded StructureData has no effect here (that was the
    static-video bug: every frame rendered the stored geometry).
    """
    import pyvista as pv

    from vibeview.renderers.structure import StructureRenderer

    section = next((s for s in reader.sections if s.kind == "structure"), None)
    if section is None:
        return False
    plotter = pv.Plotter(off_screen=True, window_size=[int(size[0]), int(size[1])])
    try:
        plotter.set_background("#1a1a2e")
        renderer = StructureRenderer(section, reader)
        if positions is not None:
            import numpy as np

            data = renderer.load()
            for atom, pos in zip(data.atoms, positions):
                atom.position = np.asarray(pos, dtype=float)
            # Bonds must follow the displaced geometry.
            renderer._bonds = reader.infer_bonds(data)
        renderer.add_to_plotter(plotter, representation=representation)
        plotter.view_isometric()
        plotter.reset_camera()
        plotter.screenshot(str(path))
    finally:
        plotter.close()
    return True


def render_trajectory_video(
    reader: QVFReader,
    output: str | Path,
    *,
    fps: float = 5,
    size=(900, 600),
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
        import numpy as np

        for i in range(n_frames):
            path = frame_dir / f"frame_{i:04d}.png"
            _render_structure_frame(reader, path, size=size, positions=data.coords[i])

        if format == "mp4" and _ffmpeg_available():
            _frames_to_mp4(frame_dir, output, fps=fps)
        elif format == "frames":
            out = output.with_suffix("")
            _frames_to_pngs(frame_dir, out)
            return out
        elif format in ("gif", "mp4"):
            out = output.with_suffix(".gif")
            _frames_to_gif(frame_dir, out, fps=fps)
            return out

    return output if output.exists() else None


def render_reaction_video(
    reader: QVFReader,
    output: str | Path,
    *,
    fps: float = 5,
    size=(900, 600),
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

        for i in range(n_frames):
            # get_frame returns [(symbol, position Å), ...] in the same
            # atom order as the structure section, which is what the
            # shared frame renderer overrides positions from.
            positions = [np.asarray(pos, dtype=float) for _, pos in renderer.get_frame(i)]
            path = frame_dir / f"frame_{i:04d}.png"
            _render_structure_frame(reader, path, size=size, positions=positions)

        if format == "mp4" and _ffmpeg_available():
            _frames_to_mp4(frame_dir, output, fps=fps)
        elif format == "frames":
            out = output.with_suffix("")
            _frames_to_pngs(frame_dir, out)
            return out
        elif format in ("gif", "mp4"):
            out = output.with_suffix(".gif")
            _frames_to_gif(frame_dir, out, fps=fps)
            return out

    return output if output.exists() else None


def render_vibration_video(
    reader: QVFReader,
    output: str | Path,
    *,
    mode: int = 0,
    n_frames: int = 60,
    fps: float = 15,
    size=(900, 600),
    format: str = "mp4",
) -> Path | None:
    """Render a vibrational mode as an MP4/GIF video.

    ``mode`` is the 0-based index of the normal mode to animate.
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
    freq = data.frequencies[mode]
    disp = data.displacements[mode]  # [n_atoms, 3]
    orig_positions = [np.array(a.position, dtype=float) for a in data.atoms]

    with tempfile.TemporaryDirectory() as td:
        frame_dir = Path(td)
        for i in range(n_frames):
            phase = 2 * np.pi * i / n_frames
            amplitude = np.sin(phase)
            frame_pos = [
                orig_positions[atom_idx] + disp[atom_idx] * amplitude
                for atom_idx in range(len(orig_positions))
            ]
            path = frame_dir / f"frame_{i:04d}.png"
            _render_structure_frame(reader, path, size=size, positions=frame_pos)

        if format == "mp4" and _ffmpeg_available():
            _frames_to_mp4(frame_dir, output, fps=fps)
        elif format == "frames":
            out = output.with_suffix("")
            _frames_to_pngs(frame_dir, out)
            return out
        elif format in ("gif", "mp4"):
            out = output.with_suffix(".gif")
            _frames_to_gif(frame_dir, out, fps=fps)
            return out

    return output if output.exists() else None


def render_orbital_animation(
    reader: QVFReader,
    output: str | Path,
    *,
    fps: float = 3,
    isovalue: float = 0.04,
    size=(900, 600),
    format: str = "mp4",
) -> Path | None:
    """Render an animated sequence of all molecular orbitals as MP4/GIF.

    Evaluates each MO from ``wavefunction.gto`` and renders its isosurface.
    Returns output path on success, None if no wavefunction section.
    """
    import numpy as np

    from vibeview.renderers.volume import build_isosurface_mesh
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    wf_sec = next((s for s in reader.sections if s.kind == "wavefunction.gto"), None)
    if wf_sec is None:
        return None

    wf_renderer = WavefunctionRenderer(wf_sec, reader)
    wf = wf_renderer.load()
    n_mo = len(wf.energies) if wf.energies is not None else 0
    if n_mo == 0:
        return None

    output = Path(output)

    with tempfile.TemporaryDirectory() as td:
        frame_dir = Path(td)
        import pyvista as pv

        from vibeview.renderers.structure import StructureRenderer

        struct_sec = next((s for s in reader.sections if s.kind == "structure"), None)

        for mo_idx in range(n_mo):
            plotter = pv.Plotter(off_screen=True, window_size=[int(size[0]), int(size[1])])
            try:
                plotter.set_background("#1a1a2e")
                if struct_sec:
                    StructureRenderer(struct_sec, reader).add_to_plotter(plotter)
                grid, values = wf_renderer.evaluate_mo(mo_idx, n_per_dim=60)
                if values is not None:
                    # GridData lives in vibeview.qvf (volume.py only imports
                    # it under TYPE_CHECKING — the old import crashed every
                    # invocation). evaluate_mo returns an Å grid while
                    # build_isosurface_mesh expects bohr and converts, so
                    # feed it bohr to avoid the double Å conversion.
                    from vibeview.qvf import GridData

                    ang_to_bohr = 1.0 / 0.529177210903
                    gd = GridData(
                        origin=np.asarray(grid.origin) * ang_to_bohr,
                        voxel_vectors=np.asarray(grid.voxel_vectors) * ang_to_bohr,
                        shape=grid.shape,
                    )
                    # Two contours: clipping one +isovalue contour at 0
                    # can never yield the negative lobe (all its scalars
                    # are +isovalue) — the old code drew only one phase.
                    pos = build_isosurface_mesh(values, gd, isovalue)
                    neg = build_isosurface_mesh(values, gd, -isovalue)
                    if pos is not None and pos.n_points:
                        plotter.add_mesh(
                            pos,
                            color="#3366cc",
                            opacity=0.6,
                            name="mo_pos",
                            show_scalar_bar=False,
                        )
                    if neg is not None and neg.n_points:
                        plotter.add_mesh(
                            neg,
                            color="#cc3333",
                            opacity=0.6,
                            name="mo_neg",
                            show_scalar_bar=False,
                        )
                plotter.view_isometric()
                plotter.reset_camera()
                plotter.screenshot(str(frame_dir / f"frame_{mo_idx:04d}.png"))
            finally:
                plotter.close()

        if format == "mp4" and _ffmpeg_available():
            _frames_to_mp4(frame_dir, output, fps=fps)
        elif format == "frames":
            out = output.with_suffix("")
            _frames_to_pngs(frame_dir, out)
            return out
        elif format in ("gif", "mp4"):
            out = output.with_suffix(".gif")
            _frames_to_gif(frame_dir, out, fps=fps)
            return out

    return output if output.exists() else None


def render_turntable(
    plotter,
    output_path,
    *,
    num_frames=120,
    duration=4.0,
    fps=None,
    resolution=(1920, 1080),
    format="mp4",
):
    """Render a 360-degree rotating view of the molecule.

    Yields a video file suitable for embedding in presentations.
    Uses PyVista's orbit camera to animate a full rotation.

    Parameters
    ----------
    fps : The effective frame rate for the output video. When provided,
        overrides ``num_frames`` (set to ``int(fps * duration)``).
    """
    from pathlib import Path

    import numpy as np

    if fps is not None:
        num_frames = max(1, int(fps * duration))

    out = Path(output_path)
    tmp_dir = out.parent / f".turntable_{out.stem}"
    tmp_dir.mkdir(exist_ok=True)

    try:
        # Save frames
        for i in range(num_frames):
            angle = (360.0 / num_frames) * i
            plotter.camera_position = plotter.camera_position  # Get current
            plotter.camera.azimuth = angle
            plotter.render()
            frame_path = tmp_dir / f"frame_{i:04d}.png"
            plotter.screenshot(str(frame_path), window_size=resolution)

        fps = num_frames / duration
        if format == "mp4" and _ffmpeg_available():
            _frames_to_mp4(tmp_dir, out, fps=fps)
        elif format == "frames":
            _frames_to_pngs(tmp_dir, out.with_suffix(""))
            out = out.with_suffix("")
        elif format in ("gif", "mp4"):
            _frames_to_gif(tmp_dir, out.with_suffix(".gif"), fps=fps)
            out = out.with_suffix(".gif")
        return out
    finally:
        import shutil as _shutil

        _shutil.rmtree(tmp_dir, ignore_errors=True)
