"""Closed disclosure lattice and executable sink inventory owned by projections."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .r9_boundary import ReadContextPort, SourceHeadPort, SubjectSourceHeadPort, WorkspaceRejected
from .workspace_boundary import (
    DisclosureEnvelope,
    DisclosureLabel,
    ProvenanceSubject,
    WorkspaceReadContext,
)


def label(value: str, endpoints: tuple[str, ...] = ()) -> DisclosureLabel:
    result = DisclosureLabel.model_validate(
        {"lattice_version": "chiplog.disclosure.v1", "value": value, "allowed_endpoints": endpoints}
    )
    validate_label(result)
    return result


def validate_label(value: DisclosureLabel) -> None:
    endpoints = value.allowed_endpoints
    if endpoints != tuple(sorted(set(endpoints))) or any(not item for item in endpoints):
        raise WorkspaceRejected("noncanonical disclosure endpoints")
    if (value.value == "ENDPOINT_RESTRICTED") != bool(endpoints):
        raise WorkspaceRejected("invalid disclosure lattice member")


def join(values: tuple[DisclosureLabel, ...]) -> DisclosureLabel:
    if not values:
        raise WorkspaceRejected("missing disclosure label")
    allowed: set[str] | None = None
    denied = False
    for value in values:
        validate_label(value)
        if value.value == "DENY_ALL":
            denied = True
        elif value.value == "ENDPOINT_RESTRICTED":
            incoming = set(value.allowed_endpoints)
            allowed = incoming if allowed is None else allowed & incoming
    if denied or allowed == set():
        return label("DENY_ALL")
    return (
        label("UNRESTRICTED")
        if allowed is None
        else label("ENDPOINT_RESTRICTED", tuple(sorted(allowed)))
    )


class CurrentDisclosureGuard:
    def __init__(self, contexts: ReadContextPort, sources: SourceHeadPort) -> None:
        self._contexts = contexts
        self._sources = sources

    def check(
        self, envelope: DisclosureEnvelope, context: WorkspaceReadContext, endpoint: str
    ) -> None:
        self._check(envelope, context, endpoint, None)

    def check_subject(
        self,
        subject: ProvenanceSubject,
        envelope: DisclosureEnvelope,
        context: WorkspaceReadContext,
        endpoint: str,
    ) -> None:
        self._check(envelope, context, endpoint, subject)

    def _check(
        self,
        envelope: DisclosureEnvelope,
        context: WorkspaceReadContext,
        endpoint: str,
        subject: ProvenanceSubject | None,
    ) -> None:
        self._contexts.validate(context)
        if (
            envelope.tenant_id != context.tenant_id
            or envelope.policy_head != context.policy_head
            or envelope.contour_head != context.principal_contour_head
            or envelope.deletion_fence_head != context.deletion_fence_head
        ):
            raise WorkspaceRejected("disclosure context/head mismatch")
        keys = tuple((s.owner, s.record_id, s.record_version) for s in envelope.sources)
        if keys != tuple(sorted(set(keys))):
            raise WorkspaceRejected("provenance missing canonical unique order")
        for source in envelope.sources:
            if source.tenant_id != envelope.tenant_id:
                raise WorkspaceRejected("foreign provenance")
            self._sources.validate(source, context)
        if subject is None:
            self._sources.validate_manifest(envelope, context)
        else:
            if not isinstance(self._sources, SubjectSourceHeadPort):
                raise WorkspaceRejected("subject-bound provenance source port is required")
            if subject.tenant_id != context.tenant_id:
                raise WorkspaceRejected("foreign provenance subject")
            self._sources.validate_subject(subject, envelope, context)
        inherited = join(tuple(source.label for source in envelope.sources))
        if join((inherited, envelope.label)) != envelope.label:
            raise WorkspaceRejected("model cannot narrow inherited disclosure")
        if envelope.label.value == "DENY_ALL" or (
            envelope.label.value == "ENDPOINT_RESTRICTED"
            and endpoint not in envelope.label.allowed_endpoints
        ):
            raise WorkspaceRejected("endpoint denied by disclosure")


def verify_payload(payload: bytes, envelope: DisclosureEnvelope) -> None:
    if hashlib.sha256(payload).hexdigest() != envelope.content_digest:
        raise WorkspaceRejected("content/provenance digest mismatch")


@dataclass(frozen=True)
class DisclosureSurface:
    surface_id: str
    implementation: Callable[..., object]
    owner: str
    provenance_producer: str
    label_envelope: str
    reader_endpoint: str
    narrowing_invalidator: str
    deletion_invalidator: str
    replay_rebuild: str
    enforcement_cut: str


class DisclosureSurfaceManifest:
    """Compare independent compiled inventory with actual bound execution paths."""

    def __init__(
        self, expected: tuple[DisclosureSurface, ...], actual: Mapping[str, Callable[..., object]]
    ) -> None:
        keys = tuple(row.surface_id for row in expected)
        if len(keys) != len(set(keys)) or set(keys) != set(actual):
            raise WorkspaceRejected("disclosure surface exact-set mismatch")
        if len({id(row.implementation) for row in expected}) != len(expected):
            raise WorkspaceRejected("aliased disclosure surface")
        for row in expected:
            if not all(
                (
                    row.owner,
                    row.provenance_producer,
                    row.label_envelope,
                    row.reader_endpoint,
                    row.narrowing_invalidator,
                    row.deletion_invalidator,
                    row.replay_rebuild,
                    row.enforcement_cut,
                )
            ):
                raise WorkspaceRejected("incomplete disclosure surface contract")
            if actual[row.surface_id] is not row.implementation:
                raise WorkspaceRejected("substituted disclosure implementation")
        self._expected = expected
        self._actual = actual
        self.entries = MappingProxyType(dict(actual))

    def validate(self) -> None:
        if set(self._actual) != set(self.entries):
            raise WorkspaceRejected("dynamic disclosure surface mutation")
        for row in self._expected:
            if self._actual[row.surface_id] is not row.implementation:
                raise WorkspaceRejected("dynamic disclosure implementation substitution")
