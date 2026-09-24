"""Exact owner-local decoding and selection of recovery fault rules."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Literal

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_fault_rule_contracts import (
    FaultFindingCodeV1,
    FaultRuleObservationRoleV1,
    FaultRuleRegistryIntegrityError,
    FaultRuleRegistryV1,
    FaultRuleSourceKindV1,
    FaultRuleV1,
    SelectedFaultRuleRegistryV1,
    decode_fault_rule_registry,
    fault_rule_reference,
    lookup_fault_rule,
)


def _head(subject_id: str) -> CallSubjectHead:
    digest = hashlib.sha256(subject_id.encode()).hexdigest()
    return CallSubjectHead(
        subject_id=subject_id,
        revision=Present(head="head:" + digest, fingerprint=digest),
    )


def _source() -> FaultRuleSourceKindV1:
    return FaultRuleSourceKindV1(
        owner="agent_loop",
        record_kind="SUSPENSION_PAIR",
        schema_id="chiplog.execution.suspension-pair.v2",
    )


def _rule(
    rule_id: str,
    version: str,
    *,
    code: FaultFindingCodeV1 = "CORRUPT_CANONICAL_BYTES",
    relation: str = "canonical-body",
    completeness: Literal[
        "POSITIVE_WITNESS_SUFFICIENT", "COMPLETE_REGISTERED_INVENTORY_REQUIRED"
    ] = "POSITIVE_WITNESS_SUFFICIENT",
) -> FaultRuleV1:
    return FaultRuleV1(
        rule_id=rule_id,
        version=version,
        classifier_id="classifier",
        classifier_version="v1",
        finding_code=code,
        expected_relation=relation,
        predicate_kind="CANONICAL_BODY",
        verifier_id="owner-verifier",
        verifier_version="v1",
        ordered_observation_roles=(
            FaultRuleObservationRoleV1(
                role="OBSERVED_BODY",
                min_count=1,
                max_count=1,
                allowed_owner_kinds=(_source(),),
            ),
        ),
        completeness=completeness,
        witness_contract_schema="chiplog.execution.fault-witness.v1",
        witness_contract_fingerprint="a" * 64,
    )


def _registry(*rules: FaultRuleV1) -> FaultRuleRegistryV1:
    return FaultRuleRegistryV1(
        registry_id="fault-rules",
        version="v1",
        classifier_id="classifier",
        classifier_version="v1",
        disposition_version="v1",
        ordered_rules=rules,
    )


def _registry_head(registry: FaultRuleRegistryV1) -> CallSubjectHead:
    digest = hashlib.sha256(registry.canonical_bytes()).hexdigest()
    return CallSubjectHead(
        subject_id=registry.registry_id,
        revision=Present(head=registry.schema_id + ":" + digest, fingerprint=digest),
    )


def _selected(registry: FaultRuleRegistryV1) -> SelectedFaultRuleRegistryV1:
    return SelectedFaultRuleRegistryV1(
        registry=registry,
        registry_reference=_registry_head(registry),
        canonical_registry_bytes=registry.canonical_bytes(),
        selected_decision=_head("selected-rule-registry"),
    )


def _decode(selected: SelectedFaultRuleRegistryV1) -> FaultRuleRegistryV1:
    return decode_fault_rule_registry(
        selected,
        expected_registry=selected.registry_reference,
        expected_selected_decision=selected.selected_decision,
        expected_classifier_id="classifier",
        expected_classifier_version="v1",
        expected_disposition_version="v1",
    )


def test_decodes_two_ordered_rules_and_selects_exact_same_id_version() -> None:
    first = _rule("rule", "v1")
    second = _rule("rule", "v2", relation="canonical-body-v2")
    registry = _registry(first, second)
    selected = _selected(registry)

    decoded = _decode(selected)

    assert decoded == registry
    assert fault_rule_reference(first).subject_id == "rule"
    assert fault_rule_reference(first).revision.head == (
        "chiplog.execution.recovery-fault-rule.v1:"
        + hashlib.sha256(first.canonical_bytes()).hexdigest()
    )
    assert (
        lookup_fault_rule(
            decoded,
            fault_rule_reference(second),
            finding_code="CORRUPT_CANONICAL_BYTES",
            expected_relation="canonical-body-v2",
        )
        == second
    )


def test_selected_decision_is_independent_and_binary_carrier_roundtrips() -> None:
    registry = _registry(_rule("rule", "v1"))
    selected = _selected(registry)
    binary = selected.model_copy(update={"canonical_registry_bytes": b"\xff\x00"})

    restored = SelectedFaultRuleRegistryV1.model_validate_json(binary.canonical_bytes())
    assert restored.canonical_registry_bytes == b"\xff\x00"
    with pytest.raises(FaultRuleRegistryIntegrityError, match="decode_fault_rule_registry"):
        _decode(restored)
    with pytest.raises(FaultRuleRegistryIntegrityError, match="selected decision"):
        decode_fault_rule_registry(
            selected,
            expected_registry=selected.registry_reference,
            expected_selected_decision=_head("different-selection"),
            expected_classifier_id="classifier",
            expected_classifier_version="v1",
            expected_disposition_version="v1",
        )


def test_decode_rejects_semantically_equal_but_noncanonical_registry_bytes() -> None:
    registry = _registry(_rule("rule", "v1"))
    selected = _selected(registry)
    noncanonical = selected.model_copy(
        update={"canonical_registry_bytes": b" " + registry.canonical_bytes()}
    )

    with pytest.raises(FaultRuleRegistryIntegrityError, match="not canonical"):
        _decode(noncanonical)


def test_decode_rejects_independently_supplied_wrong_registry_reference() -> None:
    selected = _selected(_registry(_rule("rule", "v1")))

    with pytest.raises(FaultRuleRegistryIntegrityError, match="expected registry"):
        decode_fault_rule_registry(
            selected,
            expected_registry=_head("other-registry"),
            expected_selected_decision=selected.selected_decision,
            expected_classifier_id="classifier",
            expected_classifier_version="v1",
            expected_disposition_version="v1",
        )


@pytest.mark.parametrize(
    "field,value",
    (
        ("registry_reference", _head("wrong-subject")),
        (
            "registry_reference",
            CallSubjectHead(
                subject_id="fault-rules",
                revision=Present(head="wrong", fingerprint="b" * 64),
            ),
        ),
        ("canonical_registry_bytes", b'{"version":"v1","registry_id":"fault-rules"}'),
    ),
)
def test_decode_rejects_noncanonical_or_wrong_registry_identity(field: str, value: object) -> None:
    selected = _selected(_registry(_rule("rule", "v1")))
    with pytest.raises(FaultRuleRegistryIntegrityError):
        _decode(selected.model_copy(update={field: value}))


@pytest.mark.parametrize(
    "expected_classifier_id,expected_classifier_version,expected_disposition_version",
    (("other", "v1", "v1"), ("classifier", "other", "v1"), ("classifier", "v1", "other")),
)
def test_decode_rejects_explicit_classifier_or_disposition_drift(
    expected_classifier_id: str, expected_classifier_version: str, expected_disposition_version: str
) -> None:
    selected = _selected(_registry(_rule("rule", "v1")))
    with pytest.raises(FaultRuleRegistryIntegrityError):
        decode_fault_rule_registry(
            selected,
            expected_registry=selected.registry_reference,
            expected_selected_decision=selected.selected_decision,
            expected_classifier_id=expected_classifier_id,
            expected_classifier_version=expected_classifier_version,
            expected_disposition_version=expected_disposition_version,
        )


@pytest.mark.parametrize(
    "build",
    (
        lambda: FaultRuleObservationRoleV1(
            role="role", min_count=2, max_count=1, allowed_owner_kinds=(_source(),)
        ),
        lambda: FaultRuleObservationRoleV1(
            role="role", min_count=0, max_count=0, allowed_owner_kinds=()
        ),
        lambda: FaultRuleObservationRoleV1(
            role="role", min_count=0, max_count=1, allowed_owner_kinds=(_source(),) * 2
        ),
        lambda: FaultRuleSourceKindV1(owner="*", record_kind="SUSPENSION_PAIR", schema_id="schema"),
        lambda: FaultRuleV1.model_validate(
            _rule("rule", "v1").model_dump() | {"ordered_observation_roles": ()}
        ),
        lambda: FaultRuleV1.model_validate(
            _rule("rule", "v1").model_dump()
            | {"ordered_observation_roles": (_rule("rule", "v1").ordered_observation_roles[0],) * 2}
        ),
        lambda: FaultRuleV1.model_validate(
            _rule("rule", "v1").model_dump() | {"finding_code": "UNKNOWN"}
        ),
        lambda: FaultRuleV1.model_validate(
            _rule("rule", "v1").model_dump() | {"completeness": "UNKNOWN"}
        ),
        lambda: FaultRuleV1.model_validate(
            _rule("rule", "v1").model_dump() | {"predicate_kind": "UNKNOWN"}
        ),
        lambda: FaultRuleV1.model_validate(
            _rule("rule", "v1").model_dump() | {"schema_id": "wrong"}
        ),
    ),
)
def test_closed_role_and_rule_invariants_reject_invalid_payloads(
    build: Callable[[], object],
) -> None:
    with pytest.raises(ValidationError):
        build()


def test_registry_rejects_unordered_duplicate_or_classifier_drift_rules() -> None:
    first = _rule("a", "v1")
    second = _rule("b", "v1")
    with pytest.raises(ValidationError):
        _registry(second, first)
    with pytest.raises(ValidationError):
        _registry(first, first)
    with pytest.raises(ValidationError):
        _registry(first.model_copy(update={"classifier_id": "other"}))


def test_lookup_rejects_same_id_rival_head_code_or_relation() -> None:
    first = _rule("rule", "v1")
    second = _rule("rule", "v2", relation="canonical-body-v2")
    registry = _registry(first, second)
    with pytest.raises(FaultRuleRegistryIntegrityError):
        lookup_fault_rule(
            registry,
            _head("rule"),
            finding_code="CORRUPT_CANONICAL_BYTES",
            expected_relation="canonical-body",
        )
    with pytest.raises(FaultRuleRegistryIntegrityError):
        lookup_fault_rule(
            registry,
            fault_rule_reference(first),
            finding_code="FINGERPRINT_MISMATCH",
            expected_relation="canonical-body",
        )
    with pytest.raises(FaultRuleRegistryIntegrityError):
        lookup_fault_rule(
            registry,
            fault_rule_reference(first),
            finding_code="CORRUPT_CANONICAL_BYTES",
            expected_relation="other",
        )
