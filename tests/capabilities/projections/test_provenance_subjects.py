"""Equal payloads do not authorize exchanging independent source closures."""

import pytest

from chiplog.capabilities.projections.disclosure import label
from chiplog.capabilities.projections.provenance import (
    ProvenanceBinding,
    ProvenanceClosures,
    ProvenanceSubject,
)
from chiplog.capabilities.projections.r9_boundary import WorkspaceRejected
from tests.support.workspace import envelope, source


def _subject(record: str) -> ProvenanceSubject:
    return ProvenanceSubject(
        tenant_id="tenant", producer="conversation", record_id=record, revision="1"
    )


def test_equal_bytes_from_different_origins_keep_distinct_complete_closures() -> None:
    restricted = source()
    unrestricted = source().model_copy(
        update={"record_id": "other", "label": label("UNRESTRICTED")}
    )
    first, second = _subject("first"), _subject("second")
    closures = ProvenanceClosures(
        (
            ProvenanceBinding(
                subject=first, content_digest=envelope().content_digest, sources=(restricted,)
            ),
            ProvenanceBinding(
                subject=second, content_digest=envelope().content_digest, sources=(unrestricted,)
            ),
        )
    )
    first_envelope = envelope()
    second_envelope = envelope().model_copy(
        update={"sources": (unrestricted,), "label": unrestricted.label}
    )
    closures.check(first, first_envelope)
    closures.check(second, second_envelope)
    with pytest.raises(WorkspaceRejected, match="complete closure"):
        closures.check(first, second_envelope)
    with pytest.raises(WorkspaceRejected, match="complete closure"):
        closures.check(second, first_envelope)


def test_derived_closure_cannot_drop_even_an_unrestricted_member() -> None:
    unrestricted = source().model_copy(
        update={"record_id": "z-unrestricted", "label": label("UNRESTRICTED")}
    )
    subject = _subject("derived")
    closure = (source(), unrestricted)
    registry = ProvenanceClosures(
        (
            ProvenanceBinding(
                subject=subject, content_digest=envelope().content_digest, sources=closure
            ),
        )
    )
    registry.check(subject, envelope().model_copy(update={"sources": closure}))
    with pytest.raises(WorkspaceRejected, match="complete closure"):
        registry.check(subject, envelope())


@pytest.mark.parametrize("field", ("tenant_id", "producer", "record_id", "revision"))
def test_unknown_subject_component_never_falls_back_to_matching_bytes(field: str) -> None:
    subject = _subject("known")
    registry = ProvenanceClosures(
        (
            ProvenanceBinding(
                subject=subject, content_digest=envelope().content_digest, sources=(source(),)
            ),
        )
    )
    with pytest.raises(WorkspaceRejected, match="unknown independent"):
        registry.check(subject.model_copy(update={field: "substituted"}), envelope())


def test_same_subject_cannot_be_registered_twice_even_for_equal_bytes() -> None:
    binding = ProvenanceBinding(
        subject=_subject("same"), content_digest=envelope().content_digest, sources=(source(),)
    )
    with pytest.raises(WorkspaceRejected, match="duplicate independent"):
        ProvenanceClosures((binding, binding))
