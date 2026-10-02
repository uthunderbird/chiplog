"""Exact original native context scope binding; carries no SEND authority."""

import hashlib
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.execution_transition_contracts import PrepareExecutionRequest
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.capabilities.deployment_trust.native_one_call_scope_contracts import (
    NativeOneCallContextScopeAnchorV1,
    NativeOneCallContextScopeV1,
)


class NativePrepareScopeBindingV1(RecoveryDTO):
    """Inert retained bytes, authenticated independently at each authority seam."""

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")
    kind: Literal["R14_NATIVE_PREPARE_SCOPE_BINDING_V1"] = "R14_NATIVE_PREPARE_SCOPE_BINDING_V1"
    anchor: NativeOneCallContextScopeAnchorV1
    scope_head: ExactHead
    scope_bytes: bytes = Field(min_length=1)
    decision_bytes: bytes = Field(min_length=1)
    record_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact(self) -> Self:
        scope = NativeOneCallContextScopeV1.model_validate_json(self.scope_bytes)
        digest = hashlib.sha256(self.scope_bytes).hexdigest()
        if (
            scope.canonical_bytes() != self.scope_bytes
            or self.scope_head.identity != scope.scope_id
            or self.scope_head.fingerprint != digest
            or self.anchor.scope_revision != scope.revision
            or self.anchor.predecessor != scope.predecessor
            or self.anchor.selected_resource_observation_ref
            != scope.selected_resource_observation_ref
        ):
            raise ValueError("native Prepare scope bytes or anchor differ")
        return self

    def verify_request(self, request: PrepareExecutionRequest) -> None:
        scope = NativeOneCallContextScopeV1.model_validate_json(self.scope_bytes)
        run = request.run
        if (
            scope.planned_run_id != run.run_id
            or scope.planned_run_head
            != ExactHead(identity=run.run_id, head=run.head, fingerprint=run.digest())
            or scope.planned_prepare_command_id != request.command_id
            or scope.worker_session_id != run.worker_session
            or scope.worker_session_id != request.manifest.worker_session
            or scope.tenant_id != run.tenant
            or scope.principal_id != run.principal
            or scope.recipient != run.origin.recipient
            or request.manifest.run_id != run.run_id
            or request.manifest.tenant != run.tenant
            or request.manifest.principal != run.principal
        ):
            raise ValueError("native Prepare scope planned identity or recipient differs")


def bind_native_prepare_scope(authenticated: object) -> NativePrepareScopeBindingV1:
    return NativePrepareScopeBindingV1(
        anchor=authenticated.anchor,
        scope_head=authenticated.scope_head,
        scope_bytes=authenticated.scope.canonical_bytes(),
        decision_bytes=authenticated.decision_bytes,
        record_bytes=authenticated.record_bytes,
    )


def verify_native_prepare_scope(
    runtime: object,
    request: PrepareExecutionRequest,
    binding: NativePrepareScopeBindingV1,
    *,
    historical: bool,
) -> None:
    """Reopen selected custody and physical record; DTO bytes never grant trust."""
    from .r17_native_one_call_scope import NativeOneCallScopeService

    binding.verify_request(request)
    service = NativeOneCallScopeService(runtime)
    authenticated = (
        service.read_historical(binding.anchor)
        if historical
        else service.read_current(binding.anchor)
    )
    if authenticated is None or bind_native_prepare_scope(authenticated) != binding:
        raise ValueError("native Prepare selected scope or physical bytes differ")
