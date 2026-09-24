"""Inert Phase-C declarations for the COMMON operation registry.

This module deliberately declares no mounted owner adapter, writer admission, or
selected-record decoder.  A contract can be C-closed while its inventory entry is
``UNMOUNTED``; Phase I supplies the executable bridge to ``RegisteredPublication``.
"""

from typing import Annotated, Literal, Protocol, get_args

from pydantic import Field, model_validator

from ._ingress_contracts import Digest, Head, Identity, IngressDTO, UInt64
from ._owner_publication_contracts import (
    JournalSelectedPublication,
    Owner,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationRejected,
)
from .runtime_surface_contracts import ExecutableSymbol, RuntimeSurfaceCut, WorkerApplicabilityKind

CardinalityV1 = Literal["ONE", "CONDITIONAL_ONE", "ORDERED_MANY"]
AuthenticationKindV1 = Literal[
    "WORKER",
    "INDEPENDENT_EVIDENCE",
    "BROKER_INGRESS",
    "BROKER_TRANSPORT_OBSERVATION",
    "BROKER_RECOVERY_DIAGNOSTIC",
]
ResultDispositionV1 = Literal["REJECT", "CLASSIFY", "PREPARED", "SELECTED"]
InventoryStateV1 = Literal["C_OPEN", "UNMOUNTED"]


class OperationKeyV1(IngressDTO):
    """A semantic versioned public request key, never a command invocation id."""

    operation_id: Identity
    request_schema_id: Identity
    request_version: Identity
    request_variant: Identity


class WireRefV1(IngressDTO):
    schema_id: Identity
    schema_fingerprint: Digest
    canonicalization_id: Identity
    decoder_symbol: ExecutableSymbol


class RecordRefV1(IngressDTO):
    """One named physical-record role referenced by a result role block."""

    role: Identity
    owner: Owner
    record_kind: Identity
    physical_schema: WireRefV1
    identity_extractor: ExecutableSymbol
    selected_decoder: ExecutableSymbol
    current_cut_validator: ExecutableSymbol
    historical_replay_validator: ExecutableSymbol


class ParticipantRefV1(IngressDTO):
    role: Identity
    owner: Owner
    preparation_key: OperationKeyV1
    request: WireRefV1
    result: WireRefV1
    cardinality: CardinalityV1
    membership_validator: ExecutableSymbol


class RoleBlockV1(IngressDTO):
    """An ordered 0..n role template; ORDERED_MANY is deliberately allowed empty."""

    role: Identity
    cardinality: CardinalityV1


class RepeatedRoleGroupV1(IngressDTO):
    """Interleaved 0..n companion group; every member of one instance shares an ordinal."""

    group_id: Identity
    ordered_roles: tuple[Identity, ...] = Field(min_length=1)
    cardinality: Literal["ORDERED_MANY"]

    @model_validator(mode="after")
    def roles_are_unique(self) -> RepeatedRoleGroupV1:
        if len(set(self.ordered_roles)) != len(self.ordered_roles):
            raise ValueError("repeated group roles must be unique")
        return self


type ResultRecordPatternV1 = RoleBlockV1 | RepeatedRoleGroupV1


class ApplicabilityRefV1(IngressDTO):
    authentication_kind: AuthenticationKindV1
    worker_kind: WorkerApplicabilityKind | None
    proof: WireRefV1
    current_validator: ExecutableSymbol
    permitted_record_roles: tuple[Identity, ...]

    @model_validator(mode="after")
    def worker_kind_matches_authentication(self) -> ApplicabilityRefV1:
        if (self.authentication_kind == "WORKER") != (self.worker_kind is not None):
            raise ValueError(
                "WORKER applicability requires a worker kind and only WORKER may have one"
            )
        if len(set(self.permitted_record_roles)) != len(self.permitted_record_roles):
            raise ValueError("permitted record roles must be unique")
        return self


