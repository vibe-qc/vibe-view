"""Shared browser runtime; UI values live separately in Trame state.

Controller objects retain this record, never a snapshot of its active reader.
The render locks and generation counters keep their original ownership here.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pyvista as pv

from vibeview.qvf import QVFReader
from vibeview.viewer_defaults import ViewerState

if TYPE_CHECKING:
    from vibeview.app.display import DisplayControllers
    from vibeview.app.editing import EditingControllers
    from vibeview.app.exports import ExportsControllers
    from vibeview.app.files import FilesControllers
    from vibeview.app.lifecycle import LifecycleControllers
    from vibeview.app.playback import PlaybackControllers
    from vibeview.app.preferences import PreferencesControllers
    from vibeview.app.queue import QueueControllers
    from vibeview.app.sessions import SessionsControllers
    from vibeview.app.wavefunction import WavefunctionControllers
    from vibeview.live_opt import LiveOptService
    from vibeview.sketch import SketchSession


@dataclass
class ViewerRuntime:
    server: Any
    plotter: pv.Plotter
    reader: QVFReader
    _all_readers: list[QVFReader]
    viewer_state: ViewerState
    structure_renderer: object | None = None
    is_periodic: bool = False
    live_opt: LiveOptService = field(init=False)
    section_list: list[dict[str, Any]] = field(default_factory=list)
    sidebar_entries: list[dict[str, Any]] = field(default_factory=list)
    orbital_sections: list[dict[str, Any]] = field(default_factory=list)
    basis_ao_sections: list[dict[str, Any]] = field(default_factory=list)
    orbital_options: list[dict[str, Any]] = field(default_factory=list)
    ao_options: list[dict[str, Any]] = field(default_factory=list)
    _anim_task: asyncio.Task[Any] | None = None
    _vib_anim_task: asyncio.Task[Any] | None = None
    _filmstrip_task: asyncio.Task[Any] | None = None
    _vib_filmstrip_task: asyncio.Task[Any] | None = None
    _library_thumb_task: asyncio.Task[Any] | None = None
    _presentation_auto_task: asyncio.Task[Any] | None = None
    _relocalize_task: asyncio.Task[Any] | None = None
    _relocalize_probe_task: asyncio.Task[Any] | None = None
    _vq_monitor_task: asyncio.Task[Any] | None = None
    _watcher_task: asyncio.Task[Any] | None = None
    _sketch_task: asyncio.Task[Any] | None = None
    _sketch_session: SketchSession | None = None
    _anim_epoch: int = 0
    _vib_epoch: int = 0
    _filmstrip_epoch: int = 0
    _vib_filmstrip_epoch: int = 0
    _library_thumb_epoch: int = 0
    _presentation_auto_epoch: int = 0
    _relocalize_generation: int = 0
    _live_opt_probe_epoch: int = 0
    _live_opt_run_epoch: int = 0
    _wf_render_epoch: int = 0
    _live_opt_source_reader: QVFReader | None = None
    _live_opt_undo_pushed: bool = False
    _retired_readers: list[QVFReader] = field(default_factory=list)
    _watcher_path: str | None = None
    _wf_anim_timer: asyncio.TimerHandle | None = None
    _wf_render_lock: threading.RLock = field(default_factory=threading.RLock)
    _wf_epoch_lock: threading.RLock = field(default_factory=threading.RLock)
    _hover_state: dict[str, Any] = field(
        default_factory=lambda: {"world": None, "atom_idx": -1, "at": float("-inf")}
    )
    _ctx_menu_target: dict[str, Any] = field(
        default_factory=lambda: {"world": None, "atom_idx": -1}
    )
    display: DisplayControllers = field(init=False)
    editing: EditingControllers = field(init=False)
    exports: ExportsControllers = field(init=False)
    files: FilesControllers = field(init=False)
    lifecycle: LifecycleControllers = field(init=False)
    playback: PlaybackControllers = field(init=False)
    preferences: PreferencesControllers = field(init=False)
    queue: QueueControllers = field(init=False)
    sessions: SessionsControllers = field(init=False)
    wavefunction: WavefunctionControllers = field(init=False)

    @property
    def ctrl(self) -> Any:
        return self.server.controller
