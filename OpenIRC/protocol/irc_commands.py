"""Ordinary IRC commands over the shared domain service boundary."""
import fnmatch
import time
from datetime import datetime, timezone

from OpenIRC import __version__
from OpenIRC.core.permissions import channel_role
from OpenIRC.models.domain import Role
from .capabilities import handle_authenticate, handle_cap
from .dispatcher import command
from .errors import IRCError
from .modes import prefix_for_role, isupport_modes
from .numerics import IRC as N
from .unicode import casefold, wire_name, incoming_name


def _channel(server, name):
    channel = server.find_channel(name)
    if channel is None:
        raise IRCError(N.ERR_NOSUCHCHANNEL, "No such channel", (name,))
    return channel


def _user(server, name):
    user = server.find_user(name)
    if user is None or not user.registered:
        raise IRCError(N.ERR_NOSUCHNICK, "No such nickname", (name,))
    return user


def _split_names(names, byte_limit=270):
    chunk = []
    length = 0
    for name in names:
        size = len(name.encode("utf-8")) + 1
        if chunk and length + size > byte_limit:
            yield " ".join(chunk)
            chunk, length = [], 0
        chunk.append(name)
        length += size
    if chunk:
        yield " ".join(chunk)


def send_names(server, session, channel):
    if server.visible_channel(session, channel):
        symbol = "@" if "s" in channel.modes else "*" if "p" in channel.modes else "="
        budget = max(1, 500 - len(f":{server.settings.get('server_name')} 353 {session.nick} {symbol} {channel.name} :".encode()))
        for names in _split_names(server.channel_names(session, channel), budget):
            session.numeric(N.RPL_NAMREPLY, symbol, channel.name, text=names)
    session.numeric(N.RPL_ENDOFNAMES, channel.name, text="End of NAMES list")


def send_topic(session, channel):
    if channel.topic:
        session.numeric(N.RPL_TOPIC, channel.name, text=channel.topic)
        session.numeric(N.RPL_TOPICWHOTIME, channel.name,
                        channel.properties.get("_TOPIC_SETTER", "server"),
                        channel.properties.get("_TOPIC_TIME", str(int(channel.created_at))))
    else:
        session.numeric(N.RPL_NOTOPIC, channel.name, text="No topic is set")


@command("CAP", min_params=1, registered=False)
async def cap(server, session, args):
    await handle_cap(server, session, args)


@command("AUTHENTICATE", min_params=1, registered=False)
async def authenticate(server, session, args):
    await handle_authenticate(server, session, args)


@command("PASS", min_params=1, registered=False)
async def password(server, session, args):
    if session.registered:
        raise IRCError(N.ERR_ALREADYREGISTERED, "You may not reregister")
    session.password = args[0]


@command("NICK", registered=False)
async def nick(server, session, args):
    if not args:
        raise IRCError(N.ERR_NONICKNAMEGIVEN, "No nickname given")
    await server.set_nick(session, args[0])
    await server.maybe_register(session)


@command("USER", min_params=4, registered=False)
async def user(server, session, args):
    if session.registered or session.username:
        raise IRCError(N.ERR_ALREADYREGISTERED, "You may not reregister")
    username = args[0]
    if not username or any(c.isspace() or ord(c) < 32 or c in "@!:*?\x7f" for c in username) or len(username.encode("utf-8")) > 16:
        raise IRCError(N.ERR_NEEDMOREPARAMS, "Invalid username", ("USER",))
    session.username = username
    session.realname = args[3][:100]
    await server.maybe_register(session)


@command("PING", registered=False)
async def ping(server, session, args):
    if not args:
        raise IRCError(N.ERR_NOORIGIN, "No origin specified")
    session.send("PONG", server.settings.get("server_name"), trailing=args[0])


@command("PONG", registered=False)
async def pong(server, session, args):
    session.pending_ping = None


@command("QUIT", registered=False)
async def quit_command(server, session, args):
    await server.disconnect(session, args[0] if args else "Client quit")


