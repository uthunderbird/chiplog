"""Phase-C consumers for inert operation-registry declarations."""

from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.platform import operation_registry_contracts as registry
from chiplog.platform import runtime_surface_contracts as surface
from chiplog.platform._ingress_contracts import Head
from chiplog.platform._owner_publication_contracts import Owner, OwnerCommandBytes, OwnerRecordBytes


def symbol(name: str) -> surface.ExecutableSymbol:
    return surface.ExecutableSymbol(
        module="fixture", qualified_name=name, source_path="fixture.py", source_fingerprint="a" * 64
    )


def wire(name: str) -> registry.WireRefV1:
    return registry.WireRefV1(
        schema_id=name,
        schema_fingerprint="c" * 64,
        canonicalization_id="canon.v1",
        decoder_symbol=symbol(name),
    )


def key(name: str) -> registry.OperationKeyV1:
    return registry.OperationKeyV1(
        operation_id=name,
        request_schema_id=name + ".request.v1",
        request_version="v1",
        request_variant="SINGLE",
    )


def contract(
    name: str, *, owner: Owner = "agent_loop", worker: bool = True
) -> registry.OperationContractV1:
    operation_key = key(name)
    participant = registry.ParticipantRefV1(
        role="primary",
        owner=owner,
        preparation_key=operation_key,
        request=wire(operation_key.request_schema_id),
        result=wire(name + ".owner-result.v1"),
        cardinality="ONE",
        membership_validator=symbol("member"),
    )
    ref = registry.RecordRefV1(
        role="result",
        owner=owner,
        record_kind="result",
        physical_schema=wire(name + ".record.v1"),
        identity_extractor=symbol("identity"),
        selected_decoder=symbol("selected"),
        current_cut_validator=symbol("current"),
        historical_replay_validator=symbol("history"),
    )
    return registry.OperationContractV1(
        key=operation_key,
        public_port=symbol("public"),
        request=wire(operation_key.request_schema_id),
        result=wire(name + ".result.v1"),
        owner=owner,
        ordered_participants=(participant,),
        result_branches=(
            registry.ResultBranchV1(
                result_variant="ACCEPTED",
                disposition="SELECTED",
                publication_recipe_id="recipe",
                ordered_record_blocks=(registry.RoleBlockV1(role="result", cardinality="ONE"),),
                owner_result_membership="REQUIRED",
            ),
            registry.ResultBranchV1(
                result_variant="REJECTED",
                disposition="REJECT",
                publication_recipe_id=None,
                ordered_record_blocks=(),
                owner_result_membership="NONE",
            ),
        ),
        record_refs=(ref,),
        applicability=(
            registry.ApplicabilityRefV1(
                authentication_kind="WORKER" if worker else "INDEPENDENT_EVIDENCE",
                worker_kind="NON_SCHEDULER_NOT_APPLICABLE" if worker else None,
                proof=wire(name + ".proof.v1"),
                current_validator=symbol("fence"),
                permitted_record_roles=("result",),
            ),
        ),
        publication_recipe=symbol("recipe"),
        complete_shape_validator=symbol("shape"),
        selected_result_decoder=symbol("decode-result"),
        replay_identity_policy=symbol("replay"),
    )


def cut() -> surface.RuntimeSurfaceCut:
    return surface.RuntimeSurfaceCut(
        runtime_profile=Head(identity="profile", head="profile:head", fingerprint="b" * 64),
        broker_generation="broker",
        runtime_generation="owner",
        source_inventory_fingerprint="d" * 64,
        worker_registry=Head(identity="worker", head="worker:head", fingerprint="b" * 64),
        ingress_manifest=Head(identity="ingress", head="ingress:head", fingerprint="b" * 64),
    )


def tagged_command(
    role: str, ordinal: int, participant: registry.ParticipantRefV1
) -> registry.RoleBoundCommandV1:
    return registry.RoleBoundCommandV1(
        role=role,
        ordinal=ordinal,
        request_schema=participant.request,
        command=OwnerCommandBytes(
            owner=participant.owner,
            schema_id=participant.request.schema_id,
            canonical_bytes=b"command",
            fingerprint="1" * 64,
        ),
    )


def tagged_record(role: str, ordinal: int, ref: registry.RecordRefV1) -> registry.RoleBoundRecordV1:
    return registry.RoleBoundRecordV1(
        role=role,
        ordinal=ordinal,
        physical_schema=ref.physical_schema,
        record=OwnerRecordBytes(
            owner=ref.owner,
            record_kind=ref.record_kind,
            record_id=role + str(ordinal),
            schema_id=ref.physical_schema.schema_id,
            canonical_bytes=b"record",
            fingerprint="2" * 64,
        ),
    )


