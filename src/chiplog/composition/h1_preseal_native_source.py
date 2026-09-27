"""Issuer-owned native H1 facts captured before a V2 seal is selected.

This source deliberately has no response-seal locator and no wire DTO.  It
holds a live, pre-seal capability that P/E may later bind at the V2 decision
admission seam.  It neither authorizes that decision nor reconstructs a
post-seal source.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import RLock

from chiplog.capabilities.agent_loop.contracts import DisclosureLabel, VisibilityMember
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionPromptArtifact,
    ExecutionRunRecord,
)

from . import h1_preseal
from .common_cli_execution_runtime import CommonCliExecutionRuntime
from .h1_owner_inventory import read_h1_scoped_owner_inventory
from .h1_preseal_contracts import H1V2SealPreflight
from .h1_selected_prepare import reopen_selected_h1_workspace, select_h1_v3_prepare_for_candidate


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class H1PresealNativeOccurrence:
    """One ordered native manifest occurrence, before any E projection exists."""

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
class H1PresealNativeSourceCut:
    """Private issuer-owned pre-seal source material; it has no authority by itself."""

    _preflight: H1V2SealPreflight
    _occurrences: tuple[H1PresealNativeOccurrence, ...]


class H1PresealNativeSource:
    """Live issuer for an exact native occurrence vector before V2 sealing."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("preseal native source requires the canonical common CLI runtime")
        self._runtime = runtime
        self._closed = False
        self._issued: dict[int, H1PresealNativeSourceCut] = {}
        self._lock = RLock()

    def capture(self, preflight: H1V2SealPreflight) -> H1PresealNativeSourceCut:
        """Capture exact V3-native facts from a live, runtime-issued preflight only."""
        with self._runtime._authority_gate().hold(), self._lock:
            self._require_open()
            self._require_preflight(preflight)
            fresh = _read_preflight(self._runtime, preflight.captured_run)
            if fresh != preflight:
                raise ValueError("H1 preseal native source is not current")
            cut = H1PresealNativeSourceCut(preflight, _occurrences(fresh))
            self._issued[id(cut)] = cut
            return cut

    def replay(self, cut: H1PresealNativeSourceCut) -> tuple[H1PresealNativeOccurrence, ...]:
        """Recheck the exact pre-seal sources before exposing their ordered facts."""
        with self._runtime._authority_gate().hold(), self._lock:
            self._require_open()
            if type(cut) is not H1PresealNativeSourceCut or self._issued.get(id(cut)) is not cut:
                raise ValueError("H1 preseal native source cut is not issuer-owned")
            self._require_preflight(cut._preflight)
            fresh = _read_preflight(self._runtime, cut._preflight.captured_run)
            if fresh != cut._preflight or _occurrences(fresh) != cut._occurrences:
                raise ValueError("H1 preseal native source cut is no longer current")
            return cut._occurrences

    def revoke(self) -> None:
        """Permanently burn every private capability issued by this source."""
        with self._runtime._authority_gate().hold(), self._lock:
            self._closed = True
            self._issued.clear()

    def close(self) -> None:
        """Release this issuer's private capabilities; safe to call repeatedly."""
        self.revoke()

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("H1 preseal native source is closed and revoked")

    def _require_preflight(self, preflight: H1V2SealPreflight) -> None:
        if type(preflight) is not H1V2SealPreflight:
            raise ValueError("H1 preseal native source requires a runtime-issued preflight")
        issued = h1_preseal._issued.get(id(preflight))
        if issued is None or issued[0] is not self._runtime or issued[1] is not preflight:
            raise ValueError("H1 preseal native source requires a runtime-issued preflight")


def _read_preflight(
    runtime: CommonCliExecutionRuntime, captured: ExecutionRunRecord
) -> H1V2SealPreflight:
    """Rebuild all live source joins; this is intentionally independent of a seal."""
    prepare = select_h1_v3_prepare_for_candidate(runtime, captured, expected_head=captured.head)
    workspace = reopen_selected_h1_workspace(runtime, prepare)
    inventory = read_h1_scoped_owner_inventory(
        runtime,
        captured=captured,
        selected_prepare=prepare,
        workspace=workspace,
        phase="PRE_SEAL",
    )
    return H1V2SealPreflight(captured, prepare, workspace, inventory, runtime.current_worker())


