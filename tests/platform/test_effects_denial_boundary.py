"""Real isolated owner route and mechanical retained-record boundary tests."""

import hashlib
import json
import time

import pytest

from chiplog.adapters.driven.effects_queries import _decode_row, _validate_denial_predecessor
from chiplog.architecture.r7_runtime import R14_PRODUCTION_MANIFEST, R16_PRODUCTION_MANIFEST
from chiplog.capabilities.effects.contracts import (
    CommandIdentity,
    CurrentEffectInputs,
    EffectDenied,
    EffectPreparationRequest,
    EffectRecord,
    EffectStoreSnapshot,
    ExactHead,
    PreparedEffectPublication,
    PublishPlanEffectCommand,
)
from chiplog.capabilities.effects.denial import prepare_denial
from chiplog.capabilities.effects.denial_contracts import DenialPreparationRequest
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.support.effects import head
from tests.support.effects_denial import make_denial_request


def call(runtime: AuthorityBrokerRuntime, payload: bytes) -> PublicPortCall:
    return PublicPortCall(
        operation_id="effects.prepare_denial",
        request_id="denial",
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="generation",
            owner_id="broker",
            session_id="broker",
        ),
        callee=runtime.session("effects"),
        schema_id="chiplog.effects.before-send-preparation.v2",
        canonical_payload=payload,
        budget=CallBudget(
            remaining_calls=4,
            remaining_depth=2,
            absolute_deadline_ns=time.monotonic_ns() + 10_000_000_000,
            policy_version=1,
        ),
    )


def legacy_preparation(value: DenialPreparationRequest) -> EffectPreparationRequest:
    original = value.expected.records[0].snapshot.intent
    command = PublishPlanEffectCommand(
        identity=CommandIdentity(command_id="create", fingerprint="create", expected_tenant_head=0),
        intent=original,
        planning_publication=head("planning"),
        planning_owner_bytes=b"planning",
        complete_publication_manifest=(head("planning"), value.command.intent),
        fence=value.current.fence,
    )
    current = CurrentEffectInputs(
        command_id="create",
        command_fingerprint="create",
        store_frontier=0,
        observed_time_ns=10,
        authority=original.authority,
        supported_semantics=original.semantics,
        fence=value.current.fence,
        authority_decision=head("decision"),
        blocking_effect_heads=(),
        current_original_ambiguity_heads=(),
        initialized_call=None,
        active_run_head="run-head",
        independently_verified_safe_proof=None,
        authenticated_evidence=None,
        original_reducer_semantics=None,
        authorized_reconciler=None,
    )
    return EffectPreparationRequest(
        operation="effects.publish_plan_effect",
        command_bytes=command.canonical_bytes(),
        expected=EffectStoreSnapshot(tenant_id="tenant", tenant_head=0, records=()),
        current=current,
    )


def test_versioned_owner_prepares_denial_and_preserves_legacy_route_over_real_ipc() -> None:
    request = make_denial_request()
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R16_PRODUCTION_MANIFEST, b"offline"
    ) as runtime:
        sent = call(runtime, request.canonical_bytes())
        response = runtime.call_sync(sent)
        assert isinstance(response, PublicPortSuccess)
        assert response.schema_id == "chiplog.effects.before-send-prepared-publication.v2"
        assert response.responder == sent.callee
        assert response.canonical_payload == prepare_denial(request).canonical_bytes()
        attestations = {row.identity.owner_id: row for row in runtime.attest()}
        assert attestations["effects"].loaded_policy_modules == (
            "chiplog.capabilities.effects._process",
            "chiplog.capabilities.effects._r16_process",
        )
        old = legacy_preparation(request)
        response = runtime.call_sync(
            sent.model_copy(
                update={
                    "operation_id": "effects.prepare_transition",
                    "schema_id": "chiplog.effects.prepare.v1",
                    "request_id": "legacy",
                    "canonical_payload": old.canonical_bytes(),
                }
            )
        )
        assert isinstance(response, PublicPortSuccess)
        assert response.schema_id == "chiplog.effects.prepared-publication.v1"
        prepared = PreparedEffectPublication.model_validate_json(response.canonical_payload)
        assert prepared.record.kind == "PLAN_EFFECT_PUBLISHED"
        assert prepared.record.source_command == old.command_bytes
        assert prepared.record.snapshot.intent == request.expected.records[0].snapshot.intent


