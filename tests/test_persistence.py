"""Storage integrity and domain persistence regression tests."""
import asyncio
from contextlib import closing
from dataclasses import asdict
import sqlite3

import pytest

from OpenIRC.models.domain import Account, AccessEntry, Ban, Channel, new_id, wire_oid
from OpenIRC.persistence.database import Database


def test_records_survive_reopen_and_constraints(tmp_path):
    async def run():
        path = tmp_path / "openirc.sqlite3"
        database = Database(path)
        await database.open()
        account = Account(new_id(), "Alice", "Alice Example", "hash-is-not-plaintext")
        await database.put("accounts", account.id, account.to_record())
        channel = Channel(new_id(), wire_oid(), "#persist", founder_id=account.id, registered=True)
        channel.access.append(AccessEntry(new_id(), channel.id, "OWNER", "Alice!*@*", "admin"))
        channel.properties["ONJOIN"] = "Welcome!"
        await database.put("registered_channels", channel.id, channel.to_record())
        ban = Ban(new_id(), "cidr", "192.0.2.0/24", reason="Test")
        await database.put("server_bans", ban.id, ban.to_record())
        await database.save_settings({"irc_port": 7000, "_cloak_secret": "retained"})
        await database.save_settings({"irc_port": 7001})
        with pytest.raises(sqlite3.IntegrityError):
            await database.delete("accounts", account.id)
        with pytest.raises(sqlite3.IntegrityError):
            duplicate = Account(new_id(), "ALICE", "Duplicate", "hash")
            await database.put("accounts", duplicate.id, duplicate.to_record())
        with pytest.raises(ValueError):
            await database.list("accounts; DROP TABLE accounts")
        await database.close()
        database = Database(path)
        await database.open()
        try:
            restored = Channel.from_record((await database.list("registered_channels"))[0])
            assert restored.id == channel.id and restored.oid == channel.oid
            assert restored.access[0].mask == "Alice!*@*"
            assert restored.properties["ONJOIN"] == "Welcome!"
            assert (await database.list("accounts"))[0]["username"] == "Alice"
            assert (await database.list("server_bans"))[0]["mask"] == "192.0.2.0/24"
            assert await database.get_settings() == {"irc_port": 7001, "_cloak_secret": "retained"}
        finally:
            await database.close()
    asyncio.run(run())


def test_settings_writes_are_transactional_and_audit_is_bounded(tmp_path):
    async def run():
        database = Database(tmp_path / "openirc.sqlite3")
        await database.open()
        try:
            await database.save_settings({"server_name": "old.example"})
            with pytest.raises(TypeError):
                await database.save_settings({"server_name": "new.example", "bad": object()})
            assert (await database.get_settings())["server_name"] == "old.example"
            await asyncio.gather(*(database.audit("admin", "channel.save", "channel", str(i), "Channel updated") for i in range(20)))
            assert len(await database.audit_entries(limit=5)) == 5
            assert len(await database.audit_entries()) == 20
        finally:
            await database.close()
    asyncio.run(run())


def test_account_deletion_cascades_operator_and_reservations(tmp_path):
    async def run():
        database = Database(tmp_path / "openirc.sqlite3")
        await database.open()
        try:
            account = Account(new_id(), "DeleteMe", "", "hash")
            await database.put("accounts", account.id, account.to_record())
            await database.put("operators", account.id, {"account_id": account.id, "permissions": []})
            await database.put("nick_reservations", "nick", {"nickname": "Nick", "account_id": account.id})
            await database.delete("accounts", account.id)
            assert await database.list("operators") == []
            assert await database.list("nick_reservations") == []
        finally:
            await database.close()
    asyncio.run(run())


def test_transaction_rolls_back_related_records_and_settings(tmp_path):
    async def run():
        database = Database(tmp_path / "openirc.sqlite3")
        await database.open()
        try:
            account = Account(new_id(), "Transactional", "", "hash")
            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(puts=(("accounts", account.id, account.to_record()), ("operators", "nonexistent-account", {"account_id": "nonexistent-account"})), settings={"setup_complete": True})
            assert await database.list("accounts") == []
            assert await database.get_settings() == {}
            await database.transaction(puts=(("accounts", account.id, account.to_record()), ("operators", account.id, {"account_id": account.id})), settings={"setup_complete": True})
            assert len(await database.list("accounts")) == 1
            assert (await database.get_settings())["setup_complete"]
        finally:
            await database.close()
    asyncio.run(run())


