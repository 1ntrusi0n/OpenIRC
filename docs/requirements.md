# OpenIRC — Open-Source IRCX Server and Graphical Administration Console

You are working in a completely empty folder.

Your task is to create a complete, runnable, open-source IRC/IRCX chat server named **OpenIRC**, written in **Python 3.12+**, with a polished **PyQt6 desktop administration GUI**.

Do not produce a prototype consisting primarily of placeholders. Build a functional first version of the server.

OpenIRC must have **no MSN branding, no Microsoft branding, no OfficeIRC branding, and no copied proprietary artwork or source code**. IRCX is being implemented as an interoperable protocol. The product name throughout the application is:

**OpenIRC**

Use original OpenIRC branding and generic icons wherever needed.

The project should be suitable for eventual publication on GitHub under an open-source license.

---

# 1. Project objective

OpenIRC should provide:

1. A real TCP IRC server.
2. Backward compatibility with normal IRC clients.
3. Support for the major IRCX client/server extensions.
4. A persistent account and permissions system.
5. Persistent registered channels.
6. A desktop administration application built with PyQt6.
7. Live server monitoring.
8. Graphical management of users, channels, operators, bans, accounts, logs, and server settings.
9. TLS support.
10. A clean internal API so that a separate browser-based OpenIRC web chat can be created later.
11. A modular architecture suitable for eventual multi-server/network support.

The GUI should take visual inspiration from classic Windows server administration tools such as the historical OfficeIRC Remote Control utility:

https://www.officeirc.com/images/remotecontrol.gif

Do NOT duplicate it pixel-for-pixel.

The intended feel is:

- dedicated server-management application
- traditional Windows administration utility
- left navigation tree/sidebar
- central management workspace
- tables with live data
- property dialogs
- toolbar actions
- status bar
- obvious Start/Stop controls
- straightforward rather than flashy
- modernized enough to work well on Windows 10/11

Think of a blend of classic Windows MMC/server tools and a modern Qt application.

---

# 2. Protocol research

Before implementing protocol behavior, read these references:

IRCX overview:

https://en.wikipedia.org/wiki/IRCX

IRCX Internet-Draft:

https://datatracker.ietf.org/doc/html/draft-pfenning-irc-extensions-04

RFC 1459:

https://www.rfc-editor.org/rfc/rfc1459

Modern IRC protocol reference:

https://modern.ircdocs.horse/

OfficeIRC technical documentation may be used as an interoperability/reference aid:

https://www.officeirc.com/Manual/1

Do not blindly copy implementation-specific OfficeIRC extensions.

Implement documented IRC and IRCX behavior independently.

Where old IRCX documentation conflicts with normal modern IRC interoperability, preserve standard IRC interoperability and document the compatibility choice.

---

# 3. Important protocol philosophy

IRCX is an extension of IRC.

Therefore:

- normal IRC clients must be able to connect;
- IRCX clients should be able to discover and enable IRCX capabilities;
- IRCX-only commands should not break ordinary IRC sessions;
- protocol parsing and protocol state must be kept separate from GUI code;
- the server must never depend upon the GUI event loop to correctly process IRC traffic.

The GUI is an administration interface over the server core, not the server implementation itself.

---

# 4. Technology requirements

Use:

- Python 3.12+
- PyQt6
- asyncio for networking
- SQLite for persistent data
- sqlite3 or a lightweight async-friendly persistence wrapper
- Python logging
- ssl from the standard library
- dataclasses and type hints
- pathlib for paths
- pytest for automated tests

Avoid unnecessary heavyweight dependencies.

Create:

- `requirements.txt`
- `pyproject.toml`
- `.gitignore`
- `LICENSE`
- `README.md`

Use an OSI-compatible open-source license. MIT is acceptable.

---

# 5. Suggested repository structure

Create a clean package approximately like:

```text
OpenIRC/
    __init__.py
    __main__.py

    app.py

    core/
        server.py
        connection.py
        session.py
        dispatcher.py
        events.py
        state.py
        permissions.py
        rate_limit.py
        statistics.py

    protocol/
        parser.py
        message.py
        numerics.py
        irc_commands.py
        ircx_commands.py
        modes.py
        ircx_modes.py
        properties.py
        access.py
        unicode.py
        capabilities.py

    models/
        user.py
        account.py
        channel.py
        membership.py
        access_entry.py
        operator.py
        ban.py

    persistence/
        database.py
        migrations.py
        repositories.py

    security/
        passwords.py
        authentication.py
        tls.py
        masks.py

    admin/
        service.py
        commands.py

    gui/
        main_window.py
        navigation.py
        dashboard.py
        connections_page.py
        channels_page.py
        accounts_page.py
        operators_page.py
        bans_page.py
        logs_page.py
        settings_page.py
        statistics_page.py
        dialogs/
            channel_editor.py
            account_editor.py
            operator_editor.py
            ban_editor.py
            server_settings.py
            user_details.py
        widgets/
            status_card.py
            log_view.py
            sortable_table.py

    config/
        settings.py
        defaults.py

tests/
    test_parser.py
    test_registration.py
    test_channels.py
    test_modes.py
    test_access.py
    test_ircx.py
    test_permissions.py
    test_accounts.py
```

You may improve this structure when justified.

Do not put the entire project into one Python file.

---

# 6. Server startup

Running:

```bash
python -m OpenIRC
```

should launch the PyQt6 OpenIRC administration console.

The server should NOT automatically begin listening on first launch unless a setting enables automatic startup.

The GUI must have:

**Start Server**

and

**Stop Server**

controls.

When started, display:

- server state
- hostname
- bind address
- IRC port
- TLS port
- uptime
- connected users
- active channels
- registered accounts
- traffic in
- traffic out

The networking engine should run safely alongside Qt.

A recommended architecture is:

