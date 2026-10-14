"""The home-path allowlist, read from the one place it is defined.

``.githooks/pre-commit`` holds ``ALLOWED_USERS``. The tree scan in
``test_no_maintainer_paths.py`` and the hook regressions in
``test_privacy_hook.py`` both read it from there, so there is no second copy
to drift (#41-36).
"""

from __future__ import annotations

import re
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".githooks" / "pre-commit"


def allowed_users() -> tuple[str, ...]:
    """The usernames the hook lets through in ``/Users/<name>`` paths."""
    text = HOOK.read_text(encoding="utf-8")
    match = re.search(r"^ALLOWED_USERS='([^']*)'", text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"could not find ALLOWED_USERS in {HOOK}")
    return tuple(match.group(1).split("|"))
