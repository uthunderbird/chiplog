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
| J1: all ingress classes, custody/release | `_ingress_contracts` retained; `ingress_transition_contracts` preparation/publication ports now separate prepared, selected and uncertain results | Bind each source-specific authenticator/transport to registered record interpretation and earliest handoff; interface presence does not prove runtime loss-slot or release behavior |
| J1: admission/page/quarantine | `ingress_runtime_snapshot` plus closed ingress transition requests/results retain queue, drain, parser and page/cursor state | Complete consumer coverage for nonempty lineage/page/drain joins and registered durable record interpretation; FIFO, CAS, restart and handoff runtime remain T/I |
| J1→J3: selected inbox → Run | `agent_loop.execution_initialization_contracts` admitted and scheduled requests retain original source preimages and distinct creation cuts | Complete scheduled-materialization consumer and conversation-owner companion join; runtime must consume selected input rather than rebuilding prompt ingress |
| J2: scheduler decisions and execution | `agent_loop.scheduler_contracts.SchedulerPort`, DecideIntervalCommand, ResolveIntervalCommand, LeaseTransitionCommand, PhysicalRootRolloverCommand → SchedulerResult | Public automatic service-authority contract beyond one-delivery TickPolicy; executable Run materialization and lease joins must not fall back to legacy RunRecord |
| J3: seal/accept/cancel | ExecutionCapturedFanOutRequest/Result; composition CallAcceptancePort; ExecutionCallCancellationPort → ExecutionCancelledCallReceipt | Import cancellation boundary; retain two-record execution cancellation versus three-record legacy cancellation. Runtime authentication and both CAS orders remain Phase T/I work |
| J4: effect dispatch/retry/delivery/compensation | effects DispatchMandateV2, ExternalActionIntentV2, AuthorizeDispatchV2, CommitFirstSendV2; DispatchOutcomeCommandV2/RecordV2; legacy SafeRetransmission and purpose unions | A versioned full lifecycle contract must join safe retransmission/all-child proofs, separately scoped compensation and delivery to immutable v2 mandates without reinterpreting legacy intents |
| J4→J5: original-stream reduction | `agent_loop.original_recovery_contracts` original resolver and loop-owned semantic reduction preparation port; legacy EvidenceReduction/OriginalObligationBinding retained | Effects-owned semantic reduction producer and registered source decoding/operation routes remain OPEN; loop consumes selected foreign records and cannot write their streams; resolver/continuation runtime joins remain T/I |
| J5: frontier/suspend/resume/successor | `execution_recovery_observations` complete cut, weak/strong joins and noncircular suspension v2; `execution_recovery_contracts` executable preparation port includes suspend/resume/successor/next-Turn/abort/cancel | Consumer coverage now covers current sources, mandatory suspension pair and distinct commands; complete successor participant interpretation and terminal/work record routing remain C work; runtime classification/CAS remain T/I |
| J5: read-only branch | `readonly_execution_contracts` preparation port, distinct unfinished attempt/outcome/pending/reduction records and registered proof preimages; existing frontier types retained | Versioned executable response/artifact registration of the new history-tool wire and end-to-end consumer; positive no-mutation/runtime proofs and full counter/pending CAS remain T/I |
| J6: writer applicability/discovery | `platform.runtime_surface_contracts` registry and independent discovery DTOs; six worker fence variants retained | Concrete registered operation bindings and record interpretation; independent discovery and both-direction equality remain T/I, not established by source hashes |
| J6: post-terminal work | WorkSubjectBinding, WorkEpochBinding, PostTerminalWorkFence, WorkEpochRolloverFence | Materialize the closed work-lease state, owner preparation requests/results, terminal-manifest work creation, exact close and monotone rollover boundary; consumers preserve original obligation/evidence references |
| J6: model-attempt recovery | `model_attempt_recovery_contracts` executable replacement and separate late-evidence request/result/port | Registered source/record interpretation and operation bindings; authenticity, race handling and immutable late evidence remain T/I |
| J7: completion/delivery/channel driver | `execution_completion_contracts` direct executable completion request/result retains captured response, complete earlier joins and delivery observation; existing delivery owner port/broker envelope retained | Complete conversation/effects/work participant assembly contract and consumers, plus public common CLI/Telegram driver receipts preserving distinct authentication and committed projection; owner proposal alone is not CompleteAcceptance publication |

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