@command("JOIN", min_params=1)
async def join(server, session, args):
    if args[0] == "0":
        for channel in tuple(server.channels.values()):
            if session.id in channel.members:
                await server.part(session, channel.name, "Leaving all channels")
        return
    keys = args[1].split(",") if len(args) > 1 else []
    for index, name in enumerate(args[0].split(",")[:10]):
        try:
            await server.join(session, name, keys[index] if index < len(keys) else "")
        except IRCError as exc:
            session.numeric(exc.code, *exc.params, text=exc.text)


@command("PART", min_params=1)
async def part(server, session, args):
    for name in args[0].split(",")[:10]:
        try:
            await server.part(session, name, args[1] if len(args) > 1 else session.nick)
        except IRCError as exc:
            session.numeric(exc.code, *exc.params, text=exc.text)


@command("PRIVMSG")
async def privmsg(server, session, args):
    if not args:
        raise IRCError(N.ERR_NORECIPIENT, "No recipient given", ("PRIVMSG",))
    if len(args) < 2 or not args[1]:
        raise IRCError(N.ERR_NOTEXTTOSEND, "No text to send")
    for target in args[0].split(",")[:5]:
        try:
            await server.route_message(session, target, args[1])
        except IRCError as exc:
            session.numeric(exc.code, *exc.params, text=exc.text)


@command("NOTICE", min_params=2)
async def notice(server, session, args):
    for target in args[0].split(",")[:5]:
        try:
            await server.route_message(session, target, args[1], notice=True)
        except IRCError:
            pass  # NOTICE must never provoke an automatic error response.


@command("TOPIC", min_params=1)
async def topic(server, session, args):
    channel = _channel(server, args[0])
    if len(args) > 1:
        await server.set_topic(session, channel.name, args[1])
    elif server.visible_channel(session, channel):
        send_topic(session, channel)
    else:
        raise IRCError(N.ERR_NOTONCHANNEL, "You are not on that channel", (channel.name,))


@command("MODE", min_params=1)
async def mode(server, session, args):
    if args == ("ISIRCX",):
        from .ircx_commands import send_discovery
        send_discovery(server, session)
        return
    if len(args) > 1:
        await server.set_modes(session, args[0], args[1], list(args[2:]))
        return
    channel = server.find_channel(args[0])
    if channel:
        if not server.visible_channel(session, channel):
            raise IRCError(N.ERR_NOSUCHCHANNEL, "No such channel", (args[0],))
        params = []
        if "k" in channel.modes:
            params.append("*")
        if channel.limit:
            params.append(str(channel.limit))
        session.numeric(N.RPL_CHANNELMODEIS, channel.name, "+" + "".join(sorted(channel.modes)), *params)
        session.numeric(N.RPL_CREATIONTIME, channel.name, str(int(channel.created_at)))
    else:
        target = _user(server, args[0])
        if target.id != session.id:
            raise IRCError(N.ERR_USERSDONTMATCH, "Cannot inspect modes for other users")
        session.numeric(N.RPL_UMODEIS, "+" + "".join(sorted(session.modes)))


@command("NAMES")
async def names(server, session, args):
    if not args:
        # Enumerate complete rooms only, while reserving space for completion.
        budget = max(1, server.settings.get("send_queue_limit", 256) - 20)
        for channel in tuple(server.channels.values()):
            if not server.visible_channel(session, channel):
                continue
            estimated_lines = 2 + sum(len(name.encode()) + 1 for name in server.channel_names(session, channel)) // 100
            if estimated_lines > budget:
                break
            send_names(server, session, channel)
            budget -= estimated_lines
        session.numeric(N.RPL_ENDOFNAMES, "*", text="End of NAMES list")
        return
    for name in args[0].split(",")[:10]:
        channel = server.find_channel(name)
        if channel:
            send_names(server, session, channel)
        else:
            session.numeric(N.RPL_ENDOFNAMES, name, text="End of NAMES list")


@command("LIST")
async def list_channels(server, session, args):
    masks = args[0].split(",")[:10] if args else ["*"]
    if server.settings.get("unicode_mode") == "historical":
        masks = [incoming_name(mask, historical=True) for mask in masks]
    session.numeric(N.RPL_LISTSTART, "Channel", text="Users Name")
    count = 0
    for channel in tuple(server.channels.values()):
        if not server.visible_channel(session, channel) or not any(fnmatch.fnmatchcase(casefold(channel.name), casefold(mask)) for mask in masks):
            continue
        if count >= min(server.settings.get("listx_limit", 200), max(1, server.settings.get("send_queue_limit", 256) - 20)):
            break
        session.numeric(N.RPL_LIST, channel.name, str(len(channel.members)), text=channel.topic)
        count += 1
    session.numeric(N.RPL_LISTEND, text="End of LIST")


