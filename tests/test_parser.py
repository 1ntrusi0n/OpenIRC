import pytest

from OpenIRC.protocol.message import IRCMessage
from OpenIRC.protocol.parser import IRCParser, ParseError, parse_line


def test_normal_and_trailing_parameters():
    message = parse_line(":alice!user@host PRIVMSG #room :Hello world\r\n")
    assert message.prefix == "alice!user@host"
    assert message.command == "PRIVMSG"
    assert message.params == ("#room",)
    assert message.trailing == "Hello world"
    assert message.arguments == ("#room", "Hello world")


def test_empty_trailing_is_distinct_from_missing_trailing():
    assert parse_line("TOPIC #room :").arguments == ("#room", "")
    assert parse_line("TOPIC #room").arguments == ("#room",)


def test_split_multibyte_tcp_and_coalesced_lines():
    parser = IRCParser()
    first = "PRIVMSG #room :hé".encode()
    assert parser.feed(first[:-1]) == []
    messages = parser.feed(first[-1:] + b"\r\nPING :next\r\n")
    assert [m.command for m in messages] == ["PRIVMSG", "PING"]
    assert messages[0].trailing == "hé"
    assert parser.buffered_bytes == 0


def test_512_byte_boundary_counts_crlf_and_utf8_bytes():
    prefix = b"PRIVMSG #a :"
    valid = prefix + b"x" * (510 - len(prefix)) + b"\r\n"
    assert len(valid) == 512
    assert len(IRCParser().feed(valid)) == 1
    with pytest.raises(ParseError):
        IRCParser().feed(valid[:-2] + b"x\r\n")
    with pytest.raises(ParseError):
        parse_line("PRIVMSG #a :" + "é" * 250)


@pytest.mark.parametrize("line", ["", ":", ":prefix", "1234 x", "COM1 x", "PING a\x00b", "PING a\nb", "PING a\rb"])
def test_rejects_malformed_lines(line):
    with pytest.raises(ParseError):
        parse_line(line)


@pytest.mark.parametrize("data", [b"PING x\n", b"PING :\xff\r\n", b"x" * 513, b"x" * 512])
def test_rejects_bad_framing_encoding_and_unterminated_lines(data):
    with pytest.raises(ParseError):
        IRCParser().feed(data)


def test_parameter_limit():
    assert len(parse_line("CMD " + " ".join(str(i) for i in range(15))).params) == 15
    with pytest.raises(ParseError):
        parse_line("CMD " + " ".join(str(i) for i in range(16)))


def test_serialization_roundtrip_and_safe_truncation():
    message = IRCMessage("notice", ("Alice",), "é" * 300, "server")
    with pytest.raises(ValueError):
        message.serialize()
    wire = message.serialize(truncate=True)
    assert len(wire) <= 512
    parsed = parse_line(wire)
    assert parsed.command == "NOTICE"
    assert "�" not in parsed.trailing


@pytest.mark.parametrize("message", [IRCMessage("PRIVMSG", ("a b",)), IRCMessage("NOTICE", ("a",), "x\r\nOPER admin pw"), IRCMessage("PING", prefix="bad host")])
def test_serializer_refuses_protocol_injection(message):
    with pytest.raises(ValueError):
        message.serialize()


def test_many_valid_frames_do_not_trip_per_line_limit():
    parser = IRCParser()
    assert len(parser.feed(b"PING :x\r\n" * 1000)) == 1000
    assert parser.buffered_bytes == 0
