from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

import pytest

from chiplog.capabilities.planning._planning import (
    _batch_fingerprint,
    _InMemoryPlanningRepository,
    _PlanningPublication,
    _PlanningUseCase,
    _record,
    _request_fingerprint,
)
from chiplog.capabilities.projections import PlanningProjectionQueries
from chiplog.capabilities.projections._planning import (
    _PlanningProjectionRebuilder,
    _PlanningReadStore,
)
from chiplog.domain_primitives import RecordId, TenantId

from .test_r5_planning import TENANT, TrustFake, command, context


def committed_repository() -> _InMemoryPlanningRepository:
    repository = _InMemoryPlanningRepository()
    result = _PlanningUseCase(repository, TrustFake()).execute(context(), command())
    assert result.disposition == "COMMITTED"
    return repository


def install_publication(
    repository: _InMemoryPlanningRepository, publication: _PlanningPublication
) -> None:
    repository.transact(
        TENANT,
        lambda state: state.publications.__setitem__(0, publication),
    )


def test_projection_query_port_is_structural_and_tenant_scoped() -> None:
    projection: PlanningProjectionQueries = _PlanningProjectionRebuilder(
        cast(_PlanningReadStore, committed_repository())
    ).rebuild(TENANT)
    view = projection.get(TENANT, RecordId(TENANT, "line-1"))
    assert view is not None
    assert view.purpose == "Ship the first trustworthy slice"
    foreign = TenantId("foreign")
    assert projection.get(foreign, RecordId(foreign, "line-1")) is None
    assert projection.get(TENANT, RecordId(foreign, "line-1")) is None


def test_projection_rebuild_is_deterministic_from_committed_publications() -> None:
    repository = committed_repository()
    rebuilder = _PlanningProjectionRebuilder(cast(_PlanningReadStore, repository))
    first = rebuilder.rebuild(TENANT)
    second = rebuilder.rebuild(TENANT)
    assert first.checkpoint == second.checkpoint
    assert first.get(TENANT, RecordId(TENANT, "line-1")) == second.get(
        TENANT, RecordId(TENANT, "line-1")
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"tenant_id": TenantId("foreign")},
        {"log_identity": "provider:tenant-1"},
        {"inclusive_frontier": 99},
        {"reducer_version": 99},
        {"schema_version": 99},
        {"source_digest": "forged"},
        {"rows": ()},
    ],
)
def test_invalid_checkpoint_falls_back_to_full_verified_rebuild(
    mutation: dict[str, object],
) -> None:
    repository = committed_repository()
    rebuilder = _PlanningProjectionRebuilder(cast(_PlanningReadStore, repository))
    valid = rebuilder.rebuild(TENANT)
    invalid = replace(valid.checkpoint, **mutation)  # type: ignore[arg-type]
    rebuilt = rebuilder.rebuild(TENANT, invalid)
    assert rebuilt.get(TENANT, RecordId(TENANT, "line-1")) == valid.get(
        TENANT, RecordId(TENANT, "line-1")
    )
    assert rebuilt.checkpoint == valid.checkpoint


def test_unverified_committed_record_is_rejected() -> None:
    repository = committed_repository()
    publication = repository.committed_publications(TENANT)[0]
    # Owner values are immutable; forced corruption of durable bytes is still rejected.
    object.__setattr__(publication.records[3], "canonical_bytes", b"forged")
    with pytest.raises(ValueError, match="unverified"):
        _PlanningProjectionRebuilder(cast(_PlanningReadStore, repository)).rebuild(TENANT)


def test_projection_ignores_adapter_fields_side_channel() -> None:
    repository = committed_repository()
    publication = repository.committed_publications(TENANT)[0]
    object.__setattr__(publication.records[3], "fields", {"purpose": "adapter-forged"})
    projection = _PlanningProjectionRebuilder(cast(_PlanningReadStore, repository)).rebuild(TENANT)
    view = projection.get(TENANT, RecordId(TENANT, "line-1"))
    assert view is not None
    assert view.purpose == "Ship the first trustworthy slice"


