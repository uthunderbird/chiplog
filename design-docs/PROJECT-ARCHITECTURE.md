# Chiplog architecture — implementation handoff

## Status, authority, and assurance

This document is a compressed implementation handoff and architecture overview for Chiplog. Product semantics remain owned exclusively by [`VISION.md`](VISION.md), exact `VisionVersion: 2026-08-22.2`, whose assurance status is **bounded-incomplete**. The full implementation contract is the normative companion [`NORMATIVE.md`](project-architecture/NORMATIVE.md). When this overview selects or summarizes a mechanism, it realizes the VISION; it cannot waive a product invariant, readiness requirement, affected-party right, privacy constraint, release gate, or a stricter companion requirement.

Current process status is **`AUTHOR_STOP / CAP_LIKE / NOT_CRYSTALLIZED`**:

- 57 of the planned 64 focused attacks ran;
- cold terminal count is 1/3;
- Gate A and Gate B did not complete;
- conversation/disclosure is repaired through R63 but not independently cleared;
- logical deletion, workspace/dashboard, runtime/type/composition, evaluation, staging/migration, and verification-sufficiency focuses remain untested/open;
- document-level attacks cleared product-authority fidelity, authority partition, hexagonal enforceability, tenant/trust isolation, event recovery, effect safety, scheduler/worker fencing, and evidence durability;
- a “cleared” focus means only that its dedicated document attack returned no material objection. It is not implementation verification, adoption readiness, or release authorization.

Every real effect, disclosure, authority use, or cohort-visible evaluation remains `HOLD_ADOPTION` until the exact VISION-governed readiness, evaluation authorization, and release records exist. This document is suitable for implementation planning, not for claiming production safety.

The architecture inherits the VISION's unresolved assurance holds: repaired Plan/Fact/Journal and commitment/reliance material has not been independently re-cleared; natural-language interaction, external-information handling, and institutional/governance design did not receive dedicated terminal attacks; and neither a whole-document canonical-parent coverage audit nor a full regression sweep exists. A local fixture, cross-reference, or accepted mechanism cannot clear these holds or change `HOLD_ADOPTION`; only a successor VISION or the exact independently governed readiness and release processes may do so.

This handoff is intentionally not standalone or normative. Implementers must use the full companion for exact fields, state transitions, ordering, recovery, and fixtures; on omission, ambiguity, or conflict, [`NORMATIVE.md`](project-architecture/NORMATIVE.md) governs this overview, while VISION continues to govern product semantics. The grill source remains provenance for that companion.

## Goals and non-goals

### Goals

- One production agent loop for Telegram, CLI, scheduler, and evals.
- Durable, tenant-bound, attributable, replay-safe consequential transitions.
- Clear separation of Plan authority, Fact claims, candidate evidence, journal projections, traces, and screens.
- Local atomic decisions plus asynchronous provider convergence.
- Recovery that preserves uncertainty and never silently repeats a possible effect.
- Executable architecture claims through negative tests, crash fixtures, evals, and end-to-end traces.

### Non-goals for version one

- Microservices, distributed transactions, a tenant control-plane catalog, or cross-tenant queries.
- Transparent SQLite/PostgreSQL portability.
- Database migrations: only an initializing script for an empty exact-current-version store.
- Multi-principal/shared-channel operation.
- Physical erasure from SQLite files, WAL, snapshots, or backups.
- Production exposure, adoption readiness, or replacement of VISION semantics.

## System context

Chiplog is one Python 3.14 asyncio deployable and one tenant database per process. A startup-selected database is the tenant selection. Tenant and principal are distinct identities even though version one admits one immutable principal.

External channels are Telegram and CLI. Scheduled prompts enter through the scheduler. All use the same agent-loop application ports. `agent-dashboard` supplies screens/projections, and `promptstrings` supplies versioned prompt artifacts; neither may bypass the production loop or own domain authority.

The physical topology is a modular monolith with process-isolated authority owners behind one tenant-scoped `AuthorityBroker`. Hexagons are logical ownership and application-port boundaries, not independently deployed services.

## Domain authority model

The four semantic roles never collapse:

| Role | Owns | Cannot do |
|---|---|---|
| Plan | intended actions and planning revisions | infer facts or provider success |
| Fact | principal-confirmed factual assertions and corrections | become a Plan merely because action-like |
| Candidate evidence | provider/user observations awaiting disposition | self-promote to Fact |
| Journal | factual projection and action surface over events | create Plan or Fact authority |

`PlanningAuthorityRegistry` is a closed actor × operation × authority × proposal/adoption registry. A Plan mutation requires one exact admitted row: direct authenticated principal act, current bounded system mandate, or adopted natural-language proposal. Unknown tuples reject. `ALLOCATE_REPRESENTATION` is assessor-only and non-operative.

Natural-language planning first creates an immutable `PlanningProposal`. The displayed proposal, exact authority/current heads, complete `ProposalFreshnessBinding`, and adoption identity/digest bind the `PlanningCommand`. `AuthorityReadRecorder` records the actual typed authority-read trace; commit replays every dependency and registry input. Any missing, added, stale, conflicted, cached, injected, or unreproducible dependency writes nothing and requires redisplay/adoption.

Fact entry is narrower. `DirectFactAssertion` accepts only one authenticated principal-endorsed declarative affirmative actual-modality assertion with exact subject/payload/provenance. Questions, requests, commands, hypotheses, quotations, reports, corrections, negation, inference, ambiguity, split/merge, and consequences require immutable displayed confirmation. Fact lifecycle is `CORRECT | RETRACT | REPLACE`; correction preserves subject, replacement starts a fresh family, and concurrent successors remain conflicted until resolved.

Candidate confirmation binds immutable candidate/version, exact displayed semantics, principal, and current disposition head. Identical replay returns the prior result; changed reuse conflicts. Dependency correction/retraction/deletion yields `EVIDENCE_DIVERGED`; no projection can clear it, and planning reconciliation is a separate authorized command.

