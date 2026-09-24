# Authenticated R14/R16 call acceptance runtime

Status: implemented boundary with scoped evidence below. Full R14–R17 remains
OPEN in R14-R17-COMPLETION.md. This slice connects original executable call
initialization to authenticated adoption, atomic acceptance, dispatch and durable
outcome handling. It does not complete cancellation, reduction, continuation,
scheduler execution or channel delivery.

## Scope and assembly

ExecutionDispatchRuntime combines executable Run history and the existing dispatch
custody in registered production/evaluation profile 13. Prior profiles retain their
routes and interpretation. The combined assembly explicitly uses the joined R14
loop reader; inheriting the legacy SQLite reader rejected fanout companion records.
Worker reads independently select the exact legacy or execution Run schema, active
head, owner session and non-scheduler fence. Scheduler/successor workers remain out
of this slice.

Resources retain the original provider, signing key, clock epoch, revocation and
one shared cap. observe() retains the PlanEffect policy; observe_call() selects the
fixed initialized-call policy. Its grant ID is the original stable grant ID plus
`/initialized-call.v1`. Capture chooses this policy from the authenticated immutable
intent origin and selected initialized/accepted call, never an arbitrary caller
policy. Historical validation preserves original signatures across revocation and
restart; current authorization still checks current custody and scope.

## Preview and atomic acceptance

The public preview authenticates the actual ingress peer, independently selects
original initialization/current Run and the complete preceding call inventory,
captures current authority and obtains the isolated effects precursor. Its exact
display, mandate, original Run, capture and exchange are retained in the existing
independent journal mechanism at `.call-previews`. Preview issues no writer or send
credential. Journal identity, physical database binding, key and semantic provenance
are checked; transplanted, missing, duplicate or malformed data fails closed.

Adoption contains only the exact preview bytes and a stable act ID. Stable identity
binds tenant, authenticated principal, operation and act. Selected exact replay runs
before fresh preview/resource/owner preparation; changed adoption bytes conflict.
Replay returns the original receipt and recovers original selected SQL bytes. It
cannot refresh a mandate or rerun preparation under a new owner epoch.

Fresh acceptance calls the isolated agent-loop owner and then effects owner. The
retained exchange includes both original requests/results/sessions, full capture,
original preview and adoption. The independent pure verifier checks the complete
ordered three-record graph: accepted call, execution intent, external effect intent.
A private writer authority admits only its own exact prepared objects, recaptures
sources and checks INITIALIZED/nonterminal branch, Run/fence, complete predecessor
inventory and original deadlines. The existing sole writer selects/materializes all
three records atomically; no provider call occurs inside the writer transaction.

The history reader merges selected loop and call-effect decisions in physical
expected-head order. At each acceptance it verifies original initialization/current
Run, the exact preceding complete inventory, retained owner graph and every physical
record/publication. It includes acceptance records without inventing a Run update.
The final inventory contains ConsequentialAcceptedCall and terminal ABSENT. Startup
validates selected issuance before recovery and then the complete materialized join.
Corrupt selected data raises typed, chained EffectsIntegrityError with operation,
tenant and record identity, rather than ordinary rejection or a partial projection.

## Dispatch, outcomes and shared accounting

AUTHORIZE and first SEND derive policy from original intent and require the exact
selected acceptance/external-intent reference. One-shot consumption uses the
original custody provider. Existing durable evidence and resolver routes reach
CONFIRMED/CLOSED and survive reopen without a replacement transfer.

The shared cap counts every historical SEND under either exact stable grant ID,
including closed work. Unknown work prevents fresh adoption in the same registered
scope. The actual mixed-policy tests cover both first-policy orders and both
confirmed/closed and lost-response outcomes, with exactly one SEND and transfer.
These effects outcomes do not yet terminalize/reduce the loop call.

## Bounded representations and computation reuse

The original full Run remains in DispatchCapture. Existing source version 2 keeps
its exact origin-dependent representation: full legacy Run, compact initialized-call
Run reference. Fresh PlanEffect intents only in the combined assembly use
runtime_and_fence source version 3: exact run ID/head/state and SHA256 of canonical
full Run, plus unchanged fence, sessions and loop head. Current and historical
interpretation derive the version from immutable original intent. Unknown versions
and attempts to override the original version reject. No DispatchCapture field,
authorization deadline, owner batch bound or IPC bound was changed.

IndependentTenantDecisionJournal reads complete current body bytes, verifies file
and key identity/content, and checks the live anchored head on every read. Only
when complete body bytes match its last successfully authenticated body does it
reuse the immutable decoded tuple. Any changed body runs the original complete
hash/predecessor/HMAC validation. One atomic immutable cache pair avoids torn reads;
failed reads cannot return cached success.

