# OpenIRC

OpenIRC is an independent, open-source IRC/IRCX server with a native PyQt6 desktop administration console. It runs on Python 3.12+ and is designed for small communities, private networks, and LAN chat. The initial release is **0.1.0**.

The console manages the local server. Remote operators use their own IRC clients; the console also contains an optional chat panel that signs in with an operator account and a separately chosen nickname.

## Install and run

Windows PowerShell:

```powershell
git clone https://github.com/1ntrusi0n/OpenIRC.git
cd OpenIRC
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
.\.venv\Scripts\python.exe -m OpenIRC
```

Python 3.12 works as well. On Linux/macOS, create the environment with `python3 -m venv .venv` and use `.venv/bin/python` in the remaining commands. Alternatively, install the desktop dependencies with `python -m pip install -r requirements.txt`.

The first-run wizard creates the settings and first administrator account without internet access. The console initially shows **Stopped**. Click **Start Server**, then connect an IRC client to the configured listening address. Default ports are **6667** for IRC and **6697** for TLS; default binding is **127.0.0.1**. Select `0.0.0.0`, `::`, or a specific local interface to serve other machines. TLS remains disabled until a valid PEM certificate and private key have been selected.

The desktop console is trusted local administration: anyone able to run it against the data directory can administer that server. The wizard's account credentials are used for IRC authentication/OPER and the built-in chat panel, not for locking the console. Protect the host account and data directory accordingly.

## Features

- Real asyncio TCP/TLS listeners with IPv4/IPv6, standard IRC registration, channel chat, private messages, operator commands, and bounded client queues.
- IRCX discovery, channel creation, access lists, validated properties, extended channel lists, Owner/Host/Voice privileges, auditorium visibility, knock notifications, and private ONJOIN/ONPART messages.
- Independent accounts with Argon2id passwords, operator permissions, nickname reservations, registered channels, bans, SQLite migrations, and an administrative audit trail.
- Native desktop pages for overview, connections, channels, accounts, operators, bans/access, logs/audit, statistics, settings, and operator chat.
- Live moderation, channel/member/access editors, key replacement, MOTD editing, broadcasts, tray controls, and persistent window layout.
- One shared core for the GUI, local chat sessions, and headless operation. Configuration records remain editable while listeners are stopped.

Only capabilities supported by the implementation are advertised. See the detailed [IRC support](docs/irc-support.md) and [IRCX support](docs/ircx-support.md) tables for exact commands and compatibility choices.

## Accounts and connecting

Anonymous chat is enabled by default. Create accounts from **Accounts**, then grant operators a role from **Operators**. Account names and IRC nicknames are independent.

For account authentication from a network client, enable TLS and use **SASL PLAIN**. For operator elevation, issue:

```text
/oper account_name account_password
```

The slash is an IRC-client convention; on the wire the command is `OPER`. Password authentication over network connections requires TLS by default. IRCX also supports the documented `OPENIRC-PLAIN` AUTH mechanism. No NTLM or external identity service is required.

The built-in **Chat** page requires an enabled operator account. Its authenticated session uses a local in-process transport and the same permission/routing services as network users. It does not inherit the console's unrestricted administration principal. Chat passwords are never saved, and restarting the server disconnects chat.

Create a registered room from **Channels**, choose a founder, and configure its topic, modes, keys, welcome/part text, and access entries. `OWNER`, `HOST`, and `VOICE` entries assign privileges on subsequent joins. Registered rooms retain their settings while empty and after restart. Temporary rooms normally disappear when empty.

## TLS

Choose a certificate chain and private key in **Settings → Listening**, enable TLS, and restart the server. TLS startup errors are shown explicitly; configured TLS ports never fall back to plaintext. Disabling the ordinary IRC listener creates a TLS-only server.

For local testing, an OpenSSL installation can create a self-signed certificate:

