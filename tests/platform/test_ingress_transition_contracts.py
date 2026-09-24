"""Wire consumers for the ingress seam; constructed observations grant no authority."""

from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.platform import ingress_transition_contracts as ingress
from chiplog.platform._ingress_contracts import Head
from chiplog.platform._owner_publication_contracts import BrokerOperation
from chiplog.platform.ingress_runtime_snapshot import IngressRuntimeSnapshot


def head(name: str) -> Head:
    return Head(identity=name, head=name + ":head", fingerprint="a" * 64)


def snapshot() -> IngressRuntimeSnapshot:
    return IngressRuntimeSnapshot(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=0,
        journal_head=head("journal"),
        materialization_commitment="b" * 64,
        ingress_manifest=head("manifest"),
        epoch=head("epoch"),
        fence=0,
        state="OPEN",
        tokens=(),
        canonical_reserves=(),
        ready=(),
        blocked=(),
        bounds=(),
        deficits=(),
        scheduler_trace=(),
        quarantines=(),
        parser_remainder_fences=(),
        settled_tokens=(),
        physical_occupancy=(),
        poll_pages=(),
        poll_cursors=(),
        handoffs=(),
        drain=None,
        producer_quiescence=None,
        complete_inventory_fingerprint="c" * 64,
    )


def test_command_dispatch_covers_the_existing_broker_ingress_registry() -> None:
    mapping = TypeAdapter(ingress.IngressTransitionCommand).json_schema()["discriminator"][
        "mapping"
    ]
    assert set(mapping) == {op for op in get_args(BrokerOperation) if op.startswith("ingress.")}


def test_raw_request_retains_binary_source_and_complete_empty_cut() -> None:
    request = ingress.IngressTransitionRequest(
        identity=ingress.IngressCommandIdentity(
            tenant_id="tenant", database_id="database", command_id="stage"
        ),
        command=ingress.StageIngressRaw(
            token=head("token"), expected_token_state=head("state"), raw_bytes=b"\xff\x00raw"
        ),
        observed=snapshot(),
        source_observations=(
            ingress.RetainedIngressSource(
                source=head("source"),
                reader_id="reader",
                schema_id="source.v1",
                canonical_source_bytes=b"\x80\x00source",
            ),
        ),
    )
    restored = ingress.IngressTransitionRequest.model_validate_json(request.model_dump_json())
    assert restored == request
    assert isinstance(restored.command, ingress.StageIngressRaw)
    assert restored.command.raw_bytes == b"\xff\x00raw"
    assert restored.source_observations[0].canonical_source_bytes == b"\x80\x00source"


@pytest.mark.parametrize(
    "field",
    [
        "blocked",
        "bounds",
        "deficits",
        "quarantines",
        "parser_remainder_fences",
        "settled_tokens",
        "physical_occupancy",
        "poll_pages",
        "poll_cursors",
        "handoffs",
        "drain",
        "producer_quiescence",
    ],
)
def test_snapshot_omission_is_not_an_empty_family(field: str) -> None:
    wire = snapshot().model_dump()
    del wire[field]
    with pytest.raises(ValidationError):
        IngressRuntimeSnapshot.model_validate(wire)


def test_prepared_bytes_are_not_a_selected_or_uncertain_publication() -> None:
    prepared = ingress.PreparedIngressTransition(
        source_request_fingerprint="a" * 64,
        complete_records=(
            ingress.IngressCanonicalMember(
                record_id="record",
                record_kind="CUSTODY",
                schema_id="custody.v1",
                canonical_record_bytes=b"\xff\x00record",
                fingerprint="b" * 64,
            ),
        ),
        complete_commitment="c" * 64,
    )
    assert (
        TypeAdapter(ingress.IngressPreparationResult).validate_json(prepared.model_dump_json())
        == prepared
    )
    with pytest.raises(ValidationError):
        TypeAdapter(ingress.IngressPublicationResult).validate_json(prepared.model_dump_json())
    identity = ingress.IngressCommandIdentity(
        tenant_id="tenant", database_id="database", command_id="original-command"
    )
    uncertain = ingress.UncertainIngressPublication(
        identity=identity, original_request_fingerprint="a" * 64, reason="lost writer response"
    )
    selected = ingress.SelectedIngressPublication(
        disposition="EXACT_REPLAY",
        identity=identity,
        original_request_fingerprint="a" * 64,
        selected_decision=head("original-decision"),
        complete_records=(head("record"),),
        complete_commitment="c" * 64,
    )
    adapter: TypeAdapter[ingress.IngressPublicationResult] = TypeAdapter(
        ingress.IngressPublicationResult
    )
    for result in (uncertain, selected):
        assert adapter.validate_json(result.model_dump_json()) == result
        wire = result.model_dump()
        wire["ack_permitted"] = True
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)
    with pytest.raises(ValidationError):
        ingress.SelectedIngressPublication.model_validate_json(uncertain.model_dump_json())


@pytest.mark.parametrize("value", [False, -1, 2**64, "0"])
def test_snapshot_fence_is_a_strict_bounded_integer(value: object) -> None:
    wire = snapshot().model_dump()
    wire["fence"] = value
    with pytest.raises(ValidationError):
        IngressRuntimeSnapshot.model_validate(wire)
