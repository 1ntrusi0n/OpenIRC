"""Explicit-principal command boundary for every administration adapter."""
from __future__ import annotations

from dataclasses import replace
from collections import deque
import ipaddress
import inspect
import re
import sqlite3
import time

from OpenIRC.config.settings import RESTART_REQUIRED
from OpenIRC.models.domain import Account, AdminCommand, Ban, Operator, ROLE_PERMISSIONS, new_id
from OpenIRC.protocol.unicode import casefold
from OpenIRC.protocol.errors import IRCError
from OpenIRC.security.masks import validate_mask, matches_ban
from OpenIRC.security.passwords import hash_password
from .accounts import AccountAdministration
from .channels import ChannelAdministration
from .commands import AdminError
from .snapshots import SnapshotAdministration


ACTION_PERMISSIONS = {
    "start": "start_stop", "stop": "start_stop", "restart": "start_stop", "settings.save": "change_settings", "setup": "change_settings",
    "configuration.export": "change_settings", "account.save": "manage_accounts", "account.delete": "manage_accounts", "account.password": "manage_accounts",
    "operator.save": "manage_operators", "operator.delete": "manage_operators", "reservation.save": "manage_accounts", "reservation.delete": "manage_accounts",
    "channel.save": "manage_channels", "channel.register": "manage_channels", "channel.unregister": "manage_channels", "channel.close": "manage_channels",
    "channel.delete": "manage_channels", "channel.topic": "manage_channels", "channel.role": "set_modes", "channel.kick": "kick", "access.change": "manage_access",
    "connection.kill": "kill", "connection.join": "force_join", "connection.part": "force_part", "connection.nick": "change_nick", "connection.notice": "broadcast",
    "ban.save": "manage_bans", "ban.delete": "manage_bans", "ban.disconnect": "manage_bans", "broadcast": "broadcast",
    "chat.connect": "view_server", "chat.send": "view_server", "chat.poll": "view_server", "chat.disconnect": "view_server",
}


