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

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
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
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.h1_historical_h0_r17 import (
    HistoricalH0R17Selection,
    read_historical_h0_r17,
)
from chiplog.composition.r16_dispatch_registry import (
    ResourceObservation,
    historical_recipient,
    policy_reference,
)
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform.broker import PublicPortSuccess
from chiplog.platform.owner_publications import SelectedOwnerDecision
from chiplog.platform.r7_trust import TrustOwnerCall

if TYPE_CHECKING:
    from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV1


@dataclass(frozen=True, slots=True)
class H1HistoricalSelection:
    """The exact selected owner decision, paired with its verified issuance."""

    decision: SelectedOwnerDecision
    issuance: H1CompletionIssuanceV1


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
    from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV1

    if type(issuance) is not H1CompletionIssuanceV1:
        raise TypeError("H1 historical sources require H1CompletionIssuanceV1")


def _retained_origin(issuance: H1CompletionIssuanceV1) -> H1RetainedSelectedWrapperV1:
    """Return the one H0/R17 wrapper embedded by the local effects exchange."""
    effects = issuance.assembly.ordered_effects
    if len(effects) != 1:
        raise ValueError("H1 historical issuance has no unique local effects source")
    return effects[0].owner_call.request.retained_origin


def _verify_historical_r16(
    issuance: H1CompletionIssuanceV1,
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
        endpoint=ExactHead(**recipient.endpoint.model_dump()),
        canonical_address=recipient.canonical_address,
        credential_binding=ExactHead(**recipient.credential_binding.model_dump()),
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


def _verify_historical_scope(
    issuance: H1CompletionIssuanceV1,
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
    issue_wire = TrustOwnerCall.model_validate_json(
        issuance.scope_issue_exchange.sent.canonical_payload
    )
    current_wire = TrustOwnerCall.model_validate_json(
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
        historical_scope = HermeticOutputScopeV1.model_validate(raw_scope)
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
            candidate = HermeticOutputScopeV1.model_validate(envelope["payload"]["scope"])
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


def _verify_historical_selection(
    batch: CompleteDeliveryBatchV2,
    issuance: H1CompletionIssuanceV1,
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
        retained = _retained_origin(issuance)
        h0_r17 = read_historical_h0_r17(common_runtime, retained)
        _verify_historical_r16(issuance, h0_selection=h0_r17, custody=custody)
        _verify_historical_scope(issuance, trust_reader=trust_reader, gate=ports.gate)
        H1FirstPathSources(common_runtime).validate_historical(
            issuance.assembly.original_completion_request.source,
            initialization_envelope_bytes=retained.initialization_envelope_bytes,
        )
        return _selected_owner_decision(batch, owner_journal)


def verify_h1_historical_sources(
    batch: CompleteDeliveryBatchV2,
    issuance: H1CompletionIssuanceV1,
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
