"""Wire consumers: discovered paths are observations, not a registry attestation."""

from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.recovery_contracts import WorkerCommitApplicability
from chiplog.platform import runtime_surface_contracts as surface
from chiplog.platform._ingress_contracts import Head, SourceClass


def symbol(name: str) -> surface.ExecutableSymbol:
    return surface.ExecutableSymbol(
        module="chiplog.fixture",
        qualified_name=name,
        source_path="fixture.py",
        source_fingerprint="a" * 64,
    )


def head(name: str) -> Head:
    return Head(identity=name, head=name + ":head", fingerprint="b" * 64)


def record(name: str) -> surface.RuntimeRecordSchema:
    return surface.RuntimeRecordSchema(
        owner="broker_ingress",
        record_kind=name,
        schema_id=name + ".v1",
        schema_fingerprint="c" * 64,
    )


def retained() -> surface.RetainedIngressHandoff:
    return surface.RetainedIngressHandoff(
        retention_contract=head("source-retention"),
        allocation=symbol("allocate"),
        custody_publication=symbol("custody"),
        release_or_ack=symbol("ack"),
    )


def test_all_required_source_classes_are_representable_but_dashboard_is_not() -> None:
    expected = {
        "TELEGRAM_PUSH",
        "TELEGRAM_POLL",
        "CLI",
        "PROVIDER_CALLBACK",
        "PROVIDER_POLL",
        "RECONCILIATION",
        "TOOL_RESULT",
    }
    assert set(get_args(SourceClass)) == expected
    rows = tuple(
        surface.EvidenceIngressSurfaceRow(
            row_id="row:" + kind,
            source_class=kind,
            source_contract=head(kind),
            adapter_entrypoint=symbol(kind),
            authenticator=symbol("authenticate:" + kind),
            handoff=retained(),
            receipt_token_schema=record("token"),
            custody_schema=record("custody"),
            admission_entrypoint=symbol("admit"),
            replay_identity_version="1",
        )
        for kind in get_args(SourceClass)
    )
    manifest = surface.EvidenceIngressSurfaceManifest(
        manifest_id="ingress",
        version="1",
        ordered_rows=rows,
        fingerprint="d" * 64,
    )
    restored = surface.EvidenceIngressSurfaceManifest.model_validate_json(
        manifest.model_dump_json()
    )
    assert restored == manifest
    assert {row.source_class for row in restored.ordered_rows} == expected
    wire = rows[0].model_dump()
    wire["source_class"] = "DASHBOARD"
    with pytest.raises(ValidationError):
        surface.EvidenceIngressSurfaceRow.model_validate(wire)


def test_worker_registry_uses_exact_existing_applicability_universe() -> None:
    actual = TypeAdapter(WorkerCommitApplicability).json_schema()["discriminator"]["mapping"]
    assert set(get_args(surface.WorkerApplicabilityKind)) == set(actual)
    rows = tuple(
        surface.WorkerAuthoritativeCommitRow(
            row_id=kind,
            command_id="command",
            request_schema="request.v1",
            request_schema_fingerprint="a" * 64,
            handler=symbol("handler"),
            writer_boundary=symbol("sole_writer"),
            applicability=kind,
            published_records=(record("first"), record("second")),
            current_cut_validator=symbol("recheck"),
        )
        for kind in get_args(surface.WorkerApplicabilityKind)
    )
    registry = surface.WorkerAuthoritativeCommitRegistry(
        registry_id="writers",
        version="1",
        ordered_rows=rows,
        fingerprint="b" * 64,
    )
    assert (
        surface.WorkerAuthoritativeCommitRegistry.model_validate_json(registry.model_dump_json())
        == registry
    )
    for field in ("handler", "writer_boundary", "published_records", "current_cut_validator"):
        wire = rows[0].model_dump()
        del wire[field]
        with pytest.raises(ValidationError):
            surface.WorkerAuthoritativeCommitRow.model_validate(wire)
    wire = rows[0].model_dump()
    wire["applicability"] = "ANY_WORKER"
    with pytest.raises(ValidationError):
        surface.WorkerAuthoritativeCommitRow.model_validate(wire)


def test_destructive_handoff_requires_loss_slot_and_cannot_alias_retention() -> None:
    destructive = surface.DestructiveIngressHandoff(
        preallocate_loss_slot=symbol("loss_slot"),
        destructive_receive=symbol("receive"),
        exact_raw_custody_cas=symbol("raw_cas"),
        enumerate_unsettled_loss=symbol("losses"),
        release_or_ack=symbol("release"),
    )
    adapter: TypeAdapter[surface.IngressHandoff] = TypeAdapter(surface.IngressHandoff)
    assert adapter.validate_json(destructive.model_dump_json()) == destructive
    assert adapter.validate_json(retained().model_dump_json()) == retained()
    for field in ("preallocate_loss_slot", "exact_raw_custody_cas", "enumerate_unsettled_loss"):
        wire = destructive.model_dump()
        del wire[field]
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)
    with pytest.raises(ValidationError):
        surface.RetainedIngressHandoff.model_validate_json(destructive.model_dump_json())


def test_discovery_keeps_unexpected_and_empty_paths_observable() -> None:
    cut = surface.RuntimeSurfaceCut(
        runtime_profile=head("profile"),
        broker_generation="broker",
        runtime_generation="runtime",
        source_inventory_fingerprint="a" * 64,
        worker_registry=head("writers"),
        ingress_manifest=head("ingress"),
    )
    empty = surface.RuntimeSurfaceDiscovery(
        cut=cut,
        discovery_algorithm=head("discovery"),
        worker_paths=(),
        ingress_paths=(),
        complete_observation_fingerprint="b" * 64,
    )
    assert surface.RuntimeSurfaceDiscovery.model_validate_json(empty.model_dump_json()) == empty
    # A rogue path must be reportable even if there is no registry row for it.
    found = surface.DiscoveredWorkerPath(
        command_id="unexpected",
        request_schema="unexpected.v1",
        request_schema_fingerprint="c" * 64,
        handler=symbol("unexpected_handler"),
        writer_boundary=symbol("raw_writer"),
        applicability="NON_SCHEDULER_NOT_APPLICABLE",
        published_records=(record("unexpected"),),
        current_cut_validator=symbol("unexpected_validator"),
        discovery_origin=symbol("scan_source"),
    )
    observed = empty.model_copy(update={"worker_paths": (found,)})
    assert (
        surface.RuntimeSurfaceDiscovery.model_validate_json(observed.model_dump_json()) == observed
    )
    with pytest.raises(ValidationError):
        surface.WorkerAuthoritativeCommitRegistry.model_validate_json(observed.model_dump_json())
    assert "row_id" not in surface.DiscoveredWorkerPath.model_fields
    wire = observed.model_dump()
    wire["verified_complete"] = True
    with pytest.raises(ValidationError):
        surface.RuntimeSurfaceDiscovery.model_validate(wire)
