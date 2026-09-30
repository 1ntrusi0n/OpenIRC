"""Immutable, permission-filtered view of current server state."""
from __future__ import annotations

import time
from OpenIRC.models.domain import ServerSnapshot, Role


class SnapshotAdministration:
    async def snapshot(self, principal):
        self._require(principal, "view_server")
        server, now = self.server, time.time()
        can_ips = server.has_permission(principal, "view_ips")
        can_accounts = server.has_permission(principal, "manage_accounts")
        can_operators = server.has_permission(principal, "manage_operators")
        can_logs = server.has_permission(principal, "view_logs")
        connections = []
        for session in list(server.sessions.values()):
            connections.append({"id": session.id, "nick": session.nick, "account": server.account_name(session), "username": session.username,
                                "realname": session.realname, "ip": session.ip if can_ips else "Hidden", "host": session.host,
                                "connected_at": session.connected_at, "idle": max(0, now - session.last_activity),
                                "channels": tuple(c.name for c in server.channels.values() if session.id in c.members),
                                "ircx": session.ircx, "transport": session.transport, "tls": session.transport == "TLS",
                                "operator": session.operator_role or "", "bytes_in": session.bytes_in, "bytes_out": session.bytes_out,
                                "modes": "".join(sorted(session.modes))})
        channels = []
        for channel in list(server.channels.values()):
            record = channel.to_record()
            record.pop("secrets", None)
            record["secrets_set"] = tuple(sorted(channel.secrets))
            record["modes"] = "".join(sorted(channel.modes))
            record["users"] = len(channel.members)
            for plural, role in (("owners", Role.OWNER), ("hosts", Role.HOST), ("voiced", Role.VOICE)):
                record[plural] = sum(member.role == role for member in channel.members.values())
            members = []
            for member_id, member in channel.members.items():
                session = server.sessions.get(member_id)
                if session:
                    members.append({"session_id": member_id, "nick": session.nick, "account": server.account_name(session),
                                    "role": member.role.name.title(), "joined_at": member.joined_at,
                                    "idle": max(0, now - session.last_activity), "host": session.ip if can_ips else session.host})
            record["members"] = members
            channels.append(record)
        accounts = []
        if can_accounts:
            for account in server.accounts.values():
                record = account.to_record()
                record.pop("password_hash", None)
                record["role"] = server.operators[account.id].role if account.id in server.operators else "User"
                accounts.append(record)
        operators = []
        if can_operators:
            for operator in server.operators.values():
                operators.append({**operator.to_record(), "account": server.accounts[operator.account_id].username if operator.account_id in server.accounts else "Deleted account"})
        reservations = []
        if can_accounts:
            for record in server.reservations.values():
                account = server.accounts.get(record["account_id"])
                reservations.append({**record, "account": account.username if account else "Deleted account"})
        statistics = dict(server.statistics)
        statistics.update({"connected_users": len(server.sessions), "active_channels": sum(bool(c.members) for c in server.channels.values()),
                           "registered_channels": sum(c.registered for c in server.channels.values()), "registered_accounts": len(server.accounts)})
        statistics["runtime_settings"] = server.settings.to_dict()
        statistics["pending_restart"] = tuple(sorted(getattr(server, "pending_restart", ())))
        statistics["uptime"] = max(0, now - statistics["server_start_time"]) if statistics.get("server_start_time") else 0
        statistics["bound_addresses"] = tuple(getattr(server, "bound_addresses", ()))
        settings = getattr(server, "saved_settings", server.settings).to_dict(include_secrets=bool(getattr(principal, "local", False)))
        return ServerSnapshot(server.status, settings, statistics, tuple(connections), tuple(channels), tuple(accounts), tuple(operators),
                              tuple({**b.to_record(), "active": b.active} for b in server.bans.values()) if server.has_permission(principal, "manage_bans") else (),
                              tuple(reservations), tuple(server.logs) if can_logs else (), tuple(await server.db.audit_entries()) if can_logs else ())
