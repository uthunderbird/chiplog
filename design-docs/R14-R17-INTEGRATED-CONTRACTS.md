# Integrated contract graph, Phase C

Status: OPEN. This is the concrete contract inventory for
[R14–R17 integrated plan](R14-R17-INTEGRATED-PLAN.md), not a runtime or full-freeze
claim. Root owns the graph, consumer tests and source inventory. Baseline `feec6e9`
was clean; the reviewed cancellation contract `41e1886` is imported without changing
its wire or claiming cancellation implementation. Historical schemas remain pinned.

Inspection: AST enumeration of classes/fields in owner `*contracts.py`, broker
`_ingress_contracts.py` and `_owner_publication_contracts.py`, followed by reading
the actual referenced definitions and their preparation/runtime consumers.
The operation-name union in `_owner_publication_contracts.py` is not evidence of
a corresponding callable producer or registered handler.

## Complete graph and unresolved contract work

Symbols below are relative to `src/chiplog`. Existing types are retained unless a
new version is explicitly needed. No row is CONTRACTS READY from class existence.

| Joint histories / required boundary | Existing request/result surface | Remaining Phase-C work |
|---|---|---|
| J1: all ingress classes, custody/release | `platform._ingress_contracts`: ReceiptToken, TokenState, CustodyState, AuthenticationBinding; `composition.r17_ingress_registry` retained CLI commands | Callable source-specific handoff, release and custody transition requests/results for Telegram push/poll, CLI, provider callback/poll, reconciliation and tool result; preserve actual source authentication and loss-slot requirements |
| J1: admission/page/quarantine | Same broker module: AdmissionBound, BlockedRebase, DrainManifest, PollPageManifest, PollMemberDisposition, ParserAttempt; `_ingress_domain` pure functions | Durable command/result contracts for queue selection, complete rebase, restart/drain, parser CAS and page/cursor/ack. Pure snapshots are not public runtime ports |
| J1→J3: selected inbox → Run | `agent_loop.execution_transition_contracts.CreateExecutionRun` → ExecutionTransitionProposal | Separate admitted-input creation request retaining selected inbox/raw/authentication/origin and scheduler initialization, rather than rebuilding ingress from prompt text |
| J2: scheduler decisions and execution | `agent_loop.scheduler_contracts.SchedulerPort`, DecideIntervalCommand, ResolveIntervalCommand, LeaseTransitionCommand, PhysicalRootRolloverCommand → SchedulerResult | Public automatic service-authority contract beyond one-delivery TickPolicy; executable Run materialization and lease joins must not fall back to legacy RunRecord |
| J3: seal/accept/cancel | ExecutionCapturedFanOutRequest/Result; composition CallAcceptancePort; ExecutionCallCancellationPort → ExecutionCancelledCallReceipt | Import cancellation boundary; retain two-record execution cancellation versus three-record legacy cancellation. Runtime authentication and both CAS orders remain Phase T/I work |
| J4: effect dispatch/retry/delivery/compensation | effects DispatchMandateV2, ExternalActionIntentV2, AuthorizeDispatchV2, CommitFirstSendV2; DispatchOutcomeCommandV2/RecordV2; legacy SafeRetransmission and purpose unions | A versioned full lifecycle contract must join safe retransmission/all-child proofs, separately scoped compensation and delivery to immutable v2 mandates without reinterpreting legacy intents |
| J4→J5: original-stream reduction | agent_loop EvidenceReduction, OriginalObligationBinding, TerminalCallFrontier; effects DispatchObligationV2 | Callable original resolver and evidence-reduction CAS requests/results; bridge owner-produced outcome/closure/current reduction into loop terminal records without caller-created success or stream transfer |
| J5: frontier/suspend/resume/successor | RecoveryFrontier, SuspensionBaseline, ResumeCommand/SuccessorCommand → RecoveryResult; RecoveryPort | Executable Run preparation requests/results for suspension, both continuation consumers (TurnStarted and CompleteAcceptance), same-Run resume and atomic successor; exact complete inventory and source cut |
| J5: read-only branch | ReadOnlyRetryLineage, ReadOnlyAttemptMember, PendingCallFrontier, ReadOnlyAcceptedCall; FanOutToolPolicy READ_ONLY | Callable acceptance/attempt/pending/reduction requests, registered executable read-only tool wire and positive no-mutation proof; existing execution tool set contains only proposal tools and request_self_effect |
| J6: writer applicability/discovery | WorkerCommitApplicability has six closed fence variants; broker WorkerAuthentication | Concrete registry rows and discovery observation contracts for command/handler/schema/record/applicability, checked in both directions; a source hash catalog is not this registry |
| J6: post-terminal work | WorkSubjectBinding, WorkEpochBinding, PostTerminalWorkFence, WorkEpochRolloverFence | Materialize the closed work-lease state, owner preparation requests/results, terminal-manifest work creation, exact close and monotone rollover boundary; consumers preserve original obligation/evidence references |
| J6: model-attempt recovery | ExecutionModelAttempt has five states; legacy NoExposureProof only describes hermetic pre-emission CAS | Separate executable replacement request/result retaining complete Run/attempt/selector and registered no-exposure proof; distinguish authenticated late evidence from selected response capture |
| J7: completion/delivery/channel driver | DeliveryCompletion + DeliveryObservation → DeliveryAcceptanceProposal; DeliveryPublicationPort → PreparedDeliveryPublication; broker CompleteDeliveryBatch | Executable CompleteAcceptance consumes actual captured response and current continuation proof, with owner-produced effect companions. Public common driver/receipts for CLI/Telegram preserve distinct authentication and same committed projection |