## Read-only execution boundary (J3/J5)

`agent_loop.readonly_execution_contracts` adds separate nonterminal attempt
acceptance, observed per-attempt outcomes, a complete lineage snapshot, and
preparation ports for attempts, outcome ingestion, pending and reduction. It does
not reuse the already-terminal `ReadOnlyAcceptedCall` as an unfinished attempt.
Original lineage and counter identity survive the distinct successor-pending route.

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture / planned evidence |
|---|---|---|---|---|
| Positive read-only classification | Registered tool implementation/proof and exact query/snapshot preimages | Consumer preserves binary source; runtime checks registered implementation and denies writes | `readonly=True`, model tool name or caller's digest | unknown tool/proof/version, hidden write/effect, substituted snapshot; new consumer then J3/J5 |
| One bounded lineage | Original initialization, frozen reducer/budget, complete ordered attempts and shared counter | Counter and original call unchanged across current Run changes; ordinal advances only with predecessor outcome | New lineage/counter on resume, restart or successor | missing/extra/reordered attempts, stale counter, max/max+1, unknown outcome; J5 |
| Separate immutable attempt outcome | Typed success, retryable/final definite failure, unknown or exact obligation | Owner output binds original accepted attempt and independent evidence bytes | Caller supplies retry permission or attempt result substitutes for call terminal | wrong source/attempt, hybrid outcome, open obligation retry; consumer then J5 |
| Pending keeps an owner | Exact old pending head and complete replacement branch selected together | Successor route retains predecessor, initialization and inherited reference; one closure and replacement | same-Run pending consumption, branchless interval or copied pending lineage | stale/rival pending, alias, failed validation, successor/current fence mismatch; J5 |
| Complete reduction closes once | Complete ordered outcome heads, selected result, call outcome, terminal frontier and closed counter in one proposal | Both call-level members and original lineage survive wire; writer compares full cut | Last attempt alone, rewriting prior attempt, success flag | incomplete lineage, duplicate terminal, budget-exhausted provenance, changed reducer; J5 |

All mutation families remain required: omission, addition/unknown, substitution,
duplicate/reorder, stale/race, numeric/byte boundary and logical/physical identity.
The history-tool model wire is declared separately. Registration in an explicitly
versioned executable response/artifact and its end-to-end model/driver consumer
remains OPEN; adding its name to the old v2 union would change a frozen schema.
These consumers do not prove positive no-mutation, retry eligibility or atomicity.

Consumer command: `uv run pytest -q
tests/capabilities/agent_loop/test_readonly_execution_contracts.py` — 19 passed.
Explicit mypy on the new source and consumer passed. The legacy executable Run
schema fingerprint is unchanged. This checkpoint does not complete Phase C.

## Executable recovery and continuation boundary (J4/J5/J7)

Guarantee map for `execution_recovery_observations`, `execution_recovery_contracts`
and `execution_completion_contracts`:

| Claim | Owner / exact data | Independent observable | Forbidden substitute | Boundary fixture / evidence |
|---|---|---|---|---|
| Complete current cut | Broker-enumerated original Run lineage, sealed manifests, frontier, exact source preimages and causal changes | All required families cross the consumer seam; writer independently enumerates the same transaction | Caller ancestry, digest alone, last response only | omissions/additions/reorder/duplicate and stale cut; consumer then J5/J7 |
| Weak versus strong join | Loop accounting includes terminal open obligations; continuation additionally binds original closure/outcome/witness and current semantic reduction | Separate closed records and both continuation consumers | Raw evidence, attempt outcome or previous Turn proof as readiness | evidence/open, stale reduction, pending, rival closure; J4→J5 |
| Suspension without circular hashes | Baseline v2 binds prior Run and source cut; suspended Run binds baseline; mandatory pair binds both final heads | DAG baseline → Run → pair, selected pair required by resume/successor | Baseline/Run mutual whole-record hashes or already-selected proposed output | omitted/substituted pair, wrong prior/current head, mixed v1/v2; consumer then J5 |
| Resume/successor exact CAS | Original selected pair, complete current frontier/causal proof and frozen bindings | Owner produces exact Run transitions and complete successor companions | Caller-selected disposition, copied obligation, reset pending/counter or mutable baseline | stale/fault/hold, changed binding, both resume/successor orders, physical rollover; J5 |
| Terminal with open recovery | Loop complete terminal accounting manifest names original obligations; work owner prepares its full genesis set | Abort/cancel uses weak accounting and same-batch work companions | Strong-ready requirement that strands open recovery; terminal without work | open obligation, empty/missing/extra work, crash boundaries; J5/J6 |
| Direct CompleteAcceptance | Captured response/attempt/visibility plus every earlier response's current strong join, delivery observation and owner companions | Both next-Turn and Complete request complete current joins | Transitive TurnStarted proof, model assertions or caller-built effects/history | ancestry substitution, stale reduction/fence, omitted participant; J7 |

