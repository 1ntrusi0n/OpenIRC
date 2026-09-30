import asyncio
import base64
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from OpenIRC.models.domain import Channel, Membership, Role, new_id, wire_oid
from OpenIRC.core.permissions import has_permission
from OpenIRC.protocol.access import change_access
from OpenIRC.protocol.capabilities import decode_plain, handle_authenticate, handle_cap
from OpenIRC.protocol.dispatcher import dispatch
from OpenIRC.protocol.errors import IRCError
from OpenIRC.protocol.ircx_commands import matches_listx, parse_listx_query
from OpenIRC.protocol.modes import parse_modes, prefix_for_role
from OpenIRC.protocol.numerics import IRCX, SASL
from OpenIRC.protocol.parser import parse_line
from OpenIRC.protocol.properties import PROPERTIES, property_value, validate_property, change_property
from OpenIRC.protocol.unicode import casefold, decode_historical, encode_historical, expand_lines, fallback_nickname, restore_fallback, valid_channel, valid_nickname


def run(coro):
    return asyncio.run(coro)


class FakeSession:
    def __init__(self):
        self.id = "client"
        self.nick = "alice"
        self.username = "alice"
        self.prefix = "alice!alice@local"
        self.account_id = None
        self.registered = False
        self.ircx = False
        self.cap_negotiating = False
        self.caps = set()
        self.sasl_mechanism = None
        self.sasl_buffer = ""
        self.is_secure = True
        self.lines = []

    def send(self, command, *params, **kwargs):
        self.lines.append((command, params, kwargs))

    def numeric(self, code, *params, **kwargs):
        self.lines.append((int(code), params, kwargs))


def fake_server():
    return SimpleNamespace(settings={"server_name": "irc.test", "require_tls_auth": True},
                           maybe_register=AsyncMock(), authenticate=AsyncMock(), account_name=lambda _: "account",
                           has_permission=lambda *_: False)


def test_sasl_and_ircx_numeric_namespaces_remain_distinct():
    assert int(SASL.RPL_SASLSUCCESS) == int(IRCX.IRCERR_BADLEVEL) == 903
    assert type(SASL.RPL_SASLSUCCESS) is not type(IRCX.IRCERR_BADLEVEL)


def test_cap_registration_suspends_and_resumes_and_requests_are_atomic():
    server, session = fake_server(), FakeSession()
    run(handle_cap(server, session, ("LS", "302")))
    assert session.cap_negotiating
    assert session.lines[-1][2]["trailing"] == "sasl=PLAIN"
    run(handle_cap(server, session, ("REQ", "sasl unknown")))
    assert not session.caps
    assert session.lines[-1][1][-1] == "NAK"
    run(handle_cap(server, session, ("REQ", "sasl")))
    assert session.caps == {"sasl"}
    run(handle_cap(server, session, ("END",)))
    assert not session.cap_negotiating
    server.maybe_register.assert_awaited_once()


def test_sasl_plain_chunked_exact_400_requires_terminator():
    server, session = fake_server(), FakeSession()
    session.caps.add("sasl")
    payload = base64.b64encode(b"\x00user\x00" + b"x" * 294).decode()
    assert len(payload) == 400
    run(handle_authenticate(server, session, ("PLAIN",)))
    run(handle_authenticate(server, session, (payload,)))
    server.authenticate.assert_not_awaited()
    run(handle_authenticate(server, session, ("+",)))
    server.authenticate.assert_awaited_once_with(session, "user", "x" * 294)
    assert session.lines[-1][0] == int(SASL.RPL_SASLSUCCESS)
    assert session.sasl_buffer == "" and session.sasl_mechanism is None


def test_sasl_abort_clears_credentials_and_tls_is_required():
    server, session = fake_server(), FakeSession()
    session.caps.add("sasl")
    session.is_secure = False
    with pytest.raises(IRCError) as error:
        run(handle_authenticate(server, session, ("PLAIN",)))
    assert error.value.code == SASL.ERR_SASLFAIL
    session.is_secure = True
    run(handle_authenticate(server, session, ("PLAIN",)))
    session.sasl_buffer = "c2VjcmV0"
    with pytest.raises(IRCError):
        run(handle_authenticate(server, session, ("*",)))
    assert session.sasl_buffer == ""


