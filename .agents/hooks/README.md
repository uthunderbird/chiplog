# Harness hooks

`codex-harness.py` is the portable adapter between Codex hook JSON and the
scripts in `.harness/scripts/`. The project wiring is declared in
`.codex/hooks.json`; Codex does not discover lifecycle configuration from
`.agents/hooks/`. See the official Codex hooks contract:
<https://learn.chatgpt.com/docs/hooks>.

The adapter takes the project directory from the `cwd` field in the Codex
payload, exports it as `HARNESS_PROJECT_DIR`, preserves hook output, and
propagates exit code `2` from `guard-edit.sh` so `PreToolUse` can block direct
file-edit tools (`apply_patch`, `Edit`, and `Write`). Command execution remains
available so the agent can create the commit that clears the hard threshold.

Project hooks do not run until the repository `.codex` layer and the exact hook
definitions are reviewed and trusted. Each definition pins the reviewed adapter
and target script by SHA-256 and rejects symlinks, because Codex trust covers the
hook definition, not mutable files invoked transitively. After changing either
file, update the hashes, restart Codex and use `/hooks` to review the changed
definition again.

The repository tests validate the adapter and checked-in configuration. They do
not emulate Codex discovery or its trust UI; verify those interactively with
`/hooks` after checkout.
