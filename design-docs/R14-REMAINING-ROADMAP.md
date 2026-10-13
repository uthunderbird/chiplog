# Remaining R14 implementation roadmap

Status: OPEN. Planning snapshot: `967d5d0` (2026-10-13). This document plans only
remaining R14 work. It does not change normative requirements, discharge a ledger
row, authorize R15–R17 implementation, or choose a retry budget.

## Sources, numbering and assumptions

Authority: `IMPLEMENTATION-ROADMAP.md` R14 and common DoD,
`project-architecture/NORMATIVE.md`, and current R14 contracts. Current scope and
bounded evidence are recorded in `R14-R17-COMPLETION.md`; its OPEN rows are not
made complete by the presence of tests. Chronological contract notes describing
an earlier unmounted component must be checked against current source.

Numbering was inspected with a Python regex `\bR14\.(\d+)\b` over every current
`design-docs/**/*.md` and the output of
`git log --all -p -- design-docs`. Both sets were `[1, 2, 3, 4, 5]`.
`git log --all --format='%h %s' --grep='R14\.'` found no additional numbered
subjects. Therefore the first unused identifier is **R14.6**. New numbers below
are implementation slices mapped to the original R14.1–R14.5, not replacements
for those requirements. This establishes numbering in available local history;
unfetched remote history was not inspected.

Assumptions: existing physical writer, authenticated retained sources and owner
processes remain the publication boundary; hermetic adapters remain the evidence
environment. Existing later-item records may supply original evidence without
expanding implementation into those items. A prerequisite requiring later-item
implementation is a dependency conflict to surface, not permission to proceed.

## Existing evidence to preserve

- Native executable fanout and accept/cancel competition are mounted; retain
  `tests/composition/test_execution_call_branch_competition.py` and existing
  legacy generator/schema separation.
- Accepted-open original resolution and semantic reduction are mounted in
  `tests/composition/test_r14_original_resolution_mounted.py`. Compatible evidence
  can advance reduction; rival evidence holds. This is not broad convergence.
- Cancellation-only same-Run resume and authenticated successor are mounted in
  `test_r14_execution_suspension.py` and `test_r14_execution_successor.py` under
  `tests/composition/`; their empty obligation/retry references do not establish
  preservation of nonempty references.
- `tests/composition/test_r14_direct_terminal_next_turn_mounted.py` observes one
  reopened same-Run Turn 2 captured and sealed. The selected ingress manifest and
  CLI successor writer classification were subsequently tightened in `fc244ab`
  and `d24f666`. Preserve these paths rather than rebuilding them.
- Read-only execution, post-terminal work and model replacement have reusable
  contracts; `model_attempt_recovery_preparation.py` expressly leaves source
  authentication and final writer CAS outside its inert producer.

## Ordered remaining slices

All paths below are relative to the repository. Existing tests are starting
points, not claims that the missing histories already exist. New test selectors
must be chosen after executable requirements are frozen for each slice.

### R14.6 — Sealed-call accounting foundation (original R14.1)

Depends on existing fanout/acceptance publications. Work at
`src/chiplog/composition/r14_execution_inventory.py`,
`r14_native_accounting_history.py`, `r14_native_accounting_publication.py`,
`r14_accepted_open_accounting_publication.py`, and agent-loop
`native_one_call_accounting_preparation.py` (`prepare_native_one_call_cancelled_accounting_v2`).
The present one-call preparation is a reuse boundary, not full-fanout accounting.

Enumerate every original initialized member from the selected seal; retain exact
ordered bytes and exactly one admissible branch for the currently executable
accepted/cancelled paths. Do not publish a durable read-only pending record
before R14.7 can allocate its required lineage. **Clarification:** a
read-only call may be omitted before fanout selection only where the existing
policy already omits it. Once a selected seal contains a read-only member, hold
the entire accounting projection/publication; never filter that member or publish
partial accounting. Do not initialize it or allocate its lineage/pending record
while `max_attempts` is unset. Join all earlier-call terminal/result/obligation
records. Do not turn a recovered outcome into a rewritten terminal disposition.
Observe actual selected and physical members after reopen, not only proposed DTOs.
Publish one new-version accounting record per selected seal, with an idempotency
identity bound to that seal. A Run may have multiple seals; replay and history
selection must be scoped to the requested seal rather than requiring or returning
one global accounting record. Bind every accepted terminal, obligation and
recovered outcome to that member's exact selected acceptance/effect source and
seal; cross-seal substitution holds. After materialization, the canonical reader
must prove exact full physical membership, including absence of orphan rows; the
selected-only startup pass alone is not completion evidence. Keep the three A54
limits distinct and versioned: call count, complete manifest bytes, and total
batch bytes. Check each independently at N and N+1.

