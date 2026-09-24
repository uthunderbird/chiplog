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
| J1: admission/page/quarantine | `ingress_runtime_snapshot`, closed transition requests/results and `ingress_record_contracts` retain queue, drain, parser and page/cursor state; nonempty lineage/page/drain consumers exist | Central registration and source authentication remain C/T/I joins; FIFO, CAS, restart and handoff runtime remain T/I |
| J1→J3: selected inbox → Run | `agent_loop.execution_initialization_contracts` admitted and scheduled requests retain original source preimages and distinct creation cuts | Complete scheduled-materialization consumer and conversation-owner companion join; runtime must consume selected input rather than rebuilding prompt ingress |
| J2: scheduler decisions and execution | `agent_loop.scheduler_contracts.SchedulerPort`, DecideIntervalCommand, ResolveIntervalCommand, LeaseTransitionCommand, PhysicalRootRolloverCommand → SchedulerResult | Public automatic service-authority contract beyond one-delivery TickPolicy; executable Run materialization and lease joins must not fall back to legacy RunRecord |
| J3: seal/accept/cancel | ExecutionCapturedFanOutRequest/Result; composition CallAcceptancePort; ExecutionCallCancellationPort → ExecutionCancelledCallReceipt | Import cancellation boundary; retain two-record execution cancellation versus three-record legacy cancellation. Runtime authentication and both CAS orders remain Phase T/I work |
| J4: effect dispatch/retry/delivery/compensation | `effects.scoped_intent_contracts` v3 origin/mandate/acquisition; `scoped_dispatch_contracts` authorize/pre-send/first-SEND/all-child evidence/reconcile; `lifecycle_transition_contracts` shared v2/v3 retry/reduction | Registered source/record interpretation, complete publication and adapter operation routing remain C work; last-boundary proof and dispatch runtime remain T/I |
| J4→J5: original-stream reduction | `agent_loop.original_recovery_contracts` loop-owned resolver/reduction plus `effects.lifecycle_transition_contracts` effects-owned reduction producer | Registered source decoding, selected foreign reduction interpretation and operation routes remain OPEN; each owner writes only its own streams; resolver/continuation runtime joins remain T/I |
| J5: frontier/suspend/resume/successor | `execution_recovery_observations` complete cut, weak/strong joins and noncircular suspension v2; `execution_recovery_contracts` executable preparation port includes suspend/resume/successor/next-Turn/abort/cancel | Consumer coverage now covers current sources, mandatory suspension pair and distinct commands; complete successor participant interpretation and terminal/work record routing remain C work; runtime classification/CAS remain T/I |
| J5: read-only branch | `readonly_execution_contracts` preparation port plus `execution_history_contracts` v3 artifact/response/Run containment, transition and captured-fanout boundaries | Join OPEN recovery/readonly/completion contracts to v3 and register parser/renderer/physical interpretation; positive no-mutation/runtime proofs and full counter/pending CAS remain T/I |
| J6: writer applicability/discovery | `platform.runtime_surface_contracts` registry and independent discovery DTOs; six worker fence variants retained | Concrete registered operation bindings and record interpretation; independent discovery and both-direction equality remain T/I, not established by source hashes |
| J6: post-terminal work | Closed lease state and preparation port in `post_terminal_contracts`; six concrete durable record decoders and companion-graph consumer in `post_terminal_record_contracts` | Register durable interpretation and operation/version routes; join the v3 Run family; runtime must prove exact close, monotone rollover and atomic terminal companions |
| J6: model-attempt recovery | `model_attempt_recovery_contracts` executable replacement and separate late-evidence request/result/port | Registered source/record interpretation and operation bindings; authenticity, race handling and immutable late evidence remain T/I |
| J7: completion/delivery/channel driver | `execution_completion_contracts` direct executable completion request/result retains captured response, complete earlier joins and delivery observation; existing delivery owner port/broker envelope retained | Complete conversation/effects/work participant assembly contract and consumers, plus public common CLI/Telegram driver receipts preserving distinct authentication and committed projection; owner proposal alone is not CompleteAcceptance publication |

The recovery classification `TERMINAL_RECOVERY_FAULT` is observation-only in the
current contracts. Normative recovery requires a durable typed terminal fault;
its owner preparation and physical publication recipe remain a separate explicit
`terminal_fault_publication` C_OPEN obligation. A zero-record classification or
ordinary rejection does not satisfy that required branch.

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
reduction uses its separate lifecycle producer below; the loop port cannot substitute for it.

