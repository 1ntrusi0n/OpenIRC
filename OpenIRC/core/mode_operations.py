"""Validated user/channel mode changes with per-recipient IRC degradation."""
from __future__ import annotations

from dataclasses import replace

from OpenIRC.models.domain import Role
from OpenIRC.protocol.errors import IRCError
from OpenIRC.protocol.modes import CHANNEL_MODES, parse_modes
from OpenIRC.protocol.numerics import IRC as N
from OpenIRC.protocol.unicode import casefold
from OpenIRC.security.passwords import hash_password
from .permissions import can_set_mode, channel_role


class ModeOperations:
    async def prepare_initial_modes(self, channel, actor, spec, args):
        """Validate complete CREATE configuration before publishing the room."""
        try:
            changes = parse_modes(spec, args)
        except ValueError as error:
            raise IRCError(N.ERR_UNKNOWNMODE, str(error), (spec,)) from error
        for change in changes:
            definition = CHANNEL_MODES[change.letter]
            if definition.server_only or change.letter in "qovb":
                raise IRCError(N.ERR_UNKNOWNMODE, "This mode cannot be used during CREATE", (change.letter,))
            if definition.min_role == "administrator" and not self.has_permission(actor, "change_settings"):
                raise IRCError(N.ERR_CHANOPRIVSNEEDED, "Administrator permission required", (channel.name,))
            if change.letter == "x" and not self.settings.get("auditorium_enabled", True):
                raise IRCError(N.ERR_UNKNOWNMODE, "Auditorium is disabled", ("x",))
            if change.letter == "k":
                if change.adding:
                    channel.secrets["MEMBERKEY"] = await hash_password(change.argument)
                else:
                    channel.secrets.pop("MEMBERKEY", None)
            elif change.letter == "l":
                channel.limit = int(change.argument) if change.adding else 0
            (channel.modes.add if change.adding else channel.modes.discard)(change.letter)
            if change.adding and change.letter in "psh":
                channel.modes.difference_update(set("psh") - {change.letter})

    async def set_modes(self, actor, target, mode_string, args):
        channel = self.find_channel(target)
        if channel is None:
            return await self._user_modes(actor, target, mode_string)
        try:
            changes = parse_modes(mode_string, args)
        except ValueError as error:
            raise IRCError(N.ERR_UNKNOWNMODE, str(error), (mode_string,)) from error
        for change in changes:
            if not can_set_mode(actor, channel, change.letter):
                raise IRCError(N.ERR_CHANOPRIVSNEEDED, "Insufficient privileges for this mode", (channel.name,))
            if change.letter == "x" and not self.settings.get("auditorium_enabled", True):
                raise IRCError(N.ERR_UNKNOWNMODE, "Auditorium mode is disabled", ("x",))
            if CHANNEL_MODES[change.letter].server_only:
                raise IRCError(N.ERR_CHANOPRIVSNEEDED, "Use channel registration management to change this mode", (channel.name,))
        for change in changes:
            if change.letter == "b":
                if change.argument:
                    await self.change_access(actor, channel.name, "ADD" if change.adding else "DELETE", "DENY", change.argument)
                else:
                    for entry in channel.access:
                        if entry.level == "DENY" and entry.active:
                            actor.numeric(N.RPL_BANLIST, channel.name, entry.mask, entry.created_by, int(entry.created_at))
                    actor.numeric(N.RPL_ENDOFBANLIST, channel.name, text="End of channel ban list")
                continue
            key_hash = None
            if change.letter == "k" and change.adding:
                key_hash = await hash_password(change.argument)
            async with self.lock:
                channel = self.find_channel(target)
                if channel is None:
                    raise IRCError(N.ERR_NOSUCHCHANNEL, "Channel no longer exists", (target,))
                if not can_set_mode(actor, channel, change.letter):
                    raise IRCError(N.ERR_CHANOPRIVSNEEDED, "Privileges changed", (channel.name,))
                updated = replace(channel, modes=set(channel.modes), secrets=dict(channel.secrets))
                membership_target = None
                new_role = None
                if change.letter in "qov":
                    membership_target = self.find_user(change.argument)
                    member = channel.members.get(membership_target.id) if membership_target else None
                    if not member:
                        raise IRCError(N.ERR_USERNOTINCHANNEL, "User is not in this channel", (change.argument, channel.name))
                    own_role = channel_role(actor, channel)
                    override = self.has_permission(actor, "set_modes")
                    if member.role == Role.OWNER and own_role < Role.OWNER and not override:
                        raise IRCError(N.ERR_CHANOPRIVSNEEDED, "Hosts cannot change owners", (channel.name,))
                    value = {"q": Role.OWNER, "o": Role.HOST, "v": Role.VOICE}[change.letter]
                    if change.adding:
                        new_role = max(member.role, value)
                    elif member.role == value:
                        new_role = Role.HOST if value == Role.OWNER else Role.MEMBER
                    else:
                        new_role = member.role
                elif change.letter == "k":
                    if change.adding:
                        updated.secrets["MEMBERKEY"] = key_hash
                    else:
                        updated.secrets.pop("MEMBERKEY", None)
                elif change.letter == "l":
                    updated.limit = int(change.argument) if change.adding else 0
                if change.letter not in "qov":
                    (updated.modes.add if change.adding else updated.modes.discard)(change.letter)
                    if change.adding and change.letter in "psh":
                        updated.modes.difference_update(set("psh") - {change.letter})
                await self.persist_channel(updated)
                # Membership changes may have happened while the database worker committed.
                before = replace(channel, modes=set(channel.modes), members={key: replace(value) for key, value in channel.members.items()})
                updated.members = dict(channel.members)
                if membership_target and membership_target.id in updated.members:
                    updated.members[membership_target.id] = replace(updated.members[membership_target.id], role=new_role)
                updated.invites = channel.invites
                self.channels[casefold(channel.name)] = updated
            self.refresh_channel_visibility(before, updated)
            for identity in list(updated.members):
                viewer = self.sessions.get(identity)
                if not viewer:
                    continue
                letter = change.letter
                if membership_target:
                    if not self.visible_member(viewer, membership_target, updated):
                        continue
                    if letter == "q" and not viewer.ircx:
                        # Owners remain ordinary operators when demoted to Host.
                        if not change.adding and new_role >= Role.HOST:
                            continue
                        letter = "o"
                mode_args = []
                if change.argument is not None:
                    mode_args.append("*" if letter == "k" else change.argument)
                viewer.send("MODE", updated.name, ("+" if change.adding else "-") + letter, *mode_args,
                            prefix=getattr(actor, "prefix", self.settings.get("server_name")))
            self.emit("ChannelModeChanged", id=updated.id, mode=change.letter)

    async def _user_modes(self, actor, target, spec):
        session = self.find_user(target)
        if not session:
            raise IRCError(N.ERR_NOSUCHNICK, "No such nickname", (target,))
        if getattr(actor, "id", None) != session.id and not self.has_permission(actor, "set_modes"):
            raise IRCError(N.ERR_USERSDONTMATCH, "You cannot change another user's modes")
        adding = True
        for letter in spec:
            if letter in "+-":
                adding = letter == "+"
                continue
            if letter not in "iwo" or (letter == "o" and adding):
                raise IRCError(N.ERR_UMODEUNKNOWNFLAG, "Unsupported user mode")
            (session.modes.add if adding else session.modes.discard)(letter)
            if letter == "o" and not adding:
                session.operator_role = None
                session.permissions.clear()
            session.send("MODE", session.nick, ("+" if adding else "-") + letter, prefix=session.prefix)
