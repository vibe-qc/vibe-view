"""The options, defaults and error paths every ``vibe-view`` command shares.

``cli.py`` declared ``--port`` and ``--host`` twice, ``--size`` five times
with two help strings and four copies of the ``WxH`` parser, ``--output``
seven times, ``--json`` three times under two parameter names, opened a
reader with "print and exit 1" at fourteen sites, and chose an output
location per command (beside the input for some, the working directory
for others). This module is the one declaration of each: the option
factories, the size type, the server-option resolution that lets
``config.toml`` supply defaults for every server command, the reader
open that exits cleanly, and the output-path convention.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import click
from click.core import ParameterSource

Size = tuple[int, int]

DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"
DEFAULT_IMAGE_SIZE = "900x600"


# ── parameter types ──────────────────────────────────────────────────


class SizeType(click.ParamType[Size]):
    """``WxH`` in pixels (or columns by rows), as an integer pair."""

    name = "WxH"

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> Size:
        if isinstance(value, tuple):
            return int(value[0]), int(value[1])
        text = str(value).strip().lower()
        try:
            width, height = (int(part) for part in text.split("x"))
        except ValueError:
            name = param.opts[0] if param is not None and param.opts else "--size"
            self.fail(f"invalid {name} {value!r}, expected WxH (e.g. 900x600)", param, ctx)
        if width <= 0 or height <= 0:
            name = param.opts[0] if param is not None and param.opts else "--size"
            self.fail(f"invalid {name} {value!r}, both dimensions must be positive", param, ctx)
        return width, height


SIZE = SizeType()


# ── option factories ─────────────────────────────────────────────────

Decorator = Callable[[Any], Any]


def size_option(
    default: str | None = DEFAULT_IMAGE_SIZE, help: str = "Image size as WxH."
) -> Decorator:
    return click.option(
        "--size", type=SIZE, default=default, show_default=default is not None, help=help
    )


def port_option(
    help: str = "TCP port for the web server.", default: int = DEFAULT_PORT
) -> Decorator:
    return click.option(
        "--port",
        default=default,
        show_default=True,
        type=click.IntRange(min=1, max=65535),
        help=help,
    )


def host_option(help: str = "Interface to bind. Default = localhost-only.") -> Decorator:
    return click.option("--host", default=DEFAULT_HOST, show_default=True, help=help)


def no_browser_option(help: str = "Don't open the browser automatically.") -> Decorator:
    return click.option("--no-browser", is_flag=True, default=False, help=help)


def log_file_option() -> Decorator:
    return click.option(
        "--log-file",
        "log_file",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help=(
            "Path to write the server log. Defaults to "
            "$XDG_CACHE_HOME/vibe-view/vibe-view.log (~/.cache/vibe-view/vibe-view.log) "
            "with size-based rotation (5 MiB x 3 backups)."
        ),
    )


def json_flag(help: str = "Emit machine-readable JSON.") -> Decorator:
    """``--json`` bound to the parameter ``json_output``."""
    return click.option("--json", "json_output", is_flag=True, default=False, help=help)


def output_option(
    help: str, *, dir_okay: bool = True, file_okay: bool = True, **kwargs: Any
) -> Decorator:
    return click.option(
        "--output",
        "-o",
        type=click.Path(dir_okay=dir_okay, file_okay=file_okay, path_type=Path),
        default=kwargs.pop("default", None),
        help=help,
        **kwargs,
    )


def isovalue_option(
    default: float | None = None, help: str = "Isovalue for volume sections."
) -> Decorator:
    return click.option(
        "--isovalue", type=float, default=default, show_default=default is not None, help=help
    )


def colormap_option(help: str = "Colormap for volume sections.") -> Decorator:
    return click.option("--colormap", default=None, help=help)


def fps_option(default: float = 5.0) -> Decorator:
    return click.option(
        "--fps", default=default, show_default=True, type=float, help="Frames per second."
    )


# ── shared behaviour ─────────────────────────────────────────────────


def resolve_server_options(
    ctx: click.Context, port: int, host: str, no_browser: bool
) -> tuple[int, str, bool]:
    """Let ``config.toml`` supply the server defaults the user did not set.

    An option the user passed on the command line wins. One left at its
    declared default takes the ``[server]`` value from the config file.
    A value handed in programmatically (``ctx.invoke`` from another
    command) has no parameter source and is kept as passed, so the caller
    resolves its own options first and this cannot override them.
    """
    from vibeview.config import get as config_value

    if ctx.get_parameter_source("port") == ParameterSource.DEFAULT:
        port = int(config_value("server.port", port))
    if ctx.get_parameter_source("host") == ParameterSource.DEFAULT:
        host = str(config_value("server.host", host))
    if not no_browser and ctx.get_parameter_source("no_browser") == ParameterSource.DEFAULT:
        no_browser = not bool(config_value("server.open_browser", True))
    return port, host, no_browser


#: Structure representations the headless renderers draw (the ``[display]``
#: ``representation`` values ``config --init`` documents).
DISPLAY_REPRESENTATIONS = ("ball_and_stick", "space_filling", "sticks_only", "wireframe")


def resolve_display_options(ctx: click.Context, size: Size) -> tuple[Size, str]:
    """Let ``config.toml``'s ``[display]`` table supply image defaults.

    ``config --init`` writes ``size`` and ``representation`` and the manual
    documents them for ``batch`` and ``capture``, but nothing read them
    (#39-11). A ``--size`` given on the command line wins; one left at its
    default takes ``display.size``. ``representation`` has no option, so the
    config value applies whenever it names a known representation. A
    malformed value is reported and ignored rather than aborting the run.
    """
    from vibeview.config import get as config_value

    if ctx.get_parameter_source("size") == ParameterSource.DEFAULT:
        configured = config_value("display.size")
        if configured is not None:
            try:
                size = SIZE.convert(configured, None, None)
            except click.BadParameter as exc:
                click.echo(f"Ignoring config display.size: {exc.message}", err=True)
    representation = str(config_value("display.representation", "ball_and_stick"))
    if representation not in DISPLAY_REPRESENTATIONS:
        click.echo(
            f"Ignoring config display.representation {representation!r}; "
            f"use one of {', '.join(DISPLAY_REPRESENTATIONS)}.",
            err=True,
        )
        representation = "ball_and_stick"
    return size, representation


#: Longest error message printed in full. A fuzzed archive raised a 200 kB
#: message (a whole JSON payload echoed back); the rest is behind --debug.
_MAX_ERROR_CHARS = 2000


def clip_message(message: str) -> str:
    """``message``, cut to :data:`_MAX_ERROR_CHARS` with a note of the rest.

    Every user-facing error goes through this. #63 clipped the last-resort
    report, but the open-time path had its own copy of the echo and stayed
    unbounded: a 200,000-character ``qvf_version`` or section id put about
    400 kB of schema error on the terminal (#64).
    """
    if len(message) <= _MAX_ERROR_CHARS:
        return message
    hidden = len(message) - _MAX_ERROR_CHARS
    return (
        f"{message[:_MAX_ERROR_CHARS]}… ({hidden:,} more characters; "
        "--debug shows the full error)"
    )


def open_reader_or_exit(path: str | Path, *, json_errors: bool = False, prefix: str = "") -> Any:
    """A ``QVFReader`` on ``path``, or a clean exit 1 with the reason.

    The reason goes to stderr as ``Error: <reason>`` (with ``prefix``
    first, when given), or to stdout as ``{"error": ...}`` for a command
    whose caller asked for JSON.
    """
    from vibeview.qvf import QVFError, QVFReader

    try:
        return QVFReader(path)
    except QVFError as exc:
        message = clip_message(str(exc))
        if json_errors:
            click.echo(json.dumps({"error": message}))
        else:
            click.echo(f"{prefix}Error: {message}", err=True)
        raise SystemExit(1) from None


def terminal_safe(text: object) -> str:
    """``text`` with its control characters shown as escapes, not obeyed.

    Archive strings (a calculation name, a section id) reach the terminal
    in plain-text output. An ESC, BEL or C1 control in one of them would
    be read as a terminal command: a CSI sequence recolours or clears the
    screen, an OSC sequence retitles the window or writes a hyperlink
    (#63). Tabs survive; every other C0 control, DEL and the C1 range
    become ``\\xNN``.
    """
    out = []
    for ch in str(text):
        code = ord(ch)
        if ch != "\t" and (code < 0x20 or 0x7F <= code <= 0x9F):
            out.append(f"\\x{code:02x}")
        else:
            out.append(ch)
    return "".join(out)


def parse_csv_list[T](
    value: str,
    *,
    cast: Callable[[str], T],
    option: str,
    expected: int | None = None,
) -> list[T]:
    """``"1,2,3"`` as a list of ``cast`` values, or a usage error naming ``option``."""
    parts = [part.strip() for part in value.split(",")]
    try:
        items = [cast(part) for part in parts if part != ""]
    except ValueError:
        raise click.UsageError(f"{option} expects comma-separated values, got {value!r}") from None
    if expected is not None and len(items) != expected:
        raise click.UsageError(f"{option} expects {expected} comma-separated values, got {value!r}")
    return items


def default_output(
    input_path: Path, suffix: str, *, tag: str | None = None, beside_input: bool = True
) -> Path:
    """Where a command writes when ``--output`` is not given.

    A command that transforms one input writes ``<stem>[_<tag>]<suffix>``
    beside that input, so the result sits with what it came from. Commands
    that aggregate several inputs write in the working directory instead
    (``beside_input=False``). ``suffix`` includes the dot, or is empty for a
    directory.
    """
    name = f"{input_path.stem}_{tag}{suffix}" if tag else f"{input_path.stem}{suffix}"
    return (input_path.parent / name) if beside_input else Path(name)


def describe_default(suffix: str, *, tag: str | None = None) -> str:
    """The help-string fragment for :func:`default_output`."""
    return f"<stem>_{tag}{suffix} beside the input" if tag else f"<stem>{suffix} beside the input"


__all__: Sequence[str] = (
    "terminal_safe",
    "DEFAULT_HOST",
    "DEFAULT_IMAGE_SIZE",
    "DISPLAY_REPRESENTATIONS",
    "DEFAULT_PORT",
    "SIZE",
    "Size",
    "SizeType",
    "clip_message",
    "colormap_option",
    "default_output",
    "describe_default",
    "fps_option",
    "host_option",
    "isovalue_option",
    "json_flag",
    "log_file_option",
    "no_browser_option",
    "open_reader_or_exit",
    "output_option",
    "parse_csv_list",
    "port_option",
    "resolve_display_options",
    "resolve_server_options",
    "size_option",
)