class AdminService(AccountAdministration, ChannelAdministration, SnapshotAdministration):
    def __init__(self, server):
        self.server = server

    def _require(self, principal, permission):
        if principal is None or not self.server.has_permission(principal, permission):
            raise AdminError("permission_denied", f"This action requires the {permission.replace('_', ' ')} permission")

    async def execute(self, principal, command: AdminCommand):
        if not isinstance(command, AdminCommand):
            raise AdminError("validation", "An AdminCommand is required")
        permission = ACTION_PERMISSIONS.get(command.action)
        if permission is None:
            raise AdminError("unknown_action", "Unknown administration action")
        self._require(principal, permission)
        if command.action.startswith("chat.") and not getattr(principal, "local", False):
            raise AdminError("permission_denied", "The built-in chat transport is available only to the local desktop")
        target = str(command.payload.get("id", command.payload.get("account_id", command.payload.get("object", command.payload.get("nickname", "server")))))
        audit_values = (principal.name, command.action, command.action.split(".")[0], target, command.action.replace(".", " "))
        audited = not command.action.startswith("chat.") and command.action != "configuration.export"
        try:
            handler = getattr(self, "_" + command.action.replace(".", "_"))
            if audited:
                with self.server.db.audit_scope(*audit_values) as scope:
                    result = await handler(principal, command.payload)
                    recorded = scope["recorded"]
            else:
                result = await handler(principal, command.payload)
                recorded = False
        except AdminError:
            raise
        except (KeyError, TypeError) as exc:
            raise AdminError("validation", "The action is missing a required value or has an invalid value") from exc
        except (ValueError, PermissionError) as exc:
            raise AdminError("validation", str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise AdminError("conflict", "That record conflicts with an existing account, channel, reservation or ownership reference") from exc
        except sqlite3.Error as exc:
            raise AdminError("storage_error", "The database could not save this change; check available disk space and data-directory permissions") from exc
        except IRCError as exc:
            raise AdminError(f"protocol_{int(exc.code)}", exc.text) from exc
        if audited:
            if not recorded:
                try:
                    await self.server.db.audit(*audit_values)
                except sqlite3.Error:
                    # Listener and live-session actions cannot be rolled back after completion.
                    self.server.log("Errors", f"Completed {command.action}; unable to save its database audit record", "ERROR")
            self.server.emit("admin.changed", action=command.action, object_id=target)
        return result

    async def _start(self, principal, payload):
        await self.server.start()

    async def _stop(self, principal, payload):
        await self.server.stop()

    async def _restart(self, principal, payload):
        await self.server.stop()
        await self.server.start()

    async def _settings_save(self, principal, payload):
        async with self.server.lock:
            saved = getattr(self.server, "saved_settings", self.server.settings)
            settings = saved.updated(payload["values"])
            await self.server.db.save_settings(settings.to_dict(include_secrets=True))
            self.server.saved_settings = settings
            running = self.server.status.lower() == "running"
            pending = {key for key in RESTART_REQUIRED if self.server.settings[key] != settings[key]} if running else set()
            self.server.pending_restart = pending
            effective = settings.updated({key: self.server.settings[key] for key in RESTART_REQUIRED}) if running else settings
            self.server.settings = effective
            if hasattr(self.server, "_logger"):
                self.server._logger.setLevel(effective.log_level)
            if hasattr(self.server, "auth_failures"):
                failures = self.server.auth_failures
                failures.limit, failures.seconds = effective.auth_failures, effective.lockout_seconds
                failures.failures = {key: deque(values, maxlen=effective.auth_failures) for key, values in failures.failures.items()}
            for session in self.server.sessions.values():
                self._update_bucket(session.commands, effective.commands_per_second, effective.commands_per_second * 2)
                self._update_bucket(session.messages, effective.messages_per_second, effective.messages_per_second * 2)
                self._update_bucket(session.nick_changes, effective.nick_changes_per_minute / 60, 3)
            for bucket in getattr(self.server, "_connection_rates", {}).values():
                self._update_bucket(bucket, effective.connections_per_minute / 60, 10)
        return {"restart_required": sorted(pending)}

    @staticmethod
    def _update_bucket(bucket, rate, capacity):
        bucket.rate, bucket.capacity = max(0.01, rate), max(1.0, capacity)
        bucket.tokens = min(bucket.tokens, bucket.capacity)

    async def _setup(self, principal, payload):
        if not getattr(principal, "local", False):
            raise AdminError("permission_denied", "First-run setup is local only")
        values = {**payload.get("values", {}), "setup_complete": True}
        getattr(self.server, "saved_settings", self.server.settings).updated(values).validate()
        username = payload["username"]
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
            raise AdminError("validation", "Username must contain 1–64 letters, digits, periods, underscores or hyphens")
        password_hash = await hash_password(payload["password"])
        async with self.server.lock:
            if self.server.settings.setup_complete:
                raise AdminError("conflict", "First-run setup is already complete")
            if any(casefold(a.username) == casefold(username) for a in self.server.accounts.values()):
                raise AdminError("conflict", "That account username is already in use")
            settings = getattr(self.server, "saved_settings", self.server.settings).updated(values)
            account = Account(new_id(), username, username, password_hash)
            operator = Operator(account.id, "administrator", set(ROLE_PERMISSIONS["administrator"]))
            await self.server.db.transaction(puts=(("accounts", account.id, account.to_record()), ("operators", account.id, operator.to_record())), settings=settings.to_dict(include_secrets=True))
            self.server.accounts[account.id] = account
            self.server.operators[account.id] = operator
            self.server.settings = settings
            self.server.saved_settings = settings
            self.server.pending_restart = set()
        return {"id": account.id}

    async def _configuration_export(self, principal, payload):
        channels = []
        for channel in self.server.channels.values():
            if channel.registered and self.server.has_permission(principal, "manage_channels"):
                record = channel.to_record()
                record.pop("secrets", None)
                channels.append(record)
        return {"format": "OpenIRC configuration", "version": 1, "settings": getattr(self.server, "saved_settings", self.server.settings).to_dict(), "registered_channels": channels,
                "accounts": [{key: value for key, value in account.to_record().items() if key != "password_hash"} for account in self.server.accounts.values()] if self.server.has_permission(principal, "manage_accounts") else [],
                "operators": [operator.to_record() for operator in self.server.operators.values()] if self.server.has_permission(principal, "manage_operators") else [],
                "bans": [ban.to_record() for ban in self.server.bans.values()] if self.server.has_permission(principal, "manage_bans") else [],
                "reservations": list(self.server.reservations.values()) if self.server.has_permission(principal, "manage_accounts") else []}

    async def _access_change(self, principal, payload):
        return await self.server.change_access(principal, payload["object"], payload["operation"], payload.get("level", ""), payload.get("mask", ""), payload.get("timeout", 0), payload.get("reason", ""))

    def _session(self, identifier):
        session = self.server.sessions.get(identifier) or self.server.find_user(identifier)
        if session is None or session.closing:
            raise AdminError("not_found", "The connection no longer exists")
        return session

    async def _connection_kill(self, principal, payload):
        return await self.server.kill(principal, self._session(payload["id"]).nick, payload.get("reason", "Disconnected by administrator"))

    async def _connection_join(self, principal, payload):
        session = self._session(payload["id"])
        return await self.server.join(session, payload["channel"], force=True)

    async def _connection_part(self, principal, payload):
        return await self.server.part(self._session(payload["id"]), payload["channel"], "Removed by administrator")

    async def _connection_nick(self, principal, payload):
        return await self.server.set_nick(self._session(payload["id"]), payload["nickname"], force=True)

    async def _connection_notice(self, principal, payload):
        session = self._session(payload["id"])
        message = self._message(payload["message"])
        session.send("NOTICE", session.nick, prefix=self.server.settings.server_name, trailing=message)

    async def _ban_save(self, principal, payload):
        if payload.get("disconnect"):
            self._require(principal, "kill")
        kind = payload.get("type", "mask").lower()
        if kind not in {"mask", "nickname", "nick", "user", "host", "ip", "cidr", "account"}:
            raise AdminError("validation", "Unknown ban type")
        mask = validate_mask(payload["mask"])
        if kind in {"ip", "cidr"}:
            try:
                network = ipaddress.ip_network(mask, strict=False)
                if kind == "ip" and "/" in mask:
                    raise ValueError("Use CIDR for address ranges")
                mask = str(network.network_address) if kind == "ip" else str(network)
            except ValueError as exc:
                raise AdminError("validation", "Enter a valid IP address or CIDR range") from exc
        reason = self._message(payload.get("reason", "Banned"), maximum=300)
        expires_at = payload.get("expires_at") or None
        if expires_at is not None and (type(expires_at) not in (int, float) or expires_at <= time.time()):
            raise AdminError("validation", "Ban expiry must be in the future")
        async with self.server.lock:
            original = self.server.bans.get(payload.get("id"))
            if payload.get("id") and not original:
                raise AdminError("not_found", "The ban no longer exists")
            ban = Ban(original.id if original else new_id(), kind, mask, reason, principal.name,
                      original.created_at if original else time.time(), expires_at, bool(payload.get("enabled", True)))
            await self.server.db.put("server_bans", ban.id, ban.to_record())
            self.server.bans[ban.id] = ban
        if payload.get("disconnect"):
            await self._ban_disconnect(principal, {"id": ban.id})
        return {"id": ban.id}

    async def _ban_delete(self, principal, payload):
        async with self.server.lock:
            await self.server.db.delete("server_bans", payload["id"])
            self.server.bans.pop(payload["id"], None)

    async def _ban_disconnect(self, principal, payload):
        self._require(principal, "kill")
        ban = self.server.bans.get(payload["id"])
        if ban is None:
            raise AdminError("not_found", "The ban no longer exists")
        for session in list(self.server.sessions.values()):
            if matches_ban(ban, session, self.server.account_name(session)):
                await self.server.disconnect(session, ban.reason or "Banned")

    async def _broadcast(self, principal, payload):
        message, target = self._message(payload["message"]), payload.get("target", "all")
        if target in {"all", "operators"}:
            sessions = list(self.server.sessions.values())
            for session in sessions:
                if session.registered and (target == "all" or session.operator_role):
                    session.send("NOTICE", session.nick, prefix=self.server.settings.server_name, trailing=message)
        else:
            channel = self._channel(target)
            self.server.broadcast_channel(channel, "NOTICE", channel.name, prefix=self.server.settings.server_name, trailing=message)

    @staticmethod
    def _message(value, maximum=300):
        if not isinstance(value, str) or len(value.encode("utf-8")) > maximum or any(c in value for c in "\r\n\x00"):
            raise AdminError("validation", f"Text must contain at most {maximum} UTF-8 bytes without newlines")
        return value

    async def _chat_connect(self, principal, payload):
        result = await self.server.chat_connect(payload["username"], payload["password"], payload["nickname"])
        return {"id": result}

    async def _chat_send(self, principal, payload):
        result = self.server.chat_send(payload["id"], payload["line"])
        return await result if inspect.isawaitable(result) else result

    async def _chat_poll(self, principal, payload):
        result = self.server.chat_poll(payload["id"])
        return await result if inspect.isawaitable(result) else result

    async def _chat_disconnect(self, principal, payload):
        result = self.server.chat_disconnect(payload["id"])
        return await result if inspect.isawaitable(result) else result
