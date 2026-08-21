# Chiplog — planning / journal split

## Status

**SOURCE FOR A CORE SELECTIVELY ADOPTED INTO THE LONG-TERM VISION — 2026-08-21.**
`VISION.md` reproduces the exact adopted product baseline and is its sole normative
source. This standalone is retained design rationale and a proposed detailed
realization. Later edits here do not alter product direction unless the baseline in
`VISION.md` is explicitly revised.

The material incorporated there originated in: `Boundary`; the two adoption
manifests; P1-01–P1-02 at product-semantic level; the Core natural-language
interaction contract; `Did not swim Tuesday`; `Swam at 10, not 8`; and the
task-specialization traces. `Delayed invocation after replanning` was excluded
because its dispatch, ambiguity, retry, and effect behavior depends on proposed
P0-05–P0-06 machinery. This list records provenance; it does not dynamically import
later standalone text.

P0-05–P0-06 implementation/effect machinery, adoption-assurance annexes A–C,
operational feasibility profiles, rollout gates, and conformance claims remain
**PROPOSED — NOT OPERATIONALLY ADOPTED** until their named external policies and an
approved `OperationalAdoptionProfile` exist. The Boundary's logical-deletion rule is
represented in the vision only at product-semantic level; deletion enforcement and
assurance remain proposed. All storage, CAS,
transaction, serialization, policy-registry, enforcement, effect, and
operational-realization clauses remain proposed even when they share a paragraph
with an outcome later incorporated into the vision. No same-context polish or terminal zero is
external verification of implementation, safety, economy, or legal compliance.

The hypothesis is layered, not bidirectional. Planning is the sole authoritative
normative state. The epistemic history preserves typed, fallible claims. A fact claim never
directly projects into, mutates, closes, cancels, or otherwise changes planning.
Evidence can affect planning only by becoming input to a new explicit authorized
planning command which passes validation and commits a new planning revision: the
command-appropriate member(s) of the closed `PlanningRevision` union. This includes
task, occurrence, series/rule, authority/grant, delegation/acceptance,
commitment/arrangement families enumerated by the proposed `PlanningRevision` union.
A claim never mutates any revision family directly. Adding another normative family
changes this proposal but has no adopted product effect until `VISION.md` is
explicitly revised; registration alone cannot expand the vision's closed vocabulary.

“Sole normative authority” concerns product intent and obligation state. Security,
deletion, audit, and operational records in the assurance annexes are control-plane
preconditions on safe execution; they do not express user intent, satisfy an
obligation, or derive a plan from a claim.

## Boundary

A plan describes an expected, desired, permitted, or obligated future. A fact claim
describes what a source asserts occurred or was observed. Planning owns current
authority, acceptance, constraints, commitments, schedules, and normative state.
The epistemic claim history owns immutable claims, receipts, observations,
corrections, disputes, and artifacts. The journal is a disposable narrative view
that combines them with labeled plan context only for presentation.

The permitted causal path is:

```text
claim → proposed planning command → validation → new planning revision
```

There is no reverse projector. No claim arrival, timestamp, confidence, provider
receipt, or narrative computation selects the current plan. No later plan can
create, delete, re-subject, or redefine a historical occurrence or claim.

Privacy deletion is the sole qualified exception to payload preservation: it may
irreversibly erase authorized semantic payload but cannot rewrite, re-subject, or
replace it with another historical assertion. The survivor is only a policy-minimal,
non-reconstructive redaction proof and ordering placeholder.

## How to read this document

The product-semantic proposal begins with the Plan/Fact boundary, the two manifests,
P1-01–P1-02, the Core natural-language interaction contract, the two named Plan/Fact
correction examples, and the task-specialization traces. Their adoption scope is
defined only by what `VISION.md` reproduces. P0-05–P0-06 and `Delayed invocation
after replanning` are proposed effect-safety machinery. Platform security, deletion,
external disclosure, and operational rollout are adoption-assurance annex contracts:
they constrain implementation without adding product-state authorities. They remain
inline so this standalone artifact is self-contained, but are not part of the
Plan/Fact ontology.

## Proposed product-semantic schema manifest

This closed manifest—not the physical extent of `Canonical schema`—defines the
standalone's proposed typed model. It records provenance for, but does not expand,
the smaller type-role baseline reproduced in `VISION.md`:

- normative identities and revisions: `Task`, `TaskRevision`, `TaskOccurrence`,
  `TaskOccurrenceRevision`, `TaskSeries`, `TaskSeriesRevision`, `RuleRevision`,
  `PlanExpectation`, `AuthorityGrant`, `AcceptanceRequirement`,
  `AcceptanceSatisfaction`, `Delegation`, `DelegationSetRevision`,
  `CommitmentRevision`, and `ArrangementRevision`;
- normative union and subjects: `PlanningRevision`, `PlanSubject`,
  `DelegationSubject`, and `AcceptanceSubject`;
- recurrence and transformation identity: `DueSpec`, `RecurrenceCoordinate`,
  `RecurrenceBinding`, `RecurrenceSlotDisposition`, `OccurrenceLineage`, and
  `OccurrenceTransformation` at the product-semantic level;
- epistemic identity and history: `ClaimSubject`, `WorkOccurrence`, `FactClaim`,
  and `ClaimDisposition`;
- conversational governance: `NaturalLanguageInteraction`,
  `NLCommittedResult`, `NaturalLanguageInterpretationRevision`,
  `PlanningProposal`, `ProactivityRule`, and `ProactivityRuleRevision`;
- reliance classification: `Commitment`, `Arrangement`, and
  `RelianceAssessment`;
- typed graph and narrative: `StructuralParentEdge`,
  `StructuralParentEdgeRevision`, `NodeRelation`, `NarrativeEpistemicStatus`, and
  `NarrativeView`, with the endpoint, lifetime, status, ordering, and presentation
  meanings specified by P1-01–P1-02.

The names, closed variants, stable identity/revision relationships, subject
boundaries, state meanings, and provenance meanings of the displayed fields on
those types are proposed here. Physical pointers and indexes, `commit_seq` allocation,
CAS/transaction strategy, deletion epochs, policy-registry records, and enforcement
algorithms are proposed realizations. Every schema type not listed above—including
security, dispatch/effect, deletion, disclosure, and operational-adoption records—is
outside this proposed semantic manifest. A reference from a listed type to a proposed policy
record pins proposed semantics or provenance; it does not adopt that record's
storage or enforcement design.

## Canonical schema

