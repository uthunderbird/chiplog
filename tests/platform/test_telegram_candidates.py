"""Raw projection and retained-byte comparison; no transport authority claim."""

import hashlib
import json

import pytest

from chiplog.capabilities.deployment_trust.ingress_contracts import TelegramNormalizedCandidate
from chiplog.platform.telegram_candidates import (
    TelegramCandidateRejected,
    compare_retained_candidate,
    project_retained_telegram,
)


def raw_message() -> bytes:
    return json.dumps(
        {
            "update_id": 1,
            "message": {
                "message_id": 2,
                "chat": {"id": 3, "type": "private"},
                "from": {"id": 4, "is_bot": False},
                "text": "untrusted",
                "forward_origin": {"sender_user": {"id": 99}},
                "reply_to_message": {"from": {"id": 98}, "text": "quoted"},
            },
        },
        indent=2,
    ).encode()


def test_projection_preserves_exact_raw_digest_and_never_attributes_quoted_author() -> None:
    raw = raw_message()
    request = project_retained_telegram(tenant_id="t", candidate_id="c", raw_bytes=raw)
    assert request.candidate.canonical_raw_digest == hashlib.sha256(raw).hexdigest()
    fields = request.candidate_fields
    assert fields.kind == "PRIVATE_TEXT"
    assert fields.telegram_user_id == 4
    assert fields.text == "untrusted"
    normalized = TelegramNormalizedCandidate(
        candidate=request.candidate,
        parsed_update_identity=request.parsed_update_identity,
        candidate_fields=fields,
    )
    compare_retained_candidate(
        tenant_id="t", candidate_id="c", raw_bytes=raw, normalized=normalized
    )
    mutations = (
        ("other", "c", raw, normalized),
        ("t", "other", raw, normalized),
        ("t", "c", raw + b" ", normalized),
        ("t", "c", raw, normalized.model_copy(update={"parsed_update_identity": 2})),
        (
            "t",
            "c",
            raw,
            normalized.model_copy(
                update={
                    "candidate_fields": fields.model_copy(update={"telegram_user_id": 99}),
                }
            ),
        ),
    )
    for tenant, identity, changed_raw, changed in mutations:
        with pytest.raises(TelegramCandidateRejected):
            compare_retained_candidate(
                tenant_id=tenant, candidate_id=identity, raw_bytes=changed_raw, normalized=changed
            )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"update_id":1,"update_id":2}',
        b'{"update_id":1,"message":{"text":"x","text":"y"}}',
        b'{"update_id":true}',
        b'{"update_id":1.0}',
        b'{"update_id":NaN}',
        b'{"update_id":-1}',
        b'{"update_id":9223372036854775808}',
        b"\xff",
    ],
)
def test_malformed_or_ambiguous_raw_is_rejected(raw: bytes) -> None:
    with pytest.raises(TelegramCandidateRejected):
        project_retained_telegram(tenant_id="t", candidate_id="c", raw_bytes=raw)


@pytest.mark.parametrize("mutation", ["group", "channel", "bot", "sender_chat", "unknown"])
def test_unsupported_forms_never_become_private_text(mutation: str) -> None:
    value = json.loads(raw_message())
    if mutation in {"group", "channel"}:
        value["message"]["chat"]["type"] = mutation
    elif mutation == "bot":
        value["message"]["from"]["is_bot"] = True
    elif mutation == "sender_chat":
        value["message"]["sender_chat"] = {"id": 4}
    else:
        value["edited_message"] = value.pop("message")
    request = project_retained_telegram(
        tenant_id="t", candidate_id="c", raw_bytes=json.dumps(value).encode()
    )
    assert request.candidate_fields.kind == "UNSUPPORTED"


def test_inert_candidate_crosses_registered_isolated_trust_route() -> None:
    import time

    from chiplog.architecture.r7_runtime import R14_PRODUCTION_MANIFEST
    from chiplog.platform.broker import (
        BrokerSession,
        CallBudget,
        PublicPortCall,
        PublicPortRejected,
        PublicPortSuccess,
    )
    from chiplog.platform.r7_runtime import AuthorityBrokerRuntime

    request = project_retained_telegram(tenant_id="t", candidate_id="c", raw_bytes=raw_message())
    with AuthorityBrokerRuntime("t", 1, "g", R14_PRODUCTION_MANIFEST, b"secret") as runtime:
        call = PublicPortCall(
            operation_id="deployment_trust.normalize_telegram_candidate",
            request_id="candidate",
            caller=BrokerSession(
                tenant_id="t",
                broker_epoch=1,
                generation_id="g",
                owner_id="broker",
                session_id="broker",
            ),
            callee=runtime.session("deployment_trust"),
            schema_id="chiplog.telegram.normalize.v1",
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=10,
                remaining_depth=1,
                absolute_deadline_ns=time.monotonic_ns() + 10_000_000_000,
                policy_version=1,
            ),
        )
        response = runtime.call_sync(call)
        assert isinstance(response, PublicPortSuccess)
        normalized = TelegramNormalizedCandidate.model_validate_json(response.canonical_payload)
        assert normalized.trust == "UNTRUSTED"
        compare_retained_candidate(
            tenant_id="t", candidate_id="c", raw_bytes=raw_message(), normalized=normalized
        )
        for mutation in (
            {"schema_id": "chiplog.deployment-trust.owner-call.v1"},
            {"operation_id": "deployment_trust.authenticate"},
            {"callee": call.callee.model_copy(update={"session_id": "stale"})},
            {"canonical_payload": call.canonical_payload + b" "},
        ):
            assert isinstance(
                runtime.call_sync(call.model_copy(update=mutation)), PublicPortRejected
            )
        attested = {item.identity.owner_id: item for item in runtime.attest()}
        assert attested["deployment_trust"].loaded_policy_modules == (
            "chiplog.capabilities.deployment_trust._ingress_process",
            "chiplog.capabilities.deployment_trust._r17_process",
            "chiplog.capabilities.deployment_trust._r7_process",
        )