class ResultBranchV1(IngressDTO):
    result_variant: Identity
    disposition: ResultDispositionV1
    publication_recipe_id: Identity | None
    ordered_record_blocks: tuple[ResultRecordPatternV1, ...]
    owner_result_membership: Literal["NONE", "REQUIRED"]

    @model_validator(mode="after")
    def recipe_matches_durable_shape(self) -> ResultBranchV1:
        if self.disposition in ("REJECT", "CLASSIFY"):
            if self.publication_recipe_id is not None or self.ordered_record_blocks:
                raise ValueError(
                    "rejection and classification branches publish zero durable records"
                )
        elif self.publication_recipe_id is None or not self.ordered_record_blocks:
            raise ValueError("prepared and selected branches require a physical publication recipe")
        if self.disposition in ("REJECT", "CLASSIFY") and self.owner_result_membership != "NONE":
            raise ValueError("zero-record branches cannot require owner-result membership")
        return self


class OperationContractV1(IngressDTO):
    key: OperationKeyV1
    public_port: ExecutableSymbol
    request: WireRefV1
    result: WireRefV1
    owner: Owner
    ordered_participants: tuple[ParticipantRefV1, ...]
    result_branches: tuple[ResultBranchV1, ...]
    record_refs: tuple[RecordRefV1, ...]
    applicability: tuple[ApplicabilityRefV1, ...]
    publication_recipe: ExecutableSymbol | None
    complete_shape_validator: ExecutableSymbol | None
    selected_result_decoder: ExecutableSymbol | None
    replay_identity_policy: ExecutableSymbol | None

    @model_validator(mode="after")
    def exact_named_shape(self) -> OperationContractV1:
        roles = tuple(record.role for record in self.record_refs)
        participant_roles = tuple(participant.role for participant in self.ordered_participants)
        variants = tuple(branch.result_variant for branch in self.result_branches)
        if len(set(roles)) != len(roles):
            raise ValueError("record roles must be unique")
        if len(set(participant_roles)) != len(participant_roles):
            raise ValueError("participant roles must be unique")
        if len(set(variants)) != len(variants):
            raise ValueError("result variants must be unique")
        if not self.result_branches:
            raise ValueError("an operation declares every result branch")
        if self.key.request_schema_id != self.request.schema_id:
            raise ValueError("operation key request schema must equal its request wire schema")
        refs = {record.role: record for record in self.record_refs}
        for branch in self.result_branches:
            block_roles = tuple(
                role for block in branch.ordered_record_blocks for role in _pattern_roles(block)
            )
            if len(set(block_roles)) != len(block_roles):
                raise ValueError("a result branch has each declared record role once")
            group_ids = tuple(
                block.group_id
                for block in branch.ordered_record_blocks
                if isinstance(block, RepeatedRoleGroupV1)
            )
            if len(set(group_ids)) != len(group_ids):
                raise ValueError("repeated group ids must be unique within a result branch")
            for role in block_roles:
                if role not in refs:
                    raise ValueError("result branch refers to an unknown record role")
        known_roles = set(roles)
        for applicability in self.applicability:
            if not set(applicability.permitted_record_roles) <= known_roles:
                raise ValueError("applicability refers to an unknown record role")
        for participant in self.ordered_participants:
            if participant.preparation_key.request_schema_id != participant.request.schema_id:
                raise ValueError(
                    "participant preparation key schema must equal participant request wire"
                )
        if any(branch.ordered_record_blocks for branch in self.result_branches) != (
            self.publication_recipe is not None
        ):
            raise ValueError("publication recipe must exist exactly when a branch has records")
        return self


class COpenOperationInventoryEntryV1(IngressDTO):
    state: Literal["C_OPEN"] = "C_OPEN"
    key: OperationKeyV1
    missing_declarations: tuple[Identity, ...] = Field(min_length=1)


class UnmountedOperationInventoryEntryV1(IngressDTO):
    state: Literal["UNMOUNTED"] = "UNMOUNTED"
    contract: OperationContractV1
    pending_binding: ExecutableSymbol


type OperationInventoryEntryV1 = Annotated[
    COpenOperationInventoryEntryV1 | UnmountedOperationInventoryEntryV1,
    Field(discriminator="state"),
]