@command("INVITE", min_params=2)
async def invite(server, session, args):
    target, channel = _user(server, args[0]), _channel(server, args[1])
    if session.id not in channel.members and not server.has_permission(session, "manage_channels"):
        raise IRCError(N.ERR_NOTONCHANNEL, "You are not on that channel", (channel.name,))
    if "i" in channel.modes and channel_role(session, channel) < Role.HOST and not server.has_permission(session, "manage_channels"):
        raise IRCError(N.ERR_CHANOPRIVSNEEDED, "Channel host privileges required", (channel.name,))
    if target.id in channel.members:
        raise IRCError(N.ERR_USERONCHANNEL, "Already on channel", (target.nick, channel.name))
    channel.invites.add(target.id)
    target.send("INVITE", target.nick, channel.name, prefix=session.prefix)
    session.numeric(N.RPL_INVITING, target.nick, channel.name)
    if target.away:
        session.numeric(N.RPL_AWAY, target.nick, text=target.away)


@command("KICK", min_params=2)
async def kick(server, session, args):
    await server.kick(session, args[0], args[1], args[2] if len(args) > 2 else session.nick)


@command("KILL", min_params=2, permission="kill")
async def kill(server, session, args):
    await server.kill(session, args[0], args[1])


@command("OPER", min_params=2)
async def oper(server, session, args):
    await server.authenticate(session, args[0], args[1], oper=True)
    session.numeric(N.RPL_YOUREOPER, text="You are now an IRC operator")


@command("AWAY")
async def away(server, session, args):
    session.away = args[0][:200] if args else ""
    session.numeric(N.RPL_NOWAWAY if session.away else N.RPL_UNAWAY, text="You are away" if session.away else "You are no longer away")
    server.emit("user.changed", session_id=session.id)


def _can_see_user(server, viewer, target):
    return server.visible_user(viewer, target)


@command("ISON", min_params=1)
async def ison(server, session, args):
    found = [server.find_user(name) for name in " ".join(args).split()[:30]]
    session.numeric(N.RPL_ISON, text=" ".join(session.visible_nick(u) for u in found if u and u.registered))


@command("USERHOST", min_params=1)
async def userhost(server, session, args):
    result = []
    for name in " ".join(args).split()[:5]:
        target = server.find_user(name)
        if target and target.registered:
            result.append(f"{session.visible_nick(target)}{'*' if target.operator_role else ''}={'-' if target.away else '+'}{target.username}@{target.host}")
    session.numeric(N.RPL_USERHOST, text=" ".join(result))


@command("WHO")
async def who(server, session, args):
    mask = args[0] if args else "*"
    channel = server.find_channel(mask)
    count = 0
    for target in tuple(server.sessions.values()):
        if not target.registered:
            continue
        if channel:
            if target.id not in channel.members or not server.visible_channel(session, channel) or not server.visible_member(session, target, channel):
                continue
        elif not _can_see_user(server, session, target) or not any(fnmatch.fnmatchcase(casefold(value), casefold(mask)) for value in (target.nick, target.username, target.host)):
            continue
        if len(args) > 1 and args[1] == "o" and not target.operator_role:
            continue
        if count >= 100:
            break
        flags = ("G" if target.away else "H") + ("*" if target.operator_role else "")
        if channel:
            flags += prefix_for_role(channel.members[target.id].role, session.ircx)
        session.numeric(N.RPL_WHOREPLY, channel.name if channel else "*", target.username, target.host,
                        server.settings.get("server_name"), target.nick, flags, text=f"0 {target.realname}")
        count += 1
    session.numeric(N.RPL_ENDOFWHO, mask, text="End of WHO list")


