"""Strict incremental CRLF framing with a fixed per-line byte ceiling."""
from .message import COMMAND, IRCMessage


class ParseError(ValueError):
    """Malformed or oversized client input; close the offending connection."""


def parse_line(line: str | bytes, max_bytes: int = 512) -> IRCMessage:
    try:
        raw = line.encode("utf-8") if isinstance(line, str) else bytes(line)
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        if len(raw) + 2 > max_bytes:
            raise ParseError("IRC line exceeds byte limit")
        text = raw.decode("utf-8", "strict")
    except (UnicodeError, TypeError) as exc:
        raise ParseError("Invalid UTF-8") from exc
    if not text or any(c in text for c in "\r\n\x00"):
        raise ParseError("Empty line or forbidden control character")
    prefix = None
    if text.startswith(":"):
        prefix, separator, text = text[1:].partition(" ")
        if not separator or not prefix:
            raise ParseError("Malformed prefix")
        text = text.lstrip(" ")
    command, _, rest = text.partition(" ")
    if not COMMAND.fullmatch(command):
        raise ParseError("Invalid command")
    params: list[str] = []
    trailing = None
    while rest:
        rest = rest.lstrip(" ")
        if not rest:
            break
        if rest.startswith(":"):
            trailing = rest[1:]
            break
        parameter, _, rest = rest.partition(" ")
        params.append(parameter)
    if len(params) + (trailing is not None) > 15:
        raise ParseError("Too many parameters")
    return IRCMessage(command.upper(), tuple(params), trailing, prefix)


class IRCParser:
    def __init__(self, max_bytes: int = 512):
        if max_bytes < 64:
            raise ValueError("Line limit must be at least 64 bytes")
        self.max_bytes = max_bytes
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> list[IRCMessage]:
        messages = []
        # Process each frame before retaining a partial frame. Coalesced input
        # can contain many valid lines without consuming an unbounded buffer.
        start = 0
        while start < len(data):
            end = data.find(b"\n", start)
            chunk = data[start:] if end < 0 else data[start:end + 1]
            if len(self._buffer) + len(chunk) > self.max_bytes:
                self._buffer.clear()
                raise ParseError("IRC line exceeds byte limit")
            self._buffer.extend(chunk)
            if end < 0:
                if len(self._buffer) >= self.max_bytes or b"\x00" in self._buffer:
                    self._buffer.clear()
                    raise ParseError("Invalid incomplete IRC line")
                break
            raw = bytes(self._buffer)
            self._buffer.clear()
            if not raw.endswith(b"\r\n"):
                raise ParseError("IRC lines must end with CRLF")
            if raw != b"\r\n":
                messages.append(parse_line(raw, self.max_bytes))
            start = end + 1
        return messages

    feed_data = feed


LineBuffer = IRCParser