These are work packages within one contract phase. No missing row is moved to
post-implementation detail coverage. The common runtime is implemented only after
the graph is frozen and the joint behavioral tests are written.

## Guarantees for work ownership and model-attempt recovery

The new owner-local modules are `agent_loop.post_terminal_contracts` and
`agent_loop.model_attempt_recovery_contracts`. All objects are inert proposals;
broker-authenticated sources and publication remain mandatory.

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture / planned evidence |
|---|---|---|---|---|
| Original work ownership | Stable WorkSubjectBinding + exact original obligation and terminal-manifest member | Consumer retains canonical subject bytes across claim/takeover/rollover; runtime compares selected original streams | New obligation stream or copied closure | missing/extra/aliased subject and physical epoch; consumer then J6 runtime |
| Closed bounded work lease | Owner UNLEASED(0), CLAIMED, GENERATION_EXHAUSTED_HOLD(max), CLOSED | Public tagged-union decode rejects unknown/hybrid variants and out-of-range generations | Nullable catch-all lease, wrapping/resetting generation | unknown, negative, max+1, mixed variants; consumer then stale/expiry races |
| Exact close and rollover | Exact terminal obligation versus current selector/epoch and exhaustion authority | Distinct preparation requests preserve original proof and predecessor bytes | Claim lease interpreted as close or ordinary takeover used as rollover | absent closure, substituted proof, old epoch, duplicate/rival decision; J6 |
| No-exposure replacement | Selected original attempt + immutable manifest + selector + registered proof source | Consumer roundtrip binds all identities; actual model observer later proves byte count and selection | Timeout or bool `safe_to_retry`, new Turn/call slot | wrong generation, omitted proof/source, unknown version, stale response; J6 |
| Late evidence is not capture | Original attempt/manifest and independent custody/source bytes | Distinct late-evidence request cannot decode as replacement or ordinary selected capture | Mutate terminal attempt to RESPONSE_CAPTURED | binary payload, old/superseded generation, duplicate late event; J6 |

Mutation families for these boundaries: omission, addition/unknown value,
substitution/alias, duplicate/reorder, stale/race, N/N+1 and logical/physical
identity mismatch. Consumer tests establish representation only; races, proof
authenticity, atomicity and progress remain OPEN until the joint T/I histories.
No family is waived by a successful roundtrip.

The public preparation ports and their request/result unions now exist. Work
contracts include terminal companions, claim/renew/takeover, rollover and close;
model contracts distinguish proof-bound replacement from authenticated late evidence.
Their consumers retain binary source/owner bytes and reject missing/unknown fields.
An initial negative consumer showed that Literal[0] admitted boolean False;
the generation now uses a strict integer with exact numeric bounds, including the
uint64 maximum for exhaustion. Existing Run, execution Run, legacy NoExposureProof
and rollover-fence schema hashes are explicitly unchanged.

These shapes do not complete either runtime or all of Phase C. Concrete work-record
schema interpretation, producer/operation routing and the other graph gaps remain
OPEN. The work member wrapper retains complete bytes rather than authorizing an
arbitrary registered record. No owner implementation or physical publisher is added.

## Runtime surface boundary (J1/J6)

`platform.runtime_surface_contracts` separates registered requirements from
independently discovered executable paths. This is a broker/verification seam;
capabilities do not receive it as publication authority. Registry rows include
command/handler/request schema, physical record schemas, exact writer boundary
and one of the six worker-applicability variants. Ingress rows bind actual adapter,
authenticator, earliest handoff, token/custody path and retained-source versus
destructive-read loss-slot policy. All mandatory source classes remain in scope.

