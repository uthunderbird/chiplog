# Plan / Fact / Journal — operational adoption

> Status: proposed and not operationally adopted. Authority and provenance are defined in [README.md](README.md).

## Adoption-assurance annex C — operational adoption gate

This architecture file does not invent environment-specific scale or reliability
numbers. Adoption and rollout are forbidden until one exact, approved
`OperationalAdoptionProfile` supplies and verifies all items below. Missing, stale,
or partially validated profiles yield `HOLD_ADOPTION`; they are not waived by a
successful logical conformance suite.

Profile revisions are immutable and content-addressed. Their closed evidence
manifest enumerates every required item in the four classes below. Completeness
evaluates exactly one effective authorized head per manifest item: every head must be
an unexpired `PASS`. Prior `PASS`/`FAIL` results remain digest-covered history but do
not contribute after a valid successor becomes the item head. Freshness is computed
from the manifest rule, observation time, required-input versions, evaluator-policy
head, evaluator-authority head/window, and approval window—not accepted from
`valid_until` alone. An `OperationalApproval` is bound to the exact profile revision,
target release/environment, workload envelope hash, policy versions, and sealed
evidence set. Missing, failed, expired, revoked, superseded, mismatched, or
unapproved inputs deterministically yield `HOLD_ADOPTION`.

Evidence results append under a monotone per-profile frontier and revisioned
manifest-item heads. Approval may reference only a `SEALED` `OperationalEvidenceSet`
whose digest covers every item head and every result through its frontier. Sealing
CAS-checks the open set/frontier; later evidence opens a successor set rather than
mutating the sealed snapshot. Promotion atomically checks the exact sealed set ID,
frontier, digest, profile head, and approval. A concurrent/new result therefore
invalidates current promotion eligibility until a successor set is sealed and
approved.

The profile stores the authoritative current evidence frontier. Appending any result
CAS-increments it and advances that manifest item's head. Sealing CAS-checks the same
profile frontier; promotion CAS-checks
`profile.current_evidence_frontier_seq == approval.evidence_frontier_seq` and the
current sealed set ID. Thus any successor evidence makes an older approval
ineligible even when its internal digest remains valid.

The required manifest is the exact closed obligation catalog below; omission,
duplication, or an unknown numeric code rejects profile creation. Each item binds a canonical
acceptance predicate, required inputs, authorized evaluator policy, and freshness
rule.

```text
001–006 MIGRATION: INVENTORY_MAPPING, COMPATIBILITY_MATRIX,
        BACKFILL_RECONCILIATION, SINGLE_AUTHORITY_PHASES,
        COHORT_ABORT_GATES, ROLLBACK_BOUNDARY
101–109 OPERATIONS RUNBOOKS: HELD_ATTEMPT, AMBIGUOUS_EFFECT,
        UNRESOLVED_POLICY, DELETION_PENDING, QUARANTINED_OUTPUT, STALE_RETRY,
        MANUAL_REPEAT_REQUEST, COMPENSATION_REQUEST, RECOVERY_REJECTED
110–111 OPERATIONS: QUEUE_OWNERSHIP, FOUR_EYES_OVERRIDE
201–205 RELIABILITY: SLI_SLO_ERROR_BUDGETS, RPO_RTO, INVARIANT_MONITORS,
        PRIVACY_SAFE_TRACING, FAULT_AND_RESTORE_DRILLS
301–306 SCALE: WORKLOAD_ENVELOPE, PARTITION_ORDERING, BATCH_GRAPH_BOUNDS,
        COST_MODEL, TARGET_OVERLOAD_BENCHMARK, SAFE_BACKPRESSURE
401–407 SECURITY: TENANT_ISOLATION, AUTH_SESSION_FENCING, AUTH_REPLAY_GUARD,
        AUDIT_CONTINUITY, PROVIDER_INGRESS, SUPPLY_CHAIN_ROLE_SEPARATION,
        RESOURCE_ABUSE_ISOLATION
```

