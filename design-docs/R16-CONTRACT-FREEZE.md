# R16 — effects, outbox, dispatch binding, reconciliation

Status: contracts, candidate algebra, owner preparation and mechanical adapters under
integration. All durable/runtime DoD evidence HOLD. No deployment entitlement.

## Scope and source cut

R16 owns the complete external-action lifecycle, atomic domain-revision/effect-intent
publication, consequential-call acceptance, dispatch authorization and transmission,
authenticated outcomes, reconciliation, explicit duplicate-risk recovery and separately
authorized compensation. R16.1 specializes existing publication mechanics; it does not
defer the full dispatch reducer to R18. R18 integrates the completed owner contracts
with the loop. R14 must pass before retry/recovery completion can be claimed.

Normative sources, read together:

- `VISION.md`, External-action failure protocol (lines 423–451): complete transition
  table, deny consequences, child transmission identity, duplicate-risk and compensation.
- `project-architecture/NORMATIVE.md` lines 91, 135–161, 196–208, 226–228,
  256–260, 430–438, 542, 548 and dispatch conformance paragraph 828: ownership,
  journal/writer, exact semantic binding, authentication, delivery, original streams,
  acceptance, worker registry and upgrade/race fixtures.
- `IMPLEMENTATION-ROADMAP.md`, R16 and A06–A08/A30/A35/A37/A48; V5/V6, T01/T03.
- `HEXAGONAL-CODE-LAYOUT.md`, semantic ownership and Plan/effect/provider-evidence bridges.
- `R4-R5-CONTRACT-FREEZE.md`, `R8-CONTRACT-FREEZE.md`, `R13-CONTRACT-FREEZE.md`.

Existing code: `platform/_sqlite.py` owns the sole physical EventAppender;
`composition/r13_runtime.py` independently decides exact bytes and resulting
commitment, and replays selected decisions at startup; `adapters/driven/loop_sqlite.py`
checks exact owner snapshot and atomic companions inside writer admission.
`capabilities/planning/r8_boundary.py` owns authority trace and proposal freshness;
`platform/deployment_gate.py` supplies deny-only operation gate contracts. The R5
direct-principal CREATE_INTENTION_LINE authority does not itself authorize effects.

The initial R16 worktree was clean (`git status --short`). User-owned changes to
TRANSCRIPTS.md and drafts in the original worktree are not R16 evidence. Shared
root patches are separately inventoried with hashes before application. The operational
union budget is 35 dirty files, below `.harness/scripts/thresholds.sh` hard limit 40.
Initial owner slice: this document, effects public contracts/export and public consumer
test, plus handoff journal. Full owner implementation is estimated at 16–23 files,
with shared registry/broker/catalog and review/retro files included in the union before
edits. Split semantic slices before the union exceeds the budget; never compress
unrelated responsibilities to satisfy a count.

## Plan and assumptions

1. Freeze this requirements/evidence map and owner-local immutable contracts.
2. Validate a consumer using only the intended public surface, then stop the
   new-milestone skill before business logic.
3. After common wire agreement, implement atomic publication/acceptance plus the
   full reducer, then broker-gated hermetic dispatch, evidence and reconciliation.
4. Integrate R14 fences and R17 delivery/ingress bridges, execute every claimed
   counterhistory through the canonical composition, then full checks and cold review.

Assumptions: root owns shared recovery/fence/broker changes. Capabilities exchange
inert wire data with consumer-owned DTOs and composition bridges; effects imports
no agent_loop executable DTO/validator. Provider I/O occurs after durable commit,
never inside a transaction. No real recipient or production entitlement is created.
Review has an isolation benefit but retains the common-model independence ceiling.

## Ownership and shared seam

Effects alone owns ExternalActionIntent, ExternalActionAttemptRevision,
DispatchAuthorization, TransmissionAttempt, provider/delivery attempts, authenticated
receipt identity and outcome/reconciliation reduction. Agent-loop owns sealed calls,
initialized/accepted heads, accepted delivery selection/render manifest and original
call recovery obligations. Planning produces its own exact revision and authority;
the Plan/effect workflow commits those returned bytes in one registered invariant
batch. The broker owns raw handles, authentication consumption, journal decision,
materialization and last-local ordering. Downstream code may frame owner bytes;
it may not reconstruct authority, change bytes or infer missing current heads.

R17 deliveries consume the same effects external-outcome lifecycle. There is no
second delivery attempt reducer or receipt model. Exact provider-recipient tuple,
render/disclosure/provenance heads and delivery invalidators are an effects input
under the delivery-specific SEND_COMMITTED operation. CLI/Telegram share the
canonical loop; ambiguity never reruns the model to recreate an answer.

R14 common wire seam must express exact original Run/call/obligation identity,
initialized/accepted heads, ordered complete acceptance intents, current Run and
worker/session, applicable execution-root live lease or post-terminal-work fence,
versioned reducer and exact independently durable evidence head. Semantic ownership
does not transfer on successor creation. Evidence append and original resolver closure
are two causal commits, not a cross-owner atomic closure. Evidence-present/obligation-open
is legal and remains RECOVERY_HOLD until the registered original resolver acts.

The public effects port never exposes PhysicalPublicationCommand, callbacks,
connection, generic record lists or caller-selected ownership/applicability. Closed
typed operations are registered with owner, exact DTO/record kinds and writer fence.
Exact authenticated replay must be classified before first-publication admission;
the current physical replay fast path alone does not prove A99.

## Required closed dispatch contract

Import the exact VISION dispatch protocol as a version-bound dependency. The states
are INTENT_RECORDED, HELD_BEFORE_SEND, CANCELLED_BEFORE_SEND,
SUPERSEDED_BEFORE_SEND, DISPATCH_AUTHORIZED, SEND_COMMITTED, SENT, CONFIRMED,
FAILED_NO_EFFECT, OUTCOME_UNKNOWN, PARTIAL and PARTIAL_CONFIRMED. The VISION
table is authoritative; this document does not define a competing reducer.

Every intent, authorization and transmission carries unchanged DispatchSemanticBinding:
normative manifest identity, reducer version, transition-registry version,
canonicalization/fingerprint version, adapter-contract version. Every authorizing
transition compares all five parts to supported operation versions. Missing/unknown/
changed versions produce DISPATCH_VERSION_DENIED before publication, or
DISPATCH_VERSION_HOLD for durable work. No inferred compatibility or fallback.

One first TransmissionAttempt child is allocated atomically with SEND_COMMITTED.
Later children retain the logical attempt, next retained ordinal and original
ambiguity; safe proof covers every prior child plus current authority and exact
effect. Absence-at-read, timeout, malformed response, contradictory/incomplete evidence
and expired/unverifiable idempotency never prove FAILED_NO_EFFECT. Every terminal
outcome covers all children. Duplicate ordinals deduplicate only on exact fingerprint.
Unknown/partial outcomes stop dependent steps and conflicting replacement effects.

AUTHORIZE_DUPLICATE_RISK requires authenticated principal adoption of an exact preview
enumerating every unresolved attempt, duplicate consequence, affected party/resource,
commitment consequence and safer alternative; it creates a fresh linked recovery
intent and leaves the old deny state untouched. Compensation likewise creates a fresh
previewed intent with complete current authority, affected-party/constraint, hold/conflict,
dependency/evidence, consequence/scope, proposal/adoption, freshness, endpoint/provider,
semantic binding and exact original effect/binding/ambiguity-head reference. Revalidate
all of it at SEND_COMMITTED; late resolution of the original ambiguity holds dispatch.

Before-send migration requires recorded compatible identity/semantics preservation or
new exact authorization. At/after SEND_COMMITTED no migration/re-authorization resets
uncertainty or creates replacement work. Authenticated late evidence remains admissible
despite dispatch-version mismatch, but only the original bound reducer may consume it,
or it stays held for an explicit compatible reconciliation implementation.

## Meaningful guarantee preflight

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Planned evidence |
|---|---|---|---|---|---|
| Atomic Plan/effect publication | Planning bytes + effects intent under exact registered batch/journal decision | durable none-or-complete records across crash/restart | effect before domain commit, reconstructed Planning record, second writer | omitted/extra/rival companion, pre/post DECIDED/commit crash | `test_effect_storage.py` — HOLD |
| Acceptance before execution | exact initialized call CAS, ACTIVE Run/current fence, ToolExecutionIntent and effect intent | sink send count zero until complete batch, exact replay heads | cached acceptance, generic physical append, accepting after cancellation | accept/cancel serial orders, stale initialized/Run/session/lease, pre-accept dispatch mutant | `test_effect_acceptance.py` — HOLD |
| Exact SEND_COMMITTED cut | full current authority/mandate/rights/holds/dependencies/factual evidence/freshness/target bindings in common order | independent transport log and invalidator order | prior check callback, alias lookup, caller claim of authority | every field substitution, invalidator-first/send-first, expiry equality | `test_effect_dispatch.py` — HOLD |
| Version-bound full reducer | five-part immutable binding and VISION complete table | each legal/forbidden transition and changed-version zero-send | shorthand lifecycle, migration reinterpretation, partial equality | each part missing/changed/unavailable across pending/committed/sent/unknown/partial/compensation | `test_effect_reducer.py`, `test_effect_versions.py` — HOLD |
| Stable transmission/no blind retry | retained child ordinals and all-child safe-proof coverage | independent exact send count, immutable parent history and every child | fresh intent/ordinal reset, point-in-time absence proof, timeout clears ambiguity | omitted/reordered/duplicate child, expired proof, N/N+1 ordinal bound, restart/lost ack | `test_effect_retransmission.py`, T03 — HOLD |
| Authenticated original-stream evidence | broker source/account/key/audience/raw-digest/freshness/correlation binding and original reducer | durable evidence plus distinct resolver closure; late evidence still admitted | plausible payload, lease as source authentication, successor-owned obligation | forged/wrong tenant/account/key/correlation, evidence-before-closure crash, rival closure | `test_effect_reconciliation.py` — HOLD |
| Fresh compensation and duplicate-risk authority | exact new preview/adoption/current authority, original effect/binding/ambiguity reference | new intent with old uncertainty unchanged, late original-head race denies send | old mandate/generic assent, original mutation/retry | omit affected party/alternative/old attempt, stale adoption/original head, both race orders | `test_effect_compensation.py` — HOLD |
| Same gated offline pipeline | canonical assembly, exact current deployment gate at sink cut, registered hermetic leaf | independent fake sink plus real endpoint/network canaries | offline Boolean, green tests as entitlement, eval-only loop | real recipient/leaf substitution, stale gate, replay no reenqueue | `test_effect_runtime.py`, T01/T03 — HOLD |

Every row uses omission, addition/unknown, substitution/alias, duplicate/reorder,
stale/race and logical/physical identity mismatch. N/N+1 applies to retained ordinals,
manifest/byte bounds and entitlement caps; safe proof must retain complete ordered
referential closure rather than truncate it. No mutation family is globally N/A.
Exact equality means the same canonical bytes and current heads at the publishing
writer cut; authenticated identity comes from broker peer/source state independently
of request payload. A shape test cannot discharge any behavioral row.

## Requirement-to-evidence map

All entries remain HOLD until the named observable executes against fixed source bytes.

| Requirement | Required executed evidence |
|---|---|
| A06/A30 current authority and freshness | complete recorder trace reproduction; independently mutate every authority/mandate/dependency/expiry/target input at use |
| A07 domain + intent atomicity | none-or-complete durable batch, crash before/after journal selection/materialization, exact lost-ack replay |
| A08 unknown/no unsafe replay | timeout/loss/malformed/conflicting/incomplete/absence/expired-idempotency histories yield denying uncertainty; no replacement send |
| A35 exact dispatch semantics | all five components unchanged intent→authorization→child; denied/held upgrade matrix and original-bound late reduction |
| A37 separately authorized compensation | fresh complete authority + original ambiguity head at creation and send; original uncertain record unchanged |
| A48 initialized-head acceptance + intent | exact ACTIVE Run/current live lease CAS; concurrent cancel/accept; complete intents; reached pre-accept-dispatch mutation |
| V5/V6 every named state/transition | full VISION table including forbidden edges, terminal behavior, same-parent safe retransmission and all-child conservative reduction |
| V5/V6 every crash edge | before/during/after child+SEND_COMMITTED, send observation lost, materialization restart, evidence append before closure |
| V5/V6 no provider in transaction | independent provider observation asserts transaction closed before every send; provider cannot allocate authoritative records |
| V5/V6 stale binding/rival replay | change every bound current/version field and reuse identity with changed fingerprint; zero extra publication/send |
| V5/V6 reconciliation + late evidence | exact source proof/correlation, every child coverage, post-terminal/old-worker evidence, resolver-only original closure |
| T01 fake adapter | same production loop performs authorized domain/effect journey and independently observed committed fake effect |
| T03 lost response | same production loop reaches one possibly effective attempt, durable unknown + original recovery obligation, zero replacement attempts |
| R14/R17 convergence | worker applicability rows, original-stream recovery causal bridge, delivery uses same reducer and exact tuple/disclosure cut |

## Verification and retained identity

Run `sh .harness/scripts/test.sh --preflight` before broad checks and again after
contract/entrypoint/fixture edits. Keep R8/R12 historical component entrypoints;
extend the canonical R13 loop/driver for T01/T03 rather than invent another loop.
Audit `inert_shared/r8-implementation-v1.json`, `architecture/r7_runtime.py`,
`architecture/r7_storage_surface.py`, and `verification/loop_scenario.py` identities.
Do not repin catalogs to silence failures. Root coordinates shared catalog updates
with changed-byte review and new boundary evidence. Freeze actual production bytes
before final test/lint/type/verification runs; an isolated worktree does not freeze them.
Retain command, source digest, scenario/bundle digest, actual durable trace, independent
fake transport log, reached mutation and default exposure HOLD with every evidence artifact.

Open integration items: real broker publication/query binding and runtime registration;
durable service/gate and all full-journey behavioral evidence; final cold review and
requested retro. None is a completed milestone claim.

Preparation evidence: `uv run pytest -q tests/capabilities/effects/test_contracts.py`
passed 6 public consumer shape tests; targeted `uv run ruff check` and `uv run mypy`
passed for effects and its consumer test. These do not prove dispatch behavior.
`sh .harness/scripts/test.sh --preflight` failed in the canonical corruption
reproducer because `verify_implementation_identity` reports the audited implementation
inventory incomplete/unknown after the new effects files. The reproducer is unchanged;
reviewed root-owned catalog integration is required before canonical startup evidence.

The initial public port distinguishes consequential-call acceptance, Plan/effect
publication and CompleteAcceptance delivery publication. It does not force a delivery
through a fictitious initialized tool call or new Planning revision. Evidence sources
distinguish authenticated provider evidence from exact broker-issued transport
observations; a local timeout/send observation is not a provider receipt.

The effects owner now has its own exact representation of the frozen R14/R15 fence
wire, including scheduler current Run head, tagged subject, selector version, full
clock-proof domains, original obligation stream and explicit NOT_APPLICABLE fields.
Public consumer checks compare all three supported variant schemas and canonical
wire translation. Effects has no foreign capability import. Base64 is explicit for
JSON byte serialization and parsing; a roundtrip covers every possible byte value.

Delivery preparation returns owner-produced bytes for the same CompleteAcceptance
invariant transaction, never a later standalone delivery commit. The consumer-owned
EffectsPublicationPort exposes a typed EffectStoreSnapshot and PreparedEffectPublication;
the broker selects its closed handler/owner/fence row internally. No callback, raw
connection, arbitrary operation or physical publication command crosses this port.

First pure-domain slice: `uv run pytest -q tests/capabilities/effects` passed 20 tests;
`uv run mypy src/chiplog/capabilities/effects tests/capabilities/effects` passed for
6 source files. The source-read VISION table exercises every admitted parent edge and
rejects every absent pair; other tests cover terminal/role/child atomicity, all semantic
version fields, unknown/partial all-child reduction, current authority and expiry,
safe retransmission complete proof and compensation original-head changes. This is
candidate algebra only: broker authentication, transaction-local reads, actual commit,
transport observation, crash races, integration and T01/T03 remain HOLD.

The next owner slice prepares immutable EffectRecord revisions from complete
EffectStoreSnapshot and CurrentEffectInputs. The registered isolated route is
`effects.prepare_transition`, input schema `chiplog.effects.prepare.v1`, result
schema `chiplog.effects.prepared-publication.v1`; semantic denial remains a typed
EffectDenied payload, including DISPATCH_VERSION_DENIED/HOLD. The broker must
independently reproduce observations and bind owner output before the writer cut,
then compare the entire materialization commitment/read manifest inside the writer.
No cross-owner call may execute while the writer transaction is held.

Effects recovery is an explicitly enumerable nested immutable owner record:
`effects.RECONCILIATION_OBLIGATION_OPEN` and
`effects.RECONCILIATION_OBLIGATION_CLOSED` live at
`EffectRecord.snapshot.recovery_obligation`; `.obligation` names its current stable
subject/head, and the closed variant's `.opening` preserves its exact original
attempt, crossed-child manifest, reducer and closure predicate. These selectors
require ownership/frontier registry entries; merely nesting them is not registration.
Unknown/partial opens the effect-owned stream; later complete evidence can close
it while prior revisions remain unchanged. It is not the agent-loop call obligation.

`adapters/driven/effects_broker.py` maps only closed operations to the frozen broker
port, checks exact journal replay before fresh owner preparation and rejects standalone
delivery publication. Its broker-owned query binding and committer remain integration
work. Corrupt selected bytes raise EffectsIntegrityError with operation/tenant/record
context and the original cause. The joint preparation/process/replay wiring scenario
now lives in `tests/composition/test_effects_owner_flow.py`; its prior uncommitted
nodeid was the same function under `tests/capabilities/effects/test_reducer.py`.
Shared builders are `tests/support/effects.py`; no test module is imported.

The private hermetic provider leaf records independent immutable transfers and signed
fixture receipts. Lost-response-after-effect retains one observed effect and does not
retry. The leaf accepts only its exact fixed hermetic tuple; it grants no authority and
does not advertise idempotency coverage merely because a key exists. Only a broker
that authenticated/consumed the exact committed child ticket and current gate may call it.

Latest targeted command:
`uv run pytest -q tests/platform/test_effects_hermetic.py tests/capabilities/effects tests/composition/test_effects_owner_flow.py`
passed 25 tests. Targeted mypy passed for 13 source files, ruff and worktree test layout
passed. Repeat preflight still fails at the unchanged corruption reproducer while the
reviewed source catalog awaits root integration. No full gate or real runtime PASS is
claimed. Cold reviews found and repaired prior-child idempotency coverage, filtering
away unknown evidence members, and an unbound before-send decision reference.

Identity-reset counterhistory: a fresh command/intent/idempotency key could previously
replace crossed unresolved work. `CurrentEffectInputs.blocking_effect_heads` is now a
required broker-derived complete conflict/dependency inventory of exact current logical
attempt heads. The owner validates every supplied head against the latest snapshot,
rejects duplicate/stale/unknown entries, and independently catches same effect/payload
and recipient in SEND_COMMITTED, SENT, OUTCOME_UNKNOWN or PARTIAL. Broker-listed
PARTIAL_CONFIRMED dependency blockers remain effective. Initial publication,
authorization and send commitment retain the barrier; only exact fresh adopted recovery
originals and separately proved same-attempt retransmission have their scoped paths.
Tests exercise changed identities at committed/unknown cuts, omitted and malformed
inventory, different conflicting payload, exact duplicate-risk preview, stale preview
after closure and ordinary release after complete reconciliation. Complete semantic
scope enumeration and writer-time reproduction remain broker integration HOLD.

Retro input: nested src/tests AGENTS instructions were read late. Before promotion,
the adapter failure became typed/contextual and the joint scenario moved to composition;
the incident still belongs in the requested retro rather than being erased by repairs.

## Runtime integration slice after shared baseline 0168d61

Root owns the canonical owner graph and writer integration. The executable entry stays
`composition/r13.py:open_r13_loop` → `r13_runtime.py:open_r13_runtime` → existing
`_open_runtime` and `AgentLoop`. Register the existing isolated route
`effects.prepare_transition` (broker → effects, `chiplog.effects.prepare.v1` →
`chiplog.effects.prepared-publication.v1`) in the closed runtime manifest and process
mapping/attestation. Preserve historical R13 manifest evidence explicitly. Root updates
`architecture/r7_runtime.py`, `platform/r7_runtime.py`, R13 runtime/planning composition,
authority/storage registries, exact source catalog and their consumer checks. Root's
`owner_publications.py` and `owner_decision_journal.py` extend the existing EventAppender
and independent journal; this slice creates no second SQLite writer.

Owner-local plumbing is `adapters/driven/effects_queries.py` and its platform test.
The cold plan review by R17 reported zero P0/P1 after requiring per-intent chains,
independent omitted-tail coverage and explicit publication order. Its immutable
`EffectsObservedCut` carries exact command bytes, tenant/frontier, materialization
commitment, physical rows, `CurrentEffectInputs`, and `EffectsBatchContext` from one
atomic broker read/observe. `EffectsCutSource.read_effect_cut(command, expected)` must
reject an earlier caller-chosen snapshot. Exact replay is authenticated separately
before this fresh cut is acquired. A `BoundEffectsQueries` instance belongs to one
invocation; there is no mutable shared last-cut cache.

`StoredEffectRow` includes commit sequence, publication ordinal and full batch IDs.
Actual EventAppender stores `command.records` order in `publications.record_ids`;
alphabetical record IDs are not a substitute. The bridge validates every canonical
row/digest/physical identity/tenant/schema/kind and original command, then checks
duplicate identities and each intent's predecessor chain. It preserves complete
snapshots and raises chained contextual `EffectsIntegrityError` on corrupt reads.
No row-list decoder alone proves a missing final revision absent: root must enumerate
complete effects membership against authenticated materialization/journal state.

Assumptions requiring root implementation, not caller declarations: independently
derive every AuthorityBinding field from R5/R8 sources; bind all five dispatch versions;
enumerate complete `blocking_effect_heads` and exact original ambiguity heads; preserve
worker-independent provider/transport evidence authentication. Existing R8 TRUST,
PLANNING and REGISTRY traces alone do not supply every effects field. Missing source
bindings HOLD. Root constructs the isolated request before the writer lock, then
rechecks full materialization commitment, read manifest, output issuance, versions and
current registry at the writer boundary without calling owners while holding the lock.
Nested OPEN/CLOSED reconciliation subjects require explicit registry selectors.

Reached runtime histories remain HOLD until root assembly is available:

- T01: actual loop proposal/adoption and exact initialized-call acceptance publish
  complete domain/effect/loop companions atomically; no transport before journal-selected
  SEND_COMMITTED; final gate consumes the exact immutable broker-issued child outside
  the transaction; independent hermetic provider log observes the expected effect.
- T03: the same path loses its provider response after effect; immutable child and
  OUTCOME_UNKNOWN/reconciliation obligation persist across restart; repeated commands,
  replay, changed IDs and ordinary replacement produce no extra child or provider send.
- Boundary mutants: reached preaccept dispatch, current binding/version/fence or original
  ambiguity change, omitted/extra participant, rival replay and both crash sides;
  late independently authenticated evidence reduces under the original semantics.

The bounded query slice adds two paths to the ten own dirty paths observed by
`git status --short` after rollout. Final runtime fixtures/evidence/milestone retro and
root-shared changes still count toward the target35/hard40 union; shared cadence retro
does not satisfy the later R16 milestone retro or behavioral DoD.

Query-slice verification: `uv run pytest -q tests/platform/test_effects_queries.py`
passed 20 cases, including two independent intent chains and rival owner candidates
from one predecessor. The combined effects selection (that file, hermetic leaf,
`tests/capabilities/effects`, and `tests/composition/test_effects_owner_flow.py`)
passed 45 tests. Targeted mypy checked 15 files; Ruff, layout and diff checks passed.
These are bridge/owner/leaf observations, not canonical broker or T01/T03 evidence.

Takeover correction, delegated by root after R17 cold plan review: required
`CommitSendCommand.fence` carries the current execution fence separately from the
immutable original `DispatchAuthorization.fence`. Both branches require exact current
command fence and trusted time. First transmission additionally requires equality with
the original authorization fence. Safe later transmission retains original authorization,
intent and parent state, and still requires exact current authority/version and independently
verified coverage of every earlier child. The actual owner history tests stale first-send
in both forms, successful covered retry after worker/generation change without resetting
OUTCOME_UNKNOWN, and stale fence/missing proof/omitted child/expired proof/changed actor
denials. Combined effects selection now passes 46 tests. A POST_TERMINAL fence is not
itself safe-action entitlement; root must separately establish work scope and the final
boundary. Public cut-source cold review independently ran its 20 tests and found zero
P0/P1 within the mechanical bridge, leaving complete-tail/authority/writer proof to root.

### Authenticated cut and composite adoption implementation slice

Cold plan review by `/root/r17` returned zero P0/P1; async trust acquisition
refinement also reviewed. Own files: `composition/r16_effects.py`, existing effects
query adapter, composition cut tests. Root delegated prepare-only extraction in
`composition/r7_planning.py`; legacy create must use that same preparation and
retain replay/rejection behavior. No publication occurs in preparation.

The additive producer consumes only a uniquely sealed `propose_intent` with a
closed versioned hermetic payload. It renders and stores the exact composite
planning/effect consequence. Ordinary planning adoption must reject this schema.
Authenticated exact adoption validates current source heads and expiry; proposed
planning records remain PREPARED with exact prior CAS until the combined writer.
No generic authority resolver or caller-supplied current-input DTO is accepted.

A real source acquires trust through async isolated IPC, then enumerates the
complete independent owner-journal snapshot and all corresponding SQLite rows,
publication manifests and AMR commitment in one physical read transaction.
It compares journal and SQL identities, complete bytes and physical ordinal;
omitted tail, extra rows and pending selection cannot yield a cut. Independent
heads are re-read to detect races. Immutable BoundEffectsQueries stays synchronous.
Root revalidates the complete cut at writer admission and owns final ticket issue.

Assumptions: initial registered hermetic policy is closed; unsupported party,
factual or communication scope yields HOLD, never invented empty authority.
Original authority-source acquisition metadata is retained only after validating
current semantic source heads; the fresh global physical commitment is separately
bound in the cut. No second writer or loop is introduced. Root owns canonical
assembly, AMR materialization observation and final authority/fence revalidation.
Real positive and corruption histories, legacy prepare/create/replay regression,
and reached T01/T03 runtime histories remain HOLD until executed.

### Reached canonical checkpoint and remaining assembly dependency

The repaired producer performs both trust and planning IPC outside every local
serialization lane. Its pure R8 request construction never writes the legacy
`_traced_request` / `_trace_principal` slots. Synchronous final validation checks
current interpretation, independently verified trust heads, complete source trace,
original display and source deadlines. The exact adoption ingress is an immutable
observation of display/digest/act plus actual trust identity/session/peer bytes;
it is not a broker invocation grant. Cold R17 review found and closed the original
expiry-during-IPC race. Root invocation issuance and writer checks remain HOLD.

`read_materialized_effects` now captures and rechecks resolved physical path,
device and inode, and can observe a NON_SCHEDULER worker from all Run schema rows,
physical manifest order and a complete per-Run validated chain. Root must retain
that physical binding through release and writer admission. Historical R17
CompleteDelivery batches require joining their independently selected exact
`DeliveryPrepareRequest(previous, observation)` to each Run record's physical
sequence and ordinal before calling its historical validator. The R16 mapper now
implements that historical join after the reviewed R17 core transfer; canonical
worker assembly and fresh atomic delivery admission remain root dependencies.
Current trust cannot reinterpret old completion.

Root authorized an interim exact source catalog update after actual code review:
`.artifacts/effects/reviewed-source-pin.json` enumerates sixteen reviewed entries
(nine additions, seven changes); all other entries were asserted unchanged.
`verify_implementation_identity(Path.cwd())` and
`sh .harness/scripts/test.sh --preflight` passed. This admits tests only and does
not change deployment status or pending milestone evidence.

Initial actual canonical test execution reached three passing interleavings:
frontier change, same-Run change, and expiry of the old display while a fresh
preparation remains unexpired. Two other tests reached strict current-worker HOLD:
legacy `open_r13_loop` persists `hermetic-session`, while the authenticated current
worker is `epoch:generation:session`. This is a real assembly gap, not a permitted
alias. Root's future R14 canonical factory must persist the actual worker and
support real takeover/restart. The positive worker expectation remains an explicit
unskipped integration test; independent no-worker materialization integrity tests
were separated after cold review so that path substitution and corruption can be
exercised without weakening that expectation. The separated canonical run reached
five passing tests and one explicit current-worker integration failure. Reopen
currently rejects the legacy placeholder, which was already ineligible before
restart; it does not prove stale actual-worker fencing. That future counterhistory
must first prove a valid actual worker, reopen with a new authenticated generation,
and then reject the old Run. Full EffectsCutSource current
binding, registered commit/ticket issuance, T01/T03 and milestone retro remain HOLD.

### Independent provider receipt/probe verification

The hermetic provider now signs a closed canonical receipt under a dedicated HMAC
domain. Its observation binds a versioned fingerprint of every immutable issued
child ticket field, including exact payload/address bytes, and explicitly carries
the external child/recipient/outcome tuple. The verifier rechecks payload SHA,
canonical envelope and observation bytes, signature, whole-ticket identity and
unique disjoint known outcome members. A missing probe remains `None`/unknown and
never authorizes retry or permanent no-effect. The external log is still the
independent provider object; recreating an empty provider on broker restart is not
a persistence or reconciliation proof.

`uv run pytest -q tests/platform/test_effects_hermetic.py` passed fourteen tests;
R17 independently reproduced fourteen passes and found zero scoped P0/P1 after
reading actual source and mutations. Root also read the codec and mutants. Only
the reviewed receipt source pin changed afterward, with the old hash asserted and
all other catalog entries verified unchanged; the audit artifact retains both
versions. This proves a pure cryptographic receipt boundary, not registered source
identity, issued ticket lineage, evidence custody, or actual RecordEvidence
publication/reconciliation. Those remain pending canonical integration.

### Historical expanded delivery reconstruction

The materialization reader now validates every Run history even without a worker
request. Expanded completion requires the exact selected, materialized
CompleteDeliveryBatch and canonical original DeliveryPrepareRequest, its exact
previous Run, and the full ordered physical batch with every companion byte.
The pure historical validator reconstructs completion without current trust,
lease, or source reauthorization. Unknown publisher schemas and omitted,
substituted, or downgraded Run histories fail closed.

`uv run pytest -q tests/composition/test_effects_historical_delivery.py` passed
fourteen tests; R17 independently reproduced fourteen passes and found zero scoped
P0/P1. These tests use real SQLite and an independent journal but call the history
helper directly. They prove reconstruction, not complete authenticated cut release
or fresh per-delivery effects semantic consistency. Six exact reviewed source pins
were updated after the core transfer and mapper review; all other source hashes
were asserted unchanged and source closure passed. Actual worker assembly,
registered invocation issuance and full R16 runtime histories remain incomplete.

### Actual runtime worker assembly

The additive `open_r14_loop` factory now persists `runtime.current_worker()` from
the started authenticated owner session. It accepts no worker alias and leaves
legacy R13 unchanged. The reached worker test first obtains a valid effects worker
cut, reopens the database with a different actual worker, verifies that the old Run
remains byte-equivalent through the complete historical read, and observes both
the loop accessor and effects worker read rejecting it. A newly created Run under
the reopened worker obtains a valid cut. The targeted worker test passed; the
earlier explicit assembly failure is therefore resolved for this bounded case.

The factory still uses the legacy completion path. Expanded completion, recovery
takeover commands, registered effect invocation issuance and full external outcome
histories are not established by this test and remain required. Root-reviewed
runtime, delivery assembly and trust-route prerequisites were copied with exact
baseline/hash guards and backups; R17 independently reviewed the factory source
before its exact source pin was admitted.

The combined command `uv run pytest -q
tests/composition/test_effects_authenticated_cut.py
tests/composition/test_effects_historical_delivery.py
tests/composition/test_owner_runtime_recovery.py` passed twenty-eight tests.
Factory/test mypy and Ruff, exact source closure, preflight and diff checks passed.
