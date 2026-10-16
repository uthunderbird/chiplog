# R14.7 preparation: executable read-only lineage

**Route/scope update, 2026-10-17:** the user's
[functional scope](R14-FUNCTIONAL-SCOPE.md) supersedes the full-proof prerequisites
below. Mount lean, distinctly versioned accepted/pending/counter semantics in the
new native PostgreSQL execution runtime, reusing V3 tool/Run DTOs. Keep max 3,
atomic acceptance before reading, stable original snapshot/cursor across reopen,
current authorization and pending-accounting HOLD. Do not fabricate V1 proof
objects or require a parallel SQLite/R7 V3 mount. Source-backed implementation
plan: `/private/tmp/r14-readonly-minimal-plan.md`.

Current status: **R14.7 functional acceptance CLOSED** in integrated runtime
`b514e8a`. The combined checkpoint passed 235 tests in 63.75 s; successful and
max-3-exhausted successor lineages reach actual CompleteAcceptance/reopen.
Current carried-RIVAL admission and pending HOLD checks passed. Exact commands,
scope and retained backlog are in `R14-VERIFICATION-STATUS.md`.

Earlier mounting evidence:
Checkpoint `b1470f5` in the separate `feat/postgres-per-user` worktree mounts
actual V3 history reads, atomic accepted attempts, a max-3 counter across reopen,
stable snapshots, definite retryable PostgreSQL read failures and pending
continuation HOLD. Root's independent 20-test checkpoint selection passed in
8.49 s; exact command and limits are in `R14-VERIFICATION-STATUS.md`.
Follow-up checkpoint `b5f0671` mounts shared-counter successors, inherited sealed
Turns and ordinary model continuation after retry terminalization. Its 13-test
checkpoint selection passed in 11.25 s; cold functional red-team found no confirmed
P0/P1. Exact commands, independent checks and their limits are recorded in the
verification ledger. At PostgreSQL checkpoint `e2f6f96`, completion/delivery,
abort/suspend/resume and model recovery are mounted. Root's independent native
35-case convergence selection passed in 19.05 s. Checkpoint `6a64a06` fixes the
carried-old-effect late-RIVAL admission regression after mounted RED on both
retry routes, and adds terminal-lineage CompleteAcceptance/reopen coverage.
Root's complete native selection passed 88 tests in 101.29 s. Cold review and
final integrated acceptance remain open. Do not read the historical preparation below
as evidence that these native routes are absent.
The proof-heavy preparation and planned
HOLD matrix below are historical context, superseded where the functional scope
defers them; they do not negate the mounted native slice.

Prepared on `ed4b8bf` plus the separate R8 open-verifier import repair. This
document adds no policy beyond the user's frozen `max_attempts=3`, including the
initial attempt. Budget-version registration is still required before allocation.

## Goal, prerequisites and boundaries

Mount the existing read-only attempt/outcome/pending/reduction contracts through
the actual execution flow, immutable history, isolated agent-loop preparation
and sole-writer CAS. Preserve one original lineage and counter across restart
and successor; a model tool name or a supplied proof DTO grants no authority.
No provider SEND belongs to this query path.

R14.6 has two prerequisite subjects: the accepted/cancelled foundation and the
conditional selected-read-only accounting clause. The former still needs its
full two-seal/interruption/interleaving evidence; the latter can only receive a
legitimate mounted subject after R14.7. Neither is declared complete. Preparation
may proceed, but no new runtime admission is justified by this document.

Sources: `R14-REMAINING-ROADMAP.md` R14.7, `R14-CONTRACT-FREEZE.md`,
`R14-R17-INTEGRATED-CONTRACTS.md` read-only/source/physical-codec sections,
`project-architecture/NORMATIVE.md`, `HEXAGONAL-CODE-LAYOUT.md`, and the executable
contracts listed below. Current runtime evidence remains in
`R14-VERIFICATION-STATUS.md`; planned evidence below is **HOLD** until executed.

## Existing owned boundaries

Paths in this table are under `src/chiplog/capabilities/agent_loop/`.

