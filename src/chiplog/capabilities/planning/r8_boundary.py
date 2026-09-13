"""Planning-owned immutable authority provenance and freshness contracts."""

from __future__ import annotations

import base64
import json
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


AuthorityReadKind = Literal["TRUST", "PLANNING", "REGISTRY", "ADOPTION"]


class AuthorityRead(_Frozen):
    kind: AuthorityReadKind
    source_id: str
    source_version: str
    head: str
    generation: str
    frontier: str
    valid_until_ns: int = Field(gt=0)
    canonical_value: bytes


class AuthorityTrace(_Frozen):
    tenant_id: str
    principal_id: str
    reads: tuple[AuthorityRead, ...]
    registry_inputs: tuple[tuple[str, str], ...]


class ProposalFreshnessBinding(_Frozen):
    proposal_id: str
    display_digest: str
    principal_id: str
    adoption_act_id: str
    ingress_id: str
    interpretation_revision: str
    command_digest: str
    proposed_result_digest: str
    trace: AuthorityTrace


class AuthorityReadRecorder(Protocol):
    def read(self, kind: AuthorityReadKind) -> AuthorityRead: ...

    def finish(self) -> AuthorityTrace: ...


class R8PlanningRequest(_Frozen):
    command_bytes: bytes
    authority_trace_bytes: bytes
    observed_time_ns: int = Field(ge=0)
    proposal_binding_bytes: bytes | None = None
    display_bytes: bytes | None = None

    def canonical_bytes(self) -> bytes:
        values: dict[str, object] = {
            "command_bytes": base64.b64encode(self.command_bytes).decode("ascii"),
            "authority_trace_bytes": base64.b64encode(self.authority_trace_bytes).decode("ascii"),
            "observed_time_ns": self.observed_time_ns,
        }
        if self.proposal_binding_bytes is not None or self.display_bytes is not None:
            if self.proposal_binding_bytes is None or self.display_bytes is None:
                raise ValueError("adopted request requires both original binding and display")
            values["proposal_binding_bytes"] = base64.b64encode(
                self.proposal_binding_bytes
            ).decode()
            values["display_bytes"] = base64.b64encode(self.display_bytes).decode()
        return json.dumps(
            values,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


__all__ = [
    "AuthorityRead",
    "AuthorityReadKind",
    "AuthorityReadRecorder",
    "AuthorityTrace",
    "ProposalFreshnessBinding",
    "R8PlanningRequest",
]