def test_migration_failure_rolls_back_and_future_schema_rejected():
    from OpenIRC.persistence import migrations
    connection = sqlite3.connect(":memory:")
    original = migrations.MIGRATIONS
    try:
        migrations.MIGRATIONS = (("CREATE TABLE should_rollback(id TEXT)", "NOT VALID SQL"),)
        with pytest.raises(sqlite3.OperationalError):
            migrations.migrate(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone() is None
        migrations.MIGRATIONS = original
        migrations.migrate(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        connection.execute("PRAGMA user_version=100")
        with pytest.raises(RuntimeError, match="newer"):
            migrations.migrate(connection)
    finally:
        migrations.MIGRATIONS = original
        connection.close()


def test_admin_mutation_authorization_redaction_and_failed_write(tmp_path):
    from types import SimpleNamespace
    from OpenIRC.admin.service import AdminService
    from OpenIRC.admin.commands import AdminError
    from OpenIRC.config.settings import ServerSettings
    from OpenIRC.models.domain import AdminCommand, LocalAdministrator, Principal
    from OpenIRC.protocol.unicode import casefold
    async def run():
        database = Database(tmp_path / "openirc.sqlite3")
        await database.open()
        server = SimpleNamespace(db=database, lock=asyncio.Lock(), settings=ServerSettings(), status="Stopped", accounts={}, operators={}, channels={},
                                 sessions={}, reservations={}, bans={}, statistics={}, logs=[], refresh_privileges=lambda: None,
                                 refresh_channel_visibility=lambda old, new: None, broadcast_channel=lambda *a, **k: None, emit=lambda *a, **k: None)
        server.has_permission = lambda actor, permission: actor.local or permission in actor.permissions
        server.find_channel = lambda name: server.channels.get(casefold(name))
        service, principal = AdminService(server), LocalAdministrator()
        try:
            with pytest.raises(AdminError, match="requires"):
                await service.execute(Principal("remote", "remote"), AdminCommand("account.save", {"username": "Denied", "password": "secret"}))
            created = await service.execute(principal, AdminCommand("setup", {"username": "Admin", "password": "account-secret", "values": {"irc_port": 7000}}))
            assert server.settings.setup_complete and server.settings.irc_port == 7000
            assert server.operators[created["id"]].role == "administrator"
            channel_result = await service.execute(principal, AdminCommand("channel.save", {"name": "#persistent", "registered": True, "founder_id": created["id"],
                                                                                             "secrets": {"OWNERKEY": "channel-secret"}, "properties": {"ONJOIN": "Welcome!\nBe kind."}}))
            with pytest.raises(AdminError, match="Transfer"):
                await service.execute(principal, AdminCommand("account.delete", {"id": created["id"]}))
            snapshot = await service.snapshot(principal)
            exported = await service.execute(principal, AdminCommand("configuration.export"))
            for content in (str(snapshot), str(exported), str(await database.audit_entries())):
                assert "account-secret" not in content and "channel-secret" not in content and "$argon2" not in content
            with pytest.raises(TypeError):
                snapshot.accounts[0]["username"] = "modified"
            original_put = database.put
            async def fail_write(*args, **kwargs):
                raise sqlite3.OperationalError("simulated full disk")
            database.put = fail_write
            with pytest.raises(AdminError, match="database could not save"):
                await service.execute(principal, AdminCommand("account.save", {"id": created["id"], "enabled": False}))
            assert server.accounts[created["id"]].enabled
            database.put = original_put
            await service.execute(principal, AdminCommand("channel.unregister", {"id": channel_result["id"]}))
            await service.execute(principal, AdminCommand("account.delete", {"id": created["id"]}))
            assert not server.accounts
        finally:
            await database.close()
    asyncio.run(run())


def test_audit_failure_rolls_back_mutation_and_task_scopes_are_isolated(tmp_path):
    async def run():
        path = tmp_path / "openirc.sqlite3"
        database = Database(path)
        await database.open()
        try:
            with closing(sqlite3.connect(path)) as external:
                external.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
            account = Account(new_id(), "AtomicAudit", "", "hash")
            with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
                with database.audit_scope("admin", "account.save", "account", account.id, "Create account"):
                    await database.put("accounts", account.id, account.to_record())
            assert not await database.list("accounts")
            with closing(sqlite3.connect(path)) as external:
                external.execute("DROP TRIGGER fail_audit")
            async def add(index):
                record = Account(new_id(), f"User{index}", "", "hash")
                with database.audit_scope(f"Actor{index}", "account.save", "account", record.id, f"Record{index}") as scope:
                    await database.put("accounts", record.id, record.to_record())
                    assert scope["recorded"]
            await asyncio.gather(*(add(index) for index in range(10)))
            rows = await database.audit_entries()
            assert len(rows) == 10
            assert all(row["actor"].removeprefix("Actor") == row["summary"].removeprefix("Record") for row in rows)
        finally:
            await database.close()
    asyncio.run(run())