`OAP_DIGEST_V2` is SHA-256 over an RFC 8785 JCS canonical JSON object with exactly
these top-level keys: `domain`, `evidence_frontier_seq`, `items`,
`profile_content_hash`, `profile_revision_id`,
`separation_of_duties_policy_revision_id`, and `separation_of_duties_digest`.
`domain` is `chiplog-oap-evidence-v2`; sequence integers are unsigned decimal strings; timestamps
are UTC RFC 3339 strings with exactly nine fractional digits; digests are lowercase
64-character hex strings; null is JSON null; enum values are the exact uppercase
schema tokens.

`items` is sorted by three-digit `obligation_code`. Each item object contains exactly
`manifest_item_id`, `obligation_code`, `predicate_head_id`,
`required_input_schema_digests`, `evaluator_policy_head_id`,
`freshness_rule_digest`, and `results`. Digest arrays are lexicographically sorted.
`results` includes the complete history through the frontier, sorted numerically by
`evidence_seq` then bytewise by `evidence_id`. Each result object contains exactly
`artifact_digest`, `evaluator_authority_head_id`, `evaluator_id`,
`evaluator_principal_family_head_id`, `evaluator_independence_domain`,
`evaluator_policy_head_id`, `evidence_id`, `evidence_seq`, `evidence_type`,
`manifest_item_id`, `observed_at`, `predecessor_item_result_id`, `predicate_head_id`,
`predicate_output_digest`, `required_input_digests`, `result`, and `valid_until`.
All structured inputs enter only via their listed digests. Extra/missing keys,
duplicate codes/sequences, noncanonical values, or unknown versions yield
`HOLD_ADOPTION`.

`separation_of_duties_digest` is SHA-256 over RFC 8785 JCS with domain
`chiplog-oap-sod-v1` and exactly: the separation-policy revision ID; profile author
principal-family head; lexicographically sorted artifact-producer principal-family
heads; and, for every result in the same item/result order above, obligation code,
evidence ID, evaluator ID, evaluator principal-family head, evaluator independence
domain, evaluator authority head, and evaluator-policy head. Changing any included
role, head, domain, policy, or result changes both this digest and the enclosing
`OAP_DIGEST_V2`. Approval additionally binds its approver principal-family and
authority heads and validates their independence from every digest contributor;
deployment revalidates all current heads.

Serialization test vector (format-only; an empty manifest is not adoption-valid):

```json
{"domain":"chiplog-oap-evidence-v2","evidence_frontier_seq":"0","items":[],"profile_content_hash":"0000000000000000000000000000000000000000000000000000000000000000","profile_revision_id":"p0","separation_of_duties_digest":"0000000000000000000000000000000000000000000000000000000000000000","separation_of_duties_policy_revision_id":"sod0"}
```

Its SHA-256 is
`64a5542e11feb759c336bb8b145f456ce74b6793fb73b11bf4cdc8c218000c90`
(`printf … | shasum -a 256` over the exact one-line JSON above).

Sealing and promotion re-evaluate each effective head's predicate result, required-
input digests, evaluator-policy head, evaluator-authority `ACTIVE` head/scope/window,
and freshness. Approval/revocation/supersession append immutable approval-family
successors under CAS; approver authority does likewise. Promotion CAS-checks the
profile's exact current approval/evidence heads and current approver-authority head.
A concurrent revocation, scope change, evidence successor, or approval successor
wins the CAS and makes stale promotion hold.

Each manifest item's raw head is always its greatest committed `evidence_seq`.
Every mutation of an `OperationalAcceptancePredicate`, `OperationalEvaluatorPolicy`,
or `OperationalEvaluatorAuthority` family CAS-targets its exact current head; only
the greatest committed effective head contributes. Concurrent forks and stale
activation/amendment after revocation are rejected.
Appending a result CAS-targets that raw head and atomically validates the exact
`ACTIVE` predicate, evaluator-policy, and evaluator-authority family heads, their
scope, required input digests, and validity window. An invalid, expired, or `FAIL`
raw head makes the item incomplete; projection never skips backward to an older
PASS. Recovery appends a newly authorized PASS successor against the failed/expired
raw head, so history is retained without deadlock. Later revocation or policy-head
change invalidates the result at sealing/promotion until such a successor exists.