## Tenancy, bootstrap, and trust roots

Version one uses `TenantPrincipalContour.SINGLE`. Initialization creates an empty exact-current store with immutable `DatabaseGenesis`, protected `TenantDecisionJournal`, and protected `TenantTrustHeadRegistry`. The database path is not authentication.

The local bootstrap socket admits only the configured OS user holding a short-lived one-shot token. One transaction consumes it, creates the immutable principal, first credential, recovery verifier, contour, and audit record, then changes `BOOTSTRAP_REQUIRED → ACTIVE`. Before that transaction, Telegram, ordinary CLI, scheduler, model, tools, cache, delivery, and non-bootstrap writes are denied.

Credential rotation preserves principal identity. Emergency recovery requires the configured OS peer plus offline secret and atomically revokes all credentials/sessions, installs one replacement, rotates the verifier, and records the ceremony. A second principal or shared endpoint is unrepresentable.

`DeploymentTenantBinding` authenticates the exact tenant/database/genesis, operator key, journal authority/root epoch, trust registry, authority-storage manifest, lineage rules, and schema/canonicalization versions. Startup begins from the registry’s protected current head, never a caller-presented predecessor.

Trust rotation is one registry-linearized state machine:

```text
CURRENT → PREPARED → READY → ACCEPTED → CURRENT(new)
                   ↘ ABORTED → CURRENT(old)
```

`READY` and `ABORTED` directly CAS-compete on one prepared head/generation. `READY` re-reads the final old journal/materialization heads and fence in the same common order as old-root decisions. `TrustSuccessorManifest` is the closed `OPERATOR_BINDING_KEY_ROTATION | JOURNAL_ROOT_ROTATION` union; tenant/database/genesis/registry/journal identity and every unlisted field are immutable. Only the final current-head CAS admits the new root.

Restore is same-tenant, exact-current-version only. It authenticates snapshot provenance and journal prefix, restores offline, replays later decisions, recomputes authority content, then admits. Cross-tenant restore/relabel/import and historical-schema adoption do not exist in v1.

## Durable journal, SQLite, and authority reads

Before SQLite may materialize an authority-bearing mutation, `TenantDecisionJournal` irreversibly CAS-publishes one complete `DECIDED_COMMIT` over command identity/fingerprint, predecessor journal/materialized heads and commitment, complete canonical batch or recovery payload, resulting heads/commitment, and versions. A decided batch cannot abort or change. SQLite is an idempotent materialization; identical replay returns the same decision, while rival fingerprint/predecessor conflicts.

`AuthorityStorageSurfaceManifest` independently enumerates every authority-bearing table/column and executable operation→table/column read edge. `AuthorityMaterializationRegistry` maps every non-rebuildable authority record bidirectionally to that surface. Startup independently enumerates raw base records, recomputes the full content commitment, and compares it with the authenticated journal commitment. Unknown/omitted/extra/orphaned storage or read edges deny admission. Current-version exact decided prefixes may finish materialization; corrupt, rival, non-prefix, incomplete-companion, historical, or mismatched stores hold. Version one never synthesizes companions or migrates data.

Every authority-bearing read runs only in the broker on an immutable `VerifiedAuthorityReadSnapshot` whose raw authority content was recomputed against the exact current journal commitment. At execution, the actual prepared query edge must equal the manifested edge. `AuthorityReadInvalidationSurfaceManifest` and `AuthorityReadInvalidatorRegistry` map every validity read and invalidator to one broker common-order owner.

Release uses one broker order. At the authenticated response-slot enqueue cut the broker atomically records `RELEASED(result_digest, recipient, bindings, release_id)` or `STALE_OR_INDETERMINATE_READ`; only bytes from `RELEASED` cross IPC. Credential/session/contour/endpoint/storage-generation/drain/restart invalidators compete in that order. A released replay is disposition-only; retry uses a new request/attempt, freshly verified snapshot, and new response slot.

## Hexagons, composition, and runtime

Core owners include planning, facts, agent loop, effects, evidence, scheduler, projections/screens, eval, and platform/trust. `RecordOwnershipRegistry` assigns each record/revision kind exactly one writer and legal predecessor/source families. `WorkerAuthoritativeCommitRegistry` maps every worker commit path and applicability variant to exact root/session/lease/current-head fences. Unknown paths, record kinds, wrong owners, forbidden predecessors, or missing rows reject at build, startup, and runtime generation.

`HexagonBoundaryManifest` is bidirectionally compared with imports, signatures, public exports, dynamic resolution, and the realized graph. Cross-owner calls use only strict Pydantic `PublicPortCall` / `PublicPortResult | PublicPortFailure` DTOs routed by the broker. No proxy, token, callback, container, repository, SDK, raw handle, or live object crosses an owner boundary.

The synchronous owner-call graph is a versioned DAG, including callbacks/reverse edges. Logical cycles use durable asynchronous choreography. No code awaits cross-owner work while holding a transaction, SQLite admission slot, lease guard, mutex, semaphore, file lock, or manifested exclusive resource. Calls carry finite nested depth, budget, and absolute broker-clock deadline.

Each authority owner/plugin runs in its own process-isolated `RuntimeGraphGeneration` with authenticated broker session and exact capability set. Replacement drains admitted work to definite terminal or durable uncertainty before termination; no fallback routing occurs. Broker restart rotates every session/endpoint/epoch and reconciles issued operations before admission.

`BootstrapManifest` enumerates every executable: Telegram/CLI starts, scheduler/workers, recovery/admin, eval/test, and plugins. All delegate to one canonical assembly. `RuntimeCapabilityGraphAttestation` compares realized Dishka objects, factories, callbacks, scopes, owners, dependencies, and capabilities to manifests. Opaque factories run only in authority-empty isolation over inert canonical data.

