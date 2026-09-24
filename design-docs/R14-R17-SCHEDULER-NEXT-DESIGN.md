# Corrected bounded scheduler contract task

Status: reviewed design input for the next Phase-C task, not an approved contract freeze. Prepared by a `gpt-5.6-sol` low-effort Swarm subagent, then corrected after root review. No runtime or contract code has been implemented from this design. Baseline inspected: `feat/r14-r17-integrated` at `e982c72`.

## Decision and evidence

The next slice must let one authenticated automatic scheduler cycle publish an interval and 0/1/N executable `ExecutionRunRecord`s as one agent-loop-owned WHOLE batch. It must preserve the primitive hash DAG and `PreRootDecisionFence`, use a bounded system mandate with a separate service identity, consume one shared durable budget by CAS across restart/takeover, and distinguish prepared first publication from selected replay. The public caller supplies neither trusted clock nor authentication.

Scheduler and execution are both owned by `agent_loop`. `_owner_publication_contracts.Owner` has no scheduler owner, and scheduler operations use `SingleOwnerBatch`. Do not add a synthetic multi-owner batch. The joint publication remains a `SingleOwnerBatch`; `command.owner` and all `complete_records[].owner` are `agent_loop`, while its registered operation verifier enforces the WHOLE grammar.

The evidenced cycle is `SchedulerExecutionSource.materialization: MaterializationCommitment`: that finalized value already contains `initial_run`, `initialization`, and reciprocal references, yet it is supplied to `PrepareScheduledExecution`, which prepares the new Run. A V2 scheduled path must consume a pre-finalization primitive. `ExecutionInitializationCut.materialization_commitment` is an existing source-cut commitment and is not proven cyclic; keep it unless further Phase-C inspection finds a distinct mismatch.

## Phase-C contract patch

Names below are proposed implementation names. Before editing, resolve the decisions in “Required Phase-C decisions” and use the chosen names consistently. No placeholder class or ellipsis should enter source.

### 1. Bounded system mandate in `scheduler_contracts.py`

Add immutable types with these complete fields:

- `ScheduledSystemMandateScope`: tenant; distinct service identity and beneficiary principal; exact `ScheduleDefinitionHead`, `MissedOccurrencePolicyHead`, and `SchedulerIntervalBoundHead`; prompt/template head and canonical prompt fingerprint; exact Run `BudgetPolicy` head/fingerprint; exact tool registry/schema-set head including consequential-tool admission; origin/endpoint and delivery-policy/recipient-scope heads; closed allowed-operation literals; authority registry, applicability, disclosure/contour, and runtime-graph heads.
- `ScheduledSystemMandateHorizon`: coordinate-policy version; inclusive first and last eligible `DueCoordinate` (or one explicitly chosen half-open convention); positive maximum cycles, executable Runs, and consequential calls; issuance generation and trusted-expiry proof reference. The caller never supplies observed current time.
- `ScheduledSystemMandate`: mandate id/head/fingerprint, scope, horizon, issuance id/fingerprint.
- `ScheduledMandateBudgetHead`: mandate id, durable budget head, generation, cumulative cycles/Runs/consequential calls consumed.
- `ProposedScheduledMandateConsumption`: exact predecessor, three deltas, and proposed canonical successor fields/bytes.

The writer checks current heads, horizon inclusion from its trusted clock, arithmetic, and maxima. `service_identity` is never inferred from or copied into `principal_id`. Restart, lease takeover, successor Run, and rollover cannot change the mandate or reset budget lineage.

### 2. Split primitive preparation from finalization

In `scheduler_materialization.py`, preserve `SCHEMA_DAG` and its domains. Add:

- `ScheduledMaterializationSeed`: ordinal, `ScheduledBatchPrimitiveDomainV1`, `MaterializationIdentity`, lineage/root/epoch/genesis lease, dispositions, and only members preceding `run` in `SCHEMA_DAG`.
- `PreparedIntervalSeedBatch`: parent primitive, prepared decision member, branch, ordered seeds, skipped dispositions, unchanged `PreRootDecisionFence`.

Seed count follows policy algebra: 0 boundary-only/SKIP, 1 coalesced or single, N materialize-each multi. A seed excludes Run, initialization, reciprocal, companion, finalized-members, batch-envelope, and interval-envelope outputs.

