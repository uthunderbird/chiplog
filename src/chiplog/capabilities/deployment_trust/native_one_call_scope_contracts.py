"""Inert native context/recipient scopes; never SEND or execution authority."""

import hashlib
from typing import Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient

from . import TrustReference
from .cli_custody_contracts import CliCustodyDTO, Identity, UInt64
from .hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
    SelectedHermeticResourceObservationRefV1,
)


class NativeOneCallPolicyV1(CliCustodyDTO):
    schema_id: Literal["chiplog.native-one-call-context-policy.v1"] = (
        "chiplog.native-one-call-context-policy.v1"
    )
    policy_source: Literal["USER_2026_10_02_CONTEXT_ONLY"] = "USER_2026_10_02_CONTEXT_ONLY"
    permitted_recording_tool: Literal["request_self_effect"] = "request_self_effect"
    call_count: Literal[1] = 1
    send_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False
    disclosure_authorized: Literal[False] = False
    separate_selected_acceptance_or_mandate_required: Literal[True] = True


class NativeOneCallPolicySourceV1(CliCustodyDTO):
    ref: ExactHead
    canonical_source_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact(self) -> Self:
        policy = NativeOneCallPolicyV1.model_validate_json(self.canonical_source_bytes)
        if (
            policy.canonical_bytes() != self.canonical_source_bytes
            or hashlib.sha256(self.canonical_source_bytes).hexdigest() != self.ref.fingerprint
        ):
            raise ValueError("native policy bytes differ")
        return self


class IssueNativeOneCallContextScopeV1(CliCustodyDTO):
    schema_id: Literal["chiplog.issue-native-one-call-context-scope.v1"] = (
        "chiplog.issue-native-one-call-context-scope.v1"
    )
    slot_id: Literal["r17-native-one-call-context"] = "r17-native-one-call-context"
    database_id: Identity
    scope_id: Identity
    expected_trust_observation: HermeticTrustObservationV1
    expected_scope_predecessor: ExactHead | None
    expected_revision: UInt64
    authenticated_cli_ref: TrustReference
    admitted_authentication_ref: ExactHead
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1
    worker_session_id: Identity
    planned_run_id: Identity
    planned_run_head: ExactHead
    planned_prepare_command_id: Identity

    @model_validator(mode="after")
    def exact(self) -> Self:
        if (self.expected_scope_predecessor is None) != (self.expected_revision == 0):
            raise ValueError("native genesis requires revision zero and no predecessor")
        if self.planned_run_head.identity != self.planned_run_id:
            raise ValueError("native planned Run identity differs")
        return self


class NativeOneCallContextScopeV1(CliCustodyDTO):
    schema_id: Literal["chiplog.native-one-call-context-scope.v1"] = (
        "chiplog.native-one-call-context-scope.v1"
    )
    kind: Literal["NATIVE_ONE_CALL_CONTEXT_SCOPE_V1"] = "NATIVE_ONE_CALL_CONTEXT_SCOPE_V1"
    issuer: Literal["deployment_trust"] = "deployment_trust"
    source_profile: Literal["chiplog.execution.r17-native-one-call-context.v1"] = (
        "chiplog.execution.r17-native-one-call-context.v1"
    )
    slot: Literal["r17-native-one-call-context"] = "r17-native-one-call-context"
    tenant_id: Literal["hermetic-tenant"] = "hermetic-tenant"
    principal_id: Literal["hermetic-principal"] = "hermetic-principal"
    database_id: Identity
    scope_id: Identity
    revision: UInt64
    predecessor: ExactHead | None
    worker_session_id: Identity
    planned_run_id: Identity
    planned_run_head: ExactHead
    planned_prepare_command_id: Identity
    authenticated_cli_ref: TrustReference
    admitted_authentication: ExactHead
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1
    recipient: ProviderRecipient
    disclosure_policy: NativeOneCallPolicySourceV1
    mandate_applicability: Literal["NATIVE_CLI_NO_EXECUTION_MANDATE_APPLIES_V1"] = (
        "NATIVE_CLI_NO_EXECUTION_MANDATE_APPLIES_V1"
    )
    mandate_profile: Literal["r17-native-context-record-one-call-v1"] = (
        "r17-native-context-record-one-call-v1"
    )
    mandate_inventory_complete: Literal[True] = True
    ordered_mandates: tuple[ExactHead, ...] = Field(default=(), max_length=0)
    mandate_inventory_bytes: bytes = Field(min_length=1)
    issuance_trust_observation: HermeticTrustObservationV1
    selected_initialization_bytes: bytes = Field(min_length=1)
    selected_admission_bytes: bytes = Field(min_length=1)
    selected_admission_ref: ExactHead
    selected_resource_grant_bytes: bytes = Field(min_length=1)
    selected_resource_credential_bytes: bytes = Field(min_length=1)
    selected_resource_endpoint_bytes: bytes = Field(min_length=1)
    selected_resource_clock_epoch: Identity
    selected_resource_signature: Identity

    @model_validator(mode="after")
    def exact(self) -> Self:
        if self.planned_run_head.identity != self.planned_run_id:
            raise ValueError("native planned Run differs")
        if (
            self.authenticated_cli_ref.tenant_id.value != self.tenant_id
            or self.authenticated_cli_ref.principal_id.value != self.principal_id
            or self.authenticated_cli_ref.contour != "CLI"
        ):
            raise ValueError("native CLI identity differs")
        if self.recipient.recipient_id != self.principal_id:
            raise ValueError("native recipient differs")
        return self


class NativeOneCallContextScopeAnchorV1(CliCustodyDTO):
    owner_id: Literal["deployment_trust"] = "deployment_trust"
    decision: ExactHead
    record_ordinal: Literal[1] = 1
    record_type_id: Literal["chiplog.deployment_trust.native_one_call_context_scope"] = (
        "chiplog.deployment_trust.native_one_call_context_scope"
    )
    schema_id: Literal["chiplog.deployment_trust.record.v1"] = "chiplog.deployment_trust.record.v1"
    record: ExactHead
    scope_revision: UInt64
    predecessor: ExactHead | None
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1