@pytest.mark.parametrize("value", ["!!!!", base64.b64encode(b"root\x00user\x00password").decode(), base64.b64encode(b"\x00\x00password").decode(), base64.b64encode(b"\xff\x00user\x00password").decode()])
def test_plain_rejects_invalid_encoding_and_identity(value):
    with pytest.raises(ValueError):
        decode_plain(value)


def test_dispatch_registration_and_notice_silence():
    server, session = fake_server(), FakeSession()
    with pytest.raises(IRCError):
        run(dispatch(server, session, parse_line("JOIN #room")))
    run(dispatch(server, session, parse_line("NOTICE #room :hello")))
    assert session.lines == []
    run(dispatch(server, session, parse_line("MODE ISIRCX")))
    assert session.lines[-1][0] == int(IRCX.IRCRPL_IRCX)


def test_mode_arguments_and_owner_prefix():
    changes = parse_modes("+kl-o", ("key", "42", "bob"))
    assert [(c.adding, c.letter, c.argument) for c in changes] == [(True, "k", "key"), (True, "l", "42"), (False, "o", "bob")]
    assert prefix_for_role(Role.OWNER, True) == "."
    assert prefix_for_role(Role.OWNER, False) == "@"
    assert prefix_for_role(Role.HOST, True) == "@"
    with pytest.raises(ValueError):
        parse_modes("+l", ("-1",))
    with pytest.raises(ValueError):
        parse_modes("+d")


def test_properties_are_validated_and_secret_values_are_never_returned():
    channel = Channel(new_id(), wire_oid(), "#test")
    channel.secrets["OWNERKEY"] = "argon2-hash-that-is-not-public"
    assert property_value(channel, "OWNERKEY") == "*"
    assert property_value(channel, "HOSTKEY") == ""
    with pytest.raises(PermissionError):
        validate_property("OID", "forged")
    with pytest.raises(ValueError):
        validate_property("TOPIC", "\r\nPRIVMSG #x :spoof")
    with pytest.raises(ValueError):
        validate_property("LAG", "-5")
    for value in ("3", "nan", "inf"):
        with pytest.raises(ValueError):
            validate_property("LAG", value)
    assert validate_property("LAG", "0.25") == "0.25"
    with pytest.raises(ValueError):
        validate_property("CLIENTGUID", "invalid")
    assert validate_property("LANGUAGE", "en-US") == "en-US"


def test_listx_filters_registered_age_language_topic_and_limit():
    channel = Channel(new_id(), wire_oid(), "#test", created_at=1000, registered=True, topic="Hello people")
    channel.members["user"] = Membership("user")
    channels, filters, limit = parse_listx_query(("N=#t*,R=1,>0,C>1,L=en*,T=Hello*", "2"), 100)
    assert limit == 2
    assert matches_listx(channel, channels, filters, now=1200)
    channel.registered = False
    assert not matches_listx(channel, channels, filters, now=1200)
    with pytest.raises(ValueError):
        parse_listx_query(("R=3",))
    channel.topic = "Literal * wildcard"
    channels, filters, _ = parse_listx_query((r"T=Literal\b\*\bwildcard",))
    assert matches_listx(channel, channels, filters)
    channel.topic = "Literal anything wildcard"
    assert not matches_listx(channel, channels, filters)


def test_unicode_historical_roundtrip_fallback_and_casemapping():
    name = "cafè room,room\\other"
    assert decode_historical(encode_historical(name)) == name
    assert restore_fallback(fallback_nickname("猫")) == "猫"
    assert casefold("AbC[\\]^") == "abc{|}~"
    assert casefold("cafe\u0301") == casefold("café")
    assert valid_nickname("猫") and not valid_nickname("^1234")
    assert valid_channel("#café") and not valid_channel("#bad room")


def test_notice_escapes_split_without_frame_injection():
    assert expand_lines(r"Hello\nNext\r\nLast\tword") == ["Hello", "Next", "Last word"]
    assert len(expand_lines(r"x\n" * 100)) == 16


