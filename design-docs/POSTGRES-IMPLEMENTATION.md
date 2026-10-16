# PostgreSQL implementation status

Owner: postgres-per-user implementation agent. Branch `feat/postgres-per-user`.

## Plan (reread before implementation)

1. Add PostgreSQL provisioning with one database per trusted user identity.
2. Implement immutable record/batch persistence, atomic writes and head CAS.
3. Mount existing AgentLoop in the real chat composition and verify with PostgreSQL.
4. Continue the planning/R14 storage transition; retain functional transitions and
   durable identities without porting SQLite physical assurance machinery.
5. Inspect existing SQLite formats and supply an explicit lossless migration path;
   never mutate source files. Deliver focused integration evidence and commits.

## Assumptions (reread before implementation)

- Runtime server configuration is operator controlled; user messages cannot select
  DSNs or database names. Each database binds exactly one user identity.
- Local chat has no planning authority. Its existing domain transitions, exact
  live binding and durable emission-before-SEND remain required.
- A dedicated ephemeral Docker PostgreSQL instance is available for tests.
- Old SQLite data is retained until explicit import, with unsupported formats
  rejected rather than partially imported.

## Cold plan review

Cold agent found three blockers; plan amended before code: seed a singleton head
row before any append, take consistent REPEATABLE READ snapshots, and execute
refreshed snapshot/domain/caller guards on the writer connection after head lock.
Tests must exercise independent connections and rollback, not just sequential CAS.
Zero findings does not imply independent external verification.

## Evidence / outstanding scope

First executable vertical slice: `platform/postgres.py`, `loop_postgres.py`,
and the production `open_codex_chat` composition. No SQLite/journal/anchor bundle
is opened for chat. PostgreSQL stores canonical Run bytes in complete batches.
Opening a new chat worker fences the previous model worker.

`CHIPLOG_TEST_POSTGRES_DSN=... uv run pytest -q tests/platform/test_postgres.py
tests/composition/test_codex_chat.py`: 6 passed in 1.94s on isolated PostgreSQL 18
Docker instance, localhost port 55439. Tests create/drop only UUID-owned databases.
Evidence: two separate user DBs; exact payload bytes; whole-batch rollback on a
second member collision; stale CAS; replay vs changed payload conflict; database
identity mismatch; independent concurrent first appends; consistent snapshot
while another connection writes; rejection guard rollback; live chat completion
and lost response/reopen with no repeated model request. Focused mypy passed.

Planning now uses `postgres_planning.py`, the unchanged DeploymentTrustService
with native PG journal/materialization ports, and the existing planning use case,
owner factory and projection reducer. Bootstrap/revoke/authenticate/publication
share the user row lock. Deployment entitlement defaults to HOLD; a pinned
independent proof verifier is required before importing eligibility. Exact
request, current eligibility, freshness, cap and handoff are checked in the same
transaction as the planning batch. The actual CLI bootstrap/create/show now uses
PostgreSQL (the obsolete SQLite `--database` flag is removed).

`CHIPLOG_TEST_POSTGRES_DSN=... uv run pytest -q
tests/composition/test_postgres_planning.py tests/cli/test_r8_default_hold.py`:
4 passed in 1.87s. Covers signed independent eligibility, normal 5-record create,
reopen/replay, projection, revocation, mixed-operation global head, batch+handoff
rollback and retry, and subprocess CLI default HOLD with no development bypass.
No database-role isolation against a compromised administrator runtime is claimed;
the trusted runtime chooses each user's mapped database and verifies its identity.

Native R14 substrate is now `composition/postgres_execution.py`. It publishes
V2/V3 Run transitions, exact captured model bytes, response seals, initialized
original-call identities and lean call-state records in atomic PG batches. The
model is invoked after EMITTED commits; unknown outcomes hold instead of resend.
Every public replay authenticates its supplied peer and current actor. A fresh
runtime fences the previous worker; model adapters cannot be rebound to another
runtime. Explicit accept/cancel branches compete, unknown call outcomes remain
accepted/pending, terminal outcomes persist in the original call stream.

History ingress is committed with Run creation. Reads authenticate current user,
select immutable rows at a retained frontier, and use a user-bound stable cursor
that survives reopen and later publications. Root owns the separate mounted
read-only executor; its pure `readonly_runtime*` leaf files are included for
typing/integration and are not reported as mounted by this core slice.

