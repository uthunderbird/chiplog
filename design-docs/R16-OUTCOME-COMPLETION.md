# R16 original-stream outcomes and reconciliation

Scope: the existing v2 first-child route after `SEND_COMMITTED`. This is a
completion slice, not full R16. Root integrates and owns final review/commit;
this worktree preserves all earlier worktrees and touches no shared rule.

## Context and plan

Sources: `IMPLEMENTATION-ROADMAP.md` R16/Common DoD, `VISION.md` external-action
failure protocol, `project-architecture/NORMATIVE.md` independent evidence and
two-commit recovery, the existing dispatch authority/history/outbox, and the
hermetic adapter's signed original-ticket receipts. The existing code consumed a
single durable send permit but returned receipt bytes without durable outcome.

Plan, reviewed against the existing cold-reviewed overall completion plan:

1. Freeze a separately versioned effects outcome contract and public consumer.
2. Route owner-produced evidence/reconciliation records through the existing
   effects process and physical writer. Keep original first-send bytes unchanged.
3. Select `BOUNDARY_CROSSED` / `OUTCOME_UNKNOWN` with an original OPEN obligation
   before consumption or provider I/O. A lost reply leaves that denying record.
4. Authenticate receipt bytes against the original selected/consumed ticket and
   independent retained receipt key; select evidence separately from closure.
5. Close only through a currently authenticated original-principal resolver using
   the exact current attempt, complete original children and durable evidence.
6. Verify actual runtime/selected history, fault edges and independent OS reopen.

Assumptions: only the registered first child exists on this route. The hermetic
provider's signed sequence is its replay identity under original key custody.
No real provider entitlement, retry, duplicate-risk grant or compensation is
created. Fresh-process custody is explicit through `custody_path`; omitted paths
remain the original in-memory fixture profile. Reopen assigns a fresh clock epoch,
so persisted keys do not renew an old mandate's SEND horizon.

Additional slice cold review could not run because orchestration rejected it;
no pass is claimed. Root inspected actual source and required directory fsync,
private descriptor validation, current caller checks at replay release, and
explicit mixed transition traces. Those findings were repaired. Same-model
inspection is not independent external verification.

## Contracts and downstream seams

`dispatch_outcome_contracts.py` is inert. APPEND_EVIDENCE and RESOLVE_OBLIGATION
are distinct operations. Their preparation contains the original selected SEND,
exact predecessor and original intent. Snapshots retain canonical evidence heads
and an addressable original obligation. Its identity never migrates with a Run.
The original Run/call is available through the immutable mandate origin; PlanEffect
has its distinct origin. R14 must consume the outcome record/evidence heads and
original obligation head at one authenticated cut and publish its separate
original-call recovery outcome. No such R14 authority is synthesized here.

Normal emission takes SEND → UNKNOWN/OPEN → authenticated terminal fact/OPEN →
resolver/CLOSED. Fully known mixed evidence from UNKNOWN becomes PARTIAL_CONFIRMED;
a direct receipt at bare SEND instead takes PARTIAL first, then terminal resolution.
Exact duplicate evidence returns original bytes. A new compatible late receipt is
appended; a rival late receipt remains addressable, preserves the original terminal
claim and sets a conflict that keeps downstream action held, even after closure.

The writer retains owner bytes, verifies a private resource-custodied issuance,
rechecks the exact materialized cut and rejects cloned preparation identity.
Current receipt authentication does not require an ACTIVE Run, live old worker,
or current SEND grant. Resolver authentication is checked at its write/replay
release boundary. Provider reads never emit; absence is not no-effect proof.

Safe retransmission needs a subsequent contract for every later selected child,
not substitution into this first-child interpreter. Compensation/duplicate-risk
preview, R14 execution recovery and R17 authenticated delivery remain root-owned
completion work. Full preimages make histories expensive; no broader wire-size or
unbounded multi-intent claim follows from this slice.