class OperationRegistrySnapshotV1(IngressDTO):
    """Finite obligation inventory selected explicitly by the resolver caller."""

    head: Head
    version: Identity
    fingerprint: Digest
    runtime_profile: Head
    source_inventory_fingerprint: Digest
    broker_generation: Identity
    owner_generation: Identity
    applicability_universe: tuple[WorkerApplicabilityKind, ...]
    required_keys: tuple[OperationKeyV1, ...]
    rows: tuple[OperationInventoryEntryV1, ...]

    @model_validator(mode="after")
    def finite_complete_inventory(self) -> OperationRegistrySnapshotV1:
        required = tuple(self.required_keys)
        row_keys = tuple(_entry_key(row) for row in self.rows)
        universe = tuple(get_args(WorkerApplicabilityKind))
        if len(set(required)) != len(required):
            raise ValueError("required operation keys must be unique")
        if len(set(row_keys)) != len(row_keys):
            raise ValueError("inventory operation keys must be unique")
        if set(required) != set(row_keys):
            raise ValueError("finite inventory must contain each required key exactly once")
        if self.applicability_universe != universe:
            raise ValueError("worker applicability universe must be the exact six ordered values")
        return self


class RoleOrdinalV1(IngressDTO):
    role: Identity
    ordinal: UInt64


class RoleBoundCommandV1(IngressDTO):
    role: Identity
    ordinal: UInt64
    request_schema: WireRefV1
    command: OwnerCommandBytes


class RoleBoundRecordV1(IngressDTO):
    role: Identity
    ordinal: UInt64
    physical_schema: WireRefV1
    record: OwnerRecordBytes


class OwnerResultFingerprintV1(IngressDTO):
    participant_role: Identity
    ordinal: UInt64
    fingerprint: Digest


class RecipeExpansionV1(IngressDTO):
    """Untrusted C structural input; I recomputes it from authenticated sources."""

    key: OperationKeyV1
    result_variant: Identity
    request_fingerprint: Digest
    ordered_owner_result_fingerprints: tuple[OwnerResultFingerprintV1, ...]
    ordered_command_roles: tuple[RoleOrdinalV1, ...]
    ordered_record_roles: tuple[RoleOrdinalV1, ...]
    expanded_commands: tuple[RoleBoundCommandV1, ...]
    expanded_records: tuple[RoleBoundRecordV1, ...]


class PreparedOperationV1(IngressDTO):
    """Unpublishable transport envelope; Phase I maps it to a registered batch."""

    key: OperationKeyV1
    result_variant: Identity
    request_fingerprint: Digest
    expansion: RecipeExpansionV1
    ordered_source_commands: tuple[RoleBoundCommandV1, ...]
    complete_records: tuple[RoleBoundRecordV1, ...]


class OperationResolutionFailureV1(IngressDTO):
    kind: Literal["UNKNOWN_KEY", "UNKNOWN_VERSION", "STALE_CUT"]
    key: OperationKeyV1


class OperationMountStatusV1(IngressDTO):
    kind: Literal["UNMOUNTED"] = "UNMOUNTED"
    key: OperationKeyV1
    pending_binding: ExecutableSymbol


class ShapeValidationFailureV1(IngressDTO):
    kind: Literal["UNKNOWN_RESULT_VARIANT", "COMMAND_SHAPE", "RECORD_SHAPE"]
    key: OperationKeyV1
    detail: Identity


class DecodedSelectedMemberV1(IngressDTO):
    role: Identity
    schema_id: Identity
    canonical_bytes: bytes = Field(min_length=1)
    fingerprint: Digest


class DecodedSelectedOperationV1(IngressDTO):
    key: OperationKeyV1
    selected: JournalSelectedPublication
    ordered_members: tuple[DecodedSelectedMemberV1, ...]


class SelectedDecodeFailureV1(IngressDTO):
    kind: Literal["INTEGRITY_FAILURE", "UNKNOWN_SCHEMA", "SHAPE_MISMATCH"]
    key: OperationKeyV1
    detail: Identity


type SelectedDecodeOutcomeV1 = DecodedSelectedOperationV1 | SelectedDecodeFailureV1
type OperationResolutionOutcomeV1 = OperationContractV1 | OperationResolutionFailureV1


class RegisteredPreparationAdapterV1(Protocol):
    """Phase-I adapter interface; its output is not a publication authority."""

    async def prepare(
        self, request_bytes: bytes, cut: RuntimeSurfaceCut
    ) -> PreparedOperationV1 | PublicationRejected: ...


class RegisteredSelectionAdapterV1(Protocol):
    def decode_selected(self, selected: JournalSelectedPublication) -> SelectedDecodeOutcomeV1: ...


