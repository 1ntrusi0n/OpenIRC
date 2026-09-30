"""Declarative command dispatch with centralized registration checks."""
from dataclasses import dataclass
from typing import Awaitable, Callable

from .errors import IRCError
from .numerics import IRC, IRCX


@dataclass(frozen=True)
class CommandDefinition:
    handler: Callable[..., Awaitable[None]]
    min_params: int = 0
    registered: bool = True
    ircx: bool = False
    permission: str | None = None


COMMANDS: dict[str, CommandDefinition] = {}


def command(name: str, *, min_params: int = 0, registered: bool = True, ircx: bool = False, permission: str | None = None):
    def register(handler):
        COMMANDS[name] = CommandDefinition(handler, min_params, registered, ircx, permission)
        return handler
    return register


async def dispatch(server, session, message):
    # Lazy import also permits core imports without loading authentication code.
    from . import irc_commands, ircx_commands  # noqa: F401
    name, args = message.command, message.arguments
    definition = COMMANDS.get(name)
    if definition is None:
        raise IRCError(IRC.ERR_UNKNOWNCOMMAND, "Unknown command", (name,))
    ircx_commands = {"IRCX", "ISIRCX", "AUTH", "CREATE", "PROP", "ACCESS", "LISTX", "WHISPER"}
    if name in ircx_commands and not server.settings.get("ircx_enabled", True):
        raise IRCError(IRC.ERR_UNKNOWNCOMMAND, "IRCX is disabled", (name,))
    if name in {"CREATE", "PROP", "ACCESS", "LISTX"} and not server.settings.get(name.lower() + "_enabled", True):
        raise IRCError(IRCX.IRCERR_BADCOMMAND, "Command is disabled", (name,))
    # Discovery is specifically valid before registration.
    discovery = name == "MODE" and args == ("ISIRCX",)
    if discovery and not server.settings.get("ircx_enabled", True):
        raise IRCError(IRC.ERR_UNKNOWNMODE, "IRCX is disabled", ("ISIRCX",))
    if definition.registered and not session.registered and not discovery:
        if name == "NOTICE":
            return
        raise IRCError(IRC.ERR_NOTREGISTERED, "You have not registered")
    if len(args) < definition.min_params:
        if name == "NOTICE":
            return
        raise IRCError(IRC.ERR_NEEDMOREPARAMS, "Not enough parameters", (name,))
    if definition.ircx and not session.ircx:
        raise IRCError(IRCX.IRCERR_BADCOMMAND, "Enable IRCX first", (name,))
    if definition.permission and not server.has_permission(session, definition.permission):
        raise IRCError(IRC.ERR_NOPRIVILEGES, "Permission denied")
    await definition.handler(server, session, args)