All standard mutation families apply. New contracts do not discharge runtime
authenticity, current-cut equality, progress or atomicity. Legacy baseline and Run
schemas remain unchanged. Terminal manifest hashes precede work companions; the
combined batch checks completeness without a reverse manifest→work hash edge.

Consumer command: `uv run pytest -q
tests/capabilities/agent_loop/test_execution_recovery_contracts.py` — 12 passed.
The consumer constructs baseline → Run → pair using actual canonical hashes and
requires the selected pair for resume/successor. It retains accepted evidence and
a newer current reduction separately, verifies distinct weak/strong wire types,
and exercises suspension, resume, successor, next-Turn, abort/cancel and direct
completion requests. Empty and deliberately mismatched fixture cuts establish
representation only; no runtime classification or completeness claim follows.
Explicit mypy on the three new source modules and consumer passed.

The completion result is explicitly loop-owned only. Complete conversation,
effects and work assembly remains OPEN in the graph, as does original-stream
resolver/reduction preparation. Those gaps cannot be deferred beyond Phase C.

## Original loop-stream recovery (J4→J5)

The `original_recovery_contracts` producer boundary is explicitly loop-owned. Foreign effects evidence and
reductions are independently selected inputs, never loop-writable streams. The
existing `EvidenceReduction` remains a continuation observation with resolved
anchors; a separate producer record also represents the pre-closure state.

| Claim | Owner / exact data | Independent observable | Forbidden substitute | Boundary fixture / evidence |
|---|---|---|---|---|
| Two-commit original resolver | Loop owns original call obligation; evidence owner already selected immutable source bytes | Resolver request retains original stream, physical record and selection; result contains no evidence append | Cross-owner atomic closure, supplied success or successor-owned copy | wrong owner/original identity, evidence present but obligation open, stale CAS; J4→J5 |
| Closure and outcome selected together | Primitive resolver basis → original closure → recovered outcome → resolver batch | Complete proposal retains both records and their original identities without reverse hash edges | Outcome alone, new obligation or reciprocal whole-record hashes | missing/rival member, changed witness/resolver, replay conflict; consumer then J5 |
| Current semantic reduction | Registered loop-owned stream, fixed reducer, full ordered evidence preimages and prior head | New producer handles unresolved/resolved anchors and typed non-consumable states | Writing effects stream, newest timestamp, old consumable projection | append/refinement/rival/reorder/duplicate, unknown class/version and stale head; J5 |
| Independent recovery lifetime | Independent registered authority or exact leased work applicability | Request has no mandatory live original Run | Terminal/superseded Run suppresses original recovery | late evidence, terminal original Run, wrong work subject/epoch; J5/J6 |

All mutation families apply. Owner identity, complete evidence enumeration, source
authentication and same-writer CAS are runtime obligations. Effects-owned semantic
reduction producer contracts remain OPEN; the loop port cannot substitute for them.

Consumer command: `uv run pytest -q
tests/capabilities/agent_loop/test_original_recovery_contracts.py` — 23 passed.
The consumer hashes basis → closure → outcome → batch, rejects missing resolver
members and foreign stream ownership, retains binary selected source preimages,
and covers unresolved anchors plus every typed reduction-hold reason. Explicit
mypy on the new source and consumer passed. No runtime closure or Phase-C
completion is claimed.

## Publication discipline

New contract files, their consumer tests, this inventory and exact source catalog
entries are one Phase-C checkpoint; all other graph gaps remain explicit above.
Budget includes the imported cancellation contract and its tests/docs/catalog.
Intermediate commits run only the new consumers, without full regression or
red-team. Final integrated source admission, legacy compatibility, source discovery
and normative evidence are checked before main; hashes alone establish none of them.
