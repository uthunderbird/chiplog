"""Fail-closed wire boundary for the unfinished H1 completion issuer."""

import hashlib
import time
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

from chiplog.composition.h1_completion_issuance import (
    SCHEMA,
    H1CompletionCaptureV1,
    decode_h1_completion_issuance,
    h1_completion_exchange,
)
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    WorkerAuthentication,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_trust import TrustOwnerCall


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _batch(*, applicability_schema: str) -> CompleteDeliveryBatchV2:
    def command(
        owner: Literal[
            "agent_loop",
            "effects",
            "planning",
            "broker_ingress",
            "broker_dispatch",
            "conversation",
        ],
    ) -> OwnerCommandBytes:
        return OwnerCommandBytes(
            owner=owner,
            schema_id="test.command.v1",
            canonical_bytes=b"command",
            fingerprint=_digest(b"command"),
        )

    return CompleteDeliveryBatchV2(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id="command",
            command_fingerprint="0" * 64,
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        authentication=WorkerAuthentication(
            invocation=InvocationProofRef(
                issuance_id="issuance",
                issuance_fingerprint="0" * 64,
                broker_epoch="epoch",
                broker_session="session",
                runtime_generation="generation",
                operation_subject="subject",
            ),
            applicability_schema=applicability_schema,
            applicability_bytes=b"not-an-h1-issuance",
            applicability_fingerprint=_digest(b"not-an-h1-issuance"),
        ),
        expected=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=0,
            expected_materialization_commitment="0" * 64,
            registry_head="registry",
            registry_fingerprint="0" * 64,
            ordered_heads=(),
            complete_manifest_fingerprint="0" * 64,
        ),
        loop_command=command("agent_loop"),
        conversation_command=command("conversation"),
        terminal_work_command=command("agent_loop"),
        prepared_effects_commands=(command("effects"),),
        complete_records=(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind="record",
                record_id="record",
                schema_id="test.record.v1",
                canonical_bytes=b"record",
                fingerprint=_digest(b"record"),
            ),
        ),
        complete_batch_fingerprint="0" * 64,
    )


def test_h1_decoder_fails_closed_for_an_unknown_applicability_schema() -> None:
    batch = _batch(applicability_schema="chiplog.other-issuance.v1")

    with pytest.raises(ValueError, match="exact issuance applicability"):
        decode_h1_completion_issuance(batch)


def test_h1_projection_does_not_reinterpret_another_v2_applicability() -> None:
    batch = _batch(applicability_schema="chiplog.other-issuance.v1")

    with pytest.raises(ValueError, match="exact issuance applicability"):
        h1_completion_exchange(batch)


def test_h1_decoder_requires_the_exact_applicability_fingerprint() -> None:
    batch = _batch(applicability_schema=SCHEMA).model_copy(
        update={
            "authentication": _batch(applicability_schema=SCHEMA).authentication.model_copy(
                update={"applicability_fingerprint": "0" * 64}
            )
        }
    )

    with pytest.raises(ValueError, match="exact issuance applicability"):
        decode_h1_completion_issuance(batch)


def test_h1_issuance_schema_is_a_closed_versioned_identifier() -> None:
    assert SCHEMA == "chiplog.composition.h1-completion-issuance.v1"


def test_h1_capture_requires_an_explicit_invocation_proof() -> None:
    assert H1CompletionCaptureV1.model_fields["invocation"].is_required()


def _scope_issue_exchange(*, wire: TrustOwnerCall) -> SimpleNamespace:
    callee = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="deployment_trust",
        session_id="trust",
    )
    sent = PublicPortCall(
        operation_id="deployment_trust.issue_hermetic_output_scope",
        request_id="request",
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="generation",
            owner_id="broker",
            session_id="broker",
        ),
        callee=callee,
        schema_id="chiplog.deployment-trust.owner-call.v1",
        canonical_payload=wire.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=time.monotonic_ns() + 1_000_000_000,
            policy_version=1,
        ),
    )
    return SimpleNamespace(
        sent=sent,
        returned=PublicPortSuccess(
            request_id=sent.request_id,
            responder=callee,
            schema_id="chiplog.deployment-trust.issue-hermetic-output-scope-result.v1",
            canonical_payload=b"candidate",
        ),
    )


def test_scope_issue_accepts_the_actual_canonical_owner_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.composition import h1_completion_issuance as issuance

    snapshot = b"actual-owner-snapshot"
    retained = object()
    selected_scope = SimpleNamespace(scope="selected-scope")
    call = SimpleNamespace(
        canonical_bytes=lambda: b"actual-owner-request",
        evidence=SimpleNamespace(
            trust_snapshot_digest=hashlib.sha256(snapshot).hexdigest(), retained=retained
        ),
    )
    candidate = SimpleNamespace(
        canonical_bytes=lambda: b"candidate",
        scope="selected-scope",
        check_pinned_call=lambda value: value is call,
    )
    monkeypatch.setattr(
        cast(Any, issuance).H1OwnerCandidateCallV1, "model_validate_json", lambda _: call
    )
    monkeypatch.setattr(
        cast(Any, issuance).H1OwnerCandidateV1, "model_validate_json", lambda _: candidate
    )
    exchange = _scope_issue_exchange(
        wire=TrustOwnerCall(
            mode="ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
            snapshot_bytes=snapshot,
            request_bytes=b"actual-owner-request",
        )
    )
    capture = SimpleNamespace(
        sessions=(exchange.sent.callee,),
        observed=SimpleNamespace(observation=SimpleNamespace(snapshot_bytes=snapshot)),
    )
    assembly = SimpleNamespace(
        ordered_effects=(
            SimpleNamespace(
                owner_call=SimpleNamespace(
                    request=SimpleNamespace(selected_scope=selected_scope, retained_origin=retained)
                )
            ),
        )
    )

    issuance._require_scope_issue_exchange(
        cast(Any, exchange), cast(Any, assembly), cast(Any, capture)
    )


def test_scope_issue_rejects_a_canonical_forged_snapshot_before_nested_decode() -> None:
    from chiplog.composition import h1_completion_issuance as issuance

    exchange = _scope_issue_exchange(
        wire=TrustOwnerCall(
            mode="ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
            snapshot_bytes=b"forged-owner-snapshot",
            request_bytes=b"actual-owner-request",
        )
    )
    capture = SimpleNamespace(
        sessions=(exchange.sent.callee,),
        observed=SimpleNamespace(
            observation=SimpleNamespace(snapshot_bytes=b"pinned-owner-snapshot")
        ),
    )

    with pytest.raises(ValueError, match="noncanonical outer request"):
        issuance._require_scope_issue_exchange(
            cast(Any, exchange), cast(Any, SimpleNamespace()), cast(Any, capture)
        )