@command("WHOIS", min_params=1)
async def whois(server, session, args):
    for name in args[-1].split(",")[:5]:
        target = server.find_user(name)
        if not target or not target.registered:
            session.numeric(N.ERR_NOSUCHNICK, name, text="No such nickname")
        else:
            session.numeric(N.RPL_WHOISUSER, target.nick, target.username, target.host, "*", text=target.realname)
            session.numeric(N.RPL_WHOISSERVER, target.nick, server.settings.get("server_name"), text=server.settings.get("description", "OpenIRC server"))
            rooms = [prefix_for_role(ch.members[target.id].role, session.ircx) + wire_name(ch.name, ircx=session.ircx, historical=server.settings.get("unicode_mode") == "historical", channel=True) for ch in server.channels.values()
                     if target.id in ch.members and server.visible_channel(session, ch) and server.visible_member(session, target, ch)]
            for group in _split_names(rooms):
                session.numeric(N.RPL_WHOISCHANNELS, target.nick, text=group)
            if target.operator_role:
                session.numeric(N.RPL_WHOISOPERATOR, target.nick, text="is an IRC operator")
            if target.account_id:
                session.numeric(N.RPL_WHOISACCOUNT, target.nick, server.account_name(target), text="is logged in as")
            if target.away:
                session.numeric(N.RPL_AWAY, target.nick, text=target.away)
            session.numeric(N.RPL_WHOISIDLE, target.nick, str(max(0, int(time.time() - target.last_activity))), str(int(target.connected_at)), text="seconds idle, signon time")
        session.numeric(N.RPL_ENDOFWHOIS, name, text="End of WHOIS list")


@command("WHOWAS", min_params=1)
async def whowas(server, session, args):
    name = args[0]
    lookup = incoming_name(name, historical=server.settings.get("unicode_mode") == "historical")
    try:
        limit = min(20, max(1, int(args[1]))) if len(args) > 1 else 10
    except ValueError:
        raise IRCError(N.ERR_NEEDMOREPARAMS, "Count must be an integer", ("WHOWAS",))
    count = 0
    for record in reversed(getattr(server, "whowas", ())):
        if casefold(record["nick"]) == casefold(lookup):
            rendered = wire_name(record["nick"], ircx=session.ircx, historical=server.settings.get("unicode_mode") == "historical")
            session.numeric(N.RPL_WHOWASUSER, rendered, record["username"], record["host"], "*", text=record["realname"])
            count += 1
            if count >= limit:
                break
    if not count:
        session.numeric(N.ERR_WASNOSUCHNICK, name, text="There was no such nickname")
    session.numeric(N.RPL_ENDOFWHOWAS, name, text="End of WHOWAS")


@command("MOTD")
async def motd(server, session, args):
    send_motd(server, session)


def send_motd(server, session):
    content = server.settings.get("motd", "Welcome to OpenIRC")
    if not content:
        session.numeric(N.ERR_NOMOTD, text="MOTD file is missing")
        return
    session.numeric(N.RPL_MOTDSTART, text=f"- {server.settings.get('server_name')} Message of the day -")
    for line in content.splitlines()[:100]:
        session.numeric(N.RPL_MOTD, text="- " + line)
    session.numeric(N.RPL_ENDOFMOTD, text="End of MOTD command")


@command("LUSERS")
async def lusers(server, session, args):
    send_lusers(server, session)


def send_lusers(server, session):
    clients = [u for u in server.sessions.values() if u.registered]
    operators = sum(bool(u.operator_role) for u in clients)
    session.numeric(N.RPL_LUSERCLIENT, text=f"There are {len(clients)} users and 0 services on 1 servers")
    session.numeric(N.RPL_LUSEROP, str(operators), text="operator(s) online")
    session.numeric(N.RPL_LUSERUNKNOWN, str(len(server.sessions) - len(clients)), text="unknown connection(s)")
    session.numeric(N.RPL_LUSERCHANNELS, str(len(server.channels)), text="channels formed")
    session.numeric(N.RPL_LUSERME, text=f"I have {len(clients)} clients and 0 servers")