Consumer command: `uv run pytest -q
tests/capabilities/agent_loop/test_original_recovery_contracts.py` — 23 passed.
The consumer hashes basis → closure → outcome → batch, rejects missing resolver
members and foreign stream ownership, retains binary selected source preimages,
and covers unresolved anchors plus every typed reduction-hold reason. Explicit
mypy on the new source and consumer passed. No runtime closure or Phase-C
completion is claimed.

## Effects all-child lifecycle boundary (J4)

| Claim | Owner / exact data | Independent observable | Forbidden substitute | Boundary fixture / evidence |
|---|---|---|---|---|
| Safe retry retains original intent | Effects immutable v2 intent/authorization, exact parent revision, full ordered children and new ordinal | Consumer retains original bytes and all children; result pairs new child with parent successor | New intent/key/payload/recipient or refreshed mandate | missing/extra/reordered/duplicate child, altered exact effect, stale revision; J4 |
| Coverage includes next send | Registered provider-idempotency or every-prior-child permanent-incapability proof, new transmission identity, exact effect/key/recipient and coverage interval | Separate closed proof variants with retained selected preimages; fresh authority/clock evaluated independently | Absence-at-read, timeout, bool safe, old authorization alone | uncovered child/new send, expiry/unverifiable clock, substituted provider fence; J4 |
| Ambiguity is not reset | Parent successor remains SEND_COMMITTED, SENT or OUTCOME_UNKNOWN and retains all prior children | One prepared parent/child result; final writer selects both | Retry from PARTIAL/terminal, state reset or child-only append | both race orders, ordinal replay/conflict and crash boundaries; J4 |
| Effects owns its reduction | Complete evidence/child cut, immutable original closure when present and current reduction predecessor | Effects-only producer port, typed HOLD/consumable result, no SEND output | Loop writer for effects, latest timestamp, old consumable head | mixed/rival/incomplete children, changed reducer, old witness versus refinement; J4→J5 |

All standard mutation families apply; runtime proof issuance, source completeness,
atomicity and exact intent equality remain T/I. Compensation, delivery and
AUTHORIZE_DUPLICATE_RISK use the fresh v3 origin/authority contracts below; their
SEND/lifecycle joins remain OPEN. None is encoded as safe retransmission or
silently added to legacy v2 origin.

Consumer command: `uv run pytest -q
tests/capabilities/effects/test_lifecycle_transition_contracts.py` — 21 passed.
Explicit mypy on the new producer boundary and consumer passed. The result uses
the acyclic SEND decision → child → parent → batch graph: the child's send_commit
references the primitive decision, and that decision contains no output child or
parent fingerprint. The writer must select all members together. Consumers retain
original intent bytes and verify closed proof/state variants; they do not prove
coverage validity, actual child completeness or current authority.

## Fresh scoped intents (J4/J7)

| Claim | Owner / exact data | Independent observable | Forbidden substitute | Boundary fixture / evidence |
|---|---|---|---|---|
| Full fresh authorization | Effects v3 mandate retains all v2 authority/scope/horizon fields and adds closed purpose-specific origin | Full mandate/origin bytes are adopted or independently derived under registered current authority | Reuse original uncertain intent or omit affected-party/dependency/disclosure bindings | missing field, wrong recipient/payload/scope/expiry, original id reuse; J4 |
| Compensation is separate | New mandate and exact original ambiguity/semantics/consequence references; human adoption or registered current/bounded compensation authority | New intent has no original-mutation output | Compensation marked as original success or forced per-action adoption despite valid bounded mandate | wrong original, stale authority, missing required adoption, changed consequence; J4 |
| Duplicate risk is explicit | Human adoption of exact displayed unresolved attempts, duplicate effects, parties/resources, commitments and safer alternatives | Origin→acquisition matrix permits only explicit human adoption for duplicate risk | Retry proof, bounded compensation authority or delivery authority | omitted risk component, altered display/mandate, wrong acquisition; J4 |
| Delivery preparation is acyclic | Loop-owned primitive completion command/observation/delivery proposal without effects outputs; current communication/disclosure authority | Basis → v3 mandate/acquisition → effects intent → final combined batch | Combined CompleteAcceptance bytes containing this intent, model text as authority | wrong owner/basis/cut, payload/recipient mismatch, cross-origin acquisition; J7 |