Checks: extend `test_execution_call_branch_competition.py`,
`test_r14_native_open_obligation.py` and `test_r14_original_resolution_mounted.py`
with multi-call accepted/cancelled mixtures, both cancellation orders, partial/rival fanout,
missing/extra/duplicate/reordered members and interrupted publication. Rejected
histories select zero partial companions and emit zero pre-accept external calls.
Read-only members remain unselected, not falsely accounted as terminal outcomes;
without the registered lineage bound, no read-only initialization or durable
pending record is allowed. The complete serialized envelope has explicit N/N+1 bounds.
Also cover two distinct seals in one Run, exact per-seal replay, two accepted calls
with distinct outcomes, a cross-seal accepted-evidence substitution, and full
physical orphan detection after reopen.

**Reader/writer constraints from the cold implementation review:** reconstruct the
V3 member frontier from the authenticated history cut already supplied to the
outer reader. Do not invoke the ordinary selected-accounting collector from inside
`_read_call_history`, because its history lookup re-enters that reader. Preserve
the source stamp outside the V3 owner request, then reacquire history after owner
IPC and apply the same actor, worker/session, commitment, and authenticated-source
admission/CAS checks used by the V2 writer before selecting the physical command.
These checks are required for V3 publication and canonical reopen, not optional
hardening. This is the cold review's required implementation guard, not evidence
that the V3 persistence path is already implemented.

### R14.7 — Executable read-only lineage (original R14.3; accounting completion for R14.1)

Depends on R14.6 and the user's frozen retry-bound decision. Reuse
agent-loop `readonly_execution_contracts.py` (`PrepareReadOnlyAttempt`,
`PrepareReadOnlyOutcome`, `PrepareReadOnlyPending`, `PrepareReadOnlyReduction`,
`RegisteredReadOnlyProof`, `ReadOnlyCounterRecord`) and
`recovery_frontier_contracts.py` (`ReadOnlyRetryLineage`, `PendingCallFrontier`).
Before the decision, only prepare the route and authenticate its proof sources;
the public request must HOLD before read-only call initialization or lineage
allocation, with no durable record. Do not publish `ReadOnlyRetryLineage`,
`ReadOnlyLineageSnapshot`, `PendingCallFrontier` or `ReadOnlyPendingRecord`: the
current frozen contracts require the bound at these construction points. After
the decision, mount owner preparation, authenticated retained history and the
sole-writer CAS in the existing execution flow; reuse the current composition
successor flow plus agent-loop `successor_record_contracts.py` and
`execution_recovery_preparation.py` for the minimum pending successor branch.
New runtime modules are to be named at that slice, not asserted to exist now.

R14.7 includes the minimum authenticated successor-initialization and inherited-
pending seam required for `SuccessorReadOnlyAttempt`; reuse the current successor
flow and widen its preparation/source verification only for this pending branch.
R14.9 later completes the total frontier inventory and nonempty continuation
preservation for every required subject. This keeps the initial read-only successor
observable without pulling the full recovery frontier into this slice.

For every pending head, the single CAS must choose one complete branch: either
atomically close it as `NEXT_ATTEMPT_ACCEPTED`, advance the shared counter and
publish the successor pending head, or atomically publish the complete terminal
or recovery result. A same-Run continuation cannot consume a pending head.
Crash/reopen must expose exactly one branch with no ordinal gap or partial
transition. Read-only initialization additionally requires the exact ACTIVE Run
and applicable live worker lease (A51); stale or absent authority publishes
nothing.

Observe one lineage allocated at original initialization, unique ordinals, exact
predecessor outcomes, and one monotonically consumed counter. Only registered
retryable definite failure below a frozen bound may permit another attempt.
Until the user registers that bound, all requests requiring a read-only lineage
remain HOLD before allocation; no initial attempt, retry, pending record or
read-only reduction is authorized. After the decision, observe unique ordinals,
exact predecessor outcomes and one monotonically consumed counter. Only registered
retryable definite failure below the frozen bound permits another attempt.
Unknown, open obligation, success, final failure and exhaustion forbid retry.
Atomic call-level reduction closes the lineage once; pending records exact absent
call outcome and terminal heads and cannot satisfy accounting, later Turn,
CompleteAcceptance or terminal Run. Positive read-only proof is mandatory. This
slice completes R14.6's read-only accounting subject only after the selected
lineage/outcome is available.

