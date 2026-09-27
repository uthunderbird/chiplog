"""Source-only native H1 member replay remains ordered and issuer-bound."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import chiplog.composition.h1_native_member_sources as native_sources
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, DisclosureLabel
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.h1_native_member_sources import (
    H1NativeMemberOccurrence,
    H1NativeMemberSourceCut,
    H1NativeMemberSourcePart,
    H1NativeMemberSources,
    _member_source,
)
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1WorkspaceIssuanceRefV1
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import admit_complete_script


def _occurrence(index: int) -> H1NativeMemberOccurrence:
    return H1NativeMemberOccurrence(
        turn_id="turn",
        attempt_id="attempt",
        manifest_digest="a" * 64,
        member_index=index,
        member_digest=("b" if index == 0 else "c") * 64,
        member_bytes=b"same" if index else b"first",
        source_kind="CONTEXT",
        original_label=DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
        provenance_head="provenance",
        label_head="label",
        source_bytes=b"started-run",
    )


def _workspace() -> H1WorkspaceIssuanceRefV1:
    return H1WorkspaceIssuanceRefV1(
        tenant="tenant", batch_id="batch", entry_id="d" * 64, payload_digest="e" * 64
    )


def _issuer_with_cut() -> tuple[H1NativeMemberSources, H1NativeMemberSourceCut]:
    reader = object.__new__(H1FirstPathSources)
    native = SimpleNamespace(
        source=SimpleNamespace(selected_response_seal=object()),
        initialization=SimpleNamespace(raw_bytes=b"initialization"),
    )
    cast(Any, reader).replay_selected_native_cut = lambda *_args, **_kwargs: native
    started = SimpleNamespace(
        run_id="run", head="started", canonical_bytes=lambda: b"started-canonical"
    )
    captured = SimpleNamespace(
        turns=(
            SimpleNamespace(
                attempts=(
                    SimpleNamespace(
                        manifest=SimpleNamespace(artifact=SimpleNamespace(digest=lambda: "f" * 64))
                    ),
                ),
            ),
        )
    )
    prepare = SimpleNamespace(
        prepare=SimpleNamespace(
            decision_id="prepare",
            decision_bytes=b"prepare",
            started_run=started,
            retained_bytes=b"retained-prepare",
            retained=SimpleNamespace(canonical_bytes=lambda: b"retained-prepare"),
        ),
        captured_run=captured,
    )
    issuer = object.__new__(H1NativeMemberSources)
    cast(Any, issuer)._runtime = object()
    issuer._first_path = reader
    issuer._issued = {}
    cut = H1NativeMemberSourceCut(
        reader,
        cast(Any, native),
        cast(Any, prepare),
        _workspace(),
        (_occurrence(0), _occurrence(1)),
    )
    issuer._issued[id(cut)] = cut
    return issuer, cut


def test_projection_preserves_complete_ordered_vector_including_repeated_bytes() -> None:
    issuer, cut = _issuer_with_cut()

    parts = issuer.project(cut)

    assert tuple(part.occurrence.member_index for part in parts) == (0, 1)
    assert tuple(part.occurrence.member_bytes for part in parts) == (b"first", b"same")
    assert tuple(part.occurrence.member_digest for part in parts) == ("b" * 64, "c" * 64)
    assert tuple(
        (part.occurrence.provenance_head, part.occurrence.label_head) for part in parts
    ) == (("provenance", "label"), ("provenance", "label"))
    assert tuple(part.source_locator_kind for part in parts) == ("STARTED_RUN", "STARTED_RUN")


def test_projection_rejects_copied_or_foreign_cut_before_replay() -> None:
    issuer, cut = _issuer_with_cut()
    copied = H1NativeMemberSourceCut(
        cut._reader, cut._native, cut._prepare, cut._workspace_issuance, cut._occurrences
    )

    with pytest.raises(ValueError, match="issuer-owned"):
        issuer.project(copied)


def test_current_projection_rejects_unissued_cut_before_runtime_replay() -> None:
    issuer = object.__new__(H1NativeMemberSources)
    issuer._current_issued = {}

    with pytest.raises(ValueError, match="issuer-owned"):
        issuer.project_current(cast(Any, object()))


@pytest.mark.parametrize(
    ("surface", "expected_kind", "expected_bytes"),
    [
        ("workspace", "WORKSPACE", b"workspace"),
        ("context", "CONTEXT", b"started"),
        ("prompt", "PROMPT", b"prepare"),
        ("schema", "SCHEMA", b"prepare"),
    ],
)
def test_native_surface_provenance_is_closed(
    surface: str, expected_kind: str, expected_bytes: bytes
) -> None:
    started = SimpleNamespace(canonical_bytes=lambda: b"started")
    prepare = SimpleNamespace(
        prepare=SimpleNamespace(workspace_member_bytes=b"workspace", retained_bytes=b"prepare")
    )

    assert _member_source(surface, cast(Any, started), cast(Any, prepare)) == (
        expected_kind,
        expected_bytes,
    )


def test_unknown_native_surface_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown surface"):
        _member_source("foreign", cast(Any, SimpleNamespace()), cast(Any, SimpleNamespace()))


def test_historical_replay_rejects_reordered_or_omitted_member_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuer, cut = _issuer_with_cut()
    parts = issuer.project(cut)
    prepare = cut._prepare
    monkeypatch.setattr(native_sources, "select_h1_v3_prepare_for_seal", lambda *_a, **_k: prepare)
    monkeypatch.setattr(
        native_sources,
        "reopen_selected_h1_workspace",
        lambda *_a, **_k: SimpleNamespace(issuance=cut._workspace_issuance),
    )
    monkeypatch.setattr(native_sources, "_occurrences", lambda *_a, **_k: cut._occurrences)

    with pytest.raises(ValueError, match="parts differ"):
        issuer.validate_historical(
            cast(Any, cut._native.source),
            initialization_envelope_bytes=cut._native.initialization.raw_bytes,
            member_parts=(parts[1],),
        )
    with pytest.raises(ValueError, match="parts differ"):
        issuer.validate_historical(
            cast(Any, cut._native.source),
            initialization_envelope_bytes=cut._native.initialization.raw_bytes,
            member_parts=(parts[1], parts[0]),
        )


@pytest.mark.asyncio
async def test_real_selected_native_v3_replay_preserves_full_vector_after_benign_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "native-members.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        selected_initial = cast(Any, initial)
        started = await runtime.begin_execution(
            "hermetic-ingress",
            selected_initial.stable_run_lineage_id,
            selected_initial.selected_run_head.head,
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", selected_initial.stable_run_lineage_id, started.head
        )
        sealed = await runtime.seal_execution_complete(
            "hermetic-ingress",
            selected_initial.stable_run_lineage_id,
            captured.head,
            profile="H1_V3",
        )
        decision = next(
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("operation_id") == sealed.head
        )
        retained = RetainedExecutionCompleteSealV3.model_validate_json(
            cast(str, decision["execution_complete_seal"])
        )
        seal = retained.exchange.proposal.fan_out.response_seal
        locator = CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
        )
        reader = H1FirstPathSources(runtime)
        capture = reader.capture_current(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=locator,
        )
        issuer = H1NativeMemberSources(runtime, reader)
        current_cut = issuer.capture_current(capture)
        assert issuer.replay_current(current_cut) == current_cut._native
        original_prepare = current_cut._prepare
        object.__setattr__(
            current_cut,
            "_prepare",
            replace(
                original_prepare, prepare=replace(original_prepare.prepare, decision_id="foreign")
            ),
        )
        with pytest.raises(ValueError, match="current"):
            issuer.replay_current(current_cut)
        object.__setattr__(current_cut, "_prepare", original_prepare)
        original_workspace = current_cut._workspace_issuance
        object.__setattr__(
            current_cut,
            "_workspace_issuance",
            original_workspace.model_copy(update={"batch_id": "foreign"}),
        )
        with pytest.raises(ValueError, match="current"):
            issuer.replay_current(current_cut)
        object.__setattr__(current_cut, "_workspace_issuance", original_workspace)
        original_occurrences = current_cut._occurrences
        object.__setattr__(current_cut, "_occurrences", ())
        with pytest.raises(ValueError, match="current"):
            issuer.replay_current(current_cut)
        object.__setattr__(current_cut, "_occurrences", original_occurrences)
        original_parts = native_sources._parts

        def parts_under_current_gate(*args: Any) -> tuple[H1NativeMemberSourcePart, ...]:
            runtime._authority_gate().require_held()
            return original_parts(*args)

        monkeypatch.setattr(native_sources, "_parts", parts_under_current_gate)
        current_parts = issuer.project_current(current_cut)
        monkeypatch.undo()
        cut = issuer.capture(
            capture.source, initialization_envelope_bytes=capture.initialization_envelope_bytes
        )
        parts = issuer.project(cut)
        manifest = captured.turns[0].attempts[0].manifest
        assert current_parts == parts
        assert len(parts) == len(manifest.members)
        assert {part.occurrence.source_kind for part in current_parts} == {
            "WORKSPACE",
            "CONTEXT",
            "PROMPT",
            "SCHEMA",
        }
        assert tuple(part.occurrence.member_bytes for part in parts) == tuple(
            member.canonical_bytes() for member in manifest.members
        )
        assert tuple(
            (part.occurrence.provenance_head, part.occurrence.label_head) for part in parts
        ) == tuple((member.provenance_head, member.label_head) for member in manifest.members)
        started_bytes = current_cut._prepare.prepare.started_run.canonical_bytes()
        retained_prepare_bytes = current_cut._prepare.prepare.retained_bytes
        artifact_digest = manifest.artifact.digest()
        assert all(part.started_run_canonical_bytes == started_bytes for part in current_parts)
        assert all(
            part.started_run_fingerprint == hashlib.sha256(started_bytes).hexdigest()
            for part in current_parts
        )
        assert all(
            part.selected_prepare_retained_bytes == retained_prepare_bytes for part in current_parts
        )
        assert all(
            part.selected_prepare_retained_digest
            == hashlib.sha256(retained_prepare_bytes).hexdigest()
            for part in current_parts
        )
        assert all(
            part.selected_prepare_artifact_digest == artifact_digest for part in current_parts
        )
        assert tuple(part.artifact_digest for part in current_parts) == tuple(
            artifact_digest if part.occurrence.source_kind in {"PROMPT", "SCHEMA"} else None
            for part in current_parts
        )

        await runtime.create_execution("hermetic-ingress", "benign-later", "Later", BudgetPolicy())
        with pytest.raises(ValueError):
            issuer.replay_current(current_cut)
        with pytest.raises(ValueError):
            issuer.project_current(current_cut)
        replayed = issuer.validate_historical(
            capture.source,
            initialization_envelope_bytes=capture.initialization_envelope_bytes,
            member_parts=parts,
        )
        assert replayed.occurrences == cut._occurrences
