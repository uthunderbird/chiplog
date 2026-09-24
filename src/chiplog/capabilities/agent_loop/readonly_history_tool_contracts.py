"""History-tool values shared by v3 execution history and readonly preparation.

This leaf deliberately has no dependency on an executable Run or readonly
preparation DTO, so the v3 run version union can remain acyclic.
"""

from typing import Literal

from pydantic import ConfigDict, Field

from .recovery_contracts import Identity, RecoveryDTO


class ReadOnlyDTO(RecoveryDTO):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


class ConversationHistoryQuery(ReadOnlyDTO):
    """Model arguments only; the broker supplies principal and snapshot authority."""

    limit: int = Field(strict=True, gt=0, le=2**64 - 1)
    after_cursor: Identity | None


class ReadOnlyHistoryToolSpec(RecoveryDTO):
    name: Literal["read_conversation_history"] = "read_conversation_history"
    version: Literal["1"] = "1"
    schema_id: Literal["chiplog.read-conversation-history.v1"] = (
        "chiplog.read-conversation-history.v1"
    )


class ReadOnlyHistoryToolCall(ReadOnlyDTO):
    call_id: Identity
    tool: Literal["read_conversation_history"]
    arguments: ConversationHistoryQuery