Checks: before the decision, add a mounted pre-allocation HOLD/no-write witness;
prove the existing ACTIVE Run remains selected and unchanged, with no new Run
initialization, read-only call, physical lineage or pending record selected.
Afterward,
begin with `tests/capabilities/agent_loop/test_readonly_execution_contracts.py`
and `test_readonly_source_contracts.py`; add public mounted lineage fixtures with
changed proof/schema/counter/reducer, branch mixing, post-terminal retry and bound
N/N+1. Cover pending-to-pending, pending-to-terminal/recovery, same-Run rejection,
and crash/reopen on both sides of the CAS. Shape tests or a fixture number cannot
choose policy.

### R14.8 — Current reduction at both continuation gates (original R14.2)

Depends on R14.6 and the mounted lineage/outcomes from R14.7. Reuse agent-loop
`original_semantic_reduction_preparation.py::prepare_loop_semantic_reduction` and
composition `r14_loop_semantic_reduction_publication.py`,
`r14_original_call_resolution_publication.py`, `r14_historical_suffix.py`,
`r14_next_turn_publication.py`, `r14_execution_runtime.py`, and the native
completion boundary in `execution_completion_contracts.py` plus
`execution_fan_out_preparation.py` (under agent_loop).

Both **TurnStarted** and **CompleteAcceptance** must recompute the complete
accounting join and bind accepted witness, resolver closure and exact current
consumable reduction head. A fresh compatible advance permits the appropriate
consumer; absent, stale, malformed or rival reduction gives typed hold/fault and
zero consumer publication. Latest raw append is never a substitute. Resolver-only
closure, canonical exact absence and identical full replay must survive reopen.
Accepted/cancelled cases can be mounted before the user sets the retry bound.
Any read-only request that requires a lineage remains HOLD before allocation until
R14.7 can construct it with the registered bound.

Checks: extend `tests/composition/test_r14_loop_semantic_reduction_publication.py`,
`test_r14_historical_suffix.py`, `test_r14_original_resolution_mounted.py`, and
agent-loop `test_original_semantic_reduction_preparation.py`. Add one mounted
positive and absent/stale/rival no-write witness for EACH continuation consumer,
including evidence-before-closure and publication/reopen interleavings.

### R14.9 — Total frontier and nonempty continuation preservation (original R14.3)

Depends on R14.7 and R14.8. Reuse agent-loop `recovery_frontier_contracts.py`
(`RecoveryFrontier`, `SuspensionBaseline`, `ResumeCommand`, `SuccessorCommand`),
`recovery_frontier_registry_contracts.py`, and composition
`r14_execution_suspension.py`, `r14_execution_resume.py::resume_execution`,
`r14_execution_successor.py::drive_successor`,
`r14_execution_transition_verification.py`, `r14_next_turn_publication.py`.

Independently enumerate every required subject and its exact ABSENT/N/A/PRESENT
classification in one current transaction. Preserve the immutable suspension
baseline and implement the full same-Run/successor/HOLD/FAULT partition. For every
successor, classify each original obligation as exact `REFERENCE_EXTERNAL` or
`CLOSED_BEFORE_TRANSITION`; retain immutable original obligation/evidence
observations and append a new immutable observation when their heads advance.
Reject `TRANSFER_OPEN`, successor-owned copies, omission or duplication. Carry
crossed A43 no-retry references and run the closed action-lineage check before any
model, tool, dispatch, retry or worker action; unknown proof holds. Preserve the
same read-only counter across resume, successor and restart. No changed ingress or
successor edge renews old authority or allocates a new retry budget.

Checks: extend mounted suspension/successor/direct-terminal suites and
`tests/capabilities/agent_loop/test_recovery_frontier_contracts.py` with nonempty
references, changed semantic/authority/snapshot bindings, omitted subjects,
stale/rival cuts, lost acknowledgement and crash-before/after-selection. Observe
one successor/next Turn and unchanged original stream/counter identities. Exercise
both obligation classes, changed observation heads, forbidden transfer/copy,
unknown model/effect/delivery lineage, and prove no successor action is reissued.
Freeze the takeover preservation requirement for the later scheduler seam;
implementing R15 takeover remains outside this roadmap.

### R14.10 — Current writer fencing registry (original R14.4 / A99)

Start from current existing writers after R14.6; final inventory expands as later
R14.11 and R14.12 writers are added and is not complete until R14.13. Reuse
`src/chiplog/platform/runtime_surface_contracts.py::WorkerAuthoritativeCommitRegistry`,
composition `r14_worker_applicability_support.py::interpret_worker_applicability`
and `matches_captured_cut` for current execution and recovery writers.

