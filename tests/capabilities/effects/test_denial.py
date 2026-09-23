"""Public denial contract and owner algebra; these fixtures prove no source provenance."""

from typing import get_args

import pytest
from pydantic import ValidationError
from tests.support.effects import child, head
from tests.support.effects_denial import make_denial_request

from chiplog.capabilities.effects.contracts import (
    AttemptState,
    DispatchAuthorization,
)
from chiplog.capabilities.effects.denial import prepare_denial
from chiplog.capabilities.effects.denial_contracts import (
    CancelDecision,
    DenialPreparationRequest,
    DenyingAuthority,
    DenyingSources,
    HoldDecision,
    SupersedeDecision,
)
from chiplog.capabilities.effects.domain import EffectRuleViolation


@pytest.fixture
def denial_request() -> DenialPreparationRequest:
    return make_denial_request()


def test_public_denial_request_roundtrips_without_send_authority(
    denial_request: DenialPreparationRequest,
) -> None:
    assert (
        DenialPreparationRequest.model_validate_json(denial_request.canonical_bytes())
        == denial_request
    )
    assert denial_request.expected.records[0].snapshot.intent.authority.valid_until_ns == 100
    assert denial_request.current.observed_time_ns == 200
    with pytest.raises(ValidationError):
        DenyingAuthority.model_validate(
            denial_request.command.authority.model_dump() | {"grant": True}
        )


@pytest.mark.parametrize("name", tuple(DenyingSources.model_fields))
def test_denial_cannot_omit_or_replace_required_source_with_unavailable(
    denial_request: DenialPreparationRequest, name: str
) -> None:
    values = denial_request.command.authority.sources.model_dump()
    del values[name]
    with pytest.raises(ValidationError):
        DenyingSources.model_validate(values)
    values[name] = {"kind": "UNAVAILABLE", "reason": "UNIMPLEMENTED", "detail": "no issuer"}
    with pytest.raises(ValidationError):
        DenyingSources.model_validate(values)


def with_authority(value: DenialPreparationRequest, **changes: object) -> DenialPreparationRequest:
    authority = value.command.authority.model_copy(update=changes)
    return value.model_copy(
        update={
            "command": value.command.model_copy(update={"authority": authority}),
            "current": value.current.model_copy(update={"authority": authority}),
        }
    )


def with_snapshot(value: DenialPreparationRequest, **changes: object) -> DenialPreparationRequest:
    record = value.expected.records[-1]
    return value.model_copy(
        update={
            "expected": value.expected.model_copy(
                update={
                    "records": (
                        record.model_copy(
                            update={"snapshot": record.snapshot.model_copy(update=changes)}
                        ),
                    )
                }
            )
        }
    )


def authorized(value: DenialPreparationRequest) -> DenialPreparationRequest:
    original = value.expected.records[-1].snapshot.intent
    authorization = DispatchAuthorization(
        authorization=head("old-authorization"),
        intent=value.command.intent,
        expected_attempt=head("pre-authorization-attempt"),
        semantics=original.semantics,
        current_authority=original.authority,
        fence=value.current.fence.model_copy(
            update={"worker_session_id": "old-worker", "runtime_generation": "old-generation"}
        ),
    )
    value = with_snapshot(value, state="DISPATCH_AUTHORIZED", authorizations=(authorization,))
    return value.model_copy(
        update={
            "command": value.command.model_copy(update={"authorization_to_retire": authorization})
        }
    )


@pytest.mark.parametrize(
    "decision",
    [
        HoldDecision(evidence=head("hold-policy")),
        CancelDecision(evidence=head("cancel-decision"), direct_act=head("cancel-act")),
        SupersedeDecision(
            evidence=head("supersede-decision"),
            successor_intent=head("separately-adopted-successor"),
            successor_adoption=head("successor-adoption"),
        ),
    ],
)
def test_fresh_denying_authority_preserves_expired_original_send_binding(
    denial_request: DenialPreparationRequest,
    decision: HoldDecision | CancelDecision | SupersedeDecision,
) -> None:
    value = with_authority(denial_request, decision=decision)
    before = value.canonical_bytes()
    prepared = prepare_denial(value)
    prior = value.expected.records[-1]
    result = prepared.record.snapshot
    assert result.state == decision.kind
    assert result.intent == prior.snapshot.intent
    assert result.intent.authority.valid_until_ns < value.current.observed_time_ns
    assert result.model_dump(exclude={"state", "attempt"}) == prior.snapshot.model_dump(
        exclude={"state", "attempt"}
    )
    assert prepared.record.source_command == value.command.canonical_bytes()
    assert prepared.record.predecessor == prior.record
    assert prepared.expected_store == value.expected
    assert prepared.exact_companion_manifest == ()
    assert value.canonical_bytes() == before


