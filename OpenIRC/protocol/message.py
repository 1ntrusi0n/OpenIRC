"""Validated IRC messages and bounded wire serialization."""
from dataclasses import dataclass
import re

COMMAND = re.compile(r"(?:[A-Za-z]+|[0-9]{3})\Z")


@dataclass(frozen=True, slots=True)
class IRCMessage:
    command: str
    params: tuple[str, ...] = ()
    trailing: str | None = None
    prefix: str | None = None

    @property
    def arguments(self) -> tuple[str, ...]:
        return tuple(self.params) + (() if self.trailing is None else (self.trailing,))

    def serialize(self, max_bytes: int = 512, *, truncate: bool = False) -> bytes:
        if not COMMAND.fullmatch(self.command):
            raise ValueError("Invalid IRC command")
        if len(self.arguments) > 15:
            raise ValueError("IRC messages allow at most 15 parameters")
        for value in self.params:
            if not value or value.startswith(":") or any(c in value for c in " \r\n\x00"):
                raise ValueError("Invalid middle parameter")
        if self.prefix is not None and (not self.prefix or any(c in self.prefix for c in " \r\n\x00")):
            raise ValueError("Invalid prefix")
        if self.trailing is not None and any(c in self.trailing for c in "\r\n\x00"):
            raise ValueError("Invalid trailing parameter")
        head = (f":{self.prefix} " if self.prefix else "") + self.command.upper()
        if self.params:
            head += " " + " ".join(self.params)
        if self.trailing is not None:
            head += " :"
            overhead = len(head.encode("utf-8")) + 2
            tail = self.trailing.encode("utf-8")
            if truncate and overhead <= max_bytes:
                tail = tail[:max_bytes - overhead].decode("utf-8", "ignore").encode("utf-8")
            data = head.encode("utf-8") + tail + b"\r\n"
        else:
            data = head.encode("utf-8") + b"\r\n"
        if len(data) > max_bytes:
            raise ValueError("IRC message exceeds byte limit")
        return data

    def __str__(self) -> str:
        return self.serialize().decode("utf-8").removesuffix("\r\n")
