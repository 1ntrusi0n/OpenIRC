"""Transactional, versioned SQLite schema upgrades."""
from __future__ import annotations
import sqlite3

TABLES = frozenset({"accounts", "operators", "registered_channels", "channel_properties", "channel_access", "server_bans", "nick_reservations", "user_access", "server_access", "network_access"})

SCHEMA_V1 = (
    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE accounts (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, record TEXT NOT NULL)",
    "CREATE TABLE operators (id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE, record TEXT NOT NULL)",
    "CREATE TABLE registered_channels (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, founder_id TEXT REFERENCES accounts(id) ON DELETE RESTRICT, record TEXT NOT NULL)",
    "CREATE TABLE channel_properties (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    "CREATE TABLE channel_access (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    "CREATE TABLE server_bans (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    "CREATE TABLE nick_reservations (id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, record TEXT NOT NULL)",
    "CREATE TABLE user_access (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    "CREATE TABLE server_access (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    "CREATE TABLE network_access (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    "CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, object_type TEXT NOT NULL, object TEXT NOT NULL, summary TEXT NOT NULL)",
    "CREATE INDEX audit_timestamp_idx ON audit_log(timestamp)",
)
MIGRATIONS = (SCHEMA_V1,)


def migrate(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise RuntimeError("Database was created by a newer OpenIRC version")
    for index in range(version, len(MIGRATIONS)):
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATIONS[index]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {index + 1}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
