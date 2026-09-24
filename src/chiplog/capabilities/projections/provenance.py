"""Subject-addressed complete closures supplied by independently verified producers.

This registry checks data against its inputs; it does not authenticate the inputs.
Composition must obtain bindings from the enclosing owner records, never from a
candidate disclosure envelope. Legacy envelope serialization remains unchanged.
"""

from dataclasses import dataclass
from types import MappingProxyType

from pydantic import Field

from .r9_boundary import Frozen, WorkspaceRejected
from .workspace_boundary import DisclosureEnvelope, SourceReference
from .workspace_boundary import ProvenanceSubject as ProvenanceSubject


class ProvenanceBinding(Frozen):
    subject: ProvenanceSubject
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: tuple[SourceReference, ...] = Field(min_length=1)


@dataclass(frozen=True)
class _Key:
    tenant: str
    producer: str
    record: str
    revision: str

    @classmethod
    def of(cls, subject: ProvenanceSubject) -> _Key:
        return cls(subject.tenant_id, subject.producer, subject.record_id, subject.revision)


class ProvenanceClosures:
    def __init__(self, bindings: tuple[ProvenanceBinding, ...]) -> None:
        indexed: dict[_Key, ProvenanceBinding] = {}
        for binding in bindings:
            key = _Key.of(binding.subject)
            if key in indexed:
                raise WorkspaceRejected("duplicate independent provenance subject")
            identities = tuple(
                (source.owner, source.record_id, source.record_version)
                for source in binding.sources
            )
            if identities != tuple(sorted(set(identities))):
                raise WorkspaceRejected("independent closure is not canonical and complete")
            if any(source.tenant_id != key.tenant for source in binding.sources):
                raise WorkspaceRejected("foreign source in independent closure")
            indexed[key] = binding
        self._bindings = MappingProxyType(indexed)

    def check(self, subject: ProvenanceSubject, envelope: DisclosureEnvelope) -> None:
        """Subject must come from the enclosing entry/owner row, not the envelope."""
        expected = self._bindings.get(_Key.of(subject))
        if expected is None:
            raise WorkspaceRejected("unknown independent provenance subject")
        if (
            envelope.tenant_id != subject.tenant_id
            or envelope.content_digest != expected.content_digest
            or envelope.sources != expected.sources
        ):
            raise WorkspaceRejected("subject-bound provenance differs from complete closure")
