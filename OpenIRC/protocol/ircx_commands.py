"""IRCX discovery, authentication, property and access wire adapters."""
import fnmatch
import math
import re
import time

from .access import access_target
from .capabilities import decode_plain
from .dispatcher import command
from .errors import IRCError
from .modes import CHANNEL_MODES, parse_modes
from .numerics import IRC, IRCX as X
from .properties import PROPERTIES, can_read_property, property_value
from .unicode import casefold, decode_historical


def send_discovery(server, session):
    packages = "OPENIRC-PLAIN"
    if server.settings.get("allow_anonymous", True):
        packages += ",ANON"
    session.numeric(X.IRCRPL_IRCX, "1" if session.ircx else "0", "0", packages, "512", "*")


@command("ISIRCX", registered=False)
async def isircx(server, session, args):
    send_discovery(server, session)


@command("IRCX", registered=False)
async def ircx(server, session, args):
    if args:
        raise IRCError(X.IRCERR_TOOMANYARGUMENTS, "IRCX takes no arguments", ("IRCX",))
    session.ircx = True
    send_discovery(server, session)
    if session.registered:
        from .irc_commands import send_isupport
        send_isupport(server, session)


@command("AUTH", min_params=2, registered=False)
async def auth(server, session, args):
    mechanism, sequence = args[0].upper(), args[1].upper()
    if mechanism != "OPENIRC-PLAIN":
        raise IRCError(X.IRCERR_UNKNOWNPACKAGE, "Unsupported authentication package", (mechanism,))
    if session.account_id:
        raise IRCError(X.IRCERR_ALREADYAUTHENTICATED, "Already authenticated", (mechanism,))
    if session.registered:
        raise IRCError(IRC.ERR_ALREADYREGISTERED, "Authenticate before registration")
    if sequence == "*":
        session.ircx_auth_pending = False
        raise IRCError(X.IRCERR_AUTHENTICATIONFAILED, "Authentication aborted", (mechanism,))
    if sequence not in {"I", "S"} or (sequence == "S" and not getattr(session, "ircx_auth_pending", False)):
        raise IRCError(X.IRCERR_BADCOMMAND, "Invalid AUTH sequence", ("AUTH",))
    if server.settings.get("require_tls_auth", True) and not session.is_secure:
        raise IRCError(X.IRCERR_SECURITY, "TLS is required for account authentication")
    if len(args) < 3:
        if sequence == "S":
            raise IRCError(X.IRCERR_AUTHENTICATIONFAILED, "Missing authentication payload", (mechanism,))
        session.ircx_auth_pending = True
        session.send("AUTH", mechanism, "S", trailing="+")
        return
    session.ircx_auth_pending = False
    try:
        username, password = decode_plain(args[2])
        await server.authenticate(session, username, password)
    except (ValueError, IRCError) as exc:
        raise IRCError(X.IRCERR_AUTHENTICATIONFAILED, "Authentication failed", (mechanism,)) from exc
    session.send("AUTH", mechanism, "*", server.account_name(session), session.oid)
    await server.maybe_register(session)


@command("CREATE", min_params=1, ircx=True)
async def create(server, session, args):
    name = args[0]
    mode_string = args[1] if len(args) > 1 else ""
    # Draft +c means fail if the room already exists; it is a CREATE option,
    # not a persistent channel mode.
    require_new = "c" in mode_string
    mode_string = mode_string.replace("c", "")
    existing = server.find_channel(name)
    if existing and require_new:
        raise IRCError(X.IRCERR_CHANNELEXIST, "Channel already exists", (name,))
    if existing and session.id in existing.members:
        raise IRCError(X.IRCERR_ALREADYONCHANNEL, "Already on channel", (name,))
    if mode_string.strip("+-"):
        try:
            parse_modes(mode_string, args[2:])
        except ValueError as exc:
            raise IRCError(X.IRCERR_BADVALUE, str(exc), (name,)) from exc
    await server.join(session, name, create=True, modes=mode_string, mode_args=list(args[2:]))


@command("PROP", min_params=2, ircx=True)
async def prop(server, session, args):
    channel = server.find_channel(args[0])
    if channel is None or not server.visible_channel(session, channel):
        raise IRCError(X.IRCERR_NOSUCHOBJECT, "No such channel", (args[0],))
    names = list(PROPERTIES) if args[1] == "*" else [name.upper() for name in args[1].split(",")]
    if any(name not in PROPERTIES for name in names):
        raise IRCError(X.IRCERR_BADPROPERTY, "Unknown property", (channel.name,))
    if len(args) > 2:
        if len(names) != 1 or args[1] == "*":
            raise IRCError(X.IRCERR_BADCOMMAND, "Set one property at a time", ("PROP",))
        await server.change_property(session, channel.name, names[0], args[2])
        session.numeric(X.IRCRPL_PROPLIST, channel.name, names[0], text=property_value(channel, names[0]))
    else:
        for name in names:
            if can_read_property(server, session, channel, PROPERTIES[name]):
                session.numeric(X.IRCRPL_PROPLIST, channel.name, name, text=property_value(channel, name))
            elif args[1] != "*":
                raise IRCError(X.IRCERR_SECURITY, "Property access denied", (channel.name,))
    session.numeric(X.IRCRPL_PROPEND, channel.name, text="End of properties")


def _access_reply(session, numeric, name, entry):
    timeout = max(1, math.ceil((entry.expires_at - time.time()) / 60)) if entry.expires_at else 0
    actor = "".join("_" if c.isspace() else c for c in entry.created_by) or "*"
    session.numeric(numeric, name, entry.level, entry.mask, str(timeout), actor, text=entry.reason)