def prepared(
    value: registry.OperationContractV1,
    commands: tuple[registry.RoleBoundCommandV1, ...] | None = None,
    records: tuple[registry.RoleBoundRecordV1, ...] | None = None,
) -> registry.PreparedOperationV1:
    commands = (
        commands
        if commands is not None
        else (tagged_command("primary", 0, value.ordered_participants[0]),)
    )
    records = (
        records if records is not None else (tagged_record("result", 0, value.record_refs[0]),)
    )
    expansion = registry.RecipeExpansionV1(
        key=value.key,
        result_variant="ACCEPTED",
        request_fingerprint="f" * 64,
        ordered_owner_result_fingerprints=tuple(
            registry.OwnerResultFingerprintV1(
                participant_role=item.role, ordinal=item.ordinal, fingerprint="3" * 64
            )
            for item in commands
        ),
        ordered_command_roles=tuple(
            registry.RoleOrdinalV1(role=x.role, ordinal=x.ordinal) for x in commands
        ),
        ordered_record_roles=tuple(
            registry.RoleOrdinalV1(role=x.role, ordinal=x.ordinal) for x in records
        ),
        expanded_commands=commands,
        expanded_records=records,
    )
    return registry.PreparedOperationV1(
        key=value.key,
        result_variant="ACCEPTED",
        request_fingerprint="f" * 64,
        expansion=expansion,
        ordered_source_commands=commands,
        complete_records=records,
    )


def failure(value: registry.ShapeValidationFailureV1 | None) -> str:
    assert value is not None
    return value.kind


def test_representative_ingress_completion_readonly_and_late_evidence_descriptors_roundtrip() -> (
    None
):
    for value in (
        contract("ingress.publish_custody_successor", owner="broker_ingress", worker=False),
        contract("agent_loop.complete_acceptance.v2"),
        contract("recovery.readonly_outcome"),
        contract("recovery.retain_late_execution_response", worker=False),
    ):
        assert registry.OperationContractV1.model_validate_json(value.model_dump_json()) == value
        assert registry.validate_prepared_shape(value, prepared(value)) is None


def test_lookup_returns_unmounted_descriptor_and_mount_status_is_separate() -> None:
    value = contract("ingress.publish_custody_successor", owner="broker_ingress", worker=False)
    row = registry.UnmountedOperationInventoryEntryV1(
        contract=value, pending_binding=symbol("bind")
    )
    selected = registry.OperationRegistrySnapshotV1(
        head=Head(identity="registry", head="registry:head", fingerprint="b" * 64),
        version="v1",
        fingerprint="e" * 64,
        runtime_profile=cut().runtime_profile,
        source_inventory_fingerprint=cut().source_inventory_fingerprint,
        broker_generation="broker",
        owner_generation="owner",
        applicability_universe=tuple(get_args(surface.WorkerApplicabilityKind)),
        required_keys=(value.key,),
        rows=(row,),
    )
    adapter: TypeAdapter[registry.OperationInventoryEntryV1] = TypeAdapter(
        registry.OperationInventoryEntryV1
    )
    assert adapter.validate_json(adapter.dump_json(row)) == row
    assert registry.resolve_operation(selected, value.key, cut()) == value
    assert isinstance(
        registry.resolve_mount_status(selected, value.key, cut()), registry.OperationMountStatusV1
    )


def test_worker_fences_and_public_selected_union_roundtrip() -> None:
    for fence in get_args(surface.WorkerApplicabilityKind):
        assert (
            registry.ApplicabilityRefV1(
                authentication_kind="WORKER",
                worker_kind=fence,
                proof=wire(fence),
                current_validator=symbol(fence),
                permitted_record_roles=(),
            ).worker_kind
            == fence
        )
    with pytest.raises(ValidationError, match="only WORKER"):
        registry.ApplicabilityRefV1(
            authentication_kind="BROKER_INGRESS",
            worker_kind="WORK_EPOCH_ROLLOVER",
            proof=wire("bad"),
            current_validator=symbol("bad"),
            permitted_record_roles=(),
        )
    adapter: TypeAdapter[registry.SelectedDecodeOutcomeV1] = TypeAdapter(
        registry.SelectedDecodeOutcomeV1
    )
    outcome = registry.SelectedDecodeFailureV1(kind="UNKNOWN_SCHEMA", key=key("x"), detail="inert")
    assert adapter.validate_json(adapter.dump_json(outcome)) == outcome