class CompleteShapeValidatorV1(Protocol):
    """Pure declaration check; it does not decode bytes or call an owner."""

    def validate(
        self, contract: OperationContractV1, prepared: PreparedOperationV1
    ) -> ShapeValidationFailureV1 | None: ...


class OperationRegistryV1(Protocol):
    def resolve_contract(
        self, key: OperationKeyV1, cut: RuntimeSurfaceCut, selected: OperationRegistrySnapshotV1
    ) -> OperationResolutionOutcomeV1: ...

    def mount_status(
        self, key: OperationKeyV1, cut: RuntimeSurfaceCut, selected: OperationRegistrySnapshotV1
    ) -> OperationMountStatusV1 | OperationResolutionFailureV1: ...

    def snapshot(self) -> OperationRegistrySnapshotV1: ...


class RuntimeSurfaceObservationInputV1(IngressDTO):
    """Mounted graph roots for independent observation; never registry enumeration seeds."""

    driver_and_transport_entrypoints: tuple[ExecutableSymbol, ...]
    broker_routing_mounts: tuple[ExecutableSymbol, ...]
    owner_dispatch_branches: tuple[ExecutableSymbol, ...]
    publication_and_guard_sites: tuple[ExecutableSymbol, ...]
    schema_and_readback_sites: tuple[ExecutableSymbol, ...]
    consume_issue_emit_edges: tuple[ExecutableSymbol, ...]


def _entry_key(entry: OperationInventoryEntryV1) -> OperationKeyV1:
    if isinstance(entry, COpenOperationInventoryEntryV1):
        return entry.key
    return entry.contract.key


def resolve_operation(
    selected: OperationRegistrySnapshotV1, key: OperationKeyV1, cut: RuntimeSurfaceCut
) -> OperationResolutionOutcomeV1:
    """Resolve only within the caller-selected finite snapshot; never pick latest."""

    if (
        selected.runtime_profile != cut.runtime_profile
        or selected.source_inventory_fingerprint != cut.source_inventory_fingerprint
        or selected.broker_generation != cut.broker_generation
        or selected.owner_generation != cut.runtime_generation
    ):
        return OperationResolutionFailureV1(kind="STALE_CUT", key=key)
    same_operation = any(_entry_key(row).operation_id == key.operation_id for row in selected.rows)
    for row in selected.rows:
        if _entry_key(row) != key:
            continue
        if isinstance(row, UnmountedOperationInventoryEntryV1):
            return row.contract
        return OperationResolutionFailureV1(kind="UNKNOWN_VERSION", key=key)
    return OperationResolutionFailureV1(
        kind="UNKNOWN_VERSION" if same_operation else "UNKNOWN_KEY", key=key
    )


def resolve_mount_status(
    selected: OperationRegistrySnapshotV1, key: OperationKeyV1, cut: RuntimeSurfaceCut
) -> OperationMountStatusV1 | OperationResolutionFailureV1:
    """Report a separate I-state; descriptor lookup itself is not a mount attempt."""

    contract = resolve_operation(selected, key, cut)
    if isinstance(contract, OperationResolutionFailureV1):
        return contract
    for row in selected.rows:
        if isinstance(row, UnmountedOperationInventoryEntryV1) and row.contract.key == key:
            return OperationMountStatusV1(key=key, pending_binding=row.pending_binding)
    raise AssertionError("a resolved declaration must have a finite inventory entry")