`CHIPLOG_TEST_POSTGRES_DSN=... uv run pytest -q
tests/composition/test_postgres_execution.py`: 7 passed in 1.97s. Includes V2/V3
create/begin/capture/seal, max3 read-only initialization, replay/reopen without
another request, wrong-peer replay denial, unknown response HOLD, exclusive model
ownership, replacement while old invoke is paused before SEND, exact original
call branch competition/terminal reopen, history snapshot/cursor/revocation.

Explicit `migrate-sqlite-chat` now imports the supported old local-chat-only
stream into an empty target in one transaction, retaining original bytes, IDs,
sequences and idempotency. Source uses SQLite `mode=ro` with its committed WAL
snapshot; no source deletion/update occurs. Nonchat planning/R14 authority
bundles, foreign tenants and auxiliary durable data reject the whole import.
Chat refuses an existing unmigrated SQLite source instead of hiding its history.
Tests exercise source bytes unchanged, exact PG read/replay, unsupported-source
rejection with empty target, and reopen through the actual client after import.

Mounted read-only execution is root's `composition/postgres_readonly.py` plus
the thin native runtime wrapper. One persisted state record atomically accepts
each attempt and consumes its shared max3 counter before the actual history
read; failures/reopen do not reset it. Platform maps only PostgreSQL's definite
LockNotAvailable/DeadlockDetected/SerializationFailure read aborts to retryable
failure, with no catch-all classification. A real second-connection lock timeout
test observes LockNotAvailable after acceptance, then a successful retry on the
same selected snapshot with consumed counters 1 and 2.

Native `continue_execution` reduces the selected seal's actual per-call states:
initialized, accepted, retry-pending and unknown calls hold; cancelled, definite
direct terminal and terminal read-only calls permit the next Turn. Selected
read-only result bytes appear in that Turn's actual model request. The current
two-seal PG witness is proposal-only; it is not evidence for two accepted
consequential seals. Two proposal seals, reopen/replay and a pending-then-terminal
read-only continuation passed focused tests. Accepted-open resolver closure is
still required: a caller's terminal flag cannot close an unknown original
obligation. Successor retry-budget preservation is now mounted as described below.

Original effects now use the native PostgreSQL custody and resolver path below.
Native completion/delivery and explicit suspend/resume/abort are mounted below.
Final functional convergence, full integration checks and review remain pending.
Legacy planning/R14 authority-bundle migration is explicitly unsupported. This status
does not claim the whole replacement or legacy data migration is complete.

The read-only successor route atomically supersedes the source Run, creates an
ACTIVE successor with its immutable inherited Turns, records the source edge and
accepts the next attempt in the same four-record batch. It preserves the original
query, selected snapshot and shared max3 lineage counter without another ingress.
Inherited original-call identity follows persisted source edges, with cycle
detection rather than a three-edge ancestry limit. Selected-seal pending siblings
still HOLD admission. Explicit expected-head retry in the current ACTIVE Run is
retained alongside successor retry under the functional scope.

Root independently ran `CHIPLOG_TEST_POSTGRES_DSN=... .venv/bin/pytest -q
-p no:timeout tests/composition/test_postgres_readonly_successor.py
tests/composition/test_postgres_readonly.py
tests/composition/test_postgres_continuation.py`: 13 passed in 10.47s.
The six successor tests cover atomic admission/no duplicate ingress, four edges
across two lineages, max3 across reopen, successful retry followed by an actual
model seal, interrupted accepted attempts without another read, wrong-peer and
stale-worker rejection, and pending consequential sibling HOLD until cancellation.
This is not a claim that completion acceptance or all terminal-Run gates are mounted.

Native original effects are mounted in `composition/postgres_effects.py`: explicit
exact-byte preview/adoption, signed deployment eligibility (default HOLD), immutable
first transmission and a separately committed emission claim before the actual
HermeticEffectsProvider SEND. Claim publication also retains the original Run
obligation and unknown original call state atomically. Claim-time authentication,
worker fence, unchanged target/key, adopted horizon and live deployment eligibility
are rechecked; recorded eligibility replay cannot bypass current expiry.

