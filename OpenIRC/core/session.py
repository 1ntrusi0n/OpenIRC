"""A client identity plus a bounded, serialized output transport."""
from __future__ import annotations

import asyncio
import time
from collections import deque

from OpenIRC.models.domain import new_id
from .rate_limit import TokenBucket


class Session:
    def __init__(self, server, *, reader=None, writer=None, transport="TCP", ip="local", host="local"):
        self.server, self.reader, self.writer = server, reader, writer
        self.id = new_id()
        self.nick = self.username = self.realname = ""
        self.ip, self.host, self.transport = ip, host, transport
        self.registered = self.ircx = self.cap_negotiating = self.closing = False
        self.local_admin = False
        self.account_id = self.operator_role = self.password = self.sasl_mechanism = None
        self.permissions: set[str] = set()
        self.caps: set[str] = set()
        self.modes: set[str] = set()
        self.sasl_buffer = self.away = ""
        self.connected_at = self.last_activity = self.last_received = time.time()
        self.bytes_in = self.bytes_out = 0
        self.messages_received = self.messages_sent = 0
        self.oid = server.new_oid()
        self.pending_ping: float | None = None
        self.task: asyncio.Task | None = None
        self.writer_task: asyncio.Task | None = None
        self.outgoing: asyncio.Queue = asyncio.Queue(int(server.settings.get("send_queue_limit", 256)))
        self.commands = TokenBucket(server.settings.get("commands_per_second", 20))
        self.messages = TokenBucket(server.settings.get("messages_per_second", 5))
        self.nick_changes = TokenBucket(server.settings.get("nick_changes_per_minute", 6) / 60, 3)
        self.throttled_until = 0.0
        self.channel_message_times: dict[str, float] = {}

    @property
    def prefix(self) -> str:
        return f"{self.nick or '*'}!{self.username or 'unknown'}@{self.host}"

    @property
    def is_secure(self) -> bool:
        return self.transport in {"TLS", "Local"}

    def visible_nick(self, other) -> str:
        return self.server.render_nick(self, other)

    def send(self, command: str, *params, prefix: str | None = None, trailing: str | None = None) -> None:
        prefix = prefix or self.server.settings.get("server_name")
        prefix = self.server.render_prefix(self, prefix)
        rendered = [self.server.render_parameter(self, str(param)) for param in params]
        head = f":{prefix} {command}" + (" " + " ".join(rendered) if rendered else "")
        if trailing is not None:
            text = str(trailing).replace("\r", " ").replace("\n", " ").replace("\x00", "")
            budget = 510 - len((head + " :").encode("utf-8"))
            if budget < 0:
                self.server.schedule_disconnect(self, "Output line exceeds protocol limit")
                return
            while len(text.encode("utf-8")) > budget:
                text = text.encode("utf-8")[:budget].decode("utf-8", "ignore")
            head += " :" + text
        self.send_line(head)

    def numeric(self, code, *params, text: str | None = None) -> None:
        self.send(f"{int(code):03}", self.nick or "*", *params, trailing=text)

    send_numeric = numeric

    def send_line(self, line: str) -> None:
        if self.closing:
            return
        if any(char in line for char in "\r\n\x00") or len(line.encode("utf-8")) > 510:
            self.server.schedule_disconnect(self, "Invalid output frame")
            return
        try:
            self.outgoing.put_nowait(line)
        except asyncio.QueueFull:
            self.server.schedule_disconnect(self, "Send queue exceeded")

    async def write_loop(self) -> None:
        try:
            while True:
                line = await self.outgoing.get()
                payload = (line + "\r\n").encode("utf-8")
                self.writer.write(payload)
                await asyncio.wait_for(self.writer.drain(), 15)
                self.bytes_out += len(payload)
                self.messages_sent += 1
                self.server.statistics["bytes_sent"] += len(payload)
        except (ConnectionError, OSError, TimeoutError):
            self.server.schedule_disconnect(self, "Connection write failed")
        except asyncio.CancelledError:
            raise

    def poll(self) -> list[str]:
        lines = []
        while not self.outgoing.empty():
            line = self.outgoing.get_nowait()
            lines.append(line)
            self.bytes_out += len((line + "\r\n").encode("utf-8"))
            self.messages_sent += 1
        return lines