The profile's exact separation-of-duties policy is enforced at evidence append,
seal, approval, and every deployment transition. For each obligation, evaluator is
independent of profile/change author and relevant artifact producer; approver is
independent of every contributing evaluator; promoter is independent of author,
producer, evaluator, and approver. Principal-family heads and independence-domain
attributes enter the sealed evidence-set digest and approval. Role aliasing or an
authority/identity change invalidates the gate. Any emergency exception is a
separately authorized, time-bounded, independently evidenced profile input, never an
implicit waiver.

### Migration, compatibility, rollout, and rollback

The profile inventories every legacy entity, event, API, and consumer and gives a
deterministic legacy→new mapping, including explicit `QUARANTINE` treatment for
ambiguous records. It contains API/event/schema compatibility matrices, backfill and
reconciliation invariants, and staged shadow/dual-read/write cohorts. Every phase
names exactly one normative planning authority; dual-authority operation is
forbidden. Cohort promotion uses observable gates and abort thresholds. Rollback
states what remains reversible after new claims, commands, dispatches, or other
irreversible effects, and tests both forward migration and bounded rollback.

`OperationalDeploymentRevision` is the sole environment-head rollout authority.
Every shadow/cohort/full advance, abort, or rollback CASes the exact environment
deployment head and revalidates current profile, evidence frontier/set, approval,
approver authority, supply-chain attestation, runtime configuration measurement, and
separation-of-duties heads. It records the cohort, observed gate, irreversible
boundary, and rollback target. Expiry, revocation, drift, supersession, or failed
evidence blocks every subsequent phase, not only initial promotion. Concurrent
rollout/rollback controllers serialize on the same environment head; stale work
enters `HOLD` and cannot advance another cohort.

### Operator ownership and stuck-state resolution

For `HOLD`, `AMBIGUOUS`, `UNRESOLVED_POLICY`, `DELETION_PENDING`, quarantine, stale
retry, manual repeat, compensation, and recovery rejection, the profile names an
owning role, durable work queue, privacy-filtered diagnostic context, permitted
commands and authority, required evidence, escalation deadline, and audited resolve,
abandon, compensate, or resume path. Duplicate-risk actions and deletion/recovery
overrides require four-eyes authorization. No ad-hoc datastore edit is a resolution
mechanism.

### Observability, reliability, and recovery

The profile sets measured SLIs/SLOs and error budgets for command/CAS conflicts,
projection lag, outbox age, ambiguous/held effects, recurrence expansion lag,
deletion propagation, quarantine growth, stale policy versions, and recovery
promotion. It declares RPO/RTO and deletion/recovery targets, invariant monitors,
alert thresholds, dashboards, on-call ownership, and privacy-compatible trace IDs
across command→revision→attempt→effect. Restore and fault-injection drills exercise
`PV-01–08` at a declared cadence and retain evidence.

### Scale, partitioning, performance, and cost

The profile declares tenants, tasks, claims, recurrence cardinality, disclosure
traffic, concurrency, and retention. It chooses consistency/partition boundaries and
uses the fixed domain-scoped `CommitOrdinal` and chooses only its storage/partition
implementation; it may not weaken within-domain ordering or compare ordinals across
domains. It bounds recurrence batches,
transaction sizes, DAG checks, storage/write amplification, egress, and operator
cost. Benchmarks record p50/p95/p99 latency, throughput, contention, failure, and
recovery at target and overload. Admission control, backpressure, and degrade modes
must preserve authority, privacy, deletion, and non-idempotent-effect invariants.

### Security and abuse evidence

The profile names the storage/encryption boundary for every security domain;
authentication and credential issuers; step-up rules; revocation/fencing latency;
audit key custody, checkpoint replica, and continuity recovery; provider callback
trust roots and reconciliation endpoints; separated human/service roles; signed
supply-chain provenance; per-domain quotas/fairness; and safe degradation. Evidence
includes cross-domain property tests, credential-revocation races at every
linearization boundary, ledger mutation/fork/rollback detection, forged/replayed/
reordered webhook trials, single-principal sensitive-chain denial, compromised-build
rejection, and noisy-neighbor/pathological-amplification tests.

<!-- Copied payload ends above; see MIGRATION-MANIFEST.md. -->
