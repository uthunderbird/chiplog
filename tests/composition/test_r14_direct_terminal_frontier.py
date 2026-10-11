"""Fast source-to-frontier checks for the selected R14 direct terminal."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import (
    DirectEffectTerminalClassificationV1,
)
from chiplog.capabilities.agent_loop.direct_terminal_contracts import (
    AcceptedDirectTerminalAccountingV1,
    AcceptedDirectTerminalCutV1,
    AcceptedDirectTerminalDispositionV1,
    AcceptedDirectTerminalResultV1,
    PreparedAcceptedDirectTerminalV1,
)
from chiplog.capabilities.agent_loop.direct_terminal_wire_contracts import (
    REQUEST_SCHEMA,
    RESULT_SCHEMA,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
)
from chiplog.capabilities.effects.contracts import DispatchSemanticBinding, ExactHead
from chiplog.composition.r14_accepted_direct_terminal_frontier import (
    accepted_direct_terminal_sealed_accounting,
    require_complete_direct_terminal_publication,
)
from chiplog.composition.r14_recovery_publication import _exchange
from chiplog.platform.broker import BrokerSession, PublicPortSuccess

_FINGERPRINT = "a" * 64


def _head(subject_id: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject_id,
        revision=Present(head="head:" + subject_id, fingerprint=_FINGERPRINT),
    )


def _frontier_fixture(
    kind: str,
) -> tuple[
    AcceptedDirectTerminalCutV1,
    PreparedAcceptedDirectTerminalV1,
    ConsequentialAcceptedCall,
]:
    call_id = "call:one"
    response_id = "response:one"
    cut_fingerprint = "c" * 64
    initialized = _head(call_id)
    accepted = _head("accepted:one")
    execution_intent = _head("execution-intent:one")
    effect_intent = _head("effect-intent:one")
    decision = _head("decision:one")
    semantic = DispatchSemanticBinding(
        normative_manifest="normative-v1",
        reducer_version="reducer-v1",
        transition_registry_version="transitions-v1",
        canonicalization_fingerprint_version="canonical-v1",
        adapter_contract_version="adapter-v1",
    )
    cut = cast(
        AcceptedDirectTerminalCutV1,
        SimpleNamespace(
            cut_fingerprint=cut_fingerprint,
            original_call_id=call_id,
            response_id=response_id,
            initialized=initialized,
            accepted=accepted,
            execution_intent=execution_intent,
            external_effect_intent=effect_intent,
            semantic_binding=semantic,
            selected_outcome_record_id="outcome:one",
            selected_outcome_fingerprint=_FINGERPRINT,
            selected_decision=decision,
            outcome_commit_sequence=4,
            publication_ordinal=7,
            batch_record_ids=("outcome:one", "evidence:one"),
        ),
    )
    classification = DirectEffectTerminalClassificationV1(
        mapping_id="R14_DIRECT_EFFECT_TERMINAL_V1",
        mapping_version="r14-direct-effect-terminal.v1",
        classifier_id="R14_R16_V2_OUTCOME_TRANSLATION",
        classifier_version="r14-r16-outcome-translation.v1",
        semantic_binding=semantic,
        semantic_class=(
            "R14_EFFECT_CONFIRMED" if kind == "SUCCEEDED" else "R14_EFFECT_FAILED_NO_EFFECT"
        ),
        disposition=kind,
        intent=ExactHead(
            subject_id="effect-intent:one", head="intent-head", fingerprint=_FINGERPRINT
        ),
        terminal=ExactHead(
            subject_id="effect-terminal:one", head="terminal-head", fingerprint=_FINGERPRINT
        ),
        ordered_evidence=(
            ExactHead(subject_id="evidence:one", head="evidence-head", fingerprint=_FINGERPRINT),
        ),
    )
    terminal = AcceptedDirectTerminalDispositionV1(
        kind=kind,
        terminal_id="accepted-direct-terminal-record:" + cut_fingerprint,
        source_cut_fingerprint=cut_fingerprint,
        original_call_id=call_id,
        response_id=response_id,
        accepted=accepted,
        external_effect_intent=effect_intent,
        selected_effect_terminal=classification.terminal,
        mapping_id="R14_DIRECT_EFFECT_TERMINAL_V1",
        mapping_version="r14-direct-effect-terminal.v1",
    )
    terminal_fingerprint = hashlib.sha256(terminal.canonical_bytes()).hexdigest()
    terminal_ref = CallSubjectHead(
        subject_id=terminal.terminal_id,
        revision=Present(
            head="record:" + terminal_fingerprint,
            fingerprint=terminal_fingerprint,
        ),
    )
    result = AcceptedDirectTerminalResultV1(
        result_id="accepted-direct-terminal-result:" + cut_fingerprint,
        source_cut_fingerprint=cut_fingerprint,
        original_call_id=call_id,
        terminal=terminal_ref,
        classification=classification,
    )
    result_fingerprint = hashlib.sha256(result.canonical_bytes()).hexdigest()
    result_ref = CallSubjectHead(
        subject_id=result.result_id,
        revision=Present(head="record:" + result_fingerprint, fingerprint=result_fingerprint),
    )
    accounting = AcceptedDirectTerminalAccountingV1(
        accounting_id="accepted-direct-terminal-accounting:" + cut_fingerprint,
        source_cut_fingerprint=cut_fingerprint,
        original_call_id=call_id,
        response_id=response_id,
        accepted=accepted,
        execution_intent=execution_intent,
        external_effect_intent=effect_intent,
        terminal=terminal_ref,
        result=result_ref,
        selected_outcome_record_id="outcome:one",
        selected_outcome_fingerprint=_FINGERPRINT,
        selected_decision=decision,
        outcome_commit_sequence=4,
        publication_ordinal=7,
        batch_record_ids=("outcome:one", "evidence:one"),
    )
    prepared = PreparedAcceptedDirectTerminalV1(
        source_request_fingerprint=_FINGERPRINT,
        accounting=accounting,
        terminal=terminal,
        result=result,
    )
    acceptance = ConsequentialAcceptedCall(
        initialized=initialized.revision,
        accepted=accepted.revision,
        execution_intent=execution_intent.revision,
        external_effect_intent=effect_intent.revision,
        complete_acceptance_manifest=(
            accepted.revision,
            execution_intent.revision,
            effect_intent.revision,
        ),
    )
    return cut, prepared, acceptance


@pytest.mark.parametrize("kind", ("SUCCEEDED", "FAILED_DEFINITE"))
def test_selected_direct_terminal_builds_exact_single_call_frontier(kind: str) -> None:
    cut, prepared, acceptance = _frontier_fixture(kind)

    accounting = accepted_direct_terminal_sealed_accounting(
        cut, prepared, acceptance, (cut.original_call_id,), _FINGERPRINT
    )

    assert accounting.response_id == cut.response_id
    assert accounting.sealed_manifest_fingerprint == _FINGERPRINT
    assert tuple(call.original_call_id for call in accounting.calls) == (cut.original_call_id,)
    assert accounting.calls[0].disposition.kind == kind


def test_selected_direct_terminal_rejects_manifest_subset_and_changed_record_ref() -> None:
    cut, prepared, acceptance = _frontier_fixture("SUCCEEDED")
    with pytest.raises(ValueError, match="does not join"):
        accepted_direct_terminal_sealed_accounting(
            cut,
            prepared,
            acceptance,
            (cut.original_call_id, "call:omitted"),
            _FINGERPRINT,
        )

    changed_accounting = prepared.accounting.model_copy(update={"result": _head("foreign-result")})
    with pytest.raises(ValueError, match="does not join"):
        accepted_direct_terminal_sealed_accounting(
            cut,
            prepared.model_copy(update={"accounting": changed_accounting}),
            acceptance,
            (cut.original_call_id,),
            _FINGERPRINT,
        )
    changed_acceptance = acceptance.model_copy(
        update={"accepted": _head("foreign-acceptance").revision}
    )
    with pytest.raises(ValueError, match="does not join"):
        accepted_direct_terminal_sealed_accounting(
            cut,
            prepared,
            changed_acceptance,
            (cut.original_call_id,),
            _FINGERPRINT,
        )


@pytest.mark.asyncio
async def test_direct_terminal_exchange_uses_its_registered_wire_schema() -> None:
    _, prepared, _ = _frontier_fixture("SUCCEEDED")
    request = SimpleNamespace(canonical_bytes=lambda: b"{}")

    class Engine:
        sent = None

        def session(self, owner_id: str) -> BrokerSession:
            return BrokerSession(
                tenant_id="tenant",
                broker_epoch=1,
                generation_id="generation",
                owner_id=owner_id,
                session_id=owner_id + ":session",
            )

        async def call(self, sent):
            self.sent = sent
            return PublicPortSuccess(
                request_id=sent.request_id,
                responder=sent.callee,
                schema_id=RESULT_SCHEMA,
                canonical_payload=prepared.canonical_bytes(),
            )

    engine = Engine()
    runtime = SimpleNamespace(
        _supervisor=SimpleNamespace(runtime=lambda: engine), _tenant_id="tenant"
    )
    result, sent, _ = await _exchange(
        runtime,
        request,
        "agent_loop.prepare_accepted_direct_terminal.v1",
        PreparedAcceptedDirectTerminalV1,
        request_schema=REQUEST_SCHEMA,
    )

    assert engine.sent is sent
    assert sent.schema_id == REQUEST_SCHEMA
    assert result == prepared


@pytest.mark.parametrize(
    ("state", "current", "anchor", "selected", "selected_anchor"),
    [
        ("ABSENT", "a" * 64, "a" * 64, "a" * 64, "a" * 64),
        ("COMPLETE", "a" * 64, "a" * 64, "b" * 64, "a" * 64),
        ("COMPLETE", "a" * 64, "a" * 64, "a" * 64, "b" * 64),
        ("COMPLETE", "a" * 64, "b" * 64, "a" * 64, "a" * 64),
    ],
)
def test_direct_terminal_frontier_rejects_pending_or_changed_physical_cut(
    state: str,
    current: str,
    anchor: str,
    selected: str,
    selected_anchor: str,
) -> None:
    with pytest.raises(ValueError, match="not completely materialized"):
        require_complete_direct_terminal_publication(
            state, current, anchor, selected, selected_anchor
        )


def test_direct_terminal_frontier_accepts_only_complete_three_way_cut() -> None:
    require_complete_direct_terminal_publication(
        "COMPLETE", _FINGERPRINT, _FINGERPRINT, _FINGERPRINT, _FINGERPRINT
    )
