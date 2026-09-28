"""RED wire contract for the unimplemented H1 completion-issuance V2 codec.

These tests deliberately stop at canonical DTO decoding.  No locally assembled
``CompleteDeliveryBatchV2`` is used as an apparent writer or selected-journal
witness: the installed runtime does not yet retain a valid V2 issuance value.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCurrentCandidateV1,
)
from chiplog.composition import h1_completion_issuance as issuance
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_trust import decode_trust_owner_call_canonical
from tests.composition.test_h1_scope_current_wire_runtime import _current_request
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot

V1_SCHEMA = "chiplog.composition.h1-completion-issuance.v1"
V2_SCHEMA = "chiplog.composition.h1-completion-issuance.v2"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _v2_type(name: str) -> type[Any]:
    value = getattr(issuance, name, None)
    assert isinstance(value, type), f"H1 completion issuance V2 lacks {name}"
    return value


def _dispatch(outer_schema: str, raw: bytes) -> str:
    dispatcher = getattr(issuance, "dispatch_h1_completion_issuance_schema", None)
    assert callable(dispatcher), "H1 completion issuance V2 lacks its schema dispatcher"
    return cast(str, dispatcher(outer_schema, raw))


def _decode_recovery(raw: bytes) -> issuance.H1CompletionRecoveryRefV1:
    decoder = getattr(issuance, "decode_h1_completion_recovery_ref_v1", None)
    assert callable(decoder), "H1 completion issuance V2 lacks its recovery-reference codec"
    return cast(issuance.H1CompletionRecoveryRefV1, decoder(raw))


def _decode_admission_witness(raw: bytes) -> issuance.H1CompletionTerminalAdmissionWitnessV1:
    decoder = getattr(issuance, "decode_h1_completion_terminal_admission_witness_v1", None)
    assert callable(decoder), "H1 completion issuance V2 lacks its terminal-admission codec"
    return cast(issuance.H1CompletionTerminalAdmissionWitnessV1, decoder(raw))


def _recovery() -> dict[str, str]:
    return {
        "root_id": "a" * 64,
        "journal_instance_id": "recovery-journal",
        "completed_chain_head": "terminal-work-result",
    }


def test_v2_recovery_reference_roundtrips_only_its_closed_canonical_wire() -> None:
    _v2_type("H1CompletionRecoveryRefV1")
    raw = _canonical(_recovery())

    decoded = _decode_recovery(raw)

    assert decoded.canonical_bytes() == raw
    assert decoded.root_id == "a" * 64


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: {key: item for key, item in value.items() if key != "root_id"},
        lambda value: {**value, "root_id": "A" * 64},
        lambda value: {**value, "unknown": "field"},
    ),
)
def test_v2_recovery_reference_rejects_missing_noncanonical_or_extra_members(mutate: Any) -> None:
    _v2_type("H1CompletionRecoveryRefV1")

    with pytest.raises(ValueError):
        _decode_recovery(_canonical(mutate(_recovery())))


def test_v2_recovery_reference_rejects_duplicate_members_before_model_decoding() -> None:
    _v2_type("H1CompletionRecoveryRefV1")
    raw = (
        b'{"completed_chain_head":"terminal-work-result","journal_instance_id":"recovery-journal",'
        b'"root_id":"' + b"a" * 64 + b'","root_id":"' + b"b" * 64 + b'"}'
    )

    with pytest.raises(ValueError):
        _decode_recovery(raw)


def _preterminal_current_exchange() -> issuance.H1CompletionOwnerExchangeV1:
    callee = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="deployment_trust",
        session_id="trust",
    )
    sent = PublicPortCall(
        operation_id="deployment_trust.read_current_hermetic_output_scope",
        request_id="preterminal-current",
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="generation",
            owner_id="broker",
            session_id="broker",
        ),
        callee=callee,
        schema_id="chiplog.deployment-trust.owner-call.v1",
        canonical_payload=b"{}",
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=2,
            policy_version=1,
        ),
    )
    return issuance.H1CompletionOwnerExchangeV1(
        role="scope_current",
        sent=sent,
        returned=PublicPortSuccess(
            request_id=sent.request_id,
            responder=callee,
            schema_id="chiplog.deployment-trust.current-hermetic-output-scope-result.v1",
            canonical_payload=b"{}",
        ),
        sent_at_ns=0,
        returned_at_ns=1,
    )


def test_v2_terminal_admission_witness_roundtrips_its_exact_preterminal_exchange() -> None:
    _v2_type("H1CompletionTerminalAdmissionWitnessV1")
    raw = _canonical(
        {
            "terminal_call_fingerprint": "b" * 64,
            "preterminal_current_exchange": _preterminal_current_exchange().model_dump(mode="json"),
        }
    )

    decoded = _decode_admission_witness(raw)

    assert decoded.canonical_bytes() == raw
    assert decoded.preterminal_current_exchange.role == "scope_current"


def test_v2_dispatch_accepts_only_matching_supported_outer_and_inner_versions() -> None:
    for outer, inner, expected in (
        (V1_SCHEMA, V1_SCHEMA, "V1"),
        (V2_SCHEMA, V2_SCHEMA, "V2"),
    ):
        assert _dispatch(outer, _canonical({"schema_id": inner})) == expected


def test_v2_dispatch_rejects_outer_and_inner_schema_disagreement() -> None:
    for outer, inner in (
        (V1_SCHEMA, V2_SCHEMA),
        (V2_SCHEMA, V1_SCHEMA),
        (V2_SCHEMA, "chiplog.composition.h1-completion-issuance.v9"),
        ("chiplog.composition.h1-completion-issuance.v9", V2_SCHEMA),
        ("", V2_SCHEMA),
        (V2_SCHEMA, ""),
    ):
        raw = _canonical({"schema_id": inner})
        with pytest.raises(ValueError):
            _dispatch(outer, raw)


@pytest.mark.parametrize(
    "raw",
    (
        b"{}",
        _canonical({"schema_id": "chiplog.composition.h1-completion-issuance.v9"}),
        b'{"schema_id":"chiplog.composition.h1-completion-issuance.v2","schema_id":"chiplog.composition.h1-completion-issuance.v1"}',
        b'{"schema_id":"chiplog.composition.h1-completion-issuance.v2", "extra":true}',
        _canonical({"schema_id": V2_SCHEMA, "extra": True}),
    ),
)
def test_v2_dispatch_fails_closed_for_missing_unknown_duplicate_or_noncanonical_keys(
    raw: bytes,
) -> None:
    with pytest.raises(ValueError):
        _dispatch(V2_SCHEMA, raw)


def test_v1_public_schema_and_decoder_remain_the_immutable_compatibility_route() -> None:
    # A valid pipeline-produced V1 applicability fixture is required to prove
    # decoding/replay, rather than merely schema routing.  Preserve the route's
    # public identifier here without pretending the old negative test batch is
    # a valid V1 witness.
    assert issuance.SCHEMA == V1_SCHEMA
    assert issuance.H1CompletionIssuanceV1.model_fields["schema_id"].default == V1_SCHEMA
    assert (
        hashlib.sha256(V1_SCHEMA.encode()).hexdigest()
        != hashlib.sha256(V2_SCHEMA.encode()).hexdigest()
    )


def test_v2_outer_dto_exposes_only_the_frozen_evidence_members() -> None:
    value_type = _v2_type("H1CompletionIssuanceV2")

    assert set(value_type.model_fields) == {
        "schema_id",
        "assembly",
        "capture",
        "owner_exchanges",
        "scope_issue_exchange",
        "scope_current_exchange",
        "final_current_exchange",
        "recovery",
        "terminal_admission",
        "read_plan",
    }
    assert value_type.model_fields["schema_id"].default == V2_SCHEMA


@pytest.mark.asyncio
async def test_v2_historical_current_uses_its_selected_frame_but_fresh_current_remains_bound(
    tmp_path: Path,
) -> None:
    """A P-era CURRENT is historical evidence, while a new CURRENT needs the capture session."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request = await _current_request(runtime)
            _current, wire = await runtime._read_current_hermetic_output_scope_with_wire(request)

    exchange = issuance.H1CompletionOwnerExchangeV1(
        role="scope_current",
        sent=wire.sent,
        returned=wire.returned,
        sent_at_ns=wire.sent_at_ns,
        returned_at_ns=wire.returned_at_ns,
    )
    assert isinstance(wire.returned, PublicPortSuccess)
    candidate = H1OwnerCurrentCandidateV1.model_validate_json(wire.returned.canonical_payload)
    assembly = cast(Any, SimpleNamespace(
        ordered_effects=(
            SimpleNamespace(
                owner_call=SimpleNamespace(
                    request=SimpleNamespace(
                        selected_scope=SimpleNamespace(
                            current_request=request, current_result=candidate.current
                        )
                    )
                )
            ),
        )
    ))
    historical_session = wire.sent.callee
    fresh_session = historical_session.model_copy(update={"session_id": "fresh-capture"})
    historical_capture = cast(Any, SimpleNamespace(
        sessions=(fresh_session,),
        observed=SimpleNamespace(
            observation=SimpleNamespace(
                snapshot_bytes=decode_trust_owner_call_canonical(wire.sent.canonical_payload).snapshot_bytes
            )
        ),
    ))

    # The selected P frame remains valid even though the later capture records
    # a different trust-owner session.
    issuance._require_historical_scope_current_exchange_v2(exchange, assembly)

    with pytest.raises(ValueError, match="substituted route"):
        issuance._require_historical_scope_current_exchange_v2(
            exchange.model_copy(
                update={"sent": wire.sent.model_copy(update={"operation_id": "forged"})}
            ),
            assembly,
        )
    with pytest.raises(ValueError):
        issuance._require_historical_scope_current_exchange_v2(
            exchange.model_copy(
                update={"sent": wire.sent.model_copy(update={"canonical_payload": b"{}"})}
            ),
            assembly,
        )

    # The same old frame cannot be substituted for a fresh final-fence read.
    with pytest.raises(ValueError, match="substituted route"):
        issuance._require_fresh_scope_current_exchange_v2(exchange, assembly, historical_capture)


