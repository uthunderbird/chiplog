"""Closed local R15 mandate scope sources, independent of an adopted DTO."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    ConfigurationSnapshot,
    policy_head,
    schedule_head,
)
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    ScheduledSystemMandateScopeV2,
)
from chiplog.composition.r15_scheduler_registry import _origin

EXECUTION_SERVICE_IDENTITY = "r15-hermetic-scheduler-execution-v1"
ISSUANCE_POLICY_ID = "chiplog.scheduler.hermetic-mandate-issuance-policy.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(subject_id: str, body: bytes) -> CallSubjectHead:
    digest = _digest(body)
    return CallSubjectHead(
        subject_id=subject_id,
        revision=Present(head=subject_id + ":" + digest, fingerprint=digest),
    )


def _exact(identity: str, head: str, body: object) -> ExactHead:
    return ExactHead(identity=identity, head=head, fingerprint=_digest(_canonical(body)))


@dataclass(frozen=True)
class RegisteredMandateScopeSources:
    recipient: CallSubjectHead
    recipient_bytes: bytes
    delivery: CallSubjectHead
    delivery_bytes: bytes
    disclosure: CallSubjectHead
    disclosure_bytes: bytes
    administrative_policy: CallSubjectHead
    administrative_policy_bytes: bytes
    origin: OriginSelection


def registered_scope_sources() -> RegisteredMandateScopeSources:
    """Derive three distinct no-emission heads and one explicit admin policy."""

    endpoint = _origin()
    origin = OriginSelection(
        ingress_binding=_exact(
            endpoint.ingress_binding_head,
            endpoint.ingress_binding_head,
            {"kind": "R15_LOCAL_INGRESS", "head": endpoint.ingress_binding_head},
        ),
        recipient=ProviderRecipient(
            provider_id=endpoint.provider,
            account_id="hermetic-tenant",
            recipient_id=endpoint.recipient,
            endpoint=_exact(
                endpoint.endpoint_id,
                endpoint.endpoint_head,
                {"kind": "R15_LOCAL_ENDPOINT", "origin": endpoint.model_dump(mode="json")},
            ),
            canonical_address=endpoint.canonical_address.encode(),
            credential_binding=_exact(
                endpoint.credential_binding_head,
                endpoint.credential_binding_head,
                {"kind": "R15_LOCAL_CREDENTIAL", "head": endpoint.credential_binding_head},
            ),
        ),
    )
    recipient_bytes = _canonical(
        {
            "schema": "chiplog.scheduler.local-recipient-source.v1",
            "tenant_id": "hermetic-tenant",
            "beneficiary": "hermetic-principal",
            "origin": origin.model_dump(mode="json"),
        }
    )
    recipient = _head("r15-local-recipient", recipient_bytes)
    delivery_bytes = _canonical(
        {
            "schema": "chiplog.scheduler.no-delivery-source.v1",
            "recipient": recipient.model_dump(mode="json"),
            "external_emission": "NONE",
        }
    )
    delivery = _head("r15-no-delivery", delivery_bytes)
    disclosure_bytes = _canonical(
        {
            "schema": "chiplog.scheduler.no-disclosure-source.v1",
            "delivery": delivery.model_dump(mode="json"),
            "payload": "NONE",
        }
    )
    disclosure = _head("r15-no-disclosure", disclosure_bytes)
    policy_bytes = _canonical(
        {
            "schema": ISSUANCE_POLICY_ID,
            "administrator": "hermetic-principal",
            "registered_ingress": "hermetic-ingress",
            "service_identity": EXECUTION_SERVICE_IDENTITY,
            "operation": "scheduler.issue_mandate",
            "adoption": "EXACT_CANONICAL_SCOPE_AND_ACT",
            "scope_sources": [
                recipient.model_dump(mode="json"),
                delivery.model_dump(mode="json"),
                disclosure.model_dump(mode="json"),
            ],
            "tools": "NONE",
            "consequential_calls": 0,
        }
    )
    return RegisteredMandateScopeSources(
        recipient,
        recipient_bytes,
        delivery,
        delivery_bytes,
        disclosure,
        disclosure_bytes,
        _head(ISSUANCE_POLICY_ID, policy_bytes),
        policy_bytes,
        origin,
    )


def validate_closed_scope(
    scope: ScheduledSystemMandateScopeV2,
    configuration: ConfigurationSnapshot,
    sources: RegisteredMandateScopeSources,
) -> None:
    """Require registered current configuration and every closed scope field."""

    schedule, policy, bound = configuration.schedule, configuration.policy, configuration.bound
    if schedule is None or policy is None or bound is None:
        raise ValueError("mandate requires a complete selected schedule")
    inputs = schedule.definition.run_inputs
    actual_schedule, actual_policy = schedule_head(schedule), policy_head(policy)
    if (
        inputs.tenant != "hermetic-tenant"
        or inputs.principal != "hermetic-principal"
        or inputs.origin != _origin()
        or scope.tenant_id != inputs.tenant
        or scope.service_identity != EXECUTION_SERVICE_IDENTITY
        or scope.beneficiary_principal != inputs.principal
        or scope.schedule_head
        != CallSubjectHead(
            subject_id=actual_schedule.schedule_id,
            revision=Present(head=actual_schedule.head, fingerprint=actual_schedule.fingerprint),
        )
        or scope.missed_policy_head
        != CallSubjectHead(
            subject_id=actual_policy.policy_id,
            revision=Present(head=actual_policy.head, fingerprint=actual_policy.fingerprint),
        )
        or scope.bound_head
        != CallSubjectHead(
            subject_id=actual_schedule.schedule_id,
            revision=Present(head=bound.head_id, fingerprint=_digest(bound.canonical_bytes())),
        )
        or scope.prompt_template_fingerprint != _digest(inputs.prompt.encode())
        or scope.run_budget_policy != inputs.policy
        or scope.origin != sources.origin
        or scope.recipient_scope != sources.recipient
        or scope.delivery_scope != sources.delivery
        or scope.disclosure_scope != sources.disclosure
        or scope.permitted_tools
        or scope.operations != ("scheduler.execute_interval",)
        or scope.horizon.max_consequential_calls != 0
        or scope.issuance_generation != 0
    ):
        raise ValueError("adopted mandate scope differs from registered no-emission profile")
    horizon = scope.horizon
    try:
        first, last = (
            int(horizon.first_eligible_due_coordinate),
            int(horizon.last_eligible_due_coordinate),
        )
    except ValueError as error:
        raise ValueError("mandate horizon due coordinates are not numeric") from error
    if (
        str(first) != horizon.first_eligible_due_coordinate
        or str(last) != horizon.last_eligible_due_coordinate
        or first > last
        or first < schedule.definition.start_ns
        or (first - schedule.definition.start_ns) % schedule.definition.period_ns != 0
        or (last - schedule.definition.start_ns) % schedule.definition.period_ns != 0
        or (
            schedule.definition.end_exclusive_ns != "NO_END"
            and last >= schedule.definition.end_exclusive_ns
        )
    ):
        raise ValueError("mandate horizon is outside selected due coordinates")
