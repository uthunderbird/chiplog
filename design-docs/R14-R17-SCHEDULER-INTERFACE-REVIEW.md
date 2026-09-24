# Astra interface review for R14–R17 scheduler continuation

2026-09-24. Read-only review, no repository edits. This is a bounded interface specification and remaining decision list, not a complete Phase-C freeze, Sol design pass, runtime evidence, or permission to skip joint tests. Root reviewed the bounded inspection plan without blockers. Agent-thread limits prevented the requested subsequent Sol/Terra work; preserve prescribed models on transfer.

Inspected AGENTS.md, RESUME, scheduler-next-design, integrated-contracts, scheduler materialization/preparation/contracts/configuration-related code, executable initialization/transition contracts, scoped intent authority, owner publication types, scheduler registry/runtime and relevant normative sections. Root additionally inspected historical decoder and CallEffectBatch joins.

## Corrections mandatory before Terra implementation

1. A finalized ScheduledIntervalDecision is NOT a valid pre-Run input: its materializations contain final Run/init references. Prepared first-publication source must carry the primitive interval-parent bytes and primitive reference, not the final decision/result bytes. Existing `_compile` already derives parent_ref from IntervalParentPrimitive before Runs and only creates ScheduledIntervalDecision afterwards. Preserve that distinction in names and decoders.
2. Existing `PreparedExecutionInitialization` cannot carry the proposed service binding without changing a V1 union. Introduce an explicitly versioned `PreparedScheduledExecutionInitializationV2` with executable Run, source request fingerprint, proposed scheduled binding V2, and proposal fingerprint. Preserve V1 initialization request/result schemas. The wrapper `PreparedScheduledRunV2` must contain the new result, not silently widen V1.
3. `ExecutionInitializationCut.initial_run_absence: Absent` has no subject identity and cannot prove N initial Runs absent. Introduce an interval V2 cut with tenant/database, selected tenant sequence, existing database materialization commitment, broker session/generation/registry/source observations, plus ordered subject-qualified absence entries `(ordinal, run_id, root_id, subject, expected_run=Absent)`. Each entry binds the common cut; the writer enumerates the exact identities itself. Zero-work has empty entries; coalesce has one; materialize-each has N. The complete pre-root canonical absence manifest also covers other required canonical genesis records; do not replace that fence with Run absence only.
4. OVERFLOW_HOLD requires its own seed-batch union branch: existing IntervalCandidate has `parent_primitive=None`, zero occurrences, and a SchedulerOverflowHold. Do not require a full interval manifest or primitive parent in that branch: streamed overflow is legal. Hold publishes no dispositions/Run/root/lease or boundary advance. Its canonical hold primitive precedes hold identity/body; budget bookkeeping is separately selected, if policy charges holds.
5. Existing schema DAG is only the legacy occurrence derivation. Preserve it and introduce a complete V2 dependency manifest that includes primitive first decision, service binding, Run/result, budget consumption and final WHOLE envelope. Merely reusing `SCHEMA_DAG` would omit new dependencies.

## Concrete V2 wire boundaries

- `PreparedOrdinaryIntervalSeedBatchV2`: configuration source/preimages; primitive parent and its canonical member/reference; exact FirstPublication fence; branch; ordered occurrence seeds; skipped dispositions; common V2 cut; selected mandate and budget predecessor. Each occurrence seed contains ordinal, primitive, MaterializationIdentity, derived lineage/physical epoch/selector/genesis lease, dispositions, and exact initial-Run absence entry. No final Run/init/reciprocal/final envelope hashes.
- `PreparedOverflowSeedBatchV2`: exact hold preparation/evidence, including FULL_MANIFEST or STREAMING_DIGEST branch; same common cut/fence/mandate/budget predecessor; no occurrence seeds.
- `PrepareScheduledIntervalExecutionsV2`: ordinary seed batch plus ordered CreateExecutionRun commands. Zero commands is valid for boundary/skip. Source discriminates PREPARED_PRIMITIVE_FIRST_PUBLICATION from SELECTED_EXACT_DECISION. The latter carries exact selected journal/result bytes plus exact-prefix materialization evidence, rather than asserting those bytes are unselected primitives.
- `PreparedScheduledExecutionInitializationV2`: executable Run and ScheduledExecutionBindingV2, keeping beneficiary principal in the Run and service identity/mandate head in the binding. Binding names primitive parent/materialization identity and original configuration, never the final interval envelope.
- `FinalizeScheduledIntervalV2`: exact seed batch and exact ordered owner-prepared Run outputs; optional proposed budget successor only if accounting policy allows no debit. Returns complete interval result/hold, finalized occurrence commitments, all canonical records and final envelope. No independent per-Run publish port.
- Exact selected replay uses broker lookup before any preparation. Prefix materialization must use selected decision bytes and selected debit bytes; it cannot recompute a fresh budget successor from today's head. Conflicting command bytes fail.
- Initial interval publication is agent_loop `SingleOwnerBatch`. Subsequent worker mutations use the exact current physical-root lease. Service-origin effects acceptance is the existing loop/effects `CallEffectBatch`, augmented with the loop-owned mandate-budget member; effects must not write that record itself.

## Full dependency ordering / exclusions

