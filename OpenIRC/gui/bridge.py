"""Qt/asyncio boundary: ownership stays on the server thread."""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal


class ServerBridge(QObject):
    ready = pyqtSignal()
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)
    closed = pyqtSignal()

    def __init__(self, data_dir: Path, debug: bool = False):
        super().__init__()
        self.data_dir = data_dir
        self.debug = debug
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server = None
        self._sequence = 0
        self._thread = threading.Thread(target=self._run, name="OpenIRC network", daemon=False)
        self._closing = False
        self._ready = False

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        from OpenIRC.core.server import OpenIRCServer
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.server = OpenIRCServer(self.data_dir, {"log_level": "DEBUG"} if self.debug else None)
            self.loop.run_until_complete(self.server.initialize())
            self._ready = True
            if not self._closing:
                self.ready.emit()
                self.loop.run_forever()
        except Exception as exc:
            logging.getLogger("OpenIRC.gui").exception("Server worker failed")
            self.failed.emit(0, str(exc))
        finally:
            if self.server is not None:
                try:
                    self.loop.run_until_complete(self.server.close())
                except Exception:
                    logging.getLogger("OpenIRC.gui").exception("Server shutdown failed")
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()
            self.closed.emit()

    def request(self, action: str, payload: dict | None = None) -> int:
        self._sequence += 1
        token = self._sequence
        if self.loop is None or self.server is None or self._closing:
            self.failed.emit(token, "The server service is not available.")
            return token

        async def invoke() -> None:
            from OpenIRC.admin.commands import AdminCommand
            from OpenIRC.models.domain import LocalAdministrator
            try:
                if action == "snapshot":
                    result = await self.server.admin.snapshot(LocalAdministrator())
                else:
                    result = await self.server.admin.execute(LocalAdministrator(), AdminCommand(action, payload or {}))
                self.completed.emit(token, result)
            except Exception as exc:
                # Do not log command payloads: they can contain credentials.
                self.failed.emit(token, str(exc))

        asyncio.run_coroutine_threadsafe(invoke(), self.loop)
        return token

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._ready and self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