Dishka scopes are `APP` (process/tenant), `REQUEST` (Run activation), `ACTION` (Turn/handler), and optional `STEP` (model/tool/provider call); `SESSION` is unused. Domain/application code uses constructor injection and never imports Dishka. `promptstrings.DishkaContext` is the sole controlled container-handle boundary and cannot cross a public port.

The process has one asyncio loop. I/O and ports are async; pure reducers are synchronous. All concurrency is structured with `TaskGroup`; no orphan tasks. Cancellation is recorded durably, blocking SDKs cross explicit thread boundaries, and resource concurrency is bounded.

Pydantic is mandatory for models/DTOs: authoritative models are frozen, strict, extra-forbid, discriminated closed unions with versioned canonical serialization. `typing.Protocol` is the default abstraction; ABC requires serious explicit justification. All code is type hinted and strict mypy is a gate.

## Event graph, Run, Turn, and model control

All typed events share one tenant-local event log with monotone `commit_seq`, stable `event_id`, `stream_id`, causal predecessors, producer, tenant/Run/Turn/kind/subject/fingerprint, and canonicalization version. Timestamps are payload, never winner authority. `ConversationHistory`, `RunTrace`, screens, and domain heads are rebuildable projections.

A **Run** is one prompt from a user or scheduler. It contains one or more **Turns**. A **Turn** is one model invocation and response, including tool calls. The model directly proposes Run completion; the runtime accepts it only through `CompleteAcceptance`. Default policy limits only `max_turns`; `NO_POLICY` is valid. Token/cost/time/retry/reserve limits are opt-in.

`TurnStarted` is a writer CAS over exact `Run: ACTIVE`, ordinal/head, current continuation-ready join, and applicable worker/lease/current-root fences. A stale, terminal, suspended, superseded, expired, or recovery-open Run cannot start a Turn.

Each Turn/call slot owns one durable `ModelCallAttemptLineage` with immutable attempt generations and one monotone selector:

```text
PREPARED_NOT_EMITTED → EMITTED_OUTCOME_UNKNOWN → RESPONSE_CAPTURED
                                                   ├→ TERMINAL_REJECTED
                                                   └→ TERMINAL_ACCEPTED
```

`PREPARED_NOT_EMITTED` journal-decides exact accumulated visibility, `ModelVisibilityManifest`, canonical request bytes/digest, provider contract/recipient tuple, worker/session, and applicable Run/root/work/lease fences. The CAS to `EMITTED_OUTCOME_UNKNOWN` commits before the first request byte becomes externally observable. Possible emission is a no-retry boundary. Replacement requires registered exact proof of no provider exposure, remains in the same lineage, increments retry generation, and advances the sole selector. Old/resumed workers cannot emit rival attempts.

`RESPONSE_CAPTURED` stores exact raw bytes/digest and provider receipt/session evidence before parsing. Parse errors, malformed/schema/semantic rejects, accepted output, trace, summaries, replay, projections, and eval all join the exact attempt/visibility manifest. One terminal CAS wins. A late response joins only the exact emitted generation; it never rewrites a terminal predecessor.

`ModelResponseReceived` atomically seals the ordered tool-call set and publishes every `ToolCallSubjectInitialized`; partial/reordered/rival fan-out is invisible. `RecoveryFrontierRegistry` defines complete Run/Turn/response/call/effect/evidence/delivery/policy/recipient/root head families. Recovery never trusts a cursor or incomplete trace.

Run terminalization uses `SealedCallAccountingJoin`: every sealed call is terminal or has an exact recovery obligation. A later Turn or `CompleteAcceptance` requires the stronger current `ModelContinuationReadyJoin`, so open recovery, pending retry, stale outcome/closure, or unconsumable evidence blocks continuation.

Suspension publishes one immutable `SuspensionBaseline`. Same-Run resume requires exact equality or an `AdmissibleFrontierSuccessor` proof composed only of permitted evidence/obligation advances and a writer transaction that reclassifies `SAME_RUN` while CAS-advancing suspended→active. Otherwise disposition is `SUCCESSOR_REQUIRED`, `RECOVERY_HOLD`, or `TERMINAL_RECOVERY_FAULT`.

A successor Run is one atomic `RunSuccessorTransition`: predecessor supersession, successor, edge, initialization, applicable execution-lineage current-Run advance, and complete frontier proof. It references independently owned obligations as `REFERENCE_EXTERNAL`; it never transfers or copies them. Crossed no-retry boundaries remain inherited.

## Tools, effects, uncertainty, and recovery

Consequential tools leave initialization only through a `ToolCallAccepted` CAS that revalidates exact Run/current heads, authority/policy/tool schema, and applicable worker lease, then atomically publishes `ToolExecutionIntent` and any `ExternalActionIntent`. No adapter dispatches before this batch. Cancellation is legal only from the exact still-unaccepted initialized head.

Read-only retries use a distinct `ReadOnlyToolCallAccepted` and immutable `ReadOnlyRetryLineage`. Retry requires positive read-only proof, exact snapshot/query identity, previous `FAILED_DEFINITE` explicitly retryable under frozen reducer/version, no recovery obligation, and remaining lineage-wide budget. Success, unknown, non-retryable failure, exhaustion, or terminal call forbids retry. Pending cross-successor retry is exactly `READ_ONLY_RETRY_PENDING`; it blocks continuation/Run terminalization and never resets the shared counter.

Every sealed call has one `ToolTerminalDisposition`:

| Disposition | Required meaning |
|---|---|
| `SUCCEEDED` | typed success result |
| `FAILED_DEFINITE` | typed definite error |
| `OUTCOME_UNKNOWN` | accepted/exposed work may have occurred; no blind retry |
| `CANCELLED_BEFORE_ACCEPT` | exact not-executed initialized predecessor |
| `RECOVERY_REQUIRED` | immutable recovery obligation; no fabricated result |

