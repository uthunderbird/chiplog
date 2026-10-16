# R14 functional scope and deferred assurance

User scope decision, 2026-10-17: complete client functionality; move thick
verification layers, isolated corner cases and work unnecessary to those flows
to backlog. This supersedes earlier R14 release obligations in the remaining
roadmap, completion ledger, admission preparation and normative implementation
mechanisms. It changes neither stored record bytes nor deployment eligibility.

## Current acceptance

Functional R14 is CLOSED at integrated runtime commit `b514e8a` (2026-10-17).
The retained scenarios passed on the combined source; the main workspace was
fast-forwarded to that commit and its native public-client smoke passed.
Requirement-by-requirement evidence is in `R14-VERIFICATION-STATUS.md`.
Historical green checks retain their original meaning; deferred assurance is
not reported as implemented. This does not declare the whole repository ready
to merge into main/master or grant deployment eligibility.

| Slice | Functional completion criterion |
|---|---|
| R14.6 accounting | Account accepted/cancelled calls in their selected seal; unfinished members keep it pending. Read the same result after reopen; replay selects no new publication or SEND. Two ordinary seals in one Run remain independently readable. |
| R14.7 read-only retry | Execute read-only retry with a durable lineage and maximum 3 attempts including the initial attempt; reopen cannot reset the counter. |
| R14.8 continuation | Both continuation consumers use the current reduction; unresolved or rival evidence holds. |
| R14.9 frontier | Resume or select a successor from the actual pending work, preserving nonempty obligation/retry references. |
| R14.10 current writer | Current writer can publish; an ordinary stale writer cannot. A complete bidirectional registry proof is deferred. |
| R14.11 model recovery | Recover a lost model response without blindly reissuing a possibly emitted request; preserve attempt lineage. |
| R14.12 post-terminal | Continue recovery in the original stream after Run termination without repeating the original external action. |
| R14.13 convergence | Run the retained client scenarios and record their actual outcomes; no exhaustive clause/mutation certification prerequisite. |

Keep existing data/versions, original-call identity, tenant scope, atomic writes,
current authorization/CAS and SEND idempotency. Historical readers decode
selected stored outputs and check their publication batches. They need not
recreate an old physical database or rerun the original owner preparation.
Keep normal limits; exhaustive independent boundary matrices are deferred.
Read-only members must not be silently dropped from a selected seal while their
functional branch is unavailable.

## R14.6 acceptance scenarios

- Mixed accepted/cancelled seal, publication, reopen and idempotent replay:
  `tests/composition/test_r14_native_selected_accounting_source.py::test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once`.
- Acceptance/cancellation competition:
  `tests/composition/test_execution_call_branch_competition.py::test_selected_acceptance_and_native_cancellation_compete_on_one_initialized_cut`.
- OPEN resolution/reopen without another SEND, including current-actor resume:
  `tests/composition/test_r14_original_resolution_mounted.py::test_original_resolution_publishes_closed_outcome_and_reopens_without_resend`.
- Ordinary two-seal accounting/reopen:
  `tests/composition/test_r14_native_selected_accounting_source.py::test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`.
- Reopen to a captured next Turn:
  `tests/composition/test_r14_direct_terminal_next_turn_mounted.py::test_selected_direct_terminal_reopens_to_one_next_turn`.

These are existing scenario targets, not new passing results or a per-edit test
suite. Split bundled adversarial tails from ordinary scenarios when editing the
tests. The combined multiple-OPEN/foreign-locator matrix is not minimal acceptance.

## First implementation cuts

1. Remove the historical physical-prefix reconstruction from ordinary OPEN reads
   in `r14_historical_suffix.py`: both the selected-source wrapper and final
   pending-lifecycle caller. Retain selected tenant/head/predecessor binding and
   present final-batch checks. Delete the unused backup/DELETE/hash helper rather
   than add a verification mode. Update tests to the explicitly reduced contract.
2. In `r14_native_sealed_accounting_v3_history.py`, decode the selected V3 result
   without re-verifying original preparation, collecting its sources again and
   rerunning `prepare_sealed_accounting_v3`. Retain selected seal identity,
   version decoding, exact stored command/envelope and COMPLETE batch checks.
   Keep current writer preparation and CAS.

No speedup is established yet. These cuts leave record formats unchanged and do
not claim that the previous second-accept writer-STALE failure is already fixed.

## Backlog, outside functional R14 acceptance

Owner: implementation agent. Revisit after functional R14 convergence when the
named pressure is present; these items do not hold intermediate R14 work.

| ID | Deferred work | Revisit trigger |
|---|---|---|
| B-R14-A01 | Whole-tree descriptor/receiver/assembly closure; engine/schema/AMR goldens; complete approved image/CURRENT/version-tuple certification and V2 initializer proof. Existing supported-version behavior remains. | A real format transition or deployment assurance milestone. |
| B-R14-A02 | Reconstructing historical physical prefixes; repinned historical mutation/fence proofs. | An observed historical corruption case or explicit offline audit requirement. |
| B-R14-A03 | Re-deriving historical V3 preparation/sources and exhaustive companion/source substitution proofs. | An observed selected-output mismatch. |
| B-R14-A04 | Three-plus accepted members, delayed dispatch permutations, multi-OPEN interleavings and every foreign-locator substitution. | A client needs that concurrency/interleaving. Ordinary two seals stay in current acceptance. |
| B-R14-A05 | Independent A54 N/N+1 matrices; exhaustive missing/extra/duplicate/reordered/orphan histories, including repinned materialization attacks. | Demonstrated size/corruption pressure; retain normal bounds and batch checks now. |
| B-R14-A06 | Simplifying runtime EXPLAIN/exact-read-graph/SQL-fingerprint proof. Do not issue false V2 attestations while removing it. | Next scoped performance cut after the two reader cuts. |
| B-R14-A07 | Exhaustive writer applicability inventory, lifecycle/rollover matrices and final OS-owner termination proof (handoff 69). | Scheduler/takeover integration or an observed surviving-owner failure. |
| B-R14-A08 | Separate durable terminal-recovery-fault recipe and exhaustive fault classification (handoff 23). Normal unresolved work remains HOLD and explicit abort is available. | A concrete client workflow needs a durable terminal fault distinct from HOLD/abort. |

Investigation: cold full Swarm artifact
`/private/tmp/r14-simplification-swarm.md`; source-backed recommendations, no
runtime test or performance verdict. Root inspected the relevant helpers and
retains ownership of the implementation.
