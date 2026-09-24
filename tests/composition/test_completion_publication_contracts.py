"""Completion publication closes actual owner exchanges into exact batch bytes."""

import asyncio
import hashlib

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.completion_terminal_work_sources import (
    completion_terminal_work_source_bytes,
)
from chiplog.capabilities.effects.scoped_intent_contracts import (
    HumanScopedAdoption,
    PreparedDeliveryAuthority,
)
from chiplog.composition import completion_publication_contracts as completion
from chiplog.platform._owner_publication_contracts import (
    CompleteDeliveryBatchV2,
    OwnerRecordBytes,
    RejectedCompletionBatchV1,
)
from chiplog.platform.owner_publications import source_commands
from tests.support.completion_assembly import (
    accepted_completion_fixture,
    rejected_completion_fixture,
)


def test_completion_assembly_schemas_and_fixed_role_labels_are_public() -> None:
    assert (
        completion.PrepareCompleteAcceptanceAssemblyV1.model_fields["schema_id"].default
        == "chiplog.composition.complete-acceptance-assembly.v1"
    )
    assert (
        completion.PrepareRejectedCompletionAssemblyV1.model_fields["schema_id"].default
        == "chiplog.composition.rejected-completion-assembly.v1"
    )
    assert completion.LOOP_COMPLETION_SCHEMA != completion.LOOP_REJECTION_SCHEMA
    assert completion.WORK_SCHEMA.startswith("chiplog.agent-loop.")


def test_h1_local_assembly_is_a_separate_closed_variant_from_legacy_acceptance() -> None:
    assert (
        completion.H1LocalDeliveryEffectsExchangeV1.model_fields["schema_id"].default
        == "chiplog.composition.h1-local-delivery-effects-exchange.v1"
    )
    assert (
        completion.PrepareH1CompleteAcceptanceAssemblyV1.model_fields["schema_id"].default
        == "chiplog.composition.h1-complete-acceptance-assembly.v1"
    )
    effects = completion.PrepareH1CompleteAcceptanceAssemblyV1.model_fields["ordered_effects"]
    assert effects.metadata[0].min_length == 1
    assert effects.metadata[1].max_length == 1
    assert {
        "work_source",
        "terminal_work_request",
        "terminal_work_result",
    } <= completion.PrepareH1CompleteAcceptanceAssemblyV1.model_fields.keys()
    # H1 adds a distinct branch; the established V1 field and wire stay stable.
    legacy_effects = completion.PrepareCompleteAcceptanceAssemblyV1.model_fields["ordered_effects"]
    assert legacy_effects.annotation != effects.annotation


def test_complete_acceptance_closes_v2_and_v3_two_delivery_owner_exchanges() -> None:
    async def exercise() -> None:
        for run_schema in ("v2", "v3"):
            fixture = await accepted_completion_fixture(run_schema, "nonempty")
            assert (
                TypeAdapter(CompleteDeliveryBatchV2).validate_json(fixture.batch.model_dump_json())
                == fixture.batch
            )
            assert source_commands(fixture.batch) == fixture.expected_commands
            assert fixture.batch.complete_records == fixture.expected_records
            assert (
                completion.expected_completion_records(fixture.assembly) == fixture.expected_records
            )
            assert (
                completion.validate_complete_acceptance_batch(fixture.assembly, fixture.batch)
                is None
            )
            deliveries = fixture.assembly.prepared_completion.delivery.manifest.ordered_deliveries
            expected_delivery_ids = [item.delivery_id for item in deliveries]
            actual_delivery_ids = [item.delivery_id for item in fixture.assembly.ordered_effects]
            assert actual_delivery_ids == expected_delivery_ids
            assert [item.owner for item in fixture.batch.prepared_effects_commands] == [
                "effects",
                "effects",
            ]

    asyncio.run(exercise())


def test_complete_acceptance_explicit_empty_terminal_work_still_closes() -> None:
    async def exercise() -> None:
        fixture = await accepted_completion_fixture("v3", "empty")
        assert fixture.assembly.terminal_work_result.ordered_work == ()
        assert fixture.assembly.terminal_work_result.complete_records == ()
        assert (
            completion.validate_complete_acceptance_batch(fixture.assembly, fixture.batch) is None
        )

    asyncio.run(exercise())


