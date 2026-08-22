# Plan / Fact / Journal — semantics

> Status: proposed. Authority and provenance are defined in [README.md](README.md).

## Proposed core contracts and implementation clauses

### Proposed semantic contract manifest

These are the outcome requirements proposed by the P0 contracts. They are adopted
only to the extent reproduced in `VISION.md`:

| Contract | Proposed product-semantic requirement |
|---|---|
| P0-01 | Current normative state comes only from an explicit authorized planning revision; claims and views never mutate or select it. |
| P0-02 | Plan expectations are not facts or completion evidence; facts remain typed, sourced, fallible, correctable, and non-normative. |
| P0-03 | Tasks, planned occurrences, journal-native work occurrences, and expectation seeds have distinct stable identities and deterministic semantic cardinality. |
| P0-04 | Recurring series, rule revisions, materialized occurrences, source slots, exceptions, and transformations preserve history and have unambiguous ownership. |
| P0-07 | Claim and occurrence lineage is historical, later-to-earlier, acyclic, scope-preserving, and never transfers closure or authority. |
| P0-08 | Authority, acceptance, delegation, commitment, arrangement, and affected-party scope remain distinct, attributed, bounded, and exact-node-specific. |
| P0-09 | Planning lifecycle, epistemic outcome, and due/overdue presentation are separate; deadlines and provider state never imply completion. |
| P0-10 | Evidence maps only to registered typed claims at its admissible level and never closes planning without a separate authorized command. |

The remaining prose in these P0 sections proposes one precise realization and
testable rationale. Its storage, CAS, atomicity, transaction ordering,
policy-registry, index, serialization, and enforcement mechanisms remain proposed.
P0-05–P0-06 remain wholly proposed effect-safety mechanisms.

### P0-01 — sole normative authority and canonical transaction

Each normative identity resolves current state from the greatest committed head of
its closed `PlanningRevision` family. Every planning command names and CAS-checks
the exact current heads for every family it changes; a stale target is rejected.
A canonical planning transaction atomically commits the validated command audit,
its command-appropriate `PlanningRevision` member(s), and one
monotonically allocated `commit_seq`. If any part fails, none is visible. Views
select planning state by accepted `commit_seq`, never journal arrival.

A claim may be cited by a later command. It is not a command. There is no evidence,
authority, or reconciliation projector permitted to mutate planning.

### P0-02 — Plan versus FactClaim

`PLAN_EXPECTED` records what an exact task revision expected. It is never completion
evidence. Claims require a registered claim type, semantic slot, policy version,
source type, exact source snapshot, subject, and immutable assertion status.

`CONFIRMED` means only that evidence policy confirms that claim type. A provider
receipt may confirm `TOOL_EXECUTION_CONFIRMED`; it cannot automatically confirm
`REAL_WORLD_OUTCOME_OBSERVED`, task completion, attendance, delivery to a human, or
satisfaction of an obligation. No receipt level implies the next.

A registered claim key is `subject × claim_type × policy-defined semantic slot`.
Disposition effectiveness and admissibility are one policy-pinned recursive
reduction. `CORRECT` or `SUPERSEDE` displaces its target only after the replacement
resolves to an admissible contribution on the exact registered key under the exact
claim-authority, admissibility, and disposition-precedence heads. If any required
policy is absent/unknown, or the replacement is inadmissible or unresolved, the
target retains its contribution and projection additionally reports
`UNRESOLVED_POLICY` or `DISPUTED` as the registered policy requires. `RETRACT` has
effect only after its own authority and precedence checks pass. A correction or
retraction targets a lower `stable_append_seq`, keeps the original historical, and
replaces only that claim's current epistemic contribution. Equal-strength admissible
non-superseded conflicts on one key project as `DISPUTED`; neither arrival time nor
latest-write-wins is precedence. Missing precedence policy fails closed. No
correction, retraction, conflict resolution, or projection has a planning effect.

### P0-03 — subject, occurrence, and expectation identity

Event claims use `OccurrenceSubject(occurrence_id)` for a planned occurrence or
`WorkOccurrenceSubject(work_occurrence_id)` for a journal-native episode. Resultant-
state claims use `TaskSubject(task_id, task_revision_id)`. `TaskSubject` forbids
`EVENT` and `occurred_at`; occurrence subjects forbid task-result claims that lack an
explicit evidence mapping. Neither can masquerade as the other.

