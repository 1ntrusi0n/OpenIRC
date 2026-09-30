# Persistence and administration

OpenIRC stores its durable configuration in `openirc.sqlite3` inside the selected data directory. The default location is `%LOCALAPPDATA%\OpenIRC` on Windows and `$XDG_DATA_HOME/OpenIRC` (or `~/.local/share/OpenIRC`) elsewhere. `--data-dir` selects a different directory. Runtime data, certificates, logs, and credentials are excluded from Git.

One dedicated executor thread owns the SQLite connection. Network and GUI threads submit asynchronous work; they do not access that connection directly. SQLite foreign keys, WAL journaling, a busy timeout, and parameterized values are enabled. Table names are restricted to an internal allowlist. A held operating-system file lock prevents two OpenIRC runtimes from opening the same data directory. A leftover `openirc.lock` file does not block startup when its previous process has exited.

## Schema version 1

| Table | Stored information |
| --- | --- |
| `settings` | JSON-encoded setting values keyed by name; includes a private random host-cloaking secret. |
| `accounts` | UUID, unique canonical username, JSON account record including Argon2id password hash. |
| `operators` | Account ID and JSON role/explicit permissions; account foreign key with cascading deletion. |
| `registered_channels` | UUID, unique canonical channel name, optional founder account foreign key, complete channel configuration. |
| `channel_properties` | Reserved document repository for future property-level storage; v1 properties are embedded in channel records. |
| `channel_access` | Reserved document repository for future access-row storage; v1 channel access lists are embedded in channel records. |
| `server_bans` | JSON records containing ban type, mask, reason, creator, creation/expiry times, and enabled flag. |
| `nick_reservations` | Canonical nickname key, account foreign key, nickname/protection/creation record. |
| `user_access` | Grouped access entries keyed by authenticated account ID. Anonymous-user access rules remain transient. |
| `server_access`, `network_access` | Grouped local-server and single-server-network access entries. |
| `audit_log` | Timestamp, actor, action, object type, object identifier, and credential-free summary. |

Most domain records use versioned JSON documents behind relational keys. Registered channel records contain the channel OID, creation time, founder, modes, member limit, topic, subject, language, validated properties, hashed credentials, and access entries. They exclude live memberships and invitations. UUIDs identify internal objects; separate protocol OIDs are retained across channel restarts.

Deleting an account cascades its operator grant and nickname reservations. The database rejects deletion while a registered channel references that founder. The administration service reports this clearly and requires transfer or unregistration first. Channel unregistration preserves an occupied live room; closing a registered room removes members while preserving the registration.

## Transactions and migrations

`PRAGMA user_version` tracks schema versions. Each migration executes in an explicit transaction and advances the version only after success. Existing records are not recreated or discarded during upgrades. OpenIRC rejects databases from newer, unsupported versions.

Persistent service mutations create replacement domain records, commit them, then publish the resulting in-memory state. Failed writes leave the prior published record intact. First-run setup creates the administrator, its operator grant, and settings in one transaction. `Database.transaction()` also supports grouped puts, deletes, and settings changes.

Administration storage mutations include their audit rows in the same SQLite transaction. Task-local audit scopes keep concurrent administrative requests separate. Operations affecting several commits may produce several audit entries for that action. Runtime-only operations such as starting listeners receive a separate audit record after completion. If that final audit write fails, OpenIRC logs the failure without reporting an already-completed operation as undone.

## Settings and snapshots

`server.saved_settings` holds the latest persisted configuration. `server.settings` contains the effective runtime configuration. Changes to identity, listening, TLS, Unicode compatibility, host privacy, output queue size, or log-file configuration remain pending while running and are adopted on restart. Other settings are applied live; rate-limit changes update existing sessions while preserving recorded failed-login history. Snapshots expose both saved settings and effective runtime settings plus the pending-restart field list.

The GUI communicates through `AdminService.execute(principal, AdminCommand(...))` and immutable `ServerSnapshot` records. Snapshots exclude password hashes and channel key hashes; channel records indicate only which keys are set. The local settings form may show the selected private-key file path. Configuration export omits that path, all password/key hashes, and the internal cloak secret. Exports are readable configuration reports, not complete restorable backups.

## Backup and recovery

For a complete backup, close OpenIRC and copy its data directory, storing TLS private keys with appropriate access restrictions. Do not copy only the main database file from a running WAL-mode database: recent committed changes may still be in its WAL file. The SQLite online-backup API is an alternative for operators who need live backups.

Restoring a backup means replacing the closed application's database with the backed-up database and reopening it with a compatible OpenIRC version. Keep the original database until the restored server has been verified. GUI layout settings are stored separately through Qt's platform settings facility.

## Verification

Persistence tests cover account/channel/access/ban survival, canonical uniqueness, ownership restrictions, cascading records, grouped-write rollback, migration rollback, newer-schema rejection, failed-write publication, immutable secret-free snapshots, setup, and management while listeners are stopped. Integration tests reopen real server data directories and verify accounts, registrations, access lists, reservations, and settings.