@command("ACCESS", min_params=2, ircx=True)
async def access(server, session, args):
    name, operation = args[0], args[1].upper()
    level = args[2].upper() if len(args) > 2 else ""
    mask = args[3] if len(args) > 3 else ""
    timeout, reason = 0, ""
    if operation == "ADD":
        if len(args) > 4:
            try:
                timeout = int(args[4])
            except ValueError:
                # A final trailing reason may omit the optional timeout.
                if len(args) != 5:
                    raise IRCError(X.IRCERR_BADVALUE, "Invalid timeout", (name,))
                reason = args[4]
        if len(args) > 5:
            reason = args[5]
    if operation == "DELETE" and not mask:
        raise IRCError(IRC.ERR_NEEDMOREPARAMS, "ACCESS DELETE requires level and mask", ("ACCESS",))
    entries = await server.change_access(session, name, operation, level, mask, timeout, reason)
    if operation == "LIST":
        session.numeric(X.IRCRPL_ACCESSSTART, name, text="Start of access entries")
        for entry in entries:
            if not level or entry.level == level:
                _access_reply(session, X.IRCRPL_ACCESSLIST, name, entry)
        session.numeric(X.IRCRPL_ACCESSEND, name, text="End of access entries")
    elif operation == "ADD":
        for entry in entries:
            _access_reply(session, X.IRCRPL_ACCESSADD, name, entry)
    else:
        for entry in entries:
            session.numeric(X.IRCRPL_ACCESSDELETE, name, entry.level, entry.mask)
        if operation == "CLEAR":
            session.numeric(X.IRCRPL_ACCESSEND, name, text="Access entries cleared")


def _pattern(value: str) -> str:
    # LISTX permits escaping separators and literal wildcard characters.
    converted = []
    i = 0
    replacements = {"b": " ", "c": ",", "\\": "\\", "*": "[*]", "?": "[?]"}
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            i += 1
            if value[i] not in replacements:
                raise ValueError("Invalid LISTX escape")
            replacement = replacements[value[i]]
            converted.append(replacement if value[i] in "*?" else casefold(replacement))
        else:
            converted.append(casefold(value[i]))
        i += 1
    return "".join(converted)


def parse_listx_query(args, hard_limit: int = 100, *, historical: bool = False):
    raw = list(args)
    limit = hard_limit
    if raw and raw[-1].isdigit():
        requested = int(raw.pop())
        if requested:
            limit = min(hard_limit, requested)
    tokens = [token for item in raw for token in re.split(r"[ ,]+", item) if token]
    if len(tokens) > 20:
        raise ValueError("Too many LISTX query terms")
    filters = []
    channels = []
    for token in tokens:
        if token.startswith(("#", "&", "%#", "%&")):
            if historical:
                token = decode_historical(token)
            channels.append(_pattern(token))
        elif re.fullmatch(r"[<>]\d+", token):
            filters.append(("members", token[0], int(token[1:])))
        elif re.fullmatch(r"[CT][<>]\d+", token, re.I):
            filters.append(("created" if token[0].upper() == "C" else "topic_time", token[1], int(token[2:])))
        elif token.upper() in ("R=0", "R=1"):
            filters.append(("registered", "=", token[-1] == "1"))
        elif len(token) >= 2 and token[0].upper() in "LNST" and token[1] == "=":
            value = decode_historical(token[2:]) if historical and token[0].upper() == "N" else token[2:]
            filters.append(({"L": "language", "N": "name", "S": "subject", "T": "topic"}[token[0].upper()], "match", _pattern(value)))
        else:
            raise ValueError("Invalid LISTX query term")
    return channels, filters, limit


def matches_listx(channel, channels, filters, now=None):
    now = time.time() if now is None else now
    if channels and not any(fnmatch.fnmatchcase(casefold(channel.name), pattern) for pattern in channels):
        return False
    for field, operation, expected in filters:
        if field == "members":
            actual = len(channel.members)
        elif field == "created":
            actual = (now - channel.created_at) / 60
        elif field == "topic_time":
            actual = (now - float(channel.properties.get("_TOPIC_TIME", channel.created_at))) / 60
        else:
            actual = getattr(channel, field)
        if operation == "<" and not actual < expected:
            return False
        if operation == ">" and not actual > expected:
            return False
        if operation == "=" and actual != expected:
            return False
        if operation == "match" and not fnmatch.fnmatchcase(casefold(actual), expected):
            return False
    return True


@command("LISTX", ircx=True)
async def listx(server, session, args):
    try:
        hard_limit = min(server.settings.get("listx_limit", 200), max(1, server.settings.get("send_queue_limit", 256) - 20))
        channels, filters, limit = parse_listx_query(args, hard_limit, historical=server.settings.get("unicode_mode") == "historical")
    except ValueError as exc:
        raise IRCError(X.IRCERR_BADVALUE, str(exc), ("LISTX",)) from exc
    session.numeric(X.IRCRPL_LISTXSTART, text="Start of ListX")
    count, truncated = 0, False
    for channel in tuple(server.channels.values()):
        if not server.visible_channel(session, channel) or not matches_listx(channel, channels, filters):
            continue
        if count >= limit:
            truncated = True
            break
        flags = "+" + "".join(sorted(letter for letter in channel.modes if letter in CHANNEL_MODES and CHANNEL_MODES[letter].argument == "never"))
        session.numeric(X.IRCRPL_LISTXLIST, channel.name, flags, str(len(channel.members)), str(channel.limit), text=channel.topic)
        count += 1
    session.numeric(X.IRCRPL_LISTXTRUNC if truncated else X.IRCRPL_LISTXEND, text="Truncation of ListX" if truncated else "End of ListX")


@command("WHISPER", min_params=3, ircx=True)
async def whisper(server, session, args):
    for target in args[1].split(",")[:5]:
        await server.route_message(session, target, args[2], whisper_channel=args[0])
