"""Asyncio server lifecycle; this module deliberately has no Qt dependency."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import logging.handlers
import queue
import secrets
import socket
import time
from collections import deque
from pathlib import Path

from OpenIRC.admin.service import AdminService
from OpenIRC.config.settings import ServerSettings
from OpenIRC.models.domain import Account, AccessEntry, Ban, Channel, Operator, wire_oid
from OpenIRC.persistence.database import Database
from OpenIRC.protocol.errors import IRCError
from OpenIRC.protocol.numerics import IRC as N
from OpenIRC.protocol.parser import LineBuffer, parse_line
from OpenIRC.protocol.unicode import casefold, incoming_name, wire_name
from OpenIRC.security.masks import matches_ban
from OpenIRC.security.runtime_lock import RuntimeLock
from OpenIRC.security.tls import create_server_context
from .channels import ChannelOperations
from .events import EventBus
from .identity import IdentityOperations
from .mode_operations import ModeOperations
from .permissions import has_permission
from .rate_limit import FailureWindow, TokenBucket
from .session import Session


class OpenIRCServer(IdentityOperations, ChannelOperations, ModeOperations):
    """Own all live state on one event loop and expose asynchronous services."""

    def __init__(self, data_dir: Path | str, settings_overrides: dict | None = None):
        self.data_dir = Path(data_dir)
        self.settings = ServerSettings()
        self.saved_settings = self.settings
        self.pending_restart: set[str] = set()
        self._overrides = settings_overrides or {}
        self.db = Database(self.data_dir / "openirc.sqlite3")
        self.runtime_lock = RuntimeLock(self.data_dir)
        self.lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._initialized = False
        self._closed = False
        self.status = "Stopped"
        self.sessions: dict[str, Session] = {}
        self.channels: dict[str, Channel] = {}
        self.accounts: dict[str, Account] = {}
        self.operators: dict[str, Operator] = {}
        self.bans: dict[str, Ban] = {}
        self.reservations: dict[str, dict] = {}
        self.server_access: list[AccessEntry] = []
        self.network_access: list[AccessEntry] = []
        self.user_access: dict[str, list[AccessEntry]] = {}
        self.whowas = deque(maxlen=1000)
        self.logs = deque(maxlen=2000)
        self.events = EventBus()
        self.admin = AdminService(self)
        self.statistics = dict(current_connections=0, peak_connections=0, total_connections=0,
                               current_channels=0, peak_channels=0, messages_routed=0,
                               commands_processed=0, bytes_received=0, bytes_sent=0,
                               auth_successes=0, auth_failures=0, kicks=0, kills=0,
                               server_start_time=None)
        self.command_counts: dict[str, int] = {}
        self.listeners: list[asyncio.Server] = []
        self._tasks: set[asyncio.Task] = set()
        self._disconnecting: dict[str, asyncio.Task] = {}
        self._maintenance_task = None
        self._connection_rates: dict[str, TokenBucket] = {}
        self._wire_ids: set[str] = set()
        self._log_listener = None
        self._file_handler = None
        self._logger = logging.getLogger(f"OpenIRC.{id(self)}")
        self._logger.propagate = False
        self._cloak_secret = ""
        self.auth_failures = FailureWindow(5, 300)
        self._auth_slots = asyncio.Semaphore(4)

    async def initialize(self) -> None:
        if self._initialized:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_lock.acquire()
        try:
            await self.db.open()
            stored = await self.db.get_settings()
            self._cloak_secret = stored.get("_cloak_secret") or secrets.token_hex(32)
            if "_cloak_secret" not in stored:
                await self.db.save_settings({**stored, "_cloak_secret": self._cloak_secret})
            self.settings = ServerSettings.from_dict({**stored, **self._overrides})
            self.saved_settings = self.settings
            for record in await self.db.list("accounts"):
                account = Account(**record)
                self.accounts[account.id] = account
            for record in await self.db.list("operators"):
                record["permissions"] = set(record.get("permissions", ()))
                oper = Operator(**record)
                self.operators[oper.account_id] = oper
            for record in await self.db.list("registered_channels"):
                channel = Channel.from_record(record)
                self.channels[casefold(channel.name)] = channel
                self._wire_ids.add(channel.oid)
            for record in await self.db.list("server_bans"):
                ban = Ban(**record)
                self.bans[ban.id] = ban
            for record in await self.db.list("nick_reservations"):
                self.reservations[casefold(record["nickname"])] = record
            self.server_access = [AccessEntry(**entry) for record in await self.db.list("server_access") for entry in record.get("entries", [])]
            self.network_access = [AccessEntry(**entry) for record in await self.db.list("network_access") for entry in record.get("entries", [])]
            for record in await self.db.list("user_access"):
                self.user_access[record["id"]] = [AccessEntry(**entry) for entry in record.get("entries", [])]
            self.auth_failures = FailureWindow(self.settings.get("auth_failures", 5), self.settings.get("lockout_seconds", 300))
            self._configure_logging()
            self._initialized = True
            self.log("Server", "OpenIRC runtime initialized")
        except BaseException:
            await self.db.close()
            self.runtime_lock.release()
            raise

    def _configure_logging(self) -> None:
        log_path = Path(self.settings.get("log_directory") or self.data_dir / "logs")
        log_path.mkdir(parents=True, exist_ok=True)
        self._file_handler = logging.handlers.RotatingFileHandler(
            log_path / "openirc.log", maxBytes=self.settings.get("log_max_bytes", 5_000_000),
            backupCount=self.settings.get("log_retention", 5), encoding="utf-8")
        self._file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        work_queue: queue.Queue = queue.Queue(10000)
        self._logger.handlers = [logging.handlers.QueueHandler(work_queue)]
        self._logger.setLevel(self.settings.get("log_level", "INFO"))
        self._log_listener = logging.handlers.QueueListener(work_queue, self._file_handler)
        self._log_listener.start()
        self._logging_settings = (self.settings.get("log_directory"), self.settings.get("log_max_bytes"), self.settings.get("log_retention"))

    def apply_runtime_settings(self) -> None:
        self._logger.setLevel(self.settings.get("log_level", "INFO"))
        self.auth_failures.limit = self.settings.get("auth_failures", 5)
        self.auth_failures.seconds = self.settings.get("lockout_seconds", 300)
        for session in self.sessions.values():
            session.commands.rate = self.settings.get("commands_per_second", 30)
            session.commands.capacity = max(1, session.commands.rate * 2)
            session.messages.rate = self.settings.get("messages_per_second", 10)
            session.messages.capacity = max(1, session.messages.rate * 2)
            session.nick_changes.rate = self.settings.get("nick_changes_per_minute", 10) / 60

    def new_oid(self) -> str:
        value = wire_oid()
        while value in self._wire_ids:
            value = wire_oid()
        self._wire_ids.add(value)
        return value

    def emit(self, kind: str, **data) -> None:
        self.events.emit(kind, **data)

    def log(self, category: str, message: str, level: str = "INFO") -> None:
        clean = str(message).replace("\r", " ").replace("\n", " ")[:2000]
        self.logs.append({"time": time.time(), "level": level, "category": category, "message": clean})
        self._logger.log(getattr(logging, level, logging.INFO), "[%s] %s", category, clean)
        self.emit("Log", category=category, level=level, message=clean)

    async def start(self) -> None:
        await self.initialize()
        async with self._lifecycle_lock:
            if self.status == "Running":
                return
            old_logging = self._logging_settings
            self.settings = self.saved_settings
            new_logging = (self.settings.get("log_directory"), self.settings.get("log_max_bytes"), self.settings.get("log_retention"))
            if old_logging != new_logging:
                await asyncio.to_thread(self._log_listener.stop)
                self._file_handler.close()
                self._configure_logging()
            self.apply_runtime_settings()
            self.status = "Starting"
            created = []
            try:
                context = None
                if self.settings.get("tls_enabled"):
                    context = create_server_context(self.settings.get("tls_cert"), self.settings.get("tls_key"))
                address = self.settings.get("bind_address", "127.0.0.1")
                family = socket.AF_INET6 if ":" in address else socket.AF_INET
                if family == socket.AF_INET6 and not socket.has_ipv6:
                    raise ValueError("This host does not support IPv6")
                for enabled, port, secure in (
                    (self.settings.get("irc_enabled", True), self.settings.get("irc_port", 6667), None),
                    (self.settings.get("tls_enabled"), self.settings.get("tls_port", 6697), context),
                ):
                    if enabled:
                        listener = await asyncio.start_server(self._accept, address, port, family=family, ssl=secure, start_serving=False, limit=1024)
                        created.append(listener)
                if not created:
                    raise ValueError("Enable at least one IRC listener")
                for listener in created:
                    await listener.start_serving()
                self.listeners = created
                self.status = "Running"
                self.pending_restart.clear()
                self.statistics["server_start_time"] = time.time()
                self._maintenance_task = asyncio.create_task(self._maintenance(), name="openirc-maintenance")
                self.log("Server", "Server started")
                self.emit("ServerStarted")
            except BaseException as error:
                for listener in created:
                    listener.close()
                    await listener.wait_closed()
                self.listeners.clear()
                self.status = "Error"
                self.log("Errors", f"Unable to start server: {error}", "ERROR")
                raise

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if self.status == "Stopped":
                return
            self.status = "Stopping"
            listeners = self.listeners
            for listener in listeners:
                listener.close()
            self.listeners.clear()
            if self._maintenance_task:
                self._maintenance_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._maintenance_task
                self._maintenance_task = None
            await asyncio.gather(*(self.disconnect(session, "Server shutting down") for session in list(self.sessions.values())))
            if self._disconnecting:
                await asyncio.gather(*tuple(self._disconnecting.values()))
            # Python 3.13 waits for accepted transports as well as listen sockets.
            # Close sessions before waiting, otherwise active clients deadlock Stop.
            await asyncio.gather(*(listener.wait_closed() for listener in listeners))
            current = asyncio.current_task()
            tasks = [task for task in self._tasks if task is not current and not task.done()]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.status = "Stopped"
            self.statistics["server_start_time"] = None
            self.log("Server", "Server stopped")
            self.emit("ServerStopped")

    async def close(self) -> None:
        if self._closed:
            return
        await self.stop()
        await self.db.close()
        self.runtime_lock.release()
        if self._log_listener:
            await asyncio.to_thread(self._log_listener.stop)
            self._file_handler.close()
            self._logger.handlers.clear()
        self._closed = True

    @property
    def bound_addresses(self) -> list[tuple]:
        return [sock.getsockname() for listener in self.listeners for sock in (listener.sockets or ())]

    async def _accept(self, reader, writer) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else "unknown"
        session = None
        try:
            if self.status != "Running":
                return
            if len(self.sessions) >= self.settings.get("max_users", 1000):
                return
            if sum(client.ip == ip for client in self.sessions.values()) >= self.settings.get("max_connections_per_ip", 10):
                self.log("Security", f"Connection limit reached for {ip}", "WARNING")
                return
            if len(self._connection_rates) > 10000:
                self._connection_rates.pop(next(iter(self._connection_rates)))
            rate = self._connection_rates.setdefault(ip, TokenBucket(self.settings.get("connections_per_minute", 30) / 60, 10))
            if not rate.allow():
                self.log("Security", f"Connection rate limited for {ip}", "WARNING")
                return
            secure = writer.get_extra_info("ssl_object") is not None
            session = Session(self, reader=reader, writer=writer, transport="TLS" if secure else "TCP", ip=ip, host=self.display_host(ip))
            self.check_bans(session)
            self._add_session(session)
            session.task = task
            session.writer_task = asyncio.create_task(session.write_loop())
            parser = LineBuffer()
            partial_since = None
            while not session.closing:
                deadline = self.settings.get("registration_timeout", 60) if not session.registered else self.settings.get("ping_interval", 60) + self.settings.get("ping_timeout", 120)
                if partial_since is not None:
                    deadline = min(deadline, max(0.01, 30 - (time.monotonic() - partial_since)))
                payload = await asyncio.wait_for(reader.read(1024), deadline)
                if not payload:
                    break
                session.bytes_in += len(payload)
                self.statistics["bytes_received"] += len(payload)
                session.last_received = time.time()
                messages = parser.feed(payload)
                if parser.buffered_bytes:
                    partial_since = time.monotonic() if partial_since is None or messages else partial_since
                else:
                    partial_since = None
                if partial_since is not None and time.monotonic() - partial_since >= 30:
                    raise TimeoutError("Incomplete message timed out")
                for message in messages:
                    await self.process_message(session, message)
                    if session.closing:
                        break
        except (ValueError, UnicodeError) as error:
            self.log("Security", f"Malformed input from {ip}: {type(error).__name__}", "WARNING")
        except IRCError:
            self.log("Connections", f"Connection rejected for {ip}")
        except (ConnectionError, OSError, TimeoutError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("Unhandled connection error")
            self.log("Errors", "Unexpected connection error; see server log", "ERROR")
        finally:
            try:
                if session:
                    await self.disconnect(session, "Connection closed")
                else:
                    writer.close()
                    with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                        await asyncio.wait_for(writer.wait_closed(), 2)
            finally:
                self._tasks.discard(task)

    def _add_session(self, session: Session) -> None:
        self.sessions[session.id] = session
        self.statistics["total_connections"] += 1
        self.statistics["current_connections"] = len(self.sessions)
        self.statistics["peak_connections"] = max(self.statistics["peak_connections"], len(self.sessions))
        self.log("Connections", f"Connection accepted ({session.transport})")
        self.emit("ClientConnected", id=session.id)

    async def process_message(self, session, message) -> None:
        from OpenIRC.protocol.dispatcher import COMMANDS, dispatch
        from OpenIRC.protocol import irc_commands, ircx_commands  # populate dispatcher registry
        if session.closing:
            return
        if time.monotonic() < session.throttled_until or not session.commands.allow():
            session.throttled_until = time.monotonic() + self.settings.get("flood_penalty_seconds", 2)
            self.log("Security", f"Command throttle for {session.nick or session.id}", "WARNING")
            return
        self.statistics["commands_processed"] += 1
        session.messages_received += 1
        counter = message.command if message.command in COMMANDS else "UNKNOWN"
        self.command_counts[counter] = self.command_counts.get(counter, 0) + 1
        if message.command not in {"PING", "PONG"}:
            session.last_activity = time.time()
        # Log command names only, never arguments or message bodies.
        if self.settings.get("log_level") == "DEBUG":
            self.log("IRCX Commands" if session.ircx else "IRC Commands", f"{session.id} {message.command}", "DEBUG")
        try:
            await dispatch(self, session, message)
        except IRCError as error:
            if message.command != "NOTICE":
                session.numeric(error.code, *error.params, text=error.text)
        except (ValueError, KeyError) as error:
            if message.command != "NOTICE":
                session.numeric(N.ERR_NEEDMOREPARAMS, message.command, text="Invalid command parameters")
        except Exception:
            self._logger.exception("Command processing error (%s)", message.command)
            self.log("Errors", f"Internal error handling {message.command}", "ERROR")
            if message.command != "NOTICE":
                session.send("NOTICE", session.nick or "*", trailing="The operation failed; contact the server administrator")

    async def _maintenance(self) -> None:
        while True:
            await asyncio.sleep(1)
            now = time.time()
            for session in list(self.sessions.values()):
                if session.transport == "Local":
                    continue
                if not session.registered and now - session.connected_at > self.settings.get("registration_timeout", 60):
                    await self.disconnect(session, "Registration timed out")
                elif session.pending_ping and now - session.pending_ping > self.settings.get("ping_timeout", 120):
                    await self.disconnect(session, "Ping timeout")
                elif session.registered and not session.pending_ping and now - session.last_received > self.settings.get("ping_interval", 60):
                    session.pending_ping = now
                    session.send("PING", trailing=self.settings.get("server_name"))
            self.statistics["current_channels"] = sum(bool(channel.members) for channel in self.channels.values())
            self.statistics["peak_channels"] = max(self.statistics["peak_channels"], self.statistics["current_channels"])
            self.emit("StatisticsUpdated")

    def schedule_disconnect(self, session, reason) -> None:
        if not session.closing:
            task = asyncio.create_task(self.disconnect(session, reason))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def has_permission(self, actor, permission) -> bool:
        return has_permission(actor, permission)

    def display_host(self, ip: str) -> str:
        policy = self.settings.get("host_privacy", "cloak")
        if policy in {"full", "Full host/IP"}:
            return ip
        if policy in {"hide", "hidden"}:
            return "hidden.openirc"
        digest = hmac.new(self._cloak_secret.encode(), ip.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{digest}.cloak"

    def find_user(self, nick: str):
        name = incoming_name(nick, historical=self.settings.get("unicode_mode") == "historical")
        return next((session for session in self.sessions.values() if casefold(session.nick) == casefold(name)), None)

    def find_channel(self, name: str):
        name = incoming_name(name, historical=self.settings.get("unicode_mode") == "historical")
        return self.channels.get(casefold(name))

    def render_nick(self, viewer, target) -> str:
        return wire_name(target.nick, ircx=viewer.ircx, historical=self.settings.get("unicode_mode") == "historical")

    def render_parameter(self, viewer, value: str) -> str:
        if self.settings.get("unicode_mode") != "historical":
            return value
        if value.startswith(("#", "&")):
            return wire_name(value, ircx=viewer.ircx, historical=True, channel=True)
        session = self.find_user(value)
        return self.render_nick(viewer, session) if session else value

    def render_prefix(self, viewer, value: str) -> str:
        if "!" in value:
            nick, rest = value.split("!", 1)
            return wire_name(nick, ircx=viewer.ircx, historical=self.settings.get("unicode_mode") == "historical") + "!" + rest
        return self.render_parameter(viewer, value)

    def account_name(self, session) -> str:
        account = self.accounts.get(session.account_id)
        return account.username if account else ""

    def check_bans(self, session) -> None:
        for ban in self.bans.values():
            if ban.active and matches_ban(ban, session, self.account_name(session)):
                raise IRCError(N.ERR_YOUREBANNEDCREEP, ban.reason or "Banned from this server")

    async def persist_channel(self, channel) -> None:
        if channel.registered:
            await self.db.put("registered_channels", channel.id, channel.to_record())

    async def change_property(self, actor, channel_name, name, value):
        from OpenIRC.protocol.properties import change_property
        return await change_property(self, actor, channel_name, name, value)

    async def change_access(self, actor, object_name, operation, level="", mask="", timeout=0, reason=""):
        from OpenIRC.protocol.access import change_access
        return await change_access(self, actor, object_name, operation, level, mask, timeout, reason)

    async def chat_connect(self, username, password, nickname) -> str:
        if self.status != "Running":
            raise ValueError("Start the server before connecting chat")
        session = Session(self, transport="Local")
        self._add_session(session)
        try:
            await self.authenticate(session, username, password, oper=True)
            session.ircx = True
            session.username, session.realname = username, "OpenIRC administrator chat"
            await self.set_nick(session, nickname)
            await self.maybe_register(session)
            return session.id
        except BaseException:
            await self.disconnect(session, "Chat login failed")
            raise

    async def chat_send(self, session_id: str, line: str) -> None:
        session = self.sessions.get(session_id)
        if not session or session.transport != "Local" or not session.operator_role:
            raise ValueError("Chat is disconnected; sign in again")
        await self.process_message(session, parse_line(line))

    async def chat_poll(self, session_id: str) -> list[str]:
        session = self.sessions.get(session_id)
        if not session or session.transport != "Local":
            raise ValueError("Chat disconnected")
        return session.poll()

    async def chat_disconnect(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session and session.transport == "Local":
            await self.disconnect(session, "Chat disconnected")