def test_rejected_completion_closes_v2_and_v3_without_effects_or_conversation_rows() -> None:
    async def exercise() -> None:
        for run_schema in ("v2", "v3"):
            fixture = await rejected_completion_fixture(run_schema)
            assert (
                TypeAdapter(RejectedCompletionBatchV1).validate_json(
                    fixture.batch.model_dump_json()
                )
                == fixture.batch
            )
            assert source_commands(fixture.batch) == fixture.expected_commands
            assert fixture.batch.complete_records == fixture.expected_records
            assert (
                completion.expected_completion_records(fixture.assembly) == fixture.expected_records
            )
            assert fixture.assembly.conversation_result.ordered_members == ()
            assert all(record.owner != "effects" for record in fixture.batch.complete_records)
            assert (
                completion.validate_rejected_completion_batch(fixture.assembly, fixture.batch)
                is None
            )

    asyncio.run(exercise())


def test_accepted_batch_mutants_reach_the_closure_validator() -> None:
    async def exercise() -> None:
        fixture = await accepted_completion_fixture("v3")
        batch = fixture.batch
        assembly = fixture.assembly
        second = batch.prepared_effects_commands[1]
        mutated_batches = (
            batch.model_copy(
                update={"prepared_effects_commands": (batch.prepared_effects_commands[0],)}
            ),
            batch.model_copy(
                update={"prepared_effects_commands": (second, batch.prepared_effects_commands[0])}
            ),
            batch.model_copy(update={"conversation_command": batch.loop_command}),
            batch.model_copy(update={"complete_records": batch.complete_records[:-1]}),
            batch.model_copy(
                update={"complete_records": (*batch.complete_records, batch.complete_records[0])}
            ),
            batch.model_copy(update={"complete_records": tuple(reversed(batch.complete_records))}),
            batch.model_copy(
                update={
                    "complete_records": (
                        batch.complete_records[1],
                        *batch.complete_records[1:],
                    )
                }
            ),
            batch.model_copy(update={"complete_batch_fingerprint": "0" * 64}),
        )
        for mutant in mutated_batches:
            failure = completion.validate_complete_acceptance_batch(assembly, mutant)
            assert failure is not None

        changed_work = assembly.terminal_work_request.model_copy(
            update={"original_terminalization_request": b"not-a-completion-source"}
        )
        work_mutant = assembly.model_copy(update={"terminal_work_request": changed_work})
        assert completion.validate_complete_acceptance_batch(work_mutant, batch) is not None

        changed_result = assembly.ordered_effects[1].intent_result.model_copy(
            update={"source_request_fingerprint": "0" * 64}
        )
        changed_exchange = assembly.ordered_effects[1].model_copy(
            update={"intent_result": changed_result}
        )
        effects_mutant = assembly.model_copy(
            update={"ordered_effects": (assembly.ordered_effects[0], changed_exchange)}
        )
        assert completion.validate_complete_acceptance_batch(effects_mutant, batch) is not None

    asyncio.run(exercise())