```text
SecurityScopedRecord { security_domain_id }
SecurityDomain {
  security_domain_id, tenant_id, isolation_policy_version,
  encryption_key_family_id, current_supply_chain_attestation_id?,
  current_audit_epoch_anchor_id?, state
}
CrossDomainBridge {
  bridge_id, source_domain_id, target_domain_id, bridge_control_domain_id,
  current_bridge_revision_id
}
CrossDomainBridgeRevision {
  bridge_revision_id, bridge_id, predecessor_revision_id?, exact_scope,
  purpose, authority_head_ids[], policy_version, valid_interval,
  status: ACTIVE | REVOKED | EXPIRED,
  issuer_actor_id, source_domain_approval_head_id,
  target_domain_approval_head_id,
  created_by_command_id, bridge_revision_seq
}
CrossDomainEndpointApprovalRevision {
  approval_revision_id, approval_family_id, predecessor_revision_id?,
  approving_domain_id, counterparty_domain_id, direction,
  operations, objects_and_fields, purposes, audiences, quotas, valid_interval,
  status: ACTIVE | REVOKED | EXPIRED,
  issuer_authority_head_id, created_by_security_command_id, commit_seq
}
AuthenticationContext {
  auth_context_id, security_domain_id, authenticated_subject_id,
  credential_family_id, credential_head_id,
  session_family_id, session_revision_id, principal_security_revision_id,
  auth_strength,
  issued_at, expires_at, nonce, request_digest, intended_audience,
  service_id, channel_binding, channel_binding_verification,
  principal_disable_head_id, issuer_id, issuer_authority_head_id,
  authentication_policy_head_id, canonical_context_digest,
  signature_or_mac_key_head_id, signature_or_mac,
  issuance_command_id, issuance_seq
}
AuthenticationReplayGuard {
  security_domain_id, authenticated_subject_id, intended_audience,
  nonce, request_digest, auth_context_id,
  state: RESERVED | COMMITTED | ABORTED, committed_result_digest?
}
CredentialRevision {
  credential_revision_id, credential_family_id, predecessor_revision_id?,
  principal_id, security_domain_id,
  status: ACTIVE | REVOKED | EXPIRED | COMPROMISED,
  public_key_or_secret_version, issued_at, expires_at, revocation_epoch,
  created_by_security_command_id, commit_seq
}
SessionRevision {
  session_revision_id, session_family_id, predecessor_revision_id?,
  principal_id, security_domain_id,
  status: ACTIVE | REVOKED | EXPIRED | COMPROMISED,
  credential_head_id, issued_at, expires_at, revocation_epoch,
  created_by_security_command_id, commit_seq
}
PrincipalSecurityRevision {
  principal_security_revision_id, principal_id, security_domain_id,
  predecessor_revision_id?, status: ACTIVE | DISABLED | COMPROMISED,
  revocation_epoch, created_by_security_command_id, commit_seq
}
SecurityAuditEntry {
  audit_entry_id, security_domain_id, audit_seq, previous_entry_hash,
  opaque_event_envelope_id, randomized_commitment, coarse_event_class,
  coarse_time_bucket,
  signing_key_version, entry_signature
}
SecurityAuditEncryptedSidecar {
  opaque_event_envelope_id, scope_routing_token, encrypted_semantic_event,
  encrypted_auth_context, encrypted_authorization_policy_heads,
  encrypted_object_results, encrypted_protected_linearization_id,
  scope_key_head_id
}
SecurityAuditObligation {
  audit_obligation_id, security_domain_id, opaque_randomized_linearization_token,
  opaque_event_envelope_id, randomized_commitment, coarse_event_class,
  state: PENDING | RECORDED,
  audit_entry_id?, idempotency_key
}
SecurityAuditCheckpoint {
  checkpoint_id, security_domain_id, first_seq, last_seq, merkle_root,
  previous_checkpoint_hash, signing_key_version, signature,
  nonrollback_witness_policy_revision_id,
  independent_replica_receipts[], published_at
}
SecurityAuditEpochAnchor {
  anchor_id, security_domain_id, predecessor_anchor_id?,
  first_epoch, last_epoch, prior_anchor_hash, ordered_epoch_root,
  aggregate_count, anchor_body_digest, transition_certificate_digest,
  signing_key_version, signature,
  nonrollback_witness_policy_revision_id, independent_replica_receipts[]
}
AuditFoldTransitionCertificate {
  certificate_id, security_domain_id, prior_anchor_digest,
  exact_input_epoch_start, exact_input_epoch_end,
  ordered_input_summary_digest, recomputed_aggregate_count,
  recomputed_ordered_root, output_anchor_body_digest,
  audit_quorum_policy_version, trust_evidence_bundle_digest,
  semantic_attestations[]
}
AuditNonrollbackWitnessReceipt {
  witness_id, witness_sequence, checkpoint_or_anchor_digest,
  previous_witnessed_digest, administrative_domain,
  key_custody_domain, signing_key_head_id, signature
}
AuditTrustEvidenceBundle {
  bundle_id, quorum_policy_snapshot, quorum_policy_digest,
  auditor_authority_head_proofs[], signing_key_history_proofs[],
  administrative_domain_and_custody_attributes[],
  prior_checkpoint_publication_inclusion_proof,
  valid_at_attestation_time_proofs[], canonical_bundle_digest
}
SecurityDenialBucket {
  bucket_id, bucket_family_id, bucket_seq, predecessor_bucket_id?,
  security_domain_id, denial_class, fixed_time_bucket,
  ingress_seq_start, ingress_seq_end, previous_bucket_hash,
  denied_count, keyed_request_digest_accumulator,
  overflow_state: OPEN | CLOSED | SATURATED,
  signing_key_version, signature
}
SecurityDenialAdmissionState {
  security_domain_id, denial_class, fixed_time_bucket,
  next_ingress_seq, in_flight_count, concurrency_limit,
  fence_seq?, status: OPEN | FENCING | CLOSED,
  current_bucket_id, state_revision
}
SecurityDenialLease {
  denial_token, security_domain_id, denial_class, ingress_seq,
  keyed_request_digest, denial_receipt_signature, signing_key_version,
  lease_expiry,
  state: RESERVED | ACCOUNTED, admission_state_revision
}
SecurityDenialRangeCommitment {
  range_commitment_id, security_domain_id, denial_class, fixed_time_bucket,
  ingress_seq_start, ingress_seq_end, denied_count,
  keyed_digest_accumulator, bucket_id, audit_checkpoint_id,
  previous_range_commitment_hash, signature
}
ProviderIngressReceipt {
  ingress_receipt_id, security_domain_id, provider_id, provider_account_id,
  effect_id, canonical_payload_digest, request_fingerprint,
  delivery_id, provider_schema_version, signing_key_version,
  provider_timestamp, received_at, signature_verification,
  replay_head_id, reconciliation_read_id?, disposition
}
ProviderIngressEnvelope {
  ingress_envelope_id, authenticated_receipt_binding,
  ingress_receipt_id, effect_id, exact_outbox_head_id,
  exact_effect_head_id, exact_attempt_head_id,
  resulting_outbox_revision_id, resulting_effect_revision_id,
  resulting_attempt_revision_id?, resulting_claim_id?,
  terminal_result_digest, commit_state, commit_seq
}
SupplyChainAttestation {
  attestation_id, attestation_family_id, predecessor_attestation_id?,
  release_seq, configuration_epoch,
  artifact_digest, source_digest, dependency_lock_digest,
  build_provenance_digest, policy_schema_digest, migration_digest,
  canonical_deployment_configuration_manifest_digest,
  independently_measured_deployed_configuration_digest,
  signer_identity, signing_key_head_id, approved_by_independent_role_ids[],
  target_release, status
}
Task {
  task_id, current_task_revision_id?, deletion_epoch
}
DueSpec {
  canonical_instant, original_local_date_time_or_date_boundary,
  iana_zone_id, timezone_rule_revision_id,
  fold_gap_resolution, due_boundary_policy_revision_id
}
TaskRevision {
  task_revision_id, task_id, predecessor_revision_id?, variant_id?,
  purpose, obligated_outcome, specification, constraints,
  authority_grants[], acceptance_requirements[],
  planning_state: PLANNED | IN_PROGRESS | BLOCKED | CLOSED |
                  CANCELLED | SKIPPED,
  due_spec?: DueSpec,
  commit_seq, created_by_command_id, created_at
}
PlanningCommandAudit {
  command_id, actor_id, command_type, command_input: PlanningCommandInput,
  exact_policy_head_ids[], consumed_grant_ids[], consumed_delegation_ids[],
  consumed_requirement_ids[], consumed_satisfaction_ids[],
  bootstrap_authority_id?, issuer_meta_authority_head_id?,
  task_transition_policy_revision_id?,
  authority_compatibility_policy_revision_id?,
  validated_against_authority_family_head_id?,
  resulting_authority_family_revision_id?, authority_family_kind?,
  validated_against_task_revision_id?, resulting_task_revision_id?,
  validated_against_occurrence_revision_id?, resulting_occurrence_revision_id?,
  request_id, commit_seq, created_at
}
PlanningCommandInput = CreateTaskInput { resulting_task_revision_id, ... }
                     | TaskTransitionInput {
                         validated_against_task_revision_id,
                         resulting_task_revision_id, ...
                       }
                     | OccurrenceTransitionInput {
                         validated_against_occurrence_revision_id,
                         resulting_occurrence_revision_id, ...
                       }
                     | AuthorityMutationInput {
                         authority_family_kind, operation,
                         validated_against_family_head_id?,
                         resulting_family_revision_id
                       }
                     | OtherTypedPlanningInput { schema_id, fields }
PlanningRevision = TaskRevision | TaskOccurrenceRevision | TaskSeriesRevision |
                   RuleRevision | PlanExpectation | AuthorityGrant | AcceptanceRequirement |
                   AcceptanceSatisfaction | Delegation | DelegationSetRevision |
                   CommitmentRevision | ArrangementRevision
TaskOccurrence {
  occurrence_id, task_id, recurrence_binding?: RecurrenceBinding,
  created_by_command_id,
  creation_commit_seq, scheduled_task_revision_id,
  scheduled_due_spec?: DueSpec,
  current_occurrence_revision_id,
  lineage?: OccurrenceLineage, deletion_epoch
}
RecurrenceBinding = GeneratedRecurrenceBinding {
                      series_id, recurrence_coordinate,
                      logical_recurrence_key, recurrence_rule_revision_id
                    }
                  | ExceptionRecurrenceBinding {
                      series_id, source_recurrence_coordinate,
                      source_logical_recurrence_key, recurrence_exception_key,
                      transformation_id, transformation_policy_revision_id
                    }
TaskOccurrenceRevision {
  occurrence_revision_id, occurrence_id, predecessor_revision_id?,
  planning_state: PLANNED | IN_PROGRESS | BLOCKED | CLOSED |
                  CANCELLED | SKIPPED,
  commit_seq, created_by_command_id, created_at
}
TaskTransitionPolicyRevision {
  policy_revision_id, policy_family_id, predecessor_revision_id?,
  task_edges_by_command, occurrence_edges_by_command,
  terminal_states, reopen_replan_edges, transformation_source_edges,
  issuer_authority_head_id, created_by_command_id, commit_seq
}
WorkOccurrence {
  work_occurrence_id, task_id?, task_revision_id?, task_occurrence_id?,
  creation_claim_id, occurred_at?, deletion_epoch
}
OccurrenceLineage {
  relation: RESCHEDULED_FROM | SPLIT_FROM | MERGED_FROM | RECREATED_FROM,
  predecessor_occurrence_ids[]
}
TaskSeries {
  series_id, task_id, current_series_revision_id
}
TaskSeriesRevision {
  series_revision_id, series_id, predecessor_revision_id?,
  active_rule_revision_id,
  expansion_horizon: RecurrenceCoordinate, series_revision_token,
  recurrence_key_policy_revision_id, canonical_timezone,
  calendar_system, fold_gap_policy_revision_id, state,
  created_by_command_id, commit_seq
}
RecurrenceCoordinate {
  calendar_system, canonical_local_slot, fold_ordinal
}
RuleRevision {
  recurrence_rule_revision_id, series_id, predecessor_revision_id?,
  rule, effective_interval: [inclusive_coordinate, exclusive_coordinate),
  created_by_command_id
}
RecurrenceSlotDisposition {
  series_id, recurrence_coordinate, logical_recurrence_key,
  source_occurrence_id, transformation_id?,
  recurrence_binding: RecurrenceBinding,
  status: MATERIALIZED | SKIPPED | CANCELLED | TRANSFORMED_TOMBSTONE,
  commit_seq
}
ClaimSubject = OccurrenceSubject { occurrence_id }
             | WorkOccurrenceSubject { work_occurrence_id }
             | TaskSubject { task_id, task_revision_id }
PlanSubject = OccurrenceSubject { occurrence_id }
            | TaskIdentitySubject { task_id }
FactClaim {
  claim_id, claim_type, semantic_slot, claim_policy_version,
  registered_claim_policy_snapshot_id,
  value,
  subject: ClaimSubject,
  claim_semantics: RESULTANT_STATE | EVENT,
  source_type, exact_source_snapshot, asserting_principal_id, authenticated_by,
  ingestion_authority_id, created_by_claim_command_id,
  assertion_status: UNCONFIRMED | CONFIRMED,
  stable_append_seq, occurred_at?, effective_at?, observed_at,
  deletion_epoch
}
ClaimDisposition {
  disposition_id, operation: CORRECT | RETRACT | SUPERSEDE,
  target_claim_id, replacement_claim_id?, source_type, exact_source_snapshot,
  asserting_principal_id, authenticated_by, ingestion_authority_id,
  created_by_claim_command_id,
  policy_version, registered_claim_policy_snapshot_id,
  stable_append_seq, observed_at, deletion_epoch
}
ClaimPolicyFamily { policy_family_id, policy_kind, current_revision_id }
ClaimPolicyRevision {
  policy_revision_id, policy_family_id, predecessor_revision_id?,
  policy_kind: CLAIM_AUTHORITY | ADMISSIBILITY_MAPPING | DISPOSITION_PRECEDENCE,
  content_digest, status: ACTIVE | REVOKED | EXPIRED,
  issuer_authority_head_id, created_by_command_id, commit_seq
}
RegisteredClaimPolicySnapshot {
  snapshot_id, registered_claim_key,
  claim_authority_revision_id, admissibility_mapping_revision_id,
  disposition_precedence_revision_id, created_by_command_id, commit_seq
}
PlanExpectation {
  expectation_id, expectation_key, subject: PlanSubject, exact_task_revision_id,
  source_snapshot, expectation_kind: PLAN_EXPECTED,
  created_by_command_id, commit_seq, created_at
}
NaturalLanguageInteraction {
  interaction_id, utterance_snapshot, actor_id,
  state: INTERPRETED | PROPOSED | CONFIRMED | COMMITTED | REJECTED | CANCELLED,
  interpretation_revision_id, proposal_id?, committed_result?: NLCommittedResult,
  created_at
}
NLCommittedResult = ClaimAppendResult { claim_command_id, claim_ids[] }
                  | PlanningCommandResult { planning_command_id,
                                             planning_revision_ids[] }
                  | AtomicClaimPlanningResult { claim_command_id, claim_ids[],
                                                planning_command_id,
                                                planning_revision_ids[] }
NaturalLanguageInterpretationRevision {
  interpretation_revision_id, predecessor_revision_id?, interaction_id,
  classified_intent: FACT_REPORT | PLANNING_REQUEST | PROACTIVITY_RULE |
                     QUESTION | AMBIGUOUS,
  candidate_subjects[], field_attributions[], ambiguity_dimensions[],
  created_at
}
PlanningProposal {
  proposal_id, interaction_id, exact_base_revision_ids[],
  generated_by_proactivity_rule_revision_id?,
  planning_deltas[], external_effect_previews[], inferred_field_attributions[],
  expires_at, status: OPEN | AUTHORIZED | REJECTED | CANCELLED | STALE
}
ProactivityRule {
  rule_id, owner_principal_id, current_rule_revision_id
}
ProactivityRuleRevision {
  rule_revision_id, rule_id, predecessor_revision_id?,
  cadence, timezone_policy_revision_id, bounded_proposal_scope,
  allowed_defaults[], status: ACTIVE | PAUSED | CANCELLED | EXPIRED,
  commit_dispatch_authority: NONE,
  authorizing_command_id, commit_seq
}
InvocationAttempt {
  attempt_id, occurrence_id, scheduled_task_revision_id,
  scheduled_occurrence_revision_id, current_invocation_task_revision_id,
  current_occurrence_revision_id, compatibility_policy_revision_id,
  current_attempt_revision_id
}
AuthorityCompatibilityPolicyRevision {
  policy_revision_id, policy_family_id, predecessor_revision_id?,
  protected_dimensions, compatibility_predicate_digest,
  status: ACTIVE | REVOKED | EXPIRED,
  issuer_authority_head_id, created_by_command_id, commit_seq
}
InvocationAttemptRevision {
  attempt_revision_id, attempt_id, predecessor_revision_id?,
  state: HELD | ATTEMPT_ACCEPTED | DISPATCHING_IDEMPOTENT |
         DISPATCHING_NONIDEMPOTENT | TOOL_EXECUTION_CONFIRMED |
         REAL_WORLD_OUTCOME_OBSERVED | AMBIGUOUS | FAILED,
  actor_or_worker_id, command_id, commit_seq
}
ExternalEffect {
  effect_id, attempt_id, provider_id, resource_id?, idempotency_key?,
  canonical_request_fingerprint, provider_contract_version,
  idempotency_scope?, dedupe_retained_until?,
  dispatch_mode: IDEMPOTENT | NON_IDEMPOTENT, current_effect_revision_id
}
ExternalEffectRevision {
  effect_revision_id, effect_id, predecessor_revision_id?,
  state: CREATED | DISPATCHING | PROVIDER_ACCEPTED | PROVIDER_CONFIRMED |
         AMBIGUOUS | FAILED,
  actor_or_worker_id, command_id, commit_seq
}
ExternalDispatchOutbox {
  outbox_id, effect_id, dispatch_mode, provider_id,
  idempotency_key?, canonical_request_fingerprint,
  current_outbox_revision_id
}
ExternalDispatchOutboxRevision {
  outbox_revision_id, outbox_id, predecessor_revision_id?,
  state: AUTHORIZED | CLAIMED | SEND_STARTED | ACKNOWLEDGED | AMBIGUOUS,
  owner_id?, fencing_token, lease_expires_at?,
  provider_receipt_id?, created_by_command_id, commit_seq
}
AtomicRequestEnvelope {
  tenant_or_principal_id, request_id, request_fingerprint,
  operation, result: AtomicRequestResult,
  committed_result, dedupe_retained_until?, commit_state
}
AtomicRequestResult = MarkDoneResult { claim_id, planning_command_id }
                    | CreateWorkOccurrenceResult {
                        work_occurrence_id, creation_claim_id
                      }
                    | TypedAtomicResult { result_schema_id, fields }
OccurrenceTransformation {
  transformation_id, request_id, request_fingerprint,
  operation: RESCHEDULE | RECREATE | SPLIT | MERGE,
  sources[]: { occurrence_id, exact_source_revision_id },
  results[]: { occurrence_id, scheduled_task_revision_id,
               scheduled_due_spec?, recurrence_binding?: RecurrenceBinding,
               initial_state },
  transformation_policy_revision_id, command_id, commit_seq
}
OccurrenceTransformationPolicyRevision {
  policy_revision_id, policy_family_id, predecessor_revision_id?,
  operation, source_compatibility_dimensions,
  result_provenance_rules, recurrence_rules, status,
  issuer_authority_head_id, created_by_command_id, commit_seq
}
AuthorityGrant {
  grant_id, grant_family_id, predecessor_grant_id?,
  status: ACTIVE | REVOKED | EXPIRED,
  issuer_actor_id, issuer_authority_record_id?, grantee_actor_id,
  exact_task_revision_id?, bootstrap_scope_id?, scope, bounds, policy_version,
  created_by_command_id, commit_seq
}
AcceptanceRequirement {
  requirement_id, requirement_family_id, predecessor_requirement_id?,
  status: OPEN | WITHDRAWN | REVOKED | EXPIRED | SATISFIED,
  subject: AcceptanceSubject, counterparty_id, scope,
  satisfaction_policy_version, created_by_command_id, commit_seq
}
AcceptanceSatisfaction {
  satisfaction_id, satisfaction_family_id, predecessor_satisfaction_id?,
  status: ACTIVE | WITHDRAWN | REVOKED | EXPIRED,
  requirement_id,
  subject: AcceptanceSubject, counterparty_id, accepting_actor_id,
  acceptance_authority_record_id, accepted_scope, policy_version,
  evidence_claim_ids[], authenticated_command_id, commit_seq
}
Delegation {
  delegation_id, delegation_family_id, predecessor_delegation_id?,
  status: ACTIVE | WITHDRAWN | REVOKED | EXPIRED,
  delegator_id, delegate_id, subject: DelegationSubject,
  delegated_scope, bounds, acceptance_requirement_ids[], policy_version,
  created_by_command_id, commit_seq
}
DelegationSubject = DelegatedTaskSubject { task_id, exact_task_revision_id }
                  | DelegatedOccurrenceSubject {
                      occurrence_id, exact_occurrence_revision_id
                    }
AcceptanceSubject = GeneralTaskAcceptanceSubject { task_id, exact_task_revision_id }
                  | GeneralOccurrenceAcceptanceSubject {
                      occurrence_id, exact_occurrence_revision_id
                    }
                  | DelegationAcceptanceSubject {
                      delegation_family_id, exact_delegation_head_id,
                      delegate_id, delegated_subject: DelegationSubject
                    }
DelegationSet {
  subject: DelegationSubject, current_set_revision_id
}
DelegationSetRevision {
  set_revision_id, predecessor_revision_id?, member_delegation_head_ids[],
  coverage_policy_revision_id, projection: NONE | PARTIAL | FULL | DISPUTED | HELD,
  created_by_command_id, commit_seq
}
DelegationCoveragePolicyRevision {
  policy_revision_id, policy_family_id, predecessor_revision_id?,
  scope_coverage_rules, overlap_conflict_rules,
  issuer_authority_head_id, created_by_command_id, commit_seq
}
Arrangement {
  arrangement_id, commitment_id, current_arrangement_revision_id
}
Commitment { commitment_id, current_commitment_revision_id }
CommitmentRevision {
  commitment_revision_id, commitment_id, predecessor_revision_id?,
  obligated_parties[], promised_scopes[], lifecycle_state,
  created_by_command_id, commit_seq
}
ArrangementRevision {
  arrangement_revision_id, arrangement_id, predecessor_revision_id?,
  exact_commitment_revision_id, acceptance_mode,
  reliance_policy_revision_id,
  created_by_command_id, commit_seq
}
AffectedCounterpartyPolicyRevision {
  policy_revision_id, policy_family_id, predecessor_revision_id?,
  observable_inputs, reliance_thresholds, clarification_deadline_policy,
  resolver_authority_head_id, created_by_command_id, commit_seq
}
RelianceAssessment {
  assessment_id, arrangement_id, party_id, predecessor_assessment_id?,
  state: NONE | SUSPECTED | CONFIRMED | DISPUTED | REJECTED | EXPIRED | WITHDRAWN,
  observable_input_snapshot, policy_revision_id, provenance,
  resolver_authority_head_id?, clarification_deadline?,
  created_by_command_id, commit_seq
}
RelianceAssessmentFamily {
  assessment_family_id, arrangement_id, party_id,
  predecessor_family_id?, current_assessment_id
}
RelianceAssessmentLineage {
  arrangement_id, party_id, current_family_id
}
NodeRelation {
  relation_id, relation_type: STRUCTURAL_PARENT | DERIVED_FROM | EVIDENCES |
                  SUPERSEDES | VARIANT_OF,
  typed_source, typed_target, source_revision_id?, target_revision_id?,
  created_by_command_id?, stable_append_seq?, commit_seq?
}
StructuralParentEdge {
  edge_id, child_task_id, parent_task_id, current_edge_revision_id
}
StructuralParentEdgeRevision {
  edge_revision_id, edge_id, predecessor_revision_id?,
  status: ACTIVE | EXPIRED, created_by_command_id, commit_seq
}
DeletionRequest {
  deletion_id, unlinkable_deletion_token, epoch, predecessor_epoch?,
  current_deletion_revision_id
}
DeletionRoutingEnvelope {
  deletion_id, encrypted_scope_kind_id_time_policy_registry_metadata,
  scope_key_head_id,
  state: OPEN | SHRED_PENDING | SHRED_CONFIRMED
}
DeletionRequestRevision {
  deletion_revision_id, deletion_id, predecessor_revision_id?,
  state: PENDING | PROPAGATING | COMPLETE | HELD,
  nonsemantic_revision_ordinal
}
DeletionAcknowledgement {
  unlinkable_deletion_token, store_class, acknowledged_epoch,
  acknowledgement_bit, proof_class, nonsemantic_ordering_ordinal
}
DeletionOperationalSidecar {
  deletion_id, encrypted_command_policy_registry_hold_recovery_metadata,
  encrypted_store_ids_watermarks_and_proofs, scope_key_head_id
}
KeyDestructionOperation {
  destruction_id, unlinkable_deletion_token, deletion_epoch,
  request_head_id, key_head_id, idempotency_key,
  state: PENDING | CONFIRMED,
  kms_hsm_destruction_receipt?, verifier_policy_revision_id,
  nonsemantic_ordering_ordinal
}
RetentionPolicyRevision {
  policy_revision_id, policy_family_id, predecessor_revision_id?,
  data_class, purpose, maximum_payload_retention,
  minimum_safety_retention, expiry_action, hold_authority?, deletion_override
}
RetentionHoldRevision {
  hold_revision_id, hold_family_id, predecessor_revision_id?,
  unlinkable_scope_token, authority_head_id, legal_basis,
  allowed_retained_field_set, status: ACTIVE | RELEASED | EXPIRED,
  expires_at?, created_by_command_id, commit_seq
}
RetentionHoldSetRevision {
  hold_set_revision_id, predecessor_revision_id?, active_hold_revision_ids[],
  created_by_command_id, commit_seq
}
DeletionFrontier {
  frontier_id, security_domain_id, frontier_seq, previous_frontier_hash,
  per_scope_epoch_root, frozen_registry_generation,
  signing_key_head_id, signing_key_history_digest, signature,
  independent_nonrollback_witness_receipts[], published_at
}
ExternalDisclosureGrant {
  grant_id, protected_subject_id, current_grant_revision_id
}
ExternalDisclosureGrantRevision {
  grant_revision_id, grant_id, predecessor_revision_id?,
  status: ACTIVE | REVOKED | EXPIRED,
  principal_id, audience, allowed_purpose, exact_scope,
  schema_revision_id, correlation_domain, valid_interval,
  issuer_authority_head_id, created_by_command_id, commit_seq
}
ExternalDisclosureSchema {
  schema_id, current_schema_revision_id
}
ExternalDisclosureSchemaRevision {
  schema_revision_id, schema_id, predecessor_revision_id?,
  status: ACTIVE | REVOKED | EXPIRED,
  allowed_fields, precision, ttl, audience, purpose, correlation_scope,
  issuer_authority_head_id, created_by_command_id, commit_seq
}
DisclosureLedger {
  protected_subject_id, correlation_domain, rolling_horizon,
  release_epoch, canonical_bucket_policy, consumed_budget_cache, budget_limit,
  current_debit_id?
}
DisclosureDebit {
  debit_id, protected_subject_id, correlation_domain, release_epoch,
  predecessor_debit_id?, request_envelope_id, amount,
  grant_revision_id, schema_revision_id,
  source_scope_token, source_deletion_epoch, deletion_handler_policy_revision_id,
  commit_seq
}
DisclosureRequestEnvelope {
  disclosure_request_id, canonical_request_fingerprint,
  grant_revision_id, schema_revision_id, prior_debit_id?, debit_id,
  canonical_response_digest, encrypted_response_reference?,
  source_scope_token, source_deletion_epoch, deletion_handler_policy_revision_id,
  opaque_receipt_token, terminal_result, commit_seq
}
ExternalDisclosureReceipt {
  opaque_request_token, coarse_terminal_class, receipt_access_cohort,
  authorized_principal_id, audience, grant_family_id,
  source_scope_token, source_deletion_epoch, release_epoch
}
ParticipationConfirmation {
  confirmation_id, confirmation_family_id, predecessor_confirmation_id?,
  status: ACTIVE | WITHDRAWN | REVOKED | EXPIRED | DISPUTED,
  exact_occurrence_id, exact_occurrence_revision_id,
  participant_principal_id, confirming_authority_head_id, source_snapshot,
  allowed_detail_scope, purpose, audience, valid_interval,
  source_deletion_epoch, created_by_command_id, commit_seq
}
OperationalAdoptionProfile {
  profile_id, current_profile_revision_id, current_evidence_frontier_seq,
  current_evidence_set_id?, current_approval_id?,
  current_supply_chain_attestation_id?
}
OperationalAdoptionProfileRevision {
  profile_revision_id, profile_id, predecessor_revision_id?, content_hash,
  target_environment, target_release, owners, workload_envelope,
  migration_and_compatibility_plan, rollout_and_abort_gates,
  stuck_state_runbooks, observability_and_slos,
  recovery_objectives_and_drills, partition_and_cost_model,
  security_and_abuse_plan,
  required_evidence_manifest, policy_versions,
  author_principal_id, author_principal_family_head_id,
  artifact_producer_principal_ids[], artifact_producer_principal_family_head_ids[],
  separation_of_duties_policy_revision_id,
  created_by_command_id, commit_seq
}
OperationalEvidenceResult {
  evidence_id, profile_revision_id, manifest_item_id,
  evidence_seq, predecessor_item_result_id?, evidence_type,
  result: PASS | FAIL, required_input_digests[], predicate_head_id,
  predicate_output_digest, artifact_digest, observed_at, valid_until,
  evaluator_id, evaluator_principal_family_head_id,
  evaluator_independence_domain,
  evaluator_policy_head_id, evaluator_authority_head_id
}
OperationalEvidenceManifestItem {
  manifest_item_id, profile_revision_id, obligation_code,
  predicate_head_id, required_input_schema_digests[], evaluator_policy_head_id,
  freshness_rule, required: true
}
OperationalAcceptancePredicate {
  predicate_id, predicate_family_id, predecessor_predicate_id?,
  status: ACTIVE | REVOKED | EXPIRED, obligation_code,
  executable_spec_digest, required_input_schema_digests[],
  policy_version, created_by_command_id, commit_seq
}
OperationalEvaluatorPolicy {
  evaluator_policy_id, policy_family_id, predecessor_policy_id?,
  status: ACTIVE | REVOKED | EXPIRED, obligation_codes[], evaluator_scope,
  freshness_rule, created_by_command_id, commit_seq
}
OperationalEvaluatorAuthority {
  evaluator_authority_id, authority_family_id, predecessor_authority_id?,
  status: ACTIVE | REVOKED | EXPIRED, evaluator_id, obligation_codes[],
  environment_scope, policy_head_id, valid_from, valid_until,
  created_by_command_id, commit_seq
}
OperationalEvidenceSet {
  evidence_set_id, profile_revision_id, evidence_frontier_seq,
  manifest_item_head_ids[], digest_policy: OAP_DIGEST_V2,
  complete_result_digest, separation_of_duties_digest,
  state: OPEN | SEALED, sealed_by_authority_id, sealed_at
}
OperationalApproval {
  approval_id, approval_family_id, predecessor_approval_id?,
  status: ACTIVE | REVOKED | SUPERSEDED,
  profile_revision_id, target_environment, target_release,
  workload_envelope_hash, policy_versions, evidence_set_id,
  evidence_frontier_seq, complete_result_digest,
  approver_id, approver_principal_family_head_id,
  separation_of_duties_digest,
  approver_authority_head_id, approved_at, valid_until
}
OperationalApproverAuthority {
  authority_id, authority_family_id, predecessor_authority_id?,
  status: ACTIVE | REVOKED | EXPIRED, approver_id,
  environment_scope, release_scope, workload_envelope_hash,
  policy_version, valid_from, valid_until, created_by_command_id, commit_seq
}
OperationalDeployment {
  environment_id, current_deployment_revision_id
}
OperationalDeploymentRevision {
  deployment_revision_id, predecessor_revision_id?, environment_generation,
  phase: PLANNED | SHADOW | COHORT | FULL | ABORTING | ROLLED_BACK | HOLD,
  cohort_id?, profile_revision_id, evidence_frontier_seq, evidence_set_id,
  approval_id, approver_authority_head_id, supply_chain_attestation_id,
  runtime_configuration_measurement_digest, promoter_id,
  promoter_principal_family_head_id, observed_gate_result,
  irreversible_boundary, rollback_target_revision_id?,
  created_by_promotion_command_id, commit_seq
}
OperationalSeparationOfDutiesPolicyRevision {
  policy_revision_id, policy_family_id, predecessor_revision_id?,
  role_independence_matrix, independence_domain_rules,
  emergency_exception_evidence_requirements,
  issuer_authority_head_id, created_by_command_id, commit_seq
}
```