The binding requires persistent provider transfer and observation journals plus a
registered receipt key. Reopen reconstructs provider custody from that journal.
A repeated claim never SENDs, including lost response after the external transfer;
reconciliation only reads the original receipt. Authenticated receipts and their
complete-inventory semantic reduction publish atomically. The existing effect
reducer recomputes all original members/children from OUTCOME_UNKNOWN, so a late
rival receipt produces a current HOLD without erasing historical closure.

Fresh authenticated resolution publishes invocation, original closure, current
semantic reduction and terminal call state in one batch. Continuation requires
both the original closure and closed current semantics for every retained sealed
Turn, including an older effect behind a later closed proposal seal. Generic
acceptance/outcome APIs reject self effects before adoption too: a caller flag
cannot fabricate a native provider result. Cancellation before adoption remains.

Focused code review found and repaired that pre-adoption generic API bypass; the
cold reviewer then found no remaining P0/P1 in this bounded slice. This does not
replace final integrated review. `CHIPLOG_TEST_POSTGRES_DSN=... .venv/bin/pytest -q
-p no:timeout tests/composition/test_postgres_effects.py
tests/composition/test_postgres_execution.py
tests/composition/test_postgres_readonly_successor.py` passed 19 tests in 12.54s
before the final provider-reopen and gate-expiry assertion refinements; the
checkpoint gate reruns the selected modules. The supported transport remains the
existing registered hermetic self recipient; no external production provider or
completion delivery is claimed by this slice.

Model response custody is now mounted at the actual CodexModel and HermeticModel
transport boundary. Native execution registers the exact emitted attempt in the
same transaction as EMITTED; a one-shot STARTED permit commits before transport.
Raw returned response bytes and receipt are retained independently of Run capture.
The original sender may retain immutable response facts after worker takeover;
only the current authenticated worker can consume them into the selected Run.
No custody permit is recovered from reading STARTED, including lost start ACK.
The native factory forwards the concrete effects binding and independent gate
verifier; defaults remain HOLD for SEND.

Root's eight focused model custody cases passed in 2.72s: V2/V3 response recovery
and capture-ACK replay, actual Codex lost-capture recovery with one POST, actual
late HTTP response after worker takeover, unknown STARTED without retained bytes,
and lost start ACK with zero transport calls and no second permit. Full fenced
replacement of an unstarted model attempt and adoption by a new worker remain
pending this first custody checkpoint; retained-response recovery alone is not
reported as whole model recovery. Shared chat adapters are included in the
checkpoint test selection because their optional private custody hooks changed.

Native model replacement now fences an exact NEVER_STARTED attempt under the
same writer lock that selects the fresh prepared/emitted generation and registers
its new custody permit. STARTED wins the race by holding replacement; fence-first
prevents the old sender from starting or retaining substituted facts. Replacement
replay never invokes transport again, including a lost publication ACK. A fresh
current credential/session is adopted for the new worker while prior immutable
Turn and original obligation identities remain retained.

Root's final model selection passed 14 cases in 5.32s: eight response custody cases
plus six V2/V3 replacement cases, including actual trust credential rotation,
fence-first zero-old/one-new SEND, STARTED-first recovery HOLD, committed replacement
ACK loss, and rejection of a response submitted by the fenced old attempt. This
closes the earlier unstarted-attempt/new-worker-adoption gap; final integrated
regression and whole-replacement review remain pending.

Native Complete acceptance and delivery now use `composition/postgres_completion.py`
and thin client runtime methods. Acceptance binds the exact retained sealed source
Run, captured raw bytes/selector and original visibility manifests. The existing
`prepare_completion` renders a separately named canonical typed interpretation of
ordinary JSON, preserving original noncanonical model bytes. Actual current
provider queries come from authenticated receipts plus original closure/current
semantic reduction; unsupported planning/fact/candidate assertion authorities HOLD.
Their source frontier tracks original-effect reductions, so model and delivery
publications do not invalidate their own current inputs.

One PG transaction publishes the SUCCEEDED/CompleteAcceptance Run, acceptance,
actual manifest, distinct prepared delivery intent and rendered conversation row.
Delivery identity is a real completion subject, never an invented original call.
Forced interruption after SQL inserts rolls the complete five-record batch back.
A separately authorized signed first-send handoff and durable emission claim
precede the existing provider transport. Both acceptance and claim recheck every
selected old seal and reproduce current assertions under the same writer lock;
a late rival after acceptance or behind a newer Complete seal prevents SEND.
Claim-time current actor, worker, exact recipient/key and live expiry are checked.
Recorded gate handoff replay grants no expiry bypass. Lost response is reconciled
from provider custody after reopen, with no automatic repeated SEND.

