from __future__ import annotations

import hashlib
import json
from typing import Literal

import pytest
from pydantic import Field, ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present, RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    RecoveryFrontierRegistry,
    RecoveryRegistryRow,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    RecoveryFrontierRegistryIntegrityError,
    decode_frontier_registry,
    frontier_registry_content_fingerprint,
    frontier_registry_reference,
)


class _IndependentContentPreimage(RecoveryDTO):
    schema_id: Literal["chiplog.recovery.frontier-registry-content.v1"] = (
        "chiplog.recovery.frontier-registry-content.v1"
    )
    registry_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    ordered_rows: tuple[RecoveryRegistryRow, ...]


def _row(ordinal: int, family: Literal["RUN", "TURN"] = "RUN") -> RecoveryRegistryRow:
    return RecoveryRegistryRow(
        family=family,
        ordinal=ordinal,
        subject_extractor_id=f"extract:{ordinal}",
        cardinality_rule=f"cardinality:{ordinal}",
        terminal_conflict_rule=f"conflict:{ordinal}",
        serialization_rule=f"serialization:{ordinal}",
        canonicalization_version=f"canonical:{ordinal}",
    )


def _content_fingerprint(
    registry_id: str, version: str, ordered_rows: tuple[RecoveryRegistryRow, ...]
) -> str:
    return hashlib.sha256(
        _IndependentContentPreimage(
            registry_id=registry_id, version=version, ordered_rows=ordered_rows
        ).canonical_bytes()
    ).hexdigest()


def _registry(
    rows: tuple[RecoveryRegistryRow, ...] = (_row(0), _row(1, "TURN")),
) -> RecoveryFrontierRegistry:
    return RecoveryFrontierRegistry(
        registry_id="registry:recovery",
        version="v1",
        ordered_rows=rows,
        fingerprint=_content_fingerprint("registry:recovery", "v1", rows),
    )


def _external_reference(raw: bytes, subject_id: str = "registry:recovery") -> CallSubjectHead:
    digest = hashlib.sha256(raw).hexdigest()
    return CallSubjectHead(
        subject_id=subject_id,
        revision=Present(head=RECOVERY_FRONTIER_REGISTRY_SCHEMA + ":" + digest, fingerprint=digest),
    )


def _canonical_from_wire(wire: dict[str, object]) -> bytes:
    return RecoveryFrontierRegistry.model_validate_json(json.dumps(wire)).canonical_bytes()


def test_decodes_nonempty_registry_and_keeps_two_digest_domains_independent() -> None:
    registry = _registry()
    raw = registry.canonical_bytes()
    expected = _external_reference(raw)

    assert frontier_registry_content_fingerprint(
        registry.registry_id, registry.version, registry.ordered_rows
    ) == _content_fingerprint(registry.registry_id, registry.version, registry.ordered_rows)
    assert registry.fingerprint != expected.revision.fingerprint
    assert frontier_registry_reference(registry) == expected
    assert (
        decode_frontier_registry(
            RECOVERY_FRONTIER_REGISTRY_SCHEMA, raw, expected_reference=expected
        )
        == registry
    )


def test_existing_dto_rejects_empty_registry_rows() -> None:
    with pytest.raises(ValidationError):
        _registry(())


@pytest.mark.parametrize(
    ("schema_id", "raw", "reference"),
    [
        (
            "chiplog.unknown.v1",
            _registry().canonical_bytes(),
            _external_reference(_registry().canonical_bytes()),
        ),
        (RECOVERY_FRONTIER_REGISTRY_SCHEMA, b"{", _external_reference(b"{")),
        (
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            b" " + _registry().canonical_bytes(),
            _external_reference(b" " + _registry().canonical_bytes()),
        ),
    ],
)
def test_decoder_rejects_schema_malformed_and_noncanonical_bytes(
    schema_id: str, raw: bytes, reference: CallSubjectHead
) -> None:
    with pytest.raises(RecoveryFrontierRegistryIntegrityError) as raised:
        decode_frontier_registry(schema_id, raw, expected_reference=reference)

    assert raised.value.operation == "decode_frontier_registry"
    assert raised.value.schema_id == schema_id
    assert raised.value.expected_reference == reference


@pytest.mark.parametrize(
    "mutate",
    [
        lambda wire: wire.__setitem__("fingerprint", "0" * 64),
        lambda wire: wire.__setitem__("registry_id", "registry:other"),
    ],
)
def test_decoder_rejects_embedded_content_or_subject_mutation(
    mutate: object,
) -> None:
    wire = json.loads(_registry().canonical_bytes())
    assert callable(mutate)
    mutate(wire)
    raw = _canonical_from_wire(wire)
    reference = _external_reference(raw)

    with pytest.raises(RecoveryFrontierRegistryIntegrityError):
        decode_frontier_registry(
            RECOVERY_FRONTIER_REGISTRY_SCHEMA, raw, expected_reference=reference
        )