Add a separate owner finalization request/result that accepts exact ordered prepared agent-loop Run outputs and returns finalized `MaterializationCommitment`, interval decision, and canonical members. Phase C defines this wire and dependency manifest. The algorithm and semantic DAG verification are Phase T behavior, not shape-test claims.

Retain existing `MaterializedOccurrence.run: RunRecord` and its historical interpretation; migrate via V2 deliberately.

### 3. Version scheduled execution initialization

Keep `PrepareInboxExecution` and V1 unchanged. Add:

- `PreparedIntervalDecisionSource`: prepared decision member/bytes plus `FirstPublication`, with no selected head.
- `SelectedIntervalDecisionSource`: exact independently selected decision head/schema/bytes plus `MaterializeExactDecision`. Complete replay is looked up before owner preparation.
- `ScheduledExecutionSeed`: ordinal, materialization seed, `CreateExecutionRun`.
- `PrepareScheduledIntervalExecutionsV2`: configuration source, discriminated decision source, mandate, expected durable budget predecessor, ordered seeds, and current `ExecutionInitializationCut` unless separately disproven.
- `PreparedScheduledRunV2`: ordinal, primitive fingerprint, `PreparedExecutionInitialization`.
- `PreparedScheduledIntervalExecutionsV2`: ordered Runs, optional proposed mandate consumption, request fingerprint, complete output fingerprint.

Zero seeds yield zero Runs. Zero-work cycle accounting follows the required decision below. For 1/N seeds, exactly one proposed consumption has Run delta N. Agent-loop checks create fields against seed and mandate. A V2 scheduled binding stores service identity and mandate head separately while the Run retains beneficiary principal.

### 4. Reuse single-owner publication

In `_owner_publication_contracts.py`, add only an operation literal if the chosen operation is new; otherwise retain `scheduler.decide_interval`. Use `SingleOwnerBatch`:

- owner is `agent_loop` for command and every record;
- `AuthoritativeReadManifest.ordered_heads` contains current mandate/applicability/budget predecessor and scheduler source cut;
- records contain the whole ordered batch: first decision when applicable, interval/boundary/dispositions, 0/1/N seed/Run/init/reciprocal/lease/companion members, optional budget successor, final envelopes.

The registered verifier derives kinds/order/cardinality from branch and dependency manifest and CAS-checks the proposed budget successor in the writer transaction.

First publication uses prepared bytes and `FirstPublication(decision=Absent())`. Incomplete-prefix recovery uses exact selected bytes and `MaterializeExactDecision`. `ReturnExactReplay` uses `lookup_exact()` before owner preparation and consumes no budget. Rival/changed input rejects rather than falling through to fresh preparation.

### 5. Registered service acquisition

In `scoped_intent_contracts.py`, add `RegisteredScheduledServiceAuthority` containing exact mandate; independently selected service-registration and current-applicability records; scheduled initialization; expected budget predecessor; **proposed** consequential-call consumption; and complete independently selected authority sources.

Permit it only for `INITIALIZED_CONSEQUENTIAL_CALL`, alongside human adoption. Acceptance/effects publication verifies and selects its proposed successor atomically; the acquisition must not claim that successor is already selected. Keep `PLAN_EFFECT_PUBLICATION` unchanged and `AUTHORIZE_DUPLICATE_RISK` exactly human-only.

### 6. Public automatic-cycle request

Version `r15_tick_contracts.py` with a minimal request carrying request/delivery identity and schedule selector only. It has no principal, service identity, mandate, authentication, clock, cutoff, lease, budget, or prepared decision. Private issuance obtains current auth, clock, schedule/policy/bound revisions, mandate/applicability, and budget head. Preview/adoption remains administrative policy, not automatic-cycle authority.

## Phase-C consumer tests: wire/routing claims only

1. `test_scheduler_contracts.py`: strict round trips; all scope/horizon fields required; closed operations; positive limits; separate service/principal; unknown/missing rejection; no caller clock/auth fields.
2. `test_scheduler_materialization.py`: seed types round-trip; 0/1/N tuples are representable; seed schema lacks post-Run fields; prepared/finalized types do not cross-decode. Do not claim semantic cardinality or DAG verification.
3. `test_execution_initialization_contracts.py`: prepared/selected unions round-trip and reject hybrids; V1 final commitment cannot decode as V2 seed; empty/one/many outputs preserve bytes; distinct service/principal. Root/ordinal semantic rejection waits for a real owner function.
4. `test_owner_publications.py`: operation uses `SingleOwnerBatch`; all owners are `agent_loop`; replay query keeps original command and fresh invocation separate. Do not claim WHOLE verification, CAS, or atomicity from shape tests.
5. `test_scheduler_tick_contracts.py`: public request rejects injected trust/clock/mandate/budget/prepared fields; private issuance retains broker clock/auth.
6. `test_scoped_intent_contracts.py`: acquisition round-trip; discriminator/matrix exhaustiveness; duplicate-risk row remains human-only. Semantic mismatch tests wait for the verifier.

