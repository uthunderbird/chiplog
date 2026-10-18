"""Mounted evidence tests for the read-only original Prepare projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import chiplog.composition.r14_original_prepare_source as original_source
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_preparation_bindings_contracts import (
    NativeExecutionPreparationBindingsV2,
    decode_native_execution_preparation_bindings_v2,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
    open_installed_h1_runtime,
)
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_seal
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit, admit_complete_script
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


@asynccontextmanager
async def _selected_v3_prepare(
    tmp_path: Path,
) -> AsyncIterator[tuple[CommonCliExecutionRuntime, CallSubjectHead]]:
    database = tmp_path / "original-prepare.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        await runtime.seal_execution_complete(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
        )
        decision = next(
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if "execution_complete_seal" in json.loads(raw)
            and "V3" in json.loads(raw)["execution_complete_seal"]
        )
        retained = RetainedExecutionCompleteSealV3.model_validate_json(
            decision["execution_complete_seal"]
        )
        seal = retained.exchange.proposal.fan_out.response_seal
        yield (
            cast(CommonCliExecutionRuntime, runtime),
            CallSubjectHead(
                subject_id=seal.response_seal_id,
                revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
            ),
        )


async def _seal_installed_v3(runtime: CommonCliExecutionRuntime) -> CallSubjectHead:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    completion = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="mounted scope"),)),),
    )
    runtime._execution_model._responses = (completion.canonical_bytes(),)
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
    )
    decision = next(
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == sealed.head
    )
    retained = RetainedExecutionCompleteSealV3.model_validate_json(
        decision["execution_complete_seal"]
    )
    seal = retained.exchange.proposal.fan_out.response_seal
    return CallSubjectHead(
        subject_id=seal.response_seal_id,
        revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
    )


@pytest.mark.asyncio
async def test_reopens_exact_original_prepare_after_later_publication(tmp_path: Path) -> None:
    async with _selected_v3_prepare(tmp_path) as (runtime, seal):
        source = original_source.R14OriginalNativePrepareSource(runtime)
        projection = source.collect(seal)
        assert projection is not None
        assert projection.descriptive_original_h1_output_scope is None
        bindings = bind_native_execution_preparation_v2(projection)
        assert (
            decode_native_execution_preparation_bindings_v2(bindings.canonical_bytes()) == bindings
        )
        assert bindings.reported_template_digest == projection.artifact.content_hash
        assert (
            bindings.rendered_prompt_sha256
            == hashlib.sha256(projection.rendered_prompt_bytes).hexdigest()
        )
        assert bindings.reported_template_digest != bindings.rendered_prompt_sha256
        selected = select_h1_v3_prepare_for_seal(runtime, selected_seal=seal, historical=True)
        assert (
            selected.prepare.physical_run.record_id == selected.prepare.retained.proposal.run.head
        )
        assert projection.retained_prepare_bytes == selected.prepare.retained_bytes
        assert projection.database_identity == runtime._database_identity
        assert projection.prepared_run_bytes == selected.prepare.physical_run.canonical_bytes
        issuance = (
            R13Workspace(runtime).open_h1_workspace_issuance().load(selected.prepare.issuance_ref)
        )
        assert projection.workspace_issuance_bytes == issuance.canonical_bytes()
        await runtime.create_execution("hermetic-ingress", "later-run", "Later", BudgetPolicy())
        replay = source.collect(seal)
        assert replay == projection
        assert bind_native_execution_preparation_v2(replay) == bindings
        assert replay.rendered_prompt_bytes == replay.artifact.rendered.encode()
        assert replay.artifact_bytes == replay.artifact.canonical_bytes()
        assert replay.generated_schema_bytes == replay.artifact.response_schema_json.encode()
        assert replay.ordered_tool_spec_bytes == tuple(
            tool.canonical_bytes() for tool in replay.artifact.tools
        )
        assert replay.policy_bytes
        rendered_digest = hashlib.sha256(replay.rendered_prompt_bytes).hexdigest()
        assert rendered_digest != replay.artifact.content_hash


@pytest.mark.asyncio
async def test_native_bindings_v2_rejects_every_projection_byte_join(tmp_path: Path) -> None:
    async with _selected_v3_prepare(tmp_path) as (runtime, seal):
        projection = original_source.R14OriginalNativePrepareSource(runtime).collect(seal)
        assert projection is not None
        for changed in (
            replace(projection, selected_prepare_decision_bytes=b"{}"),
            replace(projection, selected_prepare_decision_fingerprint="0" * 64),
            replace(projection, prepared_run_bytes=b"{}"),
            replace(
                projection,
                prepared_run=projection.prepared_run.model_copy(update={"subject_id": "other-run"}),
            ),
            replace(projection, artifact_bytes=b"{}"),
            replace(projection, rendered_prompt_bytes=b"other rendered"),
            replace(projection, generated_schema_bytes=b"{}"),
            replace(
                projection,
                ordered_tool_spec_bytes=tuple(reversed(projection.ordered_tool_spec_bytes)),
            ),
            replace(projection, policy_bytes=b"{}"),
        ):
            with pytest.raises(ValueError):
                bind_native_execution_preparation_v2(changed)

        other_artifact = projection.artifact.model_copy(
            update={"content_hash": "1" * 64, "rendered": "other rendered"}
        )
        members = projection.manifest.members
        substituted_members = (
            members[0],
            members[1],
            members[2].model_copy(
                update={
                    "content": other_artifact.rendered,
                    "revision_head": other_artifact.content_hash,
                    "provenance_head": other_artifact.content_hash,
                }
            ),
            members[3].model_copy(
                update={
                    "revision_head": other_artifact.digest(),
                    "provenance_head": other_artifact.digest(),
                }
            ),
        )
        cross_prepare = replace(
            projection,
            manifest=projection.manifest.model_copy(
                update={"artifact": other_artifact, "members": substituted_members}
            ),
            artifact=other_artifact,
            artifact_bytes=other_artifact.canonical_bytes(),
            rendered_prompt_bytes=other_artifact.rendered.encode(),
            generated_schema_bytes=other_artifact.response_schema_json.encode(),
            ordered_tool_spec_bytes=tuple(tool.canonical_bytes() for tool in other_artifact.tools),
        )
        with pytest.raises(ValueError):
            bind_native_execution_preparation_v2(cross_prepare)

        bindings = bind_native_execution_preparation_v2(projection)
        for field, replacement in (
            ("reported_template_digest", bindings.rendered_prompt_sha256),
            ("rendered_prompt_sha256", bindings.reported_template_digest),
        ):
            raw = bindings.model_dump(mode="json")
            raw[field] = replacement
            with pytest.raises(ValueError):
                NativeExecutionPreparationBindingsV2.model_validate(raw)


@pytest.mark.asyncio
async def test_holds_for_substituted_seal_or_manifest_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _selected_v3_prepare(tmp_path) as (runtime, seal):
        source = original_source.R14OriginalNativePrepareSource(runtime)
        substituted_seal = seal.model_copy(
            update={"revision": Present(head=seal.revision.head, fingerprint="0" * 64)}
        )
        assert source.collect(substituted_seal) is None
        selected = select_h1_v3_prepare_for_seal(runtime, selected_seal=seal, historical=True)
        attempt = selected.captured_run.turns[0].attempts[0]
        manifest = attempt.manifest.model_copy(
            update={"members": tuple(reversed(attempt.manifest.members))}
        )
        turn = selected.captured_run.turns[0].model_copy(
            update={"attempts": (attempt.model_copy(update={"manifest": manifest}),)}
        )
        captured_run = selected.captured_run.model_copy(update={"turns": (turn,)})
        substituted = replace(selected, captured_run=captured_run)
        monkeypatch.setattr(
            original_source,
            "select_h1_v3_prepare_for_seal",
            lambda *_a, **_k: substituted,
        )
        assert source.collect(seal) is None


@pytest.mark.asyncio
async def test_reopens_descriptive_v2_scope_and_holds_one_sided_substitutions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            seal = await _seal_installed_v3(cast(CommonCliExecutionRuntime, runtime))
            source = original_source.R14OriginalNativePrepareSource(
                cast(CommonCliExecutionRuntime, runtime)
            )
            projection = source.collect(seal)
            assert projection is not None
            descriptive = projection.descriptive_original_h1_output_scope
            assert descriptive is not None
            assert descriptive.scope_bytes == descriptive.scope.canonical_bytes()
            assert descriptive.scope.disclosure_policy.canonical_source_bytes
            policy = original_source.decode_h1_workspace_policy_v2(descriptive.policy_bytes)
            assert policy.registration.output_scope_anchor == descriptive.policy_anchor
            assert policy.registration.output_scope_ref == descriptive.policy_scope_ref
            with runtime._authority_gate().hold():
                record = runtime._h1_historical_trust_reader().historical_record(
                    descriptive.policy_anchor.decision.head,
                    descriptive.policy_anchor.record_ordinal,
                )
            assert descriptive.scope_decision_bytes == record.envelope_bytes
            assert descriptive.scope_record_bytes == record.record_bytes
            accepted = descriptive.scope.disclosure_policy.canonical_source_bytes
            assert b'"purpose":"H1_LOCAL_COMMENTARY"' in accepted
            assert b'"call_count":0' in accepted

            changed_anchor = policy.registration.output_scope_anchor.model_copy(
                update={
                    "decision": ExactHead(
                        identity="deployment-trust/journal",
                        head="0" * 64,
                        fingerprint="0" * 64,
                    )
                }
            )
            changed_policy = policy.model_copy(
                update={
                    "registration": policy.registration.model_copy(
                        update={"output_scope_anchor": changed_anchor}
                    )
                }
            )
            monkeypatch.setattr(
                original_source,
                "decode_h1_workspace_policy_v2",
                lambda _raw: changed_policy,
            )
            assert source.collect(seal) is None
            monkeypatch.undo()

            changed_policy = policy.model_copy(
                update={
                    "registration": policy.registration.model_copy(
                        update={
                            "output_scope_ref": ExactHead(
                                identity=descriptive.scope.scope_id,
                                head=descriptive.scope.scope_id + "/" + "0" * 64,
                                fingerprint="0" * 64,
                            )
                        }
                    )
                }
            )
            monkeypatch.setattr(
                original_source,
                "decode_h1_workspace_policy_v2",
                lambda _raw: changed_policy,
            )
            assert source.collect(seal) is None
            monkeypatch.undo()

            changed_policy = policy.model_copy(
                update={
                    "registration": policy.registration.model_copy(
                        update={"origin_recipient_id": "other-recipient"}
                    )
                }
            )
            monkeypatch.setattr(
                original_source,
                "decode_h1_workspace_policy_v2",
                lambda _raw: changed_policy,
            )
            assert source.collect(seal) is None
            monkeypatch.undo()

            selected = select_h1_v3_prepare_for_seal(runtime, selected_seal=seal, historical=True)
            issuance = (
                R13Workspace(runtime)
                .open_h1_workspace_issuance()
                .load(selected.prepare.issuance_ref)
            )
            malformed_issuance = issuance.model_copy(
                update={
                    "sources": issuance.sources.model_copy(
                        update={"policy_payload_base64": "not-base64!"}
                    )
                }
            )

            class _MalformedIssuanceReader:
                def open_h1_workspace_issuance(self) -> _MalformedIssuanceReader:
                    return self

                def load(self, _ref: object) -> object:
                    return malformed_issuance

            monkeypatch.setattr(
                original_source,
                "R13Workspace",
                lambda _runtime: _MalformedIssuanceReader(),
            )
            assert source.collect(seal) is None
            monkeypatch.undo()

        async with open_installed_h1_runtime(launch, resources=resources) as reopened:
            replay = original_source.R14OriginalNativePrepareSource(
                cast(CommonCliExecutionRuntime, reopened)
            ).collect(seal)
            assert replay == projection
