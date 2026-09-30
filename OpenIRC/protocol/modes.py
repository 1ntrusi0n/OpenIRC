"""One source of truth for supported channel modes and their arguments."""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ModeDefinition:
    letter: str
    name: str
    argument: str = "never"  # never, always, set, list
    min_role: str = "host"
    server_only: bool = False
    ircx: bool = False
    advisory: bool = False


CHANNEL_MODES = MappingProxyType({m.letter: m for m in (
    ModeDefinition("p", "Private"), ModeDefinition("s", "Secret"),
    ModeDefinition("m", "Moderated"), ModeDefinition("n", "No external messages"),
    ModeDefinition("t", "Restricted topic"), ModeDefinition("i", "Invite only"),
    ModeDefinition("k", "Member key", "always"), ModeDefinition("l", "Member limit", "set"),
    ModeDefinition("b", "Ban", "list"), ModeDefinition("q", "Owner", "always", "owner", ircx=True),
    ModeDefinition("o", "Host", "always"), ModeDefinition("v", "Voice", "always"),
    ModeDefinition("h", "Hidden", ircx=True), ModeDefinition("u", "Knock", ircx=True),
    ModeDefinition("f", "No format indication", min_role="administrator", ircx=True, advisory=True),
    ModeDefinition("w", "No whispers", min_role="owner", ircx=True),
    ModeDefinition("x", "Auditorium", min_role="owner", ircx=True),
    ModeDefinition("r", "Registered", min_role="administrator", server_only=True, ircx=True),
    ModeDefinition("a", "Authenticated users only", ircx=True),
)})
USER_MODES = frozenset("iow")
PLANNED_MODES = MappingProxyType({"d": "Cloneable (planned)", "e": "Clone (planned)", "z": "Service indication (planned)"})


@dataclass(frozen=True, slots=True)
class ModeChange:
    adding: bool
    letter: str
    argument: str | None = None


def parse_modes(spec: str, arguments: list[str] | tuple[str, ...] = ()) -> list[ModeChange]:
    if not spec or len(spec) > 64:
        raise ValueError("Invalid mode string")
    adding = True
    changes = []
    offset = 0
    for letter in spec:
        if letter in "+-":
            adding = letter == "+"
            continue
        definition = CHANNEL_MODES.get(letter)
        if definition is None:
            raise ValueError(f"Unknown channel mode: {letter}")
        argument = None
        if definition.argument in ("always", "list") or (definition.argument == "set" and adding):
            if offset < len(arguments):
                argument = arguments[offset]
                offset += 1
            elif definition.argument != "list":
                raise ValueError(f"Mode {letter} requires a parameter")
        if letter == "l" and adding and (not str(argument).isdigit() or not 1 <= int(argument) <= 1000000):
            raise ValueError("Member limit must be between 1 and 1000000")
        changes.append(ModeChange(adding, letter, argument))
    return changes


def prefix_for_role(role: object, ircx: bool = False) -> str:
    name = str(getattr(role, "name", role)).lower()
    return {"owner": "." if ircx else "@", "host": "@", "voice": "+"}.get(name, "")


def isupport_modes(ircx: bool = False) -> tuple[str, str]:
    prefix = "PREFIX=(qov).@+" if ircx else "PREFIX=(ov)@+"
    return prefix, "CHANMODES=b,k,l,aimnpstuhfwxr"
