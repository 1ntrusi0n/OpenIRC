# Architecture

## Ownership and execution

`OpenIRCServer` is a Qt-independent asyncio runtime. All mutable sessions, memberships and live channel records belong to its event loop. The desktop runs that loop on one worker thread and Qt on the main thread. Headless mode runs the same core directly. No protocol processing depends on widget repainting or a Qt timer.

The runtime survives Start/Stop cycles. Starting validates certificates and binds the requested sockets before announcing Running. A startup failure closes every socket created during that attempt. Stopping closes listener sockets, disconnects sessions, waits for accepted transports, and cancels maintenance work. SQLite and management services remain available until application exit. `close()` additionally flushes/closes persistence, releases the instance lock and stops logging.

Core domain behavior is divided between lifecycle, identity, channel operations, mode operations, permissions, rate limits, sessions, and events. Standard IRC and IRCX handlers are independent adapters over these services. Numerics, mode metadata, property metadata and Unicode conversion have centralized definitions.

## Administration and GUI

An `AdminCommand` names an operation with validated arguments. `AdminService.execute(principal, command)` checks its explicit permission before applying it. `snapshot(principal)` returns a frozen `ServerSnapshot`; nested records are read-only. Qt submits coroutines using `run_coroutine_threadsafe` and receives results through queued signals. It never receives live connection dictionaries or waits synchronously for a server future.

The trusted local console uses `LocalAdministrator`. Remote operator identities originate from authenticated accounts and separate operator grants. The role/permission editor controls those grants. The UI refreshes snapshots at a bounded rate; traffic processing continues while the window is minimized or a dialog is open.

## Built-in chat

The chat panel is a session adapter, not an administrative bypass. An enabled operator account must authenticate with a username/password. The user selects a distinct IRC nickname. A core-created Local transport avoids transmitting those credentials over the network and is labeled Local rather than TLS.

Chat input passes through the normal parser/dispatcher and routing path. Its member lists and transcript come exclusively from its session's outbound messages. It cannot consume unrestricted administration snapshots as chat data. This preserves channel permissions, privacy and auditorium behavior. Logout, KILL, account revocation and server shutdown remove the session normally. Passwords are not retained in settings or transcripts.

## Persistence, threading and events

SQLite executes on one dedicated worker through awaited calls. Transactions and schema migrations are versioned; a schema update never recreates a database. Persistent mutation services coordinate through the server lock and publish changes after persistence succeeds. Transient memberships and invitations are never restored after restart.

The event bus uses bounded subscription queues. GUI logs and per-session network output are bounded too. Slow readers are disconnected instead of blocking other clients. Rotating log writes are handled by a logging worker; Argon2 verification runs outside the event loop with bounded concurrent credential work.

Account/operator/ban changes and explicit administrative operations are audited without recording secrets. A separate redacted configuration export is available; it is not a full database backup.

## Extension boundary

Public channel inspection, account authentication, server capabilities, and administration are services rather than direct database APIs. A future HTTP/WebSocket adapter can call them with an appropriate principal and immutable views. User/account/channel UUIDs are independent from SQLite rows, and channel wire OIDs are persisted separately. Local server identity and network identity are distinct settings.

No federation, remote administration listener, HTTP API, web chat, or external service execution is included in 0.1. Cloning and service-mode controls are explicitly deferred rather than implemented as inert successful commands.
