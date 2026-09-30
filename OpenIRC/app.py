"""Lazy GUI loading and a Qt-free headless entry point."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from pathlib import Path
import signal
import sys

from OpenIRC import __version__


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "OpenIRC"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenIRC"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "OpenIRC"


async def run_headless(args) -> int:
    from OpenIRC.core.server import OpenIRCServer
    from OpenIRC.models.domain import AdminCommand, LocalAdministrator
    server = OpenIRCServer(args.data_dir, {"log_level": "DEBUG"} if args.debug else None)
    try:
        await server.initialize()
        if args.init_admin:
            if server.settings.get("setup_complete"):
                print("OpenIRC is already initialized. Manage accounts with the local console.", file=sys.stderr)
                return 2
            if not sys.stdin.isatty():
                print("Administrator initialization requires an interactive terminal.", file=sys.stderr)
                return 2
            username = input("First administrator username: ").strip()
            password = getpass.getpass("Administrator password: ")
            if password != getpass.getpass("Repeat password: "):
                print("Passwords do not match.", file=sys.stderr)
                return 2
            name = input("Server name [irc.openirc.local]: ").strip() or "irc.openirc.local"
            bind = input("Bind address [127.0.0.1]: ").strip() or "127.0.0.1"
            await server.admin.execute(LocalAdministrator(), AdminCommand("setup", {"username": username, "password": password, "values": {"server_name": name, "bind_address": bind, "ipv6_enabled": ":" in bind}}))
            print(f"OpenIRC initialized in {args.data_dir}. Run python -m OpenIRC --headless to start.")
            return 0
        if not server.settings.get("setup_complete"):
            print("Initialize OpenIRC with the desktop wizard or python -m OpenIRC --headless --init-admin.", file=sys.stderr)
            return 2
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        def stop_handler(*_):
            loop.call_soon_threadsafe(stopped.set)
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), stop_handler)
        await server.start()
        print(f"OpenIRC {__version__} running on {server.bound_addresses}", flush=True)
        await stopped.wait()
        return 0
    finally:
        await server.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OpenIRC IRC/IRCX server and administration console")
    parser.add_argument("--headless", action="store_true", help="Run the server without Qt")
    parser.add_argument("--start-server", action="store_true", help="Start GUI listeners after loading configuration or completing first-run setup")
    parser.add_argument("--debug", action="store_true", help="Enable diagnostic logs (credentials are always excluded)")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(), help="Configuration, database and log directory")
    parser.add_argument("--init-admin", action="store_true", help="Initialize a headless instance interactively")
    parser.add_argument("--version", action="version", version=f"OpenIRC {__version__}")
    args = parser.parse_args(argv)
    try:
        if args.headless or args.init_admin:
            return asyncio.run(run_headless(args))
        from OpenIRC.gui.main_window import run_gui
        return run_gui(args.data_dir, args.debug, start_server=args.start_server)
    except KeyboardInterrupt:
        return 130
    except ModuleNotFoundError as error:
        if error.name and error.name.startswith("PyQt6"):
            print('Install the desktop components with: python -m pip install -e ".[gui]"', file=sys.stderr)
            return 2
        raise
    except (OSError, ValueError, RuntimeError) as error:
        print(f"OpenIRC could not start: {error}", file=sys.stderr)
        return 1
