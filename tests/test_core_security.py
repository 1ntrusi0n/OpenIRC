"""Regression coverage for authentication racing administrative revocation."""
import asyncio
from dataclasses import replace

import pytest

import OpenIRC.core.identity as identity
from OpenIRC.core.server import OpenIRCServer
from OpenIRC.core.session import Session
from OpenIRC.models.domain import AdminCommand, LocalAdministrator
from OpenIRC.protocol.errors import IRCError
from .helpers import IRCClient


@pytest.mark.parametrize("mutation", ["disable", "lock", "password", "remove_operator", "disable_operator"])
async def test_authentication_does_not_restore_revoked_records(tmp_path, monkeypatch, mutation):
    server = OpenIRCServer(tmp_path)
    await server.initialize()
    try:
        await server.admin.execute(LocalAdministrator(), AdminCommand("setup", {
            "username": "admin", "password": "original-secret", "values": {},
        }))
        account = next(iter(server.accounts.values()))
        operator = server.operators[account.id]
        session = Session(server, transport="Local")
        server._add_session(session)
        verified = asyncio.Event()

        async def valid_password(*args):
            verified.set()
            return True

        monkeypatch.setattr(identity, "verify_password", valid_password)
        # A settings/account transaction owns the lock while hashing finishes.
        # Event.wait yields only after authentication has reached lock acquisition.
        async with server.lock:
            authentication = asyncio.create_task(server.authenticate(session, "admin", "original-secret", oper=True))
            await asyncio.wait_for(verified.wait(), 2)
            changed_account = account
            changed_operator = operator
            if mutation == "disable":
                changed_account = replace(account, enabled=False)
            elif mutation == "lock":
                changed_account = replace(account, locked=True)
            elif mutation == "password":
                changed_account = replace(account, password_hash="replacement-credential-hash")
            elif mutation == "disable_operator":
                changed_operator = replace(operator, enabled=False)
            if mutation == "remove_operator":
                await server.db.delete("operators", account.id)
                server.operators.pop(account.id)
            else:
                await server.db.transaction(puts=(("accounts", account.id, changed_account.to_record()), ("operators", account.id, changed_operator.to_record())))
                server.accounts[account.id] = changed_account
                server.operators[account.id] = changed_operator
        with pytest.raises(IRCError):
            await asyncio.wait_for(authentication, 2)
        assert server.accounts[account.id] == changed_account
        if mutation == "remove_operator":
            assert account.id not in server.operators
        else:
            assert server.operators[account.id] == changed_operator
        assert session.account_id is None
        assert session.operator_role is None
        assert not session.permissions
        records = await server.db.list("accounts")
        assert records[0]["password_hash"] == changed_account.password_hash
        assert records[0]["enabled"] == changed_account.enabled
        assert records[0]["locked"] == changed_account.locked
    finally:
        await server.close()


async def test_authentication_cannot_activate_a_disconnected_session(tmp_path, monkeypatch):
    server = OpenIRCServer(tmp_path)
    await server.initialize()
    try:
        await server.admin.execute(LocalAdministrator(), AdminCommand("setup", {
            "username": "admin", "password": "original-secret", "values": {},
        }))
        session = Session(server, transport="Local")
        server._add_session(session)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_verify(*args):
            entered.set()
            await release.wait()
            return True

        monkeypatch.setattr(identity, "verify_password", delayed_verify)
        authentication = asyncio.create_task(server.authenticate(session, "admin", "original-secret", oper=True))
        await asyncio.wait_for(entered.wait(), 2)
        await server.disconnect(session, "Closed while hashing")
        release.set()
        with pytest.raises(IRCError):
            await asyncio.wait_for(authentication, 2)
        assert session.id not in server.sessions
        assert session.account_id is None
        assert session.operator_role is None
    finally:
        await server.close()


async def test_stop_finishes_disconnect_when_maintenance_is_cancelled(tmp_path, monkeypatch):
    server = OpenIRCServer(tmp_path, {"irc_port": 0})
    await server.start()
    client = await IRCClient.connect(server, "DrainRace")
    try:
        session = server.find_user("DrainRace")
        server._maintenance_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await server._maintenance_task
        entered = asyncio.Event()
        release = asyncio.Event()
        cancelled = asyncio.Event()

        async def delayed_drain():
            entered.set()
            await release.wait()

        async def timeout_disconnect():
            try:
                await server.disconnect(session, "Registration timed out")
            finally:
                cancelled.set()

        monkeypatch.setattr(session.writer, "drain", delayed_drain)
        server._maintenance_task = asyncio.create_task(timeout_disconnect())
        await asyncio.wait_for(entered.wait(), 2)
        stop = asyncio.create_task(server.stop())
        # Stop cancels maintenance while its disconnect has an unfinished write.
        await asyncio.wait_for(cancelled.wait(), 2)
        release.set()
        await asyncio.wait_for(stop, 3)
        assert server.status == "Stopped"
        assert session.writer.is_closing()
        assert not server.sessions
        assert not server.listeners
        assert not [task for task in server._tasks if not task.done()]
    finally:
        await client.close()
        await server.close()
