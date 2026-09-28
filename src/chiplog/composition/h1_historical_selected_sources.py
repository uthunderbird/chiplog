"""Fail-closed boundary for independently selected historical H1 sources.

The H1 issuance is evidence, never a substitute for the raw H0/R17/R16/native
and trust selections that originally authorized it.  This module deliberately
does not fall back to the current H1 reader or a live dispatch resource.

The native first-path reader and its historical inventory are reopened only
inside the same authority cut as the retained H0/R17/R16 and owner selection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from chiplog.architecture.r7_storage_surface import AUTHORITY_STORAGE_SURFACE_DIGEST
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.post_terminal_contracts import (
    PreparedPostTerminalWork,
)
from chiplog.capabilities.agent_loop.post_terminal_record_contracts import (
    validate_prepared_post_terminal_work,
)
from chiplog.capabilities.agent_loop.terminal_work_preparation import prepare_h1_terminal_work
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCandidateCallV1,
    H1OwnerCandidateV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
    H1RetainedSelectedWrapperV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputScopeV1,
    IssueHermeticOutputScopeV1,
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.capabilities.effects.h1_local_preparation import prepare_h1_local_commentary
from chiplog.capabilities.effects.h1_local_preparation_contracts import (
    PreparedH1LocalCommentaryV1,
)
from chiplog.capabilities.projections.conversation_completion_owner import (
    ConversationCompletionOwner,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    PreparedConversationCompletionV1,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from chiplog.composition.h1_completion_readplan_registry import require_registry_identity
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.h1_historical_h0_r17 import (
    HistoricalH0R17Selection,
    read_historical_h0_r17,
)
from chiplog.composition.h1_historical_readplan import reconstruct_h1_historical_read_manifest
from chiplog.composition.h1_postseal_recovery import H1PostSealRecoveryJournal
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.h1_preseal_p_scope_frames import decode_h1_preseal_p_scope_frames
from chiplog.composition.h1_preseal_p_scope_wires import decode_h1_preseal_p_scope_wires
from chiplog.composition.h1_preseal_pe_anchor_records import decode_h1_preseal_pe_anchor_record
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.h1_verified_snapshot_rows import H1VerifiedSnapshotRows
from chiplog.composition.r16_dispatch_registry import (
    ResourceObservation,
    historical_recipient,
    policy_reference,
)
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform.broker import PublicPortSuccess
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import SelectedOwnerDecision
from chiplog.platform.r7_trust import decode_trust_owner_call_canonical

if TYPE_CHECKING:
    from chiplog.composition.h1_completion_issuance import (
        H1CompletionIssuanceV1,
        H1CompletionIssuanceV2,
    )


@dataclass(frozen=True, slots=True)
class H1HistoricalSelection:
    """The exact selected owner decision, paired with its verified issuance."""

    decision: SelectedOwnerDecision
    issuance: H1CompletionIssuanceV1 | H1CompletionIssuanceV2


class H1V2SelectedPresealFullFrameUnavailable(ValueError):
    """A legacy selected V2 seal predates the required full-frame sibling."""


@dataclass(frozen=True, slots=True)
class _HistoricalPorts:
    """Private registered ports required before historical verification exists."""

    gate: object
    custody_factory: object
    loop_factory: object
    owner_factory: object
    trust_factory: object


def _require_historical_ports(runtime: object) -> _HistoricalPorts:
    """Require the registered read-only ports; never substitute current services."""
    gate_factory = getattr(runtime, "_authority_gate", None)
    if not callable(gate_factory):
        raise ValueError("H1 historical validation lacks its authority gate")
    gate = gate_factory()
    if not callable(getattr(gate, "hold", None)):
        raise ValueError("H1 historical validation has an unsupported authority gate")

    custody_factory = getattr(runtime, "_h1_historical_custody", None)
    if not callable(custody_factory):
        raise ValueError("H1 historical validation lacks registered historical custody")
    loop_factory = getattr(runtime, "_loop_decisions", None)
    owner_factory = getattr(runtime, "_owner_decisions", None)
    trust_factory = getattr(runtime, "_h1_historical_trust_reader", None)
    if not callable(loop_factory) or not callable(owner_factory) or not callable(trust_factory):
        raise ValueError("H1 historical validation lacks a registered raw selected-source reader")
    return _HistoricalPorts(gate, custody_factory, loop_factory, owner_factory, trust_factory)


def _require_common_cli_runtime(runtime: object) -> CommonCliExecutionRuntime:
    """Accept only the mounted runtime that owns the native V2 source reader."""
    if type(runtime) is not CommonCliExecutionRuntime:
        raise TypeError("H1 historical sources require the canonical common CLI runtime")
    return runtime


def _open_historical_ports(ports: _HistoricalPorts) -> tuple[object, object, object, object]:
    """Open every raw source while the caller holds the common authority gate."""
    custody = cast(Any, ports.custody_factory)()
    loop_journal = cast(Any, ports.loop_factory)()
    owner_journal = cast(Any, ports.owner_factory)()
    trust_reader = cast(Any, ports.trust_factory)()
    if (
        custody is None
        or not callable(getattr(loop_journal, "entries", None))
        or not callable(getattr(owner_journal, "snapshot", None))
        or not callable(getattr(trust_reader, "historical_record", None))
        or not callable(getattr(trust_reader, "historical_prefix", None))
    ):
        raise ValueError("H1 historical validation has an unsupported raw selected-source reader")
    return custody, loop_journal, owner_journal, trust_reader


def _require_typed_inputs(batch: object, issuance: object) -> None:
    if type(batch) is not CompleteDeliveryBatchV2:
        raise TypeError("H1 historical sources require CompleteDeliveryBatchV2")
    # Delay this import: the issuance module statically imports this seam.
    from chiplog.composition.h1_completion_issuance import (
        H1CompletionIssuanceV1,
        H1CompletionIssuanceV2,
    )

    if type(issuance) not in (H1CompletionIssuanceV1, H1CompletionIssuanceV2):
        raise TypeError(
            "H1 historical sources require H1CompletionIssuanceV1 or H1CompletionIssuanceV2"
        )


def _retained_origin(
    issuance: H1CompletionIssuanceV1 | H1CompletionIssuanceV2,
) -> H1RetainedSelectedWrapperV1:
    """Return the one H0/R17 wrapper embedded by the local effects exchange."""
    effects = issuance.assembly.ordered_effects
    if len(effects) != 1:
        raise ValueError("H1 historical issuance has no unique local effects source")
    return effects[0].owner_call.request.retained_origin


def _verify_historical_r16(
    issuance: H1CompletionIssuanceV1 | H1CompletionIssuanceV2,
    *,
    h0_selection: HistoricalH0R17Selection,
    custody: object,
) -> None:
    """Check the signed original R16 observation without opening live resources.

    The custody format currently proves the signature and original endpoint/
    credential derivation.  It intentionally does not claim current-provider,
    revocation, scenario, or time-domain facts that the read-only binding does
    not retain.
    """
    initialization = h0_selection.initialization
    observation = ResourceObservation(
        initialization.dispatch_grant_bytes,
        initialization.dispatch_credential_bytes,
        initialization.dispatch_endpoint_bytes,
        initialization.dispatch_clock_epoch,
        initialization.dispatch_signature,
    )
    effect_request = issuance.assembly.ordered_effects[0].owner_call.request
    scope = effect_request.selected_scope.scope
    resource_ref = scope.selected_resource_observation_ref
    selected_initialization = resource_ref.selected_initialization
    if (
        resource_ref.signature_domain != "dispatch-resources.v1"
        or selected_initialization.identity != initialization.proposal.run.head
        or selected_initialization.head != h0_selection.initialization_decision_id
        or selected_initialization.fingerprint
        != hashlib.sha256(_retained_origin(issuance).initialization_envelope_bytes).hexdigest()
    ):
        raise ValueError("H1 historical R16 selection differs from retained H0")
    try:
        recipient = historical_recipient(cast(Any, custody), observation)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("H1 historical R16 observation is unauthentic") from error
    try:
        grant = json.loads(observation.grant_bytes)
        credential = json.loads(observation.credential_bytes)
        endpoint = json.loads(observation.endpoint_bytes)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("H1 historical R16 signed facts are malformed") from error
    binding = cast(Any, custody)
    if (
        not isinstance(grant, dict)
        or not isinstance(credential, dict)
        or not isinstance(endpoint, dict)
        or grant
        != {
            "schema": "chiplog.hermetic.dispatch-grant.v1",
            "grant_id": binding.grant_id,
            "version": grant.get("version"),
            "status": "ACTIVE",
            "cap": binding.cap,
            "policy": policy_reference().model_dump(mode="json"),
            "tenant": "hermetic-tenant",
            "recipient": "hermetic-principal",
            "provider": "hermetic-effects",
            "clock_epoch": observation.clock_epoch,
        }
        or type(grant["version"]) is not int
        or grant["version"] < 0
        or credential
        != {
            "schema": "chiplog.hermetic.dispatch-credential.v1",
            "credential_id": binding.credential_id,
            "version": grant["version"],
            "status": "ACTIVE",
            "account": "hermetic-account",
            "provider": "hermetic-effects",
            "grant_id": binding.grant_id,
        }
        or endpoint
        != {
            "schema": "chiplog.hermetic.dispatch-endpoint.v1",
            "provider": "hermetic-effects",
            "account": "hermetic-account",
            "recipient": "hermetic-principal",
            "canonical_address": "hermetic://effects/hermetic-principal",
            "adapter_contract": "chiplog.hermetic-effects.v1",
        }
    ):
        raise ValueError("H1 historical R16 signed policy or custody facts differ")
    expected_recipient = ProviderRecipient(
        provider_id=recipient.provider,
        account_id=recipient.account,
        recipient_id=recipient.recipient,
        endpoint=ExactHead(
            identity=recipient.endpoint.subject_id,
            head=recipient.endpoint.head,
            fingerprint=recipient.endpoint.fingerprint,
        ),
        canonical_address=recipient.canonical_address,
        credential_binding=ExactHead(
            identity=recipient.credential_binding.subject_id,
            head=recipient.credential_binding.head,
            fingerprint=recipient.credential_binding.fingerprint,
        ),
    )
    if expected_recipient != scope.recipient:
        raise ValueError("H1 historical R16 recipient differs from selected scope")


def _selected_owner_decision(
    batch: CompleteDeliveryBatchV2, owner_journal: object
) -> SelectedOwnerDecision:
    """Find exactly the authenticated owner selection for the complete batch."""
    snapshot = cast(Any, owner_journal).snapshot()
    matches = tuple(
        decision
        for decision in snapshot.decisions
        if decision.prepared.request.identity.tenant_id == batch.identity.tenant_id
        and decision.prepared.request.identity.command_id == batch.identity.command_id
    )
    if len(matches) != 1:
        raise ValueError("H1 historical completion has no unique selected owner decision")
    decision = matches[0]
    if (
        decision.prepared.request != batch
        or decision.tenant_commit_sequence != batch.expected.tenant_frontier + 1
        or decision.prepared.predecessor_commitment
        != batch.expected.expected_materialization_commitment
        or decision.prepared.fence_generation != "r6"
        or decision.prepared.fence_frontier != 0
    ):
        raise ValueError("H1 historical completion owner selection differs from batch")
    if (
        decision.prepared.request.operation != batch.operation
        or decision.prepared.request.identity.command_fingerprint
        != batch.identity.command_fingerprint
    ):
        raise ValueError("H1 historical completion command identity differs")
    raw_journal = getattr(owner_journal, "_raw", None)
    entries = getattr(raw_journal, "entries", None)
    if not callable(entries):
        raise ValueError("H1 historical completion lacks its raw owner journal")
    physical = tuple(
        (record_id, raw) for record_id, _, raw in entries() if record_id == decision.decision_id
    )
    if (
        len(physical) != 1
        or decision.decision_head != decision.decision_id
        or hashlib.sha256(physical[0][1]).hexdigest() != decision.decision_fingerprint
    ):
        raise ValueError("H1 historical completion physical owner selection differs")
    return cast(SelectedOwnerDecision, decision)


def _verify_selected_v2_delivery_closure(
    decision: SelectedOwnerDecision,
    runtime: object,
    issuance: H1CompletionIssuanceV2 | None = None,
) -> str | None:
    """Reopen the selected V2 closure before consuming its issuance evidence.

    The caller holds the installed authority gate.  This deliberately dispatches
    from the public applicability schema, rather than decoding the issuance:
    a corrupted selected V2 record therefore cannot trigger historical-source
    reads merely to determine whether it has retained closure evidence.
    """
    from chiplog.composition.h1_completion_issuance import (
        SCHEMA,
        V2_SCHEMA,
        H1CompletionIssuanceV2,
    )
    from chiplog.composition.h1_delivery_evidence_contracts import (
        ROOT_V2_SCHEMA,
        H1DeliverySelectionClosureV2,
    )
    from chiplog.composition.h1_delivery_evidence_journal import H1DeliveryEvidenceJournal
    from chiplog.platform.h1_delivery_binding_contracts import H1DeliveryBinding
    from chiplog.platform.owner_decision_journal import canonical_owner_publication_bytes

    batch = decision.prepared.request
    if type(batch) is not CompleteDeliveryBatchV2:
        raise ValueError("H1 selected delivery closure has the wrong batch")
    applicability_schema = batch.authentication.applicability_schema
    if applicability_schema == SCHEMA:
        return None
    if applicability_schema != V2_SCHEMA:
        raise ValueError("H1 selected delivery closure has unknown applicability")
    gate_factory = getattr(runtime, "_authority_gate", None)
    if not callable(gate_factory):
        raise ValueError("H1 selected delivery closure lacks its authority gate")
    gate = gate_factory()
    require_held = getattr(gate, "require_held", None)
    if not callable(require_held):
        raise ValueError("H1 selected delivery closure has an unsupported authority gate")
    require_held()
    binding = decision.prepared.h1_delivery_binding
    journal = getattr(runtime, "_h1_delivery_evidence_journal", None)
    if (
        type(binding) is not H1DeliveryBinding
        or binding.closure_schema_id != ROOT_V2_SCHEMA
        or type(journal) is not H1DeliveryEvidenceJournal
    ):
        raise ValueError("H1 selected V2 delivery closure is unavailable")
    retained = journal.read_closure(binding)
    if type(retained.record) is not H1DeliverySelectionClosureV2:
        raise ValueError("H1 selected V2 delivery closure has the wrong record")
    value = retained.record._value
    try:
        request_digest = hashlib.sha256(canonical_owner_publication_bytes(batch)).hexdigest()
        source_tenant = batch.identity.tenant_id
        source_command = batch.identity.command_id
        source_fingerprint = batch.identity.command_fingerprint
        source_predecessor = batch.expected.expected_materialization_commitment
        expected_frontier = batch.expected.tenant_frontier
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("H1 selected V2 delivery closure source request differs") from error
    if (
        binding.deployment_id != value.get("deployment_id")
        or binding.database_id != value.get("database_id")
        or binding.database_genesis_digest != value.get("database_genesis_digest")
        or binding.tenant_id != value.get("tenant_id")
        or binding.journal_instance_id != value.get("journal_instance_id")
        or binding.command_id != value.get("command_id")
        or binding.command_fingerprint != value.get("command_fingerprint")
        or binding.request_digest != value.get("request_digest")
        or source_tenant != binding.tenant_id
        or source_command != binding.command_id
        or source_fingerprint != binding.command_fingerprint
        or request_digest != binding.request_digest
        or value.get("predecessor_commitment") != decision.prepared.predecessor_commitment
        or decision.prepared.predecessor_commitment != source_predecessor
        or value.get("expected_tenant_frontier") != expected_frontier
        or decision.tenant_commit_sequence != expected_frontier + 1
    ):
        raise ValueError("H1 selected V2 delivery closure differs from selected decision")
    retained_principal = value.get("principal_id")
    if not isinstance(retained_principal, str):
        raise ValueError("H1 selected V2 delivery closure principal differs")
    if issuance is not None:
        if type(issuance) is not H1CompletionIssuanceV2:
            raise ValueError("H1 selected V2 delivery closure issuance differs")
        if retained_principal != _selected_v2_issuance_principal(issuance):
            raise ValueError("H1 selected V2 delivery closure principal differs")
    return retained_principal


def _selected_v2_issuance_principal(issuance: H1CompletionIssuanceV2) -> str:
    """Read the principal only from a decoded, exact V2 issuance."""
    from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV2

    if type(issuance) is not H1CompletionIssuanceV2:
        raise ValueError("H1 selected V2 delivery closure issuance differs")
    try:
        principal_id = (
            issuance.assembly.original_completion_request.source.selected_admitted_input.principal_id
        )
    except AttributeError as error:
        raise ValueError("H1 selected V2 delivery closure principal differs") from error
    if not isinstance(principal_id, str) or not principal_id:
        raise ValueError("H1 selected V2 delivery closure principal differs")
    return principal_id


def _require_v2_read_plan_prechecks(
    batch: CompleteDeliveryBatchV2,
    issuance: H1CompletionIssuanceV2,
    *,
    runtime: CommonCliExecutionRuntime,
    owner_journal: object,
    decision: SelectedOwnerDecision,
) -> tuple[H1VerifiedSnapshotRows, OwnerJournalSnapshot]:
    """Bind V2 predecessor evidence before replaying its frozen selectors.

    This deliberately establishes only the parts whose independent historical
    inputs already exist here.  Selector replay remains a separate audited
    resolver: neither today's inventory nor the live capture owner is a
    substitute for it.
    """
    read_plan = issuance.read_plan
    try:
        registry = require_registry_identity(
            canonical_bytes=read_plan.registry_bytes,
            expected_head=batch.expected.registry_head,
            expected_fingerprint=batch.expected.registry_fingerprint,
        )
    except ValueError as error:
        raise ValueError("H1 V2 historical read-plan registry differs") from error
    if (
        registry.head != issuance.capture.expected.registry_head
        or registry.fingerprint != issuance.capture.expected.registry_fingerprint
    ):
        raise ValueError("H1 V2 historical read-plan registry differs from capture")

    raw_journal = getattr(owner_journal, "_raw", None)
    entries = getattr(raw_journal, "entries", None)
    snapshot_at = getattr(owner_journal, "snapshot_at", None)
    if not callable(entries) or not callable(snapshot_at):
        raise ValueError("H1 V2 historical read-plan lacks raw owner predecessor")
    raw_entries = entries()
    matching = [entry for entry in raw_entries if entry[0] == decision.decision_id]
    if len(matching) != 1 or matching[0][1] != read_plan.predecessor_owner_head:
        raise ValueError("H1 V2 historical read-plan owner predecessor differs")
    # snapshot_at first validates the whole retained tail, then returns exactly
    # the predecessor prefix.  Its head check prevents an adapter from treating
    # an absent/null predecessor as an arbitrary empty cut.
    predecessor = snapshot_at(read_plan.predecessor_owner_head)
    if (
        getattr(predecessor, "head", object()) != read_plan.predecessor_owner_head
        or getattr(predecessor, "tenant_id", object()) != batch.identity.tenant_id
        or any(
            selected.prepared.request.identity.command_id
            not in getattr(predecessor, "materialized_command_ids", frozenset())
            for selected in getattr(predecessor, "decisions", ())
        )
    ):
        raise ValueError("H1 V2 historical read-plan predecessor prefix differs")

    checkpoint_factory = getattr(runtime, "_h1_checkpoint_store", None)
    if not callable(checkpoint_factory):
        raise ValueError("H1 V2 historical read-plan lacks checkpoint store")
    checkpoint_store = checkpoint_factory()
    resolve_verified = getattr(checkpoint_store, "resolve_verified", None)
    if not callable(resolve_verified):
        raise ValueError("H1 V2 historical read-plan has unsupported checkpoint store")
    try:
        verified = resolve_verified(
            read_plan.predecessor_checkpoint,
            expected_resulting=batch.expected.expected_materialization_commitment,
            expected_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        )
        rows = H1VerifiedSnapshotRows.from_verified(verified)
    except (TypeError, ValueError, RuntimeError) as error:
        raise ValueError("H1 V2 historical read-plan checkpoint differs") from error
    if (
        rows.commitment != batch.expected.expected_materialization_commitment
        or rows.tenant_head(batch.identity.tenant_id) != batch.expected.tenant_frontier
    ):
        raise ValueError("H1 V2 historical read-plan checkpoint frontier differs")
    if not isinstance(predecessor, OwnerJournalSnapshot):
        raise ValueError("H1 V2 historical read-plan predecessor snapshot differs")
    return rows, predecessor


def _verify_historical_scope(
    issuance: H1CompletionIssuanceV1 | H1CompletionIssuanceV2,
    *,
    trust_reader: object,
    gate: object,
) -> HermeticOutputScopeV1:
    """Join retained scope exchanges to raw trust envelopes and full records.

    This deliberately replays only historical prefixes.  It never asks the
    trust owner, captures a current observation, or consults live resources.
    """
    reader = cast(Any, trust_reader)
    if reader.authority_gate is not gate:
        raise ValueError("H1 historical trust reader has a different authority gate")
    issue_wire = decode_trust_owner_call_canonical(
        issuance.scope_issue_exchange.sent.canonical_payload
    )
    current_wire = decode_trust_owner_call_canonical(
        issuance.scope_current_exchange.sent.canonical_payload
    )
    issue_call = H1OwnerCandidateCallV1.model_validate_json(issue_wire.request_bytes)
    current_call = H1OwnerCurrentCallV1.model_validate_json(current_wire.request_bytes)
    issue_result = issuance.scope_issue_exchange.returned
    current_result = issuance.scope_current_exchange.returned
    if not isinstance(issue_result, PublicPortSuccess) or not isinstance(
        current_result, PublicPortSuccess
    ):
        raise ValueError("H1 historical scope exchange did not retain successful owner responses")
    issue_candidate = H1OwnerCandidateV1.model_validate_json(issue_result.canonical_payload)
    current_candidate = H1OwnerCurrentCandidateV1.model_validate_json(
        current_result.canonical_payload
    )
    issue_intent = IssueHermeticOutputScopeV1.model_validate_json(
        issue_call.evidence.selected_request_bytes
    )
    current_request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
        current_call.read_request_bytes
    )
    issue_prefix = reader.historical_prefix(issue_intent.expected_trust_observation)
    current_prefix = reader.historical_prefix(current_request.expected_trust_observation)
    if (
        issue_wire.canonical_bytes() != issuance.scope_issue_exchange.sent.canonical_payload
        or current_wire.canonical_bytes() != issuance.scope_current_exchange.sent.canonical_payload
        or issue_wire.mode != "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
        or current_wire.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
        or issuance.scope_issue_exchange.sent.request_id != issue_call.evidence.route.request_id
        or issuance.scope_current_exchange.sent.request_id != current_call.route.request_id
        or issue_candidate.route != issue_call.evidence.route
        or current_candidate.route != current_call.route
        or issue_result.request_id != issuance.scope_issue_exchange.sent.request_id
        or current_result.request_id != issuance.scope_current_exchange.sent.request_id
        or issue_result.responder != issuance.scope_issue_exchange.sent.callee
        or current_result.responder != issuance.scope_current_exchange.sent.callee
        or issuance.scope_issue_exchange.sent.caller.owner_id != "broker"
        or issuance.scope_current_exchange.sent.caller.owner_id != "broker"
        or issuance.scope_issue_exchange.sent.callee.owner_id != "deployment_trust"
        or issuance.scope_current_exchange.sent.callee.owner_id != "deployment_trust"
        or issuance.scope_issue_exchange.sent.held_resources != ()
        or issuance.scope_current_exchange.sent.held_resources != ()
        or any(
            (
                exchange.sent.budget.remaining_calls != 1
                or exchange.sent.budget.remaining_depth != 1
                or exchange.sent.budget.policy_version != 1
                or exchange.sent.budget.absolute_deadline_ns <= exchange.sent_at_ns
                or exchange.returned_at_ns < exchange.sent_at_ns
            )
            for exchange in (issuance.scope_issue_exchange, issuance.scope_current_exchange)
        )
        or issue_prefix.snapshot_bytes != issue_wire.snapshot_bytes
        or current_prefix.snapshot_bytes != current_wire.snapshot_bytes
        or issue_call.evidence.trust_observation != issue_intent.expected_trust_observation
        or issue_call.evidence.trust_snapshot_digest
        != hashlib.sha256(issue_prefix.snapshot_bytes).hexdigest()
        or issue_call.evidence.retained != _retained_origin(issuance)
    ):
        raise ValueError("H1 historical trust prefix differs from retained scope exchange")

    selected = issuance.assembly.ordered_effects[0].owner_call.request.selected_scope
    scope = selected.scope
    anchor = current_request.source_anchor
    # The selected-scope DTO already joins candidate/current wires structurally.
    # Here the raw materialized row proves that those retained bytes name this scope.
    record = reader.historical_record(anchor.decision.head, anchor.record_ordinal)
    if (
        anchor.owner_id != "deployment_trust"
        or anchor.decision.identity != "deployment-trust/journal"
        or anchor.decision.head != record.physical_decision_id
        or anchor.decision.fingerprint != record.envelope_fingerprint
        or anchor.record_ordinal != record.record_ordinal
        or anchor.record.identity != "trust-record:" + record.physical_decision_id + ":1"
        or anchor.record.head != anchor.record.identity + "/" + anchor.record.fingerprint
        or anchor.record.fingerprint != hashlib.sha256(record.record_bytes).hexdigest()
        or not any(
            entry[0] == record.physical_decision_id for entry in current_prefix.physical_entries
        )
    ):
        raise ValueError("H1 historical trust scope anchor differs from raw materialization")
    try:
        envelope = json.loads(record.envelope_bytes)
        materialized = json.loads(record.record_bytes)
        raw_scope = materialized["scope"]
        historical_scope = HermeticOutputScopeV1.model_validate_json(
            json.dumps(raw_scope, sort_keys=True, separators=(",", ":")).encode()
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("H1 historical trust scope materialization is malformed") from error
    if (
        record.record_ordinal != 1
        or envelope.get("kind") != "HERMETIC_OUTPUT_SCOPE_V1"
        or materialized.get("decision_id") != record.physical_decision_id
        or materialized.get("operation_kind") != "HERMETIC_OUTPUT_SCOPE_V1"
        or materialized.get("record_type_id") != anchor.record_type_id
        or materialized.get("schema_id") != anchor.schema_id
        or historical_scope.canonical_bytes()
        != json.dumps(raw_scope, sort_keys=True, separators=(",", ":")).encode()
        or historical_scope != scope
        or issue_candidate.scope != scope
        or current_candidate.current != selected.current_result
        or anchor.scope_revision != scope.revision
        or anchor.predecessor != scope.predecessor
        or anchor.selected_resource_observation_ref != scope.selected_resource_observation_ref
    ):
        raise ValueError("H1 historical trust scope differs from its raw record")
    scope_bytes = scope.canonical_bytes()
    scope_ref = ExactHead(
        identity=scope.scope_id,
        head=scope.scope_id + "/" + hashlib.sha256(scope_bytes).hexdigest(),
        fingerprint=hashlib.sha256(scope_bytes).hexdigest(),
    )
    current = selected.current_result
    if (
        current_request.expected_scope_ref != scope_ref
        or current.scope_ref != scope_ref
        or current.source_anchor != anchor
        or current.selector_generation != 0
    ):
        raise ValueError("H1 historical trust current scope differs from raw record")
    _verify_historical_scope_lineage(
        scope=scope,
        selected_decision_id=record.physical_decision_id,
        selected_predecessor=record.physical_predecessor,
        issue_prefix=issue_prefix,
        current_prefix=current_prefix,
    )
    return scope


def _verify_historical_scope_lineage(
    *,
    scope: HermeticOutputScopeV1,
    selected_decision_id: str,
    selected_predecessor: str | None,
    issue_prefix: object,
    current_prefix: object,
) -> None:
    """Prove revision/predecessor from physical entry order, never from NOW."""
    current_entries = cast(Any, current_prefix).physical_entries
    issue_ids = {entry[0] for entry in cast(Any, issue_prefix).physical_entries}
    selected_index: int | None = None
    same_lineage: list[tuple[int, str, HermeticOutputScopeV1]] = []
    for index, (decision_id, _, raw) in enumerate(current_entries):
        try:
            envelope = json.loads(raw)
            if envelope.get("kind") != "HERMETIC_OUTPUT_SCOPE_V1":
                continue
            candidate = HermeticOutputScopeV1.model_validate_json(
                json.dumps(
                    envelope["payload"]["scope"], sort_keys=True, separators=(",", ":")
                ).encode()
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("H1 historical trust lineage is malformed") from error
        if (candidate.database_id, candidate.scope_id) == (scope.database_id, scope.scope_id):
            same_lineage.append((index, decision_id, candidate))
        if decision_id == selected_decision_id:
            selected_index = index
    if selected_index is None:
        raise ValueError("H1 historical scope is absent from its current trust prefix")
    selected_matches = [item for item in same_lineage if item[1] == selected_decision_id]
    if len(selected_matches) != 1 or selected_matches[0][2] != scope:
        raise ValueError("H1 historical trust selected scope differs from its lineage")
    if any(index > selected_index for index, _, _ in same_lineage):
        raise ValueError("H1 historical trust prefix supersedes the selected scope")
    if scope.revision == 0:
        if scope.predecessor is not None:
            raise ValueError("H1 historical genesis scope has a predecessor")
    else:
        predecessor = scope.predecessor
        if predecessor is None or predecessor.head != selected_predecessor:
            raise ValueError("H1 historical scope predecessor differs from physical lineage")
        prior = [item for item in same_lineage if item[1] == predecessor.head]
        if len(prior) != 1 or prior[0][2].revision + 1 != scope.revision:
            raise ValueError("H1 historical scope revision differs from predecessor lineage")
    conflicting = [
        item for _, _, item in same_lineage if item.revision == scope.revision and item != scope
    ]
    if conflicting:
        raise ValueError("H1 historical trust lineage has a conflicting scope revision")
    if (
        selected_decision_id not in issue_ids
        and selected_predecessor != cast(Any, issue_prefix).observation.physical_journal_head.head
    ):
        raise ValueError("H1 historical newly issued scope has the wrong physical predecessor")


def _verify_v2_current_as_of(
    issuance: H1CompletionIssuanceV2,
    exchange: object,
    *,
    trust_reader: object,
    gate: object,
) -> None:
    """Prove a V2 CURRENT against the prefix named by that exact wire.

    This is intentionally separate from the capture-time check in the codec:
    the selected reader must also prove the immutable, as-of trust material.
    """
    reader = cast(Any, trust_reader)
    if reader.authority_gate is not gate:
        raise ValueError("H1 V2 historical trust reader has a different authority gate")
    sent = cast(Any, exchange).sent
    returned = cast(Any, exchange).returned
    try:
        wire = decode_trust_owner_call_canonical(sent.canonical_payload)
        call = H1OwnerCurrentCallV1.model_validate_json(wire.request_bytes)
        request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(call.read_request_bytes)
        candidate = H1OwnerCurrentCandidateV1.model_validate_json(returned.canonical_payload)
        prefix = reader.historical_prefix(request.expected_trust_observation)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("H1 V2 historical current wire is malformed") from error
    selected = issuance.assembly.ordered_effects[0].owner_call.request.selected_scope
    if (
        wire.canonical_bytes() != sent.canonical_payload
        or wire.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
        or call.canonical_bytes() != wire.request_bytes
        or request.canonical_bytes() != call.read_request_bytes
        or candidate.canonical_bytes() != returned.canonical_payload
        or wire.snapshot_bytes != prefix.snapshot_bytes
        or sent.request_id != call.route.request_id
        or candidate.route != call.route
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "deployment_trust"
        or request != selected.current_request
        or candidate.current != selected.current_result
    ):
        raise ValueError("H1 V2 historical current differs from its own trust prefix")
    anchor = request.source_anchor
    record = reader.historical_record(anchor.decision.head, anchor.record_ordinal)
    try:
        materialized = json.loads(record.record_bytes)
        scope = HermeticOutputScopeV1.model_validate_json(
            json.dumps(materialized["scope"], sort_keys=True, separators=(",", ":")).encode()
        )
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("H1 V2 historical current scope materialization is malformed") from error
    if (
        anchor.decision.head != record.physical_decision_id
        or anchor.decision.fingerprint != record.envelope_fingerprint
        or anchor.record_ordinal != record.record_ordinal
        or scope != selected.scope
        or not any(entry[0] == record.physical_decision_id for entry in prefix.physical_entries)
    ):
        raise ValueError("H1 V2 historical current anchor differs from raw materialization")
    _verify_historical_scope_lineage(
        scope=scope,
        selected_decision_id=record.physical_decision_id,
        selected_predecessor=record.physical_predecessor,
        issue_prefix=prefix,
        current_prefix=prefix,
    )


def _canonical_json_bytes(value: object, *, name: str) -> bytes:
    """Encode an inert selected sibling or JSON-mode DTO without normalization."""
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"H1 V2 selected preseal {name} is malformed") from error


def _selected_decision_bytes(value: object) -> bytes:
    """Match the installed loop-journal encoder, including escaped Unicode."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _full_frame_json(value: object, *, name: str) -> bytes:
    dump = getattr(value, "model_dump", None)
    if not callable(dump):
        raise ValueError(f"H1 V2 selected preseal {name} is malformed")
    try:
        return _canonical_json_bytes(dump(mode="json"), name=name)
    except TypeError as error:
        raise ValueError(f"H1 V2 selected preseal {name} is malformed") from error


