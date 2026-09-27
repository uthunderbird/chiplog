"""Bounded readback of the native members in a selected H1 first path.

This module deliberately stops before delivery evidence is assembled.  In
particular, it does not manufacture a narrowing cut or a
``H1DeliveryMemberEvidenceV1``: that is an E5 responsibility.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.contracts import DisclosureLabel, VisibilityMember
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionPromptArtifact,
    ExecutionRunRecord,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    FirstPathCompletionCutV2,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_first_path_sources import (
    H1CurrentFirstPathNativeCut,
    H1FirstPathCapture,
    H1FirstPathSources,
    H1HistoricalFirstPathNativeCut,
)
from chiplog.composition.h1_selected_prepare import (
    H1SelectedPostSealPrepare,
    reopen_selected_h1_workspace,
    select_h1_v3_prepare_for_seal,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1WorkspaceIssuanceRefV1


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class H1NativeMemberOccurrence:
    """One position in the selected native manifest, including duplicate bytes."""

    turn_id: str
    attempt_id: str
    manifest_digest: str
    member_index: int
    member_digest: str
    member_bytes: bytes
    source_kind: str
    original_label: DisclosureLabel
    provenance_head: str
    label_head: str
    source_bytes: bytes


@dataclass(frozen=True, slots=True)
class H1NativeMemberSourcePart:
    """Authenticated source material, without a delivery or policy authority claim."""

    occurrence: H1NativeMemberOccurrence
    workspace_issuance: H1WorkspaceIssuanceRefV1
    selected_prepare_id: str
    selected_prepare_digest: str
    started_run_id: str
    started_run_head: str
    started_run_canonical_bytes: bytes
    started_run_fingerprint: str
    selected_prepare_retained_bytes: bytes
    selected_prepare_retained_digest: str
    selected_prepare_artifact_digest: str
    source_locator_kind: str
    artifact_digest: str | None


@dataclass(frozen=True, slots=True)
class H1NativeMemberSourceCut:
    """Private, issuer-owned snapshot of one selected native manifest."""

    _reader: H1FirstPathSources
    _native: H1HistoricalFirstPathNativeCut
    _prepare: H1SelectedPostSealPrepare
    _workspace_issuance: H1WorkspaceIssuanceRefV1
    _occurrences: tuple[H1NativeMemberOccurrence, ...]


@dataclass(frozen=True, slots=True)
class H1CurrentNativeMemberSourceCut:
    """Issuer-owned current source cut, valid only until its next replay."""

    _capture: H1FirstPathCapture
    _native: H1CurrentFirstPathNativeCut
    _prepare: H1SelectedPostSealPrepare
    _workspace_issuance: H1WorkspaceIssuanceRefV1
    _occurrences: tuple[H1NativeMemberOccurrence, ...]


@dataclass(frozen=True, slots=True)
class ValidatedH1NativeMemberSources:
    """Read-only proof for a later root validator; it grants no delivery right."""

    native: H1HistoricalFirstPathNativeCut
    occurrences: tuple[H1NativeMemberOccurrence, ...]


class H1NativeMemberSources:
    """Private mounted issuer for current capture and selected historical replay."""

    def __init__(self, runtime: CommonCliExecutionRuntime, first_path: H1FirstPathSources) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native H1 member sources require the canonical common CLI runtime")
        if type(first_path) is not H1FirstPathSources:
            raise TypeError("native H1 member sources require the mounted first-path reader")
        self._runtime = runtime
        self._first_path = first_path
        self._issued: dict[int, H1NativeMemberSourceCut] = {}
        self._current_issued: dict[int, H1CurrentNativeMemberSourceCut] = {}

    def capture_current(self, capture: H1FirstPathCapture) -> H1CurrentNativeMemberSourceCut:
        """Freeze only an exact first-path capture that is current under the gate."""
        native = self._first_path.replay_current_native_cut(capture)
        prepare = select_h1_v3_prepare_for_seal(
            self._runtime, selected_seal=native.source.selected_response_seal
        )
        workspace = reopen_selected_h1_workspace(self._runtime, prepare.prepare)
        occurrences = _occurrences(native, prepare)
        cut = H1CurrentNativeMemberSourceCut(
            capture, native, prepare, workspace.issuance, occurrences
        )
        self._current_issued[id(cut)] = cut
        return cut

    def project_current(
        self, cut: H1CurrentNativeMemberSourceCut
    ) -> tuple[H1NativeMemberSourcePart, ...]:
        """Replay and expose exact source bytes under one identity-held gate."""
        self._require_current_cut(cut)
        with self._runtime._authority_gate().hold():
            self._replay_current_held(cut)
            return _parts(
                cut._occurrences,
                cut._workspace_issuance,
                cut._prepare,
            )

    def replay_current(self, cut: H1CurrentNativeMemberSourceCut) -> H1CurrentFirstPathNativeCut:
        """Reopen an exact issuer-owned current capability under the native gate.

        The nominal cut is the capability: callers cannot construct a usable
        substitute because membership is checked by object identity.  The
        returned carrier remains source material, never a delivery authority.
        """
        with self._runtime._authority_gate().hold():
            return self._replay_current_held(cut)

    def _replay_current_held(
        self, cut: H1CurrentNativeMemberSourceCut
    ) -> H1CurrentFirstPathNativeCut:
        self._runtime._authority_gate().require_held()
        self._require_current_cut(cut)

        fresh = self._first_path.replay_current_native_cut(cut._capture)
        prepare = select_h1_v3_prepare_for_seal(
            self._runtime, selected_seal=fresh.source.selected_response_seal
        )
        workspace = reopen_selected_h1_workspace(self._runtime, prepare.prepare)
        occurrences = _occurrences(fresh, prepare)
        if (
            fresh != cut._native
            or prepare != cut._prepare
            or workspace.issuance != cut._workspace_issuance
            or occurrences != cut._occurrences
        ):
            raise ValueError("native H1 current member source cut is no longer current")
        return fresh

    def _require_current_cut(self, cut: H1CurrentNativeMemberSourceCut) -> None:
        if (
            type(cut) is not H1CurrentNativeMemberSourceCut
            or self._current_issued.get(id(cut)) is not cut
        ):
            raise ValueError("native H1 current member source cut is not issuer-owned")

    def capture(
        self, source: FirstPathCompletionCutV2, *, initialization_envelope_bytes: bytes
    ) -> H1NativeMemberSourceCut:
        """Authenticate selected raw bytes and freeze every native member position."""
        native = self._first_path.replay_selected_native_cut(
            source,
            initialization_envelope_bytes=initialization_envelope_bytes,
        )
        prepare = select_h1_v3_prepare_for_seal(
            self._runtime, selected_seal=native.source.selected_response_seal, historical=True
        )
        workspace = reopen_selected_h1_workspace(self._runtime, prepare.prepare)
        if workspace.issuance != prepare.prepare.issuance_ref:
            raise ValueError("native H1 workspace issuance differs from selected Prepare")
        occurrences = _occurrences(native, prepare)
        cut = H1NativeMemberSourceCut(
            self._first_path, native, prepare, workspace.issuance, occurrences
        )
        self._issued[id(cut)] = cut
        return cut

    def project(self, cut: H1NativeMemberSourceCut) -> tuple[H1NativeMemberSourcePart, ...]:
        """Re-read the exact historical raw cut before exposing source-only parts."""
        if type(cut) is not H1NativeMemberSourceCut or self._issued.get(id(cut)) is not cut:
            raise ValueError("native H1 member source cut is not issuer-owned")
        fresh = self._first_path.replay_selected_native_cut(
            cut._native.source,
            initialization_envelope_bytes=cut._native.initialization.raw_bytes,
        )
        if fresh != cut._native:
            raise ValueError("native H1 member source cut is no longer the selected raw replay")
        return _parts(cut._occurrences, cut._workspace_issuance, cut._prepare)

    def validate_historical(
        self,
        source: FirstPathCompletionCutV2,
        *,
        initialization_envelope_bytes: bytes,
        member_parts: tuple[H1NativeMemberSourcePart, ...],
    ) -> ValidatedH1NativeMemberSources:
        """Replay selected raw initialization and require its complete ordered vector."""
        native = self._first_path.replay_selected_native_cut(
            source,
            initialization_envelope_bytes=initialization_envelope_bytes,
        )
        prepare = select_h1_v3_prepare_for_seal(
            self._runtime, selected_seal=native.source.selected_response_seal, historical=True
        )
        workspace = reopen_selected_h1_workspace(self._runtime, prepare.prepare)
        occurrences = _occurrences(native, prepare)
        expected = _parts(occurrences, workspace.issuance, prepare)
        if member_parts != expected:
            raise ValueError("native H1 member source parts differ from selected raw replay")
        return ValidatedH1NativeMemberSources(native, occurrences)


def _parts(
    occurrences: tuple[H1NativeMemberOccurrence, ...],
    workspace_issuance: H1WorkspaceIssuanceRefV1,
    prepare: H1SelectedPostSealPrepare,
) -> tuple[H1NativeMemberSourcePart, ...]:
    started_run_bytes = prepare.prepare.started_run.canonical_bytes()
    retained_prepare_bytes = prepare.prepare.retained_bytes
    if prepare.prepare.retained.canonical_bytes() != retained_prepare_bytes:
        raise ValueError("native H1 selected Prepare retained bytes are not canonical")
    selected_prepare_artifact_digest = _selected_prepare_artifact_digest(prepare)
    return tuple(
        H1NativeMemberSourcePart(
            occurrence=row,
            workspace_issuance=workspace_issuance,
            selected_prepare_id=prepare.prepare.decision_id,
            selected_prepare_digest=_digest(prepare.prepare.decision_bytes),
            started_run_id=prepare.prepare.started_run.run_id,
            started_run_head=prepare.prepare.started_run.head,
            started_run_canonical_bytes=started_run_bytes,
            started_run_fingerprint=_digest(started_run_bytes),
            selected_prepare_retained_bytes=retained_prepare_bytes,
            selected_prepare_retained_digest=_digest(retained_prepare_bytes),
            selected_prepare_artifact_digest=selected_prepare_artifact_digest,
            source_locator_kind=_locator_kind(row.source_kind),
            artifact_digest=_artifact_digest(row.source_kind, prepare),
        )
        for row in occurrences
    )


def _occurrences(
    native: H1HistoricalFirstPathNativeCut | H1CurrentFirstPathNativeCut,
    prepare: H1SelectedPostSealPrepare,
) -> tuple[H1NativeMemberOccurrence, ...]:
    """Derive all entries from the genuine selected captured Run; never a DTO list."""
    captured = native.source.complete_ordered_run_lineage[-2]
    if captured != prepare.captured_run:
        raise ValueError("native H1 selected captured Run differs from selected Prepare")
    if len(captured.turns) != 1 or len(captured.turns[0].attempts) != 1:
        raise ValueError("native H1 selected Run is not one first attempt")
    turn = captured.turns[0]
    attempt = turn.attempts[0]
    manifest = attempt.manifest
    if (manifest.tenant, manifest.principal, manifest.run_id, manifest.turn_id) != (
        captured.tenant,
        captured.principal,
        captured.run_id,
        turn.turn_id,
    ):
        raise ValueError("native H1 manifest coordinates differ from selected Run")
    manifest_bytes = manifest.canonical_bytes()
    rows: list[H1NativeMemberOccurrence] = []
    for index, member in enumerate(manifest.members):
        kind, source = _member_source(member.surface, prepare.prepare.started_run, prepare)
        member_bytes = member.canonical_bytes()
        _require_native_member(
            kind=kind,
            member=member,
            content=member.content,
            revision_head=member.revision_head,
            provenance_head=member.provenance_head,
            label_head=member.label_head,
            started=prepare.prepare.started_run,
            prepare=prepare,
            artifact=manifest.artifact,
        )
        rows.append(
            H1NativeMemberOccurrence(
                turn_id=turn.turn_id,
                attempt_id=attempt.attempt_id,
                manifest_digest=_digest(manifest_bytes),
                member_index=index,
                member_digest=_digest(member_bytes),
                member_bytes=member_bytes,
                source_kind=kind,
                original_label=member.label,
                provenance_head=member.provenance_head,
                label_head=member.label_head,
                source_bytes=source,
            )
        )
    if not rows:
        raise ValueError("native H1 selected manifest has no members")
    return tuple(rows)


def _require_native_member(
    *,
    kind: str,
    member: VisibilityMember,
    content: str,
    revision_head: str,
    provenance_head: str,
    label_head: str,
    started: ExecutionRunRecord,
    prepare: H1SelectedPostSealPrepare,
    artifact: ExecutionPromptArtifact,
) -> None:
    """Bind each recognized surface to the native source that produced it."""
    if kind == "WORKSPACE":
        try:
            original = VisibilityMember.model_validate_json(prepare.prepare.workspace_member_bytes)
        except ValueError as error:
            raise ValueError("original V2 workspace member is malformed") from error
        if member != original:
            raise ValueError("native H1 workspace member differs from original V2 workspace")
        return
    if kind == "CONTEXT":
        try:
            workspace = VisibilityMember.model_validate_json(prepare.prepare.workspace_member_bytes)
            expected = json.dumps(
                {"prompt": started.prompt, "workspace": workspace.content, "prior_turns": []},
                sort_keys=True,
                separators=(",", ":"),
            )
        except ValueError as error:
            raise ValueError("original V2 workspace member is malformed") from error
        if (
            content != expected
            or revision_head != started.head
            or provenance_head != started.head
            or label_head != started.contour_head
        ):
            raise ValueError("native H1 context member differs from selected started Run")
        return
    rendered = artifact.rendered
    schema = artifact.response_schema_json
    content_hash = artifact.content_hash
    artifact_digest = artifact.digest()
    if kind == "PROMPT":
        if (
            content != rendered
            or revision_head != content_hash
            or provenance_head != content_hash
            or label_head != started.policy_head
        ):
            raise ValueError("native H1 prompt member differs from selected Prepare artifact")
        return
    if kind == "SCHEMA" and (
        content != schema
        or revision_head != artifact_digest
        or provenance_head != artifact_digest
        or label_head != started.policy_head
    ):
        raise ValueError("native H1 schema member differs from selected Prepare artifact")


def _member_source(
    surface: str, started: ExecutionRunRecord, prepare: H1SelectedPostSealPrepare
) -> tuple[str, bytes]:
    if surface == "workspace":
        return "WORKSPACE", prepare.prepare.workspace_member_bytes
    if surface == "context":
        return "CONTEXT", started.canonical_bytes()
    if surface == "prompt":
        return "PROMPT", prepare.prepare.retained_bytes
    if surface == "schema":
        return "SCHEMA", prepare.prepare.retained_bytes
    raise ValueError("native H1 member has an unknown surface")


def _locator_kind(source_kind: str) -> str:
    if source_kind in {"PROMPT", "SCHEMA"}:
        return "PREPARE_ARTIFACT"
    if source_kind == "WORKSPACE":
        return "WORKSPACE_ISSUANCE"
    if source_kind == "CONTEXT":
        return "STARTED_RUN"
    raise ValueError("native H1 member has an unknown source kind")


def _artifact_digest(source_kind: str, prepare: H1SelectedPostSealPrepare) -> str | None:
    if source_kind not in {"PROMPT", "SCHEMA"}:
        return None
    return _selected_prepare_artifact_digest(prepare)


def _selected_prepare_artifact_digest(prepare: H1SelectedPostSealPrepare) -> str:
    manifest = prepare.captured_run.turns[0].attempts[0].manifest
    return manifest.artifact.digest()
