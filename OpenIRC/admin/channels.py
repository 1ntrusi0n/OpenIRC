"""Channel-management operations over the same core state used by IRC sessions."""
from __future__ import annotations
import time

from OpenIRC.models.domain import Channel, Role, new_id, wire_oid
from OpenIRC.protocol.unicode import casefold, valid_channel
from OpenIRC.protocol.modes import CHANNEL_MODES
from OpenIRC.protocol.properties import PROPERTIES, validate_property
from OpenIRC.security.passwords import hash_password
from .commands import AdminError


class ChannelAdministration:
    def _channel(self, identifier):
        found = next((c for c in self.server.channels.values() if c.id == identifier), None)
        found = found or self.server.find_channel(identifier)
        if found is None:
            raise AdminError("not_found", "The channel no longer exists")
        return found

    async def _channel_save(self, principal, payload):
        server = self.server
        if "modes" in payload or "limit" in payload or "MEMBERKEY" in {key.upper() for key in payload.get("secrets", {})}:
            self._require(principal, "set_modes")
        replacements = {}
        for name, value in payload.get("secrets", {}).items():
            name = name.upper()
            if name not in {"OWNERKEY", "HOSTKEY", "MEMBERKEY"}:
                raise AdminError("validation", "Unknown channel key")
            validate_property(name, value)
            replacements[name] = await hash_password(value) if value else ""
        async with server.lock:
            original = self._channel(payload["id"]) if payload.get("id") else None
            name = payload.get("name", original.name if original else "")
            if not valid_channel(name):
                raise AdminError("validation", "Channel names must start with # or & and contain no spaces")
            if original and name != original.name:
                raise AdminError("validation", "A channel cannot be renamed; create a new channel instead")
            if not original and server.find_channel(name):
                raise AdminError("conflict", "A channel with that name already exists")
            if not original and len(server.channels) >= server.settings.max_channels:
                raise AdminError("limit", "Maximum channel count reached")
            channel = Channel.from_record(original.to_record()) if original else Channel(new_id(), getattr(server, "new_oid", wire_oid)(), name, modes=set(server.settings.default_channel_modes))
            if original:
                channel.members, channel.invites = original.members, original.invites
            for key in ("topic", "subject", "language"):
                if key in payload:
                    setattr(channel, key, validate_property(key.upper(), payload[key]))
            if "registered" in payload:
                if type(payload["registered"]) is not bool:
                    raise AdminError("validation", "Registered must be a boolean")
                channel.registered = payload["registered"]
            if "founder_id" in payload:
                channel.founder_id = payload["founder_id"] or None
                if channel.founder_id:
                    self._account(channel.founder_id)
            if "modes" in payload:
                mode_value = payload["modes"]
                modes = set(mode_value.replace("+", "")) if isinstance(mode_value, str) else set(mode_value)
                if modes - set(CHANNEL_MODES) or modes & set("bqov"):
                    raise AdminError("validation", "Channel modes contain an unsupported mode or a member privilege")
                if len(modes & set("psh")) > 1:
                    raise AdminError("validation", "Choose only one visibility mode: private, secret or hidden")
                if "x" in modes and not server.settings.auditorium_enabled:
                    raise AdminError("validation", "Auditorium mode is disabled in server settings")
                channel.modes = modes
            if "limit" in payload:
                if type(payload["limit"]) is not int or not 0 <= payload["limit"] <= 1000000:
                    raise AdminError("validation", "Member limit must be between 0 and 1000000")
                channel.limit = payload["limit"]
            for key, value in payload.get("properties", {}).items():
                key = key.upper()
                if key in {"ONJOIN", "ONPART"}:
                    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
                value = validate_property(key, value)
                if PROPERTIES[key].writable_by == "administrator" and value != channel.properties.get(key, ""):
                    self._require(principal, "change_settings")
                if PROPERTIES[key].secret:
                    raise AdminError("validation", "Set secret keys through the replacement fields")
                if key in {"TOPIC", "SUBJECT", "LANGUAGE"}:
                    setattr(channel, key.lower(), value)
                else:
                    channel.properties[key] = value
            if channel.topic != (original.topic if original else ""):
                channel.properties["_TOPIC_TIME"] = str(int(time.time()))
                channel.properties["_TOPIC_SETTER"] = principal.name
            for key, value in replacements.items():
                if value:
                    channel.secrets[key] = value
                else:
                    channel.secrets.pop(key, None)
            if "k" not in channel.modes and "modes" in payload and "MEMBERKEY" not in replacements:
                channel.secrets.pop("MEMBERKEY", None)
            if channel.secrets.get("MEMBERKEY"):
                channel.modes.add("k")
            else:
                channel.modes.discard("k")
            if channel.limit:
                channel.modes.add("l")
            else:
                channel.modes.discard("l")
            if channel.registered:
                channel.modes.add("r")
                await server.db.put("registered_channels", channel.id, channel.to_record())
            else:
                channel.modes.discard("r")
                if original and original.registered:
                    await server.db.delete("registered_channels", channel.id)
            server.channels[casefold(channel.name)] = channel
        if original:
            server.refresh_channel_visibility(original, channel)
            if original.topic != channel.topic:
                server.broadcast_channel(channel, "TOPIC", channel.name, prefix=server.settings.server_name, trailing=channel.topic)
            # Clients refresh state after configuration edits; mode arguments never reveal keys.
            removed = "".join(sorted(original.modes - channel.modes - set("kl")))
            added = "".join(sorted(channel.modes - original.modes - set("kl")))
            if removed or added:
                server.broadcast_channel(channel, "MODE", channel.name, ("-" + removed if removed else "") + ("+" + added if added else ""), prefix=server.settings.server_name)
            if original.limit != channel.limit:
                params = (channel.name, "+l", str(channel.limit)) if channel.limit else (channel.name, "-l")
                server.broadcast_channel(channel, "MODE", *params, prefix=server.settings.server_name)
            if original.secrets.get("MEMBERKEY") != channel.secrets.get("MEMBERKEY"):
                server.broadcast_channel(channel, "MODE", channel.name, "+k" if "k" in channel.modes else "-k", "*", prefix=server.settings.server_name)
        return {"id": channel.id}

    async def _channel_register(self, principal, payload):
        return await self._channel_save(principal, {"id": self._channel(payload["id"]).id, "registered": True})

    async def _channel_unregister(self, principal, payload):
        channel = self._channel(payload["id"])
        result = await self._channel_save(principal, {"id": channel.id, "registered": False})
        if not channel.members and self.server.settings.destroy_empty_channels:
            self.server.channels.pop(casefold(channel.name), None)
        return result

    async def _channel_close(self, principal, payload):
        channel = self._channel(payload["id"])
        if channel.members:
            self._require(principal, "kick")
        for member_id in list(channel.members):
            session = self.server.sessions.get(member_id)
            if session:
                await self.server.kick(principal, channel.name, session.nick, "Channel closed by administrator")
        if not channel.registered:
            self.server.channels.pop(casefold(channel.name), None)

    async def _channel_delete(self, principal, payload):
        channel = self._channel(payload["id"])
        if channel.members:
            self._require(principal, "kick")
        if channel.registered:
            await self._channel_save(principal, {"id": channel.id, "registered": False})
        if self.server.find_channel(channel.name):
            await self._channel_close(principal, {"id": channel.id})

    async def _channel_topic(self, principal, payload):
        channel = self._channel(payload["id"])
        return await self.server.set_topic(principal, channel.name, payload["topic"])

    async def _channel_role(self, principal, payload):
        channel = self._channel(payload["id"])
        session = self._session(payload["session_id"])
        if session.id not in channel.members:
            raise AdminError("not_found", "The user is no longer in this channel")
        try:
            role = Role[payload["role"].upper()] if isinstance(payload["role"], str) else Role(payload["role"])
        except (KeyError, ValueError) as exc:
            raise AdminError("validation", "Unknown channel privilege") from exc
        letter = {Role.OWNER: "q", Role.HOST: "o", Role.VOICE: "v"}
        current = channel.members[session.id].role
        while current > role:
            await self.server.set_modes(principal, channel.name, "-" + letter[current], [session.nick])
            channel = self._channel(payload["id"])
            current = channel.members[session.id].role
        if current < role and role in letter:
            await self.server.set_modes(principal, channel.name, "+" + letter[role], [session.nick])

    async def _channel_kick(self, principal, payload):
        return await self.server.kick(principal, self._channel(payload["id"]).name, self._session(payload["session_id"]).nick, payload.get("reason", "Removed by administrator"))