def validate_prepared_shape(
    contract: OperationContractV1, prepared: PreparedOperationV1
) -> ShapeValidationFailureV1 | None:
    """Check the declared role/schema/order recipe without interpreting payload bytes."""

    if prepared.key != contract.key:
        return ShapeValidationFailureV1(
            kind="COMMAND_SHAPE", key=prepared.key, detail="prepared operation key differs"
        )
    if (
        prepared.ordered_source_commands != prepared.expansion.expanded_commands
        or prepared.complete_records != prepared.expansion.expanded_records
    ):
        return ShapeValidationFailureV1(
            kind="RECORD_SHAPE",
            key=prepared.key,
            detail="prepared physical members differ from expansion",
        )
    source_roles = tuple(
        RoleOrdinalV1(role=value.participant_role, ordinal=value.ordinal)
        for value in prepared.expansion.ordered_owner_result_fingerprints
    )
    if prepared.expansion.ordered_command_roles != source_roles:
        return ShapeValidationFailureV1(
            kind="COMMAND_SHAPE",
            key=prepared.key,
            detail="owner-result fingerprint roles differ from command roles",
        )
    branch = next(
        (
            candidate
            for candidate in contract.result_branches
            if candidate.result_variant == prepared.result_variant
        ),
        None,
    )
    if branch is None:
        return ShapeValidationFailureV1(
            kind="UNKNOWN_RESULT_VARIANT", key=prepared.key, detail="undeclared result variant"
        )
    if (
        prepared.expansion.key != prepared.key
        or prepared.expansion.result_variant != prepared.result_variant
        or prepared.expansion.request_fingerprint != prepared.request_fingerprint
    ):
        return ShapeValidationFailureV1(
            kind="COMMAND_SHAPE",
            key=prepared.key,
            detail="expansion does not bind prepared request",
        )
    command_failure = _validate_commands(contract, prepared)
    if command_failure is not None:
        return command_failure
    record_failure = _validate_records(contract, branch, prepared)
    if record_failure is not None:
        return record_failure
    if (
        not prepared.expansion.ordered_owner_result_fingerprints
        and branch.owner_result_membership == "REQUIRED"
    ):
        return ShapeValidationFailureV1(
            kind="COMMAND_SHAPE",
            key=prepared.key,
            detail="owner-result membership fingerprints missing",
        )
    source_failure = _validate_role_sequence(
        source_roles,
        tuple(
            RoleBlockV1(role=item.role, cardinality=item.cardinality)
            for item in contract.ordered_participants
        ),
        "owner-result fingerprint",
        prepared.key,
        "COMMAND_SHAPE",
    )
    if source_failure is not None:
        return source_failure
    return None


def _validate_commands(
    contract: OperationContractV1, prepared: PreparedOperationV1
) -> ShapeValidationFailureV1 | None:
    expected_roles = tuple(
        RoleOrdinalV1(role=item.role, ordinal=item.ordinal)
        for item in prepared.ordered_source_commands
    )
    if expected_roles != prepared.expansion.ordered_command_roles:
        return ShapeValidationFailureV1(
            kind="COMMAND_SHAPE", key=prepared.key, detail="command tags differ from expansion"
        )
    template = tuple(
        RoleBlockV1(role=item.role, cardinality=item.cardinality)
        for item in contract.ordered_participants
    )
    failure = _validate_role_sequence(
        expected_roles, template, "command", prepared.key, "COMMAND_SHAPE"
    )
    if failure is not None:
        return failure
    participants = {item.role: item for item in contract.ordered_participants}
    for tagged in prepared.ordered_source_commands:
        participant = participants[tagged.role]
        if (
            tagged.request_schema != participant.request
            or tagged.command.owner != participant.owner
            or tagged.command.schema_id != participant.request.schema_id
        ):
            return ShapeValidationFailureV1(
                kind="COMMAND_SHAPE", key=prepared.key, detail="command physical reference differs"
            )
    return None


def _validate_records(
    contract: OperationContractV1, branch: ResultBranchV1, prepared: PreparedOperationV1
) -> ShapeValidationFailureV1 | None:
    tags = tuple(
        RoleOrdinalV1(role=item.role, ordinal=item.ordinal) for item in prepared.complete_records
    )
    if tags != prepared.expansion.ordered_record_roles:
        return ShapeValidationFailureV1(
            kind="RECORD_SHAPE", key=prepared.key, detail="record tags differ from expansion"
        )
    failure = _validate_record_pattern_sequence(tags, branch.ordered_record_blocks, prepared.key)
    if failure is not None:
        return failure
    records = {item.role: item for item in contract.record_refs}
    for tagged in prepared.complete_records:
        expected = records[tagged.role]
        actual = tagged.record
        if (
            tagged.physical_schema != expected.physical_schema
            or actual.owner != expected.owner
            or actual.record_kind != expected.record_kind
            or actual.schema_id != expected.physical_schema.schema_id
        ):
            return ShapeValidationFailureV1(
                kind="RECORD_SHAPE", key=prepared.key, detail="record physical reference differs"
            )
    return None