def service_server(channel):
    server = fake_server()
    server.lock = asyncio.Lock()
    server.find_channel = lambda name: channel if name == channel.name else None
    server.find_user = lambda name: None
    server.has_permission = has_permission
    server.visible_channel = lambda actor, target: actor.id in target.members
    server.persist_channel = AsyncMock()
    server.db = SimpleNamespace(audit=AsyncMock(), put=AsyncMock())
    server.db.audit_scope = lambda *args: nullcontext({})
    server.sessions = {}
    server.accounts = {}
    server.server_access = []
    server.network_access = []
    server.user_access = {}
    server.emit = lambda *args, **kwargs: None
    server.broadcast_channel = lambda *args, **kwargs: None
    return server


def test_channel_property_failed_persistence_leaves_state_unchanged():
    channel = Channel(new_id(), wire_oid(), "#test", registered=True, subject="original")
    actor = SimpleNamespace(id="owner", nick="owner", permissions=set())
    channel.members[actor.id] = Membership(actor.id, Role.OWNER)
    server = service_server(channel)
    server.persist_channel.side_effect = OSError("database full")
    with pytest.raises(OSError):
        run(change_property(server, actor, "#test", "SUBJECT", "new subject"))
    assert channel.subject == "original"
    server.db.audit.assert_not_awaited()


def test_channel_property_permissions_and_member_key_share_mode(monkeypatch):
    channel = Channel(new_id(), wire_oid(), "#test")
    actor = SimpleNamespace(id="host", nick="host", permissions=set())
    channel.members[actor.id] = Membership(actor.id, Role.HOST)
    server = service_server(channel)
    with pytest.raises(IRCError) as error:
        run(change_property(server, actor, "#test", "OWNERKEY", "secret"))
    assert error.value.code == IRCX.IRCERR_SECURITY
    monkeypatch.setattr("OpenIRC.security.passwords.hash_password", AsyncMock(return_value="test-hash"))
    run(change_property(server, actor, "#test", "MEMBERKEY", "secret"))
    assert channel.secrets["MEMBERKEY"] == "test-hash" and "k" in channel.modes
    assert "secret" not in repr(server.db.audit.call_args)
    run(change_property(server, actor, "#test", "MEMBERKEY", ""))
    assert "MEMBERKEY" not in channel.secrets and "k" not in channel.modes


def test_access_owner_protection_expiration_and_atomic_persistence():
    channel = Channel(new_id(), wire_oid(), "#test", registered=True)
    owner = SimpleNamespace(id="owner", nick="owner", permissions=set())
    host = SimpleNamespace(id="host", nick="host", permissions=set())
    channel.members[owner.id] = Membership(owner.id, Role.OWNER)
    channel.members[host.id] = Membership(host.id, Role.HOST)
    server = service_server(channel)
    records = run(change_access(server, owner, channel.name, "ADD", "DENY", "bad!*@*", 1, "reason"))
    assert records[0].expires_at - records[0].created_at == pytest.approx(60, abs=.1)
    with pytest.raises(IRCError) as error:
        run(change_access(server, host, channel.name, "DELETE", "DENY", "bad!*@*"))
    assert error.value.code == IRCX.IRCERR_NOACCESS
    with pytest.raises(IRCError):
        run(change_access(server, host, channel.name, "ADD", "OWNER", "host!*@*"))
    server.persist_channel.side_effect = OSError("database full")
    with pytest.raises(OSError):
        run(change_access(server, owner, channel.name, "ADD", "HOST", "helper!*@*"))
    assert [entry.level for entry in channel.access] == ["DENY"]
    channel.access[0].expires_at = 1
    assert run(change_access(server, owner, channel.name, "LIST")) == []


def test_ircx_auth_protocol_and_wrong_sequence():
    server, session = fake_server(), FakeSession()
    session.oid = "012345678"
    payload = base64.b64encode(b"\x00admin\x00password").decode()
    with pytest.raises(IRCError) as error:
        run(dispatch(server, session, parse_line("AUTH OPENIRC-PLAIN S :" + payload)))
    assert error.value.code == IRCX.IRCERR_BADCOMMAND
    run(dispatch(server, session, parse_line("AUTH OPENIRC-PLAIN I")))
    assert session.lines[-1][0] == "AUTH"
    run(dispatch(server, session, parse_line("AUTH OPENIRC-PLAIN S :" + payload)))
    assert session.lines[-1][1] == ("OPENIRC-PLAIN", "*", "account", "012345678")
    assert not session.ircx_auth_pending