Update `R14-R17-INTEGRATED-CONTRACTS.md` with actual symbols and commands, marking only this slice ready after tests pass.

## Phase T behavioral tests, red before runtime

Use the common composition (suggested `tests/composition/test_scheduler_integrated_cycle.py`):

1. branches produce exactly 0/1/N executable `ExecutionRunRecord`s in one independently read WHOLE journal/SQL batch;
2. omission/addition/duplicate/reorder, ordinal, primitive, root, disposition, registry, and byte-bound mutants reject through the real verifier;
3. crash before selection, between journal/materialization, after materialization/before reply, and during N preparation yields no batch or one recoverable complete batch;
4. first publication has only prepared decision bytes before commit; a rival decision leaks nothing;
5. exact replay after lost ack calls `lookup_exact`, invokes no owner preparation, returns original bytes, and consumes no budget;
6. selected-prefix recovery uses the exact selected decision and permitted missing prefix;
7. sessions race the same predecessor at N/N+1; one CAS wins; restart/takeover/successor/rollover retain counters;
8. public clock/auth injection has no effect; unavailable/backward/expired registered clock or failed auth writes nothing;
9. beneficiary principal and service actor stay distinct; stale/foreign registration, mandate, applicability, initialization, or budget rejects before intent publication;
10. acceptance/intent and proposed consequential budget successor select atomically; crash/replay never double-consumes;
11. possible duplicate risk rejects automatic/system/compensation routes and accepts only exact authenticated human adoption.

Minimum positive history: public wakeup → private authenticated clock/mandate/budget cut → prepared interval seed → 0/1/N Run preparation → agent-loop `SingleOwnerBatch` CAS → scheduled Run → initialized consequential call → registered service acquisition and atomic budget consumption.

## Required Phase-C decisions before implementation

1. **Budget record identity and operation.** No current scheduler-mandate budget record exists. Inspect the owner-record registry/writer schema and decide exact `record_kind`, subject key, schema id, and whether to extend `scheduler.decide_interval` or add `scheduler.materialize_interval`. Record with `decide.sh` before editing; do not defer to Phase I.
2. **Zero-work accounting.** Decide whether every interval cycle consumes `cycles_consumed=1` with Run delta zero. Recommendation: yes, otherwise repeated empty cycles are outside the finite mandate bound. Encode the decision in counter fields and fixtures before editing.

## Additional root review requirements

Before delegating contract code, Astra must reconcile the sketches with all required
branches, including `OVERFLOW_HOLD`, and give every seed an exact initial-Run
absence witness at one common cut. The single V1 `initial_run_absence` field does
not by itself establish a complete N-Run absence manifest. Preserve the full
`PreRootDecisionFence`; the abbreviated `FirstPublication(decision=Absent())`
notation above also requires `expected_canonical_absence_manifest` in actual code.
A prepared decision containing final output hashes may itself need a primitive
form: demonstrate the full dependency DAG rather than merely renaming it a seed.

The proposed positive maxima, exact-head scope and cycle debit are design proposals,
not newly authorized policy requirements. Check their behavior against zero-capacity
mandates, allowed authority changes and all original mandatory histories before
freezing them. Do not claim this document resolves the complete scheduler contract.

## Known gaps

- Scheduler materialization creates legacy `RunRecord`; integrated execution needs `ExecutionRunRecord` preparation while preserving old wires.
- `SchedulerExecutionSource` takes a finalized commitment containing the Run refs it is meant to prepare.
- No registered scheduled-service acquisition exists.
- Public tick policy is one-delivery/no-execution, not an automatic-cycle authority contract.
- No durable system-mandate budget record/verifier exists; Phase C must fix its exact registry identity first.

Swarm convergence: this route reuses the real single-owner seam and separates shape evidence from behavior. The remaining registry identity and zero-work accounting are Phase-C decisions, not implementation guesses. No repository edits were made.