Every canonical record except `CrossDomainBridge` and
`CrossDomainBridgeRevision` belongs to exactly one `security_domain_id`; the field
is omitted from later declarations only to reduce repetition. Bridge records are
dual-endpoint control-plane records carrying immutable source, target, and dedicated
coordinator control domains. Their family orders by a bridge-local monotone
`bridge_revision_seq` allocated under exact bridge-head CAS, never either endpoint's
`commit_seq`. The source approval resolves only in the source domain and target
approval only in the target domain. A child otherwise derives its
domain from its authoritative parent and cannot accept a caller-supplied override.
All other foreign keys, uniqueness keys, CAS heads, transactions, relations, queues,
outboxes, caches, indexes, blobs, projections, provider resources, audit/operator
queries, backups, restores, and deletion scopes enforce identical domain binding.
The only exception is a transaction that validates an exact active bridge plus both
endpoint approval heads; that exception is bounded to the bridge's declared scope
and never changes record ownership.
The sole cross-domain path is an exact active `CrossDomainBridge`; it never changes
either endpoint's ownership. Storage isolation and per-domain encryption-key
boundaries are mandatory where the chosen platform can enforce them; any weaker
boundary must be explicit adoption evidence. Cross-domain denials use one versioned
externally observable response class and do not intentionally expose object
existence. Any stronger indistinguishability claim requires an adopted threat model
and conformance tests covering status, body/size, timing bounds, cache behavior, rate
limits, error paths, and repeated queries.

`CommitOrdinal = (security_domain_id, commit_seq)`. `commit_seq` is unique and
strictly monotone only inside its immutable security domain. Every greatest-current
selection, uniqueness key, CAS comparison, revision/audit binding, ordinal edge
check, and vector using `commit_seq` is scoped to one named domain and compares only
the numeric second component after domain equality. Equal numeric sequences in two
domains are unrelated. A bridge may authorize a cross-domain operation but never an
ordinal comparison or shared current-head calculation across domains.

`StableAppendOrdinal = (security_domain_id, stable_append_seq)` follows the same
rule for journal-native appends: its numeric component is unique and strictly
monotone only within one immutable security domain. Claim dispositions, corrections,
supersession, dedupe, and every claim-carried `NodeRelation` compare complete
same-domain ordinals. A bridge never authorizes a cross-domain append-order
comparison or a shared claim head.

`PlanningCommandInput` is a closed tagged union, not an escape hatch. `CREATE_TASK`
requires `CreateTaskInput`, a bounded `bootstrap_authority_id`, and the exact created
task revision, with no fictitious predecessor. Task and occurrence transitions use
their matching variants and exact predecessor/result IDs. Authority mutation uses
`AuthorityMutationInput` tagged by family and operation, its exact predecessor head
when one exists, its mandatory result revision, and issuer meta-authority head.
Those values equal the audit's typed authority-family predecessor, result, and kind
fields; creation has no invented predecessor.
`OtherTypedPlanningInput` is allowed only after its schema is registered with a
command-type-to-required-fields validator. Unknown or mismatched variants reject.
The protected action, typed audit, consumed authority heads, and result commit
atomically.

`Task` is the atemporal identity; `current_task_revision_id` is a rebuildable cache,
and every normative change creates a `TaskRevision` under that identity. Purpose,
obligated outcome, specification, constraints, due semantics, grants, and planning
state exist only on the exact revision. `TaskOccurrence` is a planned temporal slot bound to the exact
task revision that created it. A `WorkOccurrence` is optional and journal-native: it
is created only when an observed event needs identity independent of a planned slot,
for example ad-hoc work with no pre-existing task. It never back-creates a `Task`,
`TaskRevision`, or planning history. `TaskSubject` is therefore used only for a
resultant-state claim about an existing exact task revision. Such a claim must have
`claim_semantics = RESULTANT_STATE`, must not carry `occurred_at`, and may carry an
`effective_at` state boundary without thereby asserting an event. Concrete episodes
must have `claim_semantics = EVENT` and use an `OccurrenceSubject` or
`WorkOccurrenceSubject`. A task-linked `WorkOccurrence` must carry matching
`task_id + task_revision_id`; an ad-hoc one carries neither, and
`task_occurrence_id`, when present, must resolve to an occurrence scheduled by the
same task revision. There is
one canonical revision binding in the tagged
subject, never a second revision field.

`TaskOccurrence` is stable identity; its state changes only through append-only
`TaskOccurrenceRevision` successors. The `current_occurrence_revision_id` pointer is
a derived/cache pointer to the greatest accepted commit and is never authority by
itself. Occurrence commands CAS the exact current occurrence revision.

For every planning revision, `created_by_command_id` resolves to exactly one
`PlanningCommandAudit`, and its `commit_seq` equals that audit's unique, strictly
monotonic-within-domain `commit_seq`, with the same `security_domain_id`. No separate
accepted sequence exists.

`TaskSeries` owns recurrence rules, not occurrences' outcomes. `RuleRevision` binds
one immutable rule version. `PlanExpectation` binds one expectation to an exact
subject and exact task revision. It is a planning-native immutable child of the exact
creating `PlanningCommandAudit`/`TaskRevision`; its domain-scoped `commit_seq` is
provenance, `expectation_kind` is not a claim type, and it is neither a `FactClaim`
nor a journal projection. These names completely replace the former generic
`PlanLine`, `PlanRevision`, `ScheduledOccurrence`, `Series`, `RecurrenceRuleRevision`,
`ExpectationSeed`, and `Claim` identities in this task specialization; every foreign
key above resolves to the task-specialized identity shown here.

Identifiers are opaque and never reused. Claim IDs, occurrence IDs, effect IDs,
source snapshots, append sequences, plan expectations, and lineage are immutable.
Correction, retraction, cancellation, and supersession append state rather than
rewriting history.

The claim registry version validates `(claim_type, subject kind, claim_semantics,
semantic_slot)`. `UNCONFIRMED` and `CONFIRMED` are immutable assertion statuses.
`DISPUTED`, `CORRECTED`, and `RETRACTED` are derived projection results, never stored
claim states. `CORRECT` must name a replacement claim on the same registered key;
`RETRACT` removes only the target's current contribution; `SUPERSEDE` replaces it
under the registered policy. Every disposition targets a lower `stable_append_seq`.
Claims and dispositions are authenticated and atomically checked by a versioned
`ClaimAuthorityPolicy` over principal, source, claim type, subject, and operation.
Cross-principal correction without explicit authority appends a competing claim and
may produce `DISPUTED`; an unauthorized disposition is retained for audit but has no
projection effect.

Disposition projection is a deterministic versioned graph reduction on one
registered claim key. It recursively evaluates replacement claims and their
dispositions. Multiple incomparable effective dispositions project `DISPUTED`; no
arrival-time tie-break exists. The policy explicitly defines whether an authorized
retraction dominates, coexists with, or conflicts with correction/supersession.
Unknown policy projects `UNRESOLVED_POLICY`/`DISPUTED` and removes no contribution.
Every claim and disposition append resolves one immutable
`RegisteredClaimPolicySnapshot`, validates the exact active heads and registered
key, and stores its snapshot ID atomically with the append. Reduction uses only
those recorded revisions; later policy-family successors never reinterpret prior
claims or dispositions. A disposition and its replacement must resolve under a
compatible registered-key snapshot or remain ineffective/disputed as the recorded
precedence policy specifies.

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

### P0-05 — invocation compatibility and receipt levels

Invocation stores its scheduled task and occurrence revisions and the exact current
task and occurrence revisions used at invocation. A versioned
`AuthorityCompatibilityPolicyRevision` treats
any change to time, target, audience, scope, reliance, status, mandate, or
precondition as incompatible. Attempt acceptance records the exact active policy
revision in both attempt and command audit; acceptance and dispatch CAS the policy
family head together with every protected head. Amendment/revocation advances that
family under issuer-authority and predecessor CAS. Compatibility validation and
`ATTEMPT_ACCEPTED` commit atomically. Stale, revoked, expired, incompatible, or missing-policy attempts
remain held.

The attempt's `current_occurrence_revision_id` is an immutable snapshot captured in
the atomic acceptance transaction, not a live pointer. Every occurrence transition
audit requires both typed `validated_against_occurrence_revision_id` and
`resulting_occurrence_revision_id`; an occurrence revision without those bindings is
invalid.

