"""``python -m vibeview`` entry point — opens QVF files directly.

Usage::

    python -m vibeview h2o.qvf
    python -m vibeview h2o.qvf --port 9999
    python -m vibeview --help
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    from vibeview.cli import main

    # Make the docstring true: `python -m vibeview h2o.qvf` opens the file.
    # A bare existing file path as the first argument routes to `open`;
    # anything else (subcommands, --help) passes through unchanged.
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-") and Path(argv[0]).is_file():
        argv = ["open", *argv]
    sys.exit(main(args=argv))
