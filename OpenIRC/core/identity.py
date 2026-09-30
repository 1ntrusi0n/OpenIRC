"""Client registration, account authentication and session lifecycle."""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace

from OpenIRC import __version__
from OpenIRC.models.domain import ROLE_PERMISSIONS
from OpenIRC.protocol.errors import IRCError
from OpenIRC.protocol.numerics import IRC as N
from OpenIRC.protocol.unicode import casefold, incoming_name, valid_nickname
from OpenIRC.security.masks import matches_mask
from OpenIRC.security.passwords import verify_password


class IdentityOperations:
    async def set_nick(self, session, nick: str, force: bool = False) -> None:
        nick = incoming_name(nick, historical=self.settings.get("unicode_mode") == "historical")
        if not valid_nickname(nick, self.settings.get("nick_length", 30)):
            raise IRCError(N.ERR_ERRONEUSNICKNAME, "Invalid nickname", (nick,))
        other = self.find_user(nick)
        if other and other.id != session.id:
            raise IRCError(N.ERR_NICKNAMEINUSE, "Nickname is already in use", (nick,))
        if session.nick and nick != session.nick and not force and not session.nick_changes.allow():
            raise IRCError(N.ERR_NICKNAMEINUSE, "Nickname changes are temporarily limited", (nick,))
        reservation = self.reservations.get(casefold(nick))
        policy = self.settings.get("reservation_policy", "off")
        if not force and reservation and reservation.get("protected", True) and reservation.get("account_id") != session.account_id:
            if policy in {"require", "require_account"}:
                raise IRCError(N.ERR_ERRONEUSNICKNAME, "Authenticate as the account reserving this nickname first", (nick,))
            if policy == "warn":
                session.send("NOTICE", session.nick or "*", trailing="This nickname is reserved to another account")
        old_nick, old_prefix = session.nick, session.prefix
        session.nick = nick
        try:
            self.check_bans(session)
        except IRCError:
            session.nick = old_nick
            raise
        if session.registered:
            self._remember_nick(session, old_nick)
            recipients = {session.id}
            for channel in self.channels.values():
                if session.id in channel.members:
                    recipients.update(viewer.id for viewer in self.sessions.values() if viewer.id in channel.members and self.visible_member(viewer, session, channel))
            for identity in recipients:
                viewer = self.sessions.get(identity)
                if viewer:
                    # old prefix must remain renderable after nickname mutation.
                    viewer.send("NICK", trailing=self.render_nick(viewer, session), prefix=old_prefix)
            self.emit("NicknameChanged", id=session.id, old=old_nick, new=nick)
        await self.maybe_register(session)

    async def maybe_register(self, session) -> None:
        if session.registered or session.closing or not session.nick or not session.username or session.cap_negotiating or getattr(session, "ircx_auth_pending", False):
            return
        if not self.settings.get("allow_anonymous", True) and not session.account_id:
            raise IRCError(N.ERR_NEEDREGGEDNICK, "Authenticate with SASL or IRCX AUTH before registration")
        server_password = self.settings.get("server_password_hash")
        if server_password and not await verify_password(server_password, session.password or ""):
            raise IRCError(N.ERR_PASSWDMISMATCH, "Incorrect server password")
        self.check_bans(session)
        self._check_server_access(session)
        session.password = None
        session.registered = True
        from OpenIRC.protocol.irc_commands import send_isupport, send_lusers, send_motd
        session.numeric(N.RPL_WELCOME, text=f"Welcome to {self.settings.get('network_name')} {session.prefix}")
        session.numeric(N.RPL_YOURHOST, text=f"Your host is {self.settings.get('server_name')}, running OpenIRC {__version__}")
        session.numeric(N.RPL_CREATED, text="This server was created " + time.ctime(self.statistics.get("server_start_time") or time.time()))
        session.numeric(N.RPL_MYINFO, self.settings.get("server_name"), __version__, "iow", "abfhiklmnopqrstuvwx")
        send_isupport(self, session)
        send_lusers(self, session)
        send_motd(self, session)
        self.log("Connections", f"User registered: {session.nick}")
        self.emit("ClientRegistered", id=session.id)

    def _check_server_access(self, session) -> None:
        for entries in (self.network_access, self.server_access):
            matches = [entry for entry in entries if entry.active and matches_mask(entry.mask, session.nick, session.username, session.host, session.ip, self.account_name(session), self.settings.server_name)]
            if any(entry.level == "GRANT" for entry in matches):
                continue
            if any(entry.level == "DENY" for entry in matches):
                raise IRCError(N.ERR_YOUREBANNEDCREEP, "Server access denied")

    async def authenticate(self, session, username: str, password: str, oper: bool = False) -> None:
        if self.settings.get("require_tls_auth", True) and not session.is_secure:
            raise IRCError(N.ERR_PASSWDMISMATCH, "TLS is required for account authentication")
        username = username.strip()
        if not username or len(username) > 64 or len(password) > 1024:
            raise IRCError(N.ERR_PASSWDMISMATCH, "Authentication failed")
        keys = (f"ip:{session.ip}", f"account:{username.casefold()}")
        if any(self.auth_failures.blocked(key) for key in keys):
            raise IRCError(N.ERR_PASSWDMISMATCH, "Authentication temporarily locked; try again later")
        account = next((item for item in self.accounts.values() if item.username.casefold() == username.casefold()), None)
        expected_hash = account.password_hash if account else None
        valid = account is not None and account.enabled and not account.locked
        if valid:
            async with self._auth_slots:
                valid = await verify_password(expected_hash, password)
        current = self.accounts.get(account.id) if account else None
        valid = valid and current is not None and current.enabled and not current.locked and current.password_hash == expected_hash
        operator = self.operators.get(account.id) if account else None
        if oper and (not operator or not operator.enabled):
            valid = False
        if not valid:
            for key in keys:
                self.auth_failures.fail(key)
            self.statistics["auth_failures"] += 1
            self.log("Authentication", "Account authentication failed", "WARNING")
            raise IRCError(N.ERR_PASSWDMISMATCH, "Authentication failed")
        async with self.lock:
            # The verifier yields to the executor, and the lock may have been
            # held by a revocation/reset. Never write an earlier account copy
            # back into storage after either asynchronous boundary.
            current = self.accounts.get(account.id)
            operator = self.operators.get(account.id)
            if (session.closing or session.id not in self.sessions or current is None or
                    not current.enabled or current.locked or current.password_hash != expected_hash or
                    (oper and (operator is None or not operator.enabled))):
                raise IRCError(N.ERR_PASSWDMISMATCH, "Authentication failed")
            updated = replace(current, last_login=time.time())
            updates = [("accounts", updated.id, updated.to_record())]
            if oper:
                operator = replace(operator, last_login=time.time())
                updates.append(("operators", operator.account_id, operator.to_record()))
            await self.db.transaction(puts=updates)
            self.accounts[updated.id] = updated
            if oper:
                self.operators[operator.account_id] = operator
        if session.closing or session.id not in self.sessions:
            raise IRCError(N.ERR_NOTREGISTERED, "Session disconnected during authentication")
        old_id = session.account_id
        session.account_id = updated.id
        try:
            self.check_bans(session)
        except IRCError:
            session.account_id = old_id
            raise
        if oper:
            session.operator_role = operator.role
            session.permissions = set(operator.permissions)
            session.modes.add("o")
        for key in keys:
            self.auth_failures.clear(key)
        self.statistics["auth_successes"] += 1
        self.log("Authentication", f"{'Operator' if oper else 'Account'} authenticated: {updated.username}")
        self.emit("OperatorAuthenticated" if oper else "ClientAuthenticated", id=session.id)

    def refresh_privileges(self) -> None:
        for session in list(self.sessions.values()):
            account = self.accounts.get(session.account_id)
            if session.account_id and (not account or not account.enabled or account.locked):
                session.account_id = session.operator_role = None
                session.permissions.clear()
                session.modes.discard("o")
                self.schedule_disconnect(session, "Account access revoked")
            elif session.operator_role:
                operator = self.operators.get(session.account_id)
                if not operator or not operator.enabled:
                    session.operator_role = None
                    session.permissions.clear()
                    session.modes.discard("o")
                    if session.transport == "Local":
                        self.schedule_disconnect(session, "Operator access revoked")
                    else:
                        session.send("MODE", session.nick, "-o")
                else:
                    session.operator_role = operator.role
                    session.permissions = set(operator.permissions)

    def _remember_nick(self, session, nick: str | None = None) -> None:
        self.whowas.append({"nick": nick or session.nick, "username": session.username, "host": session.host,
                            "realname": session.realname, "account": self.account_name(session), "time": time.time()})

    async def disconnect(self, session, reason: str = "Client quit") -> None:
        task = self._disconnecting.get(session.id)
        if task is None:
            if session.closing:
                return
            task = asyncio.create_task(self._disconnect_session(session, reason), name="openirc-disconnect")
            self._disconnecting[session.id] = task
            task.add_done_callback(lambda finished: self._disconnecting.pop(session.id, None))
        # A cancelled reader/maintenance task must not interrupt transport cleanup.
        await asyncio.shield(task)

    async def _disconnect_session(self, session, reason: str) -> None:
        if session.closing:
            return
        session.closing = True
        if session.registered:
            self._remember_nick(session)
            recipients = set()
            for channel in self.channels.values():
                if session.id in channel.members:
                    recipients.update(viewer.id for viewer in self.sessions.values() if viewer.id != session.id and viewer.id in channel.members and self.visible_member(viewer, session, channel))
            for identity in recipients:
                viewer = self.sessions.get(identity)
                if viewer:
                    viewer.send("QUIT", prefix=session.prefix, trailing=reason)
        for channel in list(self.channels.values()):
            channel.members.pop(session.id, None)
            channel.invites.discard(session.id)
            self._destroy_if_empty(channel)
        self.sessions.pop(session.id, None)
        self._wire_ids.discard(session.oid)
        self.user_access.pop(session.id, None)
        if session.writer:
            try:
                clean = reason.replace("\r", " ").replace("\n", " ")[:180]
                session.writer.write(f"ERROR :{clean}\r\n".encode())
                await asyncio.wait_for(session.writer.drain(), 1)
            except (ConnectionError, OSError, TimeoutError):
                pass
            session.writer.close()
            with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                await asyncio.wait_for(session.writer.wait_closed(), 2)
        if session.writer_task:
            session.writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.writer_task
        if session.task and session.task is not asyncio.current_task():
            session.task.cancel()
        self.statistics["current_connections"] = len(self.sessions)
        self.log("Connections", f"Disconnected {session.nick or session.id}: {reason}")
        self.emit("ClientDisconnected", id=session.id)

    async def kill(self, actor, target_nick: str, reason: str = "Killed by operator") -> None:
        if not self.has_permission(actor, "kill"):
            raise IRCError(N.ERR_NOPRIVILEGES, "Permission denied")
        target = self.find_user(target_nick)
        if not target:
            raise IRCError(N.ERR_NOSUCHNICK, "No such nickname", (target_nick,))
        self.statistics["kills"] += 1
        self.log("Moderation", f"Killed {target.nick}")
        await self.db.audit(getattr(actor, "name", getattr(actor, "nick", "operator")), "kill", "session", target.id, reason)
        await self.disconnect(target, reason)