- PyQt GUI in main thread;
- asyncio network loop in a dedicated worker thread;
- Qt signals/slots or a thread-safe event bus between server core and GUI.

Do not modify Qt widgets from networking threads.

---

# 7. Default ports

Defaults:

```text
IRC: 6667
IRC TLS: 6697
Administration GUI: local desktop application
```

Make ports configurable.

Allow multiple listening addresses eventually, but at minimum support:

```text
0.0.0.0
127.0.0.1
specific IPv4 address
::
specific IPv6 address
```

Implement IPv4 and IPv6 when supported by the host OS.

---

# 8. IRC protocol parser

Build a proper IRC message parser.

Represent a message internally as something like:

```python
IRCMessage(
    prefix=None,
    command="PRIVMSG",
    params=["#general"],
    trailing="Hello world"
)
```

Correctly support:

- optional prefix
- command
- parameters
- trailing parameter
- CRLF framing
- partial TCP packets
- multiple messages in one TCP packet
- 512-byte traditional IRC line semantics where applicable
- UTF-8
- malformed input rejection

Never assume that one socket read equals one IRC command.

Add unit tests.

---

# 9. Basic client registration

Implement traditional registration using:

```text
PASS
NICK
USER
```

as applicable.

A client becomes fully registered after required registration information is present.

Send correct welcome numerics such as:

```text
001 RPL_WELCOME
002 RPL_YOURHOST
003 RPL_CREATED
004 RPL_MYINFO
005 RPL_ISUPPORT
```

Also provide:

```text
MOTD
LUSERS
VERSION
TIME
ADMIN
INFO
```

where practical.

Do not fake numeric responses that contradict actual server state.

---

# 10. Essential standard IRC commands

Implement functional versions of at least:

```text
ADMIN
AWAY
INVITE
ISON
JOIN
KICK
KILL
LIST
LUSERS
MODE
MOTD
NAMES
NICK
NOTICE
OPER
PART
PASS
PING
PONG
PRIVMSG
QUIT
STATS
TIME
TOPIC
USER
USERHOST
VERSION
WHO
WHOIS
WHOWAS
```

Also support:

```text
CAP
```

with a clean capability subsystem even if only a limited initial capability set is advertised.

Unknown commands must produce the correct error behavior rather than crash.

---

# 11. IRCX discovery

Implement:

```text
ISIRCX
IRCX
```

IRCX clients must be able to query whether IRCX is available.

When IRCX mode is enabled for a session, track that state on the client connection.

Implement IRCX reply numerics as defined by the draft where applicable.

Keep these constants in one clear module rather than scattering numeric literals throughout the code.

For example, IRCX numerics occupy ranges including responses around:

```text
800+
```

and errors around:

```text
900+
```

Consult the IRCX draft for exact meanings.

---

# 12. IRCX user privilege model

Implement IRCX's conceptual permission hierarchy:

```text
Sysop Manager
Sysop
Channel Owner
Channel Host
Channel Member
Chat User
```

Internally use explicit enums/permission objects rather than comparing strings.

Server/network privilege and channel privilege must be separate concepts.

For a channel membership, support:

```text
Owner
Host
Voice
Normal member
```

The permissions system should answer questions such as:

```python
can_kick(actor, target, channel)
can_change_topic(actor, channel)
can_set_mode(actor, channel, mode)
can_edit_access(actor, channel)
can_edit_property(actor, channel, property_name)
can_force_join(actor)
can_kill(actor, target)
```

Avoid duplicating permission logic in individual command handlers.

---

# 13. IRCX ACCESS command

Implement:

```text
ACCESS <object> LIST

ACCESS <object> ADD <level> <mask> [timeout] [:reason]

ACCESS <object> DELETE <level> <mask>

ACCESS <object> CLEAR [level]
```

Initially support these official IRCX levels:

```text
DENY
GRANT
HOST
OWNER
VOICE
```

The design may allow additional extensions later.

Object targets should support the concepts of:

- channel
- user
- local server
- network

where meaningful in a single-server implementation.

Access entries need:

```text
id
object
level
mask
created_by
created_at
expires_at
reason
```

Implement wildcard masks.

Persist permanent access entries.

Temporary access entries must expire correctly.

The GUI needs an access-list editor for every registered channel.

---

# 14. CREATE

Implement IRCX:

```text
CREATE <channel> [modes [modeargs]]
```

The operation should create a channel and join its creator.

The creator should normally become the initial channel owner.

Respect appropriate initial modes.

Generate an internal object ID for the channel.

Return IRCX `CREATE` notification/reply behavior where appropriate.

---

# 15. IRCX channel properties

Implement the `PROP` system.

At minimum support:

```text
OID
NAME
CREATION
LANGUAGE
OWNERKEY
HOSTKEY
MEMBERKEY
TOPIC
SUBJECT
CLIENT
ONJOIN
ONPART
LAG
ACCOUNT
CLIENTGUID
```

If some obscure draft property is intentionally deferred, clearly document it.

Support:

```text
PROP #channel *
PROP #channel PROPERTY
PROP #channel PROPERTY1,PROPERTY2
PROP #channel PROPERTY :value
PROP #channel PROPERTY :
```

Read-only properties must actually be read-only.

Sensitive key properties must never be leaked to unauthorized users.

`OWNERKEY`, `HOSTKEY`, and `MEMBERKEY` should be stored securely enough that they are not exposed in logs or normal GUI tables.

Channel owners and hosts should be able to manipulate appropriate properties according to IRCX permissions.

---

# 16. ONJOIN and ONPART

Implement these particularly well.

If a channel has:

```text
ONJOIN
```

send that text only to the joining user after joining.

If it has:

```text
ONPART
```

send that text only to the departing user.

Support escaped/newline content where appropriate.

Expose easy editors for both values in the GUI.

---

