"""Exercise real server services while stopped, running, and after reopening."""
import asyncio
import sqlite3

import pytest

from OpenIRC.admin.commands import AdminError
from OpenIRC.core.server import OpenIRCServer
from OpenIRC.models.domain import AdminCommand, LocalAdministrator, Principal, Role


async def command(server, action, **payload):
    return await server.admin.execute(LocalAdministrator(), AdminCommand(action, payload))


async def setup(server):
    await server.initialize()
    return (await command(server, "setup", username="admin", password="admin-password", values={"irc_port": 0}))["id"]


def test_stopped_crud_and_restart_persistence(tmp_path):
    async def run():
        server = OpenIRCServer(tmp_path)
        await setup(server)
        try:
            account_id = (await command(server, "account.save", username="alice", display_name="Alice", password="first-password"))["id"]
            await command(server, "account.password", id=account_id, password="second-password")
            await command(server, "operator.save", account_id=account_id, role="moderator", enabled=True)
            await command(server, "reservation.save", nickname="AliceNick", account_id=account_id, protected=True)
            channel_id = (await command(server, "channel.save", name="#persist", registered=True, founder_id=account_id, topic="Persistent topic", modes="ntx",
                                        properties={"ONJOIN": "Hello\nBe kind", "ONPART": "Goodbye"}, secrets={"OWNERKEY": "owner-secret"}))["id"]
            await command(server, "access.change", object="#persist", operation="ADD", level="HOST", mask="AliceNick!*@*")
            await command(server, "access.change", object="*", operation="ADD", level="DENY", mask="Bad!*@*")
            await command(server, "access.change", object=server.settings.server_name, operation="ADD", level="DENY", mask="192.0.2.0/24")
            ban_id = (await command(server, "ban.save", type="cidr", mask="198.51.100.0/24", reason="test ban"))["id"]
            await command(server, "settings.save", values={"motd": "Line one\nLine two", "reservation_policy": "require"})
            with pytest.raises(AdminError, match="Transfer"):
                await command(server, "account.delete", id=account_id)
            snapshot = await server.admin.snapshot(LocalAdministrator())
            assert snapshot.status == "Stopped" and len(snapshot.accounts) == 2
            assert snapshot.channels[0]["access"][0]["level"] == "HOST"
            assert "owner-secret" not in str(snapshot) and "$argon2" not in str(snapshot)
        finally:
            await server.close()
        server = OpenIRCServer(tmp_path)
        await server.initialize()
        try:
            assert server.settings.motd == "Line one\nLine two"
            assert server.accounts[account_id].display_name == "Alice"
            assert server.operators[account_id].role == "moderator"
            assert server.find_channel("#persist").id == channel_id
            assert server.find_channel("#persist").access[0].mask == "AliceNick!*@*"
            assert server.server_access[0].mask == "192.0.2.0/24"
            assert server.network_access[0].mask == "Bad!*@*"
            assert server.bans[ban_id].active
            assert server.reservations["alicenick"]["account_id"] == account_id
            await command(server, "access.change", object="#persist", operation="CLEAR")
            await command(server, "ban.delete", id=ban_id)
            await command(server, "reservation.delete", nickname="AliceNick")
            await command(server, "operator.delete", account_id=account_id)
            await command(server, "channel.unregister", id=channel_id)
            await command(server, "account.delete", id=account_id)
            assert not server.bans and not server.reservations
            assert account_id not in server.accounts and not server.find_channel("#persist")
        finally:
            await server.close()
    asyncio.run(run())


