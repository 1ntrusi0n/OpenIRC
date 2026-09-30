import asyncio
from types import SimpleNamespace
import pytest

from OpenIRC.config.settings import ServerSettings
from OpenIRC.models.domain import Ban, new_id
from OpenIRC.security.passwords import hash_password, verify_password
from OpenIRC.security.masks import matches_mask, matches_ban, wildcard_match, cloak_host
from OpenIRC.security.runtime_lock import RuntimeLock
from OpenIRC.security.tls import create_server_context


def test_argon2id_credentials_and_malformed_hashes():
    async def run():
        first = await hash_password("correct horse battery staple")
        second = await hash_password("correct horse battery staple")
        assert first.startswith("$argon2id$") and first != second
        assert await verify_password(first, "correct horse battery staple")
        assert not await verify_password(first, "incorrect")
        assert not await verify_password("bad hash", "password")
        with pytest.raises(ValueError):
            await hash_password("")
    asyncio.run(run())


def test_masks_and_bans_do_not_interpret_regex():
    assert matches_mask("Alice!*@*", "alice", "user", "host", "192.0.2.3")
    assert matches_mask("192.0.2.0/24", ip="192.0.2.3")
    assert not matches_mask("192.0.2.0/24", ip="198.51.100.3")
    assert matches_mask("$a:alice", account="ALICE")
    assert matches_mask("*!*@*$*", "Alice", "user", "host")
    assert matches_mask("Alice!*@*$irc.*", "alice", "user", "host", server_name="irc.example.net")
    assert not matches_mask("*!*@*$other.example.net", "Alice", "user", "host", server_name="irc.example.net")
    assert wildcard_match("[nick]", "[nick]")
    assert not wildcard_match("n", "[nick]")
    assert wildcard_match("a" * 1024, "*")
    assert not wildcard_match("a" * 1025, "*")
    session = SimpleNamespace(nick="Alice", username="alice", host="hidden.openirc", ip="192.0.2.3")
    assert matches_ban(Ban(new_id(), "account", "trusted*"), session, "trusted-account")
    assert matches_ban(Ban(new_id(), "ip", "192.0.2.3"), session)
    assert cloak_host(session.ip, "secret") != cloak_host(session.ip, "different-secret")


def test_runtime_lock_blocks_second_instance_and_recovers(tmp_path):
    first, second = RuntimeLock(tmp_path), RuntimeLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="already open"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_configuration_validation_and_tls_failure(tmp_path):
    settings = ServerSettings.from_dict({"_cloak_secret": "internal", "irc_port": 0})
    assert settings.get("irc_port") == 0
    with pytest.raises(ValueError):
        settings.updated({"irc_port": 65536})
    with pytest.raises(ValueError):
        settings.updated({"allow_anonymous": "false"})
    with pytest.raises(ValueError):
        settings.updated({"server_name": "bad\r\ninjection"})
    with pytest.raises(ValueError):
        settings.updated({"unknown": True})
    with pytest.raises(ValueError):
        settings.updated({"tls_enabled": True})
    with pytest.raises(ValueError, match="does not exist"):
        create_server_context(str(tmp_path / "missing.crt"), str(tmp_path / "missing.key"))
    assert settings.updated({"tls_key": "private.pem"}).to_dict()["tls_key"] == ""
