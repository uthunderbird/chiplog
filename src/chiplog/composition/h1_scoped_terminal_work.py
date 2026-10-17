"""Typed terminal-work reconstruction for a selected scoped H1 Effects exchange."""

from __future__ import annotations

import hashlib

from chiplog.capabilities.agent_loop.completion_terminal_work_sources import (
    AcceptedCompletionWorkSourceV1,
    completion_terminal_work_source_bytes,
)
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
    first_path_completion_request_fingerprint,
)
from chiplog.capabilities.agent_loop.post_terminal_contracts import (
    PrepareTerminalWork,
    WorkCommandIdentity,
)
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    manifest_ref,
    run_ref,
)
from chiplog.capabilities.effects.fences import NonSchedulerFence
from chiplog.capabilities.effects.h1_scoped_preparation import (
    prepare_h1_scoped_delivery,
    prepare_h1_scoped_delivery_v2,
)
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryOwnerCallV1,
    H1ScopedDeliveryRouteV1,
    PreparedH1ScopedDeliveryV1,
    PrepareH1ScopedDeliveryV1,
)
from chiplog.capabilities.effects.h1_scoped_preparation_v2_contracts import (
    H1ScopedDeliveryOwnerCallV2,
    H1ScopedDeliveryRouteV2,
    PreparedH1ScopedDeliveryV2,
    PrepareH1ScopedDeliveryV2,
)


class H1ScopedTerminalWorkError(ValueError):
    """The selected scoped Effects exchange cannot source terminal work."""


def build_h1_scoped_terminal_work_request(
    *,
    completion_request: PrepareExecutionCompletionFirstPathV2,
    completion_result: PreparedExecutionCompletion,
    effects_request: PrepareH1ScopedDeliveryV1,
    effects_result: PreparedH1ScopedDeliveryV1,
) -> PrepareTerminalWork:
    """Build the registered terminal request from one authenticated scoped exchange."""
    if (
        type(completion_request) is not PrepareExecutionCompletionFirstPathV2
        or type(completion_result) is not PreparedExecutionCompletion
        or type(effects_request) is not PrepareH1ScopedDeliveryV1
        or type(effects_result) is not PreparedH1ScopedDeliveryV1
    ):
        raise H1ScopedTerminalWorkError("scoped terminal work inputs differ")
    try:
        completion_bytes = completion_request.canonical_bytes()
        result_bytes = completion_result.canonical_bytes()
        effects_bytes = effects_request.canonical_bytes()
        effects_result_bytes = effects_result.canonical_bytes()
        if (
            PrepareExecutionCompletionFirstPathV2.model_validate_json(
                completion_bytes
            ).canonical_bytes()
            != completion_bytes
            or PreparedExecutionCompletion.model_validate_json(result_bytes).canonical_bytes()
            != result_bytes
            or PrepareH1ScopedDeliveryV1.model_validate_json(effects_bytes).canonical_bytes()
            != effects_bytes
            or PreparedH1ScopedDeliveryV1.model_validate_json(
                effects_result_bytes
            ).canonical_bytes()
            != effects_result_bytes
        ):
            raise ValueError("noncanonical scoped terminal predecessor")
    except ValueError as error:
        raise H1ScopedTerminalWorkError("scoped terminal evidence is noncanonical") from error
    if (
        completion_result.source_request_fingerprint
        != first_path_completion_request_fingerprint(completion_request)
        or effects_request.original_completion_request != completion_request
        or effects_request.prepared_completion != completion_result
        or effects_result.source_request_fingerprint != hashlib.sha256(effects_bytes).hexdigest()
        or effects_result.authority_evidence != effects_request.authority_evidence
        or effects_result.precursor_request != effects_request.precursor_request
        or effects_result.retained_sources != effects_request.retained_sources
        or effects_result.intent_request.identity != effects_request.identity
        or effects_result.intent_request.expected_intent != effects_request.expected_intent
        or effects_result.intent_request.current != effects_request.current
        or effects_result.intent_request.fence != effects_request.fence
        or effects_result.intent_request.intent.intent_id != effects_request.intent_id
    ):
        raise H1ScopedTerminalWorkError("scoped Effects exchange differs from completion source")
    if not isinstance(effects_request.fence, NonSchedulerFence):
        raise H1ScopedTerminalWorkError("scoped Effects fence is not a runtime generation source")
    call = H1ScopedDeliveryOwnerCallV1(
        route=H1ScopedDeliveryRouteV1(
            tenant_id=completion_request.run.tenant,
            database_id=completion_request.source.database_id,
            worker_session_id=completion_request.run.worker_session,
            broker_epoch=0,
            runtime_generation=effects_request.fence.runtime_generation,
            broker_session_id="historical-recovery-broker",
            owner_session_id="historical-recovery-effects",
            request_id=effects_request.identity.command_id,
        ),
        request=effects_request,
        request_digest=hashlib.sha256(effects_bytes).hexdigest(),
    )
    expected_effects = prepare_h1_scoped_delivery(call)
    if (
        type(expected_effects) is not PreparedH1ScopedDeliveryV1
        or expected_effects != effects_result
    ):
        raise H1ScopedTerminalWorkError("scoped Effects derivation differs from selected source")
    source = AcceptedCompletionWorkSourceV1(
        original_completion_request_bytes=completion_bytes,
        prepared_completion_bytes=result_bytes,
        terminal_run=completion_result.run,
        terminal_run_head=run_ref(completion_result.run),
        terminal_manifest=completion_result.terminal_manifest,
        terminal_manifest_head=manifest_ref(completion_result.terminal_manifest),
        ordered_open_obligations=completion_result.terminal_manifest.complete_open_original_obligations,
    )
    if source.ordered_open_obligations:
        raise H1ScopedTerminalWorkError("scoped terminal work requires zero open obligations")
    return PrepareTerminalWork(
        identity=WorkCommandIdentity(
            tenant_id=source.terminal_run.tenant,
            command_id=source.terminal_manifest.command_id,
        ),
        terminal_run=source.terminal_run,
        original_terminalization_request=completion_terminal_work_source_bytes(source),
        terminal_manifest=source.terminal_manifest_head,
        ordered_open_obligations=source.ordered_open_obligations,
    )