| Surface | Owner, inputs and outputs | Current limit |
|---|---|---|
| `readonly_history_tool_contracts.py` | Model supplies only canonical query arguments (`limit`, `after_cursor`); broker supplies principal, contour and snapshot authority | No query execution or positive no-mutation proof |
| `execution_history_contracts.py`, `execution_history_transition_contracts.py`, `execution_history_fan_out_contracts.py` | Explicit V3 artifact/response/Run and transition/fanout request/result unions | DTO containment only; do not add the tool to frozen V2 |
| `fan_out_contracts.py`, `call_acceptance_contracts.py` | Registered classification and retry policy; original initialized call retains `InitializedReadOnlyLineage` | Current mounted V2 registry has no READ_ONLY entry |
| `readonly_execution_contracts.py` | `ReadOnlyExecutionPreparationPort.prepare_readonly` accepts attempt/outcome/pending/reduction requests; returns their typed proposals or `CallPreparationRejected` | Port exists; no isolated executable owner route is mounted |
| `recovery_source_contracts.py` | Broker-owned `ReadOnlyHistoryProofSource` retains exact query/snapshot/implementation/registration plus external tenant/database/principal/contour expectations | Decoder proves representation, not issuer authenticity or immutable execution |
| `readonly_source_contracts.py` | Complete lineage/physical manifests, selected evidence/reducer/pending/successor wrappers | Caller must independently authenticate selection and current sources |
| `readonly_record_contracts.py` | Closed physical codecs, including acyclic pending body and separately projected frontier | Physical membership/selection still require the writer and reader |
| `readonly_assembly_contracts.py` | Public pure validators join selected counter, native ACTIVE Run, accepted/outcome sources and exact physical members | Pure joins cannot select or publish records |
| `recovery_contracts.py`, `successor_record_contracts.py` | Run/worker fences, applicability and selected successor initialization | Scheduler live-lease and non-scheduler applicability remain distinct; a copied reference is not current authority |

Reuse these public boundaries before inventing another protocol. No new public
driver method, live publication envelope, operation identifier or budget-version
identity is silently frozen here. Materialize any missing boundary in a focused
contract slice, with a public consumer, before its corresponding business logic.

## Guarantee and requirement-to-evidence map

All rows are runtime **HOLD**. Existing pure tests are supporting evidence only.
Planned test modules live under `tests/composition/`; names describe future
witnesses and are not assertions that those files already exist.

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture / planned evidence |
|---|---|---|---|---|
| Positive read-only classification | Registered implementation/schema/policy and broker history proof; exact canonical query, tenant, principal, contour and selected immutable snapshot | Actual query uses the registered read path, preserves source bytes, emits no write/effect; changed implementation/proof/snapshot rejected | Tool name, `readonly=True`, model-supplied digest, caller-selected snapshot | Mounted proof mismatch and hidden-write controls; `test_r14_readonly_runtime.py` |
| Frozen bound and one lineage | Original initialized call, registered budget version and max 3; reducer identity/version and shared counter | Original lineage/counter identity survives reopened and successor Runs; accepted ordinals 0,1,2; fourth attempt rejected | Reallocated counter/lineage or treating a fixture number as registration | Bound N/N+1, changed budget/reducer, restart; `test_r14_readonly_lineage.py` |
| Exact progress and referential closure | Complete ordered accepted attempts and their immutable predecessor outcomes | Each new ordinal consumes the exact prior definite retryable outcome, once; no gaps or unaccounted predecessor | Last-attempt-only history, success/unknown/open-obligation retry | Missing/extra/reordered attempts, duplicate ordinal, post-terminal and exhausted retries; lineage witness |
| ACTIVE Run and worker applicability before initialization | Authenticated selected current Run plus its applicable live fence/lease and actor/session | Stale/absent Run, lease or worker changes select zero initialized/counter/pending rows; recheck at the writer cut | An earlier observation or copied fence becoming authority | Both guard cuts and stale races; `test_r14_readonly_publication.py` |
| Immutable attempt outcome | Original accepted attempt and independently selected canonical evidence | Exactly one typed outcome joins query/snapshot/implementation/proof and selected acceptance after reopen | Caller outcome flag, rewritten attempt, foreign selected decision | Cross-attempt/evidence substitution, duplicate outcomes, physical mismatch; runtime/history witness |
| Atomic call-level reduction | Complete ordered outcome heads, registered reducer, call outcome, terminal frontier and closed counter | All terminal companions appear together once, with exhaustion provenance where applicable | Partial closure, rewriting prior results or inferring terminal from last attempt alone | Crash before/after COMMIT, lost ACK, wrong reducer; `test_r14_readonly_reduction.py` |
| Pending cannot satisfy terminal gates | Exact pending body/frontier, ABSENT call-level outcome/terminal and immutable lineage | Accounting, later Turn, CompleteAcceptance and terminal Run remain HOLD; same-Run cannot consume pending | Filtering the read-only member or treating pending as terminal | Terminalized siblings plus one pending read-only member; whole-seal no-write and reopen witness |
| Restricted pending-only successor admission | Complete relevant subject inventory and closed-action-lineage proof before initialization | Exactly the selected read-only pending head is actionable; all other required subjects independently ABSENT/N/A/closed without unresolved obligations/no-retry | Pending DTO alone, partial inventory, a generic successor reference | Unknown/mixed/missing subjects HOLD before successor initialization; `test_r14_readonly_successor.py` |
| One complete CAS branch per pending head | Exact old pending selection and shared counter | Atomically close old head as NEXT_ATTEMPT_ACCEPTED, advance counter and select successor pending; or publish complete terminal/recovery branch | A branchless interval, both branches, same-Run consumption, ordinal gap | Both branch orders, stale/rival CAS, crash/reopen on both sides; successor/publication witness |
| Complete physical and versioned history | Sole-writer exact batch, selected decision, source stamp and registered durable-version tuple | Canonical reopen proves exact full membership, no orphan/extra row and no partial recovery publication | Selected-only startup pass, logical head used as physical identity, unsupported tuple accepted by schema alone | Corrupt/extra/foreign rows, unknown family/version, lost ACK and unchanged-head replay; history witness |