Three pure envelope builders (legacy fanout, executable fanout, v2 acceptance) reuse
at most one exact input/output byte pair each, limited to 4 MiB total per builder.
Public calls serialize their entire current input and decode a fresh output DTO.
Failures and oversize computations do not populate a cache. Validation/derivation
bodies are AST-identical to their previous implementations. No current authority,
physical readback, history join, clock, lease or revocation check is cached.

## Validation evidence

The following are bounded implementation checks, not full milestone completion:

- `uv run pytest -xq tests/composition/test_pure_history_reuse.py
  tests/composition/test_fanout_publication_records.py
  tests/composition/test_execution_fanout_records.py
  tests/composition/test_acceptance_v2_records.py`: 87 passed. Includes invalid
  input after a warm cache, output mutation, failure repetition and size bypass.
- `uv run pytest -xq tests/platform/test_journal_exact_body.py
  tests/platform/test_owner_journal_gate.py tests/platform/test_deployment_authority_gate.py
  tests/capabilities/deployment_trust/test_trust.py`: 60 passed. Includes same-size
  corruption with restored mtime, replaced body, changed/missing head and key,
  original-byte restoration, append invalidation and gate exclusion.
- `uv run pytest -xq tests/composition/test_call_dispatch_cap.py`: 4 passed in
  107.11 seconds before adding the final reopen assertions. Both closed orders
  reach second authorization and cap refusal; unknown orders reject adoption.
- Actual acceptance tests cover exact replay after expiry/revocation, changed-byte
  conflict, selected-before-SQL faults, startup recovery, missing companion failure,
  original owner output retention and no provider emission before SEND.
- `uv run mypy`: passed for 513 source files; `uv run ruff check`: passed.
- `uv run pytest -xq tests/architecture/test_r8_current_surfaces.py
  tests/verification/test_r8_surface.py`: 57 passed on the current source tree.
- `sh .harness/scripts/test.sh --preflight`: exited 0, including harness negative
  fixtures. Preflight is not the normal full commit gate.

The expanded combined regression passed its first 30 cases, including all mixed-history
reopen assertions and call preview/acceptance cases, then exposed a stale signature
in the legacy lease-expiry test's capture mock. The mock now accepts and verifies
intent_id, forwards it to the real capture and retains its original deadline and
no-selection assertions. No production behavior was changed for that failure.

`uv run pytest -xq tests/composition/test_dispatch_runtime.py
 tests/composition/test_call_batch.py tests/composition/test_execution_acceptance_inventory.py
 tests/composition/test_execution_effects_worker.py tests/platform/test_call_dispatch_resources.py`
passed all 37 cases in 126.96 seconds after that repair. Original frozen source/test
hashes matched before the test-only edit. Cold staged review and the normal commit
gate are separate publication requirements.

Cold plan reviews covered atomic acceptance/history joining, exact-body journal
reuse, versioned Run references and pure envelope reuse. They found no remaining
P0/P1 plan defects. These reviews are separate from implementation verification and
retain the shared-model limitation.

## Integration and remaining scope

Feature commit ca406c7 passed its normal full gate: `uv run pytest` reported
2301 passed and 455 deselected in 1546.93 seconds. Its tree is
11a740a9c0cfb024b18985435a8e31b857b343df. The merge combines it with published
master f9790f7 and preserves the extracted `r14_fanout_verification` boundary:
exact-byte reuse wraps that pure builder, while the facade and original validation
remain unchanged. Source inventory and hashes match the resolved tree exactly.

`uv run pytest -xq tests/composition/test_call_provenance_integration.py
 tests/composition/test_repeated_ingress.py tests/composition/test_pure_history_reuse.py
 tests/composition/test_fanout_publication_records.py` passed 46 tests in 22.66 seconds.
The new runtime witness accepts the first consequential call, captures and seals a
second Run with identical ingress text, preserves distinct original call/provenance
identities and the exact first acceptance references, and verifies complete selected
history after reopening. Initial fixture setup errors were repaired by explicitly
supplying the required hermetic resource budget/custody. No production repair was
needed. The integration regression command `uv run pytest -xq
 tests/composition/test_call_preview_runtime.py tests/composition/test_call_dispatch_cap.py
 tests/composition/test_dispatch_runtime.py tests/architecture/test_r8_current_surfaces.py
 tests/verification/test_r8_surface.py` passed 89 tests in 332.08 seconds, with source
and test bytes unchanged throughout. `uv run mypy`, `uv run ruff check` and
`uv run ruff format --check` passed. Mypy required an explicit accepted-branch
assertion in the new witness; that witness passed again after the test-only repair.
Cold staged review and the normal merge gate are separate publication checks.

Next R14 boundary is actual execution-schema cancellation and acceptance/cancellation
competition. Full call reduction, frontier, resume/successor, read-only retry budgets,
post-terminal work and model-attempt recovery remain open. R15 scheduling execution,
R16 remaining recovery/compensation and R17 complete ingress/admission/quarantine,
exact delivery and CLI/Telegram parity remain required by the completion ledger.
No retrospective or live external sending is included.