`CHIPLOG_TEST_POSTGRES_DSN=... .venv/bin/pytest -q -p no:timeout
tests/composition/test_postgres_completion.py tests/composition/test_postgres_effects.py
tests/composition/test_postgres_readonly.py
tests/composition/test_postgres_continuation.py`: 24 passed in 15.13s. Twelve
completion cases include actual Codex HTTP noncanonical JSON, original bytes,
recipient substitution/disclosure rejection, atomic rollback, default HOLD,
claim-time expiry/revocation, provider assertion rendering, and old-seal rival at
both acceptance and claim. Cold code review reported zero concrete P0/P1 in this
bounded slice; no final integrated-review claim is made.

This checkpoint also includes root's demonstrated replacement-policy correction:
`max_model_retries=0` now rejects generation replacement before fencing or SEND.
The prior implementation violated that explicit budget. Root observed the red
case and the repaired 15-case model selection passed in 5.15s; the checkpoint
reruns the model modules. Native lifecycle/client default mount inventory and
legacy authority-bundle import boundaries remain explicit unfinished work.

The default public programmatic client is now
`chiplog.client.open_execution_client`, a direct export of the native PG factory
without a backend selector/fallback. Existing CLI application commands were
already PG; no R14 CLI command existed to reroute. A real PG public-factory test
runs V3 create/begin/capture/seal/CompleteAcceptance and authenticated reopen/replay.
Legacy SQLite composition modules are explicitly marked compatibility surfaces;
README and `POSTGRES-SURFACE-INVENTORY.md` state their remaining writers and the
unsupported active planning/R14 authority-bundle import boundary. Removal/full
conversion is NEXT backlog, revisited for a real legacy consumer or concrete
user data migration; sources remain intact. This is an explicit retained
compatibility boundary, not a claim that every SQLite writer was deleted.

For native lifecycle integration, new effect preview/adoption/first-send
admissions now require the original selected Run ACTIVE. Existing historical
intent/send replay and previously claimed inflight facts remain inspectable.
Each emission claim atomically appends its actual native claim reference to the
Run's retained no-retry references, preserving previous references and original
obligations. `CHIPLOG_TEST_POSTGRES_DSN=... .venv/bin/pytest -q -p no:timeout
tests/composition/test_postgres_client.py tests/composition/test_postgres_effects.py`
passed 6 tests in 5.08s. Root owns the separately pending lifecycle state machine
and its negative admission tests.

Observed normal-flow repair (two actual accepted consequential seals):

- The new convergence test reached its third Complete but seal publication charged
  the full retained Run against a single request/response budget. Debugger stack
  was the actual convergence test → seal_execution → seal. Inspection expression
  `{size, bound, len(sealed.canonical_bytes()), len(raw)}` observed respectively
  169958, 131072, 169332 and 159 bytes; the selected request was only 13088 bytes.
- Plan: include the exact already-durable selected captured Run byte size in the
  publication allowance, retaining the unchanged normal new-fanout budget and
  physical request/response checks. Keep the byte guard and truthful bound field.
  Re-run the same actual two-effect scenario and focused native seal cases.
- Assumptions: seal receives the exact headed selected captured Run; copied
  immutable prior state is not new model input. New call members/metadata remain
  bounded by the existing fanout byte budget. No historical bytes are rewritten.
  This is a local repair from an observed ordinary flow, not a new tunable limit.

The captured-Run allowance repair passed the actual two accepted consequential
seals witness through a third CompleteAcceptance, including one cancelled sibling,
first lost-response reconciliation, two retained original obligations/no-retry
references, provider/runtime reopen and historical claim replay with exactly two
original SENDs. A negative new-fanout case still rejects before publication despite
request and response staying within their normal physical limits.
`CHIPLOG_TEST_POSTGRES_DSN=... .venv/bin/pytest -q -p no:timeout
tests/composition/test_postgres_two_seals.py`: 2 passed in 4.62s. Debugger restart
at the same third-seal stack observed size169958, durable input169049, new growth909,
unchanged new-fanout budget131072 and allowance300121; continued test passed.
PRE hypotheses, expected stacks and next steps were recorded before both
breakpoints. Receipt: `/private/tmp/chiplog-pg-two-seal-debug.md`; session closed.

