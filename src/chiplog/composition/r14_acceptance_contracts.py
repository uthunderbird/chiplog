"""Retained acceptance bytes and closed structural semantics; never authority."""

from typing import Literal

from pydantic import ConfigDict, Field

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.recovery_contracts import Present, RecoveryDTO
from chiplog.capabilities.effects import contracts as effects

ACCEPTED_SCHEMA = "chiplog.call.accepted.v1"
EXECUTION_SCHEMA = "chiplog.call.execution-intent.v1"
EFFECT_SCHEMA = "chiplog.effects.record.v1"
MAX_ACCEPTANCE_BYTES = 1_048_576


class SemanticComponent(RecoveryDTO):
    schema_id: Literal["chiplog.call-effects.semantic-component.v1"] = (
        "chiplog.call-effects.semantic-component.v1"
    )
    component: str
    effects_field: str
    version: str


def semantic_components() -> tuple[SemanticComponent, ...]:
    """Closed interpretation copied from the hermetic effects profile, not caller input.

    Descriptor equality is not source attestation, current authority or permission
    to publish/send. Fixed subjects are call-effects:semantic-component:<name>:v1.
    """
    return tuple(
        SemanticComponent(component=name, effects_field=field, version=version)
        for name, field, version in (
            ("normative_manifest", "normative_manifest", "chiplog.hermetic-self-effect-policy.v1"),
            ("reducer", "reducer_version", "chiplog.effects.domain.v1"),
            (
                "transition_registry",
                "transition_registry_version",
                "chiplog.effects.application.prepare-transition.v1",
            ),
            (
                "canonicalization",
                "canonicalization_fingerprint_version",
                "chiplog.effects.sorted-json-sha256.v1",
            ),
            ("adapter_contract", "adapter_contract_version", "chiplog.hermetic-effects.v1"),
        )
    )


def loop_dispatch_semantics() -> call.CallDispatchSemantics:
    return call.CallDispatchSemantics.model_validate(
        {
            item.component: call.CallSubjectHead(
                subject_id=f"call-effects:semantic-component:{item.component}:v1",
                revision=Present(head="record:" + item.digest(), fingerprint=item.digest()),
            )
            for item in semantic_components()
        }
    )


def effects_dispatch_semantics() -> effects.DispatchSemanticBinding:
    return effects.DispatchSemanticBinding.model_validate(
        {item.effects_field: item.version for item in semantic_components()}
    )


class RetainedAcceptancePreparation(RecoveryDTO):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    kind: Literal["RETAINED_CALL_EFFECT_ACCEPTANCE_V1"] = "RETAINED_CALL_EFFECT_ACCEPTANCE_V1"
    loop_request: call.AcceptConsequentialCallRequest
    loop_proposal: call.PreparedConsequentialAcceptance
    effects_command: effects.AcceptEffectCommand
    effects_proposal: effects.PreparedEffectPublication


class AcceptancePhysicalMember(RecoveryDTO):
    record_id: str = Field(min_length=1)
    owner: Literal["agent_loop", "effects"]
    schema_id: str = Field(min_length=1)
    canonical_payload_base64: str = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class AcceptancePhysicalEnvelope(RecoveryDTO):
    kind: Literal["CALL_EFFECT_ACCEPTANCE_ENVELOPE_V1"] = "CALL_EFFECT_ACCEPTANCE_ENVELOPE_V1"
    tenant_id: str = Field(min_length=1)
    original_call_id: str = Field(min_length=1)
    expected_tenant_head: int = Field(ge=0)
    retained_preparation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete_records: tuple[AcceptancePhysicalMember, ...] = Field(min_length=3, max_length=3)
    physical_batch_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
