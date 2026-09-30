"""IRCX access entries shared by wire and local administration."""
from copy import deepcopy
import time

from OpenIRC.core.permissions import channel_role
from OpenIRC.models.domain import AccessEntry, Role, new_id
from .errors import IRCError
from .numerics import IRCX
from .unicode import casefold

LEVELS = frozenset({"DENY", "GRANT", "HOST", "OWNER", "VOICE"})


def access_target(server, actor, object_name: str):
    channel = server.find_channel(object_name)
    if channel:
        if not server.has_permission(actor, "manage_access") and channel_role(actor, channel) < Role.HOST:
            raise IRCError(IRCX.IRCERR_NOACCESS, "No permission to edit channel access", ("ACCESS",))
        return "channel", channel.id, channel.access, channel
    if object_name in ("*", "$", server.settings.get("server_name"), server.settings.get("network_name")):
        if not server.has_permission(actor, "manage_bans"):
            raise IRCError(IRCX.IRCERR_NOACCESS, "No permission to edit server access", ("ACCESS",))
        if object_name in ("*", server.settings.get("network_name")):
            return "network_access", "network", server.network_access, None
        return "server_access", "server", server.server_access, None
    user = server.find_user(object_name)
    if user:
        if actor.id != user.id and not server.has_permission(actor, "manage_access"):
            raise IRCError(IRCX.IRCERR_NOACCESS, "No permission to edit user access", ("ACCESS",))
        object_id = user.account_id or user.id
        return "user_access", object_id, server.user_access.get(object_id, []), None
    raise IRCError(IRCX.IRCERR_NOSUCHOBJECT, "No such access object", (object_name,))


async def change_access(server, actor, object_name: str, operation: str, level: str = "", mask: str = "", timeout: int = 0, reason: str = ""):
    operation, level = operation.upper(), level.upper()
    if operation not in {"LIST", "ADD", "DELETE", "CLEAR"}:
        raise IRCError(IRCX.IRCERR_BADFUNCTION, "Unknown access operation", ("ACCESS",))
    if level and level not in LEVELS:
        raise IRCError(IRCX.IRCERR_BADLEVEL, "Unknown access level", ("ACCESS",))
    if operation in {"ADD", "DELETE"} and not level:
        raise IRCError(IRCX.IRCERR_BADLEVEL, "An access level is required", ("ACCESS",))
    if len(mask.encode("utf-8")) > 160 or any(c.isspace() or ord(c) < 32 or c == "\x7f" for c in mask) or mask.count("*") + mask.count("?") > 32:
        raise IRCError(IRCX.IRCERR_BADVALUE, "Invalid or overly complex mask", (object_name,))
    if len(reason.encode("utf-8")) > 200 or any(c in reason for c in "\r\n\x00"):
        raise IRCError(IRCX.IRCERR_BADVALUE, "Invalid access reason", (object_name,))
    if not isinstance(timeout, int) or not 0 <= timeout <= 5256000:
        raise IRCError(IRCX.IRCERR_BADVALUE, "Timeout must be 0 to 5256000 minutes", (object_name,))
    async with server.lock:
        table, object_id, current, channel = access_target(server, actor, object_name)
        if table != "channel" and level in {"HOST", "OWNER", "VOICE"}:
            raise IRCError(IRCX.IRCERR_BADLEVEL, "This object supports DENY and GRANT only", ("ACCESS",))
        entries = [entry for entry in current if entry.active]
        role = int(channel_role(actor, channel)) if channel else int(Role.OWNER)
        privileged = server.has_permission(actor, "manage_access")
        if channel and not privileged and level == "OWNER" and role < Role.OWNER:
            raise IRCError(IRCX.IRCERR_NOACCESS, "Only an owner may edit OWNER entries", ("ACCESS",))
        if operation == "LIST":
            return entries
        changed = []
        if operation == "ADD":
            mask = mask or "*!*@*"
            if any(e.level == level and casefold(e.mask) == casefold(mask) for e in entries):
                raise IRCError(IRCX.IRCERR_DUPACCESS, "Access entry already exists")
            if len(entries) >= server.settings.get("max_access_entries", 128):
                raise IRCError(IRCX.IRCERR_TOOMANYACCESSES, "Access list is full")
            entry = AccessEntry(new_id(), object_id, level, mask, getattr(actor, "nick", getattr(actor, "name", "server")),
                                expires_at=time.time() + timeout * 60 if timeout else None, reason=reason, creator_role=3 if privileged else role)
            entries.append(entry)
            changed = [entry]
        else:
            for entry in entries:
                selected = (not level or entry.level == level) and (operation == "CLEAR" or casefold(entry.mask) == casefold(mask))
                if selected:
                    if channel and not privileged and (entry.creator_role > role or (entry.level == "OWNER" and role < Role.OWNER)):
                        raise IRCError(IRCX.IRCERR_NOACCESS, "Access entry is protected by a higher privilege", ("ACCESS",))
                    changed.append(entry)
            if operation == "DELETE" and not changed:
                raise IRCError(IRCX.IRCERR_MISACCESS, "Access entry does not exist")
            entries = [entry for entry in entries if entry not in changed]
        audit_values = (getattr(actor, "nick", getattr(actor, "name", "server")), "access." + operation.lower(), table, object_id, f"{operation} {level or 'all levels'} access")
        with server.db.audit_scope(*audit_values):
            if channel:
                replacement = deepcopy(channel)
                replacement.access = entries
                await server.persist_channel(replacement)
                if not channel.registered:
                    await server.db.audit(*audit_values)
                channel.access = entries
            elif table == "user_access":
                # Anonymous user rules last for the session; authenticated rules persist.
                if object_id in server.accounts:
                    await server.db.put(table, object_id, {"id": object_id, "entries": [e.to_record() for e in entries]})
                else:
                    await server.db.audit(*audit_values)
                server.user_access[object_id] = entries
            else:
                await server.db.put(table, object_id, {"id": object_id, "entries": [e.to_record() for e in entries]})
                setattr(server, "network_access" if table == "network_access" else "server_access", entries)
    server.emit("access.changed", object_id=object_id)
    return changed