Subject allocation is total. An event identified as the realization or non-
realization of exactly one existing planned `TaskOccurrence` uses that
`OccurrenceSubject`, including when the report adds a time, duration, place,
attachment, witness, or dispute. A `WorkOccurrence` is required only when no planned
occurrence resolves, when the reported episode is explicitly independent of the
planned slot, or when multiple distinct episodes must be represented against one
slot. With no event assertion and only a report about the exact task revision's
resultant state, `TaskSubject` is canonical. Once a journal-native episode is
created, its identity is stable; later detail appends claims or corrections to that
`WorkOccurrence` rather than reminting it. A link from `WorkOccurrence` to a planned
slot records relation, never subject equivalence or automatic evidence precedence.

`CREATE_WORK_OCCURRENCE` atomically allocates the episode and its creation claim from
one retained `AtomicRequestEnvelope` whose operation and fingerprint bind every
immutable linkage field and whose typed result stores both IDs. The envelope,
episode, and creation claim commit atomically under one domain-scoped unique request
key. `creation_claim_id` is non-null and unique
and resolves to a claim whose subject is
`WorkOccurrenceSubject(this.work_occurrence_id)` with identical immutable optional
task/task-revision/occurrence linkage. Both IDs are deterministic under that retained
mapping; expired or unknown retry state fails closed. Later claims/dispositions may
reference the episode but never remint or rebind it.

Scheduling atomically creates a stable occurrence and its initial `PLANNED`
`TaskOccurrenceRevision`, captures the exact source snapshot, and appends one
immutable `PLAN_EXPECTED` seed. Its unique key is:

```text
PlanSubject × exact_task_revision_id × expectation_key
```

`expectation_key` is a semantic slot declared by the plan schema, such as
`OCCURRENCE_AT`, `COMPLETION_BY`, or a stable named outcome; it is not caller-chosen
free text. Exactly one seed may occupy each key for a subject and exact revision.
The originating command deterministically derives stable `expectation_id` from that
unique tuple, so transaction retries or fresh client IDs cannot duplicate it.
`TaskIdentitySubject.task_id` must equal the referenced `TaskRevision.task_id`; for an
occurrence subject, `TaskOccurrence.task_id` must equal that revision's task. The
seed transaction validates this same-task invariant atomically.
`TaskOccurrence.scheduled_task_revision_id` is immutable provenance and can never be
rebound, including to a later revision of the same task. A later revision may create
a new occurrence and seed, or separately authorize a future attempt through the
explicit compatibility contract, but it cannot change the old occurrence's subject,
expectation, claims, or scheduling provenance.
Several seeds of different declared keys may coexist. Later task revisions append
new seeds. Old seeds remain historical and non-current in projection; they are never
edited to match the new plan.

Cancellations and skips retain occurrence identity and append a new exact
`TaskOccurrenceRevision`. Reschedule is one atomic command: it CAS-targets the
predecessor revision, appends its designated terminal/non-actionable successor, and
creates exactly one replacement occurrence with `RESCHEDULED_FROM` under one
`commit_seq`; stale state or an existing replacement rejects the whole transaction.
Every reschedule, recreate, split, or merge has one stable
`OccurrenceTransformation` envelope. Its fingerprint covers the operation,
canonical ordered source pairs, every fully specified result, and the exact active
`OccurrenceTransformationPolicyRevision`. That policy defines operation-specific
source compatibility and result provenance across task revisions, due semantics,
series membership, recurrence-rule revision, recurrence keys, and initial states.
The policy defines valid all-or-none recurrence fields; the transaction proves the
rule belongs to the series and governs the key. Missing/unknown policy or
an incompatible source/result set rejects. Every result binds to the same task,
and recurrence uniqueness is checked atomically. One atomic command CASes every source revision, appends each required
terminal/non-actionable source successor, creates the entire result set and lineage,
and retains the result for identical retry. Reschedule/recreate are one source to one
result; split is one source to its declared nonempty complete result set; merge is a
canonical set of at least two sources to one result. A concurrent overlapping
transformation loses a source CAS; an identical retry returns the retained graph;
any different later transformation must explicitly target the already transformed
current revisions. Retry of an external action creates an attempt, not
an occurrence.
A recurring `TaskSeries` is distinct from its occurrences.

Lineage is validated and attached only inside the atomic occurrence-creation command
and is immutable thereafter. Predecessors are distinct, existing, lower-
`creation_commit_seq`, and from the same `task_id`. `RESCHEDULED_FROM` and
`RECREATED_FROM` require exactly one predecessor; `SPLIT_FROM` requires one source;
`MERGED_FROM` requires at least two mutually compatible sources. Duplicate IDs,
later lineage append/replacement, or cross-task ancestry are rejected. Lineage never
transfers subject, authority, acceptance, satisfaction, or closure.