def test_effects_exchanges_bind_each_outer_delivery_and_authority_basis() -> None:
    def rebuilt_batch(
        batch: CompleteDeliveryBatchV2,
        assembly: completion.PrepareCompleteAcceptanceAssemblyV1,
        records: tuple[OwnerRecordBytes, ...],
    ) -> CompleteDeliveryBatchV2:
        commands = completion.acceptance_commands(assembly)
        placeholder = batch.model_copy(
            update={
                "loop_command": commands[0],
                "conversation_command": commands[1],
                "prepared_effects_commands": commands[2:-1],
                "terminal_work_command": commands[-1],
                "complete_records": records,
                "complete_batch_fingerprint": "0" * 64,
            }
        )
        fingerprint = completion.completion_batch_fingerprint(placeholder)
        return placeholder.model_copy(update={"complete_batch_fingerprint": fingerprint})

    async def exercise() -> None:
        fixture = await accepted_completion_fixture("v3")
        first, second = fixture.assembly.ordered_effects

        duplicated = first.model_copy(update={"delivery_id": second.delivery_id})
        duplicate_assembly = fixture.assembly.model_copy(
            update={"ordered_effects": (first, duplicated)}
        )
        records = list(fixture.batch.complete_records)
        effect_indexes = [
            index for index, record in enumerate(records) if record.owner == "effects"
        ]
        records[effect_indexes[1]] = records[effect_indexes[0]]
        duplicate_batch = rebuilt_batch(fixture.batch, duplicate_assembly, tuple(records))
        duplicate_failure = completion.validate_complete_acceptance_batch(
            duplicate_assembly, duplicate_batch
        )
        assert duplicate_failure is not None
        assert duplicate_failure.code == "EXCHANGE"

        changed_basis = second.model_copy(update={"basis": first.basis})
        basis_assembly = fixture.assembly.model_copy(
            update={"ordered_effects": (first, changed_basis)}
        )
        basis_batch = rebuilt_batch(fixture.batch, basis_assembly, fixture.batch.complete_records)
        basis_failure = completion.validate_complete_acceptance_batch(basis_assembly, basis_batch)
        assert basis_failure is not None
        assert basis_failure.code == "EXCHANGE"

        authority = second.intent_request.intent.acquisition.authority
        assert isinstance(authority, PreparedDeliveryAuthority)
        non_delivery_authority = HumanScopedAdoption(
            adoption_act=authority.preexisting_communication_authority,
            display=authority.preexisting_communication_authority.head,
            exact_display_bytes=authority.preexisting_communication_authority.canonical_record_bytes,
            exact_mandate_bytes=second.intent_request.intent.mandate.canonical_bytes(),
            authenticated_invocation=authority.current_disclosure_authority,
        )
        bad_acquisition = second.intent_request.intent.acquisition.model_copy(
            update={"authority": non_delivery_authority}
        )
        bad_intent = second.intent_request.intent.model_copy(
            update={"acquisition": bad_acquisition}
        )
        bad_request = second.intent_request.model_copy(update={"intent": bad_intent})
        wrong_authority = second.model_copy(update={"intent_request": bad_request})
        authority_assembly = fixture.assembly.model_copy(
            update={"ordered_effects": (first, wrong_authority)}
        )
        authority_batch = rebuilt_batch(
            fixture.batch, authority_assembly, fixture.batch.complete_records
        )
        authority_failure = completion.validate_complete_acceptance_batch(
            authority_assembly, authority_batch
        )
        assert authority_failure is not None
        assert authority_failure.code == "EXCHANGE"

    asyncio.run(exercise())


def test_rejected_batch_mutants_reach_the_closure_validator() -> None:
    async def exercise() -> None:
        fixture = await rejected_completion_fixture("v3")
        batch = fixture.batch
        assembly = fixture.assembly
        effects_record = next(
            record
            for record in (await accepted_completion_fixture("v3")).batch.complete_records
            if record.owner == "effects"
        )
        with_effect = batch.model_copy(
            update={"complete_records": (*batch.complete_records, effects_record)}
        )
        assert completion.validate_rejected_completion_batch(assembly, with_effect) is not None

        reject_bytes = assembly.completion_reject.canonical_bytes()
        replaced_loop = batch.loop_rejection_command.model_copy(
            update={
                "canonical_bytes": reject_bytes,
                "fingerprint": hashlib.sha256(reject_bytes).hexdigest(),
            }
        )
        wrong_loop = batch.model_copy(update={"loop_rejection_command": replaced_loop})
        assert completion.validate_rejected_completion_batch(assembly, wrong_loop) is not None

        wrong_result = assembly.rejected_terminalization_result.model_copy(
            update={"source_request_fingerprint": "0" * 64}
        )
        result_mutant = assembly.model_copy(
            update={"rejected_terminalization_result": wrong_result}
        )
        assert completion.validate_rejected_completion_batch(result_mutant, batch) is not None

    asyncio.run(exercise())


