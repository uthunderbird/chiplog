import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_contracts import (
    PrepareNativeSealedAccountingV3,
)
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_preparation import (
    prepare_native_sealed_accounting_v3,
)
from chiplog.capabilities.agent_loop.recovery_contracts import NotApplicable, Present
from chiplog.capabilities.agent_loop.recovery_domain import RecoveryHold, RecoveryIntegrityFault
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    ConsequentialAcceptedCall,
    InitializedCall,
    SuccessResult,
    TerminalCallFrontier,
)


def _request(frontiers):
    return PrepareNativeSealedAccountingV3(
        command_id="account-seal",
        selected_response=CallSubjectHead(
            subject_id="response",
            revision=Present(
                head="response-head",
                fingerprint="a" * 64,
            ),
        ),
        sealed_manifest_fingerprint="b" * 64,
        ordered_sealed_call_ids=tuple(item.original_call_id for item in frontiers),
        complete_ordered_frontiers=frontiers,
    )


def _two_frontiers():
    accepted = Present(head="accepted", fingerprint="2" * 64)
    execution = Present(head="execution", fingerprint="3" * 64)
    first = TerminalCallFrontier(
        original_call_id="call",
        response_id="response",
        acceptance=ConsequentialAcceptedCall(
            initialized=Present(head="initialized", fingerprint="1" * 64),
            accepted=accepted,
            execution_intent=execution,
            external_effect_intent=NotApplicable(),
            complete_acceptance_manifest=(accepted, execution),
        ),
        disposition=SuccessResult(
            terminal=Present(head="terminal", fingerprint="4" * 64),
            result=Present(head="result", fingerprint="5" * 64),
            recovered_outcome=NotApplicable(),
        ),
    )
    initialized = Present(head="initialized-2", fingerprint="6" * 64)
    second = TerminalCallFrontier(
        original_call_id="call-2",
        response_id="response",
        acceptance=InitializedCall(initialized=initialized),
        disposition=CancelledBeforeAcceptResult(
            terminal=Present(head="cancelled", fingerprint="7" * 64),
            not_executed_result=Present(head="not-executed", fingerprint="8" * 64),
            initialized_predecessor=initialized,
            recovered_outcome=NotApplicable(),
        ),
    )
    return first, second


def test_v3_accounts_complete_ordered_members_of_exact_selected_seal():
    first, second = _two_frontiers()

    prepared = prepare_native_sealed_accounting_v3(_request((first, second)))

    assert prepared.selected_response.subject_id == "response"
    assert tuple(call.original_call_id for call in prepared.complete_ordered_calls) == (
        "call",
        "call-2",
    )
    assert not hasattr(prepared, "continuation")


def test_v3_holds_reordered_members_instead_of_sorting_them():
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


def test_v3_rejects_duplicate_members_and_cross_seal_substitution():
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