## Meaningful guarantees and required evidence (all HOLD before execution)

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Evidence path |
|---|---|---|---|---|---|
| Original exact evidence | immutable selected SEND + consumed ticket + retained signed raw receipt | independent provider transfer log and selected/materialized journal | caller receipt DTO, caller key, current target alias | foreign key/account/child/payload; valid HMAC but wrong ticket | tests/composition/test_dispatch_outcomes.py |
| Evidence survives Run death | original intent, child, key custody | append after Run terminal/reopen/revocation | ACTIVE Run or execution lease as evidence authority | terminal Run / replaced resources | tests/composition/test_dispatch_outcomes.py |
| No blind retry | stable consumption child and UNKNOWN obligation | transfer count unchanged after replay/reopen/provider read | absence as no-effect; fresh replacement | response lost after effect; crash before leaf; absence | tests/composition/test_dispatch_outcomes.py |
| Evidence before closure | separate EVIDENCE_RECORDED and RECONCILED selected decisions | crash/reopen between both; open obligation remains denying | automatic receipt-to-closed-obligation inference | evidence present/open; closure stale head race | tests/composition/test_dispatch_outcomes.py |
| Complete terminal reduction | exact ordered original children and retained evidence | owner result reproduced from original selected cut | latest-only receipt, timestamp selection | omission/duplication/reorder/conflicting terminal | tests/capabilities/effects/test_dispatch_outcomes.py |
| Original interpreter | separately registered outcome interpreter plus immutable original dispatch semantics | startup old selected output compared byte-for-byte | rewriting first-send serializer/version | changed schema/version/canonical bytes | tests/composition/test_dispatch_outcomes.py |

Mutation families: omission (child/evidence), unknown addition (closed DTO/enum),
substitution/alias (ticket/key/resources), duplicate/reorder (history/evidence),
stale/race (writer cut), boundary N/N+1 (one registered child versus extra child),
logical/physical mismatch (selected versus SQL bytes). No family is N/A.

Scope budget: approximately 18 production/test/doc files plus harness handoff and
source-bound verification catalog if required; reserve to 35, hard stop 40.
No inherited dirty files in this new worktree. Root's completion ledger is separate.
Historical profile-7 driver retains its old route; profile-10 gains only the outcome
route. Source-bound final catalogs must be regenerated only from frozen integrated
source and measured tests, never repinned to silence preflight.

## Executed evidence

- Public full-preparation/reducer tests: 12 passed (`uv run pytest -q tests/capabilities/effects/test_dispatch_outcomes.py`).
- Custody, closed route partition and contract tests: 14 passed before the two additional mixed/no-effect reducer cases.
- Runtime subset: `uv run pytest -xq tests/composition/test_dispatch_outcomes.py tests/composition/test_dispatch_runtime.py -k "lost_reply_original_evidence or actual_adoption_first_send_consumes_once"` — 3 passed, 8 deselected in 284.13s. It exercised lost reply, independent OS reopen, separate closure/replay, changed receipt denial, dead worker/revoked grant, and both existing consumption crash variants.
- Source + new tests typecheck: `uv run mypy src/chiplog tests/composition/test_dispatch_outcomes.py tests/composition/test_dispatch_runtime.py tests/capabilities/effects/test_dispatch_outcomes.py tests/capabilities/effects/support/dispatch_outcomes.py tests/support/dispatch.py tests/capabilities/effects/test_dispatch_outcome_contracts.py tests/platform/test_dispatch_custody.py` — success.
- Latest runtime selection: `uv run pytest -xq tests/composition/test_dispatch_outcomes.py tests/composition/test_dispatch_runtime.py -k 'lost_reply_original_evidence or signed_provider_partition or (actual_adoption_first_send_consumes_once and True)'` — 4 passed, 9 deselected in 369.54s. Includes blind replacement denial, explicit absence-close denial, permanent/MIXED outcomes, cloned preparation/resource substitution, and actual generation change during resolver preparation. Terminal-Run evidence beyond dead-worker restart is an R14 integration witness still required.
- Final quick suite: `uv run pytest -q tests/platform/test_effects_hermetic.py tests/platform/test_dispatch_custody.py tests/capabilities/effects/test_dispatch_outcome_contracts.py tests/capabilities/effects/test_dispatch_outcomes.py` — 30 passed in 0.28s.
- A prior resolver run failed with final authentication at monotonic `322480876112875`, past deadline `322480843948291` (32ms); its previous authentication was valid at `322480547139041`. No deadline budget was widened. Preparation now reauthenticates and compares the full frozen trust cut, request policy/budget semantics, caller/callee generations and sessions; only observation nonce and deadline may refresh. Current authentication still runs at final write. Exact replay queries additionally bind original command bytes, operation and identity. Final targeted runtime `uv run pytest -xq tests/composition/test_dispatch_outcomes.py -k 'signed_provider_partition and PERMANENT_NO_EFFECT'` passed: 1 passed, 2 deselected in 82.63s, including reached mutations of each query dimension.
- Initial preflight passed. Intermediate preflight failed the corrupt-projection reproducer while the candidate source catalog was stale. Final frozen-source `sh .harness/scripts/test.sh --preflight` passed (exit 0), retaining that adversarial fixture unchanged.