Bind real current commands/handlers/record kinds/applicability variants in both
directions against the registry. Explicitly list uncovered writer families;
partial registration must not report global completeness. Fresh stale worker,
session or selector rejects before publication and leaves records, publications,
tenant head and durable decisions unchanged. Replay/recovery is covered only if
its own admission route is registered and verified.

Before any root write, classify exactly one A99 branch from authenticated decision
and full commitment: `FIRST_PUBLICATION`, `MATERIALIZE_EXACT_DECISION`, or
`RETURN_EXACT_REPLAY`. Exact complete replay is byte-identical and need not restore
the old live lease. Materialize only the exact selected prefix. Non-prefix,
rival, or changed materialization holds with no write. An absent or exact-prefix
physical materialization under a complete authenticated decision/commitment is
the permitted `MATERIALIZE_EXACT_DECISION` case; materialize only its exact missing
members. Missing/incomplete commitment evidence or unavailable decision authority
holds with no write. Do not conflate a permitted partial physical materialization
with incomplete authorization evidence.

Checks: extend `tests/composition/test_worker_applicability_support.py`; add a
mounted fresh-publication stale-worker/session no-write observer. Reopen after a
crash with an exact physical prefix and complete commitment, materialize the exact
remainder, then prove byte-exact replay. A missing commitment witness must hold
without adding members. Registry tests must fail for a deliberately omitted
current real writer and an invented entry.
Do not claim A99 complete while later R14 writer families remain unregistered.

### R14.11 — Durable model-attempt recovery (original R14.5)

The emission marker, capture, unknown-outcome retention and lost-response HOLD
can proceed using the existing exact writer guard, without waiting for the
read-only retry-bound decision or the post-terminal lease family. Gate only
proof-authorized replacement on the current writer registration from R14.10.
Reuse agent-loop `execution_contracts.py`,
`model_attempt_recovery_contracts.py`,
`model_attempt_recovery_preparation.py::prepare_model_attempt_replacement` and
`prepare_late_execution_response`, plus composition `r14_execution_runtime.py`
and existing retained model capture/publication boundaries.

Observe the exact five-state lifecycle and durable emission marker before bytes
leave the model leaf. Lost response preserves possible emission and cannot blindly
reissue. Registered independently authenticated no-exposure proof alone allows
replacement in the same Turn/call lineage with a fresh monotone selector generation.
Late response is retained without substituting a rival selected attempt. Carry the
selected attempt and its complete manifest through response, parse, trace, replay
and evaluation; an older generation cannot replace selected evidence.

Checks: extend `tests/capabilities/agent_loop/test_model_attempt_recovery_preparation.py`
and `test_model_attempt_recovery_contracts.py` with a mounted emission/crash
observer, missing/forged/stale proof, late response, lost acknowledgement,
replacement race, restart and generation exhaustion. Inspect durable selected
state and actual emission counts; pure producer success is insufficient.

### R14.12 — Original-stream post-terminal work (original R14.4 / A59)

Depends on the accepted-obligation/frontier joins from R14.8–R14.9 and current
writer registry from R14.10. Reuse agent-loop `post_terminal_contracts.py`
(`PrepareTerminalWork`, `PrepareWorkLease`, `PrepareWorkRollover`, `PrepareWorkClose`)
and `post_terminal_record_contracts.py`.

Original obligations remain on original streams. Post-terminal work has stable
identity, an independent lease and resolver-before-close; uint64 exhaustion selects
at most one replacement physical epoch with stable logical lineage. Stale lease,
renewal, clock, generation or selector writes nothing. This does not implement R15
takeover; frozen later-item seams remain contracts.

Every Run-terminal writer, including CANCELLED, must atomically consume the weak
exhaustive accounting join and preserve the full per-call accounting manifest.
For each exact open `RECOVERY_REQUIRED` obligation, that same terminal batch creates
one stable work subject, initial epoch, selector and genesis. Pending or incomplete
accounting yields no terminal publication. CANCELLED stops new orchestration only;
already accepted streams continue and are not implicitly undone. Verify selected
physical state after reopen.

The authoritative current recovery view joins that immutable terminal manifest
with every named original obligation's current head, stable work subject, selected
epoch and status. A resolver closes the original obligation under its existing
owner/reducer first; a causally bound work update then records that exact terminal
head. Reopen/replay across a crash between these commits must not create a second
resolver authority. Terminal Run state and exhausted epochs never suppress
recovery admission or rewrite Run history.