def test_denial_ipc_rejects_protocol_confusion_and_returns_expired_authority_denial() -> None:
    request = make_denial_request()
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R16_PRODUCTION_MANIFEST, b"offline"
    ) as runtime:
        sent = call(runtime, request.canonical_bytes())
        invalid: tuple[dict[str, object], ...] = (
            {"canonical_payload": b" " + sent.canonical_payload},
            {"schema_id": "chiplog.effects.prepare.v1"},
            {"schema_id": "unknown"},
            {
                "operation_id": "effects.prepare_transition",
                "schema_id": "chiplog.effects.prepare.v1",
            },
            {
                "canonical_payload": request.model_copy(
                    update={"schema_id": "unknown"}
                ).canonical_bytes()
            },
        )
        for ordinal, change in enumerate(invalid):
            result = runtime.call_sync(
                sent.model_copy(update=change | {"request_id": f"bad-{ordinal}"})
            )
            assert isinstance(result, PublicPortRejected)
        for source in (None, "invocation"):
            authority = request.command.authority
            if source is None:
                authority = authority.model_copy(
                    update={"valid_until_ns": request.current.observed_time_ns}
                )
            else:
                authority = authority.model_copy(
                    update={
                        "sources": authority.sources.model_copy(
                            update={
                                source: authority.sources.invocation.model_copy(
                                    update={"valid_until_ns": request.current.observed_time_ns}
                                )
                            }
                        )
                    }
                )
            stale = request.model_copy(
                update={
                    "command": request.command.model_copy(update={"authority": authority}),
                    "current": request.current.model_copy(update={"authority": authority}),
                }
            )
            response = runtime.call_sync(
                sent.model_copy(
                    update={
                        "request_id": f"expired-{source}",
                        "canonical_payload": stale.canonical_bytes(),
                    }
                )
            )
            assert isinstance(response, PublicPortSuccess)
            denied = EffectDenied.model_validate_json(response.canonical_payload)
            assert denied.disposition == "STALE"
            assert denied.command_id == request.command.identity.command_id


def test_legacy_manifest_does_not_gain_denial_route() -> None:
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_PRODUCTION_MANIFEST, b"offline"
    ) as runtime:
        assert isinstance(
            runtime.call_sync(call(runtime, make_denial_request().canonical_bytes())),
            PublicPortRejected,
        )


def wrapped(record: EffectRecord) -> OwnerRecordBytes:
    """Rehash mutations too, so a digest mismatch cannot mask a semantic check."""
    body = {
        "command": record.command.model_dump(mode="json"),
        "predecessor": None
        if record.predecessor is None
        else record.predecessor.model_dump(mode="json"),
        "kind": record.kind,
        "snapshot": record.snapshot.model_dump(mode="json"),
        "source_command": record.source_command.hex(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    subject = "effects/" + record.command.command_id
    record = record.model_copy(
        update={
            "record": ExactHead(
                subject_id=subject,
                head=subject + "/" + fingerprint,
                fingerprint=fingerprint,
            )
        }
    )
    raw = record.canonical_bytes()
    return OwnerRecordBytes(
        owner="effects",
        record_kind="effects." + record.kind,
        record_id=record.record.head,
        schema_id="chiplog.effects.record.v1",
        canonical_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def test_retained_denial_reads_exact_source_and_predecessor_without_renewing_old_authority() -> (
    None
):
    request = make_denial_request()
    record = prepare_denial(request).record
    decoded = _decode_row(wrapped(record), "tenant")
    assert decoded == record
    _validate_denial_predecessor(decoded, request.expected.records[-1])
    assert decoded.source_command == request.command.canonical_bytes()
    assert decoded.snapshot.intent == request.expected.records[-1].snapshot.intent


@pytest.mark.parametrize(
    "mutation", ["kind", "state", "intent", "principal", "schema", "missing-schema"]
)
def test_rehashed_denial_record_cannot_disguise_kind_state_subject_or_version(
    mutation: str,
) -> None:
    request = make_denial_request()
    record = prepare_denial(request).record
    if mutation == "kind":
        record = record.model_copy(update={"kind": "INTENT_ACCEPTED"})
    elif mutation == "state":
        record = record.model_copy(
            update={"snapshot": record.snapshot.model_copy(update={"state": "INTENT_RECORDED"})}
        )
    else:
        command = request.command.model_dump(mode="json")
        if mutation == "intent":
            command["intent"]["head"] = "alias"
        elif mutation == "principal":
            command["authority"]["principal_id"] = "foreign"
        elif mutation == "schema":
            command["schema_id"] = "unknown"
        else:
            del command["schema_id"]
        record = record.model_copy(
            update={
                "source_command": json.dumps(
                    command, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            }
        )
    with pytest.raises(ValueError):
        _decode_row(wrapped(record), "tenant")


def test_rehashed_retained_denial_must_target_predecessor_attempt_not_result_attempt() -> None:
    request = make_denial_request()
    record = prepare_denial(request).record
    assert record.snapshot.attempt != request.command.expected_attempt
    alias = head("another-attempt")
    command = request.command.model_copy(
        update={
            "expected_attempt": alias,
            "authority": request.command.authority.model_copy(update={"expected_attempt": alias}),
        }
    )
    altered = record.model_copy(update={"source_command": command.canonical_bytes()})
    decoded = _decode_row(wrapped(altered), "tenant")
    with pytest.raises(ValueError, match="predecessor attempt"):
        _validate_denial_predecessor(decoded, request.expected.records[-1])
