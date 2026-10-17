import pytest
from tests.support.capabilities.agent_loop.native_sealed_accounting_v3 import (
    _request,
    _two_frontiers,
)

from chiplog.capabilities.agent_loop._original_recovery_process import (
    PREPARE_SEALED_ACCOUNTING_V3_OPERATION,
)
from chiplog.capabilities.agent_loop._original_recovery_process import (
    ROUTES as ORIGINAL_RECOVERY_ROUTES,
)
from chiplog.capabilities.agent_loop._original_recovery_process import (
    dispatch as dispatch_original_recovery,
)
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_contracts import (
    PrepareNativeSealedAccountingV3,
)
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_preparation import (
    prepare_native_sealed_accounting_v3,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NotApplicable,
    OriginalObligationBinding,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_domain import RecoveryHold, RecoveryIntegrityFault
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
    DefiniteFailureResult,
    RecoveredOutcomeSubject,
    RecoveryRequiredResult,
    SuccessResult,
    TerminalCallFrontier,
)


def _accepted_frontier(call_id, disposition):
    initialized = Present(head=f"initialized-{call_id}", fingerprint="1" * 64)
    accepted = Present(head=f"accepted-{call_id}", fingerprint="2" * 64)
    execution = Present(head=f"execution-{call_id}", fingerprint="3" * 64)
    return TerminalCallFrontier(
        original_call_id=call_id,
        response_id="response",
        acceptance=ConsequentialAcceptedCall(
            initialized=initialized,
            accepted=accepted,
            execution_intent=execution,
            external_effect_intent=NotApplicable(),
            complete_acceptance_manifest=(accepted, execution),
        ),
        disposition=disposition,
    )


def test_v3_accounts_complete_ordered_members_of_exact_selected_seal() -> None:
    first, second = _two_frontiers()

    prepared = prepare_native_sealed_accounting_v3(_request((first, second)))

    assert prepared.selected_response.subject_id == "response"
    assert tuple(call.original_call_id for call in prepared.complete_ordered_calls) == (
        "call",
        "call-2",
    )
    assert not hasattr(prepared, "continuation")


def test_v3_accounts_three_accepted_members_with_distinct_existing_outcomes() -> None:
    recovery_call_id = "call-recovery"
    obligation = OriginalObligationBinding(
        original_run_id="run",
        original_call_id=recovery_call_id,
        obligation_id="obligation",
        obligation_stream_id="obligation-stream",
        obligation_head="obligation-head",
        closure_predicate_id="closure",
        closure_predicate_version="v1",
        resolver_id="resolver",
        resolver_version="v1",
        reducer_id="reducer",
        reducer_version="v1",
        evidence_stream_id="evidence-stream",
        evidence_head=Absent(),
    )
    frontiers = (
        _accepted_frontier(
            "call-success",
            SuccessResult(
                terminal=Present(head="terminal-success", fingerprint="4" * 64),
                result=Present(head="result-success", fingerprint="5" * 64),
                recovered_outcome=NotApplicable(),
            ),
        ),
        _accepted_frontier(
            "call-failure",
            DefiniteFailureResult(
                terminal=Present(head="terminal-failure", fingerprint="6" * 64),
                result=Present(head="result-failure", fingerprint="7" * 64),
                recovered_outcome=NotApplicable(),
            ),
        ),
        _accepted_frontier(
            recovery_call_id,
            RecoveryRequiredResult(
                terminal=Present(head="terminal-recovery", fingerprint="8" * 64),
                obligation=obligation,
                result=Absent(),
                recovered_outcome=RecoveredOutcomeSubject(
                    original_call_id=recovery_call_id,
                    recovery_obligation_id=obligation.obligation_id,
                    outcome=Absent(),
                ),
            ),
        ),
    )

    prepared = prepare_native_sealed_accounting_v3(_request(frontiers))

    assert tuple(call.original_call_id for call in prepared.complete_ordered_calls) == (
        "call-success",
        "call-failure",
        "call-recovery",
    )
    assert tuple(call.disposition.kind for call in prepared.complete_ordered_calls) == (
        "SUCCEEDED",
        "FAILED_DEFINITE",
        "RECOVERY_REQUIRED",
    )


def test_v3_holds_reordered_members_instead_of_sorting_them() -> None:
    first, second = _two_frontiers()

    with pytest.raises(RecoveryHold, match="ordered sealed manifest"):
        prepare_native_sealed_accounting_v3(
            PrepareNativeSealedAccountingV3(
                **{
                    **_request((first, second)).model_dump(),
                    "complete_ordered_frontiers": (second, first),
                }
            )
        )


def test_v3_rejects_duplicate_members_and_cross_seal_substitution() -> None:
    first, second = _two_frontiers()
    with pytest.raises(RecoveryIntegrityFault, match="duplicate"):
        prepare_native_sealed_accounting_v3(
            PrepareNativeSealedAccountingV3(
                **{
                    **_request((first, second)).model_dump(),
                    "ordered_sealed_call_ids": ("call", "call"),
                }
            )
        )

    with pytest.raises(RecoveryIntegrityFault, match="different response seal"):
        prepare_native_sealed_accounting_v3(
            _request((first, second.model_copy(update={"response_id": "other-response"})))
        )


def test_v3_static_owner_route_uses_its_versioned_wire_schemas() -> None:
    first, second = _two_frontiers()
    request = _request((first, second))
    route = next(
        row for row in ORIGINAL_RECOVERY_ROUTES if row[0] == PREPARE_SEALED_ACCOUNTING_V3_OPERATION
    )

    response = dispatch_original_recovery(
        PREPARE_SEALED_ACCOUNTING_V3_OPERATION, request.canonical_bytes()
    )

    assert route[3] == "chiplog.agent-loop.prepare-native-sealed-accounting.v3"
    assert response["schema_id"] == "chiplog.agent-loop.prepared-native-sealed-accounting.v3"