Selected configuration, mandate, authenticated service/applicability, budget predecessor, trusted clock and existing database cut
→ primitive interval parent (or overflow primitive)
→ primitive parent identity
→ per-occurrence primitive, stable subject/root/Run IDs
→ genesis lease, lineage, physical epoch, selector
→ proposed scheduled binding V2 and executable Run
→ executable initialization result and scheduler initialization member
→ reciprocal member and epoch companion
→ finalized occurrence member set
→ occurrence envelope and complete interval result
→ whole interval envelope/journal publication.

Budget consumption basis depends only on selected predecessor, primitive command identity/fingerprint and explicit deltas; canonical successor depends on that basis. It must not hash the final WHOLE publication that will include it. Final interval envelope commits the successor and all occurrence outputs. An initialized service call similarly uses acceptance primitive identity → budget debit → acquisition → effects intent → combined acceptance publication. Never put the resulting intent/combined acceptance hash back into its own budget debit basis.

Canonical fingerprint fields are excluded only by an explicitly versioned registered domain. Parent primitive never contains final decision/materialization references. Existing database commitment is an input-cut observation, not future scheduler output commitment. Proposed budget successor is not selected evidence. Frozen registered scope and fresh session applicability are separate: lease takeover cannot require impersonating the original service session.

## Registry and compatibility choices

Recommended explicit identities: agent_loop record kind `scheduler.mandate-budget`, schema `chiplog.scheduler.mandate-budget.v1`, subject key canonical `(tenant_id, mandate_id)`. Record body includes mandate identity, generation, predecessor exact head, cumulative cycles/Runs/consequential calls and primitive consumption basis. Genesis requires explicit absence and zero counters; all subsequent updates CAS the one durable head. New issuance cannot accidentally reset the same mandate's lineage.

Recommended new operation `scheduler.execute_interval`, with its own `chiplog.scheduler.execution-interval-preparation.v2` decoder, while retaining old `scheduler.decide_interval`/`scheduler.resolve_interval` wires and behavior. This prevents silently widening the old one-delivery/no-execution authority. Whether to use this name or registered schema dispatch under the old operation is a root-recorded decision, not a Terra guess.

Root source finding: `platform/scheduler_reads.py::_historical_request` hardcodes legacy interval operation/schema/model and checks original canonical bytes/identity. `r15_scheduler_runtime._record_schema_variants` pins scheduler v1 families. Add explicit new routing/registry rows and retain legacy decoding byte-for-byte. Generic record_kind strings in DTOs are NOT registry admission.

## Bounded mandate and scoped authority

Mandate must retain exact beneficiary/service distinction; schedule/policy/bound and prompt/tool/budget bindings; consequential-tool permission; origin/recipient/delivery/disclosure scope; authority applicability and horizon. Trusted now/authentication are broker-produced observations. Frozen semantic bindings can remain exact while mutable worker/broker generations are fresh applicability inputs; freezing runtime generation into mandate applicability without a successor route would break restart/takeover.

Use nonnegative finite capacities, not globally positive maxima: zero consequential capacity must represent a scheduler restricted to no consequential calls; zero Run capacity can represent skip/no-work-only authorization. Positive consumption is rejected when capacity is zero. Horizon inclusion convention must be explicit; coordinate intervals should be compared under registered codec, not lexical strings. Fresh clock expiry remains independently mandatory.

`RegisteredScheduledServiceAuthority` joins exact selected mandate, service registration/current applicability, original scheduled initialization, selected budget predecessor, proposed consequential consumption, and complete selected source preimages. Add only to INITIALIZED_CONSEQUENTIAL_CALL. Keep AUTHORIZE_DUPLICATE_RISK human-only and PLAN_EFFECT_PUBLICATION unchanged absent a separately justified registry row. Dispatch retries cannot spend a new call budget or refresh the original intent's mandate as a workaround.

## Remaining decisions requiring root/Sol resolution and recording

A. Accounting policy: proposal is one cycle per newly selected interval/hold, Run delta exact N and zero consequential delta; replay/prefix recovery zero new consumption. Normative text requires a bounded system mandate but does not establish this precise charging policy. Decide whether new overflow holds count; charging them must not prevent required safety-hold persistence when budget is exhausted. Exhausted mandate should deny executable admission with a typed observable outcome; do not silently advance the schedule or fabricate an overflow reason.
B. Capacity/horizon: avoid positive-only maxima; decide exact zero-capacity behavior and inclusive versus half-open coordinate range. Must cover SKIP, boundary-only, coalesce, materialize-each, and OVERFLOW without inventing product requirements.
C. Authority evolution: exact immutable semantic scope vs registered allowed current authority/session changes; specify how restart/takeover stays authorized without mutating original mandate or resetting budget.
D. Registry names/operation and mandate issuance: register exact mandate, budget genesis, budget debit and V2 record families; define trusted issuance/source decoding and amendment/revocation route. Naming alone does not close that path.
E. Automatic overflow resolution: keep operator-proof path; decide whether service mandate may ever resolve after bound replacement. No automatic chunking/bypass or current-head substitution into old decision.

These are not resolved by DTO shape tests. Next prescribed step is Sol low-effort swarm design on this bounded set, then root-recorded choices, then Terra medium contract/consumer implementation. Finish all other Phase-C joins before joint behavioral tests and integrated runtime implementation. Consumer round-trips do not establish authentication, CAS, full membership, atomicity, or real hash-DAG verification.
