"""Domain records; mutable instances belong exclusively to the server loop."""
from __future__ import annotations

import getpass
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from types import MappingProxyType
from typing import Any, Mapping


def new_id() -> str:
    return str(uuid.uuid4())


def wire_oid() -> str:
    return "0" + secrets.token_hex(4).upper()


class Role(IntEnum):
    MEMBER = 0
    VOICE = 1
    HOST = 2
    OWNER = 3


@dataclass
class Account:
    id: str
    username: str
    display_name: str
    password_hash: str = field(repr=False)
    email: str = ""
    created_at: float = field(default_factory=time.time)
    last_login: float | None = None
    enabled: bool = True
    locked: bool = False
    notes: str = ""

    def to_record(self) -> dict:
        return asdict(self)


@dataclass
class Operator:
    account_id: str
    role: str = "sysop"
    permissions: set[str] = field(default_factory=set)
    enabled: bool = True
    last_login: float | None = None

    def to_record(self) -> dict:
        return {**asdict(self), "permissions": sorted(self.permissions)}


@dataclass
class Membership:
    session_id: str
    role: Role = Role.MEMBER
    joined_at: float = field(default_factory=time.time)


@dataclass
class AccessEntry:
    id: str
    object_id: str
    level: str
    mask: str
    created_by: str
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    reason: str = ""
    creator_role: int = 3

    @property
    def active(self) -> bool:
        return self.expires_at is None or self.expires_at > time.time()

    def to_record(self) -> dict:
        return asdict(self)


@dataclass
class Channel:
    id: str
    oid: str
    name: str
    created_at: float = field(default_factory=time.time)
    founder_id: str | None = None
    registered: bool = False
    topic: str = ""
    subject: str = ""
    language: str = "en-US"
    modes: set[str] = field(default_factory=lambda: set("nt"))
    limit: int = 0
    properties: dict[str, str] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict, repr=False)
    access: list[AccessEntry] = field(default_factory=list)
    members: dict[str, Membership] = field(default_factory=dict)
    invites: set[str] = field(default_factory=set)

    def to_record(self) -> dict:
        record = asdict(self)
        record.pop("members")
        record.pop("invites")
        record["modes"] = sorted(self.modes)
        return record

    @classmethod
    def from_record(cls, record: dict) -> Channel:
        values = dict(record)
        values["modes"] = set(values.get("modes", "nt"))
        values["access"] = [AccessEntry(**entry) for entry in values.get("access", [])]
        values.pop("members", None)
        values.pop("invites", None)
        return cls(**values)


@dataclass
class Ban:
    id: str
    type: str
    mask: str
    reason: str = ""
    created_by: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    enabled: bool = True

    @property
    def active(self) -> bool:
        return self.enabled and (self.expires_at is None or self.expires_at > time.time())

    def to_record(self) -> dict:
        return asdict(self)


ALL_PERMISSIONS = frozenset({
    "view_server", "change_settings", "start_stop", "manage_accounts",
    "manage_operators", "manage_channels", "manage_access", "kick", "kill",
    "force_join", "force_part", "change_nick", "set_modes", "view_ips",
    "view_logs", "manage_bans", "broadcast", "shutdown",
})
ROLE_PERMISSIONS = {
    "administrator": ALL_PERMISSIONS,
    "sysop_manager": ALL_PERMISSIONS,
    "sysop": ALL_PERMISSIONS - {"change_settings", "start_stop", "manage_accounts", "manage_operators", "shutdown"},
    "moderator": frozenset({"view_server", "manage_channels", "manage_access", "kick", "set_modes"}),
}


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    permissions: frozenset[str] = frozenset()
    local: bool = False


def LocalAdministrator() -> Principal:
    return Principal("local-administrator", getpass.getuser(), ALL_PERMISSIONS, True)


@dataclass(frozen=True)
class AdminCommand:
    action: str
    payload: dict[str, Any] = field(default_factory=dict, repr=False)


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ServerSnapshot:
    status: str
    settings: Mapping[str, Any]
    statistics: Mapping[str, Any]
    connections: tuple[Mapping, ...] = ()
    channels: tuple[Mapping, ...] = ()
    accounts: tuple[Mapping, ...] = ()
    operators: tuple[Mapping, ...] = ()
    bans: tuple[Mapping, ...] = ()
    reservations: tuple[Mapping, ...] = ()
    logs: tuple[Mapping, ...] = ()
    audit: tuple[Mapping, ...] = ()

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, freeze(getattr(self, name)))