Recovered semantics are a separate immutable `RecoveredToolOutcome` bound to exact terminal call, obligation, authenticated evidence, reducer/version, and closure head. It never rewrites original uncertainty. Evidence append and semantic reduction are separate CAS transitions; compatible refinement may advance the stable reduction identity, while contradictory/meaning-changing/reducer-changing evidence holds future consumers.

All external effects import the version-bound normative VISION dispatch reducer. Every `ExternalActionIntent`, `DispatchAuthorization`, and `TransmissionAttempt` carries immutable `DispatchSemanticBinding` over reducer/manifest, transition registry, fingerprint/canonicalization, and adapter-contract versions. At every action-authorizing cut, exact supported equality is required.

`SEND_COMMITTED` is the last local reversible boundary. At/after it, timeout, connection loss, malformed/conflicting evidence, or unavailable idempotency preserves `OUTCOME_UNKNOWN`/`PARTIAL`; no upgrade, restart, migration, reconciliation, or worker may silently replay, reset ambiguity, or allocate a rival intent. Safe retransmission requires the normative proof; duplicate-risk continuation and compensation require separate explicit authorization. Compensation is a fresh intent with complete current authority/affected-party/dependency/consequence/proposal/freshness bindings and exact reference to the original ambiguity; it never rewrites the original.

## Evidence ingress, overload, polling, and shutdown

`EvidenceIngressSurfaceManifest` covers exactly Telegram push/poll, CLI, provider callback/poll, reconciliation, and tool-result paths. Each row names owner/source, earliest irreversible handoff, authentication, maxima, receipt-token derivation, custody successors, acknowledgement/cursor/release behavior, quarantine, shutdown, and restart. Dashboard/screens have zero evidence paths; external state enters only through registered adapters.

Before destructive receive/read, the broker journal-decides `EvidenceReceiptToken.ALLOCATED_LOSS_SLOT`; after reading, it CAS-stages exact inert bytes as `RAW_STAGED` before authentication. Cooperative sources instead prove exact retention/redelivery. Both are no-authority. Each token reaches exactly one `EvidenceCustodyState`:

`ADMITTED_DURABLE | RETRY_WITH_SOURCE_CUSTODY | TERMINAL_REJECT | QUARANTINED_RAW_EVIDENCE | LOSS_OBLIGATION`.

Local acknowledgement, CLI success, polling cursor advance, or source release requires exact durable custody. Digest-only is not custody. Unknown/lost non-redeliverable input becomes a durable non-acknowledging loss obligation.

`EvidenceAdmissionEpoch` owns bounded global/per-source item, byte, and quarantine reserves; strict per-class ready FIFO; positive byte quanta; admission snapshots; cancellation shielding; and `OPEN → QUIESCING → DRAINING → CLOSED`. Physical reserves are never borrowed. Every admitted evidence or `ORDINARY` item snapshots its complete predecessor prefix, sizes, starting deficit, formula/version, origin slot, and absolute deadline. A blocked predecessor atomically leaves ready FIFO and rebases descendants without extending deadlines. Drain records every token/disposition/FIFO/snapshot/deficit/remainder for restart.

Polling first authenticates the complete raw page and creates `PollResponsePageManifest` over predecessor/candidate cursor and ordered raw membership. Every member must durably become materialized, exact duplicate, deterministic terminal non-evidence, or exact-byte quarantine before cursor authorization. `POLL_CURSOR_APPLIED` durability precedes emitting any request carrying that cursor.

Quarantine retains exact bytes and has one monotone `QuarantineReprocessingHead`. Attempts CAS-select parser/version and result `MATERIALIZED | VERSION_INDEPENDENT_TERMINAL_NON_EVIDENCE | RETRY_OR_HOLD`. Parser failure/unknown cannot terminalize. One inbox materialization or registered byte-level proof terminalizes forever; raw custody never changes.

## Scheduler and worker fencing

Each occurrence identity is `schedule_id + schedule_revision + canonical_due_coordinate`. Every interval command binds exact current `ScheduleDefinitionHead`, `MissedOccurrencePolicyHead`, `SchedulerIntervalBoundHead`, previous boundary, cutoff, frontier, and complete independently enumerated eligible manifest. Stale heads write nothing.

`ScheduledIntervalDecisionBatch` is the closed union:

`BOUNDARY_ONLY_NO_WORK | SKIP_ALL | COALESCE_SINGLE | COALESCE_MULTI | MATERIALIZE_EACH_SINGLE | MATERIALIZE_EACH_MULTI | OVERFLOW_HOLD`.

Policy algebra is exact:

| Policy | 0 members | 1 member | 2..MAX |
|---|---|---|---|
| SKIP | boundary only | skip-all | atomic skip-all |
| COALESCE | boundary only | individual Run | one aggregate/Run |
| MATERIALIZE_EACH | boundary only | individual Run | atomic N independent Runs |

No boundary advances past an undisposed member. No automatic chunking exists. Count, manifest-byte, or serialized-batch overflow publishes durable `OVERFLOW_HOLD`, no dispositions/Runs/boundary. Amendment is forbidden while the hold is active. An operator must install a monotone sufficient successor bound and publish one `SchedulerIntervalResolutionDecision` that consumes the hold, parents every sub-batch, and advances the boundary atomically.

Every materialized subject creates one stable `ExecutionLineage`, one initial Run, one selected internal `PhysicalExecutionRoot` epoch, and one `UNLEASED` genesis lease in the same batch. Runs permanently bind the stable lineage; rollover never creates a synthetic Run.

`ExecutionRootLeaseState` uses uint64 generation and closed acquire/renew/expired-takeover CAS rules over exact holder/session/lease/head/expiry/trusted-clock proof. Pre-expiry takeover rejects. Exhaustion publishes `GENERATION_EXHAUSTED_HOLD`; authenticated journal-decided `PhysicalExecutionRootRolloverDecision` creates a fresh non-aliasing epoch, terminally fences old authority, and advances `ExecutionLineageCurrentPhysicalRootSelector`. No wrap/reset/reuse.