def _occurrences(preflight: H1V2SealPreflight) -> tuple[H1PresealNativeOccurrence, ...]:
    """Derive every occurrence directly from the genuine captured Run manifest."""
    captured = preflight.captured_run
    prepare = preflight.prepare
    if type(captured) is not ExecutionRunRecord:
        raise ValueError("H1 preseal captured Run differs from the selected Prepare")
    if len(captured.turns) != 1 or len(captured.turns[0].attempts) != 1:
        raise ValueError("H1 preseal captured Run is not one first attempt")
    turn = captured.turns[0]
    attempt = turn.attempts[0]
    manifest = attempt.manifest
    if (manifest.tenant, manifest.principal, manifest.run_id, manifest.turn_id) != (
        captured.tenant,
        captured.principal,
        captured.run_id,
        turn.turn_id,
    ):
        raise ValueError("H1 preseal manifest coordinates differ from captured Run")
    manifest_bytes = manifest.canonical_bytes()
    rows: list[H1PresealNativeOccurrence] = []
    for index, member in enumerate(manifest.members):
        kind, source = _member_source(member.surface, prepare.started_run, preflight)
        member_bytes = member.canonical_bytes()
        _require_native_member(
            kind=kind,
            member=member,
            started=prepare.started_run,
            preflight=preflight,
            artifact=manifest.artifact,
        )
        rows.append(
            H1PresealNativeOccurrence(
                turn.turn_id,
                attempt.attempt_id,
                _digest(manifest_bytes),
                index,
                _digest(member_bytes),
                member_bytes,
                kind,
                member.label,
                member.provenance_head,
                member.label_head,
                source,
            )
        )
    if not rows:
        raise ValueError("H1 preseal native manifest has no members")
    return tuple(rows)


def _member_source(
    surface: str, started: ExecutionRunRecord, preflight: H1V2SealPreflight
) -> tuple[str, bytes]:
    if surface == "workspace":
        return "WORKSPACE", preflight.prepare.workspace_member_bytes
    if surface == "context":
        return "CONTEXT", started.canonical_bytes()
    if surface in {"prompt", "schema"}:
        return surface.upper(), preflight.prepare.retained_bytes
    raise ValueError("H1 preseal native member has an unknown surface")


def _require_native_member(
    *,
    kind: str,
    member: VisibilityMember,
    started: ExecutionRunRecord,
    preflight: H1V2SealPreflight,
    artifact: ExecutionPromptArtifact,
) -> None:
    """Bind each manifest member to its exact pre-seal native producer."""
    if kind == "WORKSPACE":
        try:
            original = VisibilityMember.model_validate_json(
                preflight.prepare.workspace_member_bytes
            )
        except ValueError as error:
            raise ValueError("H1 preseal original workspace member is malformed") from error
        if member != original:
            raise ValueError("H1 preseal workspace member differs from original workspace")
        return
    if kind == "CONTEXT":
        try:
            workspace = VisibilityMember.model_validate_json(
                preflight.prepare.workspace_member_bytes
            )
            expected = json.dumps(
                {"prompt": started.prompt, "workspace": workspace.content, "prior_turns": []},
                sort_keys=True,
                separators=(",", ":"),
            )
        except ValueError as error:
            raise ValueError("H1 preseal original workspace member is malformed") from error
        if (
            member.content != expected
            or member.revision_head != started.head
            or member.provenance_head != started.head
            or member.label_head != started.contour_head
        ):
            raise ValueError("H1 preseal context member differs from prepared Run")
        return
    if kind == "PROMPT":
        if (
            member.content != artifact.rendered
            or member.revision_head != artifact.content_hash
            or member.provenance_head != artifact.content_hash
            or member.label_head != started.policy_head
        ):
            raise ValueError("H1 preseal prompt member differs from selected Prepare artifact")
        return
    if kind == "SCHEMA" and (
        member.content != artifact.response_schema_json
        or member.revision_head != artifact.digest()
        or member.provenance_head != artifact.digest()
        or member.label_head != started.policy_head
    ):
        raise ValueError("H1 preseal schema member differs from selected Prepare artifact")


__all__ = ["H1PresealNativeOccurrence", "H1PresealNativeSource", "H1PresealNativeSourceCut"]