# 17. LISTX

Implement IRCX:

```text
LISTX
```

including filtering as reasonably defined by the IRCX specification.

Return extended channel information such as:

- channel
- modes
- user count
- user/member limit
- topic

and other compatible information.

Implement the official IRCX LISTX reply sequence, including:

```text
LISTX start
LISTX entry
LISTX truncation when necessary
LISTX end
```

Protect the server from huge unbounded LISTX responses.

---

# 18. IRCX Unicode support

Use UTF-8 internally everywhere.

Python strings should remain Unicode internally.

Be aware that the historical IRCX draft defined special transformations for Unicode object names and fallback hexadecimal nicknames for non-IRCX clients.

Implement the compatibility layer in a dedicated module.

Do NOT scatter Unicode conversion hacks throughout command handlers.

Modern normal IRC clients are generally UTF-8 capable, so allow an administrator setting:

```text
Unicode compatibility mode:
    Modern UTF-8
    Strict historical IRCX compatibility
```

Default to Modern UTF-8.

Document the difference.

---

# 19. Channel modes

Support ordinary IRC modes including at minimum:

```text
+p private
+s secret
+m moderated
+n no external messages
+t topic restricted
+i invite only
+k key
+l user limit
```

Implement IRCX modes where feasible:

```text
+h hidden
+u knock
+f no-format indication
+w no-whisper
+x auditorium
+r registered
+z service indication
+a authenticated-users-only
+d cloneable
+e clone
```

Do not confuse channel modes with user modes.

Some letters may conflict with mode conventions in other IRC server families. OpenIRC should follow its documented IRCX compatibility mode and clearly advertise supported modes through `005`/ISUPPORT.

Build mode behavior centrally.

---

# 20. Auditorium mode

Implement IRCX auditorium mode carefully.

When auditorium mode is enabled:

Normal members:

- can see themselves;
- can see owners;
- can see hosts;
- should not see ordinary peer members;
- should not receive peer JOIN/PART events;
- messages from normal members should reach owners/hosts as required by IRCX semantics.

Owners/hosts:

- can see normal members;
- receive their messages;
- retain moderation control.

Write specific automated tests for this mode.

---

# 21. Channel ownership

OpenIRC must make IRCX ownership a first-class feature.

Use distinct membership states for:

```text
Owner
Host
Voice
Member
```

Do not implement Owner merely as an alias for ordinary `+o`.

Expose appropriate prefixes to IRCX clients.

Provide sensible standard-IRC degradation for non-IRCX clients.

Channel owners should have greater authority than channel hosts.

A host must not be able to strip the channel's legitimate owner of owner rights unless server-level policy explicitly permits it.

---

# 22. Persistent registered channels

Support registered channels.

A registered channel should survive server restarts as configuration even if nobody is currently inside it.

Persist:

```text
name
created
founder/account owner
topic
subject
language
modes
member limit
properties
access list
welcome text
part text
registration state
```

The GUI should distinguish:

```text
Active channels
Registered channels
Temporary channels
```

Provide actions:

```text
Create
Register
Unregister
Edit
Delete
Force close
Join as administrator
```

---

# 23. Accounts

Create a real account system independent of nicknames.

Persist:

```text
username
display name
password hash
email optional
created date
last login
enabled/disabled
locked state
notes
role
```

Never store passwords in plaintext.

Use a modern password hashing approach available in a reasonable Python dependency, preferably Argon2id or bcrypt.

If avoiding an additional dependency, use a strong standard-library derivation such as `hashlib.scrypt` with unique salts.

Create clear helper APIs:

```python
create_account()
authenticate_account()
change_password()
disable_account()
delete_account()
lock_account()
unlock_account()
```

---

# 24. Authentication

Support anonymous IRC users unless disabled by configuration.

Also support authenticated OpenIRC accounts.

IRCX `AUTH` should be architected according to IRCX semantics.

Do NOT attempt to recreate obsolete NTLM behavior as the primary authentication mechanism.

Implement a safe OpenIRC authentication provider.

It is acceptable for v1 to support:

```text
AUTH PLAIN-style OpenIRC mechanism
```

or an IRCX-compatible named mechanism documented by OpenIRC.

Also support modern IRC CAP/SASL in a modular way if practical.

Never log credentials.

TLS should be strongly recommended when passwords are transmitted.

---

# 25. OPER and server operators

Implement server operator accounts.

At minimum provide:

```text
Administrator / Sysop Manager
Sysop
Moderator
```

These OpenIRC GUI roles can internally map to explicit permissions.

Do not hardcode everything around a single administrator boolean.

Permissions can include:

```text
view server
change server settings
start/stop server
manage accounts
manage operators
manage channels
manage access lists
kick users
kill connections
force join
force part
change nick
set channel modes
view IP addresses
view logs
manage bans
broadcast messages
shutdown server
```

Use role templates plus explicit permissions if reasonable.

---

# 26. Administrative IRC commands

Provide conventional operator functionality including:

```text
OPER
KILL
WALLOPS
```

You may add OpenIRC administration commands such as:

```text
SAJOIN
SAPART
SANICK
SAMODE
```

as optional documented extensions.

Keep proprietary/nonstandard extensions clearly identified as OpenIRC features.

---

# 27. Message routing

Correctly route:

```text
PRIVMSG user
PRIVMSG channel
NOTICE user
NOTICE channel
```

Implement appropriate permission/mode checks.

Do not echo messages incorrectly.

Handle disconnected users safely.

One faulty client must never crash the server.

---

# 28. WHO / WHOIS / NAMES / LIST

These must reflect actual state.

WHOIS should expose appropriate information such as:

```text
nickname
username
hostname/IP according to privacy policy
real name
server
channels according to visibility
operator state
away state
account status
idle time
connection time
```

Respect hidden/private/secret/auditorium visibility.