Worker commits bind stable lineage, exact selected physical epoch/root selector, current Run, and live lease. Scheduler pre-root decisions use a distinct applicability row with absent root/Run/lease markers. Independently authenticated late evidence never needs a worker lease.

Terminal Runs with open obligations create stable `PostTerminalRecoveryWorkSubject` plus replaceable internal `WorkLeaseEpoch` and selector. Claim/renew/takeover/rollover mirror root-lease fencing without transferring evidence or obligation authority. Work-epoch exhaustion rolls to a fresh epoch and preserves the open obligation.

Startup/recovery bidirectionally joins interval decisions, dispositions, lineages, Runs, initial epochs/leases, resolution parents, and selectors. Missing/extra/orphan/rival/partial/cyclic state holds before leasing.

## Conversation, disclosure, delivery, and history

Each tenant has one canonical agent conversation spanning Telegram, CLI, and scheduled prompts. Agent context is source-independent; human-visible histories are channel-specific and do not automatically mirror old messages. In the single-principal contour, this is presentation, not confidentiality: absent an explicit label, the agent may use tenant-wide history in any authorized channel.

Conversation is always in context subject to budget. Read-only history tools expose originals, search, ranges, threads, and Run lookup. `ConversationHistory` stores user/scheduled prompts and accepted completions. `RunTrace` stores Turns, tool/model attempts, rejects, obligations, and failures.

Disclosure uses a closed versioned `DisclosureLabel` join-semilattice. Explicit bottom means unrestricted under the exact current policy; absent/unknown/unavailable is not bottom and fails closed. Every content record carries `DisclosureProvenanceEnvelope`.

Each Turn owns a durable monotone `TurnVisibilityAccumulator`. Before every model request, `ModelVisibilityManifest` enumerates all content-bearing bytes actually visible: conversation originals/summaries, prompt/policy/instructions/tool schema, retained screens/attention, history/search/index/tool reads, prior results, and trace-derived inputs. Every model-produced byte—including tool arguments, free prose, accepted/rejected response, parse errors, and trace—inherits the whole canonical join independent of citations or paraphrase.

`DisclosureSurfaceManifest` is bidirectionally equal to executable content paths across ingress/events/tools, model input/output/reject, conversation/summary/history/search/index, screen/cache/context, delivery/render/send, replay/rebuild, and eval. Unknown, omitted, extra, duplicate, alias, dynamic substitution, or missing envelope denies generation/exposure/delivery.

Narrowing is one principal-authorized `DisclosureNarrowingDecision` CAS over exact content revision/bytes, complete provenance, all source/predecessor label and prior-narrowing heads, lattice/policy, scope, purpose, expiry, actor, and fingerprint. It creates a prospective successor only. Existing traces, summaries, caches, indexes, conversation records, renderings, and deliveries keep the old restriction.

Delivery input is normalized before acceptance to exactly one explicit `ORIGIN_EXACT | MODEL_SELECTED_EXACT`; omission becomes exact origin and never persists. `DeliveryManifest` binds selection, payload/render digest, visibility/provenance/label/narrowing heads, and policy/version; exact provider-recipient tuples are deduplicated.

`Complete` payload is `NonAuthoritativeText | DeliveryAssertion`. Free prose is always prominently rendered `UNVERIFIED MODEL COMMENTARY`. Consequential statements require typed assertions referencing exact current Plan/Fact/candidate/provider records and entailed assertion codes. `CompleteAcceptance` atomically validates selected response attempt/schema, continuation readiness, evidence/current heads, authority/policy, disclosure/routing, deterministic rendering, accepted conversation result, delivery intents, `TERMINAL_ACCEPTED`, and `Run: ACTIVE → SUCCEEDED`. Failure publishes none of those semantic-success records; rejected bytes remain labeled trace evidence.

Delivery-specific `SEND_COMMITTED` revalidates the exact local endpoint ownership/address/credential/binding, provider-recipient tuple, disclosure policy, and every label/narrowing head in one broker order with invalidators. Version one guarantees exact authenticated provider tuple and local routing integrity, not provider non-reassignment, uncompromised routing, or same-human identity. Provider-side anomalies are external evidence/reconciliation, never retroactive confidentiality proof.

## Workspace, screens, dashboards, and journal

Turn context consists of fixed policy/tool/schema material, mandatory `core.conversation`, optional bounded `attention`, up to three retained domain families, and compact Run status. Conversation is outside the LRU and only its budget changes.

The family tail is LRU with default length 3. Retained families render least-recently-used to most-recently-used so current focus is last. Eviction removes prompt projection, not navigation state or authority.

Initial families:

| Family | Purpose |
|---|---|
| `core.planning` | intentions, proposals, Plan state |
| `core.calendar` | schedules, occurrences, provider convergence |
| `core.journal` | fact journal and action surface, separate from Plan |

`attention` is a conditional cross-cutting highlight/navigation projection, not a family. Run status is cross-cutting trace/uncertainty context.

`DashboardFamilySpec` is a closed registry with family ID, screen routes, builder/query ports, budgets, provenance and refresh rules. `ScreenHub` owns navigation; `ScreenLocation` and `ScreenSnapshotRef` bind tenant, principal contour, family/screen, params, frontier, registry/version, and provenance. `DashboardScreen` is immutable presentation. Screens share one authoritative Turn frontier unless explicitly marked lagging; lagging screens cannot satisfy authority-sensitive preconditions.

`agent-dashboard` is the projection implementation. A screen never becomes evidence or authority. External state enters only through tool/provider/reconciliation adapters. `promptstrings` owns versioned prompt templates/manifests and integrates through the same Dishka composition and prompt fingerprints.

## Logical deletion