@pytest.mark.parametrize("deadline", [199, 200])
@pytest.mark.parametrize("source", [None, *DenyingSources.model_fields])
def test_expired_denying_authority_or_any_source_rejects_at_boundary(
    denial_request: DenialPreparationRequest, source: str | None, deadline: int
) -> None:
    if source is None:
        value = with_authority(denial_request, valid_until_ns=deadline)
    else:
        sources = denial_request.command.authority.sources
        value = with_authority(
            denial_request,
            sources=sources.model_copy(
                update={
                    source: getattr(sources, source).model_copy(update={"valid_until_ns": deadline})
                }
            ),
        )
    with pytest.raises(EffectRuleViolation, match="lease"):
        prepare_denial(value)
    assert prepare_denial(denial_request).record.snapshot.state == "CANCELLED_BEFORE_SEND"


@pytest.mark.parametrize("field", ["clock_contract", "clock_epoch"])
@pytest.mark.parametrize("source", [None, *DenyingSources.model_fields])
def test_current_denying_sources_cannot_cross_clock_contract_or_epoch(
    denial_request: DenialPreparationRequest, source: str | None, field: str
) -> None:
    if source is None:
        value = with_authority(denial_request, **{field: "foreign"})
    else:
        sources = denial_request.command.authority.sources
        value = with_authority(
            denial_request,
            sources=sources.model_copy(
                update={source: getattr(sources, source).model_copy(update={field: "foreign"})}
            ),
        )
    with pytest.raises(EffectRuleViolation, match="clock"):
        prepare_denial(value)


@pytest.mark.parametrize(
    "field",
    [
        None,
        "normative_manifest",
        "reducer_version",
        "transition_registry_version",
        "canonicalization_fingerprint_version",
        "adapter_contract_version",
    ],
)
def test_denial_requires_complete_original_interpreter(
    denial_request: DenialPreparationRequest, field: str | None
) -> None:
    semantics = denial_request.expected.records[0].snapshot.intent.semantics
    supported = None if field is None else semantics.model_copy(update={field: "unsupported"})
    value = denial_request.model_copy(
        update={
            "current": denial_request.current.model_copy(update={"supported_semantics": supported})
        }
    )
    with pytest.raises(EffectRuleViolation) as error:
        prepare_denial(value)
    assert error.value.code == "DISPATCH_VERSION_HOLD"


@pytest.mark.parametrize("state", get_args(AttemptState))
@pytest.mark.parametrize("disposition", ["hold", "cancel", "supersede"])
def test_denial_obeys_every_vision_state_without_clearing_crossed_outcome(
    denial_request: DenialPreparationRequest, state: AttemptState, disposition: str
) -> None:
    decisions: dict[str, HoldDecision | CancelDecision | SupersedeDecision] = {
        "hold": HoldDecision(evidence=head("hold")),
        "cancel": CancelDecision(evidence=head("cancel"), direct_act=head("act")),
        "supersede": SupersedeDecision(
            evidence=head("supersede"),
            successor_intent=head("successor"),
            successor_adoption=head("adoption"),
        ),
    }
    value = with_authority(denial_request, decision=decisions[disposition])
    value = (
        authorized(value) if state == "DISPATCH_AUTHORIZED" else with_snapshot(value, state=state)
    )
    permitted = state in {"INTENT_RECORDED", "DISPATCH_AUTHORIZED"} or (
        state == "HELD_BEFORE_SEND" and disposition != "hold"
    )
    if permitted:
        assert prepare_denial(value).record.snapshot.state == decisions[disposition].kind
    else:
        with pytest.raises(EffectRuleViolation):
            prepare_denial(value)


def test_retirement_names_latest_authorization_independently_of_current_worker(
    denial_request: DenialPreparationRequest,
) -> None:
    value = authorized(denial_request)
    prior = value.expected.records[-1].snapshot
    assert prior.authorizations[-1].fence != value.current.fence
    assert prepare_denial(value).record.snapshot.authorizations == prior.authorizations
    newer = prior.authorizations[-1].model_copy(
        update={"authorization": head("newer-authorization")}
    )
    changed = with_snapshot(value, authorizations=(*prior.authorizations, newer))
    with pytest.raises(EffectRuleViolation, match="latest authorization"):
        prepare_denial(changed)
    matched = changed.model_copy(
        update={"command": changed.command.model_copy(update={"authorization_to_retire": newer})}
    )
    assert prepare_denial(matched).record.snapshot.authorizations == (*prior.authorizations, newer)


