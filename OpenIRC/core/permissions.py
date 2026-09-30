"""Single permission policy for IRC, IRCX, chat and administration."""
from __future__ import annotations

from OpenIRC.models.domain import Principal, Role


def has_permission(actor, permission: str) -> bool:
    return bool(getattr(actor, "local", False) or getattr(actor, "local_admin", False) or permission in getattr(actor, "permissions", ()))


def channel_role(actor, channel) -> Role:
    member = channel.members.get(getattr(actor, "id", ""))
    return member.role if member else Role.MEMBER


def can_kick(actor, target, channel) -> bool:
    if has_permission(actor, "kick"):
        return True
    own = channel_role(actor, channel)
    other = channel_role(target, channel)
    return own >= Role.HOST and (other < own or other == Role.HOST == own)


def can_change_topic(actor, channel) -> bool:
    return has_permission(actor, "manage_channels") or channel_role(actor, channel) >= Role.HOST or ("t" not in channel.modes and getattr(actor, "id", "") in channel.members)


def can_set_mode(actor, channel, mode: str) -> bool:
    from OpenIRC.protocol.modes import CHANNEL_MODES
    definition = CHANNEL_MODES.get(mode)
    if definition is None:
        return False
    if has_permission(actor, "set_modes"):
        return True
    if definition.min_role == "administrator":
        return False
    return channel_role(actor, channel) >= (Role.OWNER if definition.min_role == "owner" else Role.HOST)


def can_edit_access(actor, channel) -> bool:
    return has_permission(actor, "manage_access") or channel_role(actor, channel) >= Role.HOST


def can_edit_property(actor, channel, property_name: str) -> bool:
    from OpenIRC.protocol.properties import PROPERTIES
    definition = PROPERTIES.get(property_name.upper())
    if definition is None or definition.read_only:
        return False
    if has_permission(actor, "manage_channels"):
        return True
    if definition.writable_by == "administrator":
        return False
    return channel_role(actor, channel) >= (Role.OWNER if definition.writable_by == "owner" else Role.HOST)


def can_force_join(actor) -> bool:
    return has_permission(actor, "force_join")


def can_kill(actor, target) -> bool:
    return has_permission(actor, "kill")