---

# 29. Privacy

Add configurable IP-display policy.

Options:

```text
Full host/IP
Cloaked host
Hide IP from ordinary users
```

Operators with explicit permission may view real network information in the GUI.

Do not send raw IP addresses unnecessarily to ordinary clients.

---

# 30. Rate limiting and abuse protection

Create basic anti-abuse controls:

- commands per second
- messages per second
- connection rate per IP
- maximum simultaneous sessions per IP
- maximum channels per client
- nickname change rate
- repeated failed authentication protection
- configurable flood penalty
- temporary automatic throttling

Make limits configurable.

Avoid blocking the asyncio event loop.

Log enforcement actions.

---

# 31. Ban system

Support bans against:

```text
nickname masks
user masks
host masks
IP addresses
CIDR ranges
accounts
```

At server level provide concepts similar to:

```text
temporary ban
permanent ban
disconnect
deny reconnect
```

Channel bans remain channel-specific.

Persist server bans.

GUI columns:

```text
Type
Mask
Reason
Created By
Created
Expires
Active
```

Actions:

```text
Add
Edit
Remove
Disable
Disconnect matching users
```

---

# 32. Server MOTD

Provide a multiline MOTD editor in the GUI.

Persist it.

MOTD changes should take effect without restarting.

---

# 33. Logging

Implement structured logging.

Separate categories:

```text
Server
Connections
Authentication
IRC Commands
IRCX Commands
Channels
Moderation
Security
Errors
Debug
```

Allow GUI filtering.

Log useful events such as:

```text
server started
server stopped
connection accepted
connection rejected
user registered
user authenticated
user disconnected
channel created
channel destroyed
channel registered
kick
kill
ban added
ban removed
operator login
configuration change
```

Do not log account passwords, channel owner keys, host keys, member keys, SASL payloads, or other secrets.

Use rotating log files.

---

# 34. Database

Use SQLite.

Recommended tables include:

```text
settings
accounts
operators
registered_channels
channel_properties
channel_access
server_bans
nick_reservations
audit_log
```

Use migrations.

Create the database automatically on first run.

Do not delete or recreate the database merely because the schema version changes.

Implement migration version tracking.

---

# 35. Live in-memory state

Transient state should generally live in memory:

```text
connections
sessions
current nicknames
temporary channels
active memberships
invites
temporary access entries
rate limit state
WHO/WHOWAS transient information
statistics
```

Persistent configuration belongs in SQLite.

Keep that distinction clean.

---

# 36. PyQt6 GUI layout

Create a polished main window.

Suggested layout:

```text
┌───────────────────────────────────────────────────────────────┐
│ File  Server  Users  Channels  Tools  Help                  │
├───────────────────────────────────────────────────────────────┤
│ [Start] [Stop] [Refresh] [Broadcast] [Settings]             │
├──────────────────┬────────────────────────────────────────────┤
│ OpenIRC Server     │                                            │
│                  │              Workspace                     │
│ ▾ Server         │                                            │
│   Overview       │                                            │
│   Connections    │                                            │
│   Statistics     │                                            │
│                  │                                            │
│ ▾ Chat           │                                            │
│   Channels       │                                            │
│   Users          │                                            │
│   Accounts       │                                            │
│                  │                                            │
│ ▾ Security       │                                            │
│   Operators      │                                            │
│   Access/Bans    │                                            │
│                  │                                            │
│ ▾ System         │                                            │
│   Logs           │                                            │
│   Settings       │                                            │
├──────────────────┴────────────────────────────────────────────┤
│ ● Running | Users: 24 | Channels: 7 | Uptime: 02:15:42      │
└───────────────────────────────────────────────────────────────┘
```

Use a navigation tree or QListWidget/QTreeWidget on the left and a QStackedWidget for pages.

The application should be resizable.

Remember window geometry between launches.

Use native-looking controls.

Avoid giant colorful web-dashboard styling.

---

# 37. Dashboard

The Overview page should immediately answer:

```text
Is OpenIRC running?
Where is it listening?
How many users are connected?
How many channels exist?
Are there recent errors?
```

Show status cards for:

```text
Server Status
Users
Channels
Connections
Uptime
Inbound Traffic
Outbound Traffic
```

Below these, show:

**Recent Activity**

with timestamped events.

Include a Start/Stop server button.

---

# 38. Connections page

Show a live sortable table:

```text
Nickname
Account
Username
Host/IP
Connected
Idle
Channels
IRCX
TLS
Operator
Bytes In
Bytes Out
```

Context-menu actions:

```text
View Details
Send Notice
Kill Connection
Force Join Channel
Force Part Channel
Change Nick
Ban
Copy Host/IP
```

Actions must check admin permissions.

Double-click should open a detail window.

---

# 39. User details dialog

Display:

```text
Nickname
Account
Username
Real name
Remote IP
Displayed host
Connected at
Idle time
TLS
IRCX enabled
Operator role
User modes
Channels
Traffic totals
```

Include an optional live protocol/debug view only if debug mode is enabled.

Do not expose private credentials.

---

# 40. Channels page

Table:

```text
Channel
Users
Owners
Hosts
Voiced
Modes
Topic
Registered
Created
```

Search/filter box.

Buttons:

```text
Create Channel
Edit
Register
Close
Delete Registration
Refresh
```

Context menu:

```text
View Members
Channel Properties
Access List
Set Topic
Set Modes
Broadcast to Channel
Force Close
```

---

# 41. Channel editor

Build a good multi-tab dialog.

Tabs:

```text
General
Properties
Modes
Members
Access
Security
```

General:

```text
Name
Topic
Subject
Language
Created
Registered
Founder
User limit
```

Properties:

```text
ONJOIN
ONPART
CLIENT
CLIENTGUID
LAG
```

Security:

```text
Member key
Host key
Owner key
Authenticated users only
```

