"""Actual persistent Python worker behavior without an Electron UI."""
import json
import subprocess
import sys

from vibeview.desktop_console import Console


def test_namespace_expression_output_and_error_recovery():
    console = Console()
    result = console.execute("x = 6\nprint('hello')\nx * 7")
    assert result == {'ok': True, 'stdout': 'hello\n', 'stderr': '', 'result': '42'}
    assert console.execute('x + _')['result'] == '48'
    assert console.execute('raise ValueError("expected")')['ok'] is False
    assert console.execute('x')['result'] == '6'
    assert console.execute('import sys\nsys.exit(2)')['ok'] is False
    assert console.execute('len(scene.models)')['result'] == '0'
    assert console.execute('"get_structure" in api.__all__')['result'] == 'True'
    assert console.execute('np.linalg.norm([3, 4])')['result'] in {'5.0', 'np.float64(5.0)'}


def test_bounded_output_and_syntax_error():
    console = Console()
    result = console.execute('for i in range(20000): print("abcdefghij")')
    assert result['ok'] and len(result['stdout']) < 100100
    assert result['stdout'].endswith('[output truncated]')
    assert 'SyntaxError' in console.execute('if :')['stderr']


def test_actual_worker_json_protocol(water_qvf):
    requests = [{'id': 1, 'source': 'x = 12\nx'}, {'id': 2, 'source': 'x * 2'},
                {'id': 3, 'source': 'raise RuntimeError("expected")'},
                {'id': 4, 'source': 'x + 1'},
                {'id': 5, 'source': 'import os; os.write(1, b"native without newline")'},
                {'id': 6, 'source': f'len(api.get_structure({str(water_qvf)!r})["atoms"])'}]
    completed = subprocess.run([sys.executable, '-m', 'vibeview.desktop_console'],
                               input='\n'.join(json.dumps(row) for row in requests) + '\n',
                               capture_output=True, text=True, check=True, timeout=20)
    rows = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [row['id'] for row in rows] == [1, 2, 3, 4, 5, 6]
    assert all(row['vibeview_console'] == 1 for row in rows)
    assert [row['result'] for row in rows] == ['12', '24', None, '13', '22', '3']
    assert rows[2]['ok'] is False
    assert 'native without newline' in completed.stderr
