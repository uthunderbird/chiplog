"""Public inert candidate schema dispatch, with no source authentication."""

import base64
import json

import pytest

from chiplog.capabilities.deployment_trust._ingress_process import ROUTES, dispatch
from chiplog.capabilities.deployment_trust.ingress_contracts import (
    PrivateTextFields,
    TelegramCandidateRef,
    TelegramNormalizedCandidate,
    TelegramNormalizeRequest,
)


def test_owner_returns_only_untrusted_candidate_and_rejects_authority_fields() -> None:
    request = TelegramNormalizeRequest(
        candidate=TelegramCandidateRef(
            tenant_id="t", candidate_id="c", canonical_raw_digest="a" * 64
        ),
        parsed_update_identity=1,
        candidate_fields=PrivateTextFields(chat_id=1, telegram_user_id=2, message_id=3, text="x"),
    )
    result = dispatch(ROUTES[0][0], request.canonical_bytes())
    payload = result["payload"]
    assert isinstance(payload, str)
    candidate = TelegramNormalizedCandidate.model_validate_json(base64.b64decode(payload))
    assert candidate.trust == "UNTRUSTED"
    assert candidate.candidate_fields == request.candidate_fields
    for key in ("witness", "credential", "secret", "authenticated", "raw_handle"):
        raw = request.model_dump(mode="json")
        raw[key] = "forged"
        assert dispatch(ROUTES[0][0], json.dumps(raw).encode())["failure"] == "PROTOCOL_REJECTED"
    assert (
        dispatch(ROUTES[0][0], request.canonical_bytes() + b" ")["failure"] == "PROTOCOL_REJECTED"
    )
    assert dispatch("wrong", request.canonical_bytes())["failure"] == "PROTOCOL_REJECTED"


@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1"])
def test_sender_identity_has_no_numeric_aliases(value: object) -> None:
    with pytest.raises(ValueError):
        PrivateTextFields.model_validate(
            {"chat_id": value, "telegram_user_id": 2, "message_id": 3, "text": "x"}
        )


@pytest.mark.parametrize("value", [0, True, "false", None])
def test_bot_flag_cannot_use_false_aliases(value: object) -> None:
    with pytest.raises(ValueError):
        PrivateTextFields.model_validate(
            {
                "chat_id": 1,
                "telegram_user_id": 2,
                "message_id": 3,
                "text": "x",
                "sender_is_bot": value,
            }
        )
