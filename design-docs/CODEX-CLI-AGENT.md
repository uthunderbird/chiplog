# Codex OAuth and live CLI dialogue

Status: local conversational slice implemented and verified, including live OAuth
and a two-message dialogue. Planning adoption and external actions are not mounted.

## Scope

The user requests a working Chiplog dialogue from its CLI, using Codex OAuth,
persistent credentials, default model `gpt-5.6-terra`, and reasoning effort `low`.
The existing `AgentLoop` remains the orchestrator. OAuth authenticates the model
account; it does not grant Planning authority or Calendar/Telegram entitlement.

The implementation was developed in `feat/codex-cli-agent`, worktree
`../chiplog-codex-cli-agent`, based on `6f34daf`. On 2026-09-20 it was transferred
into the main directory at `42564fe`, preserving its committed transcript/compiler
changes. Dependencies and import checks were merged, and the source catalogue
was regenerated for the combined tree. The harness edit limit is 40 dirty files. Split the work
into authentication/transport and live-loop/CLI contracts if needed; do not change
the limit. Commit and push were subsequently authorized by the user.

## Existing boundaries

- `cli.py`: authenticated R8 bootstrap/create/show entrypoints.
- `composition/r13.py`, `r13_runtime.py`: explicitly hermetic identities and model.
- `capabilities/agent_loop/application.py`: shared orchestration and durable
  prepare/emission/capture sequence.
- `capabilities/agent_loop/contracts.py`, `domain.py`: frozen attempt provider,
  recipient and disclosure labels currently limited to the hermetic contour.
- `architecture/r7_runtime.py`: exact registered leaf implementation and isolated
  owner validation. A live leaf requires a separate registered manifest.
- `verification/r8_surface.py`: executable boundary/import inventory.
- `inert_shared/r8-implementation-v1.json`: source-bound catalogue; regenerate only
  after reviewing the changed boundaries, then verify against stable source bytes.
- Historical scenario drivers and R13 fixtures retain their hermetic composition.

## Public contract

- CLI auth login starts Codex ChatGPT OAuth; status distinguishes authenticated,
  unauthenticated, unsupported credential mode, and corrupt/unavailable storage;
  logout clears only the Chiplog session. Secrets never enter public DTOs.
- Credentials persist outside the repository in a private Chiplog directory.
  Access/refresh tokens are neither command arguments nor conversation records.
- Model configuration defaults to `gpt-5.6-terra` and `low`; an unavailable model
  yields an explicit error, not a silent substitution.
- Live invocation consumes a durably emitted `ModelAttempt` and returns exact
  response bytes plus provider receipt identity. It cannot execute Chiplog tools.
- Immutable model binding records provider contract, recipient, model, effort and
  non-secret credential identity before emission. The adapter checks the binding
  against its authenticated session before transmitting.
- The live CLI authenticates the local OS principal and uses a separate database.
  It cannot relabel existing hermetic data as eligible for external disclosure.
- Only context explicitly permitting the exact model recipient may be emitted.
  Cancellation/timeout after possible transmission retains outcome-unknown and
  does not trigger a replacement attempt.

## Claim and evidence map

| Claim | Owned decision/data | Observable | Forbidden substitute | Boundary fixture | Evidence |
|---|---|---|---|---|---|
| OAuth session persists | credential store and auth mode | separate CLI process recognizes same session | API-key mode treated as OAuth | login, restart/status, logout, corrupt storage | CLI tests; pending |
| Terra/low is the default | immutable model binding | captured outbound request | ambient Codex settings or silent fallback | defaults and explicit override | adapter tests; pending |
| Only approved context leaves | source labels and exact recipient | transport invocation count and bytes | relabel restricted sources | denied recipient, stale credential identity | loop/adapter tests; pending |
| Chiplog owns the loop | existing AgentLoop and registered leaf | tool effects only through Chiplog ports | second agent independently acting | injected tool request, unrelated-file canary | composition tests; pending |
| Emission precedes exposure | durable attempt transition | store state at transport boundary | adapter-only metadata | crash/cancel before and after emission | composition tests; pending |
| No uncertain retry | persisted attempt state | request count after failure/restart | fresh run silently replacing ambiguous call | lost response and restart | composition tests; pending |
| Offline remains offline | hermetic manifest and entrypoint | network canaries and existing scenarios | provider injected under hermetic identity | substituted leaf/provider | existing verification plus mutants; pending |

Mutation coverage: omission (credentials/binding), unknown values (mode/provider),
substitution (account/recipient), duplicate/reorder (response events/attempts),
stale/race (session replacement), boundary N/N+1 (existing request/response and
turn budgets), logical/physical identity mismatch (database/attempt binding).
Each relevant family needs a reached negative fixture; no PASS is claimed here.

## Selected transport

The user supplied `uthunderbird/vibechord` as a reference. Its current main at
`a5d4147e78fe22ecd86646f27f19a92a565fbdea` uses API-key authentication; the working
OAuth example was found in the adjacent local project
`/Users/thunderbird/Projects/operator/src/agent_operator/providers/codex.py`.
It uses `oauth-cli-kit==0.1.3` and direct streamed POST requests to
`https://chatgpt.com/backend-api/codex/responses`. Chiplog adopts this transport
shape without the example's automatic retry or personal Codex token import.
The OAuth library source was inspected for PKCE, callback state validation,
refresh, storage injection and failures. Chiplog supplies its own private atomic
storage and suppresses provider error bodies. Installed Codex is not required.