Never display stored key hashes as though they were plaintext keys.

Allow replacement/reset of keys instead.

Modes should use friendly checkboxes with mode letters beside them.

Example:

```text
[x] Moderated (+m)
[x] No outside messages (+n)
[x] Topic restricted (+t)
[ ] Invite only (+i)
[ ] Hidden (+h)
[ ] Knock (+u)
[ ] No whispers (+w)
[ ] Auditorium (+x)
[ ] Authenticated users only (+a)
```

---

# 42. Channel member management

Within channel details, show:

```text
Nickname
Account
Role
Joined
Idle
Host
```

Role should visually distinguish:

```text
Owner
Host
Voice
Member
```

Provide actions:

```text
Make Owner
Remove Owner
Make Host
Remove Host
Give Voice
Remove Voice
Kick
Ban
View User
```

Require appropriate permission confirmation for destructive actions.

---

# 43. Accounts page

Columns:

```text
Username
Display Name
Role
Enabled
Locked
Created
Last Login
```

Actions:

```text
Create Account
Edit
Reset Password
Disable
Enable
Lock
Unlock
Delete
```

Password reset should require entering a new password.

Do not ever display the existing password.

---

# 44. Operators page

Manage server administrative users separately from ordinary online sessions.

Display:

```text
Account
Role
Permissions
Enabled
Last Login
```

Provide a role/permission editor.

---

# 45. Bans/access page

Use tabs:

```text
Server Bans
Channel Access
```

For channel access, provide channel selector plus table:

```text
Level
Mask
Reason
Added By
Created
Expires
```

Support:

```text
OWNER
HOST
VOICE
GRANT
DENY
```

---

# 46. Logs page

Provide:

- live log table
- severity filter
- category filter
- text search
- pause auto-scroll
- clear display without deleting log files
- export visible log
- open log folder

Columns:

```text
Time
Level
Category
Message
```

Double-click may show full details.

---

# 47. Settings page

Organize settings into sections.

## Identity

```text
Server name
Network name
Description
Administrator name
Administrator email
```

## Listening

```text
Bind address
IRC port
Enable IPv6
Enable TLS
TLS port
Certificate
Private key
```

## Users

```text
Allow anonymous users
Maximum users
Max connections per IP
Nickname length
Maximum channels per user
```

## Channels

```text
Maximum channels
Default modes
Auto-register channels
Destroy empty temporary channels
```

## Security

```text
Hide user IPs
Rate limiting
Failed login threshold
Temporary lockout
Require TLS for account auth
```

## IRCX

```text
Enable IRCX
Unicode compatibility mode
Enable LISTX
Enable ACCESS
Enable PROP
Enable CREATE
Enable auditorium mode
Enable cloneable channels
```

## Logging

```text
Log level
Log directory
Maximum file size
Retention
```

## Startup

```text
Start server when OpenIRC launches
Minimize to tray
Confirm server shutdown
```

Validate all input.

Port fields must be numeric and in valid range.

---

# 48. First-run wizard

If no configuration/database exists, display a short first-run wizard.

Ask for:

```text
Server name
Network name
Bind address
IRC port
TLS preference
First administrator username
Administrator password
```

Do NOT require internet connectivity.

After completion, open the main console.

---

# 49. System tray

Implement optional system tray support.

Tray menu:

```text
Open OpenIRC
Start Server
Stop Server
Status
Exit
```

Display status icon changes for:

```text
running
stopped
error
```

Closing the window may minimize to tray according to settings.

Exiting must cleanly shut down networking and database resources.

---

# 50. Server console/broadcast

Include an administrative broadcast dialog capable of sending:

```text
NOTICE to all users
NOTICE to a channel
WALLOPS to operators
```

Require explicit target selection.

Log broadcasts.

---

# 51. Event architecture

Create an internal event bus.

Events can include:

```text
ServerStarted
ServerStopped
ClientConnected
ClientRegistered
ClientAuthenticated
ClientDisconnected
NicknameChanged
ChannelCreated
ChannelDestroyed
UserJoinedChannel
UserPartedChannel
MessageSent
UserKicked
UserKilled
BanAdded
BanRemoved
AccessChanged
ChannelPropertyChanged
OperatorAuthenticated
ServerError
StatisticsUpdated
```

The GUI subscribes to these events.

Do not make core networking import PyQt widgets.

---

# 52. Statistics

Track at minimum:

```text
current connections
peak connections
total connections
current channels
peak channels
messages routed
commands processed
bytes received
bytes sent
auth successes
auth failures
kicks
kills
server start time
```

Dashboard updates should be throttled to avoid unnecessary GUI churn.

---

# 53. Thread safety

Treat thread safety seriously.

Networking state should have clear ownership.

Do not directly hand mutable connection dictionaries to GUI widgets.

Expose snapshots/data-transfer objects or thread-safe service methods.

Any action initiated by the GUI such as:

```text
kick user
change channel topic
stop server
ban connection
```

must be marshalled safely onto the server's asyncio loop.

---

# 54. Error handling

The program must remain running when:

- one client sends malformed data;
- a client disconnects suddenly;
- an invalid command is sent;
- a SQLite operation fails temporarily;
- TLS configuration is invalid;
- a GUI action targets a client that disconnected moments earlier.

Show actionable errors in the GUI.

Log stack traces for internal errors.

Avoid giant blanket `except Exception: pass` blocks.

---

# 55. Configuration persistence

Settings should survive restart.

Use SQLite or a clearly structured config file for application settings.

Never write configuration on every GUI repaint/update.

Use explicit save/apply operations.

Settings requiring server restart should say so.

---

# 56. Nickname handling

Enforce uniqueness case-insensitively according to an IRC-compatible casemapping.

Advertise the casemapping via ISUPPORT.

Support configurable maximum nickname length.