Checks: extend `tests/capabilities/agent_loop/test_post_terminal_contracts.py` and
`test_post_terminal_record_contracts.py`; add mounted lease/expiry/race/reopen and
rollover observers. Preserve the original stream across restart and prove exactly
one selected replacement epoch. Reopen the authoritative recovery view after
closure and after epoch exhaustion; stale/missing subject, selector, epoch or
current obligation heads must hold, while open work remains claimable.

### R14.13 — Full R14 convergence and evidence audit

Depends on R14.6–R14.12. Map every original R14 requirement and named invariant
A15, A38–A47, A49–A52, A54–A57, A59, A99 and A108 to executed V5/V6 histories and
current tree/profile/fixture-bound artifacts. Inspect selected durable state and
physical envelopes. Finish bidirectional writer inventory after all R14 additions.
No remaining HOLD or unsupported required R14 path can be reported as complete.
The read-only retry-bound decision gates read-only initialization, lineage/pending
allocation, retry/exhaustion histories and full completion. Budget-independent
worker and model-attempt results remain implementable while that decision is open.

The final audit includes a compact clause-to-evidence matrix for A50, A51, A54,
A55, A57, A59, A99 and A108: implementation slice, positive selected-state
observation, and a discriminating negative/no-write observation. Show each A54
bound independently, each A57 pending CAS branch, terminal-work creation coupled
to the Run terminal batch, all three A99 pre-root branches including exact-prefix
recovery and incomplete-commitment rejection, and selected-attempt/manifest
continuity from A108.

Intermediate slices use fast mounted positives/negatives and recovery checks;
heavy stress/combinatorial work stays in backlog until a minimal horizontal
scenario passes, then returns before final integration, per AGENTS.md. At merge
preparation run full regression and one cold swarm-red-team over the integrated
diff for critical/blocking defects. Exact final selectors follow the repository's
current verification commands; do not invent a new orchestrator or waive the
strict R8 catalog. Commit handoff follows current AGENTS.md; the user's existing
authorization for intermediate commits remains in force.

## Open user-owned decision and limits

**OPEN: read-only retry `max_attempts`.** The immutable lineage needs a registered
frozen bound/version, but this roadmap supplies no number or default. Owner:
user. R14.7 lineage allocation and read-only initialization, R14.8 read-only joins,
R14.9 read-only frontier evidence and corresponding full-convergence checks cannot
close until that decision is explicit. Preserve the question during handoff; such
requests remain HOLD before allocation in the meantime.

This planning run executed no runtime suites and claims no newly proven behavior.
It inspected files and local history; it did not inspect unfetched history or
every physical writer body. Final executable inventory and normative clause audit
are required in R14.13, not presumed from this plan.

## Swarm process and cold review

Phase 1: define a remaining-work artifact only; success is precise dependencies,
source locations, observable outcomes and honest blockers. Branch budget: three.
Phase 2: simulated perspectives were Barbara Liskov (critic, contract separation),
Leslie Lamport (critic, CAS/lineage), Martin Fowler (evangelist, smallest useful
vertical slices), and Michael Feathers (balanced, executable verification).
These are reasoning lenses, not consultation with those people.

Iteration 1, E: unknown current state → grounded bounded slices by reading current
roadmap/ledger, recent commits, contracts, runtime symbols and tests.
Iteration 2, A: competing organization → one dependency chain. Contract lens
requires both continuation gates; CAS lens separates accounting from pending;
vertical-slice lens preserves mounted work; verification lens reserves convergence
for observed outcomes. Overlap on preserving existing slices was compressed.
Iteration 3, E: uncertain numbering and runtime boundaries → grounded numbering
and exact reusable symbols. New runtime joins are explicitly future work.

A cold no-parent-context `gpt-6.1-sol`/low agent reviewed the initial plan before
the first artifact write. Its critical finding required explicit TurnStarted AND
CompleteAcceptance checks and A43 consequential no-retry preservation. Those were
incorporated in the initial artifact; later sequencing findings were repaired in
this document. Common-model agreement is not independent external verification.

The same cold agent then read the corrected artifact and reported zero remaining
critical planning findings. Structural checks verified contiguous R14.6–R14.13
headings, required consumer/A43/blocker terms and existence of named test basenames;
`git diff --check -- design-docs/R14-REMAINING-ROADMAP.md` returned exit 0 (with a
local fsmonitor diagnostic). These checks do not prove runtime completion.

H readiness/pre-mortem: contract coverage could omit a consumer (both named);
lineage could reset across successor (nonempty observer required); slice design
could redo mounted work (reuse boundaries named); green shapes could be mistaken
for runtime proof (actual selection/emission observers required). No new route
was opened. Finalization is bounded: the roadmap is usable, while max_attempts
and actual R14 completion remain open.