### P0-04 — recurrence

A series expands lazily in a rolling horizon. Recurrence binding is a tagged,
disjoint uniqueness contract:

```text
GENERATED: (security_domain_id, series_id, logical_recurrence_key)
EXCEPTION: (security_domain_id, series_id, recurrence_exception_key)
```

The tag participates in the key. Both branches also check the shared coordinate and
future-slot index for semantic collision; an original source key remains exclusively
owned by its tombstone disposition and can never be claimed by an exception.

`TaskSeries` is identity plus rebuildable current-head cache. Every normative series
field—active rule, horizon, revision token, key/timezone/calendar/fold policy and
state—lives only in append-only `TaskSeriesRevision`. Expansion, rule edit, and
migration bind and CAS the exact series revision and append its successor; historical
materialization/replay never reads mutable series scalars.

`logical_recurrence_key` is produced by the series' versioned deterministic key
policy from the immutable series-lifetime coordinate contract: calendar system,
canonical timezone, and explicit DST fold/gap policy. `RecurrenceCoordinate`, not
the encoded key bytes, is the total-order authority for horizons and rule intervals;
the key is only its versioned identity encoding. Changing timezone, calendar, or key
policy requires an explicit atomic series migration that maps every coordinate and
disposition, proves a bijection or tombstones ambiguity, and switches one series
revision token; otherwise such edits reject. The rule revision is immutable
provenance, never identity. Rule revisions own
the rule revision is immutable provenance, never identity. Rule revisions own
non-overlapping, gap-free half-open coordinate intervals. A rule-edit transaction
CASes the exact `TaskSeriesRevision` head containing the active-rule head, expansion
horizon, and shared series revision token
in one serializable transaction, re-scans the authoritative disposition set at that
snapshot, validates the partition, and classifies all already-materialized
series-wide coordinates; it cannot
recreate, rewrite, or change ownership of a materialized, skipped, cancelled, or
rescheduled key. Missing/unknown key or timezone policy fails closed.

Expansion atomically CAS-checks the exact `TaskSeriesRevision` and its
`active_rule_revision_id`, prior horizon, and series revision token,
deterministically enumerates the complete half-open interval, inserts every missing
series-wide key, and advances the horizon monotonically in the same transaction. A
uniqueness race must reconcile to the identical complete interval or abort; partial
materialization is never visible. Rule edits govern only unmaterialized future keys.
Materialized occurrences require explicit planning commands.

Every materialized source slot permanently owns a `RecurrenceSlotDisposition`.
Skip/cancel retains that disposition; reschedule/split/merge/recreate changes it to a
transformed tombstone, so expansion can never rematerialize the source key. Results
are non-series exceptions by default. A transformation policy may allow at most one
result to inherit series ownership through a distinct versioned exception-key
namespace; it may never reuse a source key or collide with a genuine future slot.
Split's remaining results and merge results are non-series unless the exact policy
selects that single inheritor. Expansion consults all dispositions and exception
keys atomically. Rescheduling onto an existing slot, transforming a skipped slot, or
any transformation/expansion race that would duplicate a coordinate, generated key,
exception key, or ownership disposition rejects the entire command. The tagged
binding is persisted on both occurrence and disposition and indexed with
domain-scoped uniqueness over generated and exception namespaces. Transformation
creation and expansion CAS/check that durable index atomically; rebuild/replay uses
the binding rather than re-deriving ownership. Concurrent transform/expansion therefore either preserve both distinct identities
without key reuse or reject the whole command.

### P0-07 — hard acyclicity and late evidence

Claim correction/supersession and occurrence lineage edges are typed, later-to-
earlier, and acyclic. Every claim edge must target a lower immutable
`stable_append_seq`; every occurrence-lineage edge must stay in one security domain
and target an occurrence whose
immutable `creation_commit_seq` is lower than the new occurrence's. The append transaction
checks existence, edge type, and ordinal inequality before commit, which makes a
cycle unrepresentable. Late evidence stays on its original subject even after
replanning. Later plans can reference history but cannot create, delete, re-subject,
or redefine historical claims or occurrences.

The narrative journal is a disposable projection over immutable claims. It may show
planning context, but current planning is read from planning commits and never
computed from past journal assertions.

### P0-08 — authority, acceptance, and node scope