@pytest.mark.parametrize(
    "reference",
    [
        CallSubjectHead(
            subject_id="registry:wrong",
            revision=Present(
                head=RECOVERY_FRONTIER_REGISTRY_SCHEMA
                + ":"
                + hashlib.sha256(_registry().canonical_bytes()).hexdigest(),
                fingerprint=hashlib.sha256(_registry().canonical_bytes()).hexdigest(),
            ),
        ),
        CallSubjectHead(
            subject_id="registry:recovery",
            revision=Present(
                head="wrong",
                fingerprint=hashlib.sha256(_registry().canonical_bytes()).hexdigest(),
            ),
        ),
        CallSubjectHead(
            subject_id="registry:recovery",
            revision=Present(
                head=RECOVERY_FRONTIER_REGISTRY_SCHEMA
                + ":"
                + hashlib.sha256(_registry().canonical_bytes()).hexdigest(),
                fingerprint="0" * 64,
            ),
        ),
    ],
)
def test_decoder_rejects_wrong_external_reference(reference: CallSubjectHead) -> None:
    raw = _registry().canonical_bytes()
    with pytest.raises(RecoveryFrontierRegistryIntegrityError):
        decode_frontier_registry(
            RECOVERY_FRONTIER_REGISTRY_SCHEMA, raw, expected_reference=reference
        )


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ((_row(0), _row(0)), "registry rows are duplicated"),
        ((_row(0), _row(0, "TURN")), "registry row ordinals"),
        ((_row(1), _row(0, "TURN")), "registry row ordinals"),
    ],
)
def test_decoder_rejects_duplicate_or_out_of_order_ordinals(
    rows: tuple[RecoveryRegistryRow, ...],
    reason: str,
) -> None:
    registry = _registry(rows)
    raw = registry.canonical_bytes()
    with pytest.raises(RecoveryFrontierRegistryIntegrityError, match=reason):
        decode_frontier_registry(
            RECOVERY_FRONTIER_REGISTRY_SCHEMA, raw, expected_reference=_external_reference(raw)
        )


def test_reference_helper_refuses_bad_embedded_fingerprint_and_row_order() -> None:
    bad_fingerprint = _registry().model_copy(update={"fingerprint": "0" * 64})
    bad_order = _registry((_row(1), _row(0, "TURN")))

    for registry in (bad_fingerprint, bad_order):
        with pytest.raises(RecoveryFrontierRegistryIntegrityError) as raised:
            frontier_registry_reference(registry)
        assert raised.value.operation == "frontier_registry_reference"
        assert raised.value.schema_id == RECOVERY_FRONTIER_REGISTRY_SCHEMA
        assert raised.value.expected_reference is None


def test_h1_v2_registry_has_immutable_profile_identity_and_all_family_ordinals() -> None:
    from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
        execution_h1_zero_call_frontier_registry_v2,
    )

    registry = execution_h1_zero_call_frontier_registry_v2()

    assert registry.registry_id == "chiplog.execution.h1-zero-call-recovery-frontier"
    assert registry.version == "2"
    assert tuple(row.ordinal for row in registry.ordered_rows) == tuple(range(17))
    assert tuple(row.family for row in registry.ordered_rows) == (
        "RUN",
        "TURN",
        "SEALED_RESPONSE",
        "CALL",
        "EFFECT",
        "EVIDENCE",
        "DELIVERY",
        "AUTHORITY",
        "MANDATE",
        "POLICY",
        "PROMPT",
        "TOOL_SCHEMA",
        "RECIPIENT",
        "SEMANTIC_BINDING",
        "EXECUTION_LINEAGE",
        "OBLIGATION",
        "SEMANTIC_REDUCTION",
    )
    for row in registry.ordered_rows:
        assert row.subject_extractor_id == f"execution-h1-zero-call-v2:{row.family}:subjects"
        assert row.cardinality_rule == f"execution-h1-zero-call-v2:{row.family}:cardinality"
        assert row.terminal_conflict_rule == f"execution-h1-zero-call-v2:{row.family}:conflicts"
        assert row.serialization_rule == f"execution-h1-zero-call-v2:{row.family}:serialization"
        assert row.canonicalization_version == "chiplog.recovery.frontier.v1"
    assert frontier_registry_reference(registry).subject_id == registry.registry_id