@pytest.mark.parametrize("name", ["tenant_id", "principal_id"])
def test_matching_claims_cannot_cancel_another_original_scope(
    denial_request: DenialPreparationRequest, name: str
) -> None:
    with pytest.raises(EffectRuleViolation):
        prepare_denial(with_authority(denial_request, **{name: "foreign"}))


def test_supersession_cannot_alias_original_even_with_changed_head(
    denial_request: DenialPreparationRequest,
) -> None:
    decision = SupersedeDecision(
        evidence=head("decision"),
        successor_intent=denial_request.command.intent.model_copy(update={"head": "different"}),
        successor_adoption=head("adoption"),
    )
    with pytest.raises(EffectRuleViolation, match="distinct adopted successor"):
        prepare_denial(with_authority(denial_request, decision=decision))


def test_transmission_or_duplicate_history_cannot_produce_local_no_send(
    denial_request: DenialPreparationRequest,
) -> None:
    with pytest.raises(EffectRuleViolation, match="crossed transmission"):
        prepare_denial(with_snapshot(denial_request, transmissions=(child(0),)))
    value = denial_request.model_copy(
        update={
            "expected": denial_request.expected.model_copy(
                update={"records": denial_request.expected.records * 2}
            )
        }
    )
    with pytest.raises(EffectRuleViolation, match="duplicate"):
        prepare_denial(value)


@pytest.mark.parametrize(
    "field", ["command_id", "command_fingerprint", "store_frontier", "fence", "authority"]
)
def test_current_denial_inputs_must_match_exact_command_and_cut(
    denial_request: DenialPreparationRequest, field: str
) -> None:
    substitutions: dict[str, object] = {
        "command_id": "another-command",
        "command_fingerprint": "another-payload",
        "store_frontier": 2,
        "fence": denial_request.current.fence.model_copy(update={"run_head": "rival-run"}),
        "authority": denial_request.current.authority.model_copy(update={"actor_id": "rival"}),
    }
    value = denial_request.model_copy(
        update={"current": denial_request.current.model_copy(update={field: substitutions[field]})}
    )
    with pytest.raises(EffectRuleViolation, match="operation or current observation"):
        prepare_denial(value)


@pytest.mark.parametrize(
    "field",
    ["actor_id", "authenticated_session", "operation_profile", "decision", "fence"],
)
def test_command_cannot_substitute_independently_observed_denying_authority(
    denial_request: DenialPreparationRequest, field: str
) -> None:
    authority = denial_request.command.authority
    substitutions: dict[str, object] = {
        "actor_id": "foreign-actor",
        "authenticated_session": head("foreign-session"),
        "operation_profile": head("foreign-profile"),
        "decision": HoldDecision(evidence=head("unobserved-hold")),
        "fence": authority.fence.model_copy(update={"run_head": "rival-run"}),
    }
    command = denial_request.command.model_copy(
        update={"authority": authority.model_copy(update={field: substitutions[field]})}
    )
    with pytest.raises(EffectRuleViolation, match="operation or current observation"):
        prepare_denial(denial_request.model_copy(update={"command": command}))


@pytest.mark.parametrize("field", ["intent", "expected_attempt"])
def test_matching_new_claims_cannot_alias_original_or_latest_attempt(
    denial_request: DenialPreparationRequest, field: str
) -> None:
    target = getattr(denial_request.command, field).model_copy(update={"head": "rival-head"})
    value = with_authority(denial_request, **{field: target})
    value = value.model_copy(update={"command": value.command.model_copy(update={field: target})})
    with pytest.raises(EffectRuleViolation, match="immutable subject"):
        prepare_denial(value)


def test_selected_command_requires_replay_instead_of_new_owner_candidate(
    denial_request: DenialPreparationRequest,
) -> None:
    prior = denial_request.expected.records[-1]
    changed = prior.model_copy(
        update={"command": prior.command.model_copy(update={"command_id": "cancel"})}
    )
    value = denial_request.model_copy(
        update={"expected": denial_request.expected.model_copy(update={"records": (changed,)})}
    )
    with pytest.raises(EffectRuleViolation, match="authenticated replay"):
        prepare_denial(value)
