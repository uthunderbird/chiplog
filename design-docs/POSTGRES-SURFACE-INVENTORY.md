# PostgreSQL surface and existing-data inventory

The executable script in `pyproject.toml` is `chiplog = "chiplog:main"`.
`src/chiplog/__init__.py` calls `cli.main`; the existing CLI surfaces are below.

| Existing surface | Current write/read path | SQLite boundary |
| --- | --- | --- |
| `auth login/status/logout` | Existing OAuth token file, no application database | No SQLite application write |
| `chat` / `chat --message` | `open_codex_chat` → per-user PG `PostgresLoopStore` | Detects old chat source and refuses silent empty history until explicit import |
| `bootstrap` | `open_postgres_planning_runtime` → PG trust store | No SQLite path option |
| `create` | PG planning repository + same-transaction gate/trust | Default HOLD without independent entitlement |
| `show` | PG projection + current auth/disclosure gate | Default HOLD without independent entitlement |
| `migrate-sqlite-chat` | Read-only old local-chat snapshot → empty mapped PG database | Explicit narrow lossless import; source stays intact |
| Native V2/V3 execution client | `open_postgres_execution_runtime` / `PostgresExecutionRuntime` | Native PG selected source and lean immutable publications |

Native execution currently mounts real model transport/custody and fenced
replacement, exact captures/seals, call adoption/cancellation, original-effect
custody/recovery, read-only histories and shared-counter successor retries,
all-seal continuation, CompleteAcceptance, actual durable delivery and explicit
Run lifecycle suspend/resume/abort with retained pending references. Native current
assertions have an actual provider-query authority; unsupported planning/fact/
candidate assertion owners HOLD rather than accepting caller-built DTOs.

There was no R14 command in the existing CLI to reroute. The R14 execution client
surface is programmatic. The new native client performs actual Codex HTTP adapter
calls in integration tests and actual provider SEND/reconciliation with persistent
provider journals; it is not only a DTO or storage adapter.

The previous library graphs remain writable SQLite implementations:
`r14_execution_runtime.open_execution_runtime`, the factories in
`r16_dispatch_runtime`, and versioned `open_installed_h1_*` factories in
`common_cli_execution_runtime`. They are not imported by the current CLI/chat
entrypoints. Existing verification and historical regression fixtures still use
these graphs. Their continued existence is an explicit legacy compatibility boundary,
not evidence that they were migrated to PG. New programmatic clients use
`chiplog.client.open_execution_client`, which directly exposes the native PostgreSQL
factory without a backend selector. These old factory contracts are not silently
redirected. Removal and active authority-bundle conversion are backlog items,
revisited when an actual legacy consumer or concrete data migration requires them;
no claim that all SQLite writers were removed is made.

## Existing durable data

The supported local-chat importer retains original immutable record bytes, record
IDs, batch membership/order, sequence, idempotency keys and fingerprints. It reads
SQLite with `mode=ro`, including its committed WAL view, validates before writing,
and imports all accepted members in one PostgreSQL transaction. It never deletes
or rewrites the SQLite source. Exact repeat import is REPLAY; target conflicts or
unsupported rows reject the complete import. Chat cannot silently hide an old
source behind a newly provisioned empty database.

Planning/R14 authority bundles, backup/sidecar evidence and installed H1 graph
state are not accepted by this importer. The new PG runtime does not claim to
continue an arbitrary old authority graph from an archival copy. Those originals
stay readable by their existing legacy graph; no automatic mass migration or
source deletion is performed. This is the explicit data boundary, not a claim of
lossless active authority migration for those formats.

Inspection commands (run from the PostgreSQL worktree):

```sh
rg -n 'project.scripts|chiplog =' pyproject.toml
cat src/chiplog/__init__.py
rg -n 'sqlite|SQLite|open_.*runtime' src/chiplog/cli.py src/chiplog/codex_cli.py src/chiplog/composition/codex_chat.py
rg -n '^async def open_|^def open_' src/chiplog/composition/r14_execution_runtime.py src/chiplog/composition/r16_dispatch_runtime.py src/chiplog/composition/common_cli_execution_runtime.py
rg -n 'open_installed_h1_native_one_call_accounting_v24_runtime' src --glob '*.py'
rg -n 'sqlite3' src/chiplog/composition/postgres*.py src/chiplog/platform/postgres*.py src/chiplog/adapters/driven/*postgres*.py
```
