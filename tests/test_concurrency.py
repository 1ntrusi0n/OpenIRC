import asyncio
import time

import pytest

from OpenIRC.core.server import OpenIRCServer
from OpenIRC.models.domain import AdminCommand, LocalAdministrator
from OpenIRC.protocol.errors import IRCError
from .helpers import IRCClient


async def test_initial_key_not_published_until_create_ready(tmp_path, monkeypatch):
    import OpenIRC.core.mode_operations as modes
    original = modes.hash_password
    entered, release = asyncio.Event(), asyncio.Event()
    async def held_hash(value):
        entered.set()
        await release.wait()
        return await original(value)
    monkeypatch.setattr(modes, "hash_password", held_hash)
    server = OpenIRCServer(tmp_path, {"irc_port": 0})
    await server.start()
    owner = await IRCClient.connect(server, "Owner", ircx=True)
    try:
        await owner.send("CREATE #atomic +k :room-secret")
        await asyncio.wait_for(entered.wait(), 3)
        assert server.find_channel("#atomic") is None
        release.set()
        await owner.until(" 366 ")
        visitor = await IRCClient.connect(server, "Visitor")
        try:
            await visitor.send("JOIN #atomic")
            await visitor.until(" 475 ")
        finally:
            await visitor.close()
    finally:
        release.set()
        await owner.close()
        await server.close()


async def test_lag_rechecks_membership_after_delay(tmp_path):
    server = OpenIRCServer(tmp_path, {"irc_port": 0, "messages_per_second": 100})
    await server.start()
    owner = await IRCClient.connect(server, "Owner", ircx=True)
    member = await IRCClient.connect(server, "Member")
    try:
        await owner.send("CREATE #lag")
        await owner.until(" 366 ")
        await member.send("JOIN #lag")
        await member.until(" 366 ")
        await server.change_property(LocalAdministrator(), "#lag", "LAG", "0.2")
        session = server.find_user("Member")
        await server.route_message(session, "#lag", "first")
        task = asyncio.create_task(server.route_message(session, "#lag", "should not be delivered"))
        # Yield one loop turn to let pacing begin, then remove the member.
        await asyncio.sleep(0)
        await server.part(session, "#lag")
        with pytest.raises(IRCError):
            await task
        assert not any("should not be delivered" in line for line in await owner.barrier())
    finally:
        await owner.close()
        await member.close()
        await server.close()


async def test_whisper_cannot_target_another_channel(tmp_path):
    server = OpenIRCServer(tmp_path, {"irc_port": 0})
    await server.start()
    sender = await IRCClient.connect(server, "Sender", ircx=True)
    receiver = await IRCClient.connect(server, "Receiver")
    try:
        await sender.send("CREATE #source")
        await sender.until(" 366 ")
        await receiver.send("JOIN #destination")
        await receiver.until(" 366 ")
        await sender.send("WHISPER #source #destination :must not broadcast")
        await sender.until(" 401 ")
        assert not any("must not broadcast" in line for line in await receiver.barrier())
    finally:
        await sender.close()
        await receiver.close()
        await server.close()


async def test_unknown_command_counters_and_transient_oids_are_bounded(tmp_path):
    from OpenIRC.protocol.parser import parse_line
    server = OpenIRCServer(tmp_path, {"irc_port": 0, "commands_per_second": 1000})
    await server.start()
    client = await IRCClient.connect(server, "Client")
    try:
        session = server.find_user("Client")
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            await server.process_message(session, parse_line("UNKNOWN" + letter))
        assert server.command_counts["UNKNOWN"] == 26
        assert not any(key.startswith("UNKNOWN") and key != "UNKNOWN" for key in server.command_counts)
        await server.join(session, "#temporary")
        assert len(server._wire_ids) == 2
        await server.part(session, "#temporary")
        assert len(server._wire_ids) == 1
        await server.disconnect(session)
        assert not server._wire_ids
    finally:
        await client.close()
        await server.close()
