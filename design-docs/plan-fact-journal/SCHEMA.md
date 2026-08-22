# Plan / Fact / Journal — schema

> Status: proposed. Authority and provenance are defined in [README.md](README.md).

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

<!-- Copied payload ends above; see MIGRATION-MANIFEST.md. -->
