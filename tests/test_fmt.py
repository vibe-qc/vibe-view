"""vibeview._fmt holds the text formatting every surface shares."""

from __future__ import annotations

import pytest

from vibeview._fmt import human_size


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (4, "4 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1048576, "1.0 MB"),
        (3 * 1024**3, "3.0 GB"),
        (2 * 1024**4, "2.0 TB"),
        (5 * 1024**5, "5120.0 TB"),
    ],
)
def test_human_size(size: int, expected: str) -> None:
    assert human_size(size) == expected


def test_every_surface_uses_the_shared_helper() -> None:
    """No module keeps a private byte formatter any more."""
    from vibeview import cli, serve
    from vibeview.renderers import run_record

    for module in (cli, serve, run_record):
        assert not hasattr(module, "_human_size")
        assert module.human_size is human_size