def build_h1_scoped_v2_terminal_work_request(
    *,
    completion_request: PrepareExecutionCompletionFirstPathV2,
    completion_result: PreparedExecutionCompletion,
    effects_request: PrepareH1ScopedDeliveryV2,
    effects_result: PreparedH1ScopedDeliveryV2,
) -> PrepareTerminalWork:
    """Build terminal work from the exact dispatch-V2 scoped owner exchange.

    This is deliberately separate from the V1 helper: accepting a V2 value
    through the V1 decoder would erase the retained wire-version boundary.
    """
    if (
        type(completion_request) is not PrepareExecutionCompletionFirstPathV2
        or type(completion_result) is not PreparedExecutionCompletion
        or type(effects_request) is not PrepareH1ScopedDeliveryV2
        or type(effects_result) is not PreparedH1ScopedDeliveryV2
    ):
        raise H1ScopedTerminalWorkError("V2 scoped terminal work inputs differ")
    try:
        completion_bytes = completion_request.canonical_bytes()
        result_bytes = completion_result.canonical_bytes()
        effects_bytes = effects_request.canonical_bytes()
        effects_result_bytes = effects_result.canonical_bytes()
        if (
            PrepareExecutionCompletionFirstPathV2.model_validate_json(
                completion_bytes
            ).canonical_bytes()
            != completion_bytes
            or PreparedExecutionCompletion.model_validate_json(result_bytes).canonical_bytes()
            != result_bytes
            or PrepareH1ScopedDeliveryV2.model_validate_json(effects_bytes).canonical_bytes()
            != effects_bytes
            or PreparedH1ScopedDeliveryV2.model_validate_json(
                effects_result_bytes
            ).canonical_bytes()
            != effects_result_bytes
        ):
            raise ValueError("noncanonical V2 scoped terminal predecessor")
    except ValueError as error:
        raise H1ScopedTerminalWorkError("V2 scoped terminal evidence is noncanonical") from error
    if (
        completion_result.source_request_fingerprint
        != first_path_completion_request_fingerprint(completion_request)
        or effects_request.original_completion_request != completion_request
        or effects_request.prepared_completion != completion_result
        or effects_result.source_request_fingerprint != hashlib.sha256(effects_bytes).hexdigest()
        or effects_result.authority_evidence != effects_request.authority_evidence
        or effects_result.precursor_request != effects_request.precursor_request
        or effects_result.retained_sources != effects_request.retained_sources
        or effects_result.intent_request.identity != effects_request.identity
        or effects_result.intent_request.expected_intent != effects_request.expected_intent
        or effects_result.intent_request.current != effects_request.current
        or effects_result.intent_request.fence != effects_request.fence
        or effects_result.intent_request.intent.intent_id != effects_request.intent_id
    ):
        raise H1ScopedTerminalWorkError("V2 scoped Effects exchange differs from completion source")
    if not isinstance(effects_request.fence, NonSchedulerFence):
        raise H1ScopedTerminalWorkError(
            "V2 scoped Effects fence is not a runtime generation source"
        )
    call = H1ScopedDeliveryOwnerCallV2(
        route=H1ScopedDeliveryRouteV2(
            tenant_id=completion_request.run.tenant,
            database_id=completion_request.source.database_id,
            worker_session_id=completion_request.run.worker_session,
            broker_epoch=0,
            runtime_generation=effects_request.fence.runtime_generation,
            broker_session_id="historical-recovery-broker",
            owner_session_id="historical-recovery-effects",
            request_id=effects_request.identity.command_id,
        ),
        request=effects_request,
        request_digest=hashlib.sha256(effects_bytes).hexdigest(),
    )
    expected_effects = prepare_h1_scoped_delivery_v2(call)
    if (
        type(expected_effects) is not PreparedH1ScopedDeliveryV2
        or expected_effects != effects_result
    ):
        raise H1ScopedTerminalWorkError("V2 scoped Effects derivation differs from selected source")
    source = AcceptedCompletionWorkSourceV1(
        original_completion_request_bytes=completion_bytes,
        prepared_completion_bytes=result_bytes,
        terminal_run=completion_result.run,
        terminal_run_head=run_ref(completion_result.run),
        terminal_manifest=completion_result.terminal_manifest,
        terminal_manifest_head=manifest_ref(completion_result.terminal_manifest),
        ordered_open_obligations=completion_result.terminal_manifest.complete_open_original_obligations,
    )
    if source.ordered_open_obligations:
        raise H1ScopedTerminalWorkError("V2 scoped terminal work requires zero open obligations")
    return PrepareTerminalWork(
        identity=WorkCommandIdentity(
            tenant_id=source.terminal_run.tenant,
            command_id=source.terminal_manifest.command_id,
        ),
        terminal_run=source.terminal_run,
        original_terminalization_request=completion_terminal_work_source_bytes(source),
        terminal_manifest=source.terminal_manifest_head,
        ordered_open_obligations=source.ordered_open_obligations,
    )
