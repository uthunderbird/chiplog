"""Inert Telegram phase-one values; none authenticates transport or principal."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Identity = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
TelegramId = Annotated[int, Field(gt=0, le=2**63 - 1)]


class CandidateDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()


class TelegramCandidateRef(CandidateDTO):
    tenant_id: Identity
    candidate_id: Identity
    # SHA256 of original retained bytes, never a JSON reserialization.
    canonical_raw_digest: Digest
    normalization_version: Literal["chiplog.telegram.candidate.v1"] = (
        "chiplog.telegram.candidate.v1"
    )


class PrivateTextFields(CandidateDTO):
    kind: Literal["PRIVATE_TEXT"] = "PRIVATE_TEXT"
    chat_id: TelegramId
    chat_type: Literal["private"] = "private"
    telegram_user_id: TelegramId
    sender_is_bot: Literal[False] = False
    message_id: TelegramId
    text: str

    @field_validator("sender_is_bot", mode="before")
    @classmethod
    def exact_false(cls, value: object) -> object:
        if value is not False:
            raise ValueError("sender bot flag must be exact false")
        return value


class UnsupportedFields(CandidateDTO):
    kind: Literal["UNSUPPORTED"] = "UNSUPPORTED"
    reason: Literal["UPDATE_FORM", "MESSAGE_FORM", "CHAT_FORM", "SENDER_FORM"]


class TelegramNormalizeRequest(CandidateDTO):
    schema_id: Literal["chiplog.telegram.normalize.v1"] = "chiplog.telegram.normalize.v1"
    candidate: TelegramCandidateRef
    parsed_update_identity: Annotated[int, Field(ge=0, le=2**63 - 1)]
    candidate_fields: Annotated[PrivateTextFields | UnsupportedFields, Field(discriminator="kind")]


class TelegramNormalizedCandidate(CandidateDTO):
    schema_id: Literal["chiplog.telegram.normalized-candidate.v1"] = (
        "chiplog.telegram.normalized-candidate.v1"
    )
    trust: Literal["UNTRUSTED"] = "UNTRUSTED"
    candidate: TelegramCandidateRef
    parsed_update_identity: Annotated[int, Field(ge=0, le=2**63 - 1)]
    candidate_fields: Annotated[PrivateTextFields | UnsupportedFields, Field(discriminator="kind")]