Version one promises logical exclusion, not physical erasure. Authorized deletion commits its authoritative fence before, or atomically with, exclusion work. The fence immediately blocks every new use and is the authority even if the disposable reverse-dependency index is absent or rebuilding. Exclusion then traverses rebuildable reverse dependencies and removes semantic payload from ordinary reads, prompt assembly, tools, projections, summaries, caches, screens, search, history, delivery, replay/import, and eval. Every content-bearing derivative and pending action has complete typed provenance; missing provenance fails closed. Dependent ordinary derivatives are purged or recomputed, inferred state is invalidated, and authority- or evidence-dependent pending work is cancelled or held at its last reversible boundary.

Only policy-minimal, non-dereferenceable tombstone and ordering metadata may remain in ordinary state. SQLite files, WAL, snapshots, backups, and quarantine copies may retain physical bytes, but the deletion fence prevents those residues from rematerializing or reconstructing deleted content into ordinary state through rebuild, replay, restore, or import. Retention periods remain policy-owned. Physical erasure becomes mandatory only if the product later claims irrecoverability; it requires separate keys/storage/backup evidence and is currently open.

## Evaluation framework

Production and eval use the same assembly, broker, owner partition, routing DAG, scopes, orchestration loop, prompt/tool schemas, and policy. Evals may replace only registered leaf adapters inside the same owner with identical capability closure. No eval-only broker, weaker IPC, skipped epoch/replay, extra read, private runner, or direct repository access.

An eval scenario binds initial tenant event graph, external `agent-dashboard` screen/system state, promptstrings artifacts, model/provider/tool adapters, policy, expected trace, and scoring versions. Production observations are recorded as immutable eval inputs; eval cannot create product authority.

Scoring has two classes:

- deterministic features: exact state, event/head, dashboard state, required/forbidden tool/effect, disclosure or hard invariant;
- nondeterministic features: response quality, instruction following, read/tool ordering, natural-language actions, scored by versioned LLM-as-a-judge.

Deterministic hard failures cannot be averaged away by a judge. Feature vectors remain immutable. Judge disagreement yields `INCONCLUSIVE` and blocks promotion. Dev runs one trajectory/judge pass; promotion uses at least three trajectories and two independently seeded judge passes per trajectory. A rerun is a new eval identity.

## Deployment stages

| Stage | Deliverable | Mandatory gates |
|---|---|---|
| 0 — skeleton | canonical assembly, hexagons, tenant bootstrap, journal/genesis, manifests, event store, CLI/Telegram ingress, empty screens | exact-current startup, boundary/runtime graph attestation, no real effects |
| 1 — read-only loop | conversation/history, promptstrings, dynamic schemas, model attempt lineage, read-only tools, dashboard families | whole-call visibility, authority-read release, no consequential dispatch |
| 2 — recoverable loop | Plan/effect workflow, Run/Turn recovery, scheduler, delivery, inbox/outbox, reconciliation | imported VISION dispatch reducer, deployment permit at every real last boundary, evidence custody, leases, no-retry/unknown handling |
| 3 — eval-first growth | scenario bundles, deterministic features, judges, replay, adapter suites | production-equivalent harness; synthetic/offline by default; explicit evaluation permit for cohort exposure |
| 4 — long-term product | remaining Plan/Fact/Journal, commitments, affected-party, multi-party privacy, physical erasure/import/migration, governance | complete clause coverage, independent conformance review, readiness and release authorization |

The operation-level `DeploymentGatePort` returns only `PERMIT_EXACT_EVALUATION | PERMIT_EXACT_PRODUCTION | HOLD`. It binds exact operation/surface/capability/cohort/mode, deployment generation, readiness/evidence freshness, and evaluation/release head. Absence, ambiguity, unsupported input, or adapter failure is `HOLD`. A permit is revalidated at the exact last reversible boundary.

## Verification matrix

Implementation evidence must include:

| Family | Required negative evidence |
|---|---|
| trust/startup | swapped tenant/path/genesis/root/manifest/schema/content; rollback; partial rotation; old journal |
| hexagons/runtime | private imports, dynamic aliases, unknown bootstrap, owner/proxy/session escape, cycle/deadlock, stale generation |
| Run/Turn/model | crash at every attempt state; old/resumed rivalry; late response; missing manifest; unknown-emission retry |
| tools/effects | pre-accept dispatch, stale Run/lease, cancellation after accept, unknown retry, version mismatch, compensation race |
| evidence | transfer before token, digest-only custody, overload, shutdown, lost slot, poison page, cursor-before-apply |
| scheduler | stale schedule/policy/bound, zero/one/N/overflow, partial batch, hold/amendment, lease takeover/exhaustion/rollover |
| disclosure | omitted visibility source, unknown content path/label, rejected trace leak, stale narrowing, endpoint invalidator/send race |
| dashboards/history | cross-tenant/principal keys, lagging authority use, LRU order/tail, screen-as-evidence, rebuild mismatch |
| eval | privileged fake, hard-gate averaging, judge disagreement, sample mutation, production/eval graph drift |
| deletion | ordinary read/search/cache/replay/import resurrection, descendant omission, pending-action stale fence |

Passing a fixture proves only the exact artifact/version/provider conditions tested.

## Normative preservation index

This index maps architecture-level named contracts into the overview. It is a navigation and compression check, not a replacement for the normative companion. Each row points to the overview section that summarizes identity/state, owner, CAS/transaction boundary, failure, and recovery behavior; exact requirements remain in [`NORMATIVE.md`](project-architecture/NORMATIVE.md). Scalar enum members are grouped with their owning family rather than repeated individually.