def test_configuration_switches_disable_extensions():
    server, session = fake_server(), FakeSession()
    server.settings["ircx_enabled"] = False
    with pytest.raises(IRCError):
        run(dispatch(server, session, parse_line("IRCX")))
    assert not session.ircx
    server.settings["ircx_enabled"] = True
    session.registered = session.ircx = True
    server.settings["listx_enabled"] = False
    with pytest.raises(IRCError):
        run(dispatch(server, session, parse_line("LISTX")))


def test_stats_link_reply_uses_standard_message_and_byte_counter_order():
    server, session = fake_server(), FakeSession()
    session.registered = True
    server.has_permission = lambda actor, permission: permission == "view_server"
    target = SimpleNamespace(nick="peer", outgoing=SimpleNamespace(qsize=lambda: 3), messages_sent=11,
                             bytes_out=1234, messages_received=7, bytes_in=432, connected_at=0)
    server.sessions = {"peer": target}
    run(dispatch(server, session, parse_line("STATS l")))
    reply = next(line for line in session.lines if line[0] == 211)
    assert reply[1][:6] == ("peer", "3", "11", "1234", "7", "432")
    assert len(reply[1]) == 7


def test_property_authorization_is_rechecked_after_password_hash(monkeypatch):
    channel = Channel(new_id(), wire_oid(), "#test")
    actor = SimpleNamespace(id="owner", nick="owner", permissions=set())
    channel.members[actor.id] = Membership(actor.id, Role.OWNER)
    server = service_server(channel)

    async def hash_during_part(password):
        channel.members.pop(actor.id)
        return "secret-hash"

    monkeypatch.setattr("OpenIRC.security.passwords.hash_password", hash_during_part)
    with pytest.raises(IRCError) as error:
        run(change_property(server, actor, channel.name, "OWNERKEY", "secret"))
    assert error.value.code == IRCX.IRCERR_SECURITY
    assert "OWNERKEY" not in channel.secrets
    server.persist_channel.assert_not_awaited()


class TCPPeer:
    """A PING barrier reads complete protocol responses without timing sleeps."""
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.sequence = 0

    @classmethod
    async def connect(cls, server, nickname=None, ircx=False):
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_addresses[0][1])
        peer = cls(reader, writer)
        if ircx:
            await peer.command("IRCX")
        if nickname:
            await peer.command(f"NICK {nickname}\r\nUSER {nickname} 0 * :Test user")
        return peer

    async def command(self, command=""):
        self.sequence += 1
        marker = f"barrier{self.sequence}"
        payload = (command + "\r\n" if command else "") + f"PING :{marker}\r\n"
        self.writer.write(payload.encode())
        await self.writer.drain()
        result = []
        while True:
            raw = await asyncio.wait_for(self.reader.readline(), 3)
            assert raw, "Connection closed unexpectedly"
            message = parse_line(raw)
            if message.command == "PONG" and message.trailing == marker:
                return result
            result.append(message)

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()


