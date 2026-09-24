# Execution-call cancellation

Status: contract candidate; runtime publication and acceptance/cancellation races
remain HOLD. Base: published 5c46409. Full R14–R17 scope stays in
R14-R17-COMPLETION.md. No retrospective or live provider operation is included.

## Context and boundary

NORMATIVE.md, under project-architecture, requires a cancellation terminal and typed
NOT_EXECUTED result against the exact still-unaccepted initialized predecessor.
Run terminal accounting is separate (roadmap A45/A48/A50/A55/A56). An accepted
uncertain call cannot become NOT_EXECUTED. ExecutionRunRecord retains initialized
references whose independent lifecycle streams supply results; per-call cancellation
therefore does not invent a Run transition. Legacy cancellation continues to return
its existing RunRecord companion and retain its three-member physical envelope.

Public boundary: `ExecutionCallCancellationPort.cancel_execution_call` accepts the
existing authority-free CancelCallSubmission and returns ExecutionCancelledCallReceipt.
Receipt fields bind original initialized, terminal/result and the original selected
complete request identity/fingerprint. Construction and roundtrip are not selection.
Authenticated exact replay returns the original receipt even after Run advancement.
Publication uncertainty remains recoverable and is never proof of non-selection.

RetainedExecutionCancellationPreparation stores original act/trust, owner request
and result, ExecutionRun predecessor and snapshot fingerprint. Its request cut stores
complete preceding inventory and independent materialization commitment. The new
physical envelope names a distinct operation and exactly two ordered owner records,
terminal then NOT_EXECUTED. No Run companion or caller-provided permission is admitted.
Runtime must verify exact bytes, schemas, references, source cut and total serialized
bound; field cardinality alone proves none of those semantic properties.

## Plan and assumptions

1. Materialize separate versioned contracts and test public consumers without
   introducing publication business logic; keep historical types unchanged.
2. Register only reviewed new source bytes in the exact inventory, run types/style,
   source checks and preflight, then cold staged review and normal commit gate.
3. In the following implementation phase, reuse the authenticated loop journal and
   sole appender; extend selected-history/startup validation and public runtime.
   Fold acceptance/cancellation by actual predecessor cuts and prove both race orders,
   exact replay, crash/reopen and physical integrity through real owner processes.

The clean worktree starts from the published integration. No inherited dirty files
are included. Estimated scope is 5–7 paths including documentation, tests, fixtures
and source catalog; hard limit is 40. Canonical entrypoints remain unchanged in this
contract phase. Existing hermetic CLI policy covers own ACTIVE nonscheduler calls;
this is not general R17 ingress or scheduler cancellation. Source/test bytes will be
frozen for verification. Cold plan review found no P0/P1; root inspected its findings.
Cold context retains the shared-model ceiling.

## Guarantee-to-evidence map

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixtures | Evidence |
|---|---|---|---|---|---|
| Exact unaccepted branch | original initialized + Run + complete inventory | selected/physical history has one winner | embedded terminal or shape validity | cancel/accept both orders, stale Run, foreign call | runtime HOLD |
| Atomic pair | owner-authored terminal/result ordered bytes | journal and exact SQL membership | regenerated owner result or digest-only count | omit/add/reorder/duplicate/alias, whole encoded request N/N+1 | envelope/crash HOLD |
| Authenticated act | actual original trust and IPC plus private live admission | changed peer/source/session denies selection | caller credential DTO or historical trust as current authority | wrong tenant/principal/database, revoke/expiry | runtime HOLD |
| Stable replay | exact original act, batch and receipt | restart preserves complete receipt/history | renewed act, fresh owner output or fabricated Run edge | changed bytes, Run advancement, partial materialization | runtime HOLD |
| Historical separation | distinct retained/envelope/receipt discriminants | old/new consumers reject substitution | widening old RunRecord | unknown version, legacy/new envelope and retained substitution | public consumer tests passed; mixed runtime HOLD |

All mutation families apply: omission, addition/unknown, substitution/alias,
duplication/reordering, stale/race, N/N+1 and logical/physical mismatch. No family is
waived. Bound checks include complete encoding, canonical member order and reference
closure, not just record count. Receipt publication fields identify the exact selected
request binding retained act/preparation and physical pair, never only the displayed DTO.

## Runtime acceptance criteria still open

Fresh selection independently authenticates current peer, original Run/Turn/initialized
identity and complete predecessor inventory; source/deadline/worker/database/anchor
recapture occurs at the sole writer. Owner preparation happens outside the writer gate.
Selected decoding precedes physical recovery and interprets original retained bytes.
History rejects unknown/substituted operations and omitted or extra physical companions.
Replay checks original submitted bytes and current caller, recovers only selected bytes,
and validates complete history before returning. It never refreshes the original act.

Required histories: both cancel/accept orders, actual two-process competition,
accepted lost-response remaining uncertain, changed current Run, wrong act/subject/peer,
revocation/expiry/stale worker, journal-before-SQL and SQL-before-reply failures,
restart, corrupt/omitted companions and mixed legacy/execution history. Subsequent
call accounting, reduction, continuation, frontier and Run terminalization remain open.

## Contract validation

`uv run pytest -xq tests/composition/test_execution_cancellation_contracts.py
 tests/composition/test_cancellation_publication_contracts.py` passed 29 tests.
They exercise a public Protocol consumer returning the receipt, exact binary
retention and original IPC request/result bytes, required original evidence, rejected
legacy discriminants/Run companion/extra authority claims, and two-member transport
cardinality. The inert fixture intentionally supplies no authentication or semantic
issuance; shape acceptance cannot authorize its values.

`uv run mypy src/chiplog/composition/r14_execution_cancellation_contracts.py
 tests/composition/test_execution_cancellation_contracts.py
 tests/support/execution_cancellation.py` and ruff on those files passed.
`uv run pytest -xq tests/architecture/test_r8_current_surfaces.py
 tests/verification/test_r8_surface.py` passed 57 tests. Only the new reviewed source
module was added to the catalog; full Python inventory and existing hashes matched.
Initial preflight failed in the fresh environment; after `uv sync --frozen`, the
required gate self-test and preflight both passed without changing the checks.
Normal staged cold review and full commit gate remain separate publication checks.
