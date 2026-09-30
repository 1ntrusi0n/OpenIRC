import asyncio
from datetime import datetime, timedelta, timezone
import ipaddress
import socket
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from OpenIRC.core.server import OpenIRCServer
from OpenIRC.models.domain import AdminCommand, LocalAdministrator
from .helpers import IRCClient


def certificate(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "test.pem", tmp_path / "test.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


async def test_tls_authentication_and_plaintext_policy(tmp_path):
    cert, key = certificate(tmp_path)
    server = OpenIRCServer(tmp_path / "data", {"irc_port": 0, "tls_port": 0, "tls_enabled": True, "tls_cert": str(cert), "tls_key": str(key)})
    await server.initialize()
    await server.admin.execute(LocalAdministrator(), AdminCommand("setup", {"username": "admin", "password": "very secure password", "values": {}}))
    try:
        await server.start()
        plain = await IRCClient.connect(server, "Plain")
        await plain.send("OPER admin :very secure password")
        await plain.until(" 464 ")
        context = ssl.create_default_context(cafile=str(cert))
        secure = await IRCClient.connect(server, "Secure", ssl=context, port=server.bound_addresses[1][1])
        await secure.send("OPER admin :very secure password")
        await secure.until(" 381 ")
        assert server.find_user("Secure").is_secure
        assert server.find_user("Secure").operator_role == "administrator"
        await plain.close()
        await secure.close()
    finally:
        await server.close()


async def test_invalid_tls_rolls_back_all_listeners_and_can_recover(tmp_path):
    server = OpenIRCServer(tmp_path, {"irc_port": 0, "tls_enabled": True, "tls_cert": "missing.pem", "tls_key": "missing.key"})
    try:
        with pytest.raises((ValueError, OSError)):
            await server.start()
        assert not server.listeners
        assert server.status == "Error"
        await server.admin.execute(LocalAdministrator(), AdminCommand("settings.save", {"values": {"tls_enabled": False}}))
        await server.start()
        assert server.status == "Running"
        client = await IRCClient.connect(server, "Recovered")
        await client.close()
    finally:
        await server.close()


async def test_bind_failure_rolls_back_plaintext_when_tls_port_occupied(tmp_path):
    cert, key = certificate(tmp_path)
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    server = OpenIRCServer(tmp_path / "data", {"irc_port": 0, "tls_enabled": True, "tls_port": occupied.getsockname()[1], "tls_cert": str(cert), "tls_key": str(key)})
    try:
        with pytest.raises(OSError):
            await server.start()
        assert server.listeners == []
    finally:
        occupied.close()
        await server.close()


async def test_state_survives_new_server_instance(tmp_path):
    local = LocalAdministrator()
    server = OpenIRCServer(tmp_path, {"irc_port": 0})
    await server.initialize()
    setup = await server.admin.execute(local, AdminCommand("setup", {"username": "admin", "password": "a persistent password", "values": {"motd": "Persisted greeting"}}))
    channel = await server.admin.execute(local, AdminCommand("channel.save", {"name": "#Persistent", "registered": True, "founder_id": setup["id"], "topic": "A lasting room", "modes": "nt"}))
    await server.admin.execute(local, AdminCommand("access.change", {"object": "#Persistent", "operation": "ADD", "level": "HOST", "mask": "Helper!*@*"}))
    await server.admin.execute(local, AdminCommand("ban.save", {"type": "cidr", "mask": "192.0.2.0/24", "reason": "Example ban"}))
    oid = server.find_channel("#Persistent").oid
    await server.close()
    second = OpenIRCServer(tmp_path, {"irc_port": 0})
    try:
        await second.start()
        assert second.settings.motd == "Persisted greeting"
        room = second.find_channel("#persistent")
        assert room.oid == oid and room.topic == "A lasting room"
        assert room.access[0].level == "HOST" and not room.members
        assert len(second.bans) == 1 and len(second.accounts) == 1
        chat = await second.chat_connect("admin", "a persistent password", "Admin")
        await second.chat_send(chat, "JOIN #Persistent")
        assert second.sessions[chat].id in second.find_channel("#persistent").members
    finally:
        await second.close()


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 not available")
async def test_ipv6_loopback(tmp_path):
    probe = socket.socket(socket.AF_INET6)
    try:
        probe.bind(("::1", 0))
    except OSError:
        pytest.skip("IPv6 loopback unavailable on this host")
    finally:
        probe.close()
    server = OpenIRCServer(tmp_path, {"irc_port": 0, "bind_address": "::1", "ipv6_enabled": True})
    try:
        await server.start()
        client = await IRCClient.connect(server, "IPv6User")
        await client.send("PING :ipv6-ok")
        await client.until("ipv6-ok")
        await client.close()
    finally:
        await server.close()


async def test_repeated_start_stop_and_concurrent_runtime_rejection(tmp_path):
    server = OpenIRCServer(tmp_path, {"irc_port": 0})
    try:
        for _ in range(3):
            await server.start()
            await server.start()
            client = await IRCClient.connect(server, "RestartTest")
            await server.stop()
            await server.stop()
            assert not server.sessions and not server.listeners
            await client.close()
        other = OpenIRCServer(tmp_path)
        with pytest.raises((OSError, RuntimeError)):
            await other.initialize()
    finally:
        await server.close()