Reject invalid nicknames cleanly.

Maintain WHOWAS history for recently changed/disconnected nicknames.

---

# 57. Channel-name handling

Support:

```text
#global
&local
```

at minimum.

IRCX extended Unicode channel naming may be implemented through the dedicated IRCX Unicode compatibility layer.

Reject malformed names.

Maintain canonical case-insensitive lookup while preserving display case where appropriate.

---

# 58. Channel lifecycle

Temporary channel:

- created when first user creates/joins it;
- exists while occupied;
- disappears when empty unless configuration says otherwise.

Registered channel:

- metadata persists;
- can become inactive when empty;
- reactivates when joined;
- retains properties, access entries, founder and modes.

Ensure this distinction is visible in the GUI.

---

# 59. Invitations

Implement normal INVITE behavior.

Invite-only channels should honor invitations and privileged bypass rules.

IRCX knock mode should notify appropriate owners/hosts when entry is denied where specified.

---

# 60. Cloneable channels

Treat IRCX cloneable channels as a later-but-real feature, not as a fake checkbox.

If implementing in v1:

Given parent:

```text
#support
```

with cloneable mode and user limit reached, create:

```text
#support1
#support2
...
```

as required.

Clone children inherit appropriate parent properties and modes.

Keep parent/clone relationships internally.

If this feature would jeopardize stability of the first build, implement the data model, UI flag, and tests for mode validation, clearly mark automatic clone creation as Phase 2 in README, and do not pretend it already works.

---

# 61. TLS

Support TLS listeners.

Allow PEM certificate/key selection in GUI.

Gracefully explain invalid/missing certs.

Show TLS status in dashboard.

Never silently fall back to plaintext on a configured TLS-only port.

---

# 62. Future web client preparation

A separate web chat will be built later.

Do NOT build it now.

However, keep OpenIRC ready for it.

Design a service boundary so future components can inspect:

```text
public channels
channel properties
authentication
account profile
server capabilities
```

Do not expose the database directly as the intended web interface.

A future HTTP/WebSocket gateway should be able to call server services cleanly.

---

# 63. Future server linking

Do NOT spend the initial build implementing a full distributed IRC network.

However:

- do not bake assumptions everywhere that there can only ever be one server;
- give users globally usable IDs internally;
- give channels stable IDs;
- separate local server identity from network identity;
- leave a `server_link` or federation package placeholder only if it contains useful interfaces/documentation.

The single-server implementation must be complete before server linking is attempted.

---

# 64. Security requirements

Treat all network clients as hostile input.

Protect against:

- oversized lines
- endless partial messages
- invalid UTF-8
- command floods
- nickname floods
- repeated auth attempts
- malformed mode strings
- wildcard abuse
- SQL injection
- path traversal in GUI-selected paths where relevant
- accidental plaintext secret logging

Always parameterize SQL.

Limit buffers.

Disconnect abusive malformed sessions where appropriate.

---

# 65. GUI quality

This project is not finished if the server works but the administration GUI looks like a developer test harness.

Pay attention to:

- sensible spacing
- consistent margins
- aligned forms
- toolbar icons
- clear headers
- readable table column widths
- sortable tables
- right-click context menus
- keyboard shortcuts where useful
- confirmation dialogs for dangerous actions
- disabled controls when server is stopped
- disabled actions when no selection exists
- persistent window size
- splitter positions
- user-friendly empty states

Use Qt's standard palette by default so it works in light and dark OS themes.

Avoid a custom stylesheet unless necessary.

---

# 66. Main menus

Implement approximately:

```text
File
    Export Configuration
    Exit

Server
    Start
    Stop
    Restart
    Broadcast
    Settings

Users
    Accounts
    Connections
    Operators
    Bans

Channels
    Create
    Registered Channels
    Active Channels

Tools
    View Logs
    Statistics
    Generate TLS Certificate Instructions

Help
    OpenIRC Documentation
    Protocol Information
    About OpenIRC
```

Only include actions that work.

---

# 67. About dialog

Display:

```text
OpenIRC
Open-source IRC/IRCX Server

Version x.y.z
```

Mention that IRC is an Internet protocol and IRCX interoperability is based on publicly documented protocol specifications.

Do not imply affiliation with Microsoft, MSN, OfficeIRC, or another IRC server vendor.

---

# 68. Initial version number

Start at:

```text
OpenIRC 0.1.0
```

Keep version information in exactly one source-of-truth module.

---

# 69. README

Write a useful README containing:

```text
What OpenIRC is
Features
Current compatibility
Installation
Running the server
Default ports
Creating the first admin
Connecting with an IRC client
IRCX support
TLS setup
Project structure
Testing
Security notes
Roadmap
License
```

Explicitly distinguish:

```text
Implemented
Partially implemented
Planned
```

Do not claim support for unimplemented features.

---

# 70. Protocol documentation

Create:

```text
docs/
    architecture.md
    irc-support.md
    ircx-support.md
    permissions.md
    database.md
```

`ircx-support.md` should include a table such as:

```text
Feature                  Status
---------------------------------------------
ISIRCX                    Implemented
IRCX                      Implemented
ACCESS                    Implemented
CREATE                    Implemented
PROP                      Implemented
LISTX                     Implemented
AUTH                      Partial/Implemented
Unicode extension         ...
Auditorium                ...
Cloneable rooms           ...
```

Include interoperability notes.

---

# 71. Tests

Automated testing is required.

Test at least:

### Parser

- normal command
- trailing argument
- malformed line
- split TCP-style buffer
- multiple commands
- Unicode

### Registration

- NICK + USER
- duplicate nick
- bad nick
- QUIT before registration

### Channels

- JOIN
- PART
- topic
- invite-only
- moderated
- key
- limit
- kick

### Permissions

