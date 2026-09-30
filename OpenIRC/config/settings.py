"""Configuration shared by headless and desktop hosts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
import ipaddress
import os
import re
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ServerSettings:
    server_name: str = "irc.openirc.local"
    network_name: str = "OpenIRC"
    description: str = "OpenIRC community server"
    admin_name: str = "OpenIRC administrator"
    admin_email: str = ""
    bind_address: str = "127.0.0.1"
    irc_enabled: bool = True
    irc_port: int = 6667
    ipv6_enabled: bool = False
    tls_enabled: bool = False
    tls_port: int = 6697
    tls_cert: str = ""
    tls_key: str = ""
    allow_anonymous: bool = True
    max_users: int = 1000
    max_connections_per_ip: int = 8
    nick_length: int = 32
    max_channels_per_user: int = 20
    max_channels: int = 1000
    default_channel_modes: str = "nt"
    auto_register_channels: bool = False
    destroy_empty_channels: bool = True
    host_privacy: str = "cloak"
    commands_per_second: int = 30
    messages_per_second: int = 10
    connections_per_minute: int = 30
    nick_changes_per_minute: int = 10
    auth_failures: int = 5
    lockout_seconds: int = 300
    flood_penalty_seconds: float = 2.0
    require_tls_auth: bool = True
    ircx_enabled: bool = True
    listx_enabled: bool = True
    access_enabled: bool = True
    prop_enabled: bool = True
    create_enabled: bool = True
    auditorium_enabled: bool = True
    unicode_mode: str = "modern"
    log_level: str = "INFO"
    log_directory: str = ""
    log_max_bytes: int = 5_000_000
    log_retention: int = 5
    auto_start: bool = False
    minimize_to_tray: bool = False
    confirm_shutdown: bool = True
    motd: str = "Welcome to OpenIRC."
    reservation_policy: str = "off"
    registration_timeout: int = 60
    ping_interval: int = 120
    ping_timeout: int = 60
    send_queue_limit: int = 256
    listx_limit: int = 200
    setup_complete: bool = False

    def validate(self) -> None:
        defaults = type(self)()
        for field in fields(self):
            value, default = getattr(self, field.name), getattr(defaults, field.name)
            if isinstance(default, bool):
                if type(value) is not bool:
                    raise ValueError(f"{field.name} must be a boolean")
            elif isinstance(default, int):
                if type(value) is not int:
                    raise ValueError(f"{field.name} must be an integer")
            elif isinstance(default, float):
                if type(value) not in (float, int):
                    raise ValueError(f"{field.name} must be a number")
            elif not isinstance(value, str):
                raise ValueError(f"{field.name} must be text")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", self.server_name) or "." not in self.server_name:
            raise ValueError("Server name must be a hostname, such as irc.example.net")
        for name in ("network_name", "description", "admin_name", "admin_email"):
            if len(getattr(self, name)) > 256 or any(c in getattr(self, name) for c in "\r\n\x00"):
                raise ValueError(f"{name} must be at most 256 characters without newlines")
        if not self.network_name or any(c.isspace() for c in self.network_name):
            raise ValueError("Network name must not contain spaces")
        try:
            address = ipaddress.ip_address(self.bind_address)
        except ValueError as exc:
            raise ValueError("Bind address must be an IPv4 or IPv6 address") from exc
        if address.version == 6 and not self.ipv6_enabled:
            raise ValueError("Enable IPv6 to use an IPv6 bind address")
        for name in ("irc_port", "tls_port"):
            if not 0 <= getattr(self, name) <= 65535:
                raise ValueError(f"{name} must be between 0 and 65535 (0 selects a free port)")
        if self.tls_enabled and self.irc_port == self.tls_port and self.irc_port:
            raise ValueError("IRC and TLS ports must differ")
        if self.tls_enabled and not (self.tls_cert and self.tls_key):
            raise ValueError("TLS requires a PEM certificate and private key")
        ranges = {"max_users": (1, 100000), "max_connections_per_ip": (1, 10000),
                  "nick_length": (1, 64), "max_channels_per_user": (1, 1000),
                  "max_channels": (1, 100000), "commands_per_second": (1, 10000),
                  "messages_per_second": (1, 10000), "connections_per_minute": (1, 10000),
                  "nick_changes_per_minute": (1, 10000), "auth_failures": (1, 100),
                  "lockout_seconds": (1, 86400), "registration_timeout": (5, 600),
                  "ping_interval": (1, 3600), "ping_timeout": (1, 3600),
                  "send_queue_limit": (16, 10000), "listx_limit": (1, 10000),
                  "log_max_bytes": (10000, 1000000000), "log_retention": (1, 100)}
        for name, (minimum, maximum) in ranges.items():
            if not minimum <= getattr(self, name) <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
        if not 0 <= self.flood_penalty_seconds <= 60:
            raise ValueError("Flood penalty must be between 0 and 60 seconds")
        if self.host_privacy not in {"full", "cloak", "hidden"}:
            raise ValueError("Host privacy must be full, cloaked or hidden")
        if self.unicode_mode not in {"modern", "historical"}:
            raise ValueError("Unicode mode must be modern or historical")
        if self.reservation_policy not in {"off", "warn", "require"}:
            raise ValueError("Reservation policy must be off, warn or require")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("Invalid logging level")
        if any(c not in "psmntihufwxa" for c in self.default_channel_modes):
            raise ValueError("Default channel modes contain unsupported or parameter modes")
        if len(set(self.default_channel_modes) & set("psh")) > 1:
            raise ValueError("Private, secret and hidden default channel modes are mutually exclusive")
        if "x" in self.default_channel_modes and not self.auditorium_enabled:
            raise ValueError("Enable auditorium before using it as a default channel mode")
        if len(self.motd) > 64000 or "\x00" in self.motd:
            raise ValueError("MOTD must be at most 64000 characters without NUL")

    def updated(self, changes: Mapping[str, Any]) -> ServerSettings:
        known = {field.name for field in fields(self)}
        unknown = changes.keys() - known
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        result = replace(self, **changes)
        result.validate()
        return result

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> ServerSettings:
        return cls().updated({key: value for key, value in values.items() if not key.startswith("_")})

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        if key not in {field.name for field in fields(self)}:
            raise KeyError(key)
        return getattr(self, key)

    def to_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        result = asdict(self)
        # A private key path is useful locally, but is not part of shared exports.
        if not include_secrets:
            result["tls_key"] = ""
        return result


Settings = ServerSettings
RESTART_REQUIRED = frozenset({"server_name", "network_name", "bind_address", "irc_enabled", "irc_port", "ipv6_enabled", "tls_enabled", "tls_port", "tls_cert", "tls_key", "unicode_mode", "host_privacy", "send_queue_limit", "log_directory", "log_max_bytes", "log_retention"})


def default_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "OpenIRC"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "OpenIRC"


async def load_settings(database) -> ServerSettings:
    saved = await database.get_settings()
    return ServerSettings.from_dict(saved) if saved else ServerSettings()


async def save_settings(database, settings: ServerSettings) -> None:
    settings.validate()
    await database.set_settings(settings.to_dict(include_secrets=True))
