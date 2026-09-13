"""Planning owner policy for complete, ordered authority provenance."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Protocol

from .r8_boundary import (
    AuthorityRead,
    AuthorityReadKind,
    AuthorityTrace,
    ProposalFreshnessBinding,
)

DIRECT_REGISTRY_INPUTS = (("authority-row", "direct-principal-create-v1"),)
PROPOSAL_REGISTRY_INPUTS = (("authority-row", "adopted-proposal-create-v1"),)
_SOURCES = {
    "TRUST": "deployment_trust.current",
    "PLANNING": "planning.committed",
    "REGISTRY": "planning.authority-registry",
    "ADOPTION": "planning.exact-adoption",
}


class AuthorityTraceViolation(ValueError):
    pass


class AuthoritySource(Protocol):
    def read_authority(self, kind: AuthorityReadKind) -> AuthorityRead: ...


def trace_bytes(trace: AuthorityTrace) -> bytes:
    value = trace.model_dump(exclude={"reads"})
    value["reads"] = [
        {
            **read.model_dump(exclude={"canonical_value"}),
            "canonical_value": base64.b64encode(read.canonical_value).decode("ascii"),
        }
        for read in trace.reads
    ]
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def decode_trace(payload: bytes) -> AuthorityTrace:
    value = json.loads(payload)
    value["reads"] = tuple(
        AuthorityRead.model_validate(
            {**read, "canonical_value": base64.b64decode(read["canonical_value"], validate=True)}
        )
        for read in value["reads"]
    )
    value["registry_inputs"] = tuple(tuple(item) for item in value["registry_inputs"])
    trace = AuthorityTrace.model_validate(value)
    if trace_bytes(trace) != payload:
        raise AuthorityTraceViolation("authority trace is not canonical")
    return trace


class PlanningAuthorityRecorder:
    """The only inputs of planning authority predicates come from this capability.

    The source is broker-authenticated IPC data. It is reproduced independently at
    publication, never accepted because a caller supplied a matching digest.
    """

    def __init__(
        self,
        source: AuthoritySource,
        *,
        tenant_id: str,
        principal_id: str,
        now_ns: int,
        adopted_proposal: bool = False,
    ) -> None:
        self._source = source
        self._tenant = tenant_id
        self._principal = principal_id
        self._now = now_ns
        self._kinds: tuple[AuthorityReadKind, ...] = (
            ("TRUST", "PLANNING", "REGISTRY", "ADOPTION")
            if adopted_proposal
            else ("TRUST", "PLANNING", "REGISTRY")
        )
        self._registry = PROPOSAL_REGISTRY_INPUTS if adopted_proposal else DIRECT_REGISTRY_INPUTS
        self._reads: list[AuthorityRead] = []
        self._finished = False

    def read(self, kind: AuthorityReadKind) -> AuthorityRead:
        position = len(self._reads)
        if self._finished or position >= len(self._kinds) or self._kinds[position] != kind:
            raise AuthorityTraceViolation(
                "unknown, duplicate, reordered or disallowed authority read"
            )
        value = self._source.read_authority(kind)
        if (
            value.kind != kind
            or value.source_id != _SOURCES[kind]
            or value.source_version != "1"
            or not value.head
            or not value.generation
            or not value.frontier
            or value.valid_until_ns <= self._now
        ):
            raise AuthorityTraceViolation(
                "authority source identity, version or freshness mismatch"
            )
        decoded = json.loads(value.canonical_value)
        if (
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
            != value.canonical_value
        ):
            raise AuthorityTraceViolation("authority value is not canonical")
        if self._reads and (value.generation, value.frontier) != (
            self._reads[0].generation,
            self._reads[0].frontier,
        ):
            raise AuthorityTraceViolation("authority inputs do not share one verified frontier")
        self._reads.append(value)
        return value

    def finish(self) -> AuthorityTrace:
        if self._finished or tuple(read.kind for read in self._reads) != self._kinds:
            raise AuthorityTraceViolation("authority predicate input trace is incomplete or reused")
        registry = json.loads(self._reads[2].canonical_value)
        if registry != [list(row) for row in self._registry]:
            raise AuthorityTraceViolation("authority registry row is unknown or substituted")
        self._finished = True
        return AuthorityTrace(
            tenant_id=self._tenant,
            principal_id=self._principal,
            reads=tuple(self._reads),
            registry_inputs=self._registry,
        )


def require_reproduced_trace(original: AuthorityTrace, reproduced: AuthorityTrace) -> None:
    if trace_bytes(original) != trace_bytes(reproduced):
        raise AuthorityTraceViolation(
            "authority trace changed; rebuild and redisplay before adoption"
        )


def freshness_bytes(binding: ProposalFreshnessBinding) -> bytes:
    values = binding.model_dump(exclude={"trace"})
    values["trace"] = base64.b64encode(trace_bytes(binding.trace)).decode("ascii")
    return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


def decode_freshness(payload: bytes) -> ProposalFreshnessBinding:
    values = json.loads(payload)
    values["trace"] = decode_trace(base64.b64decode(values["trace"], validate=True))
    result = ProposalFreshnessBinding.model_validate(values)
    if freshness_bytes(result) != payload:
        raise AuthorityTraceViolation("noncanonical immutable proposal binding")
    return result


def proposed_create_result(command: Mapping[str, object]) -> bytes:
    return json.dumps(
        {
            "operation": "CREATE_INTENTION_LINE",
            "purpose": command["purpose"],
            "intention_line_id": command["intention_line_id"],
            "revision_id": command["revision_id"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def validate_proposal_freshness(
    displayed: ProposalFreshnessBinding,
    reproduced: ProposalFreshnessBinding,
    *,
    display_bytes: bytes,
    now_ns: int,
) -> None:
    class TraceSource:
        def __init__(self, value: AuthorityTrace) -> None:
            self.rows = iter(value.reads)

        def read_authority(self, kind: AuthorityReadKind) -> AuthorityRead:
            return next(self.rows)

    for value in (displayed.trace, reproduced.trace):
        recorder = PlanningAuthorityRecorder(
            TraceSource(value),
            tenant_id=value.tenant_id,
            principal_id=value.principal_id,
            now_ns=now_ns,
            adopted_proposal=True,
        )
        for row in value.reads:
            recorder.read(row.kind)
        require_reproduced_trace(value, recorder.finish())
    adoption = next((read for read in displayed.trace.reads if read.kind == "ADOPTION"), None)
    expected_adoption = json.dumps(
        displayed.model_dump(exclude={"trace"}), sort_keys=True, separators=(",", ":")
    ).encode()
    if (
        not displayed.proposal_id
        or not displayed.adoption_act_id
        or not displayed.ingress_id
        or not displayed.interpretation_revision
        or not displayed.command_digest
        or not displayed.proposed_result_digest
        or displayed.principal_id != displayed.trace.principal_id
        or displayed.trace.registry_inputs != PROPOSAL_REGISTRY_INPUTS
        or tuple(read.kind for read in displayed.trace.reads)
        != ("TRUST", "PLANNING", "REGISTRY", "ADOPTION")
        or hashlib.sha256(display_bytes).hexdigest() != displayed.display_digest
        or adoption is None
        or adoption.canonical_value != expected_adoption
        or freshness_bytes(displayed) != freshness_bytes(reproduced)
    ):
        raise AuthorityTraceViolation(
            "proposal/adoption freshness mismatch; redisplay and new adoption required"
        )


__all__ = [
    "AuthoritySource",
    "AuthorityTraceViolation",
    "PlanningAuthorityRecorder",
    "decode_trace",
    "freshness_bytes",
    "require_reproduced_trace",
    "trace_bytes",
    "validate_proposal_freshness",
]