Authority grants and acceptance satisfactions are immutable, versioned planning
records bound to exact revisions, actors, scopes, bounds, and policies. Claims that
acceptance, release, or completion occurred are evidence only. Operative authority or
reliance changes only through a new authorized command and revision.

A validator resolves the exact `AuthorityGrant`, `Delegation`,
`AcceptanceRequirement`, and `AcceptanceSatisfaction` records referenced by the
command. Effective authority is the intersection of grant scope, delegation scope,
bounds, exact revision, counterparty acceptance, and policy version; an absent,
stale, or non-intersecting record fails closed. Close/delegate commands persist the
consumed record IDs in the corresponding typed
`PlanningCommandAudit.consumed_*_ids` fields; the command variant binds the exact
predecessor and result revisions.

Grant issue, amendment, and revocation CAS the current `grant_family_id` head and
prove issuer meta-authority. Only the greatest committed `ACTIVE` head contributes;
`REVOKED`/`EXPIRED` heads do not. `CREATE_TASK` instead consumes a separate bounded
bootstrap authority identified by `bootstrap_scope_id`; authority is never inferred
from the revision it creates. Every transition explicitly reissues, expires, or
declines to carry each grant—there is no implicit carry-forward. Consumed grant,
delegation, requirement, and satisfaction IDs are typed fields of the command audit,
not opaque prose.

Acceptance validation proves that `accepting_actor_id` is the named counterparty or
its explicitly authorized delegate, authenticates the command, and CAS-checks the
still-open exact requirement-family head. Requirement, satisfaction, and delegation
changes append immutable family successors under head CAS; only the greatest
committed head with effective `OPEN`/`ACTIVE` status contributes. Withdrawn,
revoked, expired, superseded, or stale records contribute nothing. Close, delegate,
and dispatch validation CAS-check the exact active heads, so a winning withdrawal or
revocation rejects the stale operation; an already-linearized dispatch remains
visible rather than retroactively undone. A versioned derivation policy enumerates the
complete affected-counterparty set for a proposed change; reliance remains held
unless each derived requirement has one valid non-superseded satisfaction.

An arrangement is explicitly a typed subset of commitments: it has stable identity
and every arrangement revision binds one exact `CommitmentRevision`; parties,
promised scopes, and lifecycle derive only from that commitment revision, while the
arrangement adds acceptance mode and reliance-policy head. Every counterparty or
lifecycle change atomically CASes both heads, writes one aligned pair, and every
authority-consuming command persists both exact heads; mixed-head reads reject.
`AffectedCounterpartyPolicyRevision` converts only registered observable
inputs into a revisioned `RelianceAssessment`. Context such as a human-sounding
title, invited participants, or a meeting link may append non-operative
`SUSPECTED`; it never creates authority or obligation. Only an authenticated accepted
planning command may confirm, reject, withdraw, create, change, or release a
commitment. `SUSPECTED`/`DISPUTED` has a clarification deadline and authorized
resolver path and fail-closes only the prejudicial affected change, not unrelated
planning. Counterparty-set changes CAS the arrangement head and preserve accrued
party-specific obligations rather than silently transferring them.
Each `(arrangement_id, party_id)` has one `RelianceAssessmentLineage` selecting
exactly one current `RelianceAssessmentFamily`; historical predecessor-linked
families remain immutable. Assess, confirm, reject, withdraw, and expiry commands
target only that selected family and CAS its exact current head together with the
lineage, arrangement, and commitment heads. Only the greatest committed assessment
is effective, so confirmation/withdrawal cannot both win.
Legal assessment edges are `NONE→SUSPECTED|CONFIRMED|REJECTED`,
`SUSPECTED→CONFIRMED|DISPUTED|REJECTED|EXPIRED|WITHDRAWN`, and
`DISPUTED→CONFIRMED|REJECTED|EXPIRED|WITHDRAWN`. `CONFIRMED`, `REJECTED`, `EXPIRED`,
and `WITHDRAWN` are terminal for that assessment family; later contextual evidence
starts a new family by CASing the per-party `RelianceAssessmentLineage`, linking the
predecessor family, and atomically creating its initial assessment. Authority reads
only the lineage's exact current family/head pair, so concurrent replacement-family
starts have one winner. Deadline
processing appends `EXPIRED` by exact family-head CAS, so confirmation/expiry races
have one winner.

Child, sibling, occurrence, or delegate evidence never discharges a parent. Parent
closure requires a parent-scoped planning command, exact revision, valid authority,
and satisfaction policy. Suspected reliance triggers clarification and may hold a
prejudicial external change; it never creates or releases an obligation.