def test_chat_live_moderation_revocation_and_stopped_management(tmp_path):
    async def run():
        server = OpenIRCServer(tmp_path)
        admin_id = await setup(server)
        try:
            await command(server, "start")
            session_id = (await command(server, "chat.connect", username="admin", password="admin-password", nickname="IndependentNickname"))["id"]
            session = server.sessions[session_id]
            assert session.transport == "Local" and session.operator_role == "administrator"
            initial = await command(server, "chat.poll", id=session_id)
            assert any(" 001 " in line for line in initial)
            await command(server, "access.change", object="IndependentNickname", operation="ADD", level="DENY", mask="Blocked!*@*")
            channel_id = (await command(server, "channel.save", name="#managed", registered=True, founder_id=admin_id, modes="int"))["id"]
            await command(server, "connection.join", id=session_id, channel="#managed")
            assert session_id in server.find_channel("#managed").members
            await command(server, "channel.topic", id=channel_id, topic="Changed live")
            await command(server, "channel.role", id=channel_id, session_id=session_id, role="Voice")
            assert server.find_channel("#managed").members[session_id].role == Role.VOICE
            await command(server, "connection.nick", id=session_id, nickname="Renamed")
            await command(server, "connection.notice", id=session_id, message="Private admin notice")
            await command(server, "broadcast", target="#managed", message="Channel broadcast")
            lines = await command(server, "chat.poll", id=session_id)
            assert any("Private admin notice" in line for line in lines)
            assert any("Channel broadcast" in line for line in lines)
            await command(server, "channel.close", id=channel_id)
            assert server.find_channel("#managed").registered and not server.find_channel("#managed").members
            await command(server, "chat.send", id=session_id, line="PING :still-connected")
            assert any("PONG" in line for line in await command(server, "chat.poll", id=session_id))
            await command(server, "operator.save", account_id=admin_id, role="administrator", enabled=False)
            assert not session.permissions and not session.operator_role
            with pytest.raises(AdminError):
                await command(server, "chat.send", id=session_id, line="JOIN #forbidden")
            await command(server, "stop")
            assert not server.sessions
            await command(server, "account.save", id=admin_id, locked=True)
            assert server.accounts[admin_id].locked
            await command(server, "account.save", id=admin_id, locked=False)
            await command(server, "channel.delete", id=channel_id)
            assert not server.find_channel("#managed")
        finally:
            await server.close()
        server = OpenIRCServer(tmp_path)
        await server.initialize()
        try:
            assert server.user_access[admin_id][0].mask == "Blocked!*@*"
        finally:
            await server.close()
    asyncio.run(run())


def test_pending_listener_identity_configuration_and_failed_storage(tmp_path):
    async def run():
        server = OpenIRCServer(tmp_path)
        await setup(server)
        try:
            await server.start()
            old_name = server.settings.server_name
            changed = await command(server, "settings.save", values={"server_name": "new.example.net", "unicode_mode": "historical", "motd": "Live message"})
            assert set(changed["restart_required"]) == {"server_name", "unicode_mode"}
            assert server.settings.server_name == old_name and server.settings.unicode_mode == "modern"
            assert server.settings.motd == "Live message"
            snapshot = await server.admin.snapshot(LocalAdministrator())
            assert snapshot.settings["server_name"] == "new.example.net"
            assert snapshot.statistics["runtime_settings"]["server_name"] == old_name
            await command(server, "restart")
            assert server.settings.server_name == "new.example.net" and server.settings.unicode_mode == "historical"
            assert not server.pending_restart
            original = server.db.save_settings
            async def failure(*args):
                raise sqlite3.OperationalError("disk full")
            server.db.save_settings = failure
            with pytest.raises(AdminError, match="database could not save"):
                await command(server, "settings.save", values={"motd": "Must not publish"})
            assert server.settings.motd == "Live message" and server.saved_settings.motd == "Live message"
            server.db.save_settings = original
        finally:
            await server.close()
    asyncio.run(run())


def test_remote_permissions_and_disabled_account_chat(tmp_path):
    async def run():
        server = OpenIRCServer(tmp_path)
        account_id = await setup(server)
        try:
            principal = Principal("remote", "remote", frozenset({"view_server"}))
            snapshot = await server.admin.snapshot(principal)
            assert not snapshot.accounts and not snapshot.operators and not snapshot.audit
            for action, payload in (("settings.save", {"values": {"irc_port": 7000}}), ("chat.connect", {"username": "admin", "password": "admin-password", "nickname": "Denied"})):
                with pytest.raises(AdminError, match="permission|local desktop"):
                    await server.admin.execute(principal, AdminCommand(action, payload))
            await server.start()
            session_id = (await command(server, "chat.connect", username="admin", password="admin-password", nickname="Admin"))["id"]
            await command(server, "account.save", id=account_id, enabled=False)
            assert server.sessions.get(session_id) is None or not server.sessions[session_id].permissions
            with pytest.raises(AdminError, match="Authentication failed"):
                await command(server, "chat.connect", username="admin", password="admin-password", nickname="Disabled")
        finally:
            await server.close()
    asyncio.run(run())
