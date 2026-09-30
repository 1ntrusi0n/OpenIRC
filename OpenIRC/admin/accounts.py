"""Account and operator mutations, committed before in-memory publication."""
from __future__ import annotations

from dataclasses import replace
import inspect
import re

from OpenIRC.models.domain import Account, Operator, ROLE_PERMISSIONS, ALL_PERMISSIONS, new_id
from OpenIRC.protocol.unicode import casefold, valid_nickname
from OpenIRC.security.passwords import hash_password
from .commands import AdminError


class AccountAdministration:
    async def _refresh_privileges(self):
        result = self.server.refresh_privileges()
        if inspect.isawaitable(result):
            await result

    async def _account_save(self, principal, payload):
        server = self.server
        account_id = payload.get("id")
        password = payload.get("password")
        if account_id and account_id not in server.accounts:
            raise AdminError("not_found", "The account no longer exists")
        if not account_id and not password:
            raise AdminError("validation", "A password is required for new accounts")
        password_hash = await hash_password(password) if password else None
        async with server.lock:
            original = server.accounts.get(account_id)
            if account_id and original is None:
                raise AdminError("not_found", "The account no longer exists")
            username = payload.get("username", original.username if original else "")
            if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
                raise AdminError("validation", "Username must contain 1–64 letters, digits, periods, underscores or hyphens")
            if any(casefold(a.username) == casefold(username) and a.id != account_id for a in server.accounts.values()):
                raise AdminError("conflict", "That account username is already in use")
            values = {}
            for name, maximum in (("display_name", 128), ("email", 256), ("notes", 4000)):
                value = payload.get(name, getattr(original, name) if original else "")
                if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
                    raise AdminError("validation", f"Invalid {name}; maximum length is {maximum}")
                values[name] = value
            for name in ("enabled", "locked"):
                value = payload.get(name, getattr(original, name) if original else name == "enabled")
                if type(value) is not bool:
                    raise AdminError("validation", f"{name} must be a boolean")
                values[name] = value
            account = replace(original, username=username, **values) if original else Account(new_id(), username, values.pop("display_name"), password_hash, **values)
            if password_hash:
                account.password_hash = password_hash
            await server.db.put("accounts", account.id, account.to_record())
            server.accounts[account.id] = account
        await self._refresh_privileges()
        return {"id": account.id}

    async def _account_delete(self, principal, payload):
        server, account_id = self.server, payload["id"]
        async with server.lock:
            self._account(account_id)
            if any(c.registered and c.founder_id == account_id for c in server.channels.values()):
                raise AdminError("conflict", "Transfer or unregister this account's registered channels before deleting it")
            await server.db.transaction(deletes=(("accounts", account_id), ("user_access", account_id)))
            server.accounts.pop(account_id, None)
            server.operators.pop(account_id, None)
            server.reservations = {key: value for key, value in server.reservations.items() if value.get("account_id") != account_id}
            getattr(server, "user_access", {}).pop(account_id, None)
        await self._refresh_privileges()

    async def _account_password(self, principal, payload):
        if not payload["password"]:
            raise AdminError("validation", "The new password cannot be empty")
        return await self._account_save(principal, {"id": payload["id"], "password": payload["password"]})

    async def _operator_save(self, principal, payload):
        server = self.server
        async with server.lock:
            account = self._account(payload["account_id"])
            role = str(payload.get("role", "sysop")).lower().replace(" ", "_")
            if role not in ROLE_PERMISSIONS:
                raise AdminError("validation", "Unknown operator role")
            permissions = set(payload.get("permissions", ROLE_PERMISSIONS[role]))
            if not permissions <= ALL_PERMISSIONS:
                raise AdminError("validation", "Unknown operator permission")
            enabled = payload.get("enabled", True)
            if type(enabled) is not bool:
                raise AdminError("validation", "Enabled must be a boolean")
            original = server.operators.get(account.id)
            operator = Operator(account.id, role, permissions, enabled, original.last_login if original else None)
            await server.db.put("operators", account.id, operator.to_record())
            server.operators[account.id] = operator
        await self._refresh_privileges()

    async def _operator_delete(self, principal, payload):
        async with self.server.lock:
            await self.server.db.delete("operators", payload["account_id"])
            self.server.operators.pop(payload["account_id"], None)
        await self._refresh_privileges()

    async def _reservation_save(self, principal, payload):
        nick = payload["nickname"]
        if not valid_nickname(nick, self.server.settings.nick_length):
            raise AdminError("validation", "Invalid reserved nickname")
        async with self.server.lock:
            self._account(payload["account_id"])
            key = casefold(nick)
            import time
            original = self.server.reservations.get(key, {})
            record = {"nickname": nick, "account_id": payload["account_id"], "protected": bool(payload.get("protected", True)), "created_at": original.get("created_at", time.time())}
            await self.server.db.put("nick_reservations", key, record)
            self.server.reservations[key] = record

    async def _reservation_delete(self, principal, payload):
        async with self.server.lock:
            key = casefold(payload["nickname"])
            await self.server.db.delete("nick_reservations", key)
            self.server.reservations.pop(key, None)

    def _account(self, account_id):
        account = self.server.accounts.get(account_id)
        if account is None:
            raise AdminError("not_found", "The account no longer exists")
        return account