These are proposal contracts. The registered owner must enforce the closed
origin/acquisition matrix and authenticate every source at publication and SEND.
No origin name or DTO construction grants authority. All mutation families apply;
existing v2 wire interpretation remains unchanged.

`scoped_intent_contracts` now declares the full v3 mandate and origin/acquisition
matrix, precursor and fresh-intent publication ports. Human adoption covers the
whole mandate; compensation may also use registered current/bounded authority;
duplicate risk admits only explicit human adoption; delivery uses the loop-only
preparation basis and preexisting communication/disclosure authority. Acquisitions
occur after mandate construction, avoiding an adoption-in-origin hash cycle.

Consumer command: `uv run pytest -q
tests/capabilities/effects/test_scoped_intent_contracts.py` — 11 passed; explicit
mypy on the source and consumer passed. The delivery consumer uses the actual
loop `DeliveryAcceptanceProposal`, not combined completion output. These are wire
tests, not proof of authenticated adoption or valid current scope. V3 SEND/lifecycle
joins are now represented by the scoped-dispatch boundary below; its operation
routing and participant assembly still require Phase-C closure.

## Scoped dispatch and all-child outcome joins (J4)

| Claim | Owner / exact data | Independent observable | Forbidden substitute | Boundary fixture / evidence |
|---|---|---|---|---|
| Exact v2/v3 interpretation | Effects original intent and explicitly separate authorization record versions | Union retains original canonical bytes; old schema decoders remain unchanged | Decode new intent/auth as old v2 or reconstruct mandate | unknown/hybrid/version substitution; new consumer then J4 |
| First SEND has no prior child | Pre-send cut permits absent authorization and has no child/obligation requirement | Authorize/pre-send/first-send requests use distinct cut; first result derives primitive → child → parent | Require an existing child/obligation before first send or hash authorization into itself | genesis, missing/rival authorization, ordinal false/nonzero; J4 |
| Fresh scope remains equal | Each authorization/SEND/retry carries current origin source preimages, exact immutable mandate and fresh cut | Owner compares actual ambiguity/endpoint/disclosure heads at the writer boundary | Replace old origin with newly observed values | stale original reference, disclosure/endpoint change, changed authority/fence; J4 |
| Full outcome lifecycle | Evidence/reconcile consumes all children and exact original obligation plus selected authenticated input | Parent, obligation and semantic reduction are prepared together without SEND output | One-child resolver or live original Run requirement for late evidence | incomplete/rival/late evidence, post-terminal hold, both resolver race orders; J4→J5 |

All standard mutation families apply. The contracts express the full observed
sets and candidate outputs; runtime still proves completeness, current equality,
registered transitions and atomic selection. Existing v2 histories are preserved.

`scoped_dispatch_contracts` now expresses authorization, pre-send disposition,
first SEND and all-child evidence/reconciliation. Pre-send inputs require no child
or obligation; post-send cuts explicitly permit obligation absence, and the first
obligation revision has an absent predecessor. Authorization is a primitive record
without its own output hash; the owner returns its parent revision as well.
The shared cut retains tagged v2/v3 intent bytes and separate authorization types.
Fresh origin source preimages are mandatory fields at authorization/SEND/retry;
the owner must compare them without substituting them into the immutable origin.

Consumer command: `uv run pytest -q
tests/capabilities/effects/test_scoped_dispatch_contracts.py
tests/capabilities/effects/test_lifecycle_transition_contracts.py` — 32 passed.
These two files are new Phase-C consumers relative to main. Explicit mypy passed
on the three affected source modules and two consumers. Full regression/red-team
remain reserved for integration. This checkpoint establishes representation only.

## Executable history-tool containment

`execution_history_contracts` adds an explicitly versioned v3 response/artifact,
visibility manifest, attempt, Turn and Run; `execution_history_transition_contracts`
and `execution_history_fan_out_contracts` carry that Run through creation/capture
and initialized-call preparation. Existing v2 DTOs, parsers and source bytes are
unchanged. History calls retain the registered READ_ONLY classification and retry
policy in inert consumers; DTO construction does not establish their authenticity.

The new artifact binds the exact v3 tools, generator and response schema. Negative
consumers first roundtrip a valid JSON payload through the public decoder, then
mutate that payload through the same path, avoiding unrelated strict tuple/list
failures. The new call-slot field rejects boolean false as an integer-zero alias.
Mixed response order, captured history bytes and Complete-pending representation
roundtrip; the legacy parser still rejects the new tool.

Evidence: `uv run pytest -q
tests/capabilities/agent_loop/test_execution_history_contracts.py` passed 4 tests.
Targeted Ruff and mypy passed for the three source files and the consumer. Root
inspected the definitions and consumers and reran the new tests. No full regression
or checkpoint red-team was run.

This is not global Phase-C readiness. Shared OPEN feature contracts now use
discriminated v2/v3 Run/create joins, with history-tool leaves extracted to avoid
an import cycle. Concrete parser/renderer behavior and physical publication/readback are
still absent for v3. Phase C must register their interfaces before T/I supplies
the behavior; query execution, no-mutation proof, counters, CAS and replay remain
joint behavioral work. Scheduler and completion must use the same joined version
family rather than remaining separate v2-only islands.

`execution_run_versions` is the common representation boundary. Initialization,
readonly preparation, recovery cuts and transitions, completion, model-attempt
recovery and terminal-work requests carry the tagged Run family. These are OPEN
feature-only outer contracts; the historical concrete v2 Run and create DTOs are
not rewritten. `readonly_history_tool_contracts` holds the unchanged tool leaves,
reexported from their original module to preserve existing imports.

Evidence: `uv run pytest -q
tests/capabilities/agent_loop/test_execution_run_versions.py` passed 31 tests.
The consumers roundtrip nonempty v3 payloads through public preparation/result
unions and retain v2 canonical bytes; schema introspection additionally checks
all embedded version discriminators. These tests establish representation,
not valid recovery state transitions, proof authenticity or terminal publication.
Scheduler and conversation consumers are reviewed separately before their own
checkpoint; central operation registration and all joint runtime histories remain
OPEN.

## Post-terminal durable record interpretation

`post_terminal_record_contracts` supplies closed SUBJECT/EPOCH/SELECTOR/LEASE/
ROLLOVER/EDGE schemas and the public `WORK_RECORD_CONTRACTS` table. Its decoder
retains supplied canonical bytes and checks the envelope's identity, schema and
fingerprint. The request/result consumer checks original obligation/evidence
references, proposed companion order, predecessors and the resulting work view.
Same-batch references are proposed graph links, not claims of prior selection.

Nonempty genesis, rollover and close fixtures now use the public consumer, with
missing, duplicate, reordered, substituted-obligation and stale-predecessor inputs.
Root review corrected omitted request identity and output-view joins before adding
this module to the source catalog. Historical post-terminal DTOs remain unchanged.

Evidence: `uv run pytest -q
tests/capabilities/agent_loop/test_post_terminal_record_contracts.py
tests/capabilities/agent_loop/test_post_terminal_contracts.py` passed 26 tests;
root reran this focused set. Targeted formatting, Ruff and mypy passed. This
establishes record representation and local graph consistency, not authenticated
selection, clocks, live lease CAS, original obligation closure or atomic work
creation. Concrete central routing and joint runtime histories remain OPEN.

## Concrete ingress records and retained sources

`platform.ingress_record_contracts` defines a closed table of physical record
schemas and decodes supplied member bytes with exact kind, schema, identity,
canonicalization and fingerprint checks. Distinct bound/rebase and handoff/cursor
authorization records retain their own schemas. Consumers build nonempty page,
parser/quarantine, queue/rebase and drain examples rather than only empty wrappers.

`platform.ingress_source_contracts` fixes source class/reader/schema combinations
and retains the existing hermetic CLI decoder. The latter now checks the original
metadata's tenant/database against the expected binding; a receipt slot is not
equated with a deployed source identity. Handoff observation records distinguish
local completion/failure/uncertainty, authenticated provider receipt with retained
raw custody, and reconciliation release. Unsupported command/result combinations
are rejected before publication; decoding does not authenticate the provider.

Evidence: `uv run pytest -q tests/platform/test_ingress_record_contracts.py
tests/platform/test_ingress_lineage_contracts.py
tests/platform/test_ingress_handoff_contracts.py
tests/platform/test_ingress_transition_contracts.py` passed 56 tests. Targeted
Ruff and mypy on the two new source modules and three new consumers passed.
Root inspected the source and reran the focused tests. Fixture-level graph joins
are representation evidence; owner runtime enumeration, proof verification,
journal selection, restart behavior and transport handoff remain unimplemented
by these additions. Central operation/source mounting remains OPEN.

## Conversation preparation and common driver wire

`capabilities.projections.conversation_preparation_contracts` separates admitted
input, accepted completion and semantic rejection. Source cuts retain preexisting
selected bytes; proposed terminal Run/manifest records are distinct inputs.
Both v2 and v3 Runs decode under explicit schemas. Accepted entries are versioned
conversation records; rejection returns an explicit no-change commitment and zero
new assistant entries. Full consumers use ACTIVE captured sources and distinct
SUCCEEDED/ABORTED proposed results, including a v3 history-tool Turn followed by
a real captured completion.

`composition.common_execution_driver_contracts` retains original ingress identity
and exact source bytes for CLI peer, retained CLI, Telegram push and poll. Results
distinguish selected, pending, uncertain and rejected outcomes. Terminal selected
receipts distinguish accepted completion, semantic rejection, ordinary abort and
cancellation; phase, Run state and required selected heads must agree. A semantic
rejection retains the unchanged projection (possibly absent) and its no-change
commitment, not a fabricated newly selected conversation record.

Evidence: `uv run pytest -q
tests/composition/test_common_execution_driver_contracts.py
tests/capabilities/projections/test_conversation_preparation_contracts.py` passed
47 tests after root review corrections. These are unmounted contract consumers.
Actual source authentication, Telegram admitted lookup, complete owner exchange
assembly, public driver execution and selected projection readback remain OPEN.
The driver receipt supplies no acknowledgement, cursor or SEND permission.

The broker now has separate `CompleteDeliveryBatchV2` and
`RejectedCompletionBatchV1` envelopes, fixed owner slots and exact source-command
order for replay identity. These operations cannot enter `SingleOwnerBatch`.
The historical completion envelope remains separate. Focused broker envelope
tests plus the two consumers above passed 55 tests with `uv run pytest -q
tests/platform/test_completion_batch_v2_contracts.py
tests/platform/test_owner_publication_contracts.py
tests/composition/test_common_execution_driver_contracts.py
tests/capabilities/projections/test_conversation_preparation_contracts.py`;
targeted mypy passed on those consumers and their four source modules.

This checkpoint does not admit completion assembly as complete: fixed loop and
effects owner-record conversion, full positive owner-exchange fixtures, exact
ordered batch-byte comparison and result/request fingerprint joins remain OPEN.
The independently useful driver/conversation/broker wire can be reviewed without
treating the unfinished assembly validator as evidence of atomic publication.

## Recovery physical records and proof sources

`agent_loop.recovery_record_contracts` closes sixteen physical record rows.
Rows with an explicit unique record ID retain it; newly registered rows without
one use their schema and canonical content hash. Physical IDs, logical subjects
and journal decision heads remain distinct. Fixed decoders preserve supplied
bytes and reject unknown owner/kind/schema, noncanonical JSON, wrong IDs and
fingerprints. Companion consumers cover suspension, original resolution,
accounting/continuation and readonly reduction. Readonly acceptance and terminal
reduction use separate open and closed counter revisions; terminal accounting
compares the full ordered sequence rather than checking membership.

`agent_loop.recovery_source_contracts` defines two separate broker-source wires,
`readonly-history-proof-v1` and `model-pre-emission-cas-v1`. Their envelope cannot
be used as a writable owner record. Decoders require external tenant/database
expectations (plus readonly principal/contour), exact original query/attempt
references and proof-body equality. A selected snapshot/CAS decision is a separate
reference from the new source proof. These checks prove representation only;
immutable-query execution, permanent emission fencing and issuer authentication
remain joint T/I obligations.

The new chain fixture constructs every registered row and calls the pure joins
with canonical records. Successor initialization and scheduler companions have
their own pending consumer review; these rows do not establish successor runtime
or the complete central operation registry. Completion record conversion remains
separate work even where it reuses the terminal-manifest decoder.

Evidence: `uv run pytest -q
tests/capabilities/agent_loop/test_recovery_record_contracts.py
tests/capabilities/agent_loop/test_recovery_record_chains.py
tests/capabilities/agent_loop/test_recovery_source_contracts.py` passed 33 tests.
Root inspected the source and new chains and reran this set. Ruff and mypy passed
on both source modules, the three consumers and their shared fixture. No full
regression or checkpoint red-team was run.

## Scheduler execution preparation wires

`agent_loop.scheduler_execution_contracts` separates ordinary 0/1/N seed
preparation from streamed overflow holds, Run preparation from finalization,
and selected records from proposed outputs. The new mandate schema is v2;
its immutable expiry uses Unix nanoseconds and an exclusive deadline. Ordinary
interval accounting carries (1,N,0); overflow has charged and explicit zero-debit
safety branches. Each branch has a fixed dependency edge set.

The concrete WHOLE body binds the ordered non-envelope descriptors and bytes,
seed cardinality, materializations and outcome. Finalization also checks the
ordinary resulting boundary and overflow primitive reference against that body.
Negative consumers rebuild valid envelope bytes and hashes before changing a
join, so rejection exercises the relationship rather than a stale hash.

Evidence: `uv run pytest -q
tests/capabilities/agent_loop/test_scheduler_execution_contracts.py
tests/capabilities/agent_loop/test_scheduler_execution_batch_contracts.py` passed
28 tests. Ruff and mypy passed on the source, both tests and both support files.
These tests establish wire and local consistency checks; the prepared results
are fixtures, not a running scheduler or a complete physical publication recipe.
Public cycle issuance, seed production, mandate lifecycle, service acquisition,
operator resolution, full owner-record decoding and atomic runtime publication
remain OPEN. Phase C is not frozen by this checkpoint.

## Scheduler mandate lifecycle wires

`agent_loop.scheduler_mandate_lifecycle_contracts` declares issuance, revocation
and supersession preparation ports. Issuance requires separate mandate, budget
and revocation absences and a zero-counter budget genesis. Revocation retains
the exact selected mandate and budget. Supersession closes the old mandate and
uses a distinct new ID and genesis; its permitted scope changes are limited to
ID, issuance generation and successor bound. Selected/proposed bytes and hashes
are checked locally, including the mandate IDs in the consumption basis and
terminal revocation. The pure expiry predicate rejects equality at the deadline.

Evidence: `uv run pytest -q
tests/capabilities/agent_loop/test_scheduler_mandate_lifecycle_contracts.py`
passed 14 tests. JSON negative cases first validate an unchanged baseline through
the same decoder, then rebuild dependent bytes/hashes where needed to reach the
intended join. Ruff and mypy passed on the module, consumer and support fixture.
Administrative source issuance/interpretation, request/result publication joins,
registered operation mounting, current authority, CAS and durable lifecycle
execution remain OPEN; these wire consumers do not prove those mechanisms.

## Operation registry declaration grammar

`platform.operation_registry_contracts` declares versioned operation keys, wire
and record references, explicit C_OPEN/UNMOUNTED inventory entries, preparation
and selected-decoding ports. Descriptor lookup and mount status are separate.
Prepared structural expansions bind ordered command roles/ordinals to an equal
sequence of owner-result fingerprint slots and declared record schemas.

Record patterns support singleton, optional and repeated roles plus interleaved
repeated groups. A work group keeps SUBJECT(i), EPOCH(i), SELECTOR(i), LEASE(i)
together; flattening each record family into its own repeated block is rejected.
The same group descriptor accepts zero, one and multiple groups. Expansion
provenance and the required count from actual obligations remain owner/broker
verification duties, not authority supplied by the structural expansion itself.

Evidence: `uv run pytest -q tests/platform/test_operation_registry_contracts.py`
passed 13 tests; source and consumer pass Ruff and mypy. These consumers use
synthetic descriptor symbols and establish grammar/structural consistency only.
They are not registrations of the named production operations. Concrete family
envelopes, operation/result matrices, record decoding/applicability declarations,
adapters, mount discovery and current writer validation remain OPEN.

## Completion physical assembly checkpoint

`agent_loop.completion_owner_record_contracts` binds captured completion to the
exact ACTIVE native v2/v3 Run, selected attempt, Turn and raw response. Accepted
completion additionally decodes and binds the payload identities; semantic
rejection preserves malformed response bytes. Physical delivery identity remains
distinct from its semantic proposal digest. Rejection retains the original trace
reference without fabricating a new trace record.

`rejected_completion_terminalization_contracts` and
`completion_terminal_work_sources` retain exact original exchanges and join native
terminal Runs, manifest command/cut and ordered obligations. The effects codec
retains typed source preimages, including inventories and authority-specific
sources, in its commitment. `composition.completion_publication_contracts`
checks the entire ordered command and physical-record assembly against these
owner exchanges, including conversation, each delivery intent and terminal work.

Consumer cases cover accepted/rejected v2/v3, zero/one/two work obligations,
native identity and raw-response substitutions, malformed rejection payload,
retained-source replacement, delivery-intent aliasing and dropped/reordered work.
The checkpoint selector list names the six completion/intent test modules. Ruff
and mypy cover the five new source files, six consumers and two support files.
This is pure preparation/representation evidence. Actual owner preparation,
registered publication, source authentication, writer CAS and replay remain T/I;
the overall contract graph remains OPEN.

## Native source and diagnostic authority checkpoint

`execution_run_record_contracts` decodes physical v2/v3 Run members in all seven
native states, checks canonical bytes, full hash and native head, and joins an
owner result to the retained member. It does not validate state transitions.
`recovery_fault_rule_contracts` checks exact selected registry bytes and decision
references, classifier/version and precise rule head/code/relation membership;
it does not execute the rule predicate or authenticate its selection.

`scheduler_seed_producer_contracts` declares the fresh automatic seed exchange
and a closed eleven-row source decoder. Its source projection binds native
schedule, missed-policy and bound identities to mandate scope, clock, budget and
pre-root cut. Missed-policy uses its own native policy subject. Selected replay
is outside fresh preparation. Ordinary zero/one/many cuts and full/streamed
overflow primitive helpers have consumers; the prepared-result roundtrip covers
the ordinary branch. Source authentication, actual seed preparation and replay
remain required integration work.

Broker diagnostic source references remain outside the physical owner domain.
The new diagnostic authentication branch is structurally restricted to the fixed
terminal-fault command and one loop-owned fault record. It binds the invocation
subject and declared final-request fingerprints. These guards do not verify the
request byte preimage, retained issuance, source authenticity or current writer
cut. The diagnostic broker and fault preparation contracts remain OPEN; no fault
operation is registered or mounted by this checkpoint.

Evidence: the four selected new test modules for execution Run records, fault
rules, scheduler seed and diagnostic authentication pass 71 tests. Ruff, format
and mypy cover their six source files, four consumers and scheduler support.
The checkpoint command carries the exact pytest selectors; no full regression
or implementation red-team was run. Overall Phase C remains OPEN.

## Frontier and read-only physical codec checkpoint

`recovery_frontier_registry_contracts` decodes the fixed external registry
schema, verifies ordered row ordinals and separates the embedded content hash
from the full-byte physical reference. It does not establish registry selection
or require a globally complete collection of frontier families.

`readonly_record_contracts` admits exactly ten physical row types, preserving
the four existing recovery row codecs. The pending body is hashed before its
frontier projection, avoiding a self-reference. Canonical bytes, owner, kind,
schema, physical ID and fingerprint are checked. These codecs do not yet
assemble read-only operations or authenticate retained sources.

Evidence: `uv run pytest -q
tests/capabilities/agent_loop/test_recovery_frontier_registry_contracts.py
tests/capabilities/agent_loop/test_readonly_record_contracts.py` passes 23 tests.
Ruff, format and mypy pass for the two source modules and their two consumers.
Selected-source wrappers, combined terminal attempt assembly, registered
runtime readers and atomic publication remain inside the full R14–R17 goal.
Phase C remains OPEN.

## Publication discipline

New contract files, their consumer tests, this inventory and exact source catalog
entries are one Phase-C checkpoint; all other graph gaps remain explicit above.
Budget includes the imported cancellation contract and its tests/docs/catalog.
Intermediate commits run only the new consumers, without full regression or
red-team. Final integrated source admission, legacy compatibility, source discovery
and normative evidence are checked before main; hashes alone establish none of them.