def test_role_tags_disambiguate_adjacent_identical_owner_schema_and_reject_swap() -> None:
    base = contract("agent_loop.complete_acceptance.v2")
    second = base.ordered_participants[0].model_copy(update={"role": "second"})
    second_ref = base.record_refs[0].model_copy(update={"role": "second-record"})
    data = base.model_dump()
    data["ordered_participants"] = (*data["ordered_participants"], second.model_dump())
    data["record_refs"] = (*data["record_refs"], second_ref.model_dump())
    data["result_branches"][0]["ordered_record_blocks"] = (
        {"role": "result", "cardinality": "ONE"},
        {"role": "second-record", "cardinality": "ONE"},
    )
    value = registry.OperationContractV1.model_validate(data)
    commands = (
        tagged_command("primary", 0, value.ordered_participants[0]),
        tagged_command("second", 0, value.ordered_participants[1]),
    )
    records = (
        tagged_record("result", 0, value.record_refs[0]),
        tagged_record("second-record", 0, value.record_refs[1]),
    )
    good = prepared(value, commands, records)
    assert registry.validate_prepared_shape(value, good) is None
    assert (
        failure(
            registry.validate_prepared_shape(
                value,
                good.model_copy(update={"ordered_source_commands": tuple(reversed(commands))}),
            )
        )
        == "RECORD_SHAPE"
    )


@pytest.mark.parametrize("count", [0, 1, 2])
def test_ordered_many_is_zero_to_n_for_command_and_record_blocks(count: int) -> None:
    base = contract("effects.authorize")
    participant = base.ordered_participants[0].model_copy(
        update={"role": "effect", "cardinality": "ORDERED_MANY"}
    )
    ref = base.record_refs[0].model_copy(update={"role": "effect-record"})
    data = base.model_dump()
    data["ordered_participants"] = (participant.model_dump(),)
    data["record_refs"] = (ref.model_dump(),)
    data["applicability"][0]["permitted_record_roles"] = ("effect-record",)
    data["result_branches"][0]["ordered_record_blocks"] = (
        {"role": "effect-record", "cardinality": "ORDERED_MANY"},
    )
    if count == 0:
        data["result_branches"][0]["owner_result_membership"] = "NONE"
    value = registry.OperationContractV1.model_validate(data)
    commands = tuple(
        tagged_command("effect", n, value.ordered_participants[0]) for n in range(count)
    )
    records = tuple(tagged_record("effect-record", n, value.record_refs[0]) for n in range(count))
    assert registry.validate_prepared_shape(value, prepared(value, commands, records)) is None


def test_expansion_rejects_physical_and_schema_substitution() -> None:
    value = contract("recovery.readonly_outcome")
    good = prepared(value)
    altered_record = good.complete_records[0].model_copy(
        update={"record": good.complete_records[0].record.model_copy(update={"record_id": "other"})}
    )
    assert (
        failure(
            registry.validate_prepared_shape(
                value, good.model_copy(update={"complete_records": (altered_record,)})
            )
        )
        == "RECORD_SHAPE"
    )
    altered_schema = good.ordered_source_commands[0].model_copy(
        update={
            "request_schema": good.ordered_source_commands[0].request_schema.model_copy(
                update={"schema_fingerprint": "9" * 64}
            )
        }
    )
    altered_expansion = good.expansion.model_copy(update={"expanded_commands": (altered_schema,)})
    assert (
        failure(
            registry.validate_prepared_shape(
                value,
                good.model_copy(
                    update={
                        "expansion": altered_expansion,
                        "ordered_source_commands": (altered_schema,),
                    }
                ),
            )
        )
        == "COMMAND_SHAPE"
    )


