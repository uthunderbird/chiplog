"""Private original-call writer authority; no public caller supplies prepared objects."""

from __future__ import annotations

import secrets
import time
from typing import Literal

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.platform._owner_publication_contracts import (
    CallEffectBatch,
    ExactReplayQuery,
    InvocationProofRef,
    PublicationIdentity,
    PublicationRejected,
    RegisteredPublication,
)
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision

from .r7_planning import ObservedTrustCall
from .r14_call_acceptance_port import CallAcceptanceAdoption
from .r14_call_issuance import (
    CallAcceptanceIssuance,
    call_batch,
    call_identity,
    validate_call_issuance,
)
from .r14_call_preparation import prepare_call_exchange
from .r14_loop_history import read_execution_call_history
from .r16_dispatch_authority import DispatchPublicationAuthority
from .r16_dispatch_inputs import capture_dispatch, require_dispatch_scope
from .r16_dispatch_runtime import ExecutionDispatchRuntime
from .r16_effects_authority import effect_materialization_state


class CallPublicationAuthority:
    def __init__(self, runtime: ExecutionDispatchRuntime, observed: ObservedTrustCall) -> None:
        self.runtime = runtime
        self.observed = observed
        self._invocations = DispatchPublicationAuthority(runtime, observed)
        self._issued: list[tuple[PreparedOwnerPublication, CallAcceptanceIssuance]] = []

    def invocation(self, identity: PublicationIdentity) -> InvocationProofRef:
        return self._invocations.invocation(identity)

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        if query.operation != "effects.accept_call":
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="not a call acceptance operation",
            )
        return self._invocations.authenticate_replay(query)

    async def prepare_fresh(self, peer: str, adoption: CallAcceptanceAdoption) -> CallEffectBatch:
        # Only this method can add an issued preparation. The returned exchange
        # comes from actual owner calls; there is no register(caller_value) port.
        exchange = await prepare_call_exchange(self.runtime, peer, adoption)
        if exchange.observed.result.reference_bytes != self.observed.result.reference_bytes:
            raise LoopRejected("call preparation invocation changed principal binding")
        value = CallAcceptanceIssuance.model_validate_json(exchange.canonical_bytes())
        identity = call_identity(value)
        with self.runtime._authority_gate().hold():
            batch = call_batch(value, self.invocation(identity))
            if validate_call_issuance(batch, self.runtime) != value:
                raise LoopRejected("call preparation differs from retained original exchange")
            self._check_live(value)
            prepared = PreparedOwnerPublication(
                batch,
                secrets.token_hex(24),
                "r6",
                0,
                value.captured.cut.materialization_commitment,
            )
            self._issued.append((prepared, value))
        return batch

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        for prepared, _ in self._issued:
            if prepared.request is request:
                return prepared
        return PublicationRejected(
            kind="DENIED",
            tenant_id=request.identity.tenant_id,
            command_id=request.identity.command_id,
            reason="unissued call acceptance publication",
        )

    def _check_live(self, value: CallAcceptanceIssuance) -> None:
        runtime = self.runtime
        resources = runtime._require_dispatch_resources()
        target = value.preview.preview.target
        fresh = capture_dispatch(
            runtime,
            resources,
            value.observed,
            target.current_run.subject_id,
            original_call_id=target.original_call_id,
        )
        _, inventory, _ = read_execution_call_history(runtime)
        cut = value.retained.loop_request.binding.cut
        rows = tuple(
            row
            for row in inventory.ordered_calls
            if row.original_call_id == target.original_call_id
        )
        if (
            fresh != value.captured
            or inventory != cut.predecessor_inventory
            or len(rows) != 1
            or rows[0].initialized != target.initialized
            or not isinstance(rows[0].acceptance, InitializedCall)
            or not isinstance(rows[0].terminal, Absent)
        ):
            raise LoopRejected("call acceptance predecessor or live branch changed")
        require_dispatch_scope(
            fresh, resources, value.preview.request.mandate, None, first_send=False
        )
        deadline = min(
            value.loop_sent.budget.absolute_deadline_ns,
            value.effects_sent.budget.absolute_deadline_ns,
            value.retained.effects_request.current.lease_expires_at_ns,
            value.preview.request.mandate.horizon.expires_at_ns,
            *(source.valid_until_ns for source in cut.sources),
        )
        if fresh.observed_time_ns >= deadline or time.monotonic_ns() >= deadline:
            raise LoopRejected("call acceptance original authority or owner preparation expired")

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        for issued, value in self._issued:
            if prepared is not issued:
                continue
            try:
                with self.runtime._authority_gate().hold():
                    self._check_live(value)
                return None
            except LoopRejected:
                return "STALE"
            except ValueError, RuntimeError, OSError, KeyError, TypeError:
                return "INDETERMINATE"
        return "DENIED"

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        batch = decision.prepared.request
        if not isinstance(batch, CallEffectBatch):
            raise ValueError("selected call acceptance has another envelope")
        validate_call_issuance(batch, self.runtime)
        return effect_materialization_state(self.runtime, decision)

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        return self.materialization_state(decision) == "ABSENT"
