# R14 functional scope and deferred assurance

User scope decision, 2026-10-17: complete client functionality; move thick
verification layers, isolated corner cases and work unnecessary to those flows
to backlog. This supersedes earlier R14 release obligations in the remaining
roadmap, completion ledger, admission preparation and normative implementation
mechanisms. Deployment eligibility remains separate. The user decision of
2026-10-21 in [AGENTS.md §12](../AGENTS.md#12-продуктовый-режим-без-legacy-до-завершения-роудмапа)
also removes all legacy/backward-compatibility obligations until the roadmap is
complete; formats, storage and APIs may change freely.

## Acceptance boundary

The criteria below remain current. The 2026-10-17 closure is dated evidence; its deleted
SQLite compatibility witnesses are historical; current native evidence is mapped in
[R14-VERIFICATION-STATUS.md](R14-VERIFICATION-STATUS.md). Final integration
validation of the cleaned branch is still pending.

Functional R14 was CLOSED at integrated runtime commit `b514e8a` (2026-10-17).
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

Preserve current original-call identity, tenant scope, atomic writes,
authorization/CAS and no-repeat SEND behavior. Legacy formats, version readers,
physical-prefix reconstruction and old preparation replay are not requirements.
Keep normal limits; exhaustive independent boundary matrices are deferred.
Read-only members must not be silently dropped from a selected seal while their
functional branch is unavailable.

## Current native acceptance witnesses

These existing selectors identify current scenarios, not new passing results:

- Mixed accepted/cancelled members, two seals, reopen, next turn and no-repeat SEND:
  `tests/composition/test_postgres_two_seals.py::test_two_actual_accepted_effect_seals_reopen_and_complete_without_repeat_send`.
- Adoption/cancellation competition and replay after reopen:
  `tests/composition/test_postgres_call_branch_competition.py::test_actual_adoption_and_cancellation_keep_one_branch_after_reopen`.
- Original resolution, unresolved-work HOLD, nonempty obligation preservation,
  resume/next turn and post-abort recovery without resend:
  `tests/composition/test_postgres_run_recovery.py::test_original_effect_recovers_after_suspend_or_abort_without_resend`.
- Current reduction and rival evidence across retained seals:
  `tests/composition/test_postgres_effects.py::test_late_evidence_requires_current_join_and_rival_holds_every_old_seal`.
- Both completion boundaries recheck the current result:
  `tests/composition/test_postgres_completion.py::test_current_provider_assertion_and_old_seal_rival_at_both_acceptance_and_claim`.

The old reader-cut implementation plan is cancelled: its SQLite modules were
removed with the CommonCli/H1 kernel. There is no compatibility reconstruction
work to resume.

## Backlog, outside functional R14 acceptance

Owner: implementation agent. Revisit after functional R14 convergence when the
named pressure is present; these items do not hold intermediate R14 work.

| ID | Deferred work | Revisit trigger |
|---|---|---|
| B-R14-A01 | Optional current deployment/OS-image and authority-boundary assurance. Old version-tuple certification, V1 support and V2 compatibility initializer work are cancelled. | A concrete deployment assurance requirement. |
| B-R14-A02 | Cancelled: historical SQLite physical-prefix reconstruction. | No active work. |
| B-R14-A03 | Cancelled: historical V3 preparation/source reconstruction. | No active work. |
| B-R14-A04 | Three-plus accepted members, delayed dispatch permutations, multi-OPEN interleavings and every foreign-locator substitution. | A client needs that concurrency/interleaving. Ordinary two seals stay in current acceptance. |
| B-R14-A05 | Independent A54 N/N+1 matrices; exhaustive missing/extra/duplicate/reordered/orphan histories, including repinned materialization attacks. | Demonstrated size/corruption pressure; retain normal bounds and batch checks now. |
| B-R14-A06 | Cancelled: old SQLite reader/SQL-fingerprint optimization. | No active work. |
| B-R14-A07 | Exhaustive writer applicability inventory, lifecycle/rollover matrices and final OS-owner termination proof (handoff 69). | Scheduler/takeover integration or an observed surviving-owner failure. |
| B-R14-A08 | Separate durable terminal-recovery-fault recipe and exhaustive fault classification (handoff 23). Normal unresolved work remains HOLD and explicit abort is available. | A concrete client workflow needs a durable terminal fault distinct from HOLD/abort. |

The old H1 whole-runtime inventory/decoder closure is cancelled under §12.
Current authorization, data integrity and functional tests remain required.
