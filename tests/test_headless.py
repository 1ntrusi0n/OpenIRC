"""CLI and core remain usable when every Qt import is unavailable."""
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
BLOCK_QT = """
import importlib.abc
import sys
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('PyQt6', 'PySide6')):
            raise AssertionError('Headless execution attempted to import Qt: ' + fullname)
sys.meta_path.insert(0, NoQt())
"""


def run_python(source, *args):
    return subprocess.run([sys.executable, "-c", BLOCK_QT + textwrap.dedent(source), *map(str, args)], cwd=ROOT, input="", capture_output=True, text=True, timeout=20)


def test_core_import_is_independent_of_qt():
    result = run_python("""
        from OpenIRC.core.server import OpenIRCServer
        from OpenIRC.app import main
        assert not any(name.startswith('PyQt6') for name in sys.modules)
        main(['--version'])
    """)
    # argparse's --version exits successfully before returning.
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("OpenIRC ")


def test_headless_first_run_requires_initialization(tmp_path):
    result = run_python("""
        from OpenIRC.app import main
        raise SystemExit(main(['--headless', '--data-dir', sys.argv[1]]))
    """, tmp_path)
    assert result.returncode == 2
    assert "Initialize OpenIRC" in result.stderr


def test_headless_initialization_requires_interactive_input(tmp_path):
    result = run_python("""
        from OpenIRC.app import main
        raise SystemExit(main(['--headless', '--init-admin', '--data-dir', sys.argv[1]]))
    """, tmp_path)
    assert result.returncode == 2
    assert "interactive terminal" in result.stderr


def test_headless_start_signal_shutdown_and_reopen_without_qt(tmp_path):
    result = run_python("""
        import argparse
        import asyncio
        from pathlib import Path
        import signal
        from OpenIRC.app import run_headless
        from OpenIRC.core.server import OpenIRCServer
        from OpenIRC.models.domain import AdminCommand, LocalAdministrator
        async def exercise():
            path = Path(sys.argv[1])
            server = OpenIRCServer(path)
            await server.initialize()
            await server.admin.execute(LocalAdministrator(), AdminCommand('setup', {'username': 'headless-admin', 'password': 'test-only-password', 'values': {'irc_port': 0}}))
            await server.close()
            asyncio.get_running_loop().call_later(0.3, signal.raise_signal, signal.SIGTERM)
            result = await run_headless(argparse.Namespace(data_dir=path, debug=False, init_admin=False))
            assert result == 0
            reopened = OpenIRCServer(path)
            await reopened.initialize()
            assert len(reopened.accounts) == 1
            await reopened.close()
            assert not any(name.startswith('PyQt6') for name in sys.modules)
        asyncio.run(exercise())
    """, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "running on" in result.stdout
