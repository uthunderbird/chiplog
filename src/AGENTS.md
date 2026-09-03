# Source rules

## 1. Authoritative durable reads [без гейта]

At a public adapter boundary, corrupted or unverifiable authoritative durable input
must not become a partial result. Raise a typed integrity error that identifies the
operation, tenant, and record identity and chains the root cause; do not use
`except …: continue`. Skipping is allowed only for explicitly non-authoritative,
optional display data, and that distinction must be named in the adjacent test.

## 2. Resource lifetime [без гейта]

Use a context manager for files, SQLite connections, and other resources with a
defined acquire/release lifetime. Do not open a resource and manually pair it with
`try/finally` at the call site. A `try/finally` remains valid only when no resource
context manager can express the lifecycle (for example, asynchronous shutdown), and
the reason must be clear from the local code. Mark that rare exception immediately
above the `try` as `# resource-contexts: justified-manual-close — <reason>` so the
static check can distinguish it from an accidental manual close.
