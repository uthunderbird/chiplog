"""Explicit model recipient for the opt-in local CLI dialogue."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LiveModelBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider: Literal["codex-oauth.v1"] = "codex-oauth.v1"
    recipient: Literal["openai-codex"] = "openai-codex"
    model: str = Field(default="gpt-5.6-terra", min_length=1, max_length=200)
    effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    credential_identity: str = Field(min_length=1)


class CodexError(RuntimeError):
    """Public failure with no token or provider response content."""
