"""Typed calls for the pyvista ``Plotter`` methods mypy misreads.

pyvista 0.49 wraps ``set_background``, ``view_isometric``, ``reset_camera``,
``remove_actor``, ``show_grid`` and the three axis views
in ``functools.wraps``-based deprecation shims, and its stubs type the result
as an unbound ``_Wrapped`` descriptor: mypy then reports a missing ``self`` or
a mismatched first argument at every call. The behaviour is fine; only the
typing is wrong. These wrappers carry the one ``type: ignore`` each, so call
sites stay clean and the ignores live in one place. ``warn_unused_ignores``
is on, so the day pyvista's typing is fixed this module fails the ``types``
job and can be deleted.

``cmap_argument`` covers a related stub quirk: ``add_mesh`` types ``cmap`` as
a ``Literal`` of every registered colormap name, which a name that arrives
from user input cannot satisfy statically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyvista as pv


def set_background(plotter: pv.Plotter, color: Any) -> None:
    """``plotter.set_background(color)``."""
    plotter.set_background(color)


def view_isometric(plotter: pv.Plotter) -> None:
    """``plotter.view_isometric()``."""
    plotter.view_isometric()  # type: ignore[call-arg]


def reset_camera(plotter: pv.Plotter) -> None:
    """``plotter.reset_camera()``."""
    plotter.reset_camera()  # type: ignore[call-arg]


def remove_actor(plotter: pv.Plotter, name: str) -> None:
    """``plotter.remove_actor(name)``."""
    plotter.remove_actor(name)  # type: ignore[arg-type]


def view_xy(plotter: pv.Plotter) -> None:
    """``plotter.view_xy()``."""
    plotter.view_xy()  # type: ignore[call-arg]


def view_xz(plotter: pv.Plotter) -> None:
    """``plotter.view_xz()``."""
    plotter.view_xz()  # type: ignore[call-arg]


def view_yz(plotter: pv.Plotter) -> None:
    """``plotter.view_yz()``."""
    plotter.view_yz()  # type: ignore[call-arg]


def show_grid(plotter: pv.Plotter) -> None:
    """``plotter.show_grid()``."""
    plotter.show_grid()  # type: ignore[call-arg]


def cmap_argument(name: str) -> Any:
    """A colormap name as ``add_mesh`` will accept it at runtime."""
    return name
