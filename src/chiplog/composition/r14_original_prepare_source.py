"""Authenticated, read-only reopening of one native V3 Prepare.

This source exposes historical preparation facts for later recovery work.  It
does not expose an authority scope, construct a recovery cut, or grant a writer
or broker capability.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionPromptArtifact,
    ExecutionRunRecord,
    ExecutionVisibilityManifest,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputScopeAnchorV1,
    HermeticOutputScopeV1,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_selected_prepare import (
    H1SelectedNativeFanoutPrepare,
    H1SelectedPostSealPrepare,
    reopen_selected_h1_workspace,
    select_h1_v3_prepare_for_native_fanout,
    select_h1_v3_prepare_for_seal,
)
from chiplog.composition.h1_workspace_policy_v2 import (
    H1OriginalWorkspaceIssuanceV2,
    decode_h1_workspace_policy_v2,
)
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1WorkspaceIssuanceRefV1
from chiplog.platform.h1_scope_lineage import AuthenticatedHermeticOutputScope

from .r14_execution_transition_records import RetainedExecutionTransitionV4
from .r14_native_scope_binding import NativePrepareScopeBindingV1, verify_native_prepare_scope


class R14OriginalPrepareSourceError(ValueError):
    """The installed history cannot prove one complete original Prepare."""


@dataclass(frozen=True, slots=True)
class R14OriginalH1OutputScopeProjection:
    """Historical H1 local-commentary context; this grants no authority."""

    policy_anchor: HermeticOutputScopeAnchorV1
    policy_scope_ref: ExactHead
    policy_bytes: bytes
    scope_decision_bytes: bytes
    scope_record_bytes: bytes
    scope_bytes: bytes
    scope: HermeticOutputScopeV1


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
    descriptive_original_h1_output_scope: R14OriginalH1OutputScopeProjection | None
    original_native_scope_binding: NativePrepareScopeBindingV1 | None = None


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
                selected = select_h1_v3_prepare_for_native_fanout(
                    self._runtime, selected_seal=selected_seal
                )
                if selected is None:
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
        selected: H1SelectedPostSealPrepare | H1SelectedNativeFanoutPrepare,
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
        descriptive_scope = self._descriptive_scope_held(
            issuance=issuance,
            issuance_bytes=issuance_bytes,
            run=proposal.run,
            started_worker_session=prepare.started_run.worker_session,
            manifest=manifest,
            gate=self._runtime._authority_gate(),
        )
        native_binding = None
        if isinstance(retained, RetainedExecutionTransitionV4):
            native_binding = retained.native_scope_binding
            verify_native_prepare_scope(self._runtime, request, native_binding, historical=True)
            calls = selected.sealed_run.turns[0].initialized_calls
            attempt = selected.captured_run.turns[0].attempts[0]
            if attempt.response_base64 is None or calls is None or len(calls) != 1:
                raise R14OriginalPrepareSourceError(
                    "native scope requires its sole initialized call"
                )
            raw = base64.b64decode(attempt.response_base64, validate=True)
            response = json.loads(raw)
            tool_calls = response.get("tool_calls")
            if (
                response.get("kind") != "Continue"
                or not isinstance(tool_calls, list)
                or len(tool_calls) != 1
                or tool_calls[0].get("tool") != "request_self_effect"
            ):
                raise R14OriginalPrepareSourceError("native scope has a rival or non-native call")
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
            descriptive_original_h1_output_scope=descriptive_scope,
            original_native_scope_binding=native_binding,
        )

    def _descriptive_scope_held(
        self,
        *,
        issuance: object,
        issuance_bytes: bytes,
        run: ExecutionRunRecord,
        started_worker_session: str,
        manifest: ExecutionVisibilityManifest,
        gate: object,
    ) -> R14OriginalH1OutputScopeProjection | None:
        if type(issuance) is not H1OriginalWorkspaceIssuanceV2:
            return None
        try:
            policy_bytes = base64.b64decode(
                issuance.sources.policy_payload_base64.encode("ascii"), validate=True
            )
        except (ValueError, binascii.Error) as error:
            raise R14OriginalPrepareSourceError(
                "selected V2 policy payload is malformed"
            ) from error
        policy = decode_h1_workspace_policy_v2(policy_bytes)
        registration = policy.registration
        reader = self._runtime._h1_historical_trust_reader()
        if getattr(reader, "authority_gate", None) is not gate:
            raise R14OriginalPrepareSourceError(
                "historical trust reader has a different authority gate"
            )
        anchor = registration.output_scope_anchor
        record = reader.historical_record(anchor.decision.head, anchor.record_ordinal)
        try:
            envelope = json.loads(record.envelope_bytes)
            materialized = json.loads(record.record_bytes)
            raw_scope = materialized["scope"]
            scope_bytes = json.dumps(raw_scope, sort_keys=True, separators=(",", ":")).encode()
            scope = HermeticOutputScopeV1.model_validate_json(scope_bytes)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise R14OriginalPrepareSourceError(
                "historical H1 scope record is malformed"
            ) from error
        authenticated = AuthenticatedHermeticOutputScope(
            decision_id=record.physical_decision_id,
            decision_bytes=record.envelope_bytes,
            record_ordinal=record.record_ordinal,
            record_bytes=record.record_bytes,
            scope=scope,
        )
        if (
            issuance_bytes != issuance.canonical_bytes()
            or not isinstance(envelope, dict)
            or envelope.get("kind") != "HERMETIC_OUTPUT_SCOPE_V1"
            or not isinstance(envelope.get("payload"), dict)
            or set(envelope["payload"]) != {"scope"}
            or envelope["payload"]["scope"] != raw_scope
            or record.record_ordinal != 1
            or not isinstance(materialized, dict)
            or materialized.get("decision_id") != record.physical_decision_id
            or materialized.get("operation_kind") != "HERMETIC_OUTPUT_SCOPE_V1"
            or materialized.get("record_type_id")
            != "chiplog.deployment_trust.hermetic_output_scope"
            or materialized.get("schema_id") != "chiplog.deployment_trust.record.v1"
            or scope.canonical_bytes() != scope_bytes
            or authenticated.anchor != anchor
        ):
            raise R14OriginalPrepareSourceError("historical H1 scope differs from its full anchor")
        scope_digest = hashlib.sha256(scope_bytes).hexdigest()
        scope_ref = ExactHead(
            identity=scope.scope_id,
            head=scope.scope_id + "/" + scope_digest,
            fingerprint=scope_digest,
        )
        try:
            accepted_policy_bytes = base64.b64decode(
                registration.accepted_policy_bytes_base64.encode("ascii"), validate=True
            )
        except (ValueError, binascii.Error) as error:
            raise R14OriginalPrepareSourceError(
                "selected V2 accepted policy payload is malformed"
            ) from error
        cli_state = scope.authenticated_cli_state
        if cli_state is None:
            raise R14OriginalPrepareSourceError("historical H1 scope lacks CLI state")
        if (
            registration.output_scope_ref != scope_ref
            or scope.disclosure_policy.ref != registration.accepted_policy
            or scope.disclosure_policy.canonical_source_bytes != accepted_policy_bytes
            or scope.admitted_authentication != registration.admitted_authentication_ref
            or scope.selected_resource_observation_ref
            != registration.selected_resource_observation_ref
            or scope.worker_session_id != issuance.worker_session
            or scope.worker_session_id != started_worker_session
            or scope.worker_session_id != manifest.worker_session
            or scope.tenant_id != policy.tenant
            or scope.tenant_id != run.tenant
            or scope.principal_id != policy.principal
            or scope.principal_id != run.principal
            or scope.recipient.recipient_id != registration.origin_recipient_id
            or scope.database_id != policy.database
            or scope.database_id != registration.database_id
            or scope.contour_head != policy.heads.contour
            or cli_state.credential_head != policy.heads.credential
            or cli_state.session_head != policy.heads.session
        ):
            raise R14OriginalPrepareSourceError(
                "historical H1 scope differs from V2 policy context"
            )
        return R14OriginalH1OutputScopeProjection(
            policy_anchor=anchor,
            policy_scope_ref=registration.output_scope_ref,
            policy_bytes=policy_bytes,
            scope_decision_bytes=record.envelope_bytes,
            scope_record_bytes=record.record_bytes,
            scope_bytes=scope_bytes,
            scope=scope,
        )


__all__ = [
    "R14OriginalH1OutputScopeProjection",
    "R14OriginalNativePrepareProjection",
    "R14OriginalNativePrepareSource",
    "R14OriginalPrepareSourceError",
]