- member cannot perform host actions
- host cannot improperly demote owner
- owner permissions
- sysop override

### IRCX

- ISIRCX
- IRCX enablement
- CREATE
- PROP read/write
- property permissions
- ACCESS ADD
- ACCESS DELETE
- ACCESS LIST
- OWNER/HOST/VOICE application
- LISTX
- auditorium visibility
- auth-only mode
- ONJOIN
- ONPART

### Persistence

- registered channel survives reopening DB
- account survives
- ban survives
- access list survives

---

# 72. Integration smoke test

Provide a simple automated or manual integration test that:

1. Starts OpenIRC on localhost using a temporary database.
2. Opens two TCP client connections.
3. Registers two users.
4. Creates `#test`.
5. Joins both users.
6. Sends a message.
7. Verifies the other user receives it.
8. Promotes one user to host.
9. Tests a protected action.
10. Stops cleanly.

---

# 73. Developer mode

Support:

```bash
python -m OpenIRC --debug
```

Debug mode may enable:

- more verbose logs
- protocol send/receive traces
- extra exception information

Never print plaintext credentials even in debug mode.

---

# 74. Headless server mode

Although the main product is a GUI server manager, support:

```bash
python -m OpenIRC --headless
```

This runs OpenIRC without Qt.

Use the same core server and database.

This is important for Linux servers and future Docker deployment.

Also support:

```bash
python -m OpenIRC --config ...
```

if useful.

Do not maintain two different server implementations for GUI and headless modes.

---

# 75. Graceful shutdown

When stopping:

1. stop accepting connections;
2. optionally send a server shutdown ERROR/NOTICE;
3. close client sessions;
4. flush persistent work;
5. close listeners;
6. stop worker tasks;
7. update server state;
8. return GUI to Stopped state.

Do not terminate the Python process as the mechanism for stopping the IRC service.

---

# 76. GUI/server separation

A major requirement:

The following should work without PyQt imports:

```python
from OpenIRC.core.server import OpenIRCServer
```

The core protocol server must be independently usable.

The GUI talks to it through a service/controller layer.

This matters because later OpenIRC may gain:

- Linux daemon operation
- web administration
- REST API
- Docker deployment
- automated testing
- server linking

---

# 77. Administrative audit trail

Record important administrator changes in an audit table:

```text
timestamp
actor
action
object type
object
summary
```

Examples:

```text
admin registered #general
admin promoted Alice to channel owner
admin banned 192.0.2.4
admin changed server port
admin stopped server
```

Do not include passwords or secret keys.

Expose audit information from the GUI.

---

# 78. Nickname reservations

Implement a simple reservation system.

Registered accounts may optionally reserve nicknames.

Admin GUI:

```text
Nickname
Account
Created
Protected
```

Behavior can initially be configurable:

```text
Off
Warn
Require matching account
```

Do not automatically assume account username equals nickname.

---

# 79. Friendly property abstraction

Internally do not represent channel properties as an arbitrary unvalidated dictionary only.

Define metadata for properties, for example:

```python
PropertyDefinition(
    name="TOPIC",
    max_length=160,
    readable_by=...,
    writable_by=...,
    secret=False,
    type=str,
)
```

This should centralize:

- validation
- permissions
- maximum length
- visibility
- persistence behavior

Do the same conceptually for channel modes.

---

# 80. IRC numerics

Create enums/constants for numerics.

Examples:

```python
RPL_WELCOME = 1
ERR_NOSUCHNICK = 401
ERR_NOSUCHCHANNEL = 403
...
```

plus IRCX numerics.

Create formatting helpers.

Avoid:

```python
send("482 ...")
```

throughout command handlers.

Prefer:

```python
session.send_numeric(ERR_CHANOPRIVSNEEDED, ...)
```

---

# 81. Command dispatcher

Command handlers should be modular.

For example:

```python
@irc_command("JOIN", requires_registration=True)
async def handle_join(ctx):
    ...
```

or an equivalent dispatcher architecture.

Each command should specify:

- command name
- registration requirement
- minimum parameters
- IRCX requirement if applicable
- operator requirement if applicable

This will make later extension easier.

---

# 82. Data model quality

Use dataclasses or clearly defined domain classes.

Good examples:

```text
ClientSession
UserIdentity
Account
Channel
ChannelMembership
AccessEntry
BanEntry
ServerStatistics
```

Avoid passing giant dictionaries everywhere.

Use IDs for persistent objects.

---

# 83. Object identifiers

IRCX defines object IDs.

Give active/persistent OpenIRC objects unique stable identifiers where appropriate.

Channel OIDs should not leak internal SQLite row assumptions.

Generate a dedicated safe object identifier.

The GUI can display an OID under Advanced details.

---

# 84. Compatibility behavior

For ordinary IRC clients:

- standard channel behavior should remain sensible;
- standard `JOIN`, `MODE`, `TOPIC`, etc. should work;
- IRCX ownership should gracefully map into visible IRC status;
- IRCX-only metadata should not produce unsolicited garbage;
- IRCX Unicode fallback should only be used when applicable.

Document any intentional differences from the historical draft.

---

# 85. Do not implement obsolete concepts recklessly

The historical draft contains obsolete concepts such as PICS ratings and OS-integrated authentication assumptions.

Keep protocol parsing compatible where practical, but do not make obsolete external systems central dependencies.

For something like PICS:

- optionally store the value;
- treat it as legacy metadata;
- do not implement an external PICS service.

For an implementation-dependent ACCOUNT property:

- clearly document OpenIRC's behavior.

---

# 86. No proprietary dependency

OpenIRC must not require:

- OfficeIRC
- MSN Chat
- Microsoft Exchange
- IIS
- Windows authentication
- a proprietary IRC library

The Python server must work independently.

Windows is the primary GUI target initially, but the project should remain portable.

---

# 87. Visual identity