The levels are distinct: `ATTEMPT_ACCEPTED`, `TOOL_EXECUTION_CONFIRMED`, and
`REAL_WORLD_OUTCOME_OBSERVED`. None implies the next. Each journal claim remains
non-normative; a response requires a later validated command and revision.

Attempt and effect state changes append revisions under predecessor CAS and the
canonical command `commit_seq`; scalar current pointers are caches.

| Mode | Permitted attempt edges |
|---|---|
| Both | `HELD→ATTEMPT_ACCEPTED`; `TOOL_EXECUTION_CONFIRMED→REAL_WORLD_OUTCOME_OBSERVED`; any post-dispatch state may enter `FAILED` only with an exact failure receipt. |
| Idempotent | `ATTEMPT_ACCEPTED→DISPATCHING_IDEMPOTENT→TOOL_EXECUTION_CONFIRMED`; dispatch may enter `AMBIGUOUS`, from which retry is allowed only inside the verified provider contract/window. |
| Non-idempotent | `ATTEMPT_ACCEPTED→DISPATCHING_NONIDEMPOTENT→TOOL_EXECUTION_CONFIRMED`; dispatch may enter terminal-for-automation `AMBIGUOUS`. |

Effect edges are `CREATED→DISPATCHING→PROVIDER_ACCEPTED→PROVIDER_CONFIRMED`, with
`FAILED` or `AMBIGUOUS` post-dispatch branches. Compensation is a new effect, never a
state of the old effect. Any unlisted edge is rejected. For non-idempotent effects,
the paired dispatch transition is the irrevocable linearization point and forbids
automated resend or takeover on every successor branch.

`AMBIGUOUS` is terminal for dispatch automation, not for evidence. Only an
authenticated matching `ProviderIngressEnvelope` or authorized reconciliation read
may CAS effect `AMBIGUOUS→PROVIDER_ACCEPTED|PROVIDER_CONFIRMED|FAILED` and attempt
`AMBIGUOUS→TOOL_EXECUTION_CONFIRMED|FAILED`. Those resolution edges atomically bind
the receipt/reconciliation evidence and never authorize a provider call, resend, or
takeover.

Immediately before any provider call, a dispatch-authorization transaction
serializes against the current task/occurrence revisions, grant-family heads,
delegation/acceptance heads, and deletion epoch; revalidates every protected
dimension; and CAS-appends paired mode-specific `DISPATCHING_*` attempt / `DISPATCHING`
effect revisions plus a durable outbox lease. That commit is the dispatch authority
linearization point. If cancellation, replanning, revocation, withdrawal, or deletion
wins first, dispatch is held. If dispatch wins first, later changes cannot revoke the
already-authorized effect and the race remains visible in UI/audit.

The lease is the revisioned `ExternalDispatchOutbox`, unique by domain and
`effect_id`. Each claim CASes the exact head and allocates a strictly higher fencing
token. A worker must CAS `CLAIMED→SEND_STARTED` with its current unexpired token
immediately before the provider call; a stale token cannot start sending. A crash or
expiry in `AUTHORIZED` or `CLAIMED` may be reclaimed with a higher token because no
provider call was authorized. Provider callbacks and receipts use one idempotent
`ProviderIngressEnvelope`, unique by domain and authenticated provider
receipt/effect binding. One transaction CAS-checks the exact outbox, effect, and
attempt heads and atomically commits the receipt, `ACKNOWLEDGED` outbox successor,
corresponding effect/attempt successors, and any claim append. If the selected stores
cannot share a transaction, the durable envelope is sole authority and every record
is an idempotent derivative; none of the transition is projection-visible until the
complete envelope result exists. Retry returns the identical terminal result, never
a second semantic transition.

### P0-06 — external-effect safety

Every effect has immutable `effect_id`. A provider is treated as idempotent only
within a verified, versioned contract naming the key scope, payload-mismatch
behavior, and guaranteed retention window. Its key binds provider, resource, effect,
and an immutable canonical request fingerprint covering operation, target, payload,
authority, and attempt. A retry must reuse the exact key and match that fingerprint
byte-for-byte. After provider-key expiry, or when dedupe state is unknown, automatic
retry is held; safety is claimed only inside the verified contract and retention
window.

For non-idempotent providers, durable `DISPATCHING_NONIDEMPOTENT` is the irrevocable
authorization linearization point. After ambiguity there is no takeover, automated
resend, or reuse by another worker. Manual repetition without proof of nonoccurrence
is a new separately authorized effect whose command acknowledges duplicate risk.
Compensation is also a new authorized effect, never historical mutation.
For the outbox specifically, non-idempotent `SEND_STARTED` is never re-leased: lease
expiry, worker loss, or missing acknowledgement appends terminal-for-automation
`AMBIGUOUS`. This deliberately accepts a possible authorized-but-unsent effect when
a crash follows `SEND_STARTED` but precedes the socket write, preserving the stronger
at-most-one-call boundary. Idempotent work may be reclaimed after `SEND_STARTED`
only with the identical key/fingerprint and inside the verified provider dedupe
window; otherwise it also holds as `AMBIGUOUS`.

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

## Adoption-assurance annex A — platform security

The following proposed P0 interface is required for safe adoption but is orthogonal
to the Plan/Fact ontology.

### P0-11 — security boundary, authentication, audit, and trusted ingress

Server-side request admission derives actor and security domain only from an
unexpired `AuthenticationContext` bound to the canonical request digest, nonce,
session/credential head, intended service/audience, and channel where supported.
Context issuance is append-only, audited, and atomically signs/MACs a canonical
digest of every field with a currently authorized authenticator key. Admission
verifies issuer, issuer-authority, authentication-policy and signing-key heads,
signature/MAC, exact request bytes, mandatory channel-binding result, and all
credential/session/principal heads. Unsigned, mutable, imported, field-spliced,
unknown-issuer, or auth-strength-inflated contexts reject.
Caller-supplied actor/domain fields are never authority. Grant issue/revoke, deletion
override, duplicate-risk repeat, recovery promotion, policy/evaluator/approver/key
change, operational approval, bridge creation, and break-glass actions require recent
step-up authentication; validation and commit/dispatch CAS-check that its strength
and freshness remain valid.

Admission atomically inserts a unique replay guard on
`(domain, authenticated subject, audience, nonce)` and binds its request digest.
Nonce reuse with another digest is rejected. Identical reuse returns a prior result
only for an explicitly idempotent operation whose committed envelope digest matches;
otherwise it is rejected. A fresh-record or external-effect action cannot reserve a
second guard. Guard transition and command/envelope commit share one transaction;
aborted reservations have one explicit CAS retry path and never authorize work.

Bridge creation, amendment, revocation, and expiry append
`CrossDomainBridgeRevision` successors under exact bridge-head CAS and consume two
independent endpoint approvals: one issued inside the source domain and one inside
the target domain, each bound to direction, operations, objects/fields, purpose,
audience, quota, expiry, and counterparty. Activation/amendment is invisible until
both current approvals commit through one atomic or recoverable coordinator result.
Only the greatest committed `ACTIVE`
head contributes. Every cross-domain read, write, disclosure, queue delivery, or
effect CAS-checks the exact bridge revision, both endpoint approval heads, and every referenced authority-family
head at its own visibility/dispatch linearization point. If revocation, expiry, scope
change, or authority revocation commits first, the protected operation rejects; if
the operation linearizes first, its bounded result remains auditable and is not
retroactively reclassified.

A replay guard remains authoritative until the bound context is expired beyond the
profile's maximum clock skew, every admitted operation is terminal beyond the maximum
in-flight lifetime, and a verified audit checkpoint covers the guard/result. Only
then may one atomic compaction remove or cryptoshred its request/result payload and
guard row. Replay after that point still fails because the context, session, or
credential is no longer admissible. The profile bounds context lifetime, skew, and
in-flight lifetime; if terminality/checkpoint cannot be proved, compaction is held.
Thus live replay protection is complete and retained guards are time/concurrency-
bounded rather than permanent.

Credential/session/principal families are append-only, bounded-lived, and advance
monotone revocation epochs under exact family-head CAS. Only the greatest committed
effective `CredentialRevision`, `SessionRevision`, and `PrincipalSecurityRevision`
head contributes. Those three heads must resolve to the same authenticated principal
and security domain named by the context. Admission, command commit, dispatch,
provider ingress, evidence sealing, approval, and recovery promotion recheck the
exact current credential, session, and principal-security family heads. `REVOKED`, `EXPIRED`, or
`COMPROMISED` fences queued work, sessions, tokens, evaluator signatures, and
unverified callbacks; dependent secrets rotate and unverifiable input quarantines.
Only an effect already past its recorded dispatch linearization point survives a
later revocation. Emergency recovery is scope/time-bounded and dual-controlled.

Every accepted or high-impact denied security action appends a domain-separated hash-
chained `SecurityAuditEntry` containing only an opaque envelope ID, randomized
commitment, coarse non-semantic event class/time bucket, and signature metadata.
Canonical semantic command/event bytes, authentication, authorization/policy heads,
result data, operator detail, and the actionable protected-linearization ID exist
only in the scope-keyed encrypted sidecar. The durable obligation uses a fresh opaque
randomized linearization token that cannot join back to the action after shredding.
Checkpoint and anchor advancement requires a threshold under a versioned witness
policy of administratively and key-custody-diverse nonrollback receipts with monotone
witness sequences and predecessor binding. Empty/under-quorum receipt sets are
invalid. Authoritative current-head discovery queries those witnesses rather than
local backup state; disagreement, equivocation, or unavailability marks the domain
`COMPROMISED` and blocks promotion/pruning. Restore/promotion requires that witnessed
current checkpoint and continuity proof. Gaps, forks, truncation,
rollback, signature/key-custody failure, or unverifiable rotation marks the domain
compromised and blocks promotion. Redaction follows the deletion field matrix while
preserving non-reconstructive continuity commitments. This is tamper evidence, not a
claim of legal nonrepudiation.

Audit completeness is coupled to every protected linearization point. If the action
and audit ledger share one transaction, its `SecurityAuditEntry` commits atomically.
Otherwise the same transaction creates a uniquely keyed durable
`SecurityAuditObligation`; the obligation is the sole visibility/dispatch authority,
and the action, provider outbox lease, bridge change, deletion override, promotion,
or high-impact denial remains non-visible/non-dispatchable until an idempotent relay
records the exact chained entry and atomically marks the obligation `RECORDED`.
Crashes before either commit leave neither; crashes after obligation commit recover
the same entry from its stored opaque envelope ID, randomized commitment, and
idempotency key. Missing/unavailable audit storage yields
`HOLD`, never unaudited success. Obligation/entry mismatch, duplication, or skipped
sequence marks the domain compromised.

Repeated low-level denials under abuse are never silently dropped: each contributes
exactly once to a fixed-duration, fixed-class `SecurityDenialBucket` with a keyed
request-digest accumulator, authenticated count, previous-bucket hash, overflow
state, and signature. Bucket classes/cardinality are bounded per domain; overflow
closes the bucket, trips admission/circuit breaking, and opens one successor bucket
or a coarser predeclared overflow class. High-impact, first-instance, class-change,
and operator denials remain exact entries. Checkpoints commit both exact entries and
bucket roots, preserving integrity and totals without unbounded per-request rows.

Authenticated application ingress uses one serializable admission transaction to
CAS the domain/class/window state, allocate monotone `ingress_seq`, create a uniquely
keyed bounded `SecurityDenialLease`, and increment `in_flight_count`. No sequence
exists without a durable lease containing the keyed request digest. Bucket updates
CAS the exact family head, cover one contiguous non-overlapping sequence range,
atomically update count/accumulator, mark covered leases `ACCOUNTED`, and decrement
the in-flight count; retrying an identical range returns the existing revision, while
gaps/overlaps are rejected. Expired reserved leases are deterministically accounted
from their durable digest before reuse/closure. Policy fixes a
maximum events per bucket and maximum successor buckets per time window. At the
limit, one admission-state CAS changes `OPEN→FENCING`, records
`fence_seq=next_ingress_seq-1`, and refuses new leases. The final bucket becomes
`SATURATED` only after every reserved token through the fence is accounted and
`in_flight_count=0`; then state becomes `CLOSED` until the next window. Thus
concurrent pre-fence actions drain and no post-fence application actions exist to
count. Earlier network-level drops are outside the authenticated action ledger and
use separately bounded DDoS telemetry. Lease cardinality is bounded by the declared
concurrency limit; overflow creates exactly one successor under head CAS.

Ordinary clock rollover uses the same fence protocol. At the boundary, the old state
head records its final `fence_seq` and stops old-window allocation. A next-window
state may open only after that fence is durable, cannot absorb old leases, and only
while the profile's maximum simultaneous `FENCING` windows is not exceeded. Every
lease lifetime is bounded by the same profile and expired leases are deterministically
accounted; therefore each old window drains, signs/checkpoints its final bucket root,
and becomes immutable `CLOSED` within the declared bound. If the fencing-window cap
or drain SLO is reached, domain/class admission closes until recovery. Retained old
states/leases are bounded by fencing-window cap × concurrency limit.

After a signed bucket root is included in a verified audit checkpoint, one crash-safe
compaction transaction creates a signed `SecurityDenialRangeCommitment` for its
contiguous accounted sequence range, advances the admission state's accounted high-
water mark, and removes or cryptographically shreds only `ACCOUNTED` lease rows and
their request digests. `RESERVED` leases are never compacted. Retry/replay at or below
the high-water mark resolves against a signed denial receipt token rather than
recreating a lease. Its signature covers domain, class, window, sequence, keyed
request digest, and bucket family; verification also requires that the committed
range contains the sequence and the checkpoint covers the signing-key version.
Missing, forged, or mismatched tokens are not duplicate proof and enter as new
denials when admission is open. A crash before atomic compaction leaves leases; a
crash after it leaves the commitment—never neither.

The security retention policy sets a maximum denial-replay horizon and a fixed count
of recent checkpoint epochs. Closed-window
range commitments within it are bounded by fixed bucket/successor limits. Older
commitments are folded into one signed checkpoint-epoch summary root, then detailed
ranges and keyed digests are pruned or cryptoshredded under the deletion field
matrix. When the epoch count reaches its bound, older summary roots fold recursively
into one signed cumulative anchor `(first_epoch, last_epoch, aggregate_count,
ordered_root)`; the prior detailed summaries are pruned after independent checkpoint
receipts confirm the anchor. Only that anchor plus the fixed recent epochs remain in
controlled storage. The summary preserves continuity and aggregate counts, not per-request replay
claims. An older token receives the same versioned external denial class as other
invalid tokens and, when
admission is open, counts as a new action. Retained rows are therefore bounded by
classes × configured windows plus checkpoint epochs, not elapsed windows or volume.