def test_real_tcp_ircx_create_access_properties_and_private_notices(tmp_path):
    from OpenIRC.core.server import OpenIRCServer

    async def scenario():
        server = OpenIRCServer(tmp_path, {"irc_port": 0, "require_tls_auth": False,
                                          "commands_per_second": 1000, "messages_per_second": 1000})
        peers = []
        try:
            await server.start()
            owner = await TCPPeer.connect(server, "owner", ircx=True)
            peers.append(owner)
            host = await TCPPeer.connect(server, "helper", ircx=True)
            peers.append(host)
            member = await TCPPeer.connect(server, "member")
            peers.append(member)
            result = await owner.command("CREATE #room +ml 10")
            assert any(m.command == "CREATE" for m in result)
            assert server.find_channel("#room").limit == 10
            assert any(m.command == "353" and ".owner" in m.trailing for m in result)
            result = await owner.command("ACCESS #room ADD HOST helper!*@* 0 :Room helper")
            assert any(m.command == "801" for m in result)
            await owner.command(r"PROP #room ONJOIN :Welcome\nPlease be kind")
            await owner.command(r"PROP #room ONPART :See you\nGoodbye")
            await owner.command("PROP #room OWNERKEY :a-channel-secret")
            result = await host.command("JOIN #room")
            assert server.find_channel("#room").members[server.find_user("helper").id].role == Role.HOST
            assert {m.trailing for m in result if m.command == "PRIVMSG"} == {"Welcome", "Please be kind"}
            owner_messages = await owner.command()
            assert not any(m.trailing in {"Welcome", "Please be kind"} for m in owner_messages)
            result = await host.command("PROP #room OWNERKEY")
            assert any(m.command == "908" for m in result)
            assert "a-channel-secret" not in repr(result) and "$argon2" not in repr(result)
            result = await host.command("MODE #room -q owner")
            assert any(m.command == "482" for m in result)
            await member.command("JOIN #room")
            result = await member.command("PRIVMSG #room :blocked")
            assert any(m.command == "404" for m in result)
            await host.command("MODE #room +v member")
            await member.command("PRIVMSG #room :allowed")
            assert any(m.command == "PRIVMSG" and m.trailing == "allowed" for m in await owner.command())
            result = await member.command("PART #room")
            assert {m.trailing for m in result if m.command == "NOTICE"} == {"See you", "Goodbye"}
            assert not any(m.trailing in {"See you", "Goodbye"} for m in await owner.command())
            result = await owner.command("ACCESS #room LIST")
            assert [m.command for m in result if m.command in {"803", "804", "805"}] == ["803", "804", "805"]
            result = await owner.command("ACCESS #room DELETE HOST helper!*@*")
            assert any(m.command == "802" for m in result)
            assert not server.find_channel("#room").access
            assert not any(log["level"] == "ERROR" for log in server.logs)
        finally:
            for peer in peers:
                await peer.close()
            await server.close()
    run(scenario())


def test_real_tcp_listx_sequences_and_auditorium_visibility(tmp_path):
    from OpenIRC.core.server import OpenIRCServer

    async def scenario():
        server = OpenIRCServer(tmp_path, {"irc_port": 0, "listx_limit": 1, "commands_per_second": 1000})
        peers = []
        try:
            await server.start()
            owner = await TCPPeer.connect(server, "owner", ircx=True)
            peers.append(owner)
            alice = await TCPPeer.connect(server, "alice")
            peers.append(alice)
            bob = await TCPPeer.connect(server, "bob")
            peers.append(bob)
            await owner.command("CREATE #auditorium +x")
            await owner.command("CREATE #other")
            result = await owner.command("LISTX")
            assert [m.command for m in result] == ["811", "812", "816"]
            result = await owner.command("LISTX N=#aud*")
            assert [m.command for m in result] == ["811", "812", "817"]
            await alice.command("JOIN #auditorium")
            await bob.command("JOIN #auditorium")
            result = await alice.command("NAMES #auditorium")
            assert all("bob" not in (m.trailing or "") for m in result if m.command == "353")
            assert not any(m.command == "JOIN" and m.prefix.startswith("bob!") for m in result)
            await bob.command("PRIVMSG #auditorium :hidden message")
            assert not any(m.trailing == "hidden message" for m in await alice.command())
            assert any(m.trailing == "hidden message" for m in await owner.command())
            await owner.command("MODE #auditorium +o bob")
            result = await alice.command()
            assert any(m.command == "JOIN" and m.prefix.startswith("bob!") for m in result)
            await owner.command("MODE #auditorium -o bob")
            result = await alice.command()
            assert any(m.command == "PART" and m.prefix.startswith("bob!") for m in result)
            await bob.command("PART #auditorium")
            assert not any(m.command == "PART" and m.prefix.startswith("bob!") for m in await alice.command())
            assert not any(log["level"] == "ERROR" for log in server.logs)
        finally:
            for peer in peers:
                await peer.close()
            await server.close()
    run(scenario())