### P0-09 — task transitions, outcome projection, and Mark done

Every task or occurrence transition audit records the exact active
`TaskTransitionPolicyRevision`. Its closed command-indexed graph defines every legal
predecessor/result pair, terminal state, explicit reopen/replan edge, and
transformation source-terminalization edge; any unlisted edge rejects. Terminal
`CLOSED`, `CANCELLED`, and `SKIPPED` states have no ordinary successor; only a
registered reopen/replan command may take its exact policy-listed edge.

Task presentation is a read-only Cartesian projection:

```text
TaskView = planning_state(exact current TaskRevision or TaskOccurrenceRevision)
         × epistemic_outcome(current FactClaims for an explicitly named exact
                             outcome subject and key)
         × deadline_projection(exact open revision or occurrence, evaluation instant)
```

`epistemic_outcome` is `UNKNOWN | REPORTED_DONE | REPORTED_NOT_DONE | PARTIAL |
DISPUTED`; it is not stored in `TaskRevision`. No cell transition on one axis mutates
another. `DUE` and `OVERDUE` are deterministic view labels, not task transitions or
evidence. They require the exact currently open revision or occurrence, its immutable
`DueSpec`, and an explicit evaluation instant. `DueSpec` retains the canonical
instant, original wall-time/date-boundary input, IANA zone, pinned timezone-rule
revision, fold/gap decision, and due-boundary-policy revision. `DUE` applies at the due
instant under the declared boundary policy and `OVERDUE` after it. Closed, cancelled,
skipped, or superseded identities have neither label. Deadline arrival never implies
completion, noncompletion, attempt, cancellation, satisfaction, or any command.

For an occurrence, the only deadline authority is its immutable
`scheduled_due_spec`; task revisions, series rules, and ambient timezone cannot
supply or override it. Reschedule creates a new occurrence with a new explicit due
specification.

The view always exposes its `outcome_subject`; it never silently rebinds claims to a
new current revision. For a close transition, the closed card may explicitly present
claims scoped to the exact predecessor revision that the close command targeted.
That presentation relation is not evidence or authority for the close. Reopen/replan
creates a new current revision whose default outcome subject is itself and therefore
starts `UNKNOWN`; the historical closed card keeps its predecessor-scoped outcome.

Delegation is an orthogonal revisioned authority axis, never a lifecycle state.
Multiple disjoint active delegation families may bind an exact task or occurrence
scope. Each subject has one revisioned `DelegationSet`; every create, amend,
withdraw, revoke, and authority-consuming command CASes that shared set head and
records the exact set revision consumed. Overlap and coverage are computed inside
the same serializable transaction. Conflicting additions reject or enter the set as
non-operative `HELD`, never transient active authority. A pinned `DelegationCoveragePolicyRevision` derives
`delegation_projection = NONE | PARTIAL | FULL | DISPUTED | HELD` from the greatest
active heads. Delegate/withdraw commands CAS their exact delegation-family,
coverage-policy, and shared delegation-set heads and persist every consumed head;
they do not change lifecycle state unless a separate
authorized lifecycle command commits its own revision. Withdrawing one scope leaves
other disjoint scopes active. Occurrence-scoped delegation records its exact
occurrence revision in the delegated scope/provenance.

Every operative delegation has its own requirement/satisfaction family binding the
delegation family, exact delegation head, delegate, exact task-or-occurrence subject,
accepted scope/bounds, and policy head. It remains non-operative until that exact
active acceptance exists. Amendment, withdrawal, revocation, or counterparty
substitution advances the shared delegation-set head and invalidates stale
acceptance; acceptance and mutation races serialize on those exact heads.

| Presented term | Authoritative contract |
|---|---|
| `PLANNED` | Current exact `TaskRevision.planning_state`; entered only by an authorized create/reopen/replan command. |
| `DUE` / `OVERDUE` | Clock-scoped view of an exact open revision/occurrence; never persisted as completion evidence. |
| `IN_PROGRESS` | New exact revision from an authorized start/resume command; no outcome inference. |
| `PARTIAL` | Epistemic projection from admissible typed claims; planning remains whatever its current exact revision says. |
| `BLOCKED` | New exact revision from an authorized block command naming reason/policy; no claim is generated implicitly. |
| `DELEGATION: NONE/PARTIAL/FULL/DISPUTED/HELD` | Orthogonal projection of exact-scope active delegation heads under the pinned coverage policy; delegate evidence does not close delegator or parent. |
| `CLOSED` | New exact revision from an authorized close command CAS-targeting the prior open revision. |
| `CANCELLED` | New exact revision from an authorized cancel command; does not assert an outcome. |
| `SKIPPED` | New exact occurrence/task revision from an authorized skip command; does not close its series. |
| `DISPUTED` | Epistemic projection of equal-strength unresolved claims; never a planning transition. |

