"""Owner-local exact wire registry for declared terminal-recovery fault rules.

This module binds retained rule bytes and exact registered members.  It deliberately
does not run a verifier, classify a fault, or authenticate broker capture data.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from .call_acceptance_contracts import CallSubjectHead
from .recovery_contracts import Digest, Identity, Present, RecoveryDTO, UInt64

__all__ = [
    "FaultFindingCodeV1",
    "FaultRuleObservationRoleV1",
    "FaultRuleRegistryIntegrityError",
    "FaultRuleRegistryV1",
    "FaultRuleSourceKindV1",
    "FaultRuleV1",
    "SelectedFaultRuleRegistryV1",
    "decode_fault_rule_registry",
    "fault_rule_reference",
    "lookup_fault_rule",
]

type FaultFindingCodeV1 = Literal[
    "CORRUPT_CANONICAL_BYTES",
    "FINGERPRINT_MISMATCH",
    "IMPOSSIBLE_REGISTERED_TRANSITION",
    "RIVAL_TERMINAL",
    "BROKEN_IMMUTABLE_ANCESTRY",
    "REGISTERED_INVARIANT_VIOLATION",
]

_RULE_SCHEMA: Literal["chiplog.execution.recovery-fault-rule.v1"] = (
    "chiplog.execution.recovery-fault-rule.v1"
)
_REGISTRY_SCHEMA: Literal["chiplog.execution.recovery-fault-rule-registry.v1"] = (
    "chiplog.execution.recovery-fault-rule-registry.v1"
)


class FaultRuleRegistryIntegrityError(ValueError):
    """A retained registry or exact registered rule member does not bind."""

    def __init__(self, operation: str, supplied_identity: str, reason: str) -> None:
        super().__init__(f"{operation}: {supplied_identity}: {reason}")


class FaultRuleSourceKindV1(RecoveryDTO):
    owner: Identity
    record_kind: Identity
    schema_id: Identity

    @model_validator(mode="after")
    def exact_registered_source_class(self) -> FaultRuleSourceKindV1:
        if "*" in (self.owner, self.record_kind, self.schema_id):
            raise ValueError("fault rule source class cannot use a wildcard selector")
        return self


class FaultRuleObservationRoleV1(RecoveryDTO):
    role: Identity
    min_count: UInt64
    max_count: UInt64
    allowed_owner_kinds: tuple[FaultRuleSourceKindV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_counts_and_source_kinds(self) -> FaultRuleObservationRoleV1:
        if self.max_count < self.min_count:
            raise ValueError("fault rule role maximum count is below minimum count")
        triples = tuple(
            (source.owner, source.record_kind, source.schema_id)
            for source in self.allowed_owner_kinds
        )
        if len(set(triples)) != len(triples):
            raise ValueError("fault rule role repeats an allowed owner/kind/schema triple")
        return self


class FaultRuleV1(RecoveryDTO):
    schema_id: Literal["chiplog.execution.recovery-fault-rule.v1"] = _RULE_SCHEMA
    rule_id: Identity
    version: Identity
    classifier_id: Identity
    classifier_version: Identity
    finding_code: FaultFindingCodeV1
    expected_relation: Identity
    predicate_kind: Literal[
        "CANONICAL_BODY",
        "CLAIMED_FINGERPRINT",
        "REGISTERED_TRANSITION",
        "TERMINAL_UNIQUENESS",
        "IMMUTABLE_ANCESTRY",
        "REGISTERED_OWNER_INVARIANT",
    ]
    verifier_id: Identity
    verifier_version: Identity
    ordered_observation_roles: tuple[FaultRuleObservationRoleV1, ...] = Field(min_length=1)
    completeness: Literal["POSITIVE_WITNESS_SUFFICIENT", "COMPLETE_REGISTERED_INVENTORY_REQUIRED"]
    witness_contract_schema: Identity
    witness_contract_fingerprint: Digest

    @model_validator(mode="after")
    def unique_role_names(self) -> FaultRuleV1:
        names = tuple(role.role for role in self.ordered_observation_roles)
        if len(set(names)) != len(names):
            raise ValueError("fault rule repeats an observation role")
        return self


class FaultRuleRegistryV1(RecoveryDTO):
    schema_id: Literal["chiplog.execution.recovery-fault-rule-registry.v1"] = _REGISTRY_SCHEMA
    registry_id: Identity
    version: Identity
    classifier_id: Identity
    classifier_version: Identity
    disposition_version: Identity
    ordered_rules: tuple[FaultRuleV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_unique_consistent_rules(self) -> FaultRuleRegistryV1:
        identities = tuple((rule.rule_id, rule.version) for rule in self.ordered_rules)
        if identities != tuple(sorted(identities)) or len(set(identities)) != len(identities):
            raise ValueError("fault rule registry rules must be uniquely lexically ordered")
        if any(
            rule.classifier_id != self.classifier_id
            or rule.classifier_version != self.classifier_version
            for rule in self.ordered_rules
        ):
            raise ValueError("fault rule classifier differs from registry classifier")
        return self


class SelectedFaultRuleRegistryV1(RecoveryDTO):
    """Immutable carrier retaining the exact selected registry representation."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    registry: FaultRuleRegistryV1
    registry_reference: CallSubjectHead
    canonical_registry_bytes: bytes = Field(min_length=1)
    selected_decision: CallSubjectHead


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _registry_reference(registry: FaultRuleRegistryV1) -> CallSubjectHead:
    digest = _sha256(registry.canonical_bytes())
    return CallSubjectHead(
        subject_id=registry.registry_id,
        revision=Present(head=registry.schema_id + ":" + digest, fingerprint=digest),
    )


