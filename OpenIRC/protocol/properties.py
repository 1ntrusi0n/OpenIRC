"""Validated property metadata shared by protocol and administration."""
from dataclasses import dataclass
from types import MappingProxyType
import re
import uuid
import math


@dataclass(frozen=True, slots=True)
class PropertyDefinition:
    name: str
    max_length: int = 300
    readable_by: str = "visible"
    writable_by: str = "host"
    secret: bool = False
    type: type = str
    read_only: bool = False

    def validate(self, value: str) -> str:
        if not isinstance(value, str) or len(value.encode("utf-8")) > self.max_length:
            raise ValueError(f"{self.name} exceeds {self.max_length} bytes")
        if any(c in value for c in "\r\n\x00"):
            raise ValueError("Use escaped newlines in properties")
        if self.type is int and value and (not value.isdigit() or not 0 <= int(value) <= 86400):
            raise ValueError(f"{self.name} must be an integer from 0 to 86400")
        if self.name == "LAG" and value:
            try:
                delay = float(value)
            except ValueError as exc:
                raise ValueError("LAG must be a number from 0 to 2 seconds") from exc
            if not math.isfinite(delay) or not 0 <= delay <= 2:
                raise ValueError("LAG must be a number from 0 to 2 seconds")
        if self.name == "LANGUAGE" and value and not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", value):
            raise ValueError("LANGUAGE must be a language tag, such as en-US")
        if self.name == "CLIENTGUID" and value:
            try:
                value = str(uuid.UUID(value))
            except ValueError as exc:
                raise ValueError("CLIENTGUID must be a UUID") from exc
        return value


PROPERTIES = MappingProxyType({p.name: p for p in (
    PropertyDefinition("OID", read_only=True), PropertyDefinition("NAME", read_only=True),
    PropertyDefinition("CREATION", read_only=True, type=int),
    PropertyDefinition("LANGUAGE", 64, writable_by="owner"),
    PropertyDefinition("OWNERKEY", 128, "owner", "owner", secret=True),
    PropertyDefinition("HOSTKEY", 128, "owner", "owner", secret=True),
    PropertyDefinition("MEMBERKEY", 128, "host", "host", secret=True),
    PropertyDefinition("TOPIC", 300), PropertyDefinition("SUBJECT", 300, writable_by="owner"),
    PropertyDefinition("CLIENT", 128, writable_by="owner"),
    PropertyDefinition("ONJOIN", 300, writable_by="owner"), PropertyDefinition("ONPART", 300, writable_by="owner"),
    PropertyDefinition("LAG", 10, writable_by="administrator", type=float),
    PropertyDefinition("ACCOUNT", read_only=True), PropertyDefinition("CLIENTGUID", 64, writable_by="administrator"),
)})
PROPERTY_DEFINITIONS = PROPERTIES


def validate_property(name: str, value: str) -> str:
    definition = PROPERTIES.get(name.upper())
    if definition is None:
        raise ValueError("Unknown property")
    if definition.read_only:
        raise PermissionError(f"{definition.name} is read-only")
    return definition.validate(value)


def can_read_property(server, actor, channel, definition: PropertyDefinition) -> bool:
    from OpenIRC.core.permissions import channel_role
    from OpenIRC.models.domain import Role
    if server.has_permission(actor, "manage_channels"):
        return True
    if definition.secret:
        return channel_role(actor, channel) >= (Role.OWNER if definition.readable_by == "owner" else Role.HOST)
    return server.visible_channel(actor, channel)


def property_value(channel, name: str) -> str:
    name = name.upper()
    if PROPERTIES[name].secret:
        return "*" if name in channel.secrets else ""
    return {
        "OID": channel.oid, "NAME": channel.name, "CREATION": str(int(channel.created_at)),
        "TOPIC": channel.topic, "SUBJECT": channel.subject, "LANGUAGE": channel.language,
        "ACCOUNT": channel.founder_id or "",
    }.get(name, channel.properties.get(name, ""))


def _require_write(server, actor, channel, definition):
    from OpenIRC.core.permissions import channel_role
    from OpenIRC.models.domain import Role
    from .errors import IRCError
    from .numerics import IRCX
    if not server.has_permission(actor, "manage_channels"):
        role = channel_role(actor, channel)
        if definition.writable_by == "administrator" or role < (Role.OWNER if definition.writable_by == "owner" else Role.HOST):
            raise IRCError(IRCX.IRCERR_SECURITY, "Insufficient privilege to change property", (channel.name,))
    elif definition.writable_by == "administrator" and not server.has_permission(actor, "change_settings"):
        raise IRCError(IRCX.IRCERR_SECURITY, "Administrator permission required", (channel.name,))


async def change_property(server, actor, channel_name: str, name: str, value: str):
    from copy import deepcopy
    from OpenIRC.core.permissions import channel_role
    from OpenIRC.models.domain import Role
    from OpenIRC.security.passwords import hash_password
    from .errors import IRCError
    from .numerics import IRCX
    name = name.upper()
    definition = PROPERTIES.get(name)
    if definition is None:
        raise IRCError(IRCX.IRCERR_BADPROPERTY, "Unknown channel property", (channel_name,))
    if definition.read_only:
        raise IRCError(IRCX.IRCERR_SECURITY, "Property is read-only", (channel_name,))
    try:
        value = definition.validate(value)
    except ValueError as exc:
        raise IRCError(IRCX.IRCERR_BADVALUE, str(exc), (channel_name,)) from exc
    async with server.lock:
        channel = server.find_channel(channel_name)
        if channel is None:
            raise IRCError(IRCX.IRCERR_NOSUCHOBJECT, "No such channel", (channel_name,))
        _require_write(server, actor, channel, definition)
        replacement = deepcopy(channel)
        if definition.secret:
            if value:
                replacement.secrets[name] = await hash_password(value)
            else:
                replacement.secrets.pop(name, None)
            if name == "MEMBERKEY":
                if value:
                    replacement.modes.add("k")
                else:
                    replacement.modes.discard("k")
        elif name in ("TOPIC", "SUBJECT", "LANGUAGE"):
            setattr(replacement, name.lower(), value)
        else:
            replacement.properties[name] = value
        if name == "TOPIC":
            import time
            replacement.properties["_TOPIC_TIME"] = str(int(time.time()))
            replacement.properties["_TOPIC_SETTER"] = getattr(actor, "nick", getattr(actor, "name", "server"))
        if server.find_channel(channel_name) is not channel:
            raise IRCError(IRCX.IRCERR_NOSUCHOBJECT, "Channel changed during this operation", (channel_name,))
        _require_write(server, actor, channel, definition)
        audit_values = (getattr(actor, "nick", getattr(actor, "name", "server")), "property.change", "channel", channel.id, f"Changed {name}")
        with server.db.audit_scope(*audit_values):
            await server.persist_channel(replacement)
            if not channel.registered:
                await server.db.audit(*audit_values)
        channel.topic, channel.subject, channel.language = replacement.topic, replacement.subject, replacement.language
        channel.properties, channel.secrets, channel.modes = replacement.properties, replacement.secrets, replacement.modes
    if name == "TOPIC":
        server.broadcast_channel(channel, "TOPIC", channel.name, prefix=getattr(actor, "prefix", server.settings.get("server_name")), trailing=value)
    else:
        for member_id in tuple(channel.members):
            member = server.sessions.get(member_id)
            if member and member.ircx and can_read_property(server, member, channel, definition):
                member.send("PROP", channel.name, name, prefix=server.settings.get("server_name"), trailing=property_value(channel, name))
    server.emit("channel.changed", channel_id=channel.id)
    return channel
