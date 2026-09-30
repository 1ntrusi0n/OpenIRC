import asyncio
import base64

import pytest
import pytest_asyncio

from OpenIRC.core.server import OpenIRCServer
from OpenIRC.models.domain import AdminCommand, LocalAdministrator, Role
from .helpers import IRCClient


@pytest_asyncio.fixture
async def server(tmp_path):
    instance = OpenIRCServer(tmp_path, {"irc_port": 0, "commands_per_second": 100, "messages_per_second": 100})
    await instance.start()
    yield instance
    await instance.close()
    assert not [entry for entry in instance.logs if entry["category"] == "Errors"]


async def test_two_client_chat_host_and_protected_action(server):
    alice = await IRCClient.connect(server, "Alice", ircx=True)
    bob = await IRCClient.connect(server, "Bob")
    try:
        await alice.send("CREATE #test")
        await alice.until(" 366 ")
        await bob.send("JOIN #test")
        await bob.until(" 366 ")
        await alice.send("PRIVMSG #test :Hello from Alice")
        assert "Alice!" in (await bob.until("Hello from Alice"))[-1]
        await bob.send("TOPIC #test :not allowed")
        await bob.until(" 482 ")
        await alice.send("MODE #test +o Bob")
        await bob.until("MODE #test +o Bob")
        await bob.send("TOPIC #test :Now permitted")
        await alice.until("TOPIC #test :Now permitted")
        await bob.send("MODE #test -q Alice")
        await bob.until(" 482 ")
        assert server.find_channel("#test").members[server.find_user("Alice").id].role == Role.OWNER
    finally:
        await alice.close()
        await bob.close()


async def test_onjoin_onpart_only_target_session(server):
    alice = await IRCClient.connect(server, "Alice", ircx=True)
    bob = await IRCClient.connect(server, "Bob")
    try:
        await alice.send("CREATE #welcome")
        await alice.until(" 366 ")
        await alice.send("PROP #welcome ONJOIN :Welcome\\nBe kind", "PROP #welcome ONPART :Goodbye")
        await alice.barrier()
        await bob.send("JOIN #welcome")
        joined = await bob.until("Be kind")
        assert any(":#welcome PRIVMSG Bob :Welcome" in line for line in joined)
        await bob.send("PART #welcome")
        departed = await bob.until("Goodbye")
        assert any(":#welcome NOTICE Bob :Goodbye" in line for line in departed)
        others = await alice.barrier()
        assert not any("Welcome" in line or "Goodbye" in line for line in others)
    finally:
        await alice.close()
        await bob.close()


async def test_auditorium_visibility_and_role_transitions(server):
    host = await IRCClient.connect(server, "Host", ircx=True)
    one = await IRCClient.connect(server, "One")
    two = await IRCClient.connect(server, "Two")
    try:
        await host.send("CREATE #hall +x")
        await host.until(" 366 ")
        await one.send("JOIN #hall")
        await one.until(" 366 ")
        await two.send("JOIN #hall")
        names = await two.until(" 366 ")
        assert not any("One" in line for line in names)
        assert not any(":Two!" in line for line in await one.barrier())
        await one.send("PRIVMSG #hall :for moderators")
        await host.until("for moderators")
        assert not any("for moderators" in line for line in await two.barrier())
        await host.send("MODE #hall +o One")
        promoted = await two.until("MODE #hall +o One")
        assert any(":One!" in line and "JOIN" in line for line in promoted)
        await one.send("PRIVMSG #hall :host announcement")
        await two.until("host announcement")
        await host.send("MODE #hall -o One")
        hidden = await two.until("PART #hall")
        assert any(":One!" in line for line in hidden)
    finally:
        for client in (host, one, two):
            await client.close()


async def test_invite_moderation_key_limit_and_kick(server):
    owner = await IRCClient.connect(server, "Owner", ircx=True)
    guest = await IRCClient.connect(server, "Guest")
    try:
        await owner.send("CREATE #locked +ikl password123 2")
        await owner.until(" 366 ")
        await guest.send("JOIN #locked password123")
        await guest.until(" 473 ")
        await owner.send("INVITE Guest #locked")
        await guest.until("INVITE Guest")
        await guest.send("JOIN #locked wrong")
        await guest.until(" 475 ")
        await guest.send("JOIN #locked password123")
        await guest.until(" 366 ")
        await owner.send("MODE #locked +m")
        await guest.until("MODE #locked +m")
        await guest.send("PRIVMSG #locked :blocked")
        await guest.until(" 404 ")
        await owner.send("MODE #locked +v Guest")
        await guest.until("MODE #locked +v Guest")
        await guest.send("PRIVMSG #locked :voiced message")
        await owner.until("voiced message")
        await owner.send("KICK #locked Guest :Finished")
        await guest.until("KICK #locked Guest :Finished")
    finally:
        await owner.close()
        await guest.close()


async def test_local_chat_credentials_permissions_and_restart(server):
    await server.admin.execute(LocalAdministrator(), AdminCommand("setup", {"username": "admin", "password": "a good long password", "values": {"irc_port": 0}}))
    chat_id = await server.chat_connect("admin", "a good long password", "Console")
    assert server.sessions[chat_id].operator_role == "administrator"
    assert server.sessions[chat_id].transport == "Local"
    await server.chat_send(chat_id, "JOIN #console")
    client = await IRCClient.connect(server, "NetworkUser")
    try:
        await client.send("JOIN #console")
        await client.until(" 366 ")
        await server.chat_send(chat_id, "PRIVMSG #console :hello from console")
        await client.until("hello from console")
        await client.send("PRIVMSG #console :hello from network")
        await client.barrier()
        assert any("hello from network" in line for line in await server.chat_poll(chat_id))
    finally:
        await client.close()
    await server.stop()
    assert chat_id not in server.sessions
    await server.start()
    with pytest.raises(ValueError):
        await server.chat_send(chat_id, "JOIN #console")


async def test_unknown_notice_has_no_error_and_bad_client_isolated(server):
    client = await IRCClient.connect(server, "Good")
    try:
        await client.send("NOTICE Missing :hello")
        assert not any(" 401 " in line for line in await client.barrier())
        reader, writer = await asyncio.open_connection(*server.bound_addresses[0][:2])
        writer.write(b"NICK " + b"a" * 2000)
        await writer.drain()
        assert b"ERROR" in await asyncio.wait_for(reader.read(), 2)
        writer.close()
        await writer.wait_closed()
        await client.send("UNKNOWN")
        await client.until(" 421 ")
        assert server.status == "Running"
    finally:
        await client.close()
