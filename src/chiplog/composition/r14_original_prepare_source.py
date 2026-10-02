"""Authenticated, read-only reopening of one native V3 Prepare.

This source exposes historical preparation facts for later recovery work.  It
does not expose an authority scope, construct a recovery cut, or grant a writer
or broker capability.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionPromptArtifact,
    ExecutionVisibilityManifest,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_selected_prepare import (
    H1SelectedPostSealPrepare,
    reopen_selected_h1_workspace,
    select_h1_v3_prepare_for_seal,
)
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1WorkspaceIssuanceRefV1


class R14OriginalPrepareSourceError(ValueError):
    """The installed history cannot prove one complete original Prepare."""


@dataclass(frozen=True, slots=True)
class R14OriginalNativePrepareProjection:
    """Exact historical preparation facts; none of these fields authorize work."""

    tenant_id: str
    database_identity: tuple[str, int, int]
    selected_response_seal: CallSubjectHead
    selected_prepare_decision_id: str
    selected_prepare_decision_bytes: bytes
    selected_prepare_decision_fingerprint: str
    retained_prepare_bytes: bytes
    prepared_run: CallSubjectHead
    prepared_run_bytes: bytes
    workspace_issuance: H1WorkspaceIssuanceRefV1
    workspace_issuance_bytes: bytes
    manifest: ExecutionVisibilityManifest
    artifact: ExecutionPromptArtifact
    artifact_bytes: bytes
    rendered_prompt_bytes: bytes
    generated_schema_bytes: bytes
    ordered_tool_spec_bytes: tuple[bytes, ...]
    policy_bytes: bytes


class R14OriginalNativePrepareSource:
    """Reopen one selected native V3 Prepare through the installed runtime only."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("original Prepare source requires the installed CLI runtime")
        self._runtime = runtime

    def collect(self, selected_seal: CallSubjectHead) -> R14OriginalNativePrepareProjection | None:
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError("original Prepare source requires an exact response-seal locator")
        gate = self._runtime._authority_gate()
        try:
            with gate.hold():
                self._runtime._check_database_identity()
                database_identity = self._runtime._database_identity
                selected = select_h1_v3_prepare_for_seal(
                    self._runtime, selected_seal=selected_seal, historical=True
                )
                projection = self._project_held(
                    selected_seal, selected, database_identity=database_identity
                )
                self._runtime._check_database_identity()
                if self._runtime._database_identity != database_identity:
                    raise R14OriginalPrepareSourceError(
                        "database identity changed during original Prepare read"
                    )
                return projection
        except OSError, RuntimeError, TypeError, ValueError:
            return None

    def _project_held(
        self,
        selected_seal: CallSubjectHead,
        selected: H1SelectedPostSealPrepare,
        *,
        database_identity: tuple[str, int, int],
    ) -> R14OriginalNativePrepareProjection:
        prepare = selected.prepare
        retained = prepare.retained
        request, proposal = retained.request, retained.proposal
        if (
            prepare.retained_bytes != retained.canonical_bytes()
            or prepare.decision_bytes == b""
            or prepare.physical_run.canonical_bytes != proposal.run.canonical_bytes()
            or prepare.physical_run.record_id != proposal.run.head
            or hashlib.sha256(prepare.physical_run.canonical_bytes).hexdigest()
            != prepare.physical_run.fingerprint
            or request.run.run_id != proposal.run.run_id
            or selected.captured_run.run_id != proposal.run.run_id
            or prepare.started_run.policy != proposal.run.policy
        ):
            raise R14OriginalPrepareSourceError("selected Prepare differs from its physical Run")
        if len(selected.captured_run.turns) != 1 or len(proposal.run.turns) != 1:
            raise R14OriginalPrepareSourceError("selected Prepare has no one-turn native lineage")
        captured_turn = selected.captured_run.turns[0]
        proposal_turn = proposal.run.turns[0]
        if (
            len(captured_turn.attempts) != 1
            or captured_turn.selector != 0
            or len(proposal_turn.attempts) != 1
            or proposal_turn.selector != 0
        ):
            raise R14OriginalPrepareSourceError("selected Prepare has no sole selected attempt")
        manifest = captured_turn.attempts[0].manifest
        if manifest != request.manifest or manifest != proposal_turn.attempts[0].manifest:
            raise R14OriginalPrepareSourceError(
                "selected Prepare manifest differs from physical lineage"
            )
        members = manifest.members
        if (
            len(members) != 4
            or tuple(member.surface for member in members)
            != ("workspace", "context", "prompt", "schema")
            or len({member.record_id for member in members}) != len(members)
        ):
            raise R14OriginalPrepareSourceError("selected Prepare member vector is incomplete")
        workspace, _context, prompt, schema = members
        artifact = manifest.artifact
        if (
            prompt.content != artifact.rendered
            or prompt.revision_head != artifact.content_hash
            or schema.content != artifact.response_schema_json
            or schema.revision_head != artifact.digest()
            or workspace.model_dump_json().encode() != prepare.workspace_member_bytes
        ):
            raise R14OriginalPrepareSourceError("selected Prepare artifact members differ")
        closure = reopen_selected_h1_workspace(self._runtime, prepare)
        issuance = (
            R13Workspace(self._runtime).open_h1_workspace_issuance().load(prepare.issuance_ref)
        )
        issuance_bytes = issuance.canonical_bytes()
        if (
            closure.issuance != prepare.issuance_ref
            or issuance.workspace_member_json.encode() != prepare.workspace_member_bytes
            or issuance.proposal_context_json.encode() != prepare.proposal_context_bytes
        ):
            raise R14OriginalPrepareSourceError("selected Prepare workspace issuance differs")
        digest = hashlib.sha256(proposal.run.canonical_bytes()).hexdigest()
        return R14OriginalNativePrepareProjection(
            tenant_id=proposal.run.tenant,
            database_identity=database_identity,
            selected_response_seal=selected_seal,
            selected_prepare_decision_id=prepare.decision_id,
            selected_prepare_decision_bytes=prepare.decision_bytes,
            selected_prepare_decision_fingerprint=hashlib.sha256(
                prepare.decision_bytes
            ).hexdigest(),
            retained_prepare_bytes=prepare.retained_bytes,
            prepared_run=CallSubjectHead(
                subject_id=proposal.run.run_id,
                revision=Present(head=proposal.run.head, fingerprint=digest),
            ),
            prepared_run_bytes=proposal.run.canonical_bytes(),
            workspace_issuance=prepare.issuance_ref,
            workspace_issuance_bytes=issuance_bytes,
            manifest=manifest,
            artifact=artifact,
            artifact_bytes=artifact.canonical_bytes(),
            rendered_prompt_bytes=artifact.rendered.encode(),
            generated_schema_bytes=artifact.response_schema_json.encode(),
            ordered_tool_spec_bytes=tuple(tool.canonical_bytes() for tool in artifact.tools),
            policy_bytes=prepare.started_run.policy.canonical_bytes(),
        )


__all__ = [
    "R14OriginalNativePrepareProjection",
    "R14OriginalNativePrepareSource",
    "R14OriginalPrepareSourceError",
]
