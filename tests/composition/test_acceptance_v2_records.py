"""Actual pure owner results checked independently; runtime authenticity is separate."""

import json

import pytest

from chiplog.capabilities.effects import dispatch_v2
from chiplog.composition.r14_acceptance_v2_contracts import RetainedAcceptancePreparationV2
from chiplog.composition.r14_acceptance_v2_records import (
    build_acceptance_envelope,
    verify_acceptance_envelope,
)
from tests.support.acceptance_v2 import prepared_acceptance


def test_both_real_owner_outputs_form_exact_three_member_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = prepared_acceptance()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("verifier must not rerun effects owner")

    monkeypatch.setattr(dispatch_v2, "prepare_dispatch", forbidden)
    monkeypatch.setattr(dispatch_v2, "evaluate_precursor", forbidden)
    envelope = build_acceptance_envelope(retained)
    assert tuple(row.owner for row in envelope.complete_records) == (
        "agent_loop",
        "agent_loop",
        "effects",
    )
    assert envelope.complete_records[2].schema_id == "chiplog.effects.dispatch-record.v2"
    assert envelope.complete_records[2].record_id == retained.effects_proposal.record.head
    verify_acceptance_envelope(retained, envelope)
    for order in ((0, 2, 1), (0, 1, 1)):
        with pytest.raises(ValueError):
            verify_acceptance_envelope(
                retained,
                envelope.model_copy(
                    update={"complete_records": tuple(envelope.complete_records[i] for i in order)}
                ),
            )


@pytest.mark.parametrize(
    "path,value",
    [
        (("effects_request", "current", "command_fingerprint"), "f" * 64),
        (("effects_request", "current", "observed_time_ns"), 20),
        (
            ("effects_request", "command", "intent", "mandate", "origin", "original_call_id"),
            "foreign",
        ),
        (("effects_request", "command", "intent", "mandate", "origin", "tool_schema"), "foreign"),
        (("effects_request", "command", "intent", "mandate", "origin", "sealed_arguments"), "e30="),
        (("effects_request", "command", "intent", "mandate", "principal_id"), "foreign"),
        (
            ("effects_request", "command", "intent", "mandate", "recipient", "canonical_address"),
            "eA==",
        ),
        (
            (
                "effects_request",
                "command",
                "intent",
                "acquisition",
                "precursor_result",
                "request_digest",
            ),
            "f" * 64,
        ),
        (("effects_proposal", "snapshot", "state"), "DISPATCH_AUTHORIZED"),
        (("effects_proposal", "record", "fingerprint"), "f" * 64),
        (("loop_request", "binding", "cut", "tenant_commit_sequence"), 1),
    ],
)
def test_changed_retained_owner_graph_rejects(path: tuple[str, ...], value: object) -> None:
    wire = json.loads(prepared_acceptance().canonical_bytes())
    target = wire
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    changed = RetainedAcceptancePreparationV2.model_validate_json(json.dumps(wire))
    with pytest.raises(ValueError):
        build_acceptance_envelope(changed)


def test_outer_retention_roundtrip_preserves_effects_original_canonical_bytes() -> None:
    retained = prepared_acceptance()
    restored = RetainedAcceptancePreparationV2.model_validate_json(retained.canonical_bytes())
    assert restored.effects_request.canonical_bytes() == retained.effects_request.canonical_bytes()
    assert (
        restored.effects_proposal.canonical_bytes() == retained.effects_proposal.canonical_bytes()
    )
    assert build_acceptance_envelope(restored) == build_acceptance_envelope(retained)


@pytest.mark.parametrize("mutation", ["reorder", "omit", "foreign-call"])
def test_recomputed_owner_results_cannot_replace_original_complete_bundle(mutation: str) -> None:
    mandate = prepared_acceptance().effects_proposal.snapshot.intent.mandate
    members = mandate.bundle_members
    if mutation == "reorder":
        changed = tuple(reversed(members))
    elif mutation == "omit":
        changed = members[:1]
    else:
        changed = tuple(
            member.model_copy(update={"subject_id": "foreign/" + member.subject_id})
            for member in members
        )
    retained = prepared_acceptance(mandate_updates={"bundle_members": changed})
    # Both actual owners produced these internally consistent results; the
    # independent original-call join must still reject the false interpretation.
    with pytest.raises(ValueError, match="original call, payload or complete bundle"):
        build_acceptance_envelope(retained)


def test_recomputed_adoption_digest_does_not_hide_changed_display() -> None:
    retained = prepared_acceptance(display_bytes=b"different terms")
    with pytest.raises(ValueError, match="original acquisition"):
        build_acceptance_envelope(retained)


def test_recomputed_effects_output_cannot_use_another_materialization_cut() -> None:
    retained = prepared_acceptance()
    current = retained.effects_request.current
    cut = current.observation.cut.model_copy(update={"materialization_commitment": "b" * 64})
    observation = current.observation.model_copy(update={"cut": cut})
    request = retained.effects_request.model_copy(
        update={"current": current.model_copy(update={"observation": observation})}
    )
    changed = retained.model_copy(
        update={
            "effects_request": request,
            "effects_proposal": dispatch_v2.prepare_dispatch(request),
        }
    )
    with pytest.raises(ValueError, match="cross-owner"):
        build_acceptance_envelope(changed)


def test_consistently_recomputed_foreign_clock_does_not_extend_closed_policy() -> None:
    horizon = prepared_acceptance().effects_proposal.snapshot.intent.mandate.horizon
    retained = prepared_acceptance(
        mandate_updates={"horizon": horizon.model_copy(update={"clock_contract": "foreign-clock"})}
    )
    with pytest.raises(ValueError, match="unregistered call policy"):
        build_acceptance_envelope(retained)
