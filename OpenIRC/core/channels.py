"""Channel lifecycle, visibility and message routing shared by all transports."""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from OpenIRC.models.domain import Channel, Membership, Role, new_id
from OpenIRC.protocol.errors import IRCError
from OpenIRC.protocol.numerics import IRC as N, IRCX as X
from OpenIRC.protocol.modes import prefix_for_role
from OpenIRC.protocol.unicode import casefold, expand_lines, incoming_name, valid_channel
from OpenIRC.security.masks import matches_mask
from OpenIRC.security.passwords import verify_password
from .permissions import can_change_topic, can_kick


class ChannelOperations:
    def visible_channel(self, viewer, channel) -> bool:
        if viewer.id in channel.members or self.has_permission(viewer, "manage_channels"):
            return True
        return not bool(channel.modes & set("psh"))

    def visible_member(self, viewer, target, channel) -> bool:
        if not self.visible_channel(viewer, channel):
            return False
        if viewer.id == target.id:
            return True
        if self.has_permission(viewer, "manage_channels"):
            return True
        if "x" not in channel.modes:
            return True
        own, other = channel.members.get(viewer.id), channel.members.get(target.id)
        return bool((own and own.role >= Role.HOST) or (other and other.role >= Role.HOST))

    def visible_user(self, viewer, target) -> bool:
        if viewer.id == target.id or self.has_permission(viewer, "view_server"):
            return True
        common = [channel for channel in self.channels.values() if viewer.id in channel.members and target.id in channel.members]
        if common:
            return any(self.visible_member(viewer, target, channel) for channel in common)
        return "i" not in target.modes

    def channel_names(self, viewer, channel) -> list[str]:
        return [prefix_for_role(member.role, viewer.ircx) + self.render_nick(viewer, self.sessions[identity])
                for identity, member in channel.members.items()
                if identity in self.sessions and self.visible_member(viewer, self.sessions[identity], channel)]

    def broadcast_channel(self, channel, command, *params, prefix=None, trailing=None, source=None, exclude=None) -> None:
        for identity in list(channel.members):
            recipient = self.sessions.get(identity)
            if not recipient or identity == exclude:
                continue
            if source and not self.visible_member(recipient, source, channel):
                continue
            recipient.send(command, *params, prefix=prefix, trailing=trailing)

    def refresh_channel_visibility(self, old, new) -> None:
        """Synthesize membership deltas when auditorium/roles change."""
        for viewer_id in new.members:
            viewer = self.sessions.get(viewer_id)
            if not viewer:
                continue
            for target_id in new.members:
                target = self.sessions.get(target_id)
                if not target or target_id == viewer_id:
                    continue
                before = self.visible_member(viewer, target, old)
                after = self.visible_member(viewer, target, new)
                if before and not after:
                    viewer.send("PART", new.name, prefix=target.prefix, trailing="Auditorium visibility changed")
                elif after and not before:
                    viewer.send("JOIN", new.name, prefix=target.prefix)
                    member = new.members[target_id]
                    mode = "q" if member.role == Role.OWNER and viewer.ircx else "o" if member.role >= Role.HOST else "v" if member.role == Role.VOICE else None
                    if mode:
                        viewer.send("MODE", new.name, "+" + mode, target.nick)

    def _matching_access(self, channel, session):
        return [entry for entry in channel.access if entry.active and matches_mask(entry.mask, session.nick, session.username, session.host, session.ip, self.account_name(session), self.settings.server_name)]

    async def join(self, session, name, key="", create=False, modes="", force=False, mode_args=None):
        name = incoming_name(name, historical=self.settings.get("unicode_mode") == "historical")
        if not valid_channel(name):
            raise IRCError(N.ERR_BADCHANMASK, "Invalid channel name", (name,))
        channel = self.find_channel(name)
        if channel and session.id in channel.members:
            return channel
        if sum(session.id in room.members for room in self.channels.values()) >= self.settings.get("max_channels_per_user", 20) and not force:
            raise IRCError(N.ERR_TOOMANYCHANNELS, "Too many channels", (name,))
        new = channel is None
        if new:
            if len(self.channels) >= self.settings.get("max_channels", 500):
                raise IRCError(N.ERR_CHANNELISFULL, "Server channel limit reached", (name,))
            channel = Channel(new_id(), self.new_oid(), name, founder_id=session.account_id,
                              registered=self.settings.get("auto_register_channels", False) and bool(session.account_id),
                              modes=set(self.settings.get("default_channel_modes", "nt").replace("+", "")))
            if channel.registered:
                channel.modes.add("r")
            if modes:
                try:
                    await self.prepare_initial_modes(channel, session, modes, mode_args or [])
                except BaseException:
                    self._wire_ids.discard(channel.oid)
                    raise
        checked_secrets = dict(channel.secrets)
        role = Role.OWNER if new or (session.account_id and channel.founder_id == session.account_id) else Role.MEMBER
        matching = self._matching_access(channel, session)
        granted = any(entry.level in {"OWNER", "HOST", "VOICE", "GRANT"} for entry in matching)
        for level, value in (("VOICE", Role.VOICE), ("HOST", Role.HOST), ("OWNER", Role.OWNER)):
            if any(entry.level == level for entry in matching):
                role = max(role, value)
        key_ok = not channel.secrets.get("MEMBERKEY")
        key_role = Role.MEMBER
        if key:
            async with self._auth_slots:
                for key_name, value in (("OWNERKEY", Role.OWNER), ("HOSTKEY", Role.HOST), ("MEMBERKEY", Role.MEMBER)):
                    if channel.secrets.get(key_name) and await verify_password(channel.secrets[key_name], key):
                        key_role, key_ok = value, True
                        break
        if session.closing or session.id not in self.sessions:
            raise IRCError(N.ERR_NOTREGISTERED, "Session is no longer connected")
        if not new and (self.find_channel(name) is not channel or channel.secrets != checked_secrets):
            raise IRCError(N.ERR_BADCHANNELKEY, "Channel settings changed; retry joining", (name,))
        # Access rules may have changed during password verification.
        matching = self._matching_access(channel, session)
        granted = any(entry.level in {"OWNER", "HOST", "VOICE", "GRANT"} for entry in matching)
        role = Role.OWNER if new or (session.account_id and channel.founder_id == session.account_id) else key_role
        for level, value in (("VOICE", Role.VOICE), ("HOST", Role.HOST), ("OWNER", Role.OWNER)):
            if any(entry.level == level for entry in matching):
                role = max(role, value)
        bypass = force or role >= Role.HOST
        try:
            if not bypass:
                if "a" in channel.modes and not session.account_id:
                    raise IRCError(N.ERR_NEEDREGGEDNICK, "An authenticated account is required", (name,))
                if not granted and any(entry.level == "DENY" for entry in matching):
                    raise IRCError(N.ERR_BANNEDFROMCHAN, "You are banned from this channel", (name,))
                if "i" in channel.modes and session.id not in channel.invites:
                    raise IRCError(N.ERR_INVITEONLYCHAN, "Invite-only channel", (name,))
                if "k" in channel.modes and not key_ok:
                    raise IRCError(N.ERR_BADCHANNELKEY, "Incorrect channel key", (name,))
                if channel.limit and len(channel.members) >= channel.limit:
                    raise IRCError(N.ERR_CHANNELISFULL, "Channel is full", (name,))
        except IRCError:
            if "u" in channel.modes:
                for identity, member in channel.members.items():
                    if member.role >= Role.HOST and identity in self.sessions:
                        host = self.sessions[identity]
                        if host.ircx:
                            host.send("KNOCK", channel.name, prefix=session.prefix, trailing="Join request denied")
                        else:
                            host.send("NOTICE", host.nick, trailing=f"{session.nick} could not join {channel.name}")
            raise
        if new:
            async with self.lock:
                existing = self.find_channel(name)
                if existing is None:
                    await self.persist_channel(channel)
                    self.channels[casefold(name)] = channel
            if existing is not None:
                self._wire_ids.discard(channel.oid)
                return await self.join(session, name, key, create=create, force=force)
        channel.members[session.id] = Membership(session.id, role)
        channel.invites.discard(session.id)
        if new and create and session.ircx:
            session.send("CREATE", channel.name, prefix=session.prefix)
        self.broadcast_channel(channel, "JOIN", channel.name, prefix=session.prefix, source=session)
        if role >= Role.VOICE:
            for identity in list(channel.members):
                viewer = self.sessions.get(identity)
                if viewer and self.visible_member(viewer, session, channel):
                    letter = "q" if role == Role.OWNER and viewer.ircx else "o" if role >= Role.HOST else "v"
                    viewer.send("MODE", channel.name, "+" + letter, session.nick)
        from OpenIRC.protocol.irc_commands import send_names, send_topic
        send_topic(session, channel)
        send_names(self, session, channel)
        for line in expand_lines(channel.properties.get("ONJOIN", "")):
            if line:
                session.send("PRIVMSG", session.nick, prefix=channel.name, trailing=line)
        self.statistics["current_channels"] = sum(bool(room.members) for room in self.channels.values())
        self.statistics["peak_channels"] = max(self.statistics["peak_channels"], self.statistics["current_channels"])
        self.log("Channels", f"{session.nick} joined {channel.name}")
        self.emit("UserJoinedChannel", id=session.id, channel=channel.id)
        return channel

    async def part(self, session, channel_name, reason="") -> None:
        channel = self.find_channel(channel_name)
        if not channel:
            raise IRCError(N.ERR_NOSUCHCHANNEL, "No such channel", (channel_name,))
        if session.id not in channel.members:
            raise IRCError(N.ERR_NOTONCHANNEL, "You are not on this channel", (channel_name,))
        self.broadcast_channel(channel, "PART", channel.name, prefix=session.prefix, trailing=reason or session.nick, source=session)
        for line in expand_lines(channel.properties.get("ONPART", "")):
            if line:
                session.send("NOTICE", session.nick, prefix=channel.name, trailing=line)
        del channel.members[session.id]
        session.channel_message_times.pop(channel.id, None)
        self._destroy_if_empty(channel)
        self.emit("UserPartedChannel", id=session.id, channel=channel.id)

    def _destroy_if_empty(self, channel) -> None:
        if not channel.members and not channel.registered and self.settings.get("destroy_empty_channels", True):
            self.channels.pop(casefold(channel.name), None)
            self._wire_ids.discard(channel.oid)
            self.log("Channels", f"Temporary channel removed: {channel.name}")
            self.emit("ChannelDestroyed", id=channel.id)

    async def kick(self, actor, channel_name, target_nick, reason="") -> None:
        channel, target = self.find_channel(channel_name), self.find_user(target_nick)
        if not channel:
            raise IRCError(N.ERR_NOSUCHCHANNEL, "No such channel", (channel_name,))
        if not target or target.id not in channel.members:
            raise IRCError(N.ERR_USERNOTINCHANNEL, "User is not on that channel", (target_nick, channel.name))
        if not can_kick(actor, target, channel):
            raise IRCError(N.ERR_CHANOPRIVSNEEDED, "Insufficient channel privileges", (channel.name,))
        prefix = getattr(actor, "prefix", self.settings.get("server_name"))
        self.broadcast_channel(channel, "KICK", channel.name, target.nick, prefix=prefix, trailing=reason or "Removed by moderator", source=target)
        del channel.members[target.id]
        self._destroy_if_empty(channel)
        self.statistics["kicks"] += 1
        self.log("Moderation", f"Kicked {target.nick} from {channel.name}")
        await self.db.audit(getattr(actor, "name", getattr(actor, "nick", "operator")), "kick", "channel", channel.id, f"Removed {target.nick}")
        self.emit("UserKicked", id=target.id, channel=channel.id)

    async def set_topic(self, actor, channel_name, topic: str) -> None:
        channel = self.find_channel(channel_name)
        if not channel:
            raise IRCError(N.ERR_NOSUCHCHANNEL, "No such channel", (channel_name,))
        if not can_change_topic(actor, channel):
            raise IRCError(N.ERR_CHANOPRIVSNEEDED, "You cannot change this topic", (channel.name,))
        if len(topic.encode("utf-8")) > 300 or any(c in topic for c in "\r\n\x00"):
            raise IRCError(N.ERR_NEEDMOREPARAMS, "Topic is too long or contains invalid characters", ("TOPIC",))
        async with self.lock:
            channel = self.find_channel(channel_name)
            if channel is None:
                raise IRCError(N.ERR_NOSUCHCHANNEL, "Channel no longer exists", (channel_name,))
            if not can_change_topic(actor, channel):
                raise IRCError(N.ERR_CHANOPRIVSNEEDED, "Privileges changed", (channel.name,))
            updated = replace(channel, topic=topic, properties={**channel.properties, "_TOPIC_TIME": str(int(time.time())), "_TOPIC_SETTER": getattr(actor, "nick", getattr(actor, "name", "server"))})
            await self.persist_channel(updated)
            self.channels[casefold(channel.name)] = updated
        self.broadcast_channel(updated, "TOPIC", updated.name, prefix=getattr(actor, "prefix", self.settings.get("server_name")), trailing=topic)
        self.emit("ChannelPropertyChanged", id=updated.id, property="TOPIC")

    async def route_message(self, session, target: str, text: str, notice=False, whisper_channel=None) -> None:
        if not text:
            raise IRCError(N.ERR_NOTEXTTOSEND, "No text to send")
        if not session.messages.allow():
            self.log("Security", f"Message throttle for {session.nick}", "WARNING")
            if not notice:
                session.send("NOTICE", session.nick, trailing="You are sending messages too quickly")
            return
        command = "NOTICE" if notice else "PRIVMSG"
        if whisper_channel:
            room = self.find_channel(whisper_channel)
            if not room or session.id not in room.members:
                raise IRCError(N.ERR_NOTONCHANNEL, "You are not a member of this channel", (whisper_channel,))
            if "w" in room.modes:
                raise IRCError(X.IRCERR_NOWHISPER, "Whispers are disabled", (room.name,))
            if self.find_channel(target) or target.startswith(("#", "&", "%#", "%&")):
                raise IRCError(N.ERR_NOSUCHNICK, "WHISPER requires a nickname target", (target,))
        channel = self.find_channel(target)
        if channel:
            self._check_channel_message(session, channel)
            lag = min(2.0, max(0.0, float(channel.properties.get("LAG") or 0)))
            if lag:
                elapsed = time.monotonic() - session.channel_message_times.get(channel.id, 0)
                if elapsed < lag:
                    await asyncio.sleep(lag - elapsed)
                    channel = self.find_channel(target)
                    if not channel:
                        raise IRCError(N.ERR_NOSUCHCHANNEL, "Channel no longer exists", (target,))
                    self._check_channel_message(session, channel)
            if len(session.channel_message_times) >= 128 and channel.id not in session.channel_message_times:
                session.channel_message_times.pop(next(iter(session.channel_message_times)))
            session.channel_message_times[channel.id] = time.monotonic()
            self.broadcast_channel(channel, command, channel.name, prefix=session.prefix, trailing=text, source=session, exclude=session.id)
        else:
            recipient = self.find_user(target)
            if not recipient or not recipient.registered:
                raise IRCError(N.ERR_NOSUCHNICK, "No such nickname", (target,))
            entries = self.user_access.get(recipient.account_id or recipient.id, [])
            matching = [entry for entry in entries if entry.active and matches_mask(entry.mask, session.nick, session.username, session.host, session.ip, self.account_name(session), self.settings.server_name)]
            if any(entry.level == "DENY" for entry in matching) and not any(entry.level == "GRANT" for entry in matching):
                return
            if whisper_channel:
                room = self.find_channel(whisper_channel)
                if not room or session.id not in room.members or recipient.id not in room.members:
                    raise IRCError(N.ERR_NOTONCHANNEL, "Both users must be channel members", (whisper_channel,))
                if "w" in room.modes:
                    raise IRCError(X.IRCERR_NOWHISPER, "Whispers are disabled", (room.name,))
                if recipient.ircx:
                    recipient.send("WHISPER", room.name, recipient.nick, prefix=session.prefix, trailing=text)
                else:
                    recipient.send(command, recipient.nick, prefix=session.prefix, trailing=text)
            else:
                recipient.send(command, recipient.nick, prefix=session.prefix, trailing=text)
            if recipient.away and not notice:
                session.numeric(N.RPL_AWAY, recipient.nick, text=recipient.away)
        self.statistics["messages_routed"] += 1
        self.emit("MessageSent", source=session.id)

    def _check_channel_message(self, session, channel) -> None:
        if session.closing or session.id not in self.sessions:
            raise IRCError(N.ERR_NOTREGISTERED, "Session disconnected")
        member = channel.members.get(session.id)
        if not self.visible_channel(session, channel) or ("n" in channel.modes and not member):
            raise IRCError(N.ERR_CANNOTSENDTOCHAN, "Cannot send to channel", (channel.name,))
        if "m" in channel.modes and (not member or member.role < Role.VOICE) and not self.has_permission(session, "manage_channels"):
            raise IRCError(N.ERR_CANNOTSENDTOCHAN, "Channel is moderated", (channel.name,))
        matching = self._matching_access(channel, session)
        if any(entry.level == "DENY" for entry in matching) and (not member or member.role < Role.HOST):
            if not any(entry.level != "DENY" for entry in matching):
                raise IRCError(N.ERR_CANNOTSENDTOCHAN, "Channel access denied", (channel.name,))