Revocation custody follow-up: the persisted `.revoked` marker now fsyncs its parent after the file. The reached fault test injects directory-sync failure, observes file→directory ordering and a raised failure, checks revoked grant rejection in original/reopened resources, then checks successful repeat synchronization. Affected quick suite passed 31 tests in 0.32s; frozen preflight passed again after this change. No power-loss simulation is claimed.

Test-layout follow-up: shared builders live in `tests/support/dispatch.py`, outcome-local builders in `tests/capabilities/effects/support/dispatch_outcomes.py`. No new module imports a test module. AST comparisons preserve all moved builders and affected test functions, and before/after collection preserves all 81 nodeids. The affected quick suite passed 68 tests; Ruff, mypy (246 files), and `tests_layout.py --worktree` passed. Frozen preflight passed again. Production source and its catalog did not change during extraction.

Cold staged review found that a second already-open custody holder retained ACTIVE
authority after another holder revoked the same durable grant. The reached two-holder
reproducer still resolved a recipient after revocation. Each current observation now
checks the durable marker under the authority gate and latches revocation; only
FileNotFoundError means absence, while other read failures deny the operation.
Historical signature verification remains available. Regression tests cover another
live holder, a separate OS-process revoker, recipient rejection, historical reads,
marker disappearance after observation, and unreadable revocation storage. All six
custody tests and full mypy (449 files) pass. This does not claim resistance to a
malicious rollback before a holder has ever observed the marker.

Source catalog updates are constrained to inspected changed/new Python paths; all other mappings and complete inventory are asserted unchanged. Catalog hashes identify the tested candidate, not behavioral evidence.

The ordinary commit gate rejected four manual descriptor lifetimes. They now use
separate ExitStack scopes, preserving file fsync/close before directory fsync/close
and retaining the authority lock. The resource check, mypy and custody/provider
suite passed (`uv run pytest -xq tests/platform/test_dispatch_custody.py
tests/platform/test_effects_hermetic.py`: 20 passed). Cold delta review found no
P0/P1; its wider runtime run then exposed a pre-existing slice regression:
`test_same_contract_live_dependency_substitution_never_reaches_leaf[after_send]`
observed zero consumption records instead of one. That run ended 1 failed,
75 passed; it is not a passing candidate gate.

Boundary uncertainty was attempting to require a live original provider before
consumption. Rejecting that early left the selected SEND unconsumed and able to
emit after provider restoration. Only APPEND_EVIDENCE/BOUNDARY_CROSSED now signs
and admits from original selected SEND and pinned custody without live-provider
identity. Provider facts, reconciliation and leaf invocation retain their checks.
Signature verification alone uses the retained key; full historical receipt
validation still checks receipt custody. No provider availability is inferred from
a boundary signature, and its selected SEND/owner output remain independently
validated before writing.

The unchanged failing case passed (`uv run pytest -xq
tests/composition/test_dispatch_runtime.py -k 'substitution and after_send'`:
1 passed, 9 deselected, 53.31s). New owner-exchange races passed (`uv run pytest
-xq tests/composition/test_dispatch_outcomes.py -k custody_during`: 3 passed,
3 deselected, 146.53s): provider substitution during boundary preparation retains
UNKNOWN/OPEN then consumes once without I/O; resource substitution admits nothing;
provider substitution during explicit resolution admits nothing. Restoring the
provider cannot emit the consumed attempt. Boundary signature mutation rejects,
and the boundary signer rejects a resolution command. Normal commit gate and
review of this final semantic repair remain required.
