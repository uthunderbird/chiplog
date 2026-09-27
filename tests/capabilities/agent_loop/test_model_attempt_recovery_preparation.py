"""Bounded native-v2 model-attempt recovery producer witnesses."""

import hashlib

from tests.support.execution_fan_out import bind_run, fixture
from tests.support.execution_versions import execution_run_v3

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.model_attempt_recovery_contracts import (
    PreparedLateExecutionResponse,
    PreparedModelAttemptReplacement,
    RegisteredModelNoExposure,
    ReplaceExecutionModelAttempt,
    RetainLateExecutionResponse,
)
from chiplog.capabilities.agent_loop.model_attempt_recovery_preparation import (
    prepare_late_execution_response,
    prepare_model_attempt_replacement,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_source_contracts import (
    MODEL_PRE_EMISSION_CAS_READER,
    MODEL_PRE_EMISSION_CAS_SCHEMA,
    ModelPreEmissionCASSource,
    RecoveryProofSourceMember,
)


def _head(subject_id: str, head: str, fingerprint: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject_id, revision=Present(head=head, fingerprint=fingerprint)
    )


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


async def _prepared() -> ExecutionRunRecord:
    captured = await fixture()
    original = captured.captured_run
    old_turn = original.turns[-1]
    attempt = old_turn.attempts[-1].model_copy(
        update={
            "state": "PREPARED_NOT_EMITTED",
            "response_base64": None,
            "receipt": None,
            "rejection": None,
            "head": "pending",
        }
    )
    attempt = attempt.model_copy(update={"head": "execution-attempt:" + attempt.digest()})
    turn = old_turn.model_copy(
        update={"state": "CALL_ACTIVE", "attempts": (attempt,), "head": "pending"}
    )
    turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
    return bind_run(
        captured,
        original.model_copy(update={"event": "ModelAttemptPrepared", "turns": (turn,)}),
    ).captured_run


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return _head(run.run_id, run.head, run.digest())


def _attempt_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    attempt = run.turns[-1].attempts[-1]
    return _head(attempt.attempt_id, attempt.head, attempt.digest())


def _request_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    request = run.turns[-1].attempts[-1].request.encode()
    fingerprint = _digest(request)
    return _head(run.turns[-1].attempts[-1].request, "request:" + fingerprint, fingerprint)


def _manifest_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    manifest = run.turns[-1].attempts[-1].manifest
    return _head("manifest:" + manifest.digest(), "record:" + manifest.digest(), manifest.digest())


def _source_and_request(
    run: ExecutionRunRecord,
) -> tuple[RecoveryProofSourceMember, ReplaceExecutionModelAttempt]:
    attempt = run.turns[-1].attempts[-1]
    body = ModelPreEmissionCASSource(
        registry=_head("registry", "record:registry", _digest(b"registry")),
        original_run=_run_ref(run),
        original_attempt=_attempt_ref(run),
        immutable_request=_request_ref(run),
        visibility_manifest=_manifest_ref(run),
        lineage_id=attempt.lineage_id,
        selector_generation=attempt.generation,
        provider_contract=attempt.provider_contract,
        recipient=attempt.recipient,
        observed_emission_head=_head("emission", "record:emission", _digest(b"emission")),
        tenant_id=run.tenant,
        database_id="database",
        authenticated_model_adapter=_head("adapter", "record:adapter", _digest(b"adapter")),
        model_worker_session=_head(run.worker_session, "record:session", _digest(b"session")),
        permanently_fenced_generation=attempt.generation,
        selected_pre_emission_cas_decision=_head(
            "selected-cas", "record:selected-cas", _digest(b"selected-cas")
        ),
    )
    raw = body.canonical_bytes()
    fingerprint = _digest(raw)
    source = RecoveryProofSourceMember(
        reader_id=MODEL_PRE_EMISSION_CAS_READER,
        source_kind="MODEL_PRE_EMISSION_CAS",
        schema_id=MODEL_PRE_EMISSION_CAS_SCHEMA,
        logical_subject_id=body.original_attempt.subject_id,
        source_record_id=MODEL_PRE_EMISSION_CAS_SCHEMA + ":" + fingerprint,
        canonical_source_bytes=raw,
        fingerprint=fingerprint,
    )
    proof = RegisteredModelNoExposure(
        registry=body.registry,
        proof=_head(source.logical_subject_id, source.source_record_id, source.fingerprint),
        original_run=body.original_run,
        original_attempt=body.original_attempt,
        lineage_id=body.lineage_id,
        selector_generation=body.selector_generation,
        immutable_request=body.immutable_request,
        visibility_manifest=body.visibility_manifest,
        provider_contract=body.provider_contract,
        recipient=body.recipient,
        observed_emission_head=body.observed_emission_head,
        source_schema=source.schema_id,
        canonical_source_bytes=source.canonical_source_bytes,
    )
    from chiplog.capabilities.agent_loop.recovery_contracts import NonSchedulerFence, NotApplicable

    request = ReplaceExecutionModelAttempt(
        command_id="replace",
        run=run,
        selected_attempt=_attempt_ref(run),
        expected_selector=attempt.generation,
        no_exposure=proof,
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=run.worker_session,
            runtime_generation="generation",
        ),
    )
    return source, request