@pytest.mark.parametrize("count", [0, 1, 2])
def test_work_companion_group_is_interleaved_and_contiguous(count: int) -> None:
    base = contract("recovery.work_genesis")
    roles = ("SUBJECT", "EPOCH", "SELECTOR", "LEASE")
    refs = tuple(base.record_refs[0].model_copy(update={"role": role}) for role in roles)
    data = base.model_dump()
    data["record_refs"] = tuple(ref.model_dump() for ref in refs)
    data["applicability"][0]["permitted_record_roles"] = roles
    data["result_branches"][0]["ordered_record_blocks"] = (
        {"group_id": "work-genesis", "ordered_roles": roles, "cardinality": "ORDERED_MANY"},
    )
    value = registry.OperationContractV1.model_validate(data)
    records = tuple(
        tagged_record(role, ordinal, value.record_refs[index])
        for ordinal in range(count)
        for index, role in enumerate(roles)
    )
    good = prepared(value, records=records)
    assert registry.validate_prepared_shape(value, good) is None
    if count == 2:
        flattened = tuple(
            tagged_record(role, ordinal, value.record_refs[index])
            for index, role in enumerate(roles)
            for ordinal in range(count)
        )
        expansion = good.expansion.model_copy(
            update={
                "ordered_record_roles": tuple(
                    registry.RoleOrdinalV1(role=item.role, ordinal=item.ordinal)
                    for item in flattened
                ),
                "expanded_records": flattened,
            }
        )
        bad = good.model_copy(update={"expansion": expansion, "complete_records": flattened})
        assert failure(registry.validate_prepared_shape(value, bad)) == "RECORD_SHAPE"
        swapped = (*records[:2], records[3], records[2], *records[4:])
        partial = records[:-1]
        duplicate = (
            records[0],
            records[1].model_copy(update={"role": "SUBJECT"}),
            *records[2:],
        )
        gap = (*records[:4], records[4].model_copy(update={"ordinal": 2}), *records[5:])
        extra = (*records, tagged_record("SUBJECT", 2, value.record_refs[0]))
        wrong_schema = (
            records[0].model_copy(update={"physical_schema": wire("wrong.v1")}),
            *records[1:],
        )
        for mutant in (partial, swapped, duplicate, gap, extra, wrong_schema):
            mutated_expansion = good.expansion.model_copy(
                update={
                    "ordered_record_roles": tuple(
                        registry.RoleOrdinalV1(role=item.role, ordinal=item.ordinal)
                        for item in mutant
                    ),
                    "expanded_records": mutant,
                }
            )
            candidate = good.model_copy(
                update={"expansion": mutated_expansion, "complete_records": mutant}
            )
            assert failure(registry.validate_prepared_shape(value, candidate)) == "RECORD_SHAPE"
    data = value.model_dump()
    data["key"]["request_schema_id"] = "other.v1"
    with pytest.raises(ValidationError, match="key request schema"):
        registry.OperationContractV1.model_validate(data)
    data = value.model_dump()
    data["ordered_participants"][0]["preparation_key"]["request_schema_id"] = "other.v1"
    with pytest.raises(ValidationError, match="participant preparation key schema"):
        registry.OperationContractV1.model_validate(data)


def test_role_tags_reject_omitted_and_extra_expansion_members() -> None:
    value = contract("effects.reconcile")
    good = prepared(value)
    omitted_expansion = good.expansion.model_copy(
        update={"ordered_record_roles": (), "expanded_records": ()}
    )
    omitted = good.model_copy(update={"expansion": omitted_expansion, "complete_records": ()})
    assert failure(registry.validate_prepared_shape(value, omitted)) == "RECORD_SHAPE"
    extra_command = tagged_command("extra", 0, value.ordered_participants[0])
    extra_expansion = good.expansion.model_copy(
        update={
            "ordered_command_roles": (
                *good.expansion.ordered_command_roles,
                registry.RoleOrdinalV1(role="extra", ordinal=0),
            ),
            "expanded_commands": (*good.expansion.expanded_commands, extra_command),
        }
    )
    extra = good.model_copy(
        update={
            "expansion": extra_expansion,
            "ordered_source_commands": (*good.ordered_source_commands, extra_command),
        }
    )
    assert failure(registry.validate_prepared_shape(value, extra)) == "COMMAND_SHAPE"


def test_owner_result_fingerprints_match_command_role_ordinals_exactly() -> None:
    base = contract("effects.authorize")
    participant = base.ordered_participants[0].model_copy(
        update={"role": "effect", "cardinality": "ORDERED_MANY"}
    )
    ref = base.record_refs[0].model_copy(update={"role": "effect-record"})
    data = base.model_dump()
    data["ordered_participants"] = (participant.model_dump(),)
    data["record_refs"] = (ref.model_dump(),)
    data["applicability"][0]["permitted_record_roles"] = ("effect-record",)
    data["result_branches"][0]["ordered_record_blocks"] = (
        {"role": "effect-record", "cardinality": "ORDERED_MANY"},
    )
    value = registry.OperationContractV1.model_validate(data)
    commands = tuple(
        tagged_command("effect", ordinal, value.ordered_participants[0]) for ordinal in range(2)
    )
    records = tuple(
        tagged_record("effect-record", ordinal, value.record_refs[0]) for ordinal in range(2)
    )
    two = prepared(value, commands, records)
    one_source = two.expansion.model_copy(
        update={
            "ordered_owner_result_fingerprints": two.expansion.ordered_owner_result_fingerprints[:1]
        }
    )
    assert (
        failure(
            registry.validate_prepared_shape(
                value, two.model_copy(update={"expansion": one_source})
            )
        )
        == "COMMAND_SHAPE"
    )
    one = prepared(value, commands[:1], records[:1])
    second_source = registry.OwnerResultFingerprintV1(
        participant_role="effect", ordinal=1, fingerprint="3" * 64
    )
    two_sources = one.expansion.model_copy(
        update={
            "ordered_owner_result_fingerprints": (
                *one.expansion.ordered_owner_result_fingerprints,
                second_source,
            )
        }
    )
    assert (
        failure(
            registry.validate_prepared_shape(
                value, one.model_copy(update={"expansion": two_sources})
            )
        )
        == "COMMAND_SHAPE"
    )