Native lifecycle is root's `postgres_run_recovery.py` plus runtime wrappers.
Suspend retains a truthful sealed source/frontier baseline. Resume preserves Turns
and pending initialized/adopted/read-only work; actual crossed original obligations
must resolve with current semantics first. Continuation and CompleteAcceptance
retain their stricter all-seal checks. Abort publishes metadata without waiting on
an active HTTP request; already-claimed inflight work can retain immutable facts.
Original effect resolution after abort closes its original stream without Run
resurrection or another SEND. Read-only successor admission supports a suspended
source and atomically creates an ACTIVE successor with the original shared budget.
New model permits require the latest ACTIVE selected Run and exact attempt inside
the writer transaction. The default public client integration additionally makes
`sqlite3.connect` fail throughout its PG create/capture/completion/reopen flow;
the actual flow passed, showing that compatibility imports do not open SQLite.

Carried original-work admission repair:

Root identified a concrete missing admission join: a later readonly retry seal
could skip an older carried original effect's current semantics. Mounted real-PG
RED (`.venv/bin/pytest -q -p no:timeout
tests/composition/test_postgres_readonly_carried_work.py -k new_readonly_retry`,
with CHIPLOG_TEST_POSTGRES_DSN set) produced two DID NOT RAISE LoopHold failures
in 3.28s, for both explicit same-Run and successor retry. The trace actually sent
and resolved the first effect, selected a second-seal history call, retained a
definite retryable first read, then appended an authenticated late RIVAL.

The local repair reuses `_crossed_work_closed` on the executing Run at NEW
acceptance inside the writer snapshot guard; successor also checks it in its
existing admission function. It keeps the selected-seal sibling check. Outcome
publication deliberately omits the new admission check: an already accepted
attempt may retain its observed result after a late RIVAL.

The new six real-PG cases passed in 21.12s with command
`CHIPLOG_TEST_POSTGRES_DSN=... .venv/bin/pytest -q -p no:timeout
tests/composition/test_postgres_readonly_completion.py
tests/composition/test_postgres_readonly_carried_work.py`. They verify unchanged
publication/counter/read state on both rejected routes, accepted-then-rival outcome
retention on both routes, and successful/max3-exhausted successor lineages through
actual CompleteAcceptance and authenticated reopen/replay without new read,
attempt, publication or SEND. Pending continuation and completion are held before
terminal. The cold plan review had zero P0/P1; no new verification framework or
broad audit was added. Root retains full integration ownership.

Selected completion frontier repair (ratified cold-RT P1):

The shared provider-query frontier previously used every user's original-effect
reduction publication, including unrelated Runs. A typed mounted regression
reproduced the failure at actual `commit_delivery_send`: Run A accepted Complete,
Run B independently resolved another effect, then A was permanently held although
its selected evidence had not changed. Pre-fix command with the dedicated DSN:
`.venv/bin/pytest -q -p no:timeout
tests/composition/test_postgres_completion_frontier.py` → 1 failed in 2.98s,
LoopHold from real delivery revalidation. The provider's three actual scenarios
are configured at construction; no private scenario mutation is used.

`provider_queries` now collects the exact current reduction heads actually used
by this Run's returned query set, computes their shared publication frontier,
and applies that same aggregate to the queries. The existing observation/frontier
contract, authenticated receipts, current result reduction and old-seal RIVAL
checks remain unchanged. The cold plan review reported zero P0/P1.

Post-fix command `CHIPLOG_TEST_POSTGRES_DSN=... .venv/bin/pytest -q -p no:timeout
tests/composition/test_postgres_completion_frontier.py
tests/composition/test_postgres_completion.py
tests/composition/test_postgres_two_seals.py` → 15 passed in 13.97s. It includes
actual delivery commit/claim, provider-journal reopen, idempotent replay with no
extra SEND, selected RIVAL HOLD at both acceptance and claim, and the two accepted
consequential seals convergence witness. No existing stored bytes are rewritten.

Primary API references consulted 2026-10-17:
- https://www.psycopg.org/psycopg3/docs/basic/transactions.html
- https://www.psycopg.org/psycopg3/docs/basic/usage.html
- https://www.postgresql.org/docs/current/sql-createdatabase.html
- https://www.postgresql.org/docs/current/transaction-iso.html