def _require_v2_selected_preseal_scope_frames(
    issuance: H1CompletionIssuanceV2, native: object
) -> None:
    """Join V2 issuance frames to their selected, authenticated P siblings.

    The V1 sibling binds the accepted payload pairs.  This check additionally
    requires the complete broker DTO JSON and both timestamps to be identical
    to the selected seal, so a self-consistent frame cannot replace it.
    """
    try:
        raw_decision = cast(Any, native).seal.raw_bytes
        decision = json.loads(raw_decision)
        if (
            not isinstance(raw_decision, bytes)
            or not isinstance(decision, dict)
            or _selected_decision_bytes(decision) != raw_decision
        ):
            raise ValueError("selected decision bytes differ")
        raw_anchor = decision.get("h1_preseal_pe_anchor")
        raw_wires = decision.get("h1_preseal_p_scope_wires_v1")
        if raw_anchor is None or raw_wires is None:
            raise ValueError("selected preseal sibling is absent")
        raw_frames = decision.get("h1_preseal_p_scope_frames_v1")
        if raw_frames is None:
            raise H1V2SelectedPresealFullFrameUnavailable(
                "H1 V2 selected preseal lacks full-frame sibling"
            )
        if not isinstance(raw_anchor, str):
            raise ValueError("selected preseal anchor is malformed")
        anchor = decode_h1_preseal_pe_anchor_record(raw_anchor.encode("utf-8"))
        p = anchor.as_dict()["p"]
        if not isinstance(p, dict):
            raise ValueError("selected preseal anchor P binding is malformed")
        wires = decode_h1_preseal_p_scope_wires(
            _canonical_json_bytes(raw_wires, name="scope-wire sibling"),
            anchor_binding=anchor.binding,
            issue_digest=p["accepted_issue_wire_digest"],
            current_digest=p["accepted_current_wire_digest"],
        )
        frames = decode_h1_preseal_p_scope_frames(
            _canonical_json_bytes(raw_frames, name="full-frame sibling"),
            anchor_binding=anchor.binding,
            scope_wires=wires,
        )
        issue, current = frames.wires(anchor_binding=anchor.binding, scope_wires=wires)
    except H1V2SelectedPresealFullFrameUnavailable:
        raise
    except (AttributeError, KeyError, TypeError, UnicodeDecodeError, ValueError) as error:
        raise ValueError("H1 V2 selected preseal full-frame sibling is malformed") from error

    for role, selected, retained in (
        ("scope issue", issuance.scope_issue_exchange, issue),
        ("scope current", issuance.scope_current_exchange, current),
    ):
        if (
            _full_frame_json(selected.sent, name=f"{role} sent DTO")
            != _full_frame_json(retained.sent, name=f"{role} sent DTO")
            or _full_frame_json(selected.returned, name=f"{role} returned DTO")
            != _full_frame_json(retained.returned, name=f"{role} returned DTO")
            or selected.sent_at_ns != retained.sent_at_ns
            or selected.returned_at_ns != retained.returned_at_ns
        ):
            raise ValueError(f"H1 V2 selected preseal full-frame {role} differs")


