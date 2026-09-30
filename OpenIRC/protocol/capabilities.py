"""CAP negotiation and bounded SASL PLAIN framing."""
import base64
import binascii

from .errors import IRCError
from .numerics import IRC, SASL

CAPABILITIES = {"sasl": "PLAIN"}


async def handle_cap(server, session, args):
    command = args[0].upper()
    if command in ("LS", "REQ") and not session.registered:
        session.cap_negotiating = True
    if command == "LS":
        version302 = len(args) > 1 and args[1] == "302"
        session.send("CAP", session.nick or "*", "LS", prefix=server.settings.get("server_name"),
                     trailing="sasl=PLAIN" if version302 else "sasl")
    elif command == "LIST":
        session.send("CAP", session.nick or "*", "LIST", prefix=server.settings.get("server_name"), trailing=" ".join(sorted(session.caps)))
    elif command == "REQ":
        requested = args[1] if len(args) > 1 else ""
        tokens = requested.split()
        accepted = bool(tokens) and all(token.removeprefix("-") in CAPABILITIES for token in tokens)
        if accepted:
            for token in tokens:
                if token.startswith("-"):
                    session.caps.discard(token[1:])
                    if token == "-sasl":
                        session.sasl_mechanism = None
                        session.sasl_buffer = ""
                else:
                    session.caps.add(token)
        session.send("CAP", session.nick or "*", "ACK" if accepted else "NAK", prefix=server.settings.get("server_name"), trailing=requested)
    elif command == "END":
        session.cap_negotiating = False
        session.sasl_mechanism = None
        session.sasl_buffer = ""
        await server.maybe_register(session)
    else:
        raise IRCError(IRC.ERR_INVALIDCAPCMD, "Invalid CAP subcommand", (command,))


def decode_plain(encoded: str) -> tuple[str, str]:
    try:
        payload = base64.b64decode(encoded, validate=True).decode("utf-8", "strict")
        authzid, username, password = payload.split("\x00")
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise ValueError("Invalid PLAIN authentication payload") from exc
    if not username or not password or (authzid and authzid != username):
        raise ValueError("Invalid PLAIN authentication identity")
    if len(username.encode("utf-8")) > 64 or len(password.encode("utf-8")) > 1024:
        raise ValueError("Authentication payload is too long")
    return username, password


async def handle_authenticate(server, session, args):
    token = args[0]
    if "sasl" not in session.caps:
        raise IRCError(SASL.ERR_SASLFAIL, "Enable the sasl capability first")
    if session.account_id:
        raise IRCError(SASL.ERR_SASLALREADY, "Already authenticated")
    if token == "*":
        session.sasl_mechanism = None
        session.sasl_buffer = ""
        raise IRCError(SASL.ERR_SASLABORTED, "SASL authentication aborted")
    if session.sasl_mechanism is None:
        if token.upper() != "PLAIN":
            session.numeric(SASL.RPL_SASLMECHS, "PLAIN", text="Available SASL mechanisms")
            raise IRCError(SASL.ERR_SASLFAIL, "Unsupported SASL mechanism")
        if server.settings.get("require_tls_auth", True) and not session.is_secure:
            raise IRCError(SASL.ERR_SASLFAIL, "TLS is required for account authentication")
        session.sasl_mechanism = "PLAIN"
        session.sasl_buffer = ""
        session.send("AUTHENTICATE", "+")
        return
    if len(token) > 400 or (token != "+" and not token.isascii()):
        session.sasl_mechanism = None
        session.sasl_buffer = ""
        raise IRCError(SASL.ERR_SASLTOOLONG, "Invalid SASL chunk size")
    if token != "+":
        session.sasl_buffer += token
    if len(session.sasl_buffer) > 4096:
        session.sasl_mechanism = None
        session.sasl_buffer = ""
        raise IRCError(SASL.ERR_SASLTOOLONG, "SASL message is too long")
    if len(token) == 400:
        return
    payload, session.sasl_buffer = session.sasl_buffer, ""
    session.sasl_mechanism = None
    try:
        username, password = decode_plain(payload)
        await server.authenticate(session, username, password)
    except (ValueError, IRCError) as exc:
        raise IRCError(SASL.ERR_SASLFAIL, "SASL authentication failed") from exc
    session.numeric(SASL.RPL_LOGGEDIN, session.prefix, server.account_name(session), text="You are now logged in")
    session.numeric(SASL.RPL_SASLSUCCESS, text="SASL authentication successful")
    await server.maybe_register(session)