def _pattern_roles(pattern: ResultRecordPatternV1) -> tuple[Identity, ...]:
    if isinstance(pattern, RoleBlockV1):
        return (pattern.role,)
    return pattern.ordered_roles


def _validate_record_pattern_sequence(
    values: tuple[RoleOrdinalV1, ...],
    patterns: tuple[ResultRecordPatternV1, ...],
    key: OperationKeyV1,
) -> ShapeValidationFailureV1 | None:
    position = 0
    for pattern in patterns:
        if isinstance(pattern, RoleBlockV1):
            count = 0
            while position < len(values) and values[position].role == pattern.role:
                if values[position].ordinal != count:
                    return ShapeValidationFailureV1(
                        kind="RECORD_SHAPE", key=key, detail="record ordinal differs"
                    )
                position += 1
                count += 1
            if pattern.cardinality == "ONE" and count != 1:
                return ShapeValidationFailureV1(
                    kind="RECORD_SHAPE", key=key, detail="record ONE role count differs"
                )
            if pattern.cardinality == "CONDITIONAL_ONE" and count > 1:
                return ShapeValidationFailureV1(
                    kind="RECORD_SHAPE", key=key, detail="record optional role repeats"
                )
            continue
        ordinal = 0
        while position < len(values) and values[position].role == pattern.ordered_roles[0]:
            for role in pattern.ordered_roles:
                if position >= len(values) or values[position] != RoleOrdinalV1(
                    role=role, ordinal=ordinal
                ):
                    return ShapeValidationFailureV1(
                        kind="RECORD_SHAPE", key=key, detail="repeated group order differs"
                    )
                position += 1
            ordinal += 1
    if position != len(values):
        return ShapeValidationFailureV1(
            kind="RECORD_SHAPE", key=key, detail="record surplus or unordered role"
        )
    return None


def _validate_role_sequence(
    values: tuple[RoleOrdinalV1, ...],
    template: tuple[RoleBlockV1, ...],
    label: str,
    key: OperationKeyV1,
    kind: Literal["COMMAND_SHAPE", "RECORD_SHAPE"],
) -> ShapeValidationFailureV1 | None:
    position = 0
    for block in template:
        count = 0
        while position < len(values) and values[position].role == block.role:
            if values[position].ordinal != count:
                return ShapeValidationFailureV1(
                    kind=kind, key=key, detail=label + " ordinal differs"
                )
            position += 1
            count += 1
        if block.cardinality == "ONE" and count != 1:
            return ShapeValidationFailureV1(
                kind=kind, key=key, detail=label + " ONE role count differs"
            )
        if block.cardinality == "CONDITIONAL_ONE" and count > 1:
            return ShapeValidationFailureV1(
                kind=kind, key=key, detail=label + " optional role repeats"
            )
    if position != len(values):
        return ShapeValidationFailureV1(
            kind=kind, key=key, detail=label + " surplus or unordered role"
        )
    return None


__all__ = [
    "ApplicabilityRefV1",
    "COpenOperationInventoryEntryV1",
    "CompleteShapeValidatorV1",
    "DecodedSelectedMemberV1",
    "DecodedSelectedOperationV1",
    "OperationContractV1",
    "OperationInventoryEntryV1",
    "OperationKeyV1",
    "OperationMountStatusV1",
    "OperationRegistrySnapshotV1",
    "OperationRegistryV1",
    "OperationResolutionFailureV1",
    "OwnerResultFingerprintV1",
    "ParticipantRefV1",
    "PreparedOperationV1",
    "RecipeExpansionV1",
    "RecordRefV1",
    "RegisteredPreparationAdapterV1",
    "RegisteredSelectionAdapterV1",
    "RepeatedRoleGroupV1",
    "ResultBranchV1",
    "ResultRecordPatternV1",
    "RoleBlockV1",
    "RoleBoundCommandV1",
    "RoleBoundRecordV1",
    "RoleOrdinalV1",
    "RuntimeSurfaceObservationInputV1",
    "SelectedDecodeFailureV1",
    "SelectedDecodeOutcomeV1",
    "ShapeValidationFailureV1",
    "UnmountedOperationInventoryEntryV1",
    "WireRefV1",
    "resolve_mount_status",
    "resolve_operation",
    "validate_prepared_shape",
]