def _verify_v2_selected_recovery(
    issuance: H1CompletionIssuanceV2,
    runtime: CommonCliExecutionRuntime,
    h0: HistoricalH0R17Selection,
) -> None:
    """Join V2 issuance to a read-only, independently rebuilt recovery chain."""
    try:
        from chiplog.composition.h1_completion_preparation_session import (
            H1CompletionPreparationSession,
        )
        from chiplog.composition.h1_conversation_sources import H1ConversationSources

        original = DriveInputRequestV1.model_validate_json(h0.initialization.driver_request_bytes)
        if original.canonical_bytes() != h0.initialization.driver_request_bytes:
            raise ValueError("historical H0 request is noncanonical")
        identity = original.identity
        fingerprint = h0.initialization.driver_request_fingerprint
        locator = H1V2RecoveryNativeSource(runtime).locate_selected_seal(
            original_identity=identity, original_fingerprint=fingerprint
        )
        root = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
            identity, fingerprint, locator
        )
        journal = getattr(runtime, "_h1_postseal_recovery_journal", None)
        mount = getattr(runtime, "_h1_recovery_mount", None)
        if type(journal) is not H1PostSealRecoveryJournal or journal._mount is not mount:
            raise ValueError("enrolled recovery journal is unavailable")
        scan = journal.scan()
        state = scan.state_for_root(root.root_id())
        if (
            state.root != root
            or state.head is None
            or tuple(stage for stage, _raw, _command in state.inputs)
            != ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")
            or tuple(stage for stage, _raw in state.results)
            != ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")
            or issuance.recovery.root_id != root.root_id()
            or issuance.recovery.journal_instance_id != root.journal_instance_id
            or issuance.recovery.completed_chain_head != state.head
        ):
            raise ValueError("H1 V2 selected recovery reference differs")
        from chiplog.composition.h1_recovery_historical_pe_source import (
            H1RecoveryHistoricalPESource,
        )

        completion_request, policy, effects_source = H1RecoveryHistoricalPESource(
            runtime
        ).read_selected_projection(
            original_identity=identity,
            original_fingerprint=fingerprint,
            selected_seal=locator,
        )
        inputs = dict((stage, raw) for stage, raw, _command in state.inputs)
        results = dict(state.results)
        completion_raw = completion_request.canonical_bytes()
        completion_result = PreparedExecutionCompletion.model_validate_json(results["COMPLETION"])
        if (
            inputs["COMPLETION"] != completion_raw
            or completion_result.canonical_bytes() != results["COMPLETION"]
            or prepare_first_path_execution_completion(completion_request) != completion_result
        ):
            raise ValueError("H1 V2 selected recovery completion differs")
        conversation_source = getattr(runtime, "_h1_conversation_source_port", None)
        if (
            type(conversation_source) is not H1ConversationSources
            or conversation_source._runtime is not runtime
        ):
            raise ValueError("H1 V2 selected recovery conversation source is unavailable")
        conversation_request = conversation_source._read_selected_historical_conversation_request(
            original_identity=identity,
            original_fingerprint=fingerprint,
            selected_seal=locator,
            policy=policy,
            completion_request_bytes=completion_raw,
            completion_result_bytes=results["COMPLETION"],
        )
        conversation_raw = conversation_request.canonical_json_bytes()
        conversation_result = PreparedConversationCompletionV1.model_validate_json(
            results["CONVERSATION"]
        )
        if (
            inputs["CONVERSATION"] != conversation_raw
            or conversation_result.canonical_json_bytes() != results["CONVERSATION"]
            or ConversationCompletionOwner().prepare_completion(conversation_request)
            != conversation_result
        ):
            raise ValueError("H1 V2 selected recovery conversation differs")
        command_id = state.stage_input("EFFECTS")[1]
        if not isinstance(command_id, str):
            raise ValueError("H1 V2 selected recovery effects command is absent")
        effects_request = H1CompletionPreparationSession._build_historical_effects_request(
            completion_request=completion_request,
            completion_result=completion_result,
            conversation_request=conversation_request,
            conversation_result=conversation_result,
            p_effects_source=effects_source,
            fence=completion_request.fence,
            effects_command_id=command_id,
        )
        effects_raw = effects_request.canonical_bytes()
        effects_result = PreparedH1LocalCommentaryV1.model_validate_json(results["EFFECTS"])
        assembly = issuance.assembly
        effects_call = assembly.ordered_effects[0].owner_call
        expected_effects = prepare_h1_local_commentary(effects_call)
        if (
            inputs["EFFECTS"] != effects_raw
            or effects_result.canonical_bytes() != results["EFFECTS"]
            or effects_call.request.canonical_bytes() != effects_raw
            or effects_call.request.identity.command_id != command_id
            or expected_effects != effects_result
        ):
            raise ValueError("H1 V2 selected recovery effects differs")
        terminal_request = H1CompletionPreparationSession._build_historical_terminal_work_request(
            completion_request=completion_request,
            completion_result=completion_result,
            effects_request=effects_request,
            effects_result=effects_result,
        )
        terminal_raw = terminal_request.canonical_bytes()
        terminal_result = PreparedPostTerminalWork.model_validate_json(results["TERMINAL_WORK"])
        validate_prepared_post_terminal_work(terminal_request, terminal_result)
        if (
            inputs["TERMINAL_WORK"] != terminal_raw
            or terminal_result.canonical_bytes() != results["TERMINAL_WORK"]
            or prepare_h1_terminal_work(terminal_request) != terminal_result
        ):
            raise ValueError("H1 V2 selected recovery terminal-work differs")
        if (
            completion_raw != assembly.original_completion_request.canonical_bytes()
            or results["COMPLETION"] != assembly.prepared_completion.canonical_bytes()
            or conversation_raw != assembly.conversation_request.canonical_json_bytes()
            or results["CONVERSATION"] != assembly.conversation_result.canonical_json_bytes()
            or results["EFFECTS"] != assembly.ordered_effects[0].owner_result.canonical_bytes()
            or terminal_raw != assembly.terminal_work_request.canonical_bytes()
            or results["TERMINAL_WORK"] != assembly.terminal_work_result.canonical_bytes()
        ):
            raise ValueError("H1 V2 selected recovery differs from issuance assembly")
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith("H1 V2 selected recovery"):
            raise
        raise ValueError("H1 V2 selected recovery is malformed") from error


