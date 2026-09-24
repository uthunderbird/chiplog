"""Exact adoption replay and atomic selected acceptance through the sole writer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.effects.dispatch_v2 import canonical, digest
from chiplog.platform._owner_publication_contracts import (
    CallEffectBatch,
    ExactReplayQuery,
    JournalSelectedPublication,
    PublicationRejected,
)
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    OwnerPublicationUncertain,
    source_commands,
)

from .r14_call_acceptance_port import AcceptedCallReceipt, CallAcceptanceAdoption
from .r14_call_authority import CallPublicationAuthority
from .r14_call_issuance import call_receipt, validate_call_issuance
from .r14_loop_history import read_execution_call_history
from .r16_dispatch_publication import authenticate

if TYPE_CHECKING:
    from .r16_dispatch_runtime import ExecutionDispatchRuntime


async def _replay(
    runtime: ExecutionDispatchRuntime,
    authority: CallPublicationAuthority,
    coordinator: BrokerPublicationCoordinator,
    command_id: str,
    adoption: CallAcceptanceAdoption,
) -> AcceptedCallReceipt | None:
    with runtime._authority_gate().hold():
        selected = runtime._owner_decisions().lookup(runtime._tenant_id, command_id)
        if selected is None:
            return None
        batch = selected.prepared.request
        if not isinstance(batch, CallEffectBatch):
            raise ValueError("selected acceptance identity belongs to another envelope")
        original = validate_call_issuance(batch, runtime)
        if original.adoption != adoption:
            raise LoopRejected("acceptance act already names different exact adoption bytes")
        result = coordinator.lookup_exact(
            ExactReplayQuery(
                identity=batch.identity,
                operation=batch.operation,
                current_invocation=authority.invocation(batch.identity),
                original_commands=source_commands(batch),
            )
        )
    if isinstance(result, PublicationRejected) and result.kind == "HOLD":
        result = await coordinator.recover_selected(runtime._tenant_id, command_id)
    if not isinstance(result, JournalSelectedPublication):
        if isinstance(result, PublicationRejected):
            if result.kind == "INTEGRITY_FAULT":
                raise ValueError("selected call materialization is corrupt: " + result.reason)
            raise LoopRejected("selected call replay rejected: " + result.reason)
        raise ValueError("selected acceptance disappeared during exact replay")
    read_execution_call_history(runtime)
    return call_receipt(batch, original)


async def accept_call(
    runtime: ExecutionDispatchRuntime, peer: str, adoption: CallAcceptanceAdoption
) -> AcceptedCallReceipt:
    command_id = "<adoption>"
    try:
        adoption = CallAcceptanceAdoption.model_validate_json(adoption.canonical_bytes())
        observed = await authenticate(runtime, peer)
        authority = CallPublicationAuthority(runtime, observed)
        coordinator = BrokerPublicationCoordinator(
            runtime._appender, authority, runtime._owner_decisions()
        )
        command_id = "call-acceptance/" + digest(
            canonical(
                [
                    runtime._tenant_id,
                    "hermetic-principal",
                    "effects.accept_call",
                    adoption.act_id,
                ]
            )
        )
        async with runtime._execution_lane:
            # No fresh grant, preview or owner preparation is acquired before replay.
            previous = await _replay(runtime, authority, coordinator, command_id, adoption)
            if previous is not None:
                return previous
            batch = await authority.prepare_fresh(peer, adoption)
            if batch.identity.command_id != command_id:
                raise ValueError("fresh acceptance differs from stable authenticated act identity")
            result = await coordinator.commit(batch)
            if not isinstance(result, JournalSelectedPublication):
                # A different runtime may have selected the same act while owners ran.
                previous = await _replay(runtime, authority, coordinator, command_id, adoption)
                if previous is not None:
                    return previous
                assert isinstance(result, PublicationRejected)
                if result.kind == "INTEGRITY_FAULT":
                    raise ValueError("call acceptance materialization is corrupt: " + result.reason)
                raise LoopRejected("call acceptance rejected: " + result.reason)
            value = validate_call_issuance(batch, runtime)
            read_execution_call_history(runtime)
            return call_receipt(batch, value)
    except LoopRejected, OwnerPublicationUncertain:
        raise
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        raise EffectsIntegrityError(
            f"call.accept tenant={runtime._tenant_id} record={command_id}"
        ) from error