@pytest.mark.parametrize(
    ("record_index", "field", "changed"),
    [
        (0, "allocation_manifest", []),
        (1, "trust_reference", {"trust_head": "substituted"}),
        (2, "initial_revision_id", {"tenant_id": "tenant-1", "value": "other"}),
        (3, "intention_line_id", {"tenant_id": "tenant-1", "value": "other"}),
        (4, "command_id", {"tenant_id": "tenant-1", "value": "other"}),
        (4, "commit_sequence", 99),
    ],
)
def test_projection_rejects_reciprocal_binding_mutants(
    record_index: int, field: str, changed: object
) -> None:
    repository = committed_repository()
    publication = repository.committed_publications(TENANT)[0]
    original = publication.records[record_index]
    fields = json.loads(original.canonical_bytes)["fields"]
    fields[field] = changed
    replacement = _record(original.record_id, original.record_type_id, fields)
    records = list(publication.records)
    records[record_index] = replacement
    install_publication(repository, publication._replace(records=tuple(records)))
    with pytest.raises(ValueError, match="unverified"):
        _PlanningProjectionRebuilder(cast(_PlanningReadStore, repository)).rebuild(TENANT)


def test_projection_rejects_request_fingerprint_substitution() -> None:
    repository = committed_repository()
    publication = repository.committed_publications(TENANT)[0]
    install_publication(repository, publication._replace(request_fingerprint="substituted"))
    with pytest.raises(ValueError, match="unverified"):
        _PlanningProjectionRebuilder(cast(_PlanningReadStore, repository)).rebuild(TENANT)


def test_projection_rejects_coherent_record_rewrite_against_original_batch_binding() -> None:
    repository = committed_repository()
    publication = repository.committed_publications(TENANT)[0]
    original = publication.records[3]
    fields = json.loads(original.canonical_bytes)["fields"]
    fields["purpose"] = "coherently regenerated content"
    replacement = _record(original.record_id, original.record_type_id, fields)
    records = list(publication.records)
    records[3] = replacement
    committed_manifest = tuple(
        (record.record_id, record.record_type_id, record.fingerprint) for record in records[:4]
    )
    result_fields = json.loads(records[4].canonical_bytes)["fields"]
    result_fields["record_manifest"] = [
        {
            "fingerprint": item_fingerprint,
            "record_id": {
                "tenant_id": record_id.tenant_id.value,
                "value": record_id.value,
            },
            "record_type_id": record_type_id,
        }
        for record_id, record_type_id, item_fingerprint in committed_manifest
    ]
    result_fields["batch_fingerprint"] = _batch_fingerprint(committed_manifest)
    result_record = records[4]
    records[4] = _record(result_record.record_id, result_record.record_type_id, result_fields)
    changed_request = _request_fingerprint(
        context(), replace(command(), purpose="coherently regenerated content"), None
    )
    rewritten_manifest = tuple(
        (record.record_id, record.record_type_id, record.fingerprint) for record in records
    )
    rewritten_batch = _batch_fingerprint(rewritten_manifest)
    install_publication(
        repository,
        publication._replace(
            records=tuple(records),
            record_manifest=rewritten_manifest,
            batch_fingerprint=rewritten_batch,
            request_fingerprint=changed_request,
        ),
    )
    # All records and publication side fields are coherent, but the owner-issued immutable
    # result binding remains the original authority and rejects the adapter reconstruction.
    with pytest.raises(ValueError, match="unverified"):
        _PlanningProjectionRebuilder(cast(_PlanningReadStore, repository)).rebuild(TENANT)


def test_projection_cannot_authorize_or_submit_a_command() -> None:
    projection = _PlanningProjectionRebuilder(
        cast(_PlanningReadStore, committed_repository())
    ).rebuild(TENANT)
    assert not hasattr(projection, "execute")
    assert not hasattr(projection, "revalidate")
    assert not hasattr(projection, "repository")


def test_r5_lane_has_no_dependency_on_r4_types() -> None:
    import inspect

    import chiplog.capabilities.planning._planning as planning
    import chiplog.capabilities.projections._planning as projections

    assert "deployment_trust" not in inspect.getsource(planning)
    assert "deployment_trust" not in inspect.getsource(projections)