async def test_replacement_requires_exact_prepared_selected_attempt_and_source_member() -> None:
    run = await _prepared()
    source, request = _source_and_request(run)
    result = prepare_model_attempt_replacement(
        request, source, expected_tenant_id=run.tenant, expected_database_id="database"
    )
    assert isinstance(result, PreparedModelAttemptReplacement)
    replacement = result.run.turns[-1].attempts[-1]
    assert result.run.turns[-1].selector == 1
    assert replacement.generation == 1
    assert replacement.manifest.generation == 1
    assert (
        result.run.turns[-1].attempts[0].canonical_bytes()
        == run.turns[-1].attempts[0].canonical_bytes()
    )

    for changed in (
        request.model_copy(
            update={"selected_attempt": _head("other", "record:other", _digest(b"other"))}
        ),
        request.model_copy(update={"expected_selector": 1}),
        request.model_copy(
            update={"no_exposure": request.no_exposure.model_copy(update={"lineage_id": "other"})}
        ),
    ):
        assert isinstance(
            prepare_model_attempt_replacement(
                changed, source, expected_tenant_id=run.tenant, expected_database_id="database"
            ),
            CallPreparationRejected,
        )


async def test_replacement_rejects_nonprepared_states_and_v3() -> None:
    run = await _prepared()
    source, request = _source_and_request(run)
    for state in (
        "EMITTED_OUTCOME_UNKNOWN",
        "RESPONSE_CAPTURED",
        "TERMINAL_REJECTED",
        "TERMINAL_ACCEPTED",
    ):
        changed = run.model_copy(
            update={
                "turns": (
                    run.turns[-1].model_copy(
                        update={
                            "attempts": (
                                run.turns[-1].attempts[-1].model_copy(update={"state": state}),
                            )
                        }
                    ),
                )
            }
        )
        request_with_state = request.model_copy(update={"run": changed})
        assert isinstance(
            prepare_model_attempt_replacement(
                request_with_state,
                source,
                expected_tenant_id=run.tenant,
                expected_database_id="database",
            ),
            CallPreparationRejected,
        )
    assert isinstance(
        prepare_model_attempt_replacement(
            request.model_copy(update={"run": execution_run_v3()}),
            source,
            expected_tenant_id="tenant",
            expected_database_id="database",
        ),
        CallPreparationRejected,
    )


async def test_replacement_rejects_wrong_generation_or_unfenced_source() -> None:
    run = await _prepared()
    source, request = _source_and_request(run)
    assert isinstance(
        prepare_model_attempt_replacement(
            request,
            source.model_copy(update={"fingerprint": "0" * 64}),
            expected_tenant_id=run.tenant,
            expected_database_id="database",
        ),
        CallPreparationRejected,
    )
    assert isinstance(
        prepare_model_attempt_replacement(
            request.model_copy(
                update={
                    "no_exposure": request.no_exposure.model_copy(update={"selector_generation": 1})
                }
            ),
            source,
            expected_tenant_id=run.tenant,
            expected_database_id="database",
        ),
        CallPreparationRejected,
    )


def test_late_response_preserves_original_generation_and_binary_custody() -> None:
    request = RetainLateExecutionResponse(
        command_id="late",
        original_run=_head("run", "record:run", _digest(b"run")),
        original_attempt=_head("attempt", "record:attempt", _digest(b"attempt")),
        original_manifest=_head("manifest", "record:manifest", _digest(b"manifest")),
        lineage_id="lineage",
        generation=0,
        receipt_token=_head("receipt", "record:receipt", _digest(b"receipt")),
        selected_custody=_head("custody", "record:custody", _digest(b"custody")),
        source_authentication=_head("source", "record:source", _digest(b"source")),
        raw_response=b"\xff\x00late",
        transport_receipt=b"\x80receipt",
    )
    result = prepare_late_execution_response(request)
    assert isinstance(result, PreparedLateExecutionResponse)
    assert result.record.request.raw_response == b"\xff\x00late"
    assert result.record.request.transport_receipt == b"\x80receipt"
    assert result.record.request.generation == 0


async def test_replacement_is_deterministic_and_does_not_call_model() -> None:
    run = await _prepared()
    source, request = _source_and_request(run)
    first = prepare_model_attempt_replacement(
        request, source, expected_tenant_id=run.tenant, expected_database_id="database"
    )
    second = prepare_model_attempt_replacement(
        request, source, expected_tenant_id=run.tenant, expected_database_id="database"
    )
    assert isinstance(first, PreparedModelAttemptReplacement)
    assert first.canonical_bytes() == second.canonical_bytes()
