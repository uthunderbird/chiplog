"""Lean selected native call and conversation state for the client runtime."""

from typing import Literal

from pydantic import Field

from .call_acceptance_contracts import CallSubjectHead
from .contracts import Frozen


class NativeCallStateV1(Frozen):
    schema_id: Literal["chiplog.agent-loop.native-call-state.v1"] = (
        "chiplog.agent-loop.native-call-state.v1"
    )
    head: str = Field(min_length=1)
    predecessor: str | None
    original_call_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    initialized: CallSubjectHead
    state: Literal["INITIALIZED", "ACCEPTED", "CANCELLED", "TERMINAL"]
    authority_act_id: str | None = None
    external_intent_id: str | None = None
    result_base64: str | None = None
    outcome: Literal["SUCCEEDED", "FAILED_DEFINITE", "OUTCOME_UNKNOWN"] | None = None


class ConversationEntryV1(Frozen):
    schema_id: Literal["chiplog.conversation.entry.pg.v1"] = "chiplog.conversation.entry.pg.v1"
    entry_id: str = Field(min_length=1)
    tenant: str = Field(min_length=1)
    principal: str = Field(min_length=1)
    ordinal: int = Field(gt=0)
    role: Literal["principal", "assistant"]
    text: str