def test_terminal_work_requires_the_redecoded_source_run_manifest_and_obligation_order() -> None:
    def rebuilt_batch(
        batch: CompleteDeliveryBatchV2,
        assembly: completion.PrepareCompleteAcceptanceAssemblyV1,
        records: tuple[OwnerRecordBytes, ...] | None = None,
    ) -> CompleteDeliveryBatchV2:
        commands = completion.acceptance_commands(assembly)
        placeholder = batch.model_copy(
            update={
                "loop_command": commands[0],
                "conversation_command": commands[1],
                "prepared_effects_commands": commands[2:-1],
                "terminal_work_command": commands[-1],
                "complete_records": batch.complete_records if records is None else records,
                "complete_batch_fingerprint": "0" * 64,
            }
        )
        fingerprint = completion.completion_batch_fingerprint(placeholder)
        return placeholder.model_copy(update={"complete_batch_fingerprint": fingerprint})

    async def exercise() -> None:
        fixture = await accepted_completion_fixture("v3", "nonempty")
        empty_source = fixture.assembly.work_source.model_copy(
            update={"ordered_open_obligations": ()}
        )
        empty_request = fixture.assembly.terminal_work_request.model_copy(
            update={
                "original_terminalization_request": completion_terminal_work_source_bytes(
                    empty_source
                ),
                "ordered_open_obligations": (),
            }
        )
        empty_work = fixture.assembly.terminal_work_result.model_copy(
            update={
                "source_request_fingerprint": hashlib.sha256(
                    empty_request.canonical_bytes()
                ).hexdigest(),
                "ordered_work": (),
                "complete_records": (),
                "complete_commitment": hashlib.sha256(b"").hexdigest(),
            }
        )
        dropped_assembly = fixture.assembly.model_copy(
            update={
                "work_source": empty_source,
                "terminal_work_request": empty_request,
                "terminal_work_result": empty_work,
            }
        )
        dropped_records = fixture.batch.complete_records[
            : -len(fixture.assembly.terminal_work_result.complete_records)
        ]
        dropped_failure = completion.validate_complete_acceptance_batch(
            dropped_assembly,
            rebuilt_batch(fixture.batch, dropped_assembly, dropped_records),
        )
        assert dropped_failure is not None
        assert dropped_failure.code == "SOURCE"

        two = await accepted_completion_fixture("v3", "nonempty2")
        reversed_obligations = tuple(reversed(two.assembly.work_source.ordered_open_obligations))
        reordered_source = two.assembly.work_source.model_copy(
            update={"ordered_open_obligations": reversed_obligations}
        )
        reordered_request = two.assembly.terminal_work_request.model_copy(
            update={
                "original_terminalization_request": completion_terminal_work_source_bytes(
                    reordered_source
                ),
                "ordered_open_obligations": reversed_obligations,
            }
        )
        reordered_assembly = two.assembly.model_copy(
            update={
                "work_source": reordered_source,
                "terminal_work_request": reordered_request,
            }
        )
        reordered_failure = completion.validate_complete_acceptance_batch(
            reordered_assembly, rebuilt_batch(two.batch, reordered_assembly)
        )
        assert reordered_failure is not None
        assert reordered_failure.code == "SOURCE"

        rejected = await rejected_completion_fixture("v3")
        wrong_run_request = rejected.assembly.terminal_work_request.model_copy(
            update={"terminal_run": rejected.assembly.original_completion_request.run}
        )
        rejected_assembly = rejected.assembly.model_copy(
            update={"terminal_work_request": wrong_run_request}
        )
        commands = completion.rejected_commands(rejected_assembly)
        placeholder = rejected.batch.model_copy(
            update={
                "loop_rejection_command": commands[0],
                "rejected_terminalization_command": commands[1],
                "conversation_no_change_command": commands[2],
                "terminal_work_command": commands[3],
                "complete_batch_fingerprint": "0" * 64,
            }
        )
        fingerprint = completion.completion_batch_fingerprint(placeholder)
        rejected_batch = placeholder.model_copy(update={"complete_batch_fingerprint": fingerprint})
        rejected_failure = completion.validate_rejected_completion_batch(
            rejected_assembly, rejected_batch
        )
        assert rejected_failure is not None
        assert rejected_failure.code == "SOURCE"

    asyncio.run(exercise())
