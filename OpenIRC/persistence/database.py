"""A single dedicated worker owns each SQLite connection."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
import json
from pathlib import Path
import sqlite3
import time
import unicodedata
from typing import Any, Mapping

from .migrations import TABLES, migrate


def _canonical(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold().translate(str.maketrans("[]\\^", "{}|~"))


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OpenIRC-database")
        self._connection: sqlite3.Connection | None = None
        self._closed = False
        self._audit_context = ContextVar(f"openirc_audit_{id(self)}", default=None)

    @contextmanager
    def audit_scope(self, actor: str, action: str, object_type: str, object_id: str, summary: str):
        """Attach an administrator audit row to each successful storage commit."""
        scope = {"values": (time.time(), actor, action, object_type, object_id, summary), "recorded": False, "active": True}
        token = self._audit_context.set(scope)
        try:
            yield scope
        finally:
            scope["active"] = False
            self._audit_context.reset(token)

    def _scope(self):
        value = self._audit_context.get()
        return value if value and value["active"] else None

    def _append_audit(self, scope) -> None:
        if scope:
            self._db().execute("INSERT INTO audit_log(timestamp,actor,action,object_type,object,summary) VALUES(?,?,?,?,?,?)", scope["values"])

    async def _run(self, operation, *args):
        if self._closed:
            raise RuntimeError("Database is closed")
        return await asyncio.get_running_loop().run_in_executor(self._executor, partial(operation, *args))

    async def open(self) -> None:
        await self._run(self._open)

    def _open(self) -> None:
        if self._connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            migrate(connection)
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not open")
        return self._connection

    @staticmethod
    def _table(table: str) -> str:
        if table not in TABLES:
            raise ValueError(f"Unknown persistence table: {table}")
        return table

    async def list(self, table: str) -> list[dict[str, Any]]:
        table = self._table(table)
        return await self._run(lambda: [json.loads(row["record"]) for row in self._db().execute(f"SELECT record FROM {table} ORDER BY id")])

    async def put(self, table: str, key: str, record: Mapping[str, Any]) -> None:
        table = self._table(table)
        # Serialize before queuing so subsequent caller mutations cannot race this write.
        encoded = json.dumps(dict(record), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        values = json.loads(encoded)
        await self._run(self._put, table, str(key), encoded, values, self._scope())

    def _put(self, table: str, key: str, encoded: str, values: dict, scope=None) -> None:
        with self._db():
            self._put_uncommitted(table, key, encoded, values)
            self._append_audit(scope)
        if scope:
            scope["recorded"] = True

    def _put_uncommitted(self, table: str, key: str, encoded: str, values: dict) -> None:
        columns, row = ["id", "record"], [key, encoded]
        if table == "accounts":
            columns.append("username")
            row.append(_canonical(values["username"]))
        elif table == "registered_channels":
            columns += ["name", "founder_id"]
            row += [_canonical(values["name"]), values.get("founder_id") or None]
        elif table == "nick_reservations":
            columns.append("account_id")
            row.append(values["account_id"])
        update = ",".join(f"{column}=excluded.{column}" for column in columns if column != "id")
        self._db().execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT(id) DO UPDATE SET {update}", row)

    async def transaction(self, *, puts=(), deletes=(), settings=None) -> None:
        """Atomically apply related records, used for first-run account setup."""
        encoded_puts = []
        for table, key, record in puts:
            table = self._table(table)
            encoded = json.dumps(dict(record), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            encoded_puts.append((table, str(key), encoded, json.loads(encoded)))
        encoded_deletes = [(self._table(table), str(key)) for table, key in deletes]
        encoded_settings = [(key, json.dumps(value, ensure_ascii=False, allow_nan=False)) for key, value in (settings or {}).items()]
        scope = self._scope()
        def commit():
            with self._db():
                for row in encoded_puts:
                    self._put_uncommitted(*row)
                for table, key in encoded_deletes:
                    self._db().execute(f"DELETE FROM {table} WHERE id=?", (key,))
                self._db().executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", encoded_settings)
                self._append_audit(scope)
            if scope:
                scope["recorded"] = True
        await self._run(commit)

    async def delete(self, table: str, key: str) -> None:
        table = self._table(table)
        await self._run(self._delete, table, str(key), self._scope())

    def _delete(self, table: str, key: str, scope=None) -> None:
        with self._db():
            self._db().execute(f"DELETE FROM {table} WHERE id=?", (key,))
            self._append_audit(scope)
        if scope:
            scope["recorded"] = True

    async def get_settings(self) -> dict[str, Any]:
        return await self._run(lambda: {row["key"]: json.loads(row["value"]) for row in self._db().execute("SELECT key,value FROM settings")})

    async def save_settings(self, values: Mapping[str, Any]) -> None:
        encoded = [(key, json.dumps(value, ensure_ascii=False, allow_nan=False)) for key, value in values.items()]
        await self._run(self._save_settings, encoded, self._scope())

    set_settings = save_settings

    def _save_settings(self, encoded: list[tuple[str, str]], scope=None) -> None:
        with self._db():
            self._db().executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", encoded)
            self._append_audit(scope)
        if scope:
            scope["recorded"] = True

    async def audit(self, actor: str, action: str, object_type: str, object_id: str, summary: str) -> None:
        await self._run(self._audit, (time.time(), actor, action, object_type, object_id, summary))

    def _audit(self, values: tuple) -> None:
        with self._db():
            self._db().execute("INSERT INTO audit_log(timestamp,actor,action,object_type,object,summary) VALUES(?,?,?,?,?,?)", values)

    async def audit_entries(self, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        return await self._run(lambda: [dict(row) for row in self._db().execute("SELECT timestamp,actor,action,object_type,object,summary FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))])

    async def close(self) -> None:
        if self._closed:
            return
        def shutdown():
            if self._connection is not None:
                self._connection.close()
                self._connection = None
        await self._run(shutdown)
        self._closed = True
        await asyncio.to_thread(self._executor.shutdown, wait=True)
