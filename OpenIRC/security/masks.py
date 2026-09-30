"""Bounded wildcard and network masks, without attacker-controlled regular expressions."""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import ipaddress
import unicodedata


def irc_casefold(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold().translate(str.maketrans("[]\\^", "{}|~"))


def validate_mask(mask: str) -> str:
    if not isinstance(mask, str) or not 1 <= len(mask) <= 256 or any(ord(c) < 32 or c.isspace() for c in mask):
        raise ValueError("Mask must contain 1–256 characters without whitespace or control characters")
    return mask


def wildcard_match(value: str, mask: str) -> bool:
    """Match IRC wildcards only (* and ?), treating [] as nickname characters."""
    if len(mask) > 256 or len(value) > 1024:
        return False
    folded = irc_casefold(mask).replace("[", "[[]")
    return fnmatch.fnmatchcase(irc_casefold(value), folded)


def matches_mask(mask: str, nick: str = "", username: str = "", host: str = "", ip: str = "", account: str = "", server_name: str = "") -> bool:
    if mask.startswith("$a:"):
        return bool(account) and wildcard_match(account, mask[3:])
    if "$" in mask and ("!" in mask or "@" in mask):
        mask, _, server_mask = mask.rpartition("$")
        if not server_mask or (server_mask != "*" and (not server_name or not wildcard_match(server_name, server_mask))):
            return False
    try:
        network = ipaddress.ip_network(mask, strict=False)
        return bool(ip) and ipaddress.ip_address(ip) in network
    except ValueError:
        pass
    if "!" in mask or "@" in mask:
        normalized = mask if "!" in mask else "*!" + mask
        return wildcard_match(f"{nick}!{username}@{host}", normalized) or wildcard_match(f"{nick}!{username}@{ip}", normalized)
    return wildcard_match(nick, mask) or wildcard_match(host, mask) or wildcard_match(ip, mask)


def matches_ban(ban, session, account_name: str = "") -> bool:
    kind, mask = ban.type, ban.mask
    account = getattr(session, "account", None)
    account_name = account_name or (getattr(account, "username", "") if account else "")
    identity = {"nickname": getattr(session, "nick", ""), "nick": getattr(session, "nick", ""), "user": getattr(session, "username", ""),
                "host": getattr(session, "host", ""), "account": account_name}
    if kind in {"ip", "cidr"}:
        try:
            return ipaddress.ip_address(session.ip) in ipaddress.ip_network(mask, strict=False)
        except ValueError:
            return False
    if kind in identity:
        return wildcard_match(identity[kind], mask) or (kind == "host" and wildcard_match(getattr(session, "ip", ""), mask))
    settings = getattr(getattr(session, "server", None), "settings", {})
    return matches_mask(mask, getattr(session, "nick", ""), getattr(session, "username", ""), getattr(session, "host", ""), getattr(session, "ip", ""), account_name, settings.get("server_name", ""))


ban_matches = matches_ban


def cloak_host(ip: str, secret: str) -> str:
    token = hmac.new(secret.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"user-{token}.openirc"