Ordinary **Mark done** is one idempotent request envelope with two atomic visibility
writes: (a) a user-reported `RESULTANT_STATE` outcome `FactClaim` on the exact
`TaskSubject` with `claim_type=USER_REPORTED_OUTCOME`,
`semantic_slot=COMPLETION`, `value=DONE`, and no `occurred_at`, and (b) an independently
authorized `CLOSE_TASK` command CAS-targeting the exact open revision and producing
its `CLOSED` successor. Adoption requires the envelope, both records, and dedupe
mapping to commit in one serializable transaction/commit log; alternatively the one
atomically committed envelope is the sole visibility authority and both records are
deterministic projections from it. If either write or validation fails, neither
becomes visible.
This atomicity is not semantic causality: neither record validates, authorizes,
derives, or implies the other. The composite envelope's own validation may reject the
entire uncommitted request, but no journal claim or projection blocks or changes an
accepted planning state. A standalone authorized `CLOSE_TASK` remains available and
commits without creating or validating a claim; conversely a standalone outcome
claim never closes the task. Claim allocation uses `stable_append_seq`; planning
uses `commit_seq`. The request ID deduplicates both writes only while its immutable
mapping to the committed envelope/result remains authoritative. The default contract
retains that mapping permanently; an implementation with bounded retention must
declare the bound and fail closed on an expired or unknown request ID rather than
replay. This preserves the records' independent identities and policies. Correcting
or retracting the predecessor-
scoped claim changes only that closed card's `epistemic_outcome`; the task remains
`CLOSED`. Reopening requires a separately
authorized command against the exact current closed revision.

`(tenant_or_principal_id, request_id)` is unique. Its canonical fingerprint covers
all semantic/auth inputs and policy versions. First insert and visible records commit
atomically; an identical concurrent/replayed fingerprint waits for or returns the
committed result, while a mismatch is rejected. An aborted envelope is atomically
absent or follows an explicit safe retry transition. `CREATE_WORK_OCCURRENCE` applies
the same uniqueness, fingerprint-equality, concurrent-wait, and abort rules.

An occurrence close/skip never closes its `TaskSeries`. A child close never closes
its parent. A delegate's completion report never closes the delegator obligation.
Each broader closure requires its own exact-scope authorized command and satisfaction
policy. A completion-state claim about an existing task uses
`TaskSubject(task_id, task_revision_id)`. If the same request supplies concrete event
details, its `EVENT` claim uses the `OccurrenceSubject` when exactly one planned
occurrence resolves. Only an unresolved, explicitly independent, or additional
episode creates or resolves a `WorkOccurrenceSubject`. A typed `EVIDENCES` relation
may link distinct event and task-state claims but never merges their identities; the
episode neither authorizes nor confirms closure. Truly ad-hoc work with no task uses
a journal-native `WorkOccurrence` and never manufactures retroactive plan history.

### P0-10 — evidence mapping

Evidence confirms only the registered claim type in its cell. Outcome interpretation
requires a separate versioned claim-mapping and admissibility policy; absent policy
projects `UNKNOWN` or `DISPUTED`, never a stronger meaning.

| Source/evidence | May directly confirm | Must not directly infer |
|---|---|---|
| Deadline clock | `DUE`/`OVERDUE` view inputs only | attempt, done/not done, cancellation, satisfaction |
| Provider checkbox | provider checkbox state on exact provider item | real-world outcome, task/parent/series closure |
| Provider receipt | exact dispatch/execution receipt level | next receipt level, human receipt, satisfaction |
| User report | typed user-reported outcome on exact subject | objective truth or automatic reopen/close beyond its separate command |
| Delegate report | typed delegate-reported outcome on delegated subject | delegator discharge, parent closure, counterparty acceptance |
| Sensor | registered measurement/event claim within calibration policy | intent, obligation satisfaction, broader task outcome |
| Counterparty | typed receipt/acceptance/satisfaction claim within exact scope | authority change or closure without a planning command |

### Core natural-language interaction contract