def send_isupport(server, session):
    prefix, channel_modes = isupport_modes(session.ircx)
    tokens = ["CHANTYPES=#&", "CASEMAPPING=rfc1459", "NICKLEN=" + str(server.settings.get("nick_length", 32)), "CHANNELLEN=64",
              "TOPICLEN=300", "NETWORK=" + server.settings.get("network_name", "OpenIRC"), prefix, channel_modes, "UTF8ONLY"]
    # Short server names use one line; long valid hostnames safely use several.
    budget = 510 - len(f":{server.settings.get('server_name')} 005 {session.nick or '*'} :are supported by this server".encode())
    group, size = [], 0
    for token in tokens:
        if group and size + len(token.encode()) + 1 > budget:
            session.numeric(N.RPL_ISUPPORT, *group, text="are supported by this server")
            group, size = [], 0
        if len(token.encode()) + 1 <= budget:
            group.append(token)
            size += len(token.encode()) + 1
    if group:
        session.numeric(N.RPL_ISUPPORT, *group, text="are supported by this server")


@command("VERSION")
async def version(server, session, args):
    session.numeric(N.RPL_VERSION, __version__, server.settings.get("server_name"), text="OpenIRC IRC/IRCX server")


@command("TIME")
async def server_time(server, session, args):
    session.numeric(N.RPL_TIME, server.settings.get("server_name"), text=datetime.now(timezone.utc).isoformat())


@command("ADMIN")
async def admin(server, session, args):
    session.numeric(N.RPL_ADMINME, server.settings.get("server_name"), text="Administrative information")
    session.numeric(N.RPL_ADMINLOC1, text=server.settings.get("admin_name", "OpenIRC administrator"))
    session.numeric(N.RPL_ADMINLOC2, text=server.settings.get("description", "OpenIRC community server"))
    session.numeric(N.RPL_ADMINEMAIL, text=server.settings.get("admin_email", "Not configured"))


@command("INFO")
async def info(server, session, args):
    session.numeric(N.RPL_INFO, text=f"OpenIRC {__version__} - open-source IRC/IRCX server")
    session.numeric(N.RPL_INFO, text="Original software distributed under the MIT license")
    session.numeric(N.RPL_ENDOFINFO, text="End of INFO")


@command("STATS")
async def stats(server, session, args):
    query = args[0] if args else "u"
    if not server.has_permission(session, "view_server"):
        raise IRCError(N.ERR_NOPRIVILEGES, "Operator statistics permission required")
    if query == "u":
        started = server.statistics.get("server_start_time")
        uptime = int(time.time() - started) if started else 0
        session.numeric(N.RPL_STATSUPTIME, text=f"Server uptime: {uptime} seconds")
    elif query == "l":
        for target in list(server.sessions.values())[:100]:
            session.numeric(N.RPL_STATSLINKINFO, target.nick or "*", str(target.outgoing.qsize()),
                            str(target.messages_sent), str(target.bytes_out), str(target.messages_received),
                            str(target.bytes_in), str(int(time.time() - target.connected_at)))
    elif query == "m":
        for name, count in sorted(getattr(server, "command_counts", {}).items()):
            session.numeric(N.RPL_STATSCOMMANDS, name, str(count))
    session.numeric(N.RPL_ENDOFSTATS, query, text="End of STATS report")


@command("WALLOPS", min_params=1, permission="broadcast")
async def wallops(server, session, args):
    for target in tuple(server.sessions.values()):
        if target.registered and "w" in target.modes:
            target.send("WALLOPS", prefix=session.prefix, trailing=args[0])


@command("SAJOIN", min_params=2, permission="force_join")
async def force_join(server, session, args):
    target = _user(server, args[0])
    await server.join(target, args[1], force=True)
    await server.db.audit(session.nick, "user.force_join", "user", target.id, f"Joined {args[1]}")


@command("SAPART", min_params=2, permission="force_part")
async def force_part(server, session, args):
    target = _user(server, args[0])
    await server.part(target, args[1], args[2] if len(args) > 2 else "Operator request")
    await server.db.audit(session.nick, "user.force_part", "user", target.id, f"Left {args[1]}")


@command("SANICK", min_params=2, permission="change_nick")
async def force_nick(server, session, args):
    target = _user(server, args[0])
    await server.set_nick(target, args[1], force=True)
    await server.db.audit(session.nick, "user.force_nick", "user", target.id, "Changed nickname")


@command("SAMODE", min_params=2, permission="set_modes")
async def force_mode(server, session, args):
    await server.set_modes(session, args[0], args[1], list(args[2:]))