Anchor folding is one domain-serialized transaction: it CAS-targets the exact current
anchor and exact contiguous summary-head/frontier. The successor is cumulative: it
inherits `first_epoch=prior.first_epoch`, sets `last_epoch` to the last new summary,
and computes `aggregate_count=prior.aggregate_count + Σ(new summary counts)` using a
declared bounded unsigned representation; pre-check overflow yields `HOLD` and a
versioned wider-format migration, never wraparound. Its domain-separated
`ordered_epoch_root` commits the complete signed prior cumulative anchor fields/root
followed by every exact new summary in increasing epoch order, including epoch range,
count, and root. Construction is non-circular: first canonicalize the unsigned
successor body (all cumulative fields, no certificate digest/signature) and hash it
as `chiplog-audit-anchor-body-v1`; auditors certify that body digest; hash the
canonical certificate as `chiplog-audit-fold-cert-v1`; then sign the final anchor as
`chiplog-audit-anchor-final-v1 || anchor_body_digest || certificate_digest`. The body
does not contain the certificate digest, and the certificate does not reference the
final-anchor digest. The transaction then atomically advances
`SecurityDomain.current_audit_epoch_anchor_id`. Its idempotency key is the exact prior
anchor plus epoch range; overlaps, gaps, forks, and duplicate successors reject or
return the same result. Detailed epochs and the prior anchor are pruned only after
independent receipts attest that exact signed cumulative successor. A current-anchor-
only verifier validates the retained fold-transition certificate rather than merely
trusting the successor assertion. Before pruning, each independent auditor obtains
the exact prior anchor and contiguous input summaries, recomputes range, additive
count, and ordered root, and signs a semantic attestation over the prior digest,
input frontier/digest, recomputed values, and output anchor body digest. The certificate
must satisfy the versioned quorum threshold and administrative/key-custody diversity;
ordinary application/database operators cannot form it. The output anchor commits
the certificate digest. Its self-contained, bound `AuditTrustEvidenceBundle` retains the
exact quorum-policy snapshot/digest, historical auditor-authority head inclusion and
continuity proofs, signing-key rotation/revocation histories, signed administrative-
domain and custody attributes, validity-at-attestation proofs, and the prior
published-checkpoint inclusion proof. Canonical verification rejects missing fields,
invalid chains, duplicate auditors, or identities that fail policy independence.
Restore verifies the bundle, signatures, authority/key status at attestation time,
quorum/current trust policy, exact body binding, and chain to
the latest published checkpoint. Missing, stale, under-quorum, non-diverse, or
mismatched certificates mark the domain compromised and block promotion. Thus the
retained anchor plus certificate proves that a quorum satisfying the retained trust
policy attested to the bound transition. It does not let a later verifier recompute
transition semantics from pruned inputs; assurance depends on quorum validity,
independence, key custody, and correct pre-pruning verification. Here “evidence” is a
policy-bound trust record, not a cryptographic proof of computation. The current
anchor, certificate, and trust bundle are one
inseparable recovery unit and may be pruned only after a successor's valid bundle
attests and commits their exact digests. At most one fold may await
receipts; reaching the storage bound before acknowledgement holds further security-
relevant admission/promotion rather than creating another anchor.

Provider callbacks/receipts enter only through authenticated transport plus
signature/MAC verification over canonical bytes, with exact domain, provider account,
audience, effect, request fingerprint, schema, delivery ID, key version, and freshness
binding. Durable delivery-ID replay heads reject duplicates. Reordering/conflict,
unknown schema/key, stale delivery, payload mismatch, or account/domain mismatch
becomes `AMBIGUOUS`/quarantined, never confirmed. High-impact confirmation performs
an independently authenticated provider read when available. Caller-controlled
provider/resource IDs or callback targets are not trusted.

Security-sensitive roles are separated: artifact deployment, policy/predicate
authoring, evaluation, approval, key custody, production operation, and audit
administration. No principal may author→evaluate→approve→promote the same change.
Independent dual control governs security policy, keys, provider configuration,
bridges, break-glass, and audit infrastructure. Source, dependencies, policy/schema,
migration, build provenance, and runtime artifacts are content-addressed and signed;
promotion verifies the exact independently approved `SupplyChainAttestation` and
rejects rollback/untrusted provenance.

The attestation binds a canonical deployment/configuration manifest covering
infrastructure and identity policy, provider and audit trust roots/accounts, bridge
configuration, feature flags, runtime image/arguments, network policy, and versioned
secret/key references. Promotion compares this digest with independently measured
intended and deployed state and records the measurement in its audit. Any later
drift fences affected capabilities and triggers rollback or `HOLD`; sequence alone
never proves configuration content.

Supply-chain attestations form a predecessor-linked family under exact head CAS.
`release_seq` and `configuration_epoch` increase monotonically; the domain and
operational profile both bind the current attestation head. Promotion CAS-checks
those heads and rejects a lower/equal reused sequence, stale configuration epoch,
fork, or predecessor mismatch even when the artifact is otherwise validly signed.

Each domain has quotas and fair scheduling for request bodies/fan-out, graph and
disposition depth, recurrence/projection work, disclosure accounting, dedupe/audit
growth, webhook ingress, and held/ambiguous queues. Circuit breakers, cost budgets,
queue caps, admission control, and backpressure isolate noisy neighbors. Degrade mode
is read-only or held and never skips authentication, authorization, domain isolation,
audit, privacy, deletion fencing, or non-idempotent dispatch safety.

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

## Adoption-assurance annex B — deletion and external disclosure

P1-03 is required for every adopted deployment that stores controlled data. Missing
or unknown deletion/retention policy, dependency coverage, or recovery frontier
yields `HOLD_ADOPTION`. P1-04 is required only when external disclosure is included
in the adopted workload envelope; otherwise the capability must be absent and
default-deny, not partially implemented. The P1 numbering records triage severity,
not optionality.

### P1-03 — deletion reachability and derived data

`DeletionRequest` is a stable identity whose append-only `DeletionRequestRevision`
family is the authority. `current_deletion_revision_id` is a rebuildable cache and
never a mutable source of truth: every state transition appends one revision and
CASes the exact greatest committed predecessor. Revision state is only
`PENDING | PROPAGATING | COMPLETE | HELD`; `DELETION_PENDING` is the derived
deployment/outcome class for any request not safely complete, including unknown or
conflicting policy, and is never written as a second mutable request status. Each
scope has a monotone epoch
high-water mark allocated by the separately protected deletion control plane.
The request ledger contains only an unlinkable per-deletion token. Original
`scope_kind/scope_id` routing exists solely in a scope-keyed encrypted
`DeletionRoutingEnvelope` while propagation is open. After every required verified
destruction receipt, the envelope advances `SHRED_PENDING→SHRED_CONFIRMED`; only
then may the request publish `COMPLETE` and prove that request, acknowledgement, and
audit joins cannot recover the scope; epoch enforcement uses the opaque token and
frontier root without recreating the subject join.
Command IDs, precise store IDs/watermarks, policy/registry/hold heads, recovery scan
IDs, and detailed proofs likewise live only in a scope-keyed encrypted
`DeletionOperationalSidecar` while propagation is open. Completion validation reads
and CASes those exact heads before shredding. The survivor revision/acknowledgement
records retain only unlinkable token, epoch, coarse store/proof class, acknowledgement
bit, and non-semantic ordering ordinal; any exceptional survivor needs an explicit
field-matrix proof of non-unique, non-joinable cohort membership.
Key destruction uses a recoverable external protocol, not a fictitious database/KMS
transaction. After all producers are fenced, each envelope advances
`OPEN→SHRED_PENDING`; reads/writes stay denied. One idempotent
`KeyDestructionOperation` binds key head, deletion token/epoch, and request head.
Retries query or complete that same KMS/HSM operation. Only a verified irreversible
destruction receipt permits `SHRED_CONFIRMED`, and `COMPLETE` becomes visible only
after every required receipt and store acknowledgement exists. A crash after actual
destruction but before database completion recovers from the receipt without
decrypting routing metadata. Lost, delayed, forged, or replica-stale receipts hold;
KMS restore cannot reactivate a head covered by a current destruction receipt and
deletion frontier.
Legal revision edges are `PENDING→PROPAGATING|HELD`,
`PROPAGATING→HELD|COMPLETE`, and `HELD→PROPAGATING`; resuming a hold must revalidate
policy, the frozen dependency registry, fences, and acknowledgements before
propagation continues. `COMPLETE` is terminal for that deletion request and epoch.
Later deletion allocates a new request at a strictly higher scope epoch rather than
appending a regressive successor.
Task-scope deletion reaches its revisions, occurrences, claims, artifacts,
relations, attempts, effects, projections, disclosure ledgers/debits/envelopes,
encrypted response references, caches, exports, participation confirmations, and
receipt projections; safety-minimal command
and effect receipts survive only under the field-minimization rule below. Every
create/read/derive/dispatch transaction CAS-checks every applicable scope watermark;
a stale or unknown epoch rejects writes and suppresses/quarantines reads. Late
payload cannot attach beneath a deleted identity.

Every controlled store registers deletion dependencies, handler, propagation SLA,
proof, and epoch: plan/claim payloads, artifacts, indexes, embeddings, caches,
projections, replicas, outboxes, retries, jobs, model/session contexts,
personalization, audits, backups, and recovery stores. A request freezes the
dependency-registry generation. Each acknowledgement covers a watermark after all
earlier writers, jobs, leases, and outboxes are drained or fenced. Derivatives carry
source scope/epoch; stale post-fence output is rejected or quarantined. A new handler
joins every open deletion before registry activation. A stored `COMPLETE` revision
requires all frozen-generation acknowledgements and zero unfenced producers;
otherwise the derived outcome remains `DELETION_PENDING` over a stored
`PENDING`, `PROPAGATING`, or `HELD` head.

Derived edges declare `REDACT`, `INVALIDATE`, `RECOMPUTE`, `MINIMIZE`, or
`QUARANTINE`. Missing/failed handling quarantines. Tombstones, receipts, logs, and
minimized derivatives cannot reconstruct deleted payload or enable a new purpose.

Deletion preserves append order through a redaction event while destroying payload
keys or erasing fields irreversibly. A versioned field matrix may retain only opaque
IDs/ordinals and content-minimal non-joinable receipt fields; values, source
snapshots, request fingerprints, relation endpoints, and reconstructive metadata are
destroyed or unlinkably tokenized. Projection over a redacted dependency returns
`DELETED/UNAVAILABLE` and never consults replicas or sibling records. Reconciliation
must prove surviving receipts cannot be joined to reconstruct the subject.

For core history, the only unconditional survivors are the redaction event ID,
deletion epoch, non-semantic ordering placeholder, policy/handler proof class, and
controlled-store acknowledgement state. Original task/occurrence/claim IDs,
timestamps, actors, relation endpoints, and source/payload digests survive only when
the field matrix proves them non-reconstructive; otherwise they are destroyed or
replaced by unlinkable per-deletion tokens. A survivor can prove “a slot was
redacted under policy” but not recover what the fact, plan, person, time, or relation
was. This redaction is not a semantic correction and never substitutes new history.

Before append, immutable audit material is split: the hash chain receives only a
domain-separated randomized commitment, opaque envelope ID, coarse non-semantic
event class, and coarse time bucket. Subject IDs, semantic bytes/digests, auth and
policy joins, and result data live only in a scope-keyed encrypted sidecar. Deletion
cryptoshreds that sidecar key and appends a redaction receipt bound to the audit
ordinal. Raw or stable semantic/result digests are forbidden in immutable entries or
obligations; the field matrix must demonstrate offline dictionary and cross-ledger
join resistance after shredding.

The deletion ledger/frontier is protected from backup rollback. Each domain has one
exact-CAS `DeletionFrontier` head with monotone sequence, previous hash, per-scope
epoch root, frozen registry generation, signing-key continuity, and independent
non-rollback witness receipts. Publication atomically advances the authoritative
head before detail can be pruned; crash/retry either leaves the prior head current or
returns the identical successor. Before restored data is queryable or replayable,
recovery obtains the externally witnessed current head rather than trusting the
backup's head, verifies gapless continuity/key history, scans every
store and queued event, erases/quarantines stale payload and derivatives, regenerates
indexes, and records per-store recovery acknowledgements. Missing, older,
unverifiable, or incompletely acknowledged frontiers reject promotion.

Every controlled data class requires a versioned `RetentionPolicyRevision`. Holds
are append-only revision families aggregated by one exact `RetentionHoldSetRevision`.
Every deletion transition and acknowledgement records the exact policy and hold-set
heads. `COMPLETE` is one CAS transaction over deletion head, scope epoch, registry
generation, policy head, hold-set head, and all acknowledgements; any concurrent
hold/policy change forces `HELD` or revalidation. Post-`COMPLETE` holds cannot
resurrect erased payload. Unknown or
conflicting policy yields `DELETION_PENDING`; holds are scoped/content-minimal and
their release resumes propagation. Deletion override is explicit. Permanent dedupe
retains only a non-reconstructive request token and terminal result class—not request
fingerprints or semantic/auth inputs capable of recovering deleted content.

### P1-04 — external privacy contour

External availability is default-deny. Authenticated access uses non-transferable,
principal-bound grants; anonymous/public access has no per-subject availability
unless an independently adopted public schema explicitly enumerates it. All
anonymous traffic shares one public disclosure cohort. Purpose and downstream
retention are contractual labels unless a named mechanism proves enforcement;
bearer capabilities or self-declared purpose are never proof. Queries cannot join
private planning/claim relations.

General external visitors never receive per-subject calendar blocks. When
availability is necessary, a principal-bound audience receives only fixed-bucket,
purpose-specific coarse availability with rare/unique patterns suppressed,
unlinkable pseudonyms rotated per audience/epoch, and byte-identical repeated output
within one release epoch. A visitor may receive bounded details of one event only
through a greatest-current `ACTIVE` `ParticipationConfirmation` bound to the exact
occurrence revision, authenticated participant, confirming authority/source,
allowed-detail scope, purpose/audience, validity interval, and deletion epoch.
Detail disclosure CASes that head with grant/schema/debit heads; withdrawal,
revocation, expiry, dispute, or ambiguity denies. Its bounded schema still forbids
titles, full participant lists, stable identifiers, and fine time unless a separate
necessity policy explicitly allows the individual field. Cross-audience/epoch
linkage uncertainty denies disclosure.

One atomic `DisclosureLedger` is keyed by protected subject and correlation domain
and spans every capability, identity, endpoint, schema version, cache/export, and
rolling horizon. Requests use fixed non-sliding buckets and fixed coarse values;
arbitrary windows, counts, and existence queries are forbidden. The ledger debits
before response. Anonymous requests debit the single public cohort. Coalition
detection is supplementary only: uncertain linkage/accounting, Sybil ambiguity,
cache/export bypass, or budget exhaustion denies disclosure. `consumed_budget_cache`
and `current_debit_id` are rebuildable pointers only. Every allowed response
atomically CASes the exact prior debit head, appends one immutable
`DisclosureDebit`, checks the rolling-horizon total and release epoch, and commits
the receipt before returning; concurrent requests cannot spend the same remainder.

Every disclosure grant and schema is an immutable revision family. Issue, amend,
revoke, and expire commands CAS the exact current family head; only the greatest
committed `ACTIVE` grant and schema revision are effective. A disclosure transaction
records the schema-administration `issuer_authority_head_id` in both the schema
revision and the typed authority-mutation audit, and CAS-checks that authority with
the exact schema predecessor/result. A response transaction CAS-checks the exact
active grant and schema heads together with the ledger debit head. If revocation,
expiry, schema replacement, or a competing debit wins first, no response or
externally distinguishable receipt is emitted.

`DisclosureRequestEnvelope` is unique by domain and disclosure request ID; its
canonical fingerprint covers every semantic/auth input and policy version. It,
exactly one debit, one internal response digest/reference, and one unique opaque
receipt token commit atomically before bytes become returnable. The debit's
`request_envelope_id` resolves back to that envelope and the IDs/heads must match.
Every byte-return path—including identical retry, cache hit, export, response
reference, and receipt-mediated retrieval—freshly authenticates and checks/CASes the
current grant and schema family heads, issuer-authority head, source deletion
watermark/epoch, and schema TTL. Cached bytes are non-authoritative. Only while all
heads remain effective may an identical retry return the same committed bytes
without another debit; stale/revoked/expired/deleted/unknown state returns the common
denial class and no bytes. Changed-payload key reuse rejects. The external receipt projection never
exposes the debit or envelope linkage.

Every disclosure derivative stores immutable source scope token, deletion epoch,
and deletion handler/policy binding. Completion destroys encrypted response
references and redacts or unlinkably tokenizes response digests/fingerprints unless
the deletion field matrix proves them non-reconstructive; retries and exports compare
the current source watermark before use.

Every projection uses a versioned `ExternalDisclosureSchema` allow-listing fields,
precision, TTL, audience, purpose, and correlation scope. Exact provider IDs, fine
times, stable source IDs, titles, participants, private mandates, and tombstone
existence are forbidden absent a bounded audit/dispute schema proving necessity.

