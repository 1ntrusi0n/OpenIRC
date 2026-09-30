"""Casemapping and the optional historical IRCX object-name encoding."""
import re
import unicodedata

_RFC1459 = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ[]\\^", "abcdefghijklmnopqrstuvwxyz{}|~")
_ESCAPE = {" ": "b", ",": "c", "\\": "\\", "\r": "r", "\n": "n", "\t": "t"}
_UNESCAPE = {v: k for k, v in _ESCAPE.items()}


def casefold(value: str) -> str:
    # Unicode normalization avoids visually identical composed/decomposed IDs.
    return unicodedata.normalize("NFC", value).translate(_RFC1459).casefold()


def valid_nickname(name: str, max_bytes: int = 30) -> bool:
    if not name or len(name.encode("utf-8")) > max_bytes:
        return False
    if name[0].isdigit() or name[0] in "-:#&+!@%" or name.startswith("^"):
        return False
    return all(not c.isspace() and not unicodedata.category(c).startswith("C") and c not in " ,:*?!@.#&%" for c in name)


def valid_channel(name: str, max_bytes: int = 64) -> bool:
    return bool(name and name.startswith(("#", "&", "%#", "%&")) and len(name.encode("utf-8")) <= max_bytes
                and len(name) > 1 and all(not c.isspace() and not unicodedata.category(c).startswith("C") and c not in ",:\x07" for c in name))


def encode_historical(value: str) -> str:
    if value.isascii() and not any(c in value for c in _ESCAPE) and not value.startswith("%"):
        return value
    return "%" + "".join("\\" + _ESCAPE[c] if c in _ESCAPE else c for c in value)


def decode_historical(value: str) -> str:
    if not value.startswith("%"):
        return value
    value = value[1:]
    result: list[str] = []
    index = 0
    while index < len(value):
        c = value[index]
        if c == "\\":
            index += 1
            if index >= len(value) or value[index] not in _UNESCAPE:
                raise ValueError("Invalid IRCX object-name escape")
            c = _UNESCAPE[value[index]]
        result.append(c)
        index += 1
    return "".join(result)


def fallback_nickname(name: str) -> str:
    return "^" + name.encode("utf-8").hex().upper()


def restore_fallback(name: str) -> str:
    if not name.startswith("^"):
        return name
    try:
        return bytes.fromhex(name[1:]).decode("utf-8")
    except (ValueError, UnicodeError):
        return name


def wire_name(name: str, *, ircx: bool, historical: bool, channel: bool = False) -> str:
    if not historical:
        return name
    if ircx or channel:
        return encode_historical(name)
    return fallback_nickname(name) if not name.isascii() else name


def incoming_name(name: str, *, historical: bool) -> str:
    if not historical:
        return name
    return restore_fallback(decode_historical(name))


def expand_lines(text: str, limit: int = 16) -> list[str]:
    """Expand recognized notice escapes without allowing protocol injection."""
    expanded = re.sub(r"\\([nrbct\\])", lambda m: _UNESCAPE[m[1]], text)
    return [line.replace("\x00", "").replace("\t", " ") for line in expanded.replace("\r\n", "\n").replace("\r", "\n").split("\n")[:limit]]
