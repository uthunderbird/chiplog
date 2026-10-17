"""Shared builders extracted without changing their behavior."""

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NotApplicable,
    OriginalObligationBinding,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
    RecoveredOutcomeSubject,
    RecoveryRequiredResult,
    TerminalCallFrontier,
)


def head(name: str) -> Present:
    import hashlib

    return Present(head=name, fingerprint=hashlib.sha256(name.encode()).hexdigest())


def recovery_call(*, recovered: bool = False) -> TerminalCallFrontier:
    return TerminalCallFrontier(
        original_call_id="call",
        response_id="response",
        acceptance=ConsequentialAcceptedCall(
            initialized=head("initialized"),
            accepted=head("accepted"),
            execution_intent=head("execution"),
            external_effect_intent=NotApplicable(),
            complete_acceptance_manifest=(head("accepted"), head("execution")),
        ),
        disposition=RecoveryRequiredResult(
            terminal=head("terminal"),
            result=Absent(),
            obligation=OriginalObligationBinding(
                original_run_id="run",
                original_call_id="call",
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
            ),
            recovered_outcome=RecoveredOutcomeSubject(
                original_call_id="call",
                recovery_obligation_id="obligation",
                outcome=head("outcome") if recovered else Absent(),
            ),
        ),
    )