def test_real_tcp_sasl_and_ircx_authenticate_before_registration(tmp_path):
    from OpenIRC.core.server import OpenIRCServer
    from OpenIRC.models.domain import AdminCommand, LocalAdministrator

    async def scenario():
        server = OpenIRCServer(tmp_path, {"irc_port": 0, "allow_anonymous": False,
                                          "require_tls_auth": False, "commands_per_second": 1000})
        peers = []
        try:
            await server.initialize()
            await server.admin.execute(LocalAdministrator(), AdminCommand("account.save", {"username": "account", "password": "correct-password", "display_name": "Test"}))
            await server.start()
            sasl = await TCPPeer.connect(server)
            peers.append(sasl)
            await sasl.command("CAP LS 302")
            result = await sasl.command("NICK userone\r\nUSER userone 0 * :Test")
            assert not any(m.command == "001" for m in result)
            await sasl.command("CAP REQ :sasl")
            await sasl.command("AUTHENTICATE PLAIN")
            payload = base64.b64encode(b"\x00account\x00correct-password").decode()
            result = await sasl.command("AUTHENTICATE " + payload)
            assert any(m.command == "903" for m in result)
            assert not any(m.command == "001" for m in result)
            result = await sasl.command("CAP END")
            assert any(m.command == "001" for m in result)
            ircx = await TCPPeer.connect(server)
            peers.append(ircx)
            result = await ircx.command("MODE ISIRCX")
            assert any(m.command == "800" for m in result)
            result = await ircx.command("AUTH OPENIRC-PLAIN I :" + payload)
            assert any(m.command == "AUTH" and m.arguments[1] == "*" for m in result)
            result = await ircx.command("NICK usertwo\r\nUSER usertwo 0 * :Test")
            assert any(m.command == "001" for m in result)
            assert server.find_user("userone").account_id == server.find_user("usertwo").account_id
            assert "correct-password" not in repr(list(server.logs))
            assert not any(log["level"] == "ERROR" for log in server.logs)
        finally:
            for peer in peers:
                await peer.close()
            await server.close()
    run(scenario())


def test_real_tcp_historical_names_reverse_lookup_and_prefix_integrity(tmp_path):
    from OpenIRC.core.server import OpenIRCServer

    async def scenario():
        server = OpenIRCServer(tmp_path, {"irc_port": 0, "unicode_mode": "historical", "commands_per_second": 1000})
        peers = []
        try:
            await server.start()
            owner = await TCPPeer.connect(server, ircx=True)
            peers.append(owner)
            await owner.command("NICK %猫\r\nUSER owner 0 * :Unicode owner")
            ordinary = await TCPPeer.connect(server, "ordinary")
            peers.append(ordinary)
            await owner.command("CREATE %#茶")
            result = await owner.command("LISTX N=%#茶")
            assert any(m.command == "812" and m.arguments[1] == "%#茶" for m in result)
            result = await ordinary.command("JOIN %#茶")
            assert any(m.command == "353" and fallback_nickname("猫") in m.trailing for m in result)
            await ordinary.command(f"PRIVMSG {fallback_nickname('猫')} :hello unicode")
            assert any(m.trailing == "hello unicode" for m in await owner.command())
            await owner.command(":forged!host@host PRIVMSG %#茶 :prefix check")
            result = await ordinary.command()
            delivered = next(m for m in result if m.trailing == "prefix check")
            assert delivered.prefix.startswith(fallback_nickname("猫") + "!")
            await owner.command("NICK %狗")
            result = await ordinary.command()
            renamed = next(m for m in result if m.command == "NICK")
            assert renamed.prefix.startswith(fallback_nickname("猫") + "!")
            assert renamed.trailing == fallback_nickname("狗")
            result = await ordinary.command("WHOWAS " + fallback_nickname("猫"))
            assert any(m.command == "314" and m.arguments[1] == fallback_nickname("猫") for m in result)
            assert not any(log["level"] == "ERROR" for log in server.logs)
        finally:
            for peer in peers:
                await peer.close()
            await server.close()
    run(scenario())


