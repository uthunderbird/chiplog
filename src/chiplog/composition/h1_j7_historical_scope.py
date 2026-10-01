"""Historical verification of one inert J7 current-scope exchange.

The returned evidence describes a scope at the authenticated trust prefix named
by the retained wire.  It does not retain a selected issuance or authorize any
later use of that exchange.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    HermeticOutputScopeV1,
    HermeticTrustObservationV1,
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.composition.h1_historical_selected_sources import (
    _verify_historical_scope_lineage,
)
from chiplog.composition.h1_j7_current_scope_exchange import H1J7CurrentScopeExchangeV1
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.broker import PublicPortSuccess
from chiplog.platform.r7_trust import decode_trust_owner_call_canonical


@dataclass(frozen=True, slots=True)
class H1J7HistoricalScopeEvidence:
    """Verified, inert evidence for one retained J7 current-scope response."""

    exchange: H1J7CurrentScopeExchangeV1
    scope: HermeticOutputScopeV1
    trust_observation: HermeticTrustObservationV1


def verify_h1_j7_historical_scope(
    exchange: H1J7CurrentScopeExchangeV1,
    expected_scope: HermeticOutputScopeV1,
    *,
    trust_reader: object,
    authority_gate: AuthorityGate,
) -> H1J7HistoricalScopeEvidence:
    """Verify ``exchange`` solely from its authenticated historical trust cut.

    This function intentionally has no runtime parameter: it cannot capture a
    current trust observation, invoke the owner, inspect grants, or recheck a
    worker.  The supplied reader is accepted only when it is installed on the
    same authority gate held for the complete historical read.
    """
    if type(exchange) is not H1J7CurrentScopeExchangeV1:
        raise TypeError("H1 J7 historical scope exchange has the wrong type")
    if type(expected_scope) is not HermeticOutputScopeV1:
        raise TypeError("H1 J7 expected historical scope has the wrong type")
    if type(authority_gate) is not AuthorityGate:
        raise TypeError("H1 J7 historical scope requires an AuthorityGate")
    reader = cast(Any, trust_reader)
    if reader.authority_gate is not authority_gate:
        raise ValueError("H1 J7 historical trust reader has a different authority gate")
    if not callable(getattr(reader, "historical_prefix", None)) or not callable(
        getattr(reader, "historical_record", None)
    ):
        raise ValueError("H1 J7 historical trust reader lacks historical reads")

    with authority_gate.hold():
        wire = decode_trust_owner_call_canonical(exchange.sent.canonical_payload)
        try:
            call = H1OwnerCurrentCallV1.model_validate_json(wire.request_bytes)
            request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
                call.read_request_bytes
            )
            candidate = H1OwnerCurrentCandidateV1.model_validate_json(
                exchange.returned.canonical_payload
            )
        except (TypeError, ValueError) as error:
            raise ValueError("H1 J7 historical scope wire is malformed") from error

        if (
            wire.canonical_bytes() != exchange.sent.canonical_payload
            or call.canonical_bytes() != wire.request_bytes
            or request.canonical_bytes() != call.read_request_bytes
            or candidate.canonical_bytes() != exchange.returned.canonical_payload
            or wire.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
            or request != exchange.request
            or candidate.current != exchange.result
        ):
            raise ValueError("H1 J7 historical scope differs from its retained wire")
        try:
            candidate.check_pinned_call(call)
        except ValueError as error:
            raise ValueError("H1 J7 historical candidate differs from pinned call") from error

        _verify_broker_frame(exchange, call)
        prefix = reader.historical_prefix(request.expected_trust_observation)
        if (
            prefix.observation != request.expected_trust_observation
            or prefix.snapshot_bytes != wire.snapshot_bytes
        ):
            raise ValueError("H1 J7 historical trust prefix differs from retained wire")

        anchor = request.source_anchor
        record = reader.historical_record(anchor.decision.head, anchor.record_ordinal)
        historical_scope = _scope_from_record(record, anchor, prefix)
        if historical_scope != expected_scope:
            raise ValueError("H1 J7 historical materialized scope differs from expected scope")
        _verify_scope_joins(request, exchange.result, candidate, expected_scope)
        _verify_historical_scope_lineage(
            scope=expected_scope,
            selected_decision_id=record.physical_decision_id,
            selected_predecessor=record.physical_predecessor,
            issue_prefix=prefix,
            current_prefix=prefix,
        )
    return H1J7HistoricalScopeEvidence(
        exchange=exchange,
        scope=expected_scope,
        trust_observation=request.expected_trust_observation,
    )


def _verify_broker_frame(exchange: H1J7CurrentScopeExchangeV1, call: H1OwnerCurrentCallV1) -> None:
    sent = exchange.sent
    returned = exchange.returned
    route = call.route
    if not isinstance(returned, PublicPortSuccess):  # DTO protects this; retains boundary proof.
        raise ValueError("H1 J7 historical scope response is not successful")
    if (
        sent.operation_id != "deployment_trust.read_current_hermetic_output_scope"
        or sent.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or returned.schema_id
        != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
        or sent.request_id != route.request_id
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or sent.caller.tenant_id != route.tenant_id
        or sent.caller.broker_epoch != route.broker_epoch
        or sent.caller.generation_id != route.runtime_generation
        or sent.caller.owner_id != "broker"
        or sent.caller.session_id != route.broker_session_id
        or sent.callee.tenant_id != route.tenant_id
        or sent.callee.broker_epoch != route.broker_epoch
        or sent.callee.generation_id != route.runtime_generation
        or sent.callee.owner_id != "deployment_trust"
        or sent.callee.session_id != route.owner_session_id
        or route.operation != sent.operation_id
        or sent.held_resources != ()
        or sent.budget.remaining_calls != 1
        or sent.budget.remaining_depth != 1
        or sent.budget.policy_version != 1
        or sent.budget.absolute_deadline_ns <= exchange.sent_at_ns
        or exchange.returned_at_ns < exchange.sent_at_ns
        or exchange.returned_at_ns >= sent.budget.absolute_deadline_ns
    ):
        raise ValueError("H1 J7 historical broker frame differs from pinned route")


def _scope_from_record(record: object, anchor: object, prefix: object) -> HermeticOutputScopeV1:
    row = cast(Any, record)
    locator = cast(Any, anchor)
    historical_prefix = cast(Any, prefix)
    if (
        locator.owner_id != "deployment_trust"
        or locator.decision.identity != "deployment-trust/journal"
        or locator.decision.head != row.physical_decision_id
        or locator.decision.fingerprint != row.envelope_fingerprint
        or locator.record_ordinal != row.record_ordinal
        or locator.record.identity != f"trust-record:{row.physical_decision_id}:1"
        or locator.record.head != locator.record.identity + "/" + locator.record.fingerprint
        or locator.record.fingerprint != hashlib.sha256(row.record_bytes).hexdigest()
        or not any(
            entry[0] == row.physical_decision_id for entry in historical_prefix.physical_entries
        )
    ):
        raise ValueError("H1 J7 historical scope anchor differs from raw materialization")
    try:
        envelope = json.loads(row.envelope_bytes)
        materialized = json.loads(row.record_bytes)
        raw_scope = materialized["scope"]
        raw_scope_bytes = json.dumps(raw_scope, sort_keys=True, separators=(",", ":")).encode()
        scope = HermeticOutputScopeV1.model_validate_json(raw_scope_bytes)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("H1 J7 historical scope materialization is malformed") from error
    if (
        row.record_ordinal != 1
        or not isinstance(envelope, dict)
        or envelope.get("kind") != "HERMETIC_OUTPUT_SCOPE_V1"
        or not isinstance(materialized, dict)
        or materialized.get("decision_id") != row.physical_decision_id
        or materialized.get("operation_kind") != "HERMETIC_OUTPUT_SCOPE_V1"
        or materialized.get("record_type_id") != locator.record_type_id
        or materialized.get("schema_id") != locator.schema_id
        or scope.canonical_bytes() != raw_scope_bytes
        or locator.scope_revision != scope.revision
        or locator.predecessor != scope.predecessor
        or locator.selected_resource_observation_ref != scope.selected_resource_observation_ref
    ):
        raise ValueError("H1 J7 historical scope differs from its raw record")
    return scope


def _verify_scope_joins(
    request: ReadCurrentHermeticExecutionScopeV1,
    result: CurrentHermeticExecutionScopeV1,
    candidate: H1OwnerCurrentCandidateV1,
    scope: HermeticOutputScopeV1,
) -> None:
    scope_bytes = scope.canonical_bytes()
    scope_ref = ExactHead(
        identity=scope.scope_id,
        head=scope.scope_id + "/" + hashlib.sha256(scope_bytes).hexdigest(),
        fingerprint=hashlib.sha256(scope_bytes).hexdigest(),
    )
    expected_current = CurrentHermeticExecutionScopeV1(
        disposition="CURRENT",
        scope_ref=scope_ref,
        source_anchor=request.source_anchor,
        selector_generation=0,
        ordered_current_source_refs=(
            request.admitted_authentication_ref,
            request.source_anchor.decision,
            request.source_anchor.record,
        ),
    )
    cli = request.authenticated_cli_ref
    if (
        request.expected_scope_ref != scope_ref
        or request.expected_revision != scope.revision
        or request.tenant_id != scope.tenant_id
        or request.database_id != scope.database_id
        or request.scope_id != scope.scope_id
        or request.expected_worker_session_id != scope.worker_session_id
        or request.admitted_authentication_ref != scope.admitted_authentication
        or request.selected_resource_observation_ref != scope.selected_resource_observation_ref
        or cli.tenant_id.value != scope.tenant_id
        or cli.principal_id.value != scope.principal_id
        or cli.contour != "CLI"
        or cli.credential_head != scope.authenticated_cli_state.credential_head
        or cli.session_head != scope.authenticated_cli_state.session_head
        or cli.trust_head != scope.authenticated_cli_state.trust_binding_digest
        or result != expected_current
        or candidate.current != expected_current
    ):
        raise ValueError("H1 J7 historical current scope differs from raw scope")


__all__ = [
    "H1J7HistoricalScopeEvidence",
    "verify_h1_j7_historical_scope",
]
