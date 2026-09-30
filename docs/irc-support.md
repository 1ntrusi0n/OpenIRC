# IRC support

OpenIRC implements a standalone IRC server using the [modern IRC client protocol reference](https://modern.ircdocs.horse/) and [RFC 1459](https://www.rfc-editor.org/rfc/rfc1459). Normal IRC clients do not need to enable IRCX. This is a single-server implementation; server linking and remote-server command forwarding are planned.

## Implemented commands

| Area | Commands and behavior |
| --- | --- |
| Registration | `PASS`, `NICK`, `USER`, `QUIT`; unique nicknames, registration deadlines and actual welcome/server-support information |
| Connection | `PING`, `PONG`; keepalive and idle connection cleanup |
| Rooms | `JOIN`, `PART`, `NAMES`, `LIST`, `TOPIC`, `MODE`, `INVITE`, `KICK`; channel admission, privileges and visibility checks |
| Messages | `PRIVMSG`, `NOTICE`, `AWAY`; private/channel routing, moderated rooms and away replies |
| User queries | `WHO`, `WHOIS`, `WHOWAS`, `ISON`, `USERHOST`; visibility-filtered channel information and bounded in-memory nickname history |
| Server queries | `ADMIN`, `INFO`, `LUSERS`, `MOTD`, `TIME`, `VERSION`; responses come from current settings and state |
| Operators | `OPER`, `KILL`, `STATS`, `WALLOPS`; explicit account permissions are required |
| Negotiation | `CAP`, `AUTHENTICATE`; SASL PLAIN over a secure connection by default |
| Administrative extensions | `SAJOIN`, `SAPART`, `SANICK`, `SAMODE`; each checks its own configured operator permission |

`PASS` is accepted before registration; account sign-in uses SASL or IRCX AUTH. The default server permits anonymous users and requires no server password. Account names and IRC nicknames are independent. Successful account authentication does not implicitly grant operator privileges; use `OPER <account> <password>` to activate an enabled operator grant.

Operator extensions are:

```text
SAJOIN <nickname> <channel>
SAPART <nickname> <channel> [:reason]
SANICK <nickname> <new-nickname>
SAMODE <target> <modes> [mode-arguments]
```

`SAJOIN` bypasses room admission restrictions. `STATS u` returns uptime, `STATS l` returns live connection statistics in the standard 211 order (link name, queue size, sent messages, sent bytes, received messages, received bytes, connection age), and `STATS m` returns command counts. Statistics require `view_server`; unsupported selectors return only the end marker. `WALLOPS` requires `broadcast` and reaches users with user mode `+w`.

## Modes and compatibility

| Mode | Meaning |
| --- | --- |
| `+p`, `+s` | Private/secret rooms, hidden from nonmembers without management permission |
| `+m` | Only voiced members, hosts and owners can speak; authorized operators can override |
| `+n` | Disallow messages from outside the channel |
| `+t` | Restrict topic changes to hosts/owners or authorized operators |
| `+i` | Require an invitation; hosts/owners issue invitations |
| `+k <key>` | Require the securely hashed member key |
| `+l <count>` | Member limit; `-l` removes it |
| `+b <mask>` | Add an IRCX DENY entry; `-b <mask>` removes one; `+b` lists bans |
| `+o <nick>`, `+v <nick>` | Host and voice privileges |

IRCX channel modes are listed in [IRCX support](ircx-support.md). `005` advertises the supported channel-mode groups. User modes are `+i` (invisible to broad user searches), `+w` (receive WALLOPS), and server-granted `+o` (operator). A client cannot give itself `+o`; `-o` drops its active operator permissions.

Owners are a distinct channel role. Ordinary clients see both owners and hosts as `@`/`+o`; IRCX sessions see owners as `.`/`+q`. A host cannot kick or demote an owner. Removing an owner's owner role leaves that user as a host, so ordinary clients do not receive a misleading `-o` event.

OpenIRC intentionally hides private, secret and hidden rooms from outsiders across discovery, NAMES, WHO and WHOIS channel lists. This is stricter than implementations that expose an anonymous private-room listing. Auditorium rooms hide ordinary peers from one another; the same rule controls membership events and channel-message delivery.

## SASL

The only advertised capability is `sasl`; CAP 302 reports `sasl=PLAIN`. CAP negotiation suspends registration until `CAP END`. Example sequence, with `BASE64` replaced by the base64 encoding of `NUL + account + NUL + password`:

```text
CAP LS 302
CAP REQ :sasl
NICK ChatNickname
USER ircuser 0 * :Example user
AUTHENTICATE PLAIN
# server responds: AUTHENTICATE +
AUTHENTICATE BASE64
# server replies with 900 and 903 after successful authentication
CAP END
```

The comments above explain the exchange and are not commands. Account authentication requires TLS unless the administrator explicitly changes the policy. Authentication over the built-in Local transport is also accepted. Payloads use the [IRCv3 SASL framing rules](https://ircv3.net/specs/extensions/sasl-3.1): chunks contain at most 400 characters, exact multiples of 400 require a final `AUTHENTICATE +`, and `AUTHENTICATE *` aborts. Buffers are bounded. No SASL credentials or payloads are written to protocol logs.

## Limits and status

- Implemented: CRLF framing across fragmented/coalesced TCP input, strict UTF-8, 512-byte lines including CRLF, at most 15 parameters, bounded output queues, rate limits, TLS and host cloaking.
- Implemented: modern UTF-8 names by default; RFC1459 ASCII casemapping plus normalized Unicode casefolding. The current nickname limit is advertised through `NICKLEN`; room names are limited to 64 UTF-8 bytes and topics to 300 bytes.
- Implemented: NOTICE errors are suppressed, client-provided prefixes never establish sender identity, and membership-dependent output uses the core visibility policy.
- Bounded: global NAMES/LIST/WHO queries, nickname history, masks, client output and GUI event/log displays. Narrow a large query with channel names or LISTX filters.
- Planned: server federation, IRCv3 history, message tags, external SASL providers, WebSocket clients, and HTTP administration. These capabilities are not advertised.

Interoperability is tested with actual TCP protocol sessions and mixed ordinary/IRCX clients. No particular historical client or proprietary service is required.
