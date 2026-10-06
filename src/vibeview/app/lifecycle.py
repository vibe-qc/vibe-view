"""Lifecycle controllers for one browser viewer instance."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


from vibeview.app import helpers as _helpers

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime


class LifecycleControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    def _watch_state(self, *keys: str) -> Callable[[Any], Any]:
        """``@server.state.change`` that the next create_app can undo."""

        def register(func: Any) -> Any:
            self.viewer.server.state.change(*keys)(func)
            for key in keys:
                self.viewer.server._vibeview_state_watchers.append(
                    (self.viewer.server.state._translator.translate_key(key), func)
                )
            return func

        return register

    def _counted_view_update(self) -> None:
        """``ctrl.view_update()`` that also bumps the push-generation counter.

        Seven controllers called ``ctrl.view_update()`` directly, bypassing
        the counter ``_push_view`` keeps, so the section dispatcher could not
        tell that they had already pushed (#37-21). Best-effort like
        ``_push_view``: a client that went away must not break the caller.
        """
        _helpers._scene_hooks(self.viewer.plotter).push_generation += 1
        with contextlib.suppress(Exception):
            self.viewer.server.controller.view_update()

    async def _auto_save_loop(self) -> None:
        """Auto-save the session at the configured interval (Settings dialog).

        The interval is re-read every cycle, so a saved change applies from
        the next save on; it was a hard-coded 60 s (#37-3).
        """
        while True:
            await asyncio.sleep(
                _helpers._auto_save_interval_seconds(
                    self.viewer.server.state.settings_auto_save_interval
                )
            )
            try:
                if self.viewer.server.state.session_path:
                    self.viewer.preferences.auto_save_session()
            except Exception:
                pass