Use a simple original OpenIRC logo/icon if possible using programmatically drawn/basic geometric Qt resources.

Suggested concept:

```text
speech bubble + "24"
```

or

```text
network nodes + # symbol
```

Do not download/copy another company's logo.

The project must still run if optional icon resources fail.

---

# 88. Example target user experience

Administrator launches OpenIRC.

They see:

```text
OpenIRC Server
Status: Stopped
```

They click **Start Server**.

Status becomes:

```text
Running
irc.example.net
0.0.0.0:6667
TLS: :6697
```

A user connects through an IRC client.

Within a moment the Connections table updates.

The administrator opens Channels and creates:

```text
#Lobby
```

They set:

```text
Registered
Topic: Welcome to OpenIRC
Language: en-US
ONJOIN: Welcome to #Lobby!
+m
+n
+t
```

They add:

```text
OWNER Alice!*@*
HOST Bob!*@*
DENY trouble!*@*
```

Users connect and the room behaves accordingly.

The administrator can inspect, moderate and configure everything without manually typing IRC commands.

This is the intended product experience.

---

# 89. What counts as completion

Do not stop after generating skeleton files.

The initial delivery is complete only when all of the following are true:

1. The project installs.
2. `python -m OpenIRC` opens the GUI.
3. The GUI can start the IRC server.
4. A normal IRC client can connect.
5. Two users can join a room and chat.
6. Channels appear live in the GUI.
7. Users appear live in the GUI.
8. GUI kick/kill functionality works.
9. Accounts persist.
10. Registered channels persist.
11. IRCX can be detected/enabled.
12. `ACCESS` works.
13. `CREATE` works.
14. `PROP` works.
15. `LISTX` works at a useful baseline.
16. Owner/Host/Voice roles work.
17. ONJOIN/ONPART work.
18. Major IRC channel modes work.
19. Logs work.
20. Settings persist.
21. Server can stop and restart without closing the GUI.
22. Tests pass.
23. README accurately describes the implementation.

---

# 90. Implementation sequence

Work in this order.

## Phase 1 — foundation

Create:

- project structure
- configuration
- logging
- domain models
- SQLite database/migrations
- IRC parser

Then run parser tests.

## Phase 2 — IRC core

Implement:

- socket server
- client session
- registration
- NICK/USER
- PING/PONG
- JOIN/PART
- PRIVMSG/NOTICE
- QUIT
- NAMES/LIST
- TOPIC
- MODE
- WHOIS

Test using raw TCP sockets.

## Phase 3 — permissions/persistence

Implement:

- accounts
- authentication
- operator accounts
- registered channels
- bans
- access checking

## Phase 4 — IRCX

Implement:

- ISIRCX
- IRCX
- CREATE
- ACCESS
- PROP
- LISTX
- owner/host/voice
- IRCX modes
- IRCX numerics
- Unicode compatibility

## Phase 5 — GUI

Implement:

- main window
- navigation
- dashboard
- connections
- channels
- accounts
- operators
- bans/access
- logs
- settings

Connect it to real server state.

## Phase 6 — polish

Implement:

- system tray
- first-run wizard
- confirmations
- persistent GUI state
- TLS configuration
- graceful errors
- documentation

## Phase 7 — verification

Run:

```bash
pytest
```

Then run the integration smoke test.

Then manually start the GUI and verify no traceback appears.

---

# 91. Coding requirements

Use:

- type hints
- meaningful class names
- docstrings where useful
- small cohesive methods
- async/await correctly
- explicit permissions
- centralized numerics
- centralized modes
- centralized properties

Avoid:

- thousand-line god classes
- global mutable state
- UI code inside networking code
- blocking socket calls
- blocking database work on hot networking paths
- arbitrary sleeps used for synchronization
- duplicated permissions
- plaintext passwords
- catching all errors silently
- fake GUI data

---

# 92. Final verification before you finish

Before declaring completion:

1. Inspect every generated Python file for syntax errors.
2. Run `python -m compileall OpenIRC`.
3. Run all tests.
4. Launch the GUI.
5. Start the server.
6. Connect test clients.
7. Verify IRC messaging.
8. Verify IRCX commands.
9. Restart OpenIRC.
10. Verify persistent accounts/channels/settings survive.
11. Check logs for unhandled exceptions.

Fix problems you find rather than merely documenting them.

---

# 93. Deliverables

When finished, the empty folder should contain the full source tree.

Also provide a final summary containing:

```text
Files created
Implemented IRC commands
Implemented IRCX commands
GUI pages
Database tables
How to install
How to run
How to run tests
Default ports
Known limitations
Suggested next development steps
```

Do not paste every source file into the final response if the files already exist in the workspace. Create the actual files.

---

# 94. Important scope decisions

For version 0.1:

**Must work:**

- single-server operation
- IRC TCP server
- IRCX extension layer
- PyQt6 management console
- accounts
- operators
- persistent registered channels
- access lists
- channel properties
- TLS
- logging
- moderation

**Architect for later but do not let these delay a stable 0.1:**

- server-to-server linking
- REST administration
- browser chat
- WebSocket gateway
- Docker packaging
- advanced IRCv3 history
- external identity providers
- distributed database

The priority is a stable, pleasant-to-administer IRCX server.

---

# 95. Product rule

At every design decision ask:

> Can an administrator perform this common task comfortably through the OpenIRC GUI without knowing IRC protocol syntax?

If the answer is no, improve the administration interface.

At every protocol decision ask:

> Will a normal IRC client still behave sensibly?

If the answer is no, improve compatibility.

At every architectural decision ask:

> Can the future OpenIRC web client use the same server without rewriting the core?

If the answer is no, improve separation.

Build OpenIRC as a real open-source server that could reasonably be used by a small community, LAN, retro-chat network, hobby project, or private organization.