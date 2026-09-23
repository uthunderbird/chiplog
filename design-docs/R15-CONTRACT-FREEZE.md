# R15 — scheduler occurrence and lease lineage

Status: public boundary materialized; first pure-domain slice implemented.
Durability, worker/recovery integration and milestone completion remain HOLD.
Real exposure is HOLD. No implementation, completion, or recovery guarantee is
claimed by the contract shape checks.

## Context and ownership

Base verified with `git rev-parse --verify HEAD`:
`6f34dafc750ba894bbc7dd8a5a2659f61c87675e`. Worktree: `chiplog-r15`, branch
`feat/r15`. Initial `git status --short` was empty. User-owned TRANSCRIPTS and
drafts in the original worktree are deliberately not inherited or modified.

Sources read: IMPLEMENTATION-ROADMAP.md R15 and dependency graph;
project-architecture/NORMATIVE.md scheduler interval/materialization/lineage,
physical-root lease, worker registry and V5/V6 counterhistories; VISION.md
Recurring and conditional lines, Proactivity/action authority/revocation;
HEXAGONAL-CODE-LAYOUT.md scheduler ownership and trust boundaries;
R13-CONTRACT-FREEZE.md and agent_loop/contracts.py; AGENTS.md;
.agents/skills/new-milestone/SKILL.md; harness thresholds and preflight adapter.

R15 owns scheduler policy within `agent_loop`, as required by the layout's
`scheduler driving adapter -> agent-loop/scheduler inbound use case ->
consumer-owned occurrence/lease ports -> SQLite driven adapter` flow. It does
not create a scheduler capability, rival appender, projection authority, or
Planning recurrence semantics. R21 consumes this execution contract for the
bounded recurrence journey. A cadence request itself grants no effect authority.
Scheduler activation uses authenticated service context and bounded mandate;
payload-carried tenant, actor, clock or operator assertions do not supply trust.

The existing broker-owned EventAppender remains the atomic publication owner.
Root owns common recovery DTOs, broker edits, writer registry and integration.
R15 owns `capabilities/agent_loop/scheduler_contracts.py` and scheduler policy.
Common files arrive through root-issued hash-indexed patches checked with
`git apply --check`; common paths are not independently edited here.

## Plan and assumptions

1. Gather sources and write the complete evidence map below.
2. Agree common tagged lineage, physical fence, lease/clock and failure seams.
3. Materialize only immutable public scheduler DTOs and Protocols; run a consumer
   importing solely the intended public module. Stop the new-milestone stage.
4. After root releases implementation, build bounded intervals, overflow/resolution,
   complete materialization, leases and rollover using existing publication.
5. Integrate R14 recovery, no-retry proofs and A99 registry, then execute every
   mapped counterhistory and the full required gate on the integrated bytes.
6. Run a distinct R15 retro through its skill; prepare its required review and
   explicit commit approval. An uncommitted retro remains incomplete.

Assumptions: exact current-version empty genesis is supported; historical stores
missing mandatory companions are rejected rather than silently adopted. Offline
fake clocks/providers have no real-service reachability. Public shape tests prove
expressibility only. R14 core availability is distinct from final all-writer A99
convergence; scheduler recovery remains HOLD until both integrations pass.

The cold plan reviewer reported no critical preparation blocker, requiring explicit
overflow/resolution, CAS serial orders and atomic initial epoch/genesis coverage.
This is same-model isolated review, not external certification.

Preparation estimates three owner paths: this document, scheduler_contracts.py,
and tests/capabilities/agent_loop/test_scheduler_contracts.py. This is not a full implementation
estimate. Full semantic slices are interval decisions; overflow/resolution;
materialization/canonical DAG; leases/rollover; recovery/integration/evidence.
The union of all dirty paths, including imported common patches, tests, catalogs,
handoff and retro, must remain at most 35; hard guard is 40. Count using
`git status --porcelain --untracked-files=all` before each new slice, inventory its
paths and reassess before proceeding. Reserve evidence/review paths, do not hide
unrelated changes in giant modules. No staging or commit is authorized now.

## Proposed public surface

All DTOs are strict, immutable, extra-field rejecting and version-bound. Public
inputs are proposals carrying observed heads, never authoritative records or
effective authentication. Context is separately issued by the trusted boundary.
Owner use cases derive decisions from canonical state; adapters persist owner
results mechanically. The shape consumer must exercise all closed variants,
unknown/extra fields, field omissions and numeric endpoints without private imports.

| Contract | Required content / owner |
|---|---|
| Common ExecutionLineageSubject | R14: discriminated INDIVIDUAL(occurrence_id) or COALESCED(aggregate_id, manifest_fingerprint); disjoint namespaces |
| Common execution fence | R14: stable root id/subject/fingerprint/head, initial/current Run; separate physical selector head/version and selected epoch id/head; lease head, holder/session, lease id, uint64 generation, expiry, clock version and authenticated transaction-bound proof |
| Non-scheduler fence | R14: closed all-or-none typed NOT_APPLICABLE branch; mixed fields reject |
| ScheduleDefinitionHead / MissedOccurrencePolicyHead | Exact references to existing revision streams and fingerprints, not new authoritative families |
| SchedulerEligibilityBoundary | Exact schedule/policy heads, schedule/revisions, policy kind, previous boundary, exclusive cutoff, enumeration frontier and canonicalization version |
| SchedulerEligibilityManifest | Complete canonically ordered occurrence identities and observed UNDISPOSED heads, exact fingerprint; no representative-member substitute |
| SchedulerIntervalBound / Head | Three positive count/manifest-byte/batch-byte limits; head/predecessor/generation, owner and authority/broker/runtime generations, version |
| Interval command / result | Stable command/version, boundary, bound head and manifest; closed BOUNDARY_ONLY_NO_WORK, SKIP_ALL, COALESCE_SINGLE, COALESCE_MULTI, MATERIALIZE_EACH_SINGLE, MATERIALIZE_EACH_MULTI, OVERFLOW_HOLD |
| Overflow hold / resolution | ACTIVE or RESOLVED_BY_OPERATOR(exact decision); hold fingerprint/head, original interval/frontier/old bound, sufficient successor bound, current heads, complete manifest, selected branch and resulting boundary |
| Materialization commitment | INDIVIDUAL or COALESCED; parent interval/resolution, complete root/Run/init/reciprocal members and physical epoch/selector/genesis lease companion manifest; acyclic primitive/finalized/envelope domains |
| Lease transition | ACQUIRE/RENEW/TAKEOVER; stable command/fingerprint, exact common fence, proposed holder/session/id/expiry, authenticated clock proof and versions; UNLEASED/HELD/GENERATION_EXHAUSTED_HOLD result |
| Rollover command / result | Authenticated operator proof; exact lineage/current Run, selector, exhausted epoch/lease, authority epoch and preceding rollover edge; immutable edge and fresh selected epoch/genesis, no new Run |
| SchedulerPort | Propose interval, replace bound, resolve hold, lease transition, physical rollover, read validated lineage status; tenant-scoped context separate from payload; no SQLite, connection or appender escape |
| Closed failure variants | STALE_HEAD, CHANGED_REPLAY, RIVAL_DISPOSITION, INVALID_MANIFEST, INVALID_CANONICAL_DOMAIN, AUTHORITY_DENIED, CLOCK_UNVERIFIABLE, LEASE_NOT_LIVE, OVERFLOW_HELD, GENERATION_EXHAUSTED, INTEGRITY_HOLD, UNSUPPORTED_SCHEMA_VERSION; final names coordinated with common seam |

No placeholder success implementation accompanies the Protocol. Public read views
must distinguish stable lineage/current Run from selected physical epoch and held
state; rollover cannot appear as a new logical execution.

## Requirement-to-evidence map

Every row is HOLD until its reached executable fixtures pass. Paths below are
planned semantic test modules; final inventories will bind their exact bytes.

| Requirement | Planned observable and evidence path |
|---|---|
| A17 stable revision-bound identity / duplicate scheduler delivery | test_r15_intervals.py: restart and duplicate ingress return identical complete winning batch and one initial Run, despite wall-clock/schedule changes |
| Exact schedule/policy head ordering | test_r15_intervals.py: amendment versus publication in both orders; stale command writes zero disposition/root/Run/boundary/decision records |
| R15.1 zero/one/N policy algebra | test_r15_intervals.py: all three policies cross 0/1/2/N membership; boundary-only and skip have no Run/lease; materialize-each has exactly N independent Runs; singleton coalesce is individual |
| A53 complete eligibility and half-open cut | test_r15_intervals.py: independently enumerate at exact frontier, cutoff belongs next interval, reject omitted/extra/reordered/disposed member and wrong predecessor boundary |
| Monotone authenticated bound head | test_r15_intervals.py: genesis/replacement exact CAS, generation+1, rollback/alias/rival/broker-generation mutation; restart rebuild before admission |
| Three separate interval bounds | test_r15_overflow.py: each dimension N/N+1 and empty/one cases, exact measured bytes from independent enumeration, no automatic prefix/chunk progress |
| Durable complete overflow evidence | test_r15_overflow.py: full manifest if fitting, otherwise authenticated streaming digest/count/order/completeness evidence; replay unchanged hold, reject truncated success or caller count |
| R15.2 active hold blocks amendments | test_r15_overflow.py: every schedule/policy amendment blocked until complete resolution; no supersession or carry-forward |
| Sole sufficient-bound resolution | test_r15_overflow.py: missing/stale/insufficient successor bound writes nothing; one CAS-selected resolution parents every sub-batch and alone advances boundary; duplicate/rival/ordinary competition rejects |
| A58 three-way disposition CAS | test_r15_materialization.py: each individually/coalesced/skipped winner and all conflicting serial orders consume same UNDISPOSED head; changed reuse conflicts; no boundary-only/hold occurrence disposition |
| Tagged subject and complete aggregate | test_r15_materialization.py: namespace/tag/representative substitution, collisions, unknown tag and manifest mismatch reject; equal payload bytes in different tags remain unequal |
| Canonical acyclic domain DAG | test_r15_materialization.py: independent encoders byte-match singleton/N; each included/excluded field mutation, self-commitment slot and schema cycle rejection |
| Atomic initial publication | test_r15_materialization.py: one DECIDED_COMMIT includes parent, dispositions, stable lineage, initial CREATED Run/init, reciprocal bindings, physical epoch/selector and exactly one UNLEASED(0) per lineage; crash before/after every publication/ack edge exposes none or whole |
| Bidirectional startup/rebuild joins | test_r15_materialization.py: missing/extra/orphan/partial/rival/mismatched/genesis/wrong-parent/undisposed-behind-boundary states hold before lease/Turn/effect; projection cannot repair authority |
| Current-version replay gate | test_r15_materialization.py: empty genesis, exact absent/prefix/complete current commitment; corrupt current holds; historical/mismatched companion tuple rejected before new decisions/materialization/authority grants |
| A18 lease transitions | test_r15_leases.py: acquire fresh id generation1, renew same holder/session/id/generation with later bounded expiry, takeover only proven expired exact HELD with fresh id and generation+1; rival CAS selects one |
| Expiry alone / stale worker | test_r15_leases.py: expiry allocates no successor; takeover-before-submit and root/Run/head/holder/session/generation/clock mismatches write nothing |
| A98 uint64 exhaustion | test_r15_leases.py: UINT64_MAX-1/MAX/MAX+1, no wrap/reset; expired max takeover produces durable hold and blocks old-epoch work |
| Authenticated physical rollover | test_r15_rollover.py: operator/authority/selector/exhausted-head substitutions reject; rival/crash/lost-ack selects one immutable decision/edge/fresh epoch/genesis; no Run or prior byte changes |
| Lookup/rebuild/dedup epoch invariant | test_r15_rollover.py: exactly one selected physical epoch per stable lineage, original initial/current Run and authority bindings retained; stale old-epoch work rejects |
| A40/A47 R14 successor integration | test_r15_recovery.py: same transaction binds full stable/physical/lease/clock tuple and recomputed frontier; renewal at same generation, takeover or rollover before submit stales successor; exact committed replay only |
| A43 no-retry lineage | test_r15_recovery.py: accepted effects/unknown model/delivery boundaries survive takeover/successor/rollover; no rival effect/Run; original evidence streams remain appendable |
| A51 live ACTIVE worker admission | test_r15_recovery.py: ToolCallAccepted/read-only attempt reject terminal/suspended/superseded/expired/stale Run or lease; no budget reset via successor/takeover |
| A99 scheduler writer specialization | test_r15_recovery.py: independently discovered path×record×applicability equals registry in both directions; pre-root FIRST_PUBLICATION/MATERIALIZE_EXACT_DECISION/RETURN_EXACT_REPLAY, live epoch and physical rollover; raw/unknown/wrong variant rejects |
| No extra authority from cadence | test_r15_recovery.py: scheduler service context/mandate required, payload identity cannot authorize; revocation/hold fences unstarted consequential work |
| V5/V6 complete convergence | R15 fixture catalog plus integrated R14 registry/recovery suite; reached mutants and observed publication counts, not declared test names; full gate runs on final source snapshot |

Planned modules live under `tests/capabilities/agent_loop/` for direct scheduler
inputs, `tests/platform/` for direct broker inputs, and `tests/composition/` for
canonical runtime/cross-owner scenarios, following tests/AGENTS.md. A boundary
unit split requires an updated path inventory. R14 integration failures remain R15 incomplete,
not deferred DoD. R17 shares ingress/dedup observation; R16 supplies actual effect
no-retry observable when integrated, never a fake assertion of provider success.

## Meaningful guarantee preflight

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Planned evidence |
|---|---|---|---|---|---|
| Exact interval | scheduler decision over authoritative heads/frontier | independent eligible-set enumeration and committed records | caller manifest, latest wall time, projection | cutoff and concurrent amendment | intervals |
| Bounded and complete | authenticated bound head plus whole-set metrics | independently encoded count/manifest/batch bytes and boundary progress | chunking, subset, raised local config | each limit N/N+1 plus restart | overflow |
| Atomic complete batch | owner-authored parent/member/companion commitment | journal/materialization crash snapshots, reciprocal joins | count-only checks, root before Run, synthetic companion | every DECIDED/materialization/ack cut | materialization |
| Canonical identity | versioned acyclic primitive/finalized/envelope domains | second encoder and schema dependency DAG | self-covering fingerprint or representative alias | inclusion/exclusion mutation | materialization |
| Same lineage, new epoch | stable tagged lineage and protected selector | pre/post byte equality and one selected epoch | new Run, root identity rewrite, copied authority | exhausted rollover race | rollover |
| Authenticated live lease | separately issued service identity and registered clock proof | mismatch witness and linearization-cut expiry check | payload holder or caller time | exact expiry, stale session, unverifiable clock | leases |
| Owner-only resolution | scheduler authenticated operator decision and hold CAS | durable one parent/boundary and all member dispositions | ordinary second interval, adapter-created records | rival resolution/amendment | overflow |
| Exact worker fence | R14 common binding plus R15 lease/selector heads | discovered writer inventory and stale-write absence | same generation alone, old selector, independent cached check | renew/takeover/rollover before submit | recovery |
| Preserved no-retry | original action lineage and accepted boundaries | zero new effect/model transmission under takeover | copied obligation or renewed authorization inferred from time | lost response then successor | recovery |

All seven mutation families apply: omission (member/companion/head), addition or
unknown value (extra member/tag/path), substitution/alias (representative,
namespace, holder, operator), duplicate/reorder (manifest/decision/edge), stale/race
(head replacement/takeover/rollover), boundary N/N+1 (all three sizes and uint64),
logical/physical mismatch (stable lineage versus epoch and Run). None is N/A.
Each family must reach its intended guard; an unrelated earlier rejection is not
conformance evidence. Bound evidence includes ordering, complete membership and
legal progress, not merely a numeric comparison.

## Stable verification source and open seams

The final contract consumer imports only public agent_loop modules. Historical
R8/R12/R13 fixture drivers retain their documented historical entrypoints; new
scheduler integration uses the canonical R13/R14 assembly and real owner ports.
Before catalog generation, record the exact common patch hashes and all local
source/test bytes consumed by the generator; freeze them for that verification.
Any later source or driver change invalidates the affected evidence and reruns
preflight/catalog/integrated verification; worktree isolation alone is not a pin.
Run `sh .harness/scripts/test.sh --preflight` before broad verification and after
contract or driver changes. It does not authorize commit or replace the full gate.

The accepted root shared identity/fence slice is
`capabilities/agent_loop/recovery_contracts.py`, source SHA-256
`69fcf97acecf3e832f1a52605669452ab544a8e78bdbc62873e9f829692d5761`, verified
with `shasum -a 256`. Root distributed v1 plus v2 test-annotation correction and
v3 mandatory scheduler Run revision head; patch manifests are retained under
the root worktree's `.artifacts/r14/`.
R15 imports these types unchanged. Public owner boundary is now
`capabilities/agent_loop/scheduler_contracts.py`; shape consumer is
`tests/capabilities/agent_loop/test_scheduler_contracts.py`.

Executed preparation evidence: `uv run pytest -q tests/capabilities/agent_loop/test_scheduler_contracts.py
tests/capabilities/agent_loop/test_recovery_contracts.py` passed 25 tests (six R15,
19 shared). Targeted `uv run ruff check` passed; targeted `uv run mypy` passed after
adding explicit TypeAdapter test annotations. `git diff --check` passed. These are
shape/typing evidence only, not behavioral or authorization evidence.

Initial `sh .harness/scripts/test.sh --preflight` passed before new source files.
After materializing contracts, it fails the existing corruption reproducer:
`verify_implementation_identity` reports `audited implementation inventory is
incomplete or unknown`. Running
`sh .harness/reproducers/corrupt-authoritative-projection-records-must-fa/run.sh`
reproduces this exact source-inventory rejection at canonical startup. Root owns
the reviewed catalog update; no catalog was repinned here to suppress failure.

Open: owner contract cold review; authenticated service/clock proof consumption;
broker scheduler publication seam; final writer registry discovery and R14
activation API. The common scheduler fence now explicitly requires `run_head`
alongside the stable lineage-owned current Run identity (A51).
These are coordination inputs, not permission to narrow any requirement above.

## First pure-domain slice

`scheduler_domain.py` implements canonical occurrence identity, numerical half-open
eligibility over a supplied authoritative snapshot, exact canonical manifest
comparison, zero/one/N policy algebra, tagged execution subjects and the count and
manifest-byte measurements. It rejects coordinate aliases, wrong revision/identity/
head fingerprint, duplicate source occurrence, submitted omissions/additions/order
changes and stale frontier binding. The first explicit coordinate codec is
`chiplog.scheduler.unix-ns.v1`: canonical nonnegative decimal nanoseconds. It does
not implement TaskSeries recurrence semantics or silently interpret arbitrary
coordinate strings; unregistered codec versions reject.

The writer must obtain the complete authoritative source itself at the transaction
cut. This pure function cannot prove an externally supplied tuple complete. Every
measurement explicitly retains `whole_batch_admission=PENDING_FINALIZATION`;
serialized whole-batch sizing, Run initialization, domain DAG and publication remain
pending. Passing count/manifest limits is not permission to publish a decision.

`uv run pytest -q tests/capabilities/agent_loop/test_scheduler_domain.py
tests/capabilities/agent_loop/test_scheduler_contracts.py
tests/capabilities/agent_loop/test_recovery_contracts.py` passed 39 tests, including
14 pure-domain cases. Targeted ruff and mypy passed. Domain tests independently
encode identity/manifest digests and reach two N/N+1 measurement edges, closed
policy/cardinality algebra, coordinate aliases, half-open cutoff, manifest/member
mutants and tagged aggregate membership. This evidence closes only that pure
algebra slice; none of the mapped transaction/restart/lease requirements is promoted.

Cold preparation review's P1 fixes: lineage rollover view carries the shared
`RolloverPredecessor` decision-and-edge pair; test placement follows tests/AGENTS.md;
public successful materialization/resolution/rollover construction and JSON
roundtrips were added. Pure-domain plan was independently reviewed before execution.
Handoff decision [2] records the explicit coordinate codec and rejects lexical
ordering or implicit normalization; no acknowledgement or commit is performed.

## Pure lease candidate slice

`scheduler_leases.py` derives an unpublished lease candidate from the exact current
lineage/selector/epoch/lease view and command. It requires a separately reproduced
issued observation matching the full proof reference, command payload (excluding
the proof to avoid cycles), fence fingerprint, command identity and submission cut.
Authenticated holder/session, registered expiry horizon, used lease identities and
held clock contract are checked separately. Construction of that observation is
not authentication; the broker must supply it from actual issuance/session/history
records at the writer cut.

ACQUIRE requires genesis and generation one; RENEW preserves holder/session/id/
generation and requires live old expiry plus a strictly later bounded expiry;
TAKEOVER requires proven expiry, fresh id and generation+1. At UINT64_MAX it derives
GENERATION_EXHAUSTED_HOLD without wrap/reset; only the separate rollover path may
leave that state. No transition changes the current Run or physical selector.

`uv run pytest -q tests/capabilities/agent_loop/test_scheduler_leases.py` passed
14 cases: exact expiry before/equal/after, generation MAX-1/MAX, all lineage/selector/
lease field substitutions, proof/cut mismatches, independent session/clock mismatch,
id reuse and both simulated rival-renew orders. Targeted ruff and mypy passed.
Simulated issuance/CAS is pure-domain evidence only; durable lease/restart/rollover
and trusted clock correctness remain HOLD. Root's cold review accepted the plan;
the implemented slice awaits independent artifact review.

## Serialized owner lease preparation

`scheduler_preparation.py` defines the exact serialized input: operation, separate
context issuance reference, command bytes, and broker-reproduced tenant/frontier/
materialization/registry snapshot with current lineage, issued lease observation
and submission identity. The owner checks canonical command encoding, operation
match, tenant and proposed holder/session before deriving a candidate. Its one
`scheduler.lease_transition` record retains complete previous view, command,
issued observation and candidate plus exact source cut; output includes canonical
record bytes and their SHA-256. It exposes no store handle or callback.

`_scheduler_process.py` supplies the pure `scheduler.prepare_lease` route with
`chiplog.scheduler.lease-preparation.v1` input and `chiplog.scheduler.prepared-lease.v1`
output. Seven direct dispatch tests cover successful complete canonical record,
cross-tenant/session/operation/proof/submission mutation and noncanonical envelope
or command. Existing owner runtime wiring is root-owned and pending; direct handler
tests do not prove the canonical isolated subprocess or durable authority.
Ruff/mypy passed; `test_owner_publication_contracts.py`, historical
`test_public_contract.py` and the preparation module together passed 12 tests.

Root's v4 patch supplies the real `RunRecord.root_binding` union with immutable
`SchedulerRootReference`, and broker exact-replay lookup contract. Those shared
types are consumed unchanged. Full materialization will initialize that real Run
with stable root bytes, not a scheduler-specific replacement Run model.

## Pure complete materialization and overflow/resolution

`scheduler_materialization.py` now derives the real initial Run plus stable lineage,
initial physical epoch, selector, genesis lease, initialization, reciprocal references,
occurrence dispositions, aggregate when applicable, and explicit epoch-companion
manifest. The parent primitive excludes every child-derived identity and envelope
commitment. A strict batch primitive excludes lease/init/reciprocal/commitment slots;
genesis derives from that primitive; finalized member commitments precede envelope
commitments. The registered `SCHEMA_DAG` rejects unknown/duplicate/cyclic domains.
It is currently a registered constant, not an independently generated inventory
of executable schema dependencies; that source-bound completeness proof remains
HOLD for full convergence.
Disposition materialization references now explicitly carry primitive-domain identity,
not the finalized envelope fingerprint, avoiding a disposition↔envelope cycle.

The interval envelope binds complete non-envelope record bytes and the result with
its own commitment field excluded. Its owner-record-list size domain includes the
parent, all full Run/root/lease/initialization/companion records and the final result;
it is not a count surrogate. This is the scheduler's versioned owner-batch serialization,
not transport/IPC or a differently encoded generic broker publication envelope.
Runtime admission must reproduce this exact registered domain before byte-bound
evidence can be promoted. Count/manifest overflow is selected before Run expansion;
whole-batch overflow returns only the complete hold candidate, no ordinary records.

Overflow uses full manifest when it fits, otherwise digest/count/first/last/order and
the broker-reproduced enumeration-completeness reference. Resolution requires the
same held boundary/membership under a current sufficient successor bound, emits
one resolution parent for every sub-batch, one result boundary and explicit
`RESOLVED_BY_OPERATOR` record. Operator/context authentication, active-hold CAS,
monotone bound history and duplicate-resolution exclusion still belong to runtime.

`uv run pytest -q tests/capabilities/agent_loop/test_scheduler_materialization.py`
passed 13 cases. They cover every policy × 0/1/3, actual Run genesis validation,
Run/root/epoch/lease bijections, aggregate absence/presence, independent canonical
encoder and genesis/finalized/interval hashes, exact referenced-record fingerprints,
forbidden self-slots/cycles, subject/manifest mutations, whole-batch N/N+1,
streaming overflow and resolution. Targeted ruff and mypy passed. These are pure
factory results; atomic SQLite/journal visibility, restart, replay and actual
current-head authorization remain HOLD. The implemented slice is ready for cold
artifact review; no commit or retro completion has been claimed.

## Configuration stream proposal slice

`scheduler_configuration.py` constructs atomic definition/policy/bound genesis and
closed amendment proposals. Schedule and policy amendments compare both exact
heads recomputed from canonical revision records and reject an active overflow
hold. Bound replacement compares the complete prior record, advances generation
once without wrapping, and preserves an active hold so operator resolution can
proceed. Authentication, journal publication, replay and durable race outcomes
remain broker work; these constructors do not establish authority.

The explicit definition grammar is fixed-interval unix nanoseconds with a finite
positive period and optional exclusive end. Every enumeration requires a finite
cutoff. Exact disposed occurrence identities are checked against that revision;
eligible identities are derived from the definition, not a caller's eligible list.
Arithmetic counting precedes allocation. The arithmetic-only helper can return
a witness without allocating occurrences; owner preparation now uses the complete
streaming encoder below before proposing any overflow hold. TaskSeries recurrence
remains outside this grammar.

`uv run pytest -q tests/capabilities/agent_loop/test_scheduler_configuration.py`
passes five cases covering complete/partial genesis, canonical heads, stale heads,
hold amendment rejection with bound replacement, finite half-open enumeration,
disposed identity/duplicate mutants, large arithmetic overflow and revision
identity changes. Targeted ruff/mypy pass. Cold actual review caught a bound-edge
head mismatch: the predecessor now uses the actual previous `head_id` plus the
hash of its full bytes. Independent genesis→1→2 regression review passes with
zero remaining P0/P1 in this pure slice.
`git status --porcelain --untracked-files=all | wc -l` reports 21 dirty paths,
including received shared files; the operational ceiling remains 35, hard 40.

## Physical rollover proposal slice

`scheduler_rollover.py` produces six complete ordered records for the exact
exhausted snapshot: decision, old-epoch terminal fence, fresh epoch, genesis lease,
reciprocal edge and selector advance. The lineage and current Run remain identical.
Authority proof payload retains the authority head, command identity, predecessor
decision/edge pair and full exhaustion epoch; only self-covering proof slots are
excluded. Exact broker-issued snapshot/submission/history comparisons reject
stale inputs and historical epoch/genesis aliases. The selector cannot wrap.

Decision identities derive from a primitive without child outputs. The decision
names its deterministic edge ID; the edge binds the full decision fingerprint;
the final envelope hashes all exact record bytes. Rollover physical-root/genesis/
selector bodies have explicit distinct `rollover-*` schema domains to avoid
overloading initial-publication schemas with different canonical bodies. Root
owns registration and startup decode of both domains.

Nine tests exercise complete records, independent byte hashes, stable lineage/Run,
exact fence domains, predecessor decision and edge separately, proof self-slot
mutants, snapshot/submission/history mismatch, epoch/genesis alias and selector
exhaustion. Targeted ruff/mypy pass. Independent actual review reported zero
P0/P1 in the pure proposal scope. Durable winner
selection, journal replay, startup traversal/rebuild, registry admission and
old-epoch runtime rejection remain HOLD; pure proposal tests do not close them.

## Serialized preparation expansion

The isolated handler now declares configuration, interval and rollover preparation
routes in addition to leases. Closed configuration command variants produce the
exact three genesis records or one amended stream. Interval preparation derives
membership and Run inputs from actual definition/policy/bound records and exact
disposed identities; it compares the durable prior boundary, predecessor, hold,
publication fence and operator proof before invoking the materializer. Rollover
preparation preserves the exact issued observation and submission cut.

Each request contains a broker-reproduced source cut and authorized command hash;
the owner output retains that cut and the full request hash, plus all ordered
canonical record bytes. Public DTO construction authenticates nothing. Root must
reproduce those fields and compare them at publication, while actual isolated
owner execution stays outside the writer lock. First publication is the only
fresh interval-preparation branch; decided prefixes and exact replay use the
journal's recorded bytes through root's authenticated lookup/materialization path.

Preparation now handles complete streaming evidence as described below. Generated
schema dependencies and startup joins remain required work. Direct wire-handler
tests cover all
four configuration variants, interval, resolution and rollover plus authority,
membership, hold, boundary, operation/schema and canonical-byte mutants. Targeted
ruff/mypy pass. These are serialized handler tests, not evidence of a canonical
subprocess invocation or broker publication. Independent actual review of the
initial eighteen-test route slice reported zero P0/P1.

## Complete streaming overflow evidence

`stream_definition` visits every eligible occurrence in canonical order, validates
revision-bound identity and exact undisposed heads, and incrementally hashes the
same registered manifest byte domain as full enumeration. It measures canonical
manifest bytes exactly and records count, first/last heads, ordering version and
the broker-reproduced completeness reference. It retains a full manifest only
while that manifest fits the registered byte bound. A count-only overflow therefore
retains full evidence when required; exceeding manifest bytes drops the retained
prefix and finishes the digest, never a partial success.

The public decide command accepts the existing closed full-or-streaming witness
union. The owner compares the entire submitted witness with its independent
enumeration. Within bounds, ordinary decisions require full input; resolutions
still require full input under the sufficient successor bound. Every policy emits
only one complete overflow-hold candidate when count/manifest limits are exceeded.
Whole serialized batch measurement remains the separate final materialization
check. There is no scan-budget semantic branch or automatic interval chunking.

CPU work is O(N); interruption yields no returned candidate. Retained eligible
members are limited by manifest bytes; arithmetic disposition validation retains
O(D) state for supplied disposed identities. Independent full encoding tests cover
empty/1/4/50, Unicode and exact byte edges. A 1,999-member streaming fixture measures
peak traced memory below half its 956,511-byte full manifest. Omitted, duplicate,
reordered, substituted witness/proof and ordinary streaming-shortcut mutants reject.
Configuration/preparation/materialization tests pass 48 cases; independent cold
review reports zero P0/P1 in this pure slice. Actual authority, publication,
cancellation/restart and protected source completeness remain runtime evidence.

## Generated schema audit checkpoint

`verification/scheduler_schema.py` reads actual materialization and rollover source
and generates canonical-domain dependencies through a restricted, field-sensitive
AST interpreter. The handwritten `SCHEMA_DAG` is not its oracle: changing that
constant leaves generated dependencies unchanged. Canonical preimage operands,
local helper returns, model-copy updates, literal field exclusions and control
dependencies contribute edges. Unsupported mutation, dynamic domains, bindings,
module execution and helper syntax fail closed. Audited encoding leaves have exact
AST fingerprints; imported implementation and model versions still require the
root's admitted source manifest. This is a restricted source audit, not a general
Python semantics proof.

The negative suite exposed a branch-control omission when different literals had
equal abstract dependency values. A failing if/else lease-to-primitive mutant now
passes by rejecting the forbidden dependency; branch writes and returns preserve
their conditions. `uv run pytest -q tests/verification/test_scheduler_schema.py`
initially passed eighteen cases. Independent review then exposed a comprehension
cardinality dependency omitted when the yielded element was constant. Cardinality
is now tracked separately from element fields; constant, nested and filtered
generator mutants raise the suite to twenty-one cases. Independent rereview ran
those tests and found zero remaining P0/P1 in the inspected class. Build/startup
invocation and binding the complete admitted
source manifest remain open integration work; the generated artifact alone does not
close the R15 dependency guarantee.

Startup validation will consume immutable journal-selected record bytes and exact
historical schema versions. It must not replay historical preparation or refresh
lease/operator authorization to choose new output bytes. It verifies causal joins
and materialized-store equality and rebuilds indexes only. The R14 external lineage
advance record mapper is not yet implemented; unknown external records require an
explicit integrity hold until that mapper is integrated within the current scope.

The startup checkpoint accepts configuration and ordinary interval histories. It checks
selected ordinal/decision uniqueness, exact tenant and physical database identity,
ordered member bytes and bidirectional equality with the complete materialized cut.
Definition, policy and bound genesis/predecessor/generation checks rebuild indexes.
Bound identity verification uses the original selected context and command ID,
because these are hash preimage inputs absent from the bound body. Ordinary interval
validation checks parent/result manifests, policy bijections, initial Run/root/genesis
lease/init/reciprocal companions and exact full envelopes without preparing a command.
The combined checkpoint also validates active overflow, bound replacement and exact
resolution, historical acquire/renew/takeover/exhaustion, and six-record rollover
causal joins. Twenty-seven direct component tests pass; mypy and ruff pass. Full
combined cold review is pending. No live authority is consulted. Unknown external
R14 successor/lineage-advance records still hold pending the root's registered mapper.

Overflow validation reproduces the full historical enumeration and dimension order:
member count, canonical manifest bytes, then serialized whole batch. For the final
dimension only, the exact historical compiler measures the complete canonical member
list, including base64 payloads, schemas, identities, companions and final result.
Its hypothetical records remain local and are discarded; selected journal bytes
remain the sole replay output. Tests rehash incorrect scalar/limit/dimension inputs,
reject equality-at-limit overflow, and prohibit changing Run inputs while a hold
is active. All three dimensions have complete hold→bound replacement→resolution
histories. The broker must pin the compiler's full transitive historical model,
serializer and helper source closure, not just one source file.

The lease history retains used IDs per stable lineage across physical rollover.
Rollover tests exercise the local inductive transition at UINT64_MAX and removal of
each of six records; they do not pretend a short fixture enumerates every generation
from genesis to MAX. Current authority epoch is not equated with the physical root's
creation epoch. Independent historical clock/operator/enumeration proof provenance,
complete journal/physical database cuts and source-version dispatch remain root
integration obligations; internally matching DTO fields do not authenticate them.

The registered boundary genesis is the original immutable `ScheduleRevision` with
revision zero. Its exact head/fingerprint and original `start_ns` anchor the first
half-open interval; later amendments never replace that source. Subsequent intervals
bind the preceding terminal boundary. Rebuilt, internally consistent shifted-first
manifests/envelopes reject both before and after amendment. Independent narrow review
ran eighteen tests and found zero P0/P1 for this mapping. The broker's fresh issuer
must include the exact original revision in its authenticated read manifest and pin
the registered mapping source/version; startup checks alone do not close fresh
publication. This choice is recorded as handoff decision 4.

The broker-private `platform/scheduler_reads.py` now extracts real independent
selected journal history and the complete ordered physical batches from one SQLite
snapshot, with independent materialization commitment and final path/device/inode
checks. It retains exact full preparation requests, protected command identities,
registry references and historical proof fields. Fifteen focused real-store tests
cover extraction, altered manifests/bytes/identity, pending selections, independent
anchor changes, database replacement and temporary-table shadowing. The returned
type is explicitly `SOURCE_ADMISSION_UNRESOLVED`; no caller callback or literal
validator name grants source admission. Root registry admission and release-time
revalidation remain required. The source closure inventory in
`.artifacts/r15/startup-source-closure.json` is a static inventory, not an admission
proof or replacement for the generated dependency audit.

An actual producer regression exposed a configuration taxonomy mismatch missed by
hand-built startup fixtures. Startup now consumes only the exact prefixed producer
kinds and schemas; the actual preparation result passes and a renamed unprefixed
alias fails. The incident is recorded in `.harness/observations.md` for milestone
retro. The startup suite now has 28 passing tests; independent narrow review cleared
the taxonomy repair without extending its claim to runtime source authentication.

The current source registry in `composition/scheduler_source_registry.py` admits
one explicitly reviewed offline runtime profile. Its manifest pins fourteen local
historical compiler/validator modules, the actual generated-DAG extractor, lock
bytes, interpreter executable and shared library, complete standard-library tree,
and five installed serializer dependency trees including native Pydantic-core.
The full relative-path inventories are retained in
`.artifacts/r15/source-runtime-inventory.json`; only generated Python caches and
stdlib site-packages are excluded from the stdlib tree, with serializer packages
checked separately. Symlinks inside package/runtime trees are unsupported; the
interpreter executable and shared library are resolved before content hashing.
Absolute installation paths do not enter the profile or registry reference.

The registry compares against fixed reviewed pins and runs the actual dependency
extractor on the admitted materialization/rollover bytes. Its reference derives
from the canonical profile and generated graph. The composition entry point obtains
its own real scheduler cut, requires every protected request to name that exact
registration, and invokes the fixed immutable startup validator. Successful source
admission retains the complete cut and registration for root release rechecking.
Unknown runtime, source, or historical registry remains explicitly unresolved.
Trusted bootstrap must pin the registry module and its manifest; this mechanism
does not prove correctness of external libraries or resist hostile in-process code
replacement. OS loader/system-library trust remains a deployment prerequisite.
Another interpreter/package profile needs separate review and registration; there
is no portable-runtime claim or automatic pin refresh. Handoff decision 5 records
this boundary. Future fresh issuance and external R14 causal mappings remain open.

Independent source-registry review reproduced a retained GENESIS command with a
different interval period from otherwise valid selected configuration records.
Protected journal membership alone does not prove that command/result relation.
After source admission, a closed four-family historical equality check now runs
the exact pinned pure compiler against the retained original request, including
its saved snapshot, context and proof values, and compares the complete ordered
canonical record DTOs. All computed records are discarded. There is no current
clock, live authorization, IPC, new selection or recovery-byte substitution;
materialization still replays the protected selected bytes. Root explicitly
approved this historical comparison and cold review cleared the bounded plan.
The real-store regression checks a valid producer history first and then changes
the retained command plus its authorized command fingerprint while retaining the
original selected records; the equality check rejects the substitution.

The reviewed delivery core and captured-response parser were transferred with
before/after hash guards from root. This extends the actual historical compiler
closure with `delivery_preparation`, `delivery_contracts` and `response_parsing`.
The explicit new registry fingerprint is
`a36851ad3803877a28304b63356a242a7b8176de03e824a5f2f53b711e739536`;
the prior unpublished test-only candidate is archived without changing its identity
under `.artifacts/r15/source-registration-pre-delivery`. The manifest revision
checks permit only the reviewed core changes and these new transitive dependencies;
runtime, package and lock pins remain unchanged. Full focused verification after
transfer passed 162 tests, including 28 real-reader/source-admission cases, actual
scheduler producer/whole-batch metrics, immutable startup and generated DAG mutants.
Mypy over the registry/reader/test files, Ruff and diff checks passed. Final narrow
independent review compared every admitted source hash and the archived/current
profiles, confirmed unchanged runtime/package/lock sections, and independently
passed all three admission tests with zero remaining P0/P1 in the scoped result.