def test_real_tcp_ircx_pending_auth_suspends_registration(tmp_path):
    from OpenIRC.core.server import OpenIRCServer
    from OpenIRC.models.domain import AdminCommand, LocalAdministrator

    async def scenario():
        server = OpenIRCServer(tmp_path, {"irc_port": 0, "require_tls_auth": False, "commands_per_second": 1000})
        peer = None
        try:
            await server.initialize()
            await server.admin.execute(LocalAdministrator(), AdminCommand("account.save", {"username": "account", "password": "correct-password"}))
            await server.start()
            peer = await TCPPeer.connect(server)
            await peer.command("AUTH OPENIRC-PLAIN I")
            result = await peer.command("NICK pending\r\nUSER pending 0 * :Test")
            assert not any(m.command == "001" for m in result)
            payload = base64.b64encode(b"\x00account\x00correct-password").decode()
            result = await peer.command("AUTH OPENIRC-PLAIN S :" + payload)
            assert any(m.command == "AUTH" for m in result)
            assert any(m.command == "001" for m in result)
        finally:
            if peer:
                await peer.close()
            await server.close()
    run(scenario())


def test_registered_protocol_mutation_rolls_back_when_atomic_audit_fails(tmp_path, monkeypatch):
    import sqlite3
    from OpenIRC.core.server import OpenIRCServer
    from OpenIRC.models.domain import AdminCommand, LocalAdministrator

    async def scenario():
        server = OpenIRCServer(tmp_path)
        actor = LocalAdministrator()
        try:
            await server.initialize()
            await server.admin.execute(actor, AdminCommand("channel.save", {"name": "#durable", "registered": True, "subject": "before"}))
            channel = server.find_channel("#durable")
            initial_records = await server.db.list("registered_channels")

            def fail_audit(scope):
                raise sqlite3.OperationalError("Injected audit failure")

            monkeypatch.setattr(server.db, "_append_audit", fail_audit)
            with pytest.raises(sqlite3.OperationalError):
                await server.change_property(actor, channel.name, "SUBJECT", "after")
            assert channel.subject == "before"
            assert await server.db.list("registered_channels") == initial_records
            with pytest.raises(sqlite3.OperationalError):
                await server.change_access(actor, channel.name, "ADD", "DENY", "bad!*@*")
            assert channel.access == []
            assert await server.db.list("registered_channels") == initial_records
        finally:
            await server.close()
    run(scenario())


def test_real_tcp_hidden_room_privacy_is_shared_by_queries(tmp_path):
    from OpenIRC.core.server import OpenIRCServer

    async def scenario():
        server = OpenIRCServer(tmp_path, {"irc_port": 0, "commands_per_second": 1000})
        peers = []
        try:
            await server.start()
            owner = await TCPPeer.connect(server, "owner", ircx=True)
            peers.append(owner)
            outsider = await TCPPeer.connect(server, "outsider", ircx=True)
            peers.append(outsider)
            for letter, room in (("p", "#private"), ("s", "#secret"), ("h", "#hidden")):
                await owner.command(f"CREATE {room} +{letter}")
                for query, disallowed in ((f"NAMES {room}", "353"), (f"WHO {room}", "352"), (f"LIST {room}", "322"), (f"LISTX {room}", "812")):
                    result = await outsider.command(query)
                    assert not any(m.command == disallowed for m in result), (query, result)
                result = await outsider.command(f"PROP {room} TOPIC")
                assert any(m.command == "924" for m in result)
            result = await outsider.command("WHOIS owner")
            assert not any(m.command == "319" for m in result)
            await owner.command("CREATE #auditorium +x")
            await outsider.command("JOIN #auditorium")
            peer = await TCPPeer.connect(server, "peer")
            peers.append(peer)
            await peer.command("JOIN #auditorium")
            result = await outsider.command("WHO #auditorium")
            assert not any(m.command == "352" and m.arguments[5] == "peer" for m in result)
            result = await outsider.command("WHOIS peer")
            assert not any(m.command == "319" for m in result)
        finally:
            for peer in peers:
                await peer.close()
            await server.close()
    run(scenario())
