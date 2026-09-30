# IRCX support

OpenIRC implements the core client/server extensions described in the public [IRCX Internet-Draft, revision 04](https://datatracker.ietf.org/doc/html/draft-pfenning-irc-extensions-04). The draft is historical and expired. OpenIRC has no affiliation with its authors' employers or with another IRC server vendor.

## Feature status

| Feature | Status | Behavior |
| --- | --- | --- |
| `MODE ISIRCX`, `ISIRCX`, `IRCX` | Implemented | Discovery before registration, status query and per-session enablement |
| `CREATE` | Implemented | Create/join rooms, initial modes and owner role; `+c` requires a new room |
| `ACCESS` | Implemented | LIST, ADD, DELETE, CLEAR; channel/user/server/network targets; expiration |
| `PROP` | Implemented | Validated metadata, read/write permissions and secret reset fields |
| `LISTX` | Implemented | Name/member/age/language/registration/subject/topic filters; bounded output |
| `AUTH OPENIRC-PLAIN` | Implemented | Account authentication before registration; TLS required by default |
| Owner/Host/Voice | Implemented | Separate roles, owner protection and ordinary-client status translation |
| Auditorium | Implemented | Filtered membership/messages plus promotion/demotion visibility updates |
| ONJOIN/ONPART | Implemented | Private welcome/departure messages and escaped multiline text |
| Unicode compatibility | Implemented | Modern UTF-8 default; optional historical percent/escape names and hexadecimal nickname aliases |
| Cloning, `+d`/`+e` | Planned | Disabled GUI controls; modes not advertised |
| Service indication `+z`, SERVICEPATH | Planned | No invisible service controller is implemented or advertised |
| NTLM/DPA, EVENT, DATA/REQUEST/REPLY, PICS | Not implemented | No obsolete identity providers, service event feed, or external ratings service |

Settings can independently disable IRCX, CREATE, PROP, ACCESS, LISTX or auditorium changes. Except discovery and authentication, IRCX commands require a registered session with IRCX enabled.

## Discovery and authentication

```text
MODE ISIRCX
# response: 800 <nick-or-*> <state> 0 OPENIRC-PLAIN,ANON 512 *
IRCX
# response: 800 with state 1
```

`ANON` is included only when anonymous registration is allowed. `ISIRCX` queries the same state without enabling it. After registration, enabling IRCX also refreshes `005` so the client learns `PREFIX=(qov).@+`.

OpenIRC's named provider carries the same PLAIN credential tuple as SASL: base64 of `authzid NUL account NUL password`. Empty authzid is supported; a nonempty authzid must equal the account. Either send it immediately or request a challenge first:

```text
AUTH OPENIRC-PLAIN I :BASE64
# success: AUTH OPENIRC-PLAIN * <account> <user-oid>
```

```text
AUTH OPENIRC-PLAIN I
# server: AUTH OPENIRC-PLAIN S :+
AUTH OPENIRC-PLAIN S :BASE64
```

`AUTH OPENIRC-PLAIN *` aborts. Pending authentication suspends registration. Authentication must finish before registration; subsequent operator activation uses `OPER`. Each AUTH message must fit the 512-byte line limit; use chunked IRCv3 SASL for longer credential payloads. The built-in Local chat transport authenticates through the same account service and additionally requires an enabled operator grant.

IRCX and SASL numerics have separate internal enum namespaces. For example, IRCX error 903 means an invalid ACCESS level, while SASL 903 means successful authentication. Clients must interpret the number in the command's context.

## Channel modes and roles

| Mode | Behavior | Normal channel authority |
| --- | --- | --- |
| `+h` | Hidden from discovery by outsiders | Host |
| `+u` | Notify hosts/owners when a JOIN is denied | Host |
| `+f` | Advisory request that clients avoid text formatting | Explicit operator mode permission |
| `+w` | Reject WHISPER commands scoped to the room | Owner |
| `+x` | Auditorium visibility and message routing | Owner |
| `+r` | Registered channel configuration | Administration registration actions |
| `+a` | Require account authentication for ordinary admission | Host |
| `+q <nick>` | Channel owner membership | Owner |

Explicit server permissions can override normal channel authority. Owner/host access entries and valid owner/host keys grant elevated admission; the trusted force-join operation bypasses admission checks. `+f` is an indication only: OpenIRC does not rewrite messages or claim to control client rendering. `+z` is intentionally unavailable because no service controller exists.

Auditorium members see themselves and owners/hosts. Owners/hosts see everyone. A normal member's channel message reaches owners/hosts, while a host/owner message reaches everyone. Peer JOIN/PART/KICK and role transitions follow the same visibility policy. Unlike the draft's creation-only limitation, OpenIRC supports enabling/disabling auditorium on a live room and emits membership deltas so client lists stay consistent.

`WHISPER <channel> <nick[,nick...]> :text` requires sender and recipients to belong to the room. IRCX recipients receive WHISPER; ordinary recipients receive a private message. `+w` blocks this command, including attempts by owners; direct private messages outside the room context remain normal IRC messages.

## Properties and access

```text
PROP #room *
PROP #room TOPIC,SUBJECT
PROP #room ONJOIN :Welcome!\nPlease read the topic.
PROP #room ONPART :See you next time.
PROP #room MEMBERKEY :replacement-key
PROP #room MEMBERKEY :
```

| Properties | Behavior |
| --- | --- |
| OID, NAME, CREATION | Read-only dedicated identifier, canonical name and creation timestamp |
| ACCOUNT | Read-only founder account UUID; set ownership through administration |
| TOPIC | Topic text; PROP writes require host authority |
| SUBJECT, LANGUAGE, CLIENT | Search metadata, language tag and client metadata; owner writes |
| OWNERKEY, HOSTKEY | Owner-controlled admission credentials |
| MEMBERKEY | Host-controlled admission credential shared with channel `+k` |
| ONJOIN, ONPART | Owner-controlled private messages, up to 16 expanded lines |
| LAG | Administrator-controlled per-member message spacing, 0–2 seconds |
| CLIENTGUID | Administrator-controlled client-protocol UUID metadata |

OpenIRC uses stricter owner-write policy for welcome/part text and selected metadata than the historical draft. Text properties reject raw CR/LF/NUL, validate type and byte length, and persist with registered channel configuration. `\n`, `\r`, `\t`, `\b`, `\c`, and `\\` have bounded notice expansion; the GUI accepts multiline text directly.

Keys are Argon2id hashes. No command returns the plaintext or hash. Authorized property queries show `*` for a configured key or an empty value when absent; unauthorized queries are rejected or omitted from `PROP *`. Setting an empty key clears it. MODE replies and configuration exports do not disclose keys.

```text
ACCESS #room ADD HOST helper!*@* 60 :Temporary helper
ACCESS #room ADD DENY trouble!*@* 0 :Room restriction
ACCESS #room LIST
ACCESS #room DELETE DENY trouble!*@*
ACCESS #room CLEAR HOST
```

Timeouts are in minutes; zero means no expiry. Channel levels are DENY, GRANT, HOST, OWNER and VOICE. Positive matching access takes precedence over a matching DENY. Hosts cannot create OWNER entries or delete records protected by higher authority. Masks support `*`/`?`, nickname/user/host patterns, address/CIDR matches, and `$a:account` account matching.

`ACCESS <nickname>` controls messages received by that user, with DENY/GRANT levels. Authenticated users' rules persist against their account; anonymous rules last for their session. `$` targets the local server, `*` the network; in this version both apply to the one local server, with DENY/GRANT admission rules and server-ban authority required. Server/network and registered-channel rules persist; expired entries stop matching immediately.

ACCESS lists use replies 803, zero or more 804, then 805. Successful additions use 801 and deletions 802. PROP lists use 818 followed by 819. Mutations commit persistent records and their audit entry before publishing the new state.

## LISTX and names

```text
LISTX
LISTX #one,#two
LISTX N=#help*,>2,R=1 25
LISTX L=en*,S=*support*,T=*welcome*
LISTX C>60,T<10
```

Member filters are `<n`/`>n`; creation/topic-age filters are `C<n`, `C>n`, `T<n`, `T>n` in minutes. `N=`, `L=`, `S=`, and `T=` match names, languages, subjects and topics. `R=0`/`R=1` select unregistered/registered rooms. Terms combine with AND; a comma-separated channel list combines with OR. Escapes include `\b` for spaces, `\c` for commas, and `\*`/`\?` for literal wildcards.

LISTX emits 811, zero or more 812 entries, and exactly one completion: 817 when complete or 816 when truncated by a query/server/output bound. The entry contains name, parameterless modes, member count, limit and topic. Secret/private/hidden rooms are filtered before matching and counting. PICS records are not emitted.

Modern UTF-8 is the default: names remain UTF-8 for all clients. Historical mode encodes IRCX names with a `%` marker and draft backslash escapes; ordinary clients receive Unicode nicknames as `^` followed by uppercase hexadecimal UTF-8 bytes. Those aliases resolve back to the actual user for commands. Namespace reservation prevents a user from claiming a conflicting `^` alias. Channel-name conversion, nickname changes, NAMES, WHOIS and WHOWAS all use the same compatibility module. Tests cover mixed ordinary/IRCX sessions; compatibility with a particular legacy client is not promised.
