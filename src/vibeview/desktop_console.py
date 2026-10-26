"""Persistent local desktop Python console worker, using a JSON-lines protocol.

This module deliberately executes code supplied by the desktop user. It is not a
sandbox and must never be exposed as a browser/server execution endpoint.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
import traceback
from typing import Any

_LIMIT = 100_000


class _Output(io.TextIOBase):
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.truncated = False

    def write(self, text: str) -> int:
        available = max(0, _LIMIT - self.length)
        if available:
            self.parts.append(text[:available])
        self.length += min(len(text), available)
        self.truncated |= len(text) > available
        return len(text)

    def value(self) -> str:
        return ''.join(self.parts) + ('\n[output truncated]' if self.truncated else '')


class Console:
    def __init__(self) -> None:
        import numpy as np

        from vibeview import api
        from vibeview.model_scene import ModelScene
        from vibeview.scene_commands import SceneCommands

        self.namespace: dict[str, Any] = {'__name__': '__console__', 'api': api, 'np': np,
                                          'ModelScene': ModelScene, 'SceneCommands': SceneCommands,
                                          'scene': ModelScene()}

    def execute(self, source: str) -> dict[str, Any]:
        output, errors = _Output(), _Output()
        result: str | None = None
        ok = True
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                tree = ast.parse(source, filename='<desktop console>', mode='exec')
                last = (tree.body.pop()
                        if tree.body and isinstance(tree.body[-1], ast.Expr) else None)
                exec(compile(tree, '<desktop console>', 'exec'), self.namespace)
                if isinstance(last, ast.Expr):
                    value = eval(compile(ast.Expression(last.value), '<desktop console>', 'eval'),
                                 self.namespace)
                    self.namespace['_'] = value
                    if value is not None:
                        result = repr(value)[:_LIMIT]
            except BaseException:  # console errors, including SystemExit, stay in this session
                ok = False
                traceback.print_exc(file=errors)
        return {'ok': ok, 'stdout': output.value(), 'stderr': errors.value(), 'result': result}


def main() -> None:
    # Keep protocol output separate from native libraries writing directly to
    # fd 1 (outside redirect_stdout). Their output goes to the parent stderr pipe.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8')
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    console = Console()
    for line in sys.stdin:
        identifier: Any = None
        try:
            request = json.loads(line)
            identifier = request.get('id')
            source = request['source']
            if not isinstance(source, str) or len(source) > 1_000_000:
                raise ValueError('Console source must be a string of at most 1,000,000 characters')
            response = console.execute(source)
        except (ValueError, TypeError, KeyError) as exc:
            response = {'ok': False, 'stdout': '', 'stderr': str(exc), 'result': None}
        protocol.write(json.dumps({'vibeview_console': 1, 'id': identifier, **response}) + '\n')
        protocol.flush()


if __name__ == '__main__':
    main()
