"""Broker-private projection of retained raw bytes, before any authentication."""

import hashlib
import json

from chiplog.capabilities.deployment_trust.ingress_contracts import (
    PrivateTextFields,
    TelegramCandidateRef,
    TelegramNormalizedCandidate,
    TelegramNormalizeRequest,
    UnsupportedFields,
)


class TelegramCandidateRejected(ValueError):
    pass


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TelegramCandidateRejected("duplicate raw JSON key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise TelegramCandidateRejected("nonfinite raw JSON number")


def project_retained_telegram(
    *,
    tenant_id: str,
    candidate_id: str,
    raw_bytes: bytes,
) -> TelegramNormalizeRequest:
    """Caller must retain these exact bytes; the returned value grants nothing."""
    try:
        value = json.loads(
            raw_bytes.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant
        )
        if not isinstance(value, dict) or type(value.get("update_id")) is not int:
            raise TelegramCandidateRejected("missing exact update identity")
        fields: PrivateTextFields | UnsupportedFields
        fields = UnsupportedFields(reason="UPDATE_FORM")
        if set(value) == {"update_id", "message"}:
            message = value["message"]
            fields = UnsupportedFields(reason="MESSAGE_FORM")
            if isinstance(message, dict) and isinstance(message.get("text"), str):
                chat, sender = message.get("chat"), message.get("from")
                fields = UnsupportedFields(reason="CHAT_FORM")
                if isinstance(chat, dict) and chat.get("type") == "private":
                    fields = UnsupportedFields(reason="SENDER_FORM")
                    if (
                        isinstance(sender, dict)
                        and sender.get("is_bot") is False
                        and "sender_chat" not in message
                    ):
                        # Forward/reply/quote metadata stays only in retained raw data;
                        # it never replaces the transport message's actual sender.
                        fields = PrivateTextFields(
                            chat_id=chat["id"],
                            telegram_user_id=sender["id"],
                            message_id=message["message_id"],
                            text=message["text"],
                        )
        return TelegramNormalizeRequest(
            candidate=TelegramCandidateRef(
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                canonical_raw_digest=hashlib.sha256(raw_bytes).hexdigest(),
            ),
            parsed_update_identity=value["update_id"],
            candidate_fields=fields,
        )
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise TelegramCandidateRejected("invalid retained Telegram candidate") from error


def compare_retained_candidate(
    *,
    tenant_id: str,
    candidate_id: str,
    raw_bytes: bytes,
    normalized: TelegramNormalizedCandidate,
) -> None:
    """Phase-two precondition only, never transport authentication or admission."""
    expected = project_retained_telegram(
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        raw_bytes=raw_bytes,
    )
    if normalized != TelegramNormalizedCandidate(
        candidate=expected.candidate,
        parsed_update_identity=expected.parsed_update_identity,
        candidate_fields=expected.candidate_fields,
    ):
        raise TelegramCandidateRejected("retained candidate identity, bytes or projection changed")