Guarantee preflight: **complete surface** → broker owns registration, discovery
owns observed executable paths → compare both sets in both directions at one
runtime/profile/source cut → forbidden substitute is copying registry rows into
the discovery result or equating a hash catalog with operational coverage →
fixtures omit/add/alias/reorder/duplicate rows, substitute schema/handler/writer,
change source cut or applicability, and mismatch logical/physical paths → new
public wire consumers first, reached J1/J6 mutation fixtures in Phase T/I.
The wire does not itself discover code or prove equality; those remain OPEN.

Consumer evidence: `uv run pytest -q
tests/capabilities/agent_loop/test_post_terminal_contracts.py
tests/capabilities/agent_loop/test_model_attempt_recovery_contracts.py
tests/composition/test_execution_cancellation_contracts.py` passed 48 tests.
`uv run pytest -q tests/platform/test_runtime_surface_contracts.py` passed 4 tests.
Explicit mypy on the three new source files and their three consumers passed;
the source catalog adds only those files and the imported cancellation contract.
All prior registered source bytes were compared to their existing catalog hashes
without repinning. Full regression and staged red-team are intentionally reserved
for integration/main, following the user's checkpoint policy.

## Ingress transition and execution-input boundary (J1/J2/J3)

The next broker contracts retain the complete custody/admission/drain/parser/page
snapshot and separate it from a closed command. Preparation yields exact candidate
members; selected publication, uncertain publication and semantic rejection have
different result types. A prepared cursor or acknowledgement is not permission
to advance a provider cursor, release a source or emit response bytes. That requires
the actual selected journal decision and a fresh broker-owned boundary check.

The owner-local initialization seam keeps the selected inbox's raw bytes, containing
physical record, original source authentication, normalization version/output and
exact origin. It does not import broker-private DTOs. Scheduled creation has a
distinct source carrying original configuration/decision and pre-root disposition;
it does not require the head or live lease of a Run that does not exist yet.

| Claim | Owner / data | Independent observable | Forbidden substitute | Boundary / planned consumer and runtime evidence |
|---|---|---|---|---|
| Complete custody/admission cut | Broker complete tokens, queue, bound, deficit and epoch snapshot | All fields roundtrip; runtime enumerates and rechecks exact journal/SQL cut | Caller subset or token count | omission/addition/duplicate/order, stale epoch; ingress transition consumer, then J1 |
| FIFO progress and drain closure | Broker canonical class reserves including ORDINARY, unchanged deadline lineage, complete parser/occupancy state | Explicit full snapshot and exact selected target; runtime deadline/rebase/drain observations | Numeric limit without queue/bound closure | N/N+1, blocked-prefix rebase, restart with unsettled token; J1 |
| Exact parser/page lineage | Original raw custody/authentication, selected retry/attempt and completed attempt IDs, raw page and ordered dispositions | Binary transport and distinct parser/cursor commands; runtime exact one selected result | Rewritten raw input, new parser identity hiding a retry, candidate cursor as applied cursor | alias/substitution, partial page, rival parser, lost publication reply; J1 |
| Durable handoff versus uncertainty | Broker selected authorization and issued attempt, independent transport observation | Separate prepared/selected/uncertain variants; actual source/byte observer later | Success flag or timeout interpreted as no selection | crash at selection/ack/cursor/release and exact retry; J1 |
| Original admitted input → Run | Loop consumes broker-owned selected source bytes and authenticated normalization; conversation remains its own owner | New request cannot decode as legacy create; input/body/ref/version/origin preserved | R13Workspace.ingest(prompt) as a replacement input | identical text from distinct inputs, forged origin, stale current cut; owner consumer then J1→J3 |
| Scheduler pre-root creation | Loop owns Run, scheduler owns configuration/decision/materialization and pre-root disposition | Distinct scheduled input branch retains original bytes and root identity | Existing-Run lease or legacy RunRecord at genesis | logical/physical root alias, missing decision, later exact materialization/replay; J2→J3 |

All mutation families remain applicable: omission, addition/unknown, substitution,
duplicate/reorder, stale/race, numeric/byte boundary and logical/physical mismatch.
Shape tests do not discharge runtime authenticity, FIFO progress or atomicity.

## Publication discipline

New contract files, their consumer tests, this inventory and exact source catalog
entries are one Phase-C checkpoint; all other graph gaps remain explicit above.
Budget includes the imported cancellation contract and its tests/docs/catalog.
Intermediate commits run only the new consumers, without full regression or
red-team. Final integrated source admission, legacy compatibility, source discovery
and normative evidence are checked before main; hashes alone establish none of them.
