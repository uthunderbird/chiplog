"""Real retained native bytes exercise projection, not live source authority."""

import hashlib
import json
import lzma
from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import RecoveredEffectStatusV1
from chiplog.capabilities.agent_loop.original_recovery_contracts import RecoveredCallOutcomeRecord
from chiplog.composition.r14_next_turn_records import (
    next_turn_command,
    retained_next_turn,
    selected_prior_turn_context,
)


@pytest.fixture(scope="module")
def selected():
    body = (Path(__file__).parent / "fixtures" /
            "r14_resolved_open_next_turn_v1.json.xz").read_bytes()
    assert hashlib.sha256(body).hexdigest() == (
        "ad7f65de88ea2d739679918d59a4872f508ee1a958c1194ea8f5a538409c053b"
    )
    raw = lzma.decompress(body)
    assert hashlib.sha256(raw).hexdigest() == (
        "451f8e8fecb82e960bdb093168ef3ac2e8e69e6c8f362ca8660eb90bf3869901"
    )
    evidence = retained_next_turn(json.loads(raw))
    assert next_turn_command(evidence).operation_kind == "agent_loop.execution-next-turn.v1"
    return raw, evidence


def _runtime(raw):
    journal = SimpleNamespace(entries=lambda: (("selected-native-next-turn", None, raw),))
    return SimpleNamespace(_loop_decisions=lambda: journal)


def test_resolved_open_context_preserves_original_terminal_and_exact_recovered_result(selected):
    raw, evidence = selected
    context = selected_prior_turn_context(_runtime(raw), evidence.result.run)
    response = evidence.request.cut.complete_ordered_responses[0]
    call = response.ordered_calls[0]
    sources = evidence.request.cut.complete_sources
    terminal, = (
        source for source in sources
        if source.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
        and source.subject.subject_id == call.original_call_id
    )
    recovered, = (
        source for source in sources
        if source.schema_id == "chiplog.loop.recovered-call-outcome.v1"
    )
    outcome = RecoveredCallOutcomeRecord.model_validate_json(recovered.canonical_record_bytes)
    status = RecoveredEffectStatusV1.model_validate_json(outcome.canonical_result_bytes)
    assert status.semantic_class == "R14_EFFECT_CONFIRMED"
    assert status.canonical_bytes() == outcome.canonical_result_bytes
    assert outcome.result_schema == "chiplog.loop.recovered-effect-status.v1"
    turn = evidence.request.run.turns[0]
    attempt = turn.attempts[turn.selector]
    assert context == [{
        "turn_id": turn.turn_id,
        "response_base64": attempt.response_base64,
        "rejection": attempt.rejection,
        "visibility": attempt.manifest.digest(),
        "results": [{
            "original_call_id": call.original_call_id,
            "terminal": json.loads(terminal.canonical_record_bytes),
            "result": outcome.model_dump(mode="json"),
        }],
    }]
    assert context[0]["results"][0]["terminal"]["kind"] == "RECOVERY_REQUIRED"


@pytest.mark.parametrize("mutation", (
    "missing-closure", "missing-reduction", "held-reduction", "foreign-obligation",
    "foreign-terminal", "foreign-outcome", "consumed-evidence",
    "omit-basis", "omit-closure", "omit-outcome", "omit-batch", "duplicate-outcome",
))
def test_resolved_open_context_requires_exact_original_proof_pair(selected, mutation):
    _, evidence = selected
    cut = evidence.request.cut
    summary = cut.original_closures[0]
    if mutation == "missing-closure":
        cut = cut.model_copy(update={"original_closures": ()})
    elif mutation == "missing-reduction":
        cut = cut.model_copy(update={"current_reductions": ()})
    elif mutation in {"held-reduction", "consumed-evidence"}:
        reduction = cut.current_reductions[0]
        changes = (
            {"consumability": "HOLD"} if mutation == "held-reduction"
            else {"ordered_consumed_evidence": (summary.accepted_evidence,)}
        )
        cut = cut.model_copy(update={"current_reductions": (reduction.model_copy(update=changes),)})
    elif mutation.startswith("foreign-"):
        if mutation == "foreign-obligation":
            changes = {"original": summary.original.model_copy(update={"obligation_id": "foreign"})}
        else:
            field = (
                "terminal_disposition" if mutation == "foreign-terminal" else "recovered_outcome"
            )
            changes = {field: getattr(summary, field).model_copy(update={"head": "foreign"})}
        cut = cut.model_copy(update={"original_closures": (summary.model_copy(update=changes),)})
    else:
        schema = {
            "omit-basis": "chiplog.loop.original-resolution-basis.v1",
            "omit-closure": "chiplog.loop.original-obligation-closure.v1",
            "omit-outcome": "chiplog.loop.recovered-call-outcome.v1",
            "omit-batch": "chiplog.loop.original-resolver-batch.v1",
            "duplicate-outcome": "chiplog.loop.recovered-call-outcome.v1",
        }[mutation]
        sources = tuple(source for source in cut.complete_sources if source.schema_id != schema)
        if mutation == "duplicate-outcome":
            source, = (source for source in cut.complete_sources if source.schema_id == schema)
            sources = (*cut.complete_sources, source)
        cut = cut.model_copy(update={"complete_sources": sources})
    changed = evidence.model_copy(update={
        "request": evidence.request.model_copy(update={"cut": cut}),
    })
    entry = {"kind": "DECIDED", "execution_next_turn_evidence": changed.canonical_bytes().decode()}
    # Shape decoding must succeed before the assertion tests the actual projection join.
    retained_next_turn(entry)
    raw = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError):
        selected_prior_turn_context(_runtime(raw), changed.result.run)