Required mutation families for this milestone: omission; addition/unknown value;
substitution/alias; duplicate/reorder; stale/race; numeric and byte N/N+1;
logical/physical identity mismatch. None is N/A. Pending/terminal variants and
all source/record byte limits must be exercised independently; max attempts does
not bound retained-history work or replace A54 request/envelope limits.

## Semantic implementation splits and review budget

1. Existing public contract validation and this context; owner preparation for
   the smallest same-Run first-attempt/outcome candidate comes only after its
   exact registered proof and counter inputs are represented. No runtime grant
   follows from a pure candidate.
2. Explicit V3 model/transition/fanout registration, original lineage/counter
   allocation and actual immutable query/proof source; typed retained evidence
   and selected history; broker/public driver boundary defined before mounting.
3. Attempt/outcome/reduction sole-writer publication, both fresh source/actor
   guard cuts, physical CAS, failure recovery and reopen.
4. Pending publication and restricted successor-only consumption, complete
   inventory admission and atomic replacement/terminal branch. Do not defer the
   minimum successor seam required by R14.7 to R14.9; R14.9 later broadens it.
5. Return the real selected read-only member to R14.6 accounting controls and
   R14.8 continuation gates, then include the full R14.13 convergence audit.

Estimate each code split before edits, aiming at 8–10 owned paths including
tests, status/evidence and any review artifacts, not one whole-milestone diff.
The initial freeze reserves 35 dirty paths; `.harness/scripts/thresholds.sh`
sets soft 10/hard 40. `git status --porcelain=v1 | wc -l` returned 19 before this
artifact: inherited root-owned observations/status, the separate R8 repair/test,
and unrelated untracked files. Preserve the latter. Count the union before
each split; do not split logic merely to evade a limit.

The legacy fixture drivers (`tests/support/native_cancelled.py`, mounted V2
execution fanout and common CLI fixtures) retain their historical entrypoints.
Inspect them before any canonical V3 entrypoint change; neither changing a
fixture's API nor casting a V2 Run creates V3 executable evidence.

Final source identity is bound by `src/chiplog/inert_shared/r8-implementation-v1.json`
and `verification/r8_surface.py`. Checkpoints use staged structure/import checks
without repinning; final integration requires a stable, fully reviewed source
snapshot, strict catalog reconciliation and full tests. An isolated checkout or
preflight pass alone does not freeze implementation bytes. The active saved
continuation is diagnostic evidence, not this final frozen-source proof.

## Executed preparation checks and remaining HOLDs

`sh .harness/scripts/test.sh --preflight` initially exposed the missing R8
open-verifier imports. The real malformed-source regression was red with
NameError, then green after restoring the imports; preflight and the corruption
reproducer both exited 0. No catalog repin or verifier substitute was used.

Public contract consumers:
`uv run pytest -q tests/capabilities/agent_loop/test_readonly_execution_contracts.py
tests/capabilities/agent_loop/test_readonly_source_contracts.py
tests/capabilities/agent_loop/test_readonly_record_contracts.py
tests/capabilities/agent_loop/test_readonly_assembly_contracts.py
tests/capabilities/agent_loop/test_execution_history_contracts.py --tb=short`
— 44 passed in 0.41s. These validate expressibility and pure joins through public
models/validators. They do not prove registered max-3 policy, selected authority,
query execution, isolated owner routing, allocation, CAS or mounted V3 history.

Preparation does not implement business logic. All runtime rows above, missing
public driver/envelope boundaries, R14.6 remaining evidence and R14.7 allocation
remain HOLD. No further user decision is needed to choose the numeric bound;
registration and executable proofs are engineering work still to do.