Exact disclosure audit remains internal under access control. External receipts
contain only an opaque per-request token and coarse terminal class—never subject,
query/output hashes, precise time, capability/audience correlation IDs, denial
reason, or deletion/tombstone distinction. Unauthorized, nonexistent, and deleted
receipt lookups use one versioned externally observable response class and debit the
same correlation-domain ledger. Stronger indistinguishability requires the adopted
threat model and status/body/size/timing/cache/rate/repetition conformance tests.
Receipt storage binds the opaque token to the original authenticated principal,
audience, and grant family. Every lookup requires fresh authentication and current
receipt-access authority; possession of the token is never authority. Shared,
revoked, deleted, nonexistent, and unauthorized tokens take the same response and
budget path, with revocation/deletion fenced before lookup linearization.

Deletion cannot recall prior human/model consumption, irreversible effects, or
uncontrolled copies. A content-minimal inventory and receipt state these residuals
without payload. `COMPLETE` requires all controlled-store acknowledgements.

## Worked examples

### Did not swim Tuesday

Scheduling creates `occ-tue`, an exact snapshot, and
`PLAN_EXPECTED(expect-swim, occ-tue, rev-7)`. On Wednesday the user says they did not
swim. The journal appends an `UNCONFIRMED` outcome claim on
`OccurrenceSubject(occ-tue)`; policy may later confirm it. Neither state closes,
reschedules, or edits the plan. Trying Thursday requires a new command and new
revision/occurrence, with typed lineage if classified as rescheduling.

### Swam at 10, not 8

The 08:00 seed remains an expectation on its scheduled revision. A later claim says
the swim occurred at 10:00 on `OccurrenceSubject(occ-tue)`, because it reports the
realization of that existing planned occurrence; it does not rewrite the seed.
The expectation/fact divergence is visible but is not an evidence conflict. Only if
an independent admissible fact claim says the swim occurred at 08:00 do equal-
strength 08:00 and 10:00 fact claims project `DISPUTED`. A provider check-in confirms
only the check-in claim type, not that the swim happened.

### Delayed invocation after replanning

An invocation queued under `rev-12` reaches dispatch after `rev-13` changes time and
audience. It stores both revisions. Compatibility fails and the attempt remains held;
it cannot use old authority. A new command may authorize a new attempt. If a
non-idempotent attempt already crossed `DISPATCHING_NONIDEMPOTENT`, ambiguity forbids
takeover/resend; repetition requires a new effect and duplicate-risk acknowledgement.

## Task specialization traces

### Mark done, then correct the report

Prestate: `task-rev-4` is `IN_PROGRESS`. Request `req-9` atomically appends
`FactClaim(USER_REPORTED_OUTCOME, COMPLETION, value=DONE, RESULTANT_STATE,
TaskSubject(task-1, task-rev-4))` without `occurred_at` and an independently
authorized command whose CAS target is `task-rev-4` and successor is
`task-rev-5/CLOSED`. The closed card explicitly names `task-rev-4` as its outcome
subject, so its projection is `CLOSED × REPORTED_DONE`. A later correction says
the report was mistaken by appending a same-key replacement with `value=NOT_DONE`:
projection becomes `CLOSED × REPORTED_NOT_DONE` (or
`DISPUTED` under policy), never reopened. Only a new exact-revision reopen command can
create `task-rev-6/PLANNED`. Forbidden: treating either atomic record as authority or
evidence for the other. Vectors: TV-36–38.

### Deadline passes untouched

Prestate: exact open `task-rev-7/PLANNED`, due at 17:00 in its recorded timezone. At
17:01 the view reads `PLANNED × UNKNOWN × OVERDUE`. No claim, command, attempt, or new
revision is appended. Forbidden: inferring not done, attempted, cancelled, satisfied,
or closed. Vector: TV-39.

### Provider checkbox checked, user says not done

The provider snapshot confirms only `PROVIDER_CHECKBOX_CHECKED` on the exact item.
The user appends `USER_REPORTED_OUTCOME × COMPLETION, value=NOT_DONE` on the exact
occurrence, so the checkbox
alone does not contest that outcome and the view projects `REPORTED_NOT_DONE`. Only
a versioned mapping policy may derive an outcome claim from provider state; if it
does, it must emit the same canonical outcome claim type/slot with polarity in
`value=DONE|NOT_DONE|PARTIAL`; if the two outcome claims are equal-strength,
projection is `DISPUTED`.
Planning is unchanged in either case. Forbidden: checkbox-to-completion promotion or
latest-arrival precedence. Vectors: TV-40–41.

### Partial subtask

A child occurrence has an admissible `PARTIAL` outcome claim. Its view may show
`IN_PROGRESS × PARTIAL`; the parent revision and all siblings remain unchanged.
Closing the child still requires its own command, and closing the parent requires a
parent-scoped command and satisfaction policy. Forbidden: part-to-whole discharge.
Vector: TV-42.

### Recurring occurrence skipped

An authorized skip command CAS-targets one exact occurrence revision and appends its
`SKIPPED` `TaskOccurrenceRevision` successor under the same occurrence identity. The
`TaskSeries`, its active `RuleRevision`, and other occurrences stay
open and unchanged. Forbidden: one skipped occurrence closing/cancelling the series
or rewriting the rule. Vector: TV-43.

### Delegate reports complete

The delegate appends a typed completion report on the delegated exact subject. The
delegated task projects that report under evidence policy, but the delegator
obligation and structural parent remain open. Separate exact-scope commands and any
required counterparty acceptance are needed to close either. Forbidden: evidence-
driven delegation discharge. Vector: TV-44.

### Spontaneous atemporal completion

For an existing `task-8/task-rev-2`, the user appends a completion claim on
`TaskSubject(task-8, task-rev-2)` with `RESULTANT_STATE` semantics and no
`occurred_at`. Projection may become `PLANNED × REPORTED_DONE`,
but planning remains open until a separate authorized close command. If no task
or planned occurrence existed, or if the report explicitly distinguishes an episode
from its planned slot, the event receives a journal-native `WorkOccurrence`; a
linked episode may evidence the task-state claim but never closes the task. An event
that instead realizes one existing planned occurrence uses its `OccurrenceSubject`.
The system does not back-create a task or expectation.
Forbidden: retroactive normative history or event fields on `TaskSubject`.
Vector: TV-45.

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

## Limitations and open decisions

The core incorporated into the vision does not choose claim registries, source-admissibility, strength or
conflict policies, the exact `DUE` boundary convention, recurrence horizons,
compatibility details, partial-
outcome aggregation, parent/delegator satisfaction policy, retention periods,
propagation SLAs, privacy budgets, recovery operators, audit audiences, or user
vocabulary. Selective incorporation into the product vision does not prove economy, implementation,
migration feasibility, operational adoption, or external verification. Missing
policy fails closed; assurance adoption remains an author decision.

## Negative conformance vectors

`REJECT` means no commit; `HOLD` means no invocation or operative/material planning
transition; `FAIL_CLOSED` excludes the affected data/result. Under the proposed
triage gate, passing these vectors would be necessary, not sufficient, for adoption.