def _verify_historical_selection(
    batch: CompleteDeliveryBatchV2,
    issuance: H1CompletionIssuanceV1 | H1CompletionIssuanceV2,
    runtime: object,
) -> SelectedOwnerDecision:
    """Read the native sources and selected owner decision from one outer cut."""
    _require_typed_inputs(batch, issuance)
    common_runtime = _require_common_cli_runtime(runtime)
    ports = _require_historical_ports(common_runtime)
    # Subordinate native readers may re-enter this same re-entrant gate, but
    # this is the sole outer historical cut and its decision is returned to the
    # binder rather than re-read after release.
    with cast(Any, ports.gate).hold():
        custody, _, owner_journal, trust_reader = _open_historical_ports(ports)
        decision = _selected_owner_decision(batch, owner_journal)
        from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV2

        if type(issuance) is H1CompletionIssuanceV2:
            _verify_selected_v2_delivery_closure(decision, common_runtime, issuance)
        retained = _retained_origin(issuance)
        h0_r17 = read_historical_h0_r17(common_runtime, retained)
        _verify_historical_r16(issuance, h0_selection=h0_r17, custody=custody)
        _verify_historical_scope(issuance, trust_reader=trust_reader, gate=ports.gate)
        # V1 deliberately remains on its original two-wire route.  V2 adds
        # three independently prefix-bound CURRENT observations.
        if type(issuance) is H1CompletionIssuanceV2:
            _verify_v2_current_as_of(
                issuance,
                issuance.scope_current_exchange,
                trust_reader=trust_reader,
                gate=ports.gate,
            )
            _verify_v2_current_as_of(
                issuance,
                issuance.terminal_admission.preterminal_current_exchange,
                trust_reader=trust_reader,
                gate=ports.gate,
            )
            _verify_v2_current_as_of(
                issuance,
                issuance.final_current_exchange,
                trust_reader=trust_reader,
                gate=ports.gate,
            )
        first_path = H1FirstPathSources(common_runtime)
        native = None
        if type(issuance) is H1CompletionIssuanceV2:
            native = first_path.replay_selected_native_cut(
                issuance.assembly.original_completion_request.source,
                initialization_envelope_bytes=retained.initialization_envelope_bytes,
            )
            _require_v2_selected_preseal_scope_frames(issuance, native)
            _verify_v2_selected_recovery(issuance, common_runtime, h0_r17)
        else:
            first_path.validate_historical(
                issuance.assembly.original_completion_request.source,
                initialization_envelope_bytes=retained.initialization_envelope_bytes,
            )
        if type(issuance) is H1CompletionIssuanceV2:
            rows, predecessor_owner = _require_v2_read_plan_prechecks(
                batch,
                issuance,
                runtime=common_runtime,
                owner_journal=owner_journal,
                decision=decision,
            )
            reconstruct_h1_historical_read_manifest(
                batch=batch,
                issuance=issuance,
                native=cast(Any, native),
                predecessor_rows=rows,
                predecessor_owner=predecessor_owner,
            )
        return decision


def verify_h1_historical_sources(
    batch: CompleteDeliveryBatchV2,
    issuance: H1CompletionIssuanceV1 | H1CompletionIssuanceV2,
    runtime: object,
) -> None:
    """Verify H1 source selection from retained native historical evidence."""
    _verify_historical_selection(batch, issuance, runtime)


def bind_selected_h1_completion(
    batch: CompleteDeliveryBatchV2, runtime: object
) -> H1HistoricalSelection:
    """Bind an H1 batch to the owner decision read from its historical cut."""
    if type(batch) is not CompleteDeliveryBatchV2:
        raise TypeError("H1 historical sources require CompleteDeliveryBatchV2")
    common_runtime = _require_common_cli_runtime(runtime)
    # Decode before selection so a selected owner record cannot smuggle a
    # malformed applicability value into the raw-source boundary.
    from chiplog.composition.h1_completion_issuance import decode_h1_completion_issuance

    issuance = decode_h1_completion_issuance(batch)
    decision = _verify_historical_selection(batch, issuance, common_runtime)
    return H1HistoricalSelection(decision, issuance)