Natural language is the control surface, not a bypass around authority. Each
utterance follows `INTERPRETED→PROPOSED→CONFIRMED→COMMITTED` or ends
`REJECTED/CANCELLED`; a pure factual report may commit only its claim append after
subject/meaning validation and never a planning mutation. Every inferred or defaulted
field is labeled with source and confidence and is never treated as user intent or
authority. Ambiguity in subject, scope, time, audience, obligation, recurrence,
delegation, proactivity cadence, or external effect requires clarification.

Before authorization, a proposal displays exact base revisions, planning deltas,
external-effect previews, inferred/defaulted fields, expiry, and cancellation path.
Stale bases invalidate it. A user can express or revise proactivity entirely through
natural language—for example a recurring Sunday-evening planning proposal—but the
rule controls only when Chiplog proposes and never grants permission to commit or
dispatch. Authorization is per generated proposal through a separate current
interaction/command. Any future bounded auto-execution capability must be a distinct
revisioned authority family with explicit scope, expiry, revocation, and race tests;
it cannot be implied by a proactivity-rule utterance. Post-commit
“correction” routes by meaning: factual correction appends a disposition; plan
correction creates a new proposed command; reopening/closing remains separate.

A persisted proactivity cadence is an append-only `ProactivityRule` family. Its
revision binds owner/domain, cadence and timezone policy, bounded proposal scope,
allowed defaults, and `ACTIVE|PAUSED|CANCELLED|EXPIRED` status under predecessor CAS.
It has no commit/dispatch authority. Every generated proposal records the exact rule
revision and exact plan bases. Generation CAS-checks the current active rule head;
pause/cancel/amendment winning first prevents stale generation or authorization.
Even a valid generated proposal requires a separate explicit bounded authorization
before commit or dispatch.

`NaturalLanguageInteraction.committed_result` is a closed union. `COMMITTED` requires
exactly one result variant matching the classified action: fact-only binds its claim
command and claims, planning-only binds its planning command and revision members,
and an explicitly authorized atomic composite binds both. Non-committed states have
no result. Retries return the identical union value; later correction follows the
bound claim or planning IDs rather than an implicit lookup.

## Core P1 strengthening contracts

### P1-01 — typed graph and delegation

`STRUCTURAL_PARENT` is the sole ownership edge; it forms a DAG and conveys no
authority. `DERIVED_FROM`, `EVIDENCES`, `SUPERSEDES`, and `VARIANT_OF` are non-owning.
Moving operative responsibility creates a new node with provenance.

Structural ownership has one closed lifetime model: an active
`StructuralParentEdge` links `child_task_id → parent_task_id` at task-identity level.
Only task-to-task endpoints are structural; occurrence and claim relations use the
non-owning relation types. Edge status changes append a
`StructuralParentEdgeRevision` and CAS its exact head. Creating a new task revision
neither deletes, duplicates, nor silently copies the edge. A parent-scoped command
resolves every active edge and the exact current child and parent revisions inside
its transaction; closing a child never closes its parent, and parent closure must
explicitly validate its own current revision and registered child policy.

`NodeRelation` validates relation-specific typed endpoints and exact revision
bindings at append; `STRUCTURAL_PARENT` is materialized only through the dedicated
identity-level edge family above. Every structural-edge activation performs a transactional DAG
check. Claim replacement is represented only by `ClaimDisposition`; a
`SUPERSEDES` relation is its derived graph view, never a second authority. Claim
relations use `stable_append_seq`; planning relations use `commit_seq`, and neither
may point to an equal/newer ordinal in an acyclic relation class.

Delegation authority, delegate acceptance, and every affected counterparty's
substitution acceptance are separate exact-revision planning records. Their effective
scope is the intersection. Delegate performance is a claim about its subject, not
automatic parent discharge.

### P1-02 — narrative grammar

`NarrativeEpistemicStatus` is
`UNCONFIRMED | CONFIRMED | DISPUTED | CORRECTED | RETRACTED |
UNRESOLVED_POLICY | DELETED_UNAVAILABLE`. Assertion status maps to the first two;
disposition reduction maps to the next four; a redacted dependency maps only to
`DELETED_UNAVAILABLE`. `NarrativeView` orders by `occurred_at` when known, then
`observed_at`, then `stable_append_seq`; it shows both times when different and keeps
incomparable branches separately labeled rather than flattening them. Capability
filtering precedes composition. A view ID can never address a planning command.