```text
openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes -keyout openirc.key -out openirc.pem -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

Use a certificate trusted by your clients for a public deployment. Self-signed certificates must be explicitly trusted by test clients. Keep private keys outside the source checkout and do not publish them. The test suite generates disposable certificates automatically.

## Headless operation and data

Headless installations require no Qt dependency:

```text
python -m pip install -e .
python -m OpenIRC --headless --init-admin
python -m OpenIRC --headless
```

Initialization prompts for credentials securely. `--headless` explicitly starts listeners; Ctrl+C/SIGTERM shuts them down gracefully. `--debug` enables more diagnostic logging without recording credentials or command payloads.

Use `--data-dir PATH` on either launch mode for an isolated instance. Defaults are `%LOCALAPPDATA%\OpenIRC` on Windows, `$XDG_DATA_HOME/OpenIRC` (or `~/.local/share/OpenIRC`) on Linux, and `~/Library/Application Support/OpenIRC` on macOS. The directory contains `openirc.sqlite3`, a runtime lock, and rotating logs. Two runtimes cannot use the same directory simultaneously.

The desktop currently hosts its own core; it does not remotely attach to a separately running headless process. Stop the headless instance before opening the same data directory in the console.

Back up the data directory while the server is stopped. **Export Configuration** produces a readable, redacted configuration report; it excludes passwords and channel keys and is not a complete restore backup. Server settings use explicit Save/Apply. Changes requiring restart are identified separately from live settings.

## Compatibility status

| Area | Status |
| --- | --- |
| Required standard IRC commands and channel modes | Implemented |
| CAP and SASL PLAIN | Implemented |
| IRCX ISIRCX/IRCX/CREATE/ACCESS/PROP/LISTX | Implemented |
| IRCX AUTH | Implemented for `OPENIRC-PLAIN`; legacy providers are not implemented |
| Owner/Host/Voice, auditorium, ONJOIN/ONPART, WHISPER | Implemented |
| Modern UTF-8 and historical IRCX name conversion | Implemented; no particular historical client is certified |
| Accounts/operators/registered rooms/bans/reservations | Implemented |
| Local administration GUI and operator chat | Implemented |
| Clone creation and service integration modes | Planned; unavailable controls/modes are not advertised as working |
| Server linking, web chat, REST/WebSocket gateway, advanced history | Planned |

OpenIRC implements the public protocol independently. Modern IRC clients receive conventional operator prefixes for channel owners. Sensitive properties never reveal stored keys; owner/host/member keys can only be replaced or reset. Unicode normalization and historical compatibility choices are documented in the protocol guide.

## Development and tests

```text
python -m pip install -e ".[gui,dev]"
python -m compileall OpenIRC
python -m pytest
```

For unattended GUI tests, set `QT_QPA_PLATFORM=offscreen`. GUI tests skip when PyQt6 is absent; core and network tests do not import Qt. The tests exercise actual TCP clients, TLS, IPv6 where available, SQLite restarts, IRCX permissions/visibility, local chat, failure recovery, and GUI interactions. GitHub Actions runs the suite on Windows and Linux with Python 3.12 and 3.13.

The core is directly usable:

```python
from pathlib import Path
from OpenIRC.core.server import OpenIRCServer

async def serve_until(stop_event):
    server = OpenIRCServer(Path("server-data"))
    try:
        await server.start()
        await stop_event.wait()
    finally:
        await server.close()
```

Repository organization follows domain boundaries: `core` owns networking/state; `protocol` parses and implements commands; `models`, `security`, `persistence`, and `config` provide shared services; `admin` supplies the administration API; `gui` provides Qt adapters and views. See [architecture](docs/architecture.md), [permissions](docs/permissions.md), and [database](docs/database.md).

## Security and roadmap

Clients have bounded buffers, registration/incomplete-frame deadlines, flood limits, and authentication throttles. Public hosts are cloaked by default. SQLite operations are parameterized and run off the networking thread. Authentication and authorization are distinct; channel ownership is independent from operator roles. Debug logs intentionally omit message/credential payloads.

The next development priorities are wider client interoperability testing, automatic clone lifecycle, and a gateway built on the existing service boundary. This release does not implement server-to-server networking, an external services protocol, message history storage, or a browser client.

OpenIRC original source is MIT licensed. Dependency licenses remain their own, including PyQt6/Qt; see [third-party notices](docs/third-party-notices.md). OpenIRC is not affiliated with any historical IRCX vendor.