| ID | Guarantee | Negative input | Expected outcome |
|---|---|---|---|
| TV-01 | Planning authority | Append confirmed completion and ask a projector to close its plan. | `REJECT`: require a new validated command/revision. |
| TV-02 | Commit ordering | Choose current plan by journal arrival/time. | `REJECT`: use accepted `commit_seq`. |
| TV-03 | Atomic commit | Persist revision without command audit or commit sequence. | `REJECT/rollback`. |
| TV-04 | Plan/fact | Treat `PLAN_EXPECTED` as completion. | `REJECT`. |
| TV-05 | Typed confirmation | Promote provider execution to real-world outcome. | `REJECT`. |
| TV-06 | Conflict | Let later equal-strength conflicting claim win. | `REJECT` the precedence result; render `DISPUTED` in evidence projection. Planning is unchanged absent a separate command or policy. |
| TV-07 | Immutable claim | Edit source, subject, or claim in place. | `REJECT`; append correction/retraction. |
| TV-08 | Subject union | Put `EVENT` semantics or `occurred_at` on `TaskSubject`, or encode a concrete episode without an occurrence subject. | `REJECT`; use the uniquely resolved planned `OccurrenceSubject`, otherwise a journal-native `WorkOccurrenceSubject`, and preserve the task-state claim separately if needed. |
| TV-09 | Occurrence | Delete on cancel, mutate occurrence state in place, or reuse ID on reschedule. | `REJECT`; append an occurrence revision or create typed lineage as appropriate. |
| TV-10 | Retry | Mint occurrence for provider retry. | `REJECT`: create attempt. |
| TV-11 | Seed uniqueness | Create a second seed for the same subject/revision/semantic expectation key by minting a fresh client or expectation ID. | `REJECT`; identity is deterministically derived from the unique semantic tuple. |
| TV-12 | Old seed | Rewrite seed after later revision. | `REJECT`; append new seed. |
| TV-13 | TaskSeries | Use series as occurrence or close series from one claim. | `REJECT/HOLD`. |
| TV-14 | Recurrence key | Materialize same series/rule/logical key twice. | `REJECT`. |
| TV-15 | Recurrence CAS | Stale expander commits after active rule changes. | `REJECT` transaction. |
| TV-16 | Rule edit | Apply new rule to materialized occurrences. | `REJECT`; explicit commands required. |
| TV-17 | Compatibility | Invoke after protected authority dimension changes. | `HOLD`. |
| TV-18 | Atomic attempt | Accept attempt before compatibility commit. | `REJECT/rollback`. |
| TV-19 | Receipt ladder | Infer tool confirmation from acceptance or outcome from tool. | `REJECT`. |
| TV-20 | Idempotency | Retry with different provider/resource/effect key. | `REJECT`. |
| TV-21 | Ambiguity | Take over/resend non-idempotent ambiguous effect. | `HOLD`. |
| TV-22 | Manual repeat | Repeat without new authority/duplicate-risk acknowledgement. | `REJECT`. |
| TV-23 | Acyclicity | Create a claim or occurrence-lineage edge to an equal/newer ordinal, missing target, wrong edge type, or any cycle. | `REJECT` transaction. |
| TV-24 | Late evidence | Move late evidence to latest occurrence/revision. | `REJECT`. |
| TV-25 | History | Later plan deletes/redefines old claim/occurrence. | `REJECT`. |
| TV-26 | Parent closure | Close parent from child/delegate evidence. | `HOLD`; parent command required. |
| TV-27 | Authority | Reactivate revoked grant because claim remains. | `REJECT/HOLD`. |
| TV-28 | Narrative | Mutate via view ID or compute plan from claims. | `REJECT`. |
| TV-29 | Deletion | Report complete with stale/unknown store epoch. | `FAIL_CLOSED`; pending. |
| TV-30 | Recovery | Restore before applying deletion epochs. | `REJECT` promotion. |
| TV-31 | Derivation | Missing handler or failed recomputation. | `FAIL_CLOSED`: quarantine. |
| TV-32 | Receipt privacy | Emit forbidden exact fields without bounded schema. | `REJECT`. |
| TV-33 | Reconstruction | Join capability to private claims or evade coalition budget. | `FAIL_CLOSED`. |
| TV-34 | Narrative epistemics | Flatten branches/status or hide differing occurred/observed times. | `REJECT`; render each branch with one exact `NarrativeEpistemicStatus`, both differing timestamps, and `DELETED_UNAVAILABLE` without payload when redacted. |
| TV-35 | Reliance | Turn suspected/reported reliance into obligation/release. | `REJECT`. |
| TV-36 | Exact transition CAS | Two close/reopen commands validated against the same current revision both commit. | `REJECT` the stale target; exactly one successor may win. |
| TV-37 | Mark-done independence | Use the outcome claim to authorize close, or the close command to confirm outcome. | `REJECT`; records are atomic in visibility but semantically independent. |
| TV-38 | Correction/reopen | Retract or correct the done claim and silently reopen planning. | `REJECT`; explicit exact-revision reopen command required. |
| TV-39 | Deadline semantics | Turn due/overdue arrival into done, not-done, attempt, cancellation, satisfaction, or a persisted transition. | `REJECT`; compute only the exact-open clock projection. |
| TV-40 | Provider checkbox | Map checked provider state directly to task completion. | `REJECT`; require separate registered claim mapping/evidence policy. |
| TV-41 | Evidence precedence | Let the later checkbox or user claim win without policy, or hide an equal-strength conflict. | `REJECT` the precedence result; render `DISPUTED` in evidence projection. Planning is unchanged absent a separate command or policy. |
| TV-42 | Partial child | Close a child or parent because a subtask has a partial/completion claim. | `HOLD`; exact-scope commands required. |
| TV-43 | Recurring skip | Close/cancel series or rewrite its rule when one occurrence is skipped. | `REJECT`. |
| TV-44 | Delegate report | Discharge delegator/parent from delegate-reported completion. | `HOLD`; exact-scope command and acceptance policy required. |
| TV-45 | State/episode identity | Back-create a task or expectation for unplanned work; put event semantics on `TaskSubject`; mint `WorkOccurrence` for the unique realization of an existing slot; or remint a journal-native episode. | `REJECT`; use exact `TaskSubject` only for resultant state, the resolved `OccurrenceSubject` for one planned realization, and stable `WorkOccurrence` only for an unresolved, independent, or additional episode. |
| TV-46 | Outcome subject | Silently display a predecessor claim as if it were scoped to an arbitrary current/reopened revision. | `REJECT`; expose the exact outcome subject and keep historical scope. |
| TV-47 | Plan subject | Attach `PlanExpectation` to journal-only `WorkOccurrenceSubject`, or pair a task/occurrence subject with another task's revision. | `REJECT`; expectations require a same-task `PlanSubject`. |
| TV-48 | Occurrence audit | Commit an occurrence revision without typed exact CAS target/result audit bindings. | `REJECT/rollback`. |
| TV-49 | Episode evidence | Let a linked `WorkOccurrence` automatically confirm or close its task, parent, delegator, or series. | `REJECT`; apply explicit evidence policy and require independent exact-scope planning commands. |
| TV-50 | Planned versus journal-native event | Attach one event to both its uniquely resolved planned occurrence and a newly minted `WorkOccurrence`, or force an independent/additional episode onto the planned subject. | `REJECT`; exactly one allocation branch owns the event subject, while typed links may relate distinct identities. |
| TV-51 | Occurrence provenance | Rebind an existing occurrence's `scheduled_task_revision_id` to a later task revision, or present later authorization as original scheduling provenance. | `REJECT`; provenance is immutable, and future authorization uses a separate validated command/compatibility decision. |
| TV-52 | Provider idempotency contract | Retry with a changed request fingerprint, outside the verified key scope/window, or under unknown provider dedupe state. | `HOLD`; no automatic retry or safety claim. |
| TV-53 | Mark-done visibility | Publish claim or planning successor without the same committed envelope/serializable commit authority. | `REJECT/rollback`; partial visibility is forbidden. |
| TV-54 | Expired request dedupe | Replay a Mark-done request after its request mapping expired or became unknown. | `FAIL_CLOSED`; require retained result or a new explicit operation, never infer a safe replay. |
| TV-55 | Dispatch-time authority | Revoke/cancel/replan after attempt acceptance but before dispatch authorization. | `HOLD`; dispatch transaction loses the head CAS and no provider call occurs. |
| TV-56 | Request-key collision | Reuse one scoped request ID with changed fingerprint, or execute two concurrent identical requests twice. | `REJECT` mismatch; identical callers await/return the single committed result. |
| TV-57 | Occurrence deadline source | Compute occurrence DUE/OVERDUE from a current task revision, series rule, or ambient timezone instead of its immutable scheduled due spec. | `REJECT`; use the exact occurrence field and boundary policy. |
| TV-58 | Series-wide recurrence key | Materialize a previously materialized/skipped/rescheduled semantic slot under a new rule revision. | `REJECT`; `(series_id, logical_recurrence_key)` is unique across revisions. |
| TV-59 | Rule boundary/key policy | Commit overlapping or gapped rule intervals, or derive distinct keys for one DST/boundary slot. | `REJECT`; require the versioned deterministic key policy and gap-free half-open partition. |
| TV-60 | Atomic reschedule | Create a replacement without atomically terminalizing the exact predecessor, or create two replacements. | `REJECT/rollback`; one CAS command owns both sides. |
| TV-61 | Atomic expansion | Advance/regress the horizon without the complete deterministic interval, or expose partial inserts. | `REJECT/rollback`; reconcile to the same complete result or abort. |
| TV-62 | Deletion scope watermark | Attach/read payload under a stale, unknown, or logically deleted scope epoch. | `REJECT` write; suppress/quarantine read. |
| TV-63 | Deletion propagation fence | Acknowledge before fencing earlier jobs/outboxes, activate a new handler outside an open deletion, or accept stale post-fence output. | `FAIL_CLOSED`; deletion remains pending and output is quarantined. |
| TV-64 | Recovery frontier | Promote a backup with missing/older/unverifiable deletion frontier or an unacknowledged restored dependency. | `REJECT` promotion. |
| TV-65 | Redaction reconstruction | Retain/join payload, fingerprints, endpoints, or tombstones sufficient to reconstruct a deleted subject. | `FAIL_CLOSED`; destroy/tokenize fields and keep deletion pending. |
| TV-66 | Retention conflict | Apply unknown/conflicting retention or a broad hold that preserves deletable semantic payload. | `FAIL_CLOSED`; `DELETION_PENDING` until scoped policy resolution. |
| TV-67 | External grant | Share a capability, assert a false purpose, or rely on a retention label as technical enforcement. | `REJECT`; require principal-bound authority and treat unenforced labels as contractual only. |
| TV-68 | Disclosure composition | Reset budget across two capabilities/schema versions, use sliding windows, bypass through cache/export, or split among Sybils. | `FAIL_CLOSED`; debit one correlation-domain ledger and public cohort before response. |
| TV-69 | Availability linkage | Publish stable/rare per-subject blocks or combine them with an auxiliary calendar to identify the owner. | `REJECT`; fixed coarse audience-bound buckets, suppression, unlinkable epoch pseudonyms, or no disclosure. |
| TV-70 | Receipt oracle | Enumerate receipts, dictionary-attack hashes, or distinguish deleted/nonexistent lookup through status/body/size/timing/cache/rate/repetition behavior. | `FAIL_CLOSED`; opaque coarse receipt, one adopted external response class, and the same correlation budget. |
| TV-71 | Acceptance/delegation heads | Reuse a stale satisfaction/delegation, or let close/delegate/dispatch win after a withdrawal/revocation head committed first. | `REJECT/HOLD`; CAS exact active family heads and require greatest-committed effective status. |
| TV-72 | Attempt transition graph | Commit an unlisted attempt/effect edge, resend from non-idempotent ambiguity, or mutate compensation onto the original effect. | `REJECT`; use the mode-specific edge table and a separately authorized compensation effect. |
| TV-73 | Adoption profile | Adopt or roll out with a missing, stale, partial, or unapproved `OperationalAdoptionProfile`. | `HOLD_ADOPTION`; logical conformance cannot waive operational evidence. |
| TV-74 | Migration authority | Run old/new writers as simultaneous authorities, silently map ambiguity, roll back across irreversible effect, advance another cohort after evidence/approval/attestation drift, or race rollout with rollback. | `REJECT/HOLD`; one environment-head state machine, current gates each phase, quarantine ambiguity, and honor rollback boundary. |
| TV-75 | Stuck-state operations | Resolve hold/ambiguity/deletion/recovery by ad-hoc data edit, missing owner/evidence, or single-person duplicate-risk override. | `REJECT`; use the authorized queued runbook and required four-eyes path. |
| TV-76 | Unsafe scale degradation | Meet load by weakening ordering, authority, privacy, deletion fencing, or non-idempotent dispatch invariants. | `REJECT`; backpressure/admission control or halt inside the approved profile. |
| TV-77 | Operational evidence gate | Promote/advance with omitted/failed/expired evidence, stale heads, revoked approval, mismatched frontier, role aliasing, self-evaluation, approver-as-evaluator, or authority change after seal. | `HOLD_ADOPTION`; CAS exact current evidence/deployment heads and enforce the sealed per-obligation independence matrix. |
| TV-78 | Security-domain isolation | Substitute/cross-link an ID, queue message, cache/index key, blob, provider resource, export, backup, or operator request across domains without an active exact bridge. | One adopted external rejection class; zero mutation/disclosure and no accepted cross-domain edge. Stronger indistinguishability is tested only under the adopted threat profile. |
| TV-79 | Authentication/credential fencing | Override actor/domain, replay wrong-session/audience request, downgrade/expire step-up, or revoke/compromise credential before commit/dispatch/callback/seal/promotion. | `REJECT/HOLD`; only operations past their declared linearization boundary may survive. |
| TV-80 | Audit integrity | Mutate/delete/reorder/duplicate/truncate/fork/rollback an audit or checkpoint, forge actor attribution, or rotate/lose keys without continuity. | Detect and mark domain compromised; block restore/promotion. |
| TV-81 | Provider ingress | Forge/tamper/replay/reorder a callback; bind it to another domain/account/effect; use stale/unknown schema/key or mismatched payload. | `REJECT` or `AMBIGUOUS`/quarantine; never confirm outcome or disclose cross-domain state. |
| TV-82 | Insider/supply-chain separation | Let one principal author/evaluate/approve/promote; deploy unsigned/rolled-back source, dependency, policy, migration, key, or provider configuration. | `REJECT` promotion; require independent roles and exact signed attestation. |
| TV-83 | Resource abuse | Exhaust graph depth, recurrence, projections, webhooks, queues, audit/dedupe, or shared capacity and bypass a protected check to recover throughput. | Bound/isolate work; fair backpressure/read-only/HOLD degradation, never invariant bypass. |
| TV-84 | Authentication replay | Replay a captured nonce/context with identical or changed request across session/service boundaries or after an aborted reservation. | `REJECT`; only a matching explicitly idempotent committed envelope may return its prior result, never execute again. |
| TV-85 | Audit-anchor fold | Fold with an epoch gap/overlap/reorder, omit prior cumulative commitment, overflow count, fork concurrent successors, prune before a valid semantic quorum certificate, or restore from a non-current/under-quorum anchor. | `REJECT/HOLD`; one cumulative CAS successor and independently recomputed transition certificate preserve inherited start, additive total, and ordered root before pruning. |
| TV-86 | Commit-ordinal scope | Compare equal numeric `commit_seq` values across domains, select a cross-domain greatest revision, or omit domain from a revision/edge/audit key. | `REJECT`; compare only complete same-domain `CommitOrdinal` values. |
| TV-87 | Bridge lifecycle | Use a stale/expired/revoked bridge or authority head, fork bridge successors, or let a cross-domain operation commit after revocation won the head CAS. | Same adopted external rejection class; exact bridge/authority heads serialize the race. Stronger indistinguishability requires its threat-profile conformance suite. |
| TV-88 | Action/audit atomicity | Make a protected action visible, dispatch an effect, or promote/override after audit append failure or with a missing/mismatched obligation. | `HOLD/rollback`; exact audit entry or recorded durable obligation is required before visibility/dispatch. |
| TV-89 | Typed planning-command provenance | Omit a consumed authority head or authority-family result, use an input variant inconsistent with `command_type`, invent a predecessor for creation, or pass an unregistered opaque input schema. | `REJECT`; the closed variant, family kind, exact predecessor/result heads, protected action, audit, and result must agree atomically. |
| TV-90 | Stable-append ordinal scope | Compare equal numeric `stable_append_seq` values across domains, attach a cross-domain claim edge, or use a bridge to choose a shared claim head. | `REJECT`; compare only complete same-domain `StableAppendOrdinal` values. |
| TV-91 | Deletion revision authority | Mutate request state in place, store `DELETION_PENDING` as a competing status, advance from a stale head, regress from `COMPLETE`, or complete directly from `HELD`. | `REJECT`; use the legal append/CAS graph, resume propagation after revalidation, and allocate a higher epoch for later deletion. |
| TV-92 | Disclosure revision and debit heads | Disclose using stale/revoked grant, schema, or issuer authority; mutate budget directly; spend one remainder twice; return bytes without the unique envelope/debit/receipt binding; or retry one ID with changed input. | `FAIL_CLOSED`; CAS exact heads and atomically commit one matching envelope, debit, response digest, and opaque token before response. |
| TV-93 | Task normative revision boundary | Store or change purpose, obligated outcome, due semantics, or planning state on `Task` without a new `TaskRevision`. | `REJECT`; `Task` is identity/cache only and every normative change is revisioned. |
| TV-94 | Correction admissibility ordering | Apply an authorized correction from an admissible claim to a replacement that is inadmissible or has an absent/unknown policy; then mutate each authority, admissibility, and precedence family after append and replay history. | Preserve the target contribution and render `UNRESOLVED_POLICY`/`DISPUTED`; replay uses the immutable recorded snapshot and later policy cannot erase or reinterpret it. |
| TV-95 | Occurrence transformation identity | Retry split/merge/recreate with a changed graph/provenance, omit source terminalization, use stale source revisions, merge incompatible task revisions/series keys, omit result provenance, or race overlapping transformations. | `REJECT` or return the identical retained transformation; exact policy, paired sources, full results, recurrence uniqueness, and one atomic graph are required. |
| TV-96 | Structural-parent lifetime | Attach structural ownership to a claim/occurrence, drop/copy it implicitly on task revision, or close a parent from child evidence. | `REJECT/HOLD`; use the identity-level revisioned edge and explicit parent-scoped validation. |
| TV-97 | Work-occurrence envelope | Commit an episode without its typed envelope/creation claim, retry into different linkage or IDs, or recover only one result ID. | `REJECT/rollback`; envelope, episode, and creation claim form one domain-scoped atomic result. |
| TV-98 | Dispatch-outbox fencing | Let two workers call with one effect, accept a stale fencing token, re-lease non-idempotent work after `SEND_STARTED`, or reclaim idempotent work outside its verified dedupe window. | `REJECT/HOLD`; exact outbox-head CAS and fencing permit only the mode-safe recovery branch. |
| TV-99 | Provider-ingress atomicity | Make an acknowledgement visible after writing only the receipt, outbox, effect, attempt, or claim component; retry the partial callback into a second result. | `REJECT/rollback` or hide behind one recoverable ingress envelope; exactly one complete identical transition becomes visible. |
| TV-100 | Late ambiguity resolution | Commit `AMBIGUOUS`, then deliver a valid matching callback or authorized reconciliation result; attempt to resolve by resend or an unauthenticated claim. | One evidence-only CAS resolution commits atomically with zero provider resend; invalid resolution rejects and identical retry returns the same result. |
| TV-101 | Task transition graph | Attempt an unlisted edge from `CLOSED`, `CANCELLED`, or `SKIPPED`, or use ordinary update instead of the registered reopen/replan command. | `REJECT`; exact recorded transition-policy head and command-indexed edge are mandatory. |
| TV-102 | Deadline replay | Change ambient timezone, tzdb, fold/gap rule, or due-boundary policy after scheduling and recompute the old task/occurrence deadline. | Prior projection is unchanged and reads only its immutable `DueSpec`; missing derivation provenance rejects scheduling/materialization. |
| TV-103 | Outcome polarity key | Correct `DONE↔NOT_DONE`, or map opposed equal-strength provider/user reports using different completion claim types. | Polarity changes value on one canonical outcome key; correction reduces there and equal-strength opposition follows the registered conflict policy. |
| TV-104 | Delegation axis | Partially delegate, add two disjoint delegates, withdraw one scope, or delegate one exact occurrence while attempting to change the whole lifecycle state. | Delegation projection changes only for affected scopes; lifecycle state and other delegations remain unchanged absent separate authorized commands. |
| TV-105 | Rule-edit/expansion race | Expand under the old rule while an edit classifies a stale horizon/materialization set. | Exactly one shared series-token CAS wins; the loser re-scans and retries, leaving one gap-free authoritative coordinate partition. |
| TV-106 | Recurrence coordinate stability | Change timezone, calendar, fold/gap, or key policy in place, or order intervals by encoded key bytes. | `REJECT`; use the immutable series coordinate contract or an atomic verified migration with mapped/tombstoned dispositions. |
| TV-107 | Recurrence transformation ownership | Reschedule onto an existing slot, split one recurring slot, merge two slots, transform a skipped slot, race transformation with expansion, or rebuild/replay while trying to reuse/rematerialize a source key. | Persisted tagged occurrence/disposition bindings preserve source tombstones and at most one policy-selected exception inheritor; collision or stale-token race rejects atomically. |
| TV-108 | Delegation-set serialization | Concurrently create overlapping delegation families or consume authority while another family changes without CASing the shared subject set. | One set-head order wins; conflicting scope is rejected or non-operative `HELD`, never briefly effective. |
| TV-109 | Delegation acceptance binding | Reuse acceptance across amended delegation, another delegate, or another task/occurrence revision; race acceptance with withdrawal/substitution. | `REJECT/HOLD`; exact delegation, subject, scope, acceptance, and shared-set heads must all remain active. |
| TV-110 | Arrangement/reliance authority | Infer obligation from context, mix arrangement/commitment heads, omit/replace a relying party, leave suspicion unresolved, race terminal outcomes, or concurrently start two post-terminal families. | Context creates at most expiring `SUSPECTED`; one aligned head pair, assessment head, and current-family lineage CAS win; terminal outcomes stay distinct and obligations are preserved. |
| TV-111 | Audit deletion resistance | Put raw/stable semantic/result digests or protected-linearization ID in immutable entry/obligation, then delete and dictionary-attack or join the chain. | `REJECT` append; only randomized opaque tokens/commitment remain, sidecar key is shredded, and offline recovery/join fails. |
| TV-112 | Deletion-frontier freshness | Restore a self-consistent old backup with its old valid signed frontier. | `REJECT` promotion until the externally witnessed current head, continuity, scan, and every store acknowledgement are verified. |
| TV-113 | Deletion-ledger join | Retain plaintext scope identity or traverse revision→command audit and acknowledgement→policy/hold/recovery through precise metadata after `COMPLETE`. | `FAIL_CLOSED`; only policy-approved coarse non-joinable token/epoch/proof class survives and routing/operational keys are cryptoshredded. |
| TV-114 | Retention-hold race | Issue/release/expire/override a hold or change policy while propagation/acknowledgement/completion uses stale heads. | Completion CAS loses and becomes `HELD`/revalidation; post-complete hold cannot resurrect data. |
| TV-115 | Key-shredding linearization | Crash before/after destruction intent, actual KMS destruction, receipt persistence, sidecar confirmation, and completion; delay/forge a receipt or restore a stale KMS replica. | Reads stay fenced; identical operation recovers from verified receipt; `COMPLETE` appears only after every irreversible destruction proof and acknowledgement. |
| TV-116 | Disclosure replay revocation | Retry/cache/export committed bytes after grant/schema/issuer revocation, TTL expiry, or source deletion. | Common denial class and zero bytes; every return path revalidates current authority, epoch, and TTL. |
| TV-117 | Disclosure deletion reachability | Delete a source while debit/envelope/response reference/cache/export/receipt survives without source epoch binding. | `FAIL_CLOSED`; every derivative is fenced, reference destroyed, and reconstructive digest/fingerprint redacted before completion. |
| TV-118 | Participant-detail authority | Infer participation from context, reuse confirmation across event revision/audience, or race detail response with withdrawal/revocation/dispute. | `REJECT`; exact greatest active confirmation and bounded field schema must win the shared CAS. |
| TV-119 | Receipt bearer token | Share or leak an opaque receipt token, then lookup without the original principal/current grant or after deletion. | Same external denial/budget path as nonexistent; possession alone reveals no receipt class or existence. |
| TV-120 | Authentication-context integrity | Forge/signlessly import a context, splice fields, inflate strength, revoke issuer, or mismatch channel/request. | `REJECT`; canonical signature/MAC and every current issuer/policy/key/session head must verify. |
| TV-121 | Bilateral bridge consent | Create/amend bridge unilaterally, use stale approval from one endpoint, swap direction/counterparty, or race operation with either revocation. | Zero cross-domain effect unless bridge and both exact endpoint approvals win; either endpoint fences new work. |
| TV-122 | Audit nonrollback witnesses | Restore a validly signed stale checkpoint, provide empty/under-quorum receipts, fork/equivocate witnesses, or make current-head quorum unavailable. | Domain is `COMPROMISED`; no promotion/pruning until authoritative diverse quorum and continuity resolve. |
| TV-123 | Runtime configuration attestation | Substitute trust root, provider account, secret reference, flag, network/runtime argument, or drift after verification while retaining artifact and epoch. | `REJECT/HOLD` or rollback; attested manifest must equal independently measured intended/deployed state. |
| TV-124 | Deployment phase authority | Let concurrent controllers advance/rollback, continue after gate revocation/drift, omit cohort/irreversible boundary, or use a stale environment generation. | `HOLD`; exactly one deployment-head CAS wins and every phase revalidates all current gates. |
| TV-125 | Evidence role independence | Let author/producer evaluate its obligation, approver evaluate any contributing item, promoter alias another role, or change principal-family heads after seal. | `HOLD_ADOPTION`; exact independence digest and current role heads must satisfy the policy at append, seal, approval, and deployment. |
| TV-126 | Separation digest identity heads | Advance an author/producer identity-family head after profile creation or evidence sealing while reusing the old digest/approval. | `HOLD_ADOPTION`; digest uses exact stored creation heads and deployment requires freshly sealed evidence under current admissible heads. |
| TV-127 | Natural-language authority | Interpret ambiguous language or proactive suggestion as silent commit; generate from a paused/cancelled/stale rule; race rule amendment with generation. | Clarify or create a labeled proposal; exact active rule-head CAS wins; inferred/defaulted fields and generated proposals supply no commit/dispatch authority. |
| TV-128 | Journal actionability | Render `CONFIRMED` as objective truth, hide exact subject/source/policy scope, flatten provider/user conflict, or make “correct done” reopen the task. | `REJECT` view; branch explains epistemic ceiling, lineage, unchanged planning, and separate permitted correction versus plan actions. |
| TV-129 | Bridge domain exception | Order a bridge family by either endpoint commit sequence, resolve an endpoint approval in the wrong domain, or use bridge privilege for an unrelated foreign key. | `REJECT`; bridge-local head/sequence and exact bilateral scoped approvals are the sole cross-domain exception. |
| TV-130 | Closed planning-revision authority | Let a claim directly mutate a rule, grant, delegation, acceptance, commitment, or arrangement; or commit one without its typed revision/audit. | `REJECT`; every normative family is a `PlanningRevision` member changed only by an authorized command. |
| TV-131 | Compatibility policy head | Accept/dispatch using missing, stale, revoked, expired, or concurrently amended compatibility policy, or omit the exact head from audit. | `HOLD`; exact active family head and every protected dimension must win both CAS points. |
| TV-132 | NL committed-result union | Mark fact-only interaction committed without claim IDs, bind it to planning command, or retry into another result variant. | `REJECT/rollback`; state and one typed immutable result must match and retry identically. |
| TV-133 | Series revision authority | Mutate horizon/rule/timezone/calendar/key policy/state in place or expand from a stale series head. | `REJECT`; append and CAS exact `TaskSeriesRevision`; replay reads its immutable snapshot. |