def _registry_identity(selected: SelectedFaultRuleRegistryV1) -> str:
    return selected.registry_reference.subject_id


def decode_fault_rule_registry(
    selected: SelectedFaultRuleRegistryV1,
    *,
    expected_registry: CallSubjectHead,
    expected_selected_decision: CallSubjectHead,
    expected_classifier_id: str,
    expected_classifier_version: str,
    expected_disposition_version: str,
) -> FaultRuleRegistryV1:
    """Decode only an exact selected registry with its independent selection binding."""

    operation = "decode_fault_rule_registry"
    identity = _registry_identity(selected)
    try:
        decoded = FaultRuleRegistryV1.model_validate_json(selected.canonical_registry_bytes)
    except ValidationError as error:
        raise FaultRuleRegistryIntegrityError(
            operation, identity, "invalid registry bytes"
        ) from error
    if decoded.canonical_bytes() != selected.canonical_registry_bytes:
        raise FaultRuleRegistryIntegrityError(
            operation, identity, "registry bytes are not canonical"
        )
    if decoded != selected.registry:
        raise FaultRuleRegistryIntegrityError(
            operation, identity, "decoded registry differs from carrier"
        )
    derived_reference = _registry_reference(decoded)
    if selected.registry_reference != derived_reference:
        raise FaultRuleRegistryIntegrityError(
            operation, identity, "registry reference differs from bytes"
        )
    if expected_registry != derived_reference:
        raise FaultRuleRegistryIntegrityError(
            operation, identity, "expected registry reference differs"
        )
    if selected.selected_decision != expected_selected_decision:
        raise FaultRuleRegistryIntegrityError(operation, identity, "selected decision differs")
    if decoded.classifier_id != expected_classifier_id:
        raise FaultRuleRegistryIntegrityError(operation, identity, "classifier ID differs")
    if decoded.classifier_version != expected_classifier_version:
        raise FaultRuleRegistryIntegrityError(operation, identity, "classifier version differs")
    if decoded.disposition_version != expected_disposition_version:
        raise FaultRuleRegistryIntegrityError(operation, identity, "disposition version differs")
    return decoded


def fault_rule_reference(rule: FaultRuleV1) -> CallSubjectHead:
    """Return the exact content-addressed head of one selected rule member."""

    digest = _sha256(rule.canonical_bytes())
    return CallSubjectHead(
        subject_id=rule.rule_id,
        revision=Present(head=rule.schema_id + ":" + digest, fingerprint=digest),
    )


def lookup_fault_rule(
    registry: FaultRuleRegistryV1,
    invariant: CallSubjectHead,
    *,
    finding_code: FaultFindingCodeV1,
    expected_relation: str,
) -> FaultRuleV1:
    """Select exactly one registered rule without latest-version fallback."""

    operation = "lookup_fault_rule"
    identity = invariant.subject_id
    exact_matches = [
        rule for rule in registry.ordered_rules if fault_rule_reference(rule) == invariant
    ]
    if len(exact_matches) != 1:
        raise FaultRuleRegistryIntegrityError(
            operation, identity, "no exact registered rule reference"
        )
    rule = exact_matches[0]
    if rule.finding_code != finding_code:
        raise FaultRuleRegistryIntegrityError(operation, identity, "finding code differs from rule")
    if rule.expected_relation != expected_relation:
        raise FaultRuleRegistryIntegrityError(
            operation, identity, "expected relation differs from rule"
        )
    return rule
