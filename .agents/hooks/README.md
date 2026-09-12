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

## Early feedback after tools

`PostToolUse` matches `apply_patch` (including Edit/Write aliases) and `Bash`
(including `exec_command`). The `post-tool` adapter calls
`.harness/scripts/post-tool-checks.py` and wraps findings in the event's
`hookSpecificOutput.additionalContext` JSON. It never returns a block decision,
rewrites files, acknowledges handoff, or runs the full gate.

The first observed tool call checks inherited state. Later calls compare content
fingerprints and select existing checkers by dependency group:

| Changed inputs | Checker |
|---|---|
| Harness shell scripts or checker Python files | `messages_are_actionable.py` |
| `gate.sh` or checker Python files | `checks_are_wired.py` |
| Rules, reproducer tree, checker itself, or calendar date | `rules_have_reproducers.py` |

An unrelated edit after the initial scan runs no checkers. Shell command text is
not parsed: changed inputs are observed on the next matching completed call.
External or concurrent edits can therefore be reported too; a finding names the
repository state, not the agent who caused it. The final Git gate still runs all
checks independently. Tool hooks do not cover every tool path.

State lives under the worktree's Git directory, in `harness-post-tool/`, with a
hashed session identifier. A non-waiting lock serializes a session's scans.
Unchanged findings are silent; success clears suppression so recurrence is
reported. Different sessions/worktrees do not share suppression. A changed input
during a check is not cached. Timeout/unavailable results are advisory and retried
at subsequent matching events, never stored as successful verification. Each
checker has a 1.5-second timeout; the adapter and hook have outer timeouts of 6 and
8 seconds. Each finding is capped before joining, so all three findings fit into
the context before suppression. `additionalContextLimit: 0` avoids a second Codex
truncation; the dispatcher and adapter impose their own strict output bounds.
A busy concurrent scan can be skipped; this is early
feedback, not a completion guarantee or an immutable snapshot.

The PostToolUse definition pins the adapter, dispatcher and all three executed
checker scripts. Changing any of these invalidates its pins. Update hashes only
after reviewing the executable change, then review the exact changed definition
in `/hooks`. Pins are not automatic trust approval. Existing lifecycle definitions
also pin the shared adapter and must be reviewed when it changes.

For runtime acceptance after trusting the hooks, use a disposable worktree: make
a harness diagnostic omit its observable outcome, observe additional context,
repair it in a subsequent edit, and verify silence and that edits remain allowed.
Also check an unrelated edit, a shell edit, and a repeated identical finding.
`uv run pytest -q tests/tooling/test_post_tool_checks.py tests/tooling/test_agents_integration.py`
tests the scripts/configured command, not the active application's trust state.