## Positive concurrency and fault-injection vectors

| ID | Exercise | Required observable postcondition |
|---|---|---|
| PV-01 | Two task commands CAS the same current revision. | Exactly one command/revision has the winning `commit_seq`; loser is rejected and no partial records exist. |
| PV-02 | Crash before, during, and after `AtomicRequestEnvelope` commit. | Before/during: neither projection visible or one recoverable committed envelope authority; after: exactly one claim and planning result, identical retry returns it. |
| PV-03 | Race cancellation/revocation and two workers against dispatch authorization; crash each worker before/after claim, `SEND_STARTED`, socket write, and acknowledgement; inject stale fencing tokens. | Exactly one linearization order and current fencing token win. Non-idempotent mode makes at most one provider call and becomes bounded `AMBIGUOUS` after any post-start uncertainty; idempotent reclaim uses the same key/fingerprint only inside the verified window; every state recovers or holds without duplicate authority. |
| PV-04 | Two expanders claim the same recurrence interval and one crashes mid-build. | One complete gap-free series-wide key set and monotone horizon become visible; no partial interval or duplicate key. |
| PV-05 | Deletion begins with active job/outbox and a handler registration race. | Stale output quarantined; new handler cannot activate outside request; `COMPLETE` only after frozen-generation acknowledgements and zero unfenced producers. |
| PV-06 | Restore an old backup beneath a newer signed deletion frontier. | No query/replay before full scan; stale payload removed/quarantined, indexes regenerated, every store acknowledged before promotion. |
| PV-07 | Traverse every listed attempt/effect edge in both dispatch modes, including late evidence after ambiguity, and inject each receipt/failure. | Each permitted edge commits once under predecessor CAS; every unlisted edge is rejected; non-idempotent ambiguity has no dispatch/retry/takeover exit, and only authenticated evidence-only resolution is permitted. |
| PV-08 | Execute migration shadow/cohort/abort/rollback with competing controllers; revoke/drift every gate between cohorts; alias each role pair; then run overload, stuck-state and restore drills. | One environment-head transition wins; every later phase revalidates current gates and independence; incomplete/stale/aliased trials hold; rollback respects irreversible boundary and overload preserves invariants. |
| PV-09 | Run domain-substitution property tests, credential revocation at every boundary, audit tamper/restore trials, webhook forgery/replay/reorder, compromised artifact/role-chain attempts, and per-domain amplification floods. | No cross-domain edge/disclosure; only pre-boundary effects survive revocation; every ledger fault detected; no false provider confirmation; untrusted artifact/one-role chain cannot promote; healthy domains retain declared bounds while attackers are held. |
| PV-10 | Race two anchor folds; crash before/after pointer advance and certificate quorum; omit/reorder one summary; forge/collude below quorum; force count-width overflow; restore with only current anchor plus certificate. | Exactly one successor wins; no premature pruning; omissions/reordering/under-quorum/overflow hold; semantic attestations independently recompute and bind inherited start, exact total, ordered extension, and output anchor. |
| PV-11 | Issue one principal-bound external disclosure grant and schema revision, race two authorized fixed-bucket requests against the remaining budget, repeat within the release epoch, then revoke the grant or replace the schema while another request is pending. | Exact grant/schema/debit-head CAS permits only budget-fitting responses; one immutable debit exists per returned response; repeated output is byte-identical; the losing/stale request discloses nothing; receipt lookup remains opaque and debited; no private relation or cross-epoch linkage appears. |
| PV-12 | Concurrently create overlapping and disjoint delegation families, accept exact scopes, race action/acceptance against amendment/withdrawal/substitution, and separately report delegate completion without a parent command. | Shared delegation-set CAS admits only a coherent operative set; exact acceptance heads bind each scope; withdrawal-first holds stale action, linearization-first remains auditable; delegate evidence never closes delegator/parent. |
| PV-13 | Concurrently reserve denial leases; crash before and after bucket accounting; expire one reserved lease; race saturation and ordinary rollover fences; compact an accounted range before/after checkpoint and replay its signed token. | Every admitted ingress sequence is counted exactly once with no gaps/overlaps; expired lease is deterministically recovered; no post-fence lease enters old window; one successor wins; signed bucket/range/checkpoint chain remains continuous across crash/compaction; valid in-horizon replay returns prior denial without recounting. |
| PV-14 | Commit equal numeric sequences independently in two domains, then race commands within each domain and attempt cross-domain selection/edge insertion. | Each domain has one independent winner/current head; equal numeric values never compare; cross-domain selection/edge fails absent a bridge and remains ordinal-independent with one. |
| PV-15 | Create an authorized bridge, race cross-domain read/write/disclosure/dispatch against bridge expiry/revocation and referenced-authority revocation, then retry both outcomes. | Exactly one head order wins; revocation-first discloses/mutates nothing, operation-first leaves one bounded audited result; retries never reuse stale scope. |
| PV-16 | Crash before, during, and after protected action plus audit/obligation commitment for dispatch, bridge mutation, deletion override, promotion, and high-impact denial; make audit storage unavailable. | No action is visible/dispatched without one exact chained entry; recoverable obligations yield one idempotent entry; unavailable audit holds; no gaps, duplicates, or mismatched opaque-envelope/randomized-commitment bindings. |
| PV-17 | Crash after each provider-ingress component write and retry the authenticated callback concurrently. | Either no provider transition is visible or one durable envelope yields the same complete receipt/outbox/effect/attempt/claim result; no partial authoritative state or duplicate claim survives. |
| PV-18 | Fault-inject every key-destruction boundary, KMS timeout/duplicate response, forged receipt, and stale key replica during deletion. | No plaintext becomes readable after pending fence; no premature `COMPLETE`; confirmed destruction recovers idempotently without routing plaintext; restored keys remain fenced by frontier/receipt. |
| PV-19 | Through natural language, report done/correct/reopen; create then pause/amend/cancel a recurring proactive rule while generation races; inspect conflict; cancel a proposal. | Claims/plans remain separate; one rule-head order wins; generated proposal binds exact rule/base; paused/stale/cancelled work cannot commit; cadence proposes without dispatch authority. |

## Finding-to-contract matrix

| Guarantee | Contract | Example | Vectors |
|---|---|---|---|
| Sole planning authority/ordering and typed command provenance | P0-01 | Delayed invocation | TV-01–03, TV-28, TV-86, TV-89, TV-130, PV-01, PV-14 |
| Expectation versus fact; claim reduction | P0-02 | Both swim examples | TV-04–07, TV-40–41, TV-94 |
| Subject/occurrence/seed identity and immutable provenance | P0-03 | Swim examples; recurring skip; spontaneous completion | TV-08–13, TV-43, TV-45–51, TV-95, TV-97 |
| Lazy recurrence and atomic reschedule/expansion | P0-04 | Recurring skip | TV-14–16, TV-58–61, TV-105–107, TV-133, PV-04 |
| Invocation compatibility and transition graph | P0-05 | Delayed invocation | TV-17–19, TV-48, TV-55, TV-72, TV-100, TV-131, PV-03, PV-07 |
| External-effect idempotency/ambiguity | P0-06 | Delayed invocation | TV-20–22, TV-52, TV-100, PV-03 |
| Acyclic immutable history and graph | P0-07, P1-01 | Swam at 10 | TV-23–25, TV-51, TV-90, TV-95–96 |
| Authority, acceptance, delegation, reliance, node scope | P0-08, P1-01 | Partial/delegated traces | TV-26–27, TV-35, TV-42, TV-44, TV-71, TV-104, TV-108–110, PV-12 |
| Task transitions, deadlines, two-axis outcome | P0-09 | All task specialization traces | TV-36–39, TV-42–46, TV-49, TV-53–54, TV-57, TV-93, TV-101–103, PV-02 |
| Evidence mapping/provider-state ceiling | P0-10 | Checkbox/delegated report | TV-05–07, TV-19, TV-40–41, TV-44 |
| Narrative epistemics and actionability | Core NL contract, P1-02 | Swim/task examples | TV-28, TV-34, TV-127–128, TV-132, PV-19 |
| Mandatory controlled-data deletion, fencing, recovery, retention | Annex B / P1-03 | — | TV-29–31, TV-62–66, TV-91, TV-111–115, PV-05–06, PV-18; missing policy = `HOLD_ADOPTION` |
| Feature-conditional external privacy/disclosure/reconstruction | Annex B / P1-04 | Visitor availability rule | TV-32–33, TV-67–70, TV-92, TV-116–119, PV-11; absent workload capability = default-deny |
| Transaction, request, dispatch concurrency/liveness | P0-01, P0-05–06, P0-09 | Mark done; delayed invocation | TV-36, TV-52–56, TV-72, TV-98–99, PV-01–03, PV-07, PV-17 |
| Operational migration/rollback and stuck-state ownership | Operational adoption gate | — | TV-73–75, TV-124, PV-08 |
| Operational evidence, SLO/recovery, scale/cost | Operational adoption gate | — | TV-73, TV-76–77, TV-124–126, PV-08 |
| Security domain, bridges, authentication/credential fencing | P0-11 | — | TV-78–79, TV-84, TV-86–87, TV-120–121, TV-129, PV-09, PV-14–15 |
| Audit integrity/coupling, denial accounting, anchor recovery | P0-11 | — | TV-80, TV-83, TV-85, TV-88, TV-122, PV-09–10, PV-13, PV-16 |
| Provider ingress, insider/supply-chain, abuse isolation | P0-11 | — | TV-81–83, TV-123, PV-09 |
| Factual accuracy and prior-art boundary | Status; closest prior art | Cited CQRS/Event Sourcing/Outbox/idempotency/RFC 5545 comparison | Documentary authoritative-source pass; no executable vector applies |
| Selectively adopted core and non-adopted assurance gate | Status; triage gate; operational adoption gate | — | TV-73, PV-08 |

## Triage gate

Rate usefulness, boundary correctness, evidence semantics, identity, invocation and
effect safety, privacy/deletion, narrative continuity, integration cost, and open
policy burden. For operational adoption, any unresolved defect in
planning authority, identity, non-idempotent ambiguity, evidence semantics, or
controlled-data deletion yields `HOLD_ADOPTION`; other unresolved defects yield the
proposed outcome `REVISE` or `HOLD`. Allowed outcomes remain `ADOPT`, `REVISE`,
`REJECT`, `HOLD`, and `HOLD_ADOPTION`; the product-semantic core does not select an
implementation/adoption outcome for the assurance contour.

## Revision ledger

| Sections changed | Rules encoded | Vectors added | Contradictions removed | Residual open policy knobs |
|---|---|---|---|---|
| Status; Boundary; Schema; P0-01 | `Task`/`TaskRevision` authority, total task identity mapping, exact-revision CAS | TV-36 | Generic and task identities no longer compete; greatest-sequence no longer permits stale concurrent transitions | Migration feasibility; command authorization policy |
| Schema; P0-03/04/05 | Resultant-state-only `TaskSubject`, stable `TaskOccurrence` plus audited append-only `TaskOccurrenceRevision`, canonical journal-native `WorkOccurrence` only for unresolved/independent/additional episodes, same-task `TaskSeries`/`RuleRevision`/`PlanExpectation` bindings | TV-08–09, TV-43, TV-45–50 | Event claims can no longer masquerade as task state; planned and journal-native episode identity have a total allocation rule; occurrence state is no longer mutable; ad-hoc/cross-task work cannot receive plan expectations | Recurrence horizon/logical keys |
| P0-02/07/10 | Append-only correction, conflict precedence, source-specific evidence ceiling | TV-40–41 | Provider state no longer means outcome; arrival order no longer resolves conflict | Claim registry; admissibility/strength/mapping policies |
| P0-09 | `planning_state × epistemic_outcome × deadline_projection`; explicit outcome subject; atomic-but-independent Mark done in one visibility authority; explicit reopen | TV-37–39, TV-46, TV-53–54 | Claim correction no longer reopens planning; predecessor claims are not rebound to current revisions; deadline no longer asserts outcome or state transition; partial dual-write visibility and expired-ID replay fail closed | Due-boundary/timezone UX; chosen dedupe retention bound |
| P0-08/09; P1-01 | Occurrence/child/delegate scope isolation | TV-42–44 | Child, recurring occurrence, and delegate evidence no longer discharge broader obligations | Partial aggregation; parent/delegator satisfaction and acceptance policies |
| Task traces; Finding matrix | Seven executable state/evidence traces mapped to contracts | TV-36–45 | Abstract lifecycle examples now expose forbidden inferences and exact subjects/revisions | Vocabulary and eventual adoption decision |
| P0-05/06; P1-03/04 | Existing receipt ladder, non-idempotent effect safety, deletion and privacy contours retained | Existing TV-17–22, TV-29–33 | No task shortcut bypasses effect ambiguity, deletion epochs, or disclosure limits | Provider contracts; retention SLAs; privacy budgets/schemas |

This ledger records the evolution of the product-semantic proposal and the subset later incorporated into the vision; its
still-proposed assurance contracts. It is not evidence that the model is implemented,
feasible, operationally adopted, or externally verified.