Every rendered branch identifies `PLAN`, `FACT CLAIM`, or `PROPOSED PLANNING
CHANGE`; gives a human-readable exact subject and whether its scope is historical or
current; names source; explains what `CONFIRMED` confirms and explicitly does not
confirm; shows occurred/observed time and uncertainty; exposes correction,
retraction, supersession and dispute lineage; and states whether planning remained
unchanged. It presents only policy-permitted next actions—`Correct report`,
`Retract`, `Dispute`, `Propose plan change`, `Authorize`, or `Cancel proposal`—and
explains unavailable actions. Correcting “done” never implies reopen; reopening is a
separate displayed proposal. Conflicting provider/user evidence stays visibly
separate from plan state.

## Policy dependency registry

Mechanisms above are fixed only within this hypothesis. A dependent operation must
name the policy ID/version below; unresolved or missing policy takes the listed
fail-closed path.

| Policy ID | Status | Used by | Missing/unknown behavior | Adoption blocker |
|---|---|---|---|---|
| `CLAIM_AUTHORITY` | EXTERNAL INPUT | Claim/disposition append | Reject authority effect; retain unauthorized input only for bounded audit | Yes |
| `CLAIM_MAPPING` | UNRESOLVED | Evidence→outcome projection | `UNKNOWN`/`DISPUTED`; no planning effect | Yes |
| `DISPOSITION_PRECEDENCE` | UNRESOLVED | Correction/retraction reduction | `UNRESOLVED_POLICY`; remove no contribution | Yes |
| `AUTHORITY_COMPATIBILITY` | UNRESOLVED | Invocation/dispatch | `HOLD` | Yes |
| `SATISFACTION` | UNRESOLVED | Parent/delegator/obligation close | `HOLD`; no discharge | Yes |
| `DEADLINE_BOUNDARY` | UNRESOLVED | DUE/OVERDUE view | Omit label; no inferred outcome | No, if deadline view omitted |
| `RECURRENCE_KEY` | EXTERNAL INPUT | Rule edit/expansion | `HOLD`; materialize nothing | Yes for recurrence |
| `RETENTION` | EXTERNAL INPUT | Every controlled data class | `DELETION_PENDING`; no unsafe expiry/promotion | Yes |
| `EXTERNAL_DISCLOSURE` | EXTERNAL INPUT | External availability/receipts | Deny disclosure | No, if external access omitted |
| `SECURITY_DOMAIN_AUTH` | EXTERNAL INPUT | Domain isolation, authentication, credential fencing | Reject/hold protected operation | Yes |
| `AUDIT_AND_INGRESS_TRUST` | EXTERNAL INPUT | Audit keys/checkpoints, provider callback verification | Quarantine input; block promotion when continuity is unknown | Yes |
| `SUPPLY_CHAIN_AND_ABUSE` | EXTERNAL INPUT | Roles, attestations, quotas, safe degradation | Reject promotion or hold workload | Yes |

## Closest prior art and compatibility boundary

This hypothesis makes no novelty claim. Its nearest established patterns are:

- [CQRS](https://martinfowler.com/bliki/CQRS.html), for separating update and read
  models. Here the sharper domain boundary is normative planning versus epistemic
  claims; the added complexity warning applies directly.
- [Event Sourcing](https://martinfowler.com/eaaDev/EventSourcing.html), for immutable
  history and rebuildable projections. Unlike conventional event sourcing, journal
  claims are deliberately not the event source of planning state.
- The official Azure [Transactional Outbox pattern](https://learn.microsoft.com/en-us/azure/architecture/databases/guide/transactional-out-box-cosmos)
  and [duplicate-delivery guidance](https://learn.microsoft.com/en-us/azure/service-bus-messaging/service-bus-message-loss-and-duplicates).
  A durable outbox is at-least-once delivery machinery, not exactly-once external
  effect; this hypothesis claims at most one authorization and preserves ambiguity.
- AWS EC2 [idempotency-token semantics](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-idempotency.html),
  which likewise require same-parameter reuse and defined scope. This hypothesis is
  intentionally stricter after an unknown retention window.
- [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545.html), especially `UID`,
  `RECURRENCE-ID`, recurrence-set generation, and DATE-TIME interpretation. Verified
  [Errata 4271](https://www.rfc-editor.org/errata/eid4271) distinguishes invalid dates
  from nonexistent or repeated local times. `RECURRENCE_KEY` remains an external
  input; RRULE interoperability requires an explicit mapping to those rules and DST
  gap/fold conformance tests rather than assuming equivalence.

<!-- Copied payload ends above; see MIGRATION-MANIFEST.md. -->