| Contract family | Preserved contracts | Section |
|---|---|---|
| trust/genesis | `DatabaseGenesis`, `TenantDecisionJournal`, `TenantTrustHeadRegistry`, `DeploymentTenantBinding`, `TrustTransitionHead`, `TrustSuccessorManifest`, `PrincipalContourPrerequisiteRegistry`, `PrincipalContourAttestation` | Tenancy, bootstrap, and trust roots |
| storage/read | `DECIDED_COMMIT`, `AuthorityStorageSurfaceManifest`, `AuthorityMaterializationRegistry`, `VerifiedAuthorityReadSnapshot`, `ReadOperation`, `AuthorityReadInvalidationSurfaceManifest`, `AuthorityReadInvalidatorRegistry`, `AuthorityReadReleaseHead` | Durable journal, SQLite, and authority reads |
| assembly/owners | `AuthorityBroker`, `HexagonBoundaryManifest`, `RecordOwnershipRegistry`, `WorkerAuthoritativeCommitRegistry`, `BootstrapManifest`, `RuntimeCapabilityGraphAttestation`, `RuntimeGraphGeneration`, `PublicPortCall`, `PublicPortResult`, `PublicPortFailure` | Hexagons, composition, and runtime |
| planning/facts | `PlanningAuthorityRegistry`, `PlanningProposal`, `PlanningCommand`, `PlanningCommittedResult`, `ProposalFreshnessBinding`, `AuthorityReadRecorder`, `DirectFactAssertion`, `ConfirmedFactAssertion`, `FactAssertionConfirmationDisplay`, `FactClaim`, `ClaimDisposition` | Domain authority model |
| event/recovery | `RecoveryFrontierRegistry`, `SuspensionBaseline`, `AdmissibleFrontierSuccessor`, `RecoveryProofDisposition`, `RunSuccessorTransition`, `SealedCallAccountingJoin`, `ModelContinuationReadyJoin`, `RunTerminalAccountingManifest` | Event graph, Run, Turn, and model control |
| model call | `TurnStarted`, `TurnVisibilityAccumulator`, `ModelVisibilityManifest`, `ModelCallAttemptLineage`, `ModelCallAttempt`, `ModelResponseReceived`, `CompleteAcceptance` | Event graph; Conversation/disclosure |
| tools | `ToolSpec`, `ToolCallSubjectInitialized`, `ToolCallAccepted`, `ReadOnlyToolCallAccepted`, `ReadOnlyCallOutcome`, `CallRecoveryFrontier`, `ToolTerminalDisposition`, `ToolResultRecorded`, `ToolResultRecoveryObligation`, `RecoveredToolOutcome`, `SemanticEvidenceReduction` | Tools, effects, uncertainty, and recovery |
| effects | `ExternalActionIntent`, `ToolExecutionIntent`, `DispatchAuthorization`, `DispatchSemanticBinding`, `TransmissionAttempt`, `DeploymentGatePort`, `DeploymentGateGeneration` | Tools/effects; Deployment stages |
| evidence | `EvidenceIngressSurfaceManifest`, `EvidenceReceiptToken`, `EvidenceAuthenticationBinding`, `EvidenceCustodyState`, `EvidenceAdmissionEpoch`, `AdmissionSelectionBoundSnapshot`, `EvidenceDrainManifest`, `PollResponsePageManifest`, `QuarantineReprocessingHead` | Evidence ingress, overload, polling, and shutdown |
| scheduler | `ScheduleDefinitionHead`, `MissedOccurrencePolicyHead`, `SchedulerIntervalBoundHead`, `SchedulerEligibilityBoundary`, `SchedulerEligibilityManifest`, `ScheduledIntervalDecisionBatch`, `OccurrenceDispositionRegistry`, `ScheduledOccurrenceMaterializationBatch`, `SchedulerOverflowHoldState`, `SchedulerIntervalResolutionDecision` | Scheduler and worker fencing |
| execution lease | `ExecutionLineageSubject`, `ExecutionLineage`, `PhysicalExecutionRoot`, `ExecutionLineageCurrentPhysicalRootSelector`, `ExecutionRootLeaseState`, `PhysicalExecutionRootRolloverDecision`, `PhysicalExecutionRootRolloverEdge`, `PostTerminalRecoveryWorkSubject`, `WorkLeaseEpoch`, `PostTerminalWorkCurrentEpochSelector`, `PostTerminalWorkLeaseState`, `PostTerminalWorkEpochRolloverDecision` | Scheduler and worker fencing |
| disclosure/delivery | `DisclosureLabel`, `DisclosureProvenanceEnvelope`, `DisclosureSurfaceManifest`, `DisclosureNarrowingDecision`, `DeliveryEndpointSelection`, `DeliveryManifest`, `NonAuthoritativeText`, `DeliveryAssertion`, `ConversationHistory`, `RunTrace` | Conversation, disclosure, delivery, and history |
| dashboard/prompt | `DashboardFamilySpec`, `DashboardBuilder`, `DashboardScreen`, `ScreenHub`, `ScreenLocation`, `ScreenSnapshotRef`, `TurnContextBudgeter`, `PromptSource` | Workspace, screens, dashboards, and journal |
| release/eval | `DeploymentGatePort`, `EvaluationAuthorization`, `VisionReleaseProfile`, `ClauseCoverageLedger` | Evaluation framework; Deployment stages; Status |

Excluded from this non-normative overview during compression: round chronology, attack narratives, repair accounting, duplicate fixture prose, illustrative variable names (`B0`, `C`, `D_i`, `M_i`, `Q_i`, `N`, `R0`), and historical superseded rationales. No named contract family is intentionally omitted, but implementation relies on the companion's complete wording.

### Source-token coverage

The mechanical named-token inventory also contains enum members, port/type aliases, and transport states whose semantics are owned by the indexed families above. They are mapped here so the preservation check is closed rather than silently discarding them:

- lifecycle/status: `ABSENT`, `ACCEPTED`, `ACTIVE`, `ADMITTING`, `BLOCKED_HOLD`, `BOOTSTRAP_REQUIRED`, `BOUND_SUSPENDED_BLOCKED`, `CANCELLED`, `CLOSED`, `CLOSED_BEFORE_TRANSITION`, `CONSUMABLE`, `CREATED`, `CURRENT`, `DEGRADED`, `DRAINING`, `HELD`, `PREPARED`, `QUIESCING`, `RECONCILING`, `REPRESENTED`, `SENT`, `SINGLE`, `SUPERSEDED`, `SUSPENDED`, `TERMINAL`, `UNDISPOSED`, `UNSUPPORTED_SCHEMA_VERSION`;
- trust/runtime aliases: `BrokerEpoch`, `EventAppender`, `EventStore`, `RuntimeGraphGenerationPort`, `Protocol`, `Any`, `Callable`, `INERT_SHARED_DATA`, `POLICY_FREE_SHARED`, `ONE_SHOT`, `IDEMPOTENT_EXACT`, `OPERATOR_BINDING_KEY_ROTATION`, `JOURNAL_ROOT_ROTATION`, `PREDECESSOR_ACCEPTED`, `SUCCESSOR_PREPARED`, `SUCCESSOR_READY`, `MULTI_PRINCIPAL`;
- ingress/channel: `AuthenticatedIngressRef`, `ChannelAuthenticationBinding`, `TelegramCandidateRef`, `TransportOriginWitness`, `TELEGRAM_POLLING`, `TELEGRAM_WEBHOOK`, `ALLOCATED_LOSS_SLOT`, `PRE_AUTH`, `PROVEN_SOURCE_RETENTION`, `PREALLOCATED_LOSS_SLOT_THEN_RAW_STAGE`, `RAW_STAGED`, `LOCAL_ACK_AUTHORIZED`, `CLI_RESPONSE_ATTEMPT_ISSUED`, `CLI_RESPONSE_LOCAL_COMPLETION_OBSERVED`, `PUSH_RESPONSE_ATTEMPT_ISSUED`, `PUSH_RESPONSE_LOCAL_COMPLETION_OBSERVED`, `PROVIDER_RECEIPT_OBSERVED`, `RECONCILIATION_RELEASE_AUTHORIZED`, `RECONCILIATION_OBLIGATION_RELEASED`, `RECONCILE_DELIVERY`;
- evidence/polling: `ADMITTED_DURABLE`, `RETRY_WITH_SOURCE_CUSTODY`, `TERMINAL_REJECT`, `QUARANTINED_RAW_EVIDENCE`, `LOSS_OBLIGATION`, `MATERIALIZED`, `DUPLICATE_OF`, `TERMINAL_NON_EVIDENCE_REJECT`, `VERSION_INDEPENDENT_TERMINAL_NON_EVIDENCE`, `RETRY_OR_HOLD`, `POLL_CURSOR_ADVANCE_AUTHORIZED`, `POLL_CURSOR_APPLIED`, `DURABLE_CURSOR_BEFORE_REQUEST`, `PREFIX_REBASED_BLOCKED`, `ORDINARY`;
- scheduler/lease: `BOUNDARY_ONLY_NO_WORK`, `SKIP`, `SKIP_ALL`, `SKIPPED`, `COALESCE`, `COALESCE_SINGLE`, `COALESCE_MULTI`, `COALESCED`, `COALESCED_INTO`, `MATERIALIZE_EACH`, `MATERIALIZE_EACH_SINGLE`, `MATERIALIZE_EACH_MULTI`, `INDIVIDUAL`, `CLAIMED`, `CLAIMED_INDIVIDUALLY`, `ACQUIRE`, `RENEW`, `TAKEOVER`, `UNLEASED`, `UINT64_MAX`, `RESOLVED_BY_OPERATOR`, `PRE_ROOT_SCHEDULER_DECISION`, `EXECUTION_ROOT_LIVE_LEASE`, `NON_SCHEDULER_NOT_APPLICABLE`, `POST_TERMINAL_RECOVERY_WORK`, `ScheduledBatchPrimitiveDomainV1`, `ScheduledBatchFinalizedMembersV1`, `CoalescedOccurrenceAggregateId`, `ExecutionLineage.current_run_id`, `WorkOccurrence`;
- planning/fact: `PLAN`, `Proposal`, `ClaimSubject`, `InterpretationRevision`, `CORRECT`, `RETRACT`, `REPLACE`;
- Run/tool/model: `Run`, `Turn`, `Continue`, `CALL_REDUCED_TERMINAL`, `NEXT_ATTEMPT_ACCEPTED`, `NOT_APPLICABLE`, `TRANSFER_OPEN`, `TERMINAL_REJECTED`, `FIRST_PUBLICATION`;
- effect/release: `DISPATCH_VERSION_DENIED`, `DISPATCH_VERSION_HOLD`, `EVALUATION`, `PRODUCTION`, `MODEL_SELECTED_EXACT`, `ORIGIN_EXACT`, `EvaluationAuthorization.ACTIVE`, `VisionReleaseProfile.ACTIVE`.

## Open implementation issues

- Physical-erasure mechanism and proof if irrecoverability is ever claimed.
- Tenant payload-key ownership and backup/restore isolation after the v1 trigger.
- Migration/import/re-enveloping design; v1 has none.
- Multi-principal/shared-channel isolation and independently attested migration.
- Provider-specific proof-of-no-exposure and stable recipient-contract support.
- Complete capability-to-VISION clause coverage manifests and independent attestation.
- Independent attacks for all focus-ladder rows still open, plus Gate A and Gate B.
- PostgreSQL only after a measured trigger and a designed migration.

## Implementation handoff

Begin with Stage 0 and preserve the named contract boundaries from day one. Do not implement a happy-path agent loop first and retrofit journal, custody, authority, or no-retry semantics later: the first externally observable byte, first durable authority write, and first local acknowledgement are the load-bearing cuts.

Before a capability advances stages, require its closed surface inventory, exact owner/record registry, state-machine/property tests, crash cuts, production/eval graph equivalence, and operation-level deployment hold. Any unknown path or unavailable proof remains a typed hold rather than a permissive fallback.