def test_v2_terminal_admission_fingerprint_is_checked_against_the_terminal_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clearance fingerprint cannot be detached from the retained fourth call."""
    checker = getattr(issuance, "_require_v2_exchange_shape", None)
    assert callable(checker), "H1 completion issuance V2 lacks its exchange validator"
    terminal = _preterminal_current_exchange().model_copy(update={"role": "terminal_work"})
    value = issuance.H1CompletionIssuanceV2.model_construct(
        owner_exchanges=(
            terminal.model_copy(update={"role": "completion"}),
            terminal.model_copy(update={"role": "conversation"}),
            terminal.model_copy(update={"role": "effects"}),
            terminal,
        ),
        scope_issue_exchange=terminal.model_copy(update={"role": "scope_issue"}),
        scope_current_exchange=terminal.model_copy(update={"role": "scope_current"}),
        final_current_exchange=terminal.model_copy(update={"role": "scope_current"}),
        terminal_admission=issuance.H1CompletionTerminalAdmissionWitnessV1.model_construct(
            terminal_call_fingerprint="0" * 64,
            preterminal_current_exchange=terminal.model_copy(update={"role": "scope_current"}),
        ),
    )
    monkeypatch.setattr(issuance, "_require_success_exchange", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        issuance, "_require_historical_scope_issue_exchange_v2", lambda *_args: None
    )
    monkeypatch.setattr(
        issuance, "_require_historical_scope_current_exchange_v2", lambda *_args: None
    )
    monkeypatch.setattr(issuance, "_require_fresh_scope_current_exchange_v2", lambda *_args: None)

    with pytest.raises(ValueError, match="terminal fingerprint"):
        checker(value)