The live CLI is a conversational/proposal slice: no Planning adoption port,
workspace import, Calendar or Telegram effect is mounted. This avoids importing
the R13 hermetic identities or implicitly authorizing external actions. Existing
R8 commands remain gated. Per-session history includes only messages explicitly
entered in this live dialogue and its accepted answers; persisted old runs are
not automatically disclosed or replayed.

Sources consulted: https://learn.chatgpt.com/docs/auth,
https://learn.chatgpt.com/docs/app-server,
https://developers.openai.com/api/docs/models/gpt-5.6-terra.
Local CLI inspected: `codex-cli 0.154.0`, login/exec/app-server help and generated
app-server JSON schemas. No personal credential file was read.

## Verification state

`sh .harness/scripts/test.sh --preflight` passed in the isolated worktree.
Cold plan review found no remaining P0/P1 after the binding/disclosure/loop
requirements above were added. This is design review, not implementation evidence.

Final implementation evidence, 2026-09-20:

- `uv run pytest -q --tb=short --maxfail=3`: **495 passed**, **407 deselected**;
  stage0 confirmed execution/coverage of those 407 delegated tests in its children.
- `sh .harness/scripts/lint.sh`: Ruff/format, resource lifetime and mypy passed.
- `python3 .harness/scripts/checks/tests_layout.py --worktree` and
  `git diff --check`: passed.
- `tests/cli/test_codex_auth.py`: cross-process persistence, private file modes,
  local logout, no personal-session import, corrupt/API-key-shaped storage rejection.
- `tests/platform/test_codex_credentials.py`: refresh rotation persisted; provider
  failure bodies do not appear in the public error.
- `tests/platform/test_codex_transport.py`: invalid recipient/model/account/worker,
  denied disclosure and unemitted attempts reach neither HTTP nor token refresh;
  partial/failed/malformed/reordered/duplicate/gapped streams cannot become success.
- `tests/composition/test_codex_chat.py`: actual owner/runtime and durable loop,
  prepare/emission before HTTP, configured Terra/low request, tool proposal followed
  by completion, accepted response surviving reopen, lost response without replay.
- `tests/verification/test_r7_bypass.py`: the now-reachable existing R13 storage
  facades admit only their registered symbols; an extra raw symbol still fails.
- The user completed `uv run chiplog auth login` in the browser. A fresh
  `uv run chiplog auth status` returned `AUTHENTICATED`; no credential contents
  were printed or placed in this repository.
- Live `uv run chiplog chat --message 'Привет! Ответь коротко по-русски: готов ли
  ты обсудить мой план на неделю? Ничего не создавай.'` returned
  `Да, готов обсудить твой план на неделю.` with the requested defaults.
- A piped interactive session introduced the fictional project name `Листопад`,
  asked for it in a second message, received `Листопад`, and exited with `/exit`.

Live testing exposed two protocol details now covered by regression fixtures:
`json_object` requires the word JSON in input (not just instructions), and Codex
can send text deltas followed by a completed event with an empty output array.
The adapter applies a fixed `JSON request:` transport prefix to the stored request;
it accepts streamed text only after successful completion, checking sequence and
final-text consistency. Failed live diagnostic attempts remain retained, not rewritten.

The earlier broad test failure was the R7 import inventory not yet recognizing
the existing R13 path newly reachable through CLI. Its explicit symbol registration
and good/bad fixture closed that failure. A subsequent run was interrupted before
source changes; only the final stable-source run above is claimed as complete.
This evidence report was updated after that run; production and test source bytes
were unchanged by the report update. There was no independent cold code review
or commit at that point; the cold review above was of the plan.

## Main-directory integration verification

On 2026-09-20, after transfer onto `42564fe`:

- `uv lock --check`, `sh .harness/scripts/test.sh --preflight`,
  `sh .harness/scripts/lint.sh`,
  `python3 .harness/scripts/checks/tests_layout.py --worktree`, and
  `git diff --check` passed.
- `uv run pytest -q --tb=short --maxfail=3`: **580 passed, 407 deselected**
  in 236.82 seconds; stage0 confirmed coverage of all 407 delegated cases.
- `uv run chiplog auth status` returned `AUTHENTICATED` using the existing session.
- Live `uv run chiplog chat --message 'Ответь одним коротким предложением: готов ли ты помочь обсудить план на неделю?'`
  returned `Да, я готов помочь обсудить план на неделю.`
- The transfer manifest and byte hashes confirmed exactly the 24 intended paths;
  existing transcript/compiler files outside that set remained unchanged.

Only this evidence document was updated after the stable-source test run.
The transfer was requested without a commit or push.

The user subsequently authorized commit and push. A cold `swarm-red-team`
review of the staged implementation found no confirmed P0/P1 blockers. Its
independent focused suite passed all 36 tests; an additional actual-runtime
account-substitution probe rejected transmission before HTTP and retained the
emitted attempt. The review artifact is local at
`/tmp/chiplog-commit-cold-review.md`. This is same-model, isolated-context review,
not a proof of correctness. Production and test source remained unchanged.
