# Accounts, operator grants, and channel privileges

OpenIRC treats three kinds of authority separately: an authenticated account, a server operator grant, and a channel membership role. An account's username is independent of its current nickname. Logging in to an account does not automatically make the session a server operator; `OPER` and the built-in administrator chat explicitly request the operator grant.

The desktop administration console trusts the local operating-system administrator and creates a `LocalAdministrator` principal. It opens without an operator login. Every service request still carries an explicit principal. This trusted console principal is never inherited by a chat participant. The built-in chat requires an enabled, unlocked operator account and a separately selected nickname, uses the normal dispatcher and routing policies, and reports its transport as **Local**.

## Operator templates

Templates initialize editable explicit permission sets. Their labels do not grant additional authority beyond those permissions.

| Template | Initial permissions |
| --- | --- |
| Administrator / Sysop Manager | Every permission listed below. |
| Sysop | All except changing server settings, starting/stopping the server, managing accounts, managing operators, and shutdown. |
| Moderator | View server, manage channels, manage access, kick users, and set channel modes. |

Available permission names are `view_server`, `change_settings`, `start_stop`, `manage_accounts`, `manage_operators`, `manage_channels`, `manage_access`, `kick`, `kill`, `force_join`, `force_part`, `change_nick`, `set_modes`, `view_ips`, `view_logs`, `manage_bans`, `broadcast`, and `shutdown`. The shutdown permission is reserved for an adapter that requests application shutdown; the current desktop owns its local application lifecycle.

`view_server` permits operational snapshots. Account and reservation records require `manage_accounts`; operator grant records require `manage_operators`; real client IP addresses require `view_ips`; logs and audit records require `view_logs`; server-ban records require `manage_bans`. Desktop chat transport actions are restricted to the local principal.

Channel configuration requires `manage_channels`. Explicit mode, member-limit, and member-key edits also require `set_modes`. Editing channel access requires `manage_access`. Closing or deleting an occupied channel also requires `kick`. Disconnecting users matched by a server ban requires `kill`, in addition to ban management. Force-join, force-part, and nickname-change actions check their respective permissions.

Disabling, locking, or deleting an account revokes authenticated sessions. Removing/disabling its operator grant immediately removes operator permissions; built-in chat also disconnects. Changing the explicit grant refreshes active operator sessions. Password changes replace the stored hash; they do not independently disconnect an already-authenticated session.

## Channel roles

| Role | Baseline authority |
| --- | --- |
| Member | Join and participate subject to room modes and access rules. |
| Voice | Speak in a moderated channel. |
| Host | Moderate ordinary members, change permitted modes/topic/properties, and manage permitted access entries. |
| Owner | Host capabilities plus protected owner-level properties, owner grants, and owner-level modes. |

The creator of a new channel is its initial Owner. A registered founder account receives Owner when joining. Matching access entries and valid owner/host keys can grant the corresponding role. Roles are distinct internally. IRCX clients see Owner with the `.` prefix and `q` mode; ordinary IRC clients see an operator-compatible `@`/`o` representation.

A Host cannot demote an Owner or edit protected Owner access entries. Access entries remember their creator's role so lower-privilege users cannot delete entries established by a higher role. An explicit server permission can override channel membership checks: `kick` permits server-level removal, `set_modes` permits mode/member-role changes, `manage_channels` permits channel-property management, and `manage_access` permits channel access management. Registered mode is changed through channel registration administration, not ordinary `MODE +r`.

Property and mode metadata centralize minimum roles, validation, read-only status, and secrecy. Owner-only properties include owner/host keys and room policy metadata such as welcome/part text. Hosts can manage the topic and member key. Administrator-only `LAG` and `CLIENTGUID` changes require `change_settings`, including through the administration service. `OID`, `NAME`, `CREATION`, and `ACCOUNT` are read-only. Secret key values are replacement/reset-only and never returned as readable hashes or plaintext.

Auditorium mode hides ordinary peers from one another while preserving host/owner moderation visibility. Membership events, names, message delivery, and role transitions share the visibility policy. Operators with channel-management permission retain moderation visibility. Their authority comes from that grant, not from using the desktop chat transport.

## Authentication and privacy

Account passwords and channel credentials use salted Argon2id hashes. Hashing and verification run on dedicated bounded worker threads. Network authentication requires TLS by default; the core-created in-process Local transport is also considered secure. Account/operator states are checked again after expensive verification to handle concurrent revocation.

SASL PLAIN and the documented IRCX `OPENIRC-PLAIN` mechanism authenticate accounts. Server-operator login uses `OPER` with an enabled operator account. Anonymous users remain allowed by default. Failed attempts are throttled by account and address, and lock/disable controls are distinct from transient failed-login protection.

Public hosts are cloaked by default with a persistent random secret; administrators can select full-host or hidden-host policy. The server applies bans to nickname, user, host, IP/CIDR, account, or compound masks. Nickname reservations can be off, warn, or require the matching account. Nicknames are not automatically reserved merely because an account has the same username.

No password, key value, SASL payload, or hash belongs in operational logs, audit summaries, snapshots, or configuration exports. The administration API returns structured safe errors; raw credential-bearing commands are not logged.
