"""Mounted, runtime-private issuer for H1 pre-workspace selections.

The public H1 facade can only recognise capabilities minted here.  This module
does not open a database, a custody directory, or an installed launch: those
are lifetime responsibilities of the installed-runtime context manager.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Never, cast

from chiplog.capabilities.agent_loop.contracts import LoopRejected, VisibilityMember
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.agent_loop.delivery_preparation import (
    DeliveryObservation,
    HistoricalEnvelope,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_transition_contracts import PrepareExecutionRequest
from chiplog.capabilities.agent_loop.recovery_contracts import NonSchedulerFence, NotApplicable
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCandidateCallV1,
    H1OwnerCandidateV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
    H1RetainedSelectedWrapperV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    HermeticOutputPolicyV1,
    HermeticOutputScopeV1,
    HermeticTrustObservationV1,
    IssuedHermeticOutputScopeV1,
    IssueHermeticOutputScopeV1,
    ReadCurrentHermeticExecutionScopeV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.capabilities.effects.fences import NonSchedulerFence as EffectsNonSchedulerFence
from chiplog.capabilities.effects.h1_local_preparation_contracts import (
    H1LocalCommentaryOwnerCallV1,
    H1SelectedScopeSourceV1,
)
from chiplog.capabilities.evidence_journal.commands import TrustedIngress
from chiplog.capabilities.projections.workspace_boundary import SourceReference
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    _H1ScopeAppendReceipt,
    _H1ScopeWire,
)
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from chiplog.composition.h1_first_path_sources import H1FirstPathCapture, H1FirstPathSources
from chiplog.composition.h1_launch_enrollment import InstalledH1Launch
from chiplog.composition.h1_native_member_sources import (
    H1CurrentNativeMemberSourceCut,
    H1NativeMemberSources,
)
from chiplog.composition.h1_preissuance_registration import (
    H1AuthenticatedCompletionInputs,
    H1PreissuanceSelection,
    H1PreissuanceSourceViolation,
    H1VerifiedOriginalWorkspaceIssuance,
    _issue_selection_for_authenticated_port,
    _issue_verified_original_for_authenticated_port,
    _selection_state,
)
from chiplog.composition.h1_preseal_contracts import H1SelectedPrepare, H1V2SealPreflight
from chiplog.composition.h1_preseal_native_source import (
    H1PresealNativeSource,
    H1PresealNativeSourceCut,
)
from chiplog.composition.h1_preseal_p_scope_wires import decode_h1_preseal_p_scope_wires
from chiplog.composition.h1_preseal_pe_anchor_records import (
    decode_h1_preseal_pe_anchor_record,
)
from chiplog.composition.h1_selected_output_sources import (
    H1SelectedOutputCapture,
    H1SelectedOutputSources,
)
from chiplog.composition.h1_selected_prepare import (
    _decode_v3_prepare,
    _physical_run,
    _selected_commands,
    reopen_selected_h1_workspace,
    select_h1_v3_prepare_for_seal,
)
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.h1_workspace_policy_v2 import (
    H1OriginalWorkspaceIssuanceV2,
    H1PreissuanceRegistrationV1,
    H1WorkspacePolicyHeadsV1,
    H1WorkspacePolicyV2,
    decode_h1_workspace_policy_v2,
)
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_execution_inbox_records import RetainedInboxExecutionInitialization
from chiplog.composition.r14_execution_transition_records import transition_command
from chiplog.composition.r14_h1_workspace_issuance import verify_h1_original_workspace
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1WorkspaceIssuanceRefV1
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r16_dispatch_registry import ResourceObservation
from chiplog.composition.r17_authenticated_records import decode_authentication
from chiplog.domain_primitives import PrincipalId, TenantId
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_trust import decode_trust_owner_call_canonical

if TYPE_CHECKING:
    from chiplog.composition.h1_live_completion_enrollment import _H1TerminalClearance

_CHANNEL = "hermetic-local"


@dataclass(frozen=True, slots=True)
class _PreparedCut:
    run: ExecutionRunRecord
    source: H1SelectedOutputCapture
    custody_digest: str
    custody_generation: int
    trust: HermeticTrustObservationV1


@dataclass(frozen=True, slots=True)
class _SelectionCut:
    prepared: _PreparedCut
    issued: IssuedHermeticOutputScopeV1
    current: CurrentHermeticExecutionScopeV1
    scope: HermeticOutputScopeV1
    append_receipt: _H1ScopeAppendReceipt
    scope_issue_wire: _H1ScopeWire | None = None
    scope_current_wire: _H1ScopeWire | None = None
    workspace_policy: H1WorkspacePolicyV2 | None = None


@dataclass(frozen=True, slots=True)
class _OriginalCut:
    """Issuer-held binding of a physical V2 original to its current-scope read."""

    selection: H1PreissuanceSelection
    cut: _SelectionCut
    selected: H1SelectedPrepare
    read: ReadCurrentHermeticExecutionScopeV1
    current: CurrentHermeticExecutionScopeV1


class _AcceptedH1CompletionScopeCapability:
    """P-owned source-continuity token, never completion/current authority."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 completion-scope capabilities are issued only by the P owner")

    def __copy__(self) -> Never:
        raise TypeError("H1 completion-scope capabilities cannot be copied")

    def __deepcopy__(self, memo: object) -> Never:
        del memo
        raise TypeError("H1 completion-scope capabilities cannot be copied")

    def __reduce__(self) -> Never:
        raise TypeError("H1 completion-scope capabilities cannot be serialized")


@dataclass(frozen=True, slots=True)
class _AcceptedH1CompletionScopeProjection:
    """Inert source material; the later async fence must establish currentness."""

    scope: HermeticOutputScopeV1
    recipient: ProviderRecipient
    policy_bytes: bytes
    scope_ref: ExactHead
    scope_bytes: bytes
    policy_ref: ExactHead
    custody_entry_generation: int
    custody_entry_digest: str
    source_signature_digest: str


class _AcceptedH1PresealScopeCapability:
    """P-owned, process-local facts captured before a V2 response seal.

    It is deliberately only a source capability.  In particular it does not
    grant a caller a remote-CURRENT lease or a DECIDED admission.
    """

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 preseal scope capabilities are issued only by the P owner")

    def __copy__(self) -> Never:
        raise TypeError("H1 preseal scope capabilities cannot be copied")

    def __deepcopy__(self, memo: object) -> Never:
        del memo
        raise TypeError("H1 preseal scope capabilities cannot be copied")

    def __reduce__(self) -> Never:
        raise TypeError("H1 preseal scope capabilities cannot be serialized")


@dataclass(frozen=True, slots=True)
class _AcceptedH1PresealScopeProjection:
    """Inert P facts; final DECIDED eligibility needs the outer gate replay."""

    scope: HermeticOutputScopeV1
    recipient: ProviderRecipient
    policy_bytes: bytes
    scope_ref: ExactHead
    scope_bytes: bytes
    policy_ref: ExactHead
    custody_entry_generation: int
    custody_entry_digest: str
    source_signature_digest: str


@dataclass(frozen=True, slots=True)
class _PresealScopeCut:
    capability: _AcceptedH1PresealScopeCapability
    native: H1PresealNativeSourceCut
    preflight: H1V2SealPreflight
    selection: H1PreissuanceSelection
    cut: _SelectionCut
    scope: HermeticOutputScopeV1
    current: CurrentHermeticExecutionScopeV1
    trust: HermeticTrustObservationV1
    source: H1SelectedOutputCapture
    custody_digest: str
    custody_generation: int
    current_wire: _H1ScopeWire


@dataclass(frozen=True, slots=True)
class _AuthenticatedConversationPolicyInputs:
    """Inert P-authenticated policy material for the A conversation reader.

    This is deliberately a private, synchronous projection.  It preserves the
    original V2 issuance coordinates alongside the distinct scope disclosure
    policy so A cannot substitute one policy for the other.
    """

    workspace_policy: H1WorkspacePolicyV2
    recipient: ProviderRecipient
    scope_policy_ref: ExactHead
    scope_policy_bytes: bytes
    custody_entry_generation: int
    custody_entry_digest: str
    original_issuance_ref: H1WorkspaceIssuanceRefV1
    original_issuance_bytes: bytes
    original_tenant: str
    original_run_id: str
    original_started_run_head: str
    original_turn_id: str
    original_worker_session: str


@dataclass(frozen=True, slots=True)
class _AuthenticatedCompletionEffectsSource:
    """P-authenticated, inert input material for B's one effects-owner call."""

    selected_scope: H1SelectedScopeSourceV1
    retained_origin: H1RetainedSelectedWrapperV1
    fence: EffectsNonSchedulerFence


class _HistoricalRecoverySourceCapability:
    """Runtime-private handle for one retained V2 P reconstruction."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("historical P recovery sources are issued only by the P owner")

    def __copy__(self) -> Never:
        raise TypeError("historical P recovery sources cannot be copied")

    def __deepcopy__(self, memo: object) -> Never:
        del memo
        raise TypeError("historical P recovery sources cannot be copied")

    def __reduce__(self) -> Never:
        raise TypeError("historical P recovery sources cannot be serialized")


@dataclass(frozen=True, slots=True)
class _HistoricalRecoverySourceState:
    capability: _HistoricalRecoverySourceCapability
    original_identity: object
    original_fingerprint: str
    selected_seal: object
    conversation_bytes: bytes
    effects_bytes: bytes


@dataclass(frozen=True, slots=True)
class _HistoricalEffectsSource:
    """P-only historical effects inputs; E supplies its separate worker fence."""

    selected_scope: H1SelectedScopeSourceV1
    retained_origin: H1RetainedSelectedWrapperV1


class _HistoricalRecoverySourceUnsupported(H1PreissuanceSourceViolation):
    """A selected legacy V2 decision lacks a required immutable P residual."""


class _HistoricalRecoverySourceIntegrity(H1PreissuanceSourceViolation):
    """Retained historical P evidence is present but cannot be authenticated."""


@dataclass(frozen=True, slots=True)
class _H1PreterminalScopeSnapshot:
    session: object
    first_path: H1FirstPathCapture
    native_cap: H1CurrentNativeMemberSourceCut
    scope_cap: object
    delivery_receipt: object
    effects_exchange: object
    effects_source: _AuthenticatedCompletionEffectsSource
    terminal_sent: PublicPortCall | None
    terminal_call_fingerprint: str | None
    terminal_owner_frame_bytes: bytes | None


class _H1PreterminalCompletionProof:
    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 preterminal proofs are issued only by the P owner")


@dataclass(slots=True)
class _IssuedPreterminalCompletionProof:
    proof: _H1PreterminalCompletionProof
    session: object
    enrollment: object
    cut: object
    snapshot: _H1PreterminalScopeSnapshot
    wire: _H1ScopeWire
    consumed: bool = False


class _H1FinalCompletionFence:
    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 final completion fences are issued only by the P owner")

    def __copy__(self) -> Never:
        raise TypeError("H1 final completion fences cannot be copied")

    def __deepcopy__(self, memo: object) -> Never:
        del memo
        raise TypeError("H1 final completion fences cannot be copied")

    def __reduce__(self) -> Never:
        raise TypeError("H1 final completion fences cannot be serialized")


@dataclass(slots=True)
class _IssuedFinalCompletionFence:
    fence: _H1FinalCompletionFence
    session: object
    snapshot: _H1PreterminalScopeSnapshot
    clearance: object
    wire: _H1ScopeWire
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class _CompletionScopeCut:
    capability: _AcceptedH1CompletionScopeCapability
    first_path: H1FirstPathCapture
    native_cap: H1CurrentNativeMemberSourceCut
    native: object
    original: H1VerifiedOriginalWorkspaceIssuance
    original_cut: _OriginalCut
    scope: HermeticOutputScopeV1
    current: CurrentHermeticExecutionScopeV1
    trust: HermeticTrustObservationV1
    source: H1SelectedOutputCapture
    custody_digest: str
    custody_generation: int
    scope_issue_wire: _H1ScopeWire
    scope_current_wire: _H1ScopeWire


class _H1PreRequestDeliveryReceipt:
    """P-owned, process-local binding of the complete delivery source cut."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 delivery receipts are issued only by the P owner")

    def __copy__(self) -> Never:
        raise TypeError("H1 delivery receipts cannot be copied")

    def __deepcopy__(self, memo: object) -> Never:
        del memo
        raise TypeError("H1 delivery receipts cannot be copied")

    def __reduce__(self) -> Never:
        raise TypeError("H1 delivery receipts cannot be serialized")


@dataclass(slots=True)
class _IssuedPreRequestDelivery:
    receipt: _H1PreRequestDeliveryReceipt
    first_path: H1FirstPathCapture
    native_cap: H1CurrentNativeMemberSourceCut
    scope_cap: object
    member_receipt: object
    worker_receipt: object
    observation: DeliveryObservation
    fence: NonSchedulerFence
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class _H1WorkspaceIdentity:
    """Inert selected profile for the private W composition seam."""

    tenant: str
    principal: str
    credential_head: str
    session_head: str
    contour_head: str
    database_id: str
    channel: str


class _H1RuntimePreissuancePort:
    """The one issuer identity for a live installed H1 runtime.

    Construction is deliberately strict.  In particular an inert P0 registry,
    an arbitrary runtime subclass, or a launch constructed from database state
    cannot be substituted for the installed launch capability.
    """

    __slots__ = (
        "_completion_scopes",
        "_custody",
        "_cuts",
        "_delivery_receipts",
        "_final_completion_fences",
        "_gate",
        "_historical_recovery_sources",
        "_launch",
        "_originals",
        "_preseal_scopes",
        "_preterminal_clearances",
        "_preterminal_proofs",
        "_reopened",
        "_runtime",
    )

    def __init__(self, runtime: CommonCliExecutionRuntime, launch: InstalledH1Launch) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 preissuance requires the canonical common CLI runtime")
        if type(launch) is not InstalledH1Launch:
            raise TypeError("H1 preissuance requires an installed H1 launch")
        gate = runtime._authority_gate()
        resources = runtime._require_dispatch_resources()
        if resources._require_gate() is not gate:
            raise ValueError("H1 preissuance requires the runtime resource authority gate")
        launch.assert_current()
        custody = launch.custody
        registry = custody._registry
        slot = launch._slot
        installed_observed = slot._trust.verify()
        if (
            installed_observed is None
            or registry.deployment_id != slot.deployment_id
            or registry.database_id != slot.database_id
            or registry.database_genesis_digest != installed_observed.genesis_head
        ):
            raise ValueError("H1 preissuance installed launch and custody genesis differ")
        observed = runtime._trust.verify()
        if (
            observed is None
            or observed.phase != "ACTIVE"
            or (observed.tenant_id, observed.database_instance_id, observed.genesis_head)
            != (slot.tenant_id, slot.database_id, registry.database_genesis_digest)
        ):
            raise ValueError("H1 preissuance runtime trust differs from installed launch")
        self._runtime, self._launch, self._custody, self._gate = runtime, launch, custody, gate
        self._cuts: dict[int, tuple[H1PreissuanceSelection, _SelectionCut]] = {}
        self._originals: dict[int, tuple[H1VerifiedOriginalWorkspaceIssuance, _OriginalCut]] = {}
        self._completion_scopes: dict[
            int, tuple[_AcceptedH1CompletionScopeCapability, _CompletionScopeCut]
        ] = {}
        self._delivery_receipts: dict[int, _IssuedPreRequestDelivery] = {}
        self._preterminal_clearances: dict[
            int, tuple[object, _H1PreterminalScopeSnapshot, _H1ScopeWire]
        ] = {}
        self._preterminal_proofs: dict[int, _IssuedPreterminalCompletionProof] = {}
        self._final_completion_fences: dict[int, _IssuedFinalCompletionFence] = {}
        self._preseal_scopes: dict[
            int, tuple[_AcceptedH1PresealScopeCapability, _PresealScopeCut]
        ] = {}
        self._reopened: dict[
            int, tuple[H1PreissuanceSelection, H1VerifiedOriginalWorkspaceIssuance]
        ] = {}
        self._historical_recovery_sources: dict[int, _HistoricalRecoverySourceState] = {}

    def __reduce__(self) -> Never:
        raise TypeError("H1 preissuance issuer is not serializable")

    def _assert_launch_and_trust(self) -> None:
        self._launch.assert_current()
        slot = self._launch._slot
        registry = self._custody._registry
        observed = self._runtime._trust.verify()
        if (
            observed is None
            or observed.phase != "ACTIVE"
            or (registry.deployment_id, registry.database_id, registry.database_genesis_digest)
            != (slot.deployment_id, slot.database_id, observed.genesis_head)
            or (observed.tenant_id, observed.database_instance_id)
            != (slot.tenant_id, slot.database_id)
        ):
            raise H1PreissuanceSourceViolation("installed H1 launch/trust cut is stale")

    def _trust_observation(self) -> HermeticTrustObservationV1:
        entries = self._runtime._trust._journal.entries()
        logical = self._runtime._trust.owner_snapshot_entries()
        if not entries or not logical:
            raise H1PreissuanceSourceViolation("H1 trust history is absent")
        decision_id, _, raw = entries[-1]
        return HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=decision_id,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            logical_snapshot_head=logical[-1][0],
        )

    def _selected_cut(self, locator: object) -> _PreparedCut:
        if (
            type(locator) is not tuple
            or len(locator) != 2
            or any(type(value) is not str or not value for value in locator)
        ):
            raise H1PreissuanceSourceViolation("H1 preissuance requires exact native run locator")
        run_id, expected_head = locator
        self._assert_launch_and_trust()
        history = read_execution_history(self._runtime)
        lineage = [item for item in history.records if item.run_id == run_id]
        runs = [
            item
            for item in lineage
            if type(item) is ExecutionRunRecord
            and item.run_id == run_id
            and item.head == expected_head
        ]
        if len(runs) != 1 or lineage[-1] is not runs[0]:
            raise H1PreissuanceSourceViolation("H1 preissuance native Run is absent or ambiguous")
        run = runs[0]
        if (
            run.event != "TurnStarted"
            or len(run.turns) != 1
            or run.turns[0].state != "PREPARING"
            or run.turns[0].attempts
            or run.worker_session != self._runtime.current_worker()
        ):
            raise H1PreissuanceSourceViolation("H1 preissuance native Run is not fresh")

        candidates: list[
            tuple[SelectedHermeticResourceObservationRefV1, ExactHead, TrustReference]
        ] = []
        resources = self._runtime._require_dispatch_resources()
        for decision_id, _, raw in self._runtime._loop_decisions().entries():
            try:
                entry = json.loads(raw)
                encoded = entry.get("inbox_initialization")
                if entry.get("kind") != "DECIDED" or not isinstance(encoded, str):
                    continue
                evidence = RetainedInboxExecutionInitialization.model_validate_json(encoded)
                if evidence.proposal.run.run_id != run_id:
                    continue
                wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
                admitted = self._runtime.read_admitted_inbox(
                    wire.identity.original_ingress_identity.command_id
                )
                if admitted is None:
                    continue
                owner_call, authenticated = decode_authentication(admitted.record.command)
                observation = ResourceObservation(
                    evidence.dispatch_grant_bytes,
                    evidence.dispatch_credential_bytes,
                    evidence.dispatch_endpoint_bytes,
                    evidence.dispatch_clock_epoch,
                    evidence.dispatch_signature,
                )
                resource = SelectedHermeticResourceObservationRefV1(
                    signature_domain="dispatch-resources.v1",
                    selected_initialization=ExactHead(
                        identity=evidence.proposal.run.head,
                        head=decision_id,
                        fingerprint=hashlib.sha256(raw).hexdigest(),
                    ),
                    signed_observation_fingerprint=resources.observation_digest(observation),
                )
                proof = ExactHead(**admitted.record.inbox.authentication.proof.model_dump())
                reference = authenticated.reference
                cli = TrustReference(
                    tenant_id=TenantId(reference.tenant_id),
                    principal_id=PrincipalId(reference.principal_id),
                    contour=reference.contour,
                    credential_head=reference.credential_head,
                    session_head=reference.session_head,
                    source_head="local",
                    trust_head=reference.trust_head,
                    materialization_head=reference.materialization_head,
                    freshness_sequence=reference.freshness_sequence,
                    peer_credential=f"uid:{owner_call.request.socket.peer.uid}",
                )
                candidates.append((resource, proof, cli))
            except TypeError, ValueError, KeyError:
                raise H1PreissuanceSourceViolation(
                    "H1 preissuance selected H0/R17 source is malformed"
                ) from None
        if len(candidates) != 1:
            raise H1PreissuanceSourceViolation(
                "H1 preissuance selected H0 source is absent or ambiguous"
            )
        resource, proof, cli = candidates[0]
        source = H1SelectedOutputSources(self._runtime).capture_selected_current(
            resource, proof, cli
        )
        if source is None:
            raise H1PreissuanceSourceViolation("H1 preissuance selected H0/R17/R16 source is stale")
        entry = self._custody.select(run.tenant, run.principal, _CHANNEL)
        return _PreparedCut(
            run=run,
            source=source,
            custody_digest=hashlib.sha256(self._custody._raw).hexdigest(),
            custody_generation=entry.generation,
            trust=self._trust_observation(),
        )

    @staticmethod
    def _scope_id(
        database_id: str, run: ExecutionRunRecord, source: H1SelectedOutputCapture
    ) -> str:
        selected = source.verified.selected_resource_observation_ref.selected_initialization
        raw = json.dumps(
            [database_id, run.run_id, selected.identity, selected.head, selected.fingerprint],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        return "h1-preissuance-scope/" + hashlib.sha256(raw).hexdigest()

    def _reopen_scope(
        self, issued: IssuedHermeticOutputScopeV1, prepared: _PreparedCut
    ) -> HermeticOutputScopeV1:
        anchor = issued.anchor
        decision = next(
            (
                raw
                for key, _, raw in self._runtime._trust._journal.entries()
                if key == anchor.decision.head
            ),
            None,
        )
        if decision is None or hashlib.sha256(decision).hexdigest() != anchor.decision.fingerprint:
            raise H1PreissuanceSourceViolation("issued H1 scope decision is not persisted")
        record = self._runtime._trust._materializer.record(
            anchor.decision.head, anchor.record_ordinal
        )
        if record is None or hashlib.sha256(record).hexdigest() != anchor.record.fingerprint:
            raise H1PreissuanceSourceViolation("issued H1 scope materialization is not persisted")
        try:
            envelope = json.loads(record)
            scope = HermeticOutputScopeV1.model_validate_json(
                json.dumps(envelope["scope"], sort_keys=True, separators=(",", ":"))
            )
        except KeyError, TypeError, ValueError, json.JSONDecodeError:
            raise H1PreissuanceSourceViolation("issued H1 scope record is malformed") from None
        scope_bytes = scope.canonical_bytes()
        expected_ref = ExactHead(
            identity=scope.scope_id,
            head=scope.scope_id + "/" + hashlib.sha256(scope_bytes).hexdigest(),
            fingerprint=hashlib.sha256(scope_bytes).hexdigest(),
        )
        if (
            envelope.get("record_type_id") != anchor.record_type_id
            or envelope.get("schema_id") != anchor.schema_id
            or envelope.get("decision_id") != anchor.decision.head
            or envelope.get("operation_kind") != "HERMETIC_OUTPUT_SCOPE_V1"
            or expected_ref != issued.scope_head
            or scope.revision != issued.revision
            or scope.database_id != self._launch._slot.database_id
            or scope.scope_id != self._scope_id(scope.database_id, prepared.run, prepared.source)
            or scope.worker_session_id != prepared.run.worker_session
            or scope.selected_resource_observation_ref
            != prepared.source.verified.selected_resource_observation_ref
            or scope.admitted_authentication != prepared.source.verified.admitted_authentication_ref
            or scope.disclosure_policy.canonical_source_bytes == b""
        ):
            raise H1PreissuanceSourceViolation("issued H1 scope differs from selected cut")
        return scope

    @staticmethod
    def _same_selected_inputs(left: _PreparedCut, right: _PreparedCut) -> bool:
        """Compare the cut that must survive owner IPC, excluding its own append."""
        return (
            left.run == right.run
            and left.source == right.source
            and left.custody_digest == right.custody_digest
            and left.custody_generation == right.custody_generation
        )

    async def prepare_preissuance(
        self, selected_run_identity: object, actor_observation: object | None = None
    ) -> H1PreissuanceSelection:
        """Issue only after both real owner IPCs and a persisted re-open.

        There is intentionally no await inside either authority-gate block.
        """
        with self._gate.hold():
            prepared = self._selected_cut(selected_run_identity)
            if (
                actor_observation is None
                or actor_observation != self._runtime._trust.capture_verified_observation()
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 execution actor cut is stale before scope issue"
                )
            scope_id = self._scope_id(self._launch._slot.database_id, prepared.run, prepared.source)
            intent = IssueHermeticOutputScopeV1(
                slot_id="h1-cli-effects-origin",
                database_id=self._launch._slot.database_id,
                scope_id=scope_id,
                expected_trust_observation=prepared.trust,
                expected_scope_predecessor=None,
                expected_revision=0,
                authenticated_cli_ref=prepared.source.verified.authenticated_cli_ref,
                admitted_authentication_ref=prepared.source.verified.admitted_authentication_ref,
                selected_resource_observation_ref=prepared.source.verified.selected_resource_observation_ref,
                worker_session_id=prepared.run.worker_session,
            )
            request_id = "h1-preissuance:" + hashlib.sha256(intent.canonical_bytes()).hexdigest()
        issued = await self._runtime.issue_hermetic_output_scope(intent, request_id=request_id)
        try:
            issue_wire = self._runtime._take_h1_scope_wire_for_request(
                "scope_issue", request_id=request_id
            )
        except LoopRejected as error:
            raise H1PreissuanceSourceViolation(
                "H1 scope issue wire is absent, ambiguous, or expired"
            ) from error
        if type(issued) is not IssuedHermeticOutputScopeV1:
            raise H1PreissuanceSourceViolation("H1 scope owner did not issue the selected scope")
        with self._gate.hold():
            append_receipt = self._runtime._take_h1_scope_append_receipt(request_id, issued)
            if append_receipt.before != actor_observation:
                raise H1PreissuanceSourceViolation(
                    "H1 scope append is not bound to execution actor"
                )
            post_issue_trust = self._trust_observation()
            read = ReadCurrentHermeticExecutionScopeV1(
                expected_trust_observation=post_issue_trust,
                source_anchor=issued.anchor,
                expected_revision=issued.revision,
                admitted_authentication_ref=prepared.source.verified.admitted_authentication_ref,
                authenticated_cli_ref=prepared.source.verified.authenticated_cli_ref,
                tenant_id=prepared.run.tenant,
                database_id=self._launch._slot.database_id,
                scope_id=scope_id,
                expected_scope_ref=issued.scope_head,
                expected_worker_session_id=prepared.run.worker_session,
                selected_resource_observation_ref=prepared.source.verified.selected_resource_observation_ref,
            )
        current, current_wire = await self._runtime._read_current_hermetic_output_scope_with_wire(
            read
        )
        if type(current) is not CurrentHermeticExecutionScopeV1:
            raise H1PreissuanceSourceViolation(
                "H1 scope owner did not confirm current selected scope"
            )
        with self._gate.hold():
            self._assert_launch_and_trust()
            fresh = self._selected_cut(selected_run_identity)
            if (
                not self._same_selected_inputs(fresh, prepared)
                or self._trust_observation() != post_issue_trust
                or self._runtime._trust.capture_verified_observation() != append_receipt.after
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 preissuance source cut changed during owner read"
                )
            scope = self._reopen_scope(issued, prepared)
            if current.scope_ref != issued.scope_head or current.source_anchor != issued.anchor:
                raise H1PreissuanceSourceViolation("H1 current scope substitutes the issued anchor")
            selection = _issue_selection_for_authenticated_port(issuer=self)
            self._cuts[id(selection)] = (
                selection,
                _SelectionCut(
                    prepared,
                    issued,
                    current,
                    scope,
                    append_receipt,
                    issue_wire,
                    current_wire,
                ),
            )
            return selection

    def _cut(self, selection: H1PreissuanceSelection) -> _SelectionCut:
        found = self._cuts.get(id(selection))
        if found is None or found[0] is not selection:
            raise H1PreissuanceSourceViolation("H1 preissuance selection is foreign or stale")
        return found[1]

    def check_current(self, selection: H1PreissuanceSelection) -> bool:
        try:
            with self._gate.hold():
                cut = self._cut(selection)
                self._assert_launch_and_trust()
                fresh = self._selected_cut((cut.prepared.run.run_id, cut.prepared.run.head))
                return (
                    self._same_selected_inputs(fresh, cut.prepared)
                    and self._reopen_scope(cut.issued, cut.prepared) == cut.scope
                    and self._runtime._trust.capture_verified_observation()
                    == cut.append_receipt.after
                )
        except H1PreissuanceSourceViolation, LoopRejected, ValueError:
            return False

    def _current_workspace_cut(self, selection: H1PreissuanceSelection) -> _SelectionCut:
        """Reopen the retained owner-current scope without a new owner IPC read."""
        cut = self._cut(selection)
        state = _selection_state(selection, self)
        if state is None or state.consumed:
            raise H1PreissuanceSourceViolation("H1 workspace binding selection is consumed")
        self._assert_launch_and_trust()
        fresh = self._selected_cut((cut.prepared.run.run_id, cut.prepared.run.head))
        if (
            not self._same_selected_inputs(fresh, cut.prepared)
            or self._reopen_scope(cut.issued, cut.prepared) != cut.scope
            or self._runtime._trust.capture_verified_observation() != cut.append_receipt.after
            or cut.current.scope_ref != cut.issued.scope_head
            or cut.current.source_anchor != cut.issued.anchor
            or cut.current.ordered_current_source_refs
            != (
                cut.scope.admitted_authentication,
                cut.issued.anchor.decision,
                cut.issued.anchor.record,
            )
        ):
            raise H1PreissuanceSourceViolation("H1 workspace binding source cut is stale")
        return cut

    def validate_actor_scope_refresh(
        self, selection: H1PreissuanceSelection, old_observation: object, new_observation: object
    ) -> bool:
        """Accept only a genuine new actor bound to this exact append receipt."""
        try:
            with self._gate.hold():
                cut = self._current_workspace_cut(selection)
                return (
                    old_observation == cut.append_receipt.before
                    and new_observation == cut.append_receipt.after
                )
        except H1PreissuanceSourceViolation, LoopRejected, ValueError:
            return False

    def selected_workspace_authentication_heads(
        self, selection: H1PreissuanceSelection
    ) -> tuple[str, str]:
        """Project selected credential/session heads from the retained scope cut.

        These inert values are available only through an issuer-held, current,
        unconsumed selection.  They do not replace authentication by the W
        owner or establish a fresh owner IPC read.
        """
        with self._gate.hold():
            scope = self._current_workspace_cut(selection).scope
            return (
                scope.authenticated_cli_state.credential_head,
                scope.authenticated_cli_state.session_head,
            )

    def selected_workspace_identity(
        self, selection: H1PreissuanceSelection
    ) -> _H1WorkspaceIdentity:
        """Project the complete selected profile from issuer-held evidence.

        The returned data is inert.  ``bind_workspace_cut`` repeats the
        selection and scope authentication before using a W-built ingress.
        """
        with self._gate.hold():
            cut = self._current_workspace_cut(selection)
            scope = cut.scope
            authenticated = cut.prepared.source.verified.authenticated_cli_ref
            entry = self._custody.select(
                cut.prepared.run.tenant, cut.prepared.run.principal, _CHANNEL
            )
            if (
                self._launch._slot.tenant_id != cut.prepared.run.tenant
                or self._launch._slot.database_id != self._custody._registry.database_id
                or self._launch._slot.database_id != scope.database_id
                or cut.prepared.run.tenant != scope.tenant_id
                or cut.prepared.run.principal != scope.principal_id
                or authenticated.tenant_id.value != cut.prepared.run.tenant
                or authenticated.principal_id.value != cut.prepared.run.principal
                or authenticated.credential_head != scope.authenticated_cli_state.credential_head
                or authenticated.session_head != scope.authenticated_cli_state.session_head
                or authenticated.contour != scope.contour_head
                or entry.channel_id != _CHANNEL
                or entry.channel_id not in entry.visible_channels
                or entry.origin_recipient_id != scope.recipient.recipient_id
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 selected workspace identity differs from retained cut"
                )
            return _H1WorkspaceIdentity(
                tenant=cut.prepared.run.tenant,
                principal=cut.prepared.run.principal,
                credential_head=authenticated.credential_head,
                session_head=authenticated.session_head,
                contour_head=authenticated.contour,
                database_id=scope.database_id,
                channel=entry.channel_id,
            )

    def bind_workspace_cut(
        self,
        selection: H1PreissuanceSelection,
        identity: TrustedIngress,
        channel: str,
        database_id: str,
    ) -> None:
        """Retain the enumerated workspace cut for one issued selection.

        The W owner supplies its authenticated enumeration through this private
        same-process seam.  This is deliberately synchronous: it validates the
        owner-current reply retained by ``prepare_preissuance`` and reopens the
        physical scope under the shared gate; it does *not* purport to perform a
        fresh owner IPC read.  A later async fence owns that responsibility.
        """
        if type(identity) is not TrustedIngress:
            raise H1PreissuanceSourceViolation("H1 workspace binding requires trusted ingress")
        if any(type(value) is not str or not value for value in (channel, database_id)):
            raise H1PreissuanceSourceViolation(
                "H1 workspace binding requires exact channel/database"
            )
        with self._gate.hold():
            cut = self._current_workspace_cut(selection)
            scope = cut.scope
            if (
                database_id != self._launch._slot.database_id
                or database_id != scope.database_id
                or identity.tenant != cut.prepared.run.tenant
                or identity.principal != cut.prepared.run.principal
                or identity.tenant != scope.tenant_id
                or identity.principal != scope.principal_id
                or identity.heads.credential != scope.authenticated_cli_state.credential_head
                or identity.heads.session != scope.authenticated_cli_state.session_head
                or identity.heads.contour != scope.contour_head
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 workspace binding identity differs from scope"
                )
            entry = self._custody.select(identity.tenant, identity.principal, channel)
            if (
                channel != entry.channel_id
                or channel not in entry.visible_channels
                or entry.origin_recipient_id != scope.recipient.recipient_id
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 workspace binding registration differs from scope"
                )
            registration = H1PreissuanceRegistrationV1(
                deployment_id=self._custody._registry.deployment_id,
                database_id=database_id,
                database_genesis_digest=self._custody._registry.database_genesis_digest,
                tenant_id=identity.tenant,
                principal_id=identity.principal,
                channel_id=channel,
                registration_id=entry.registration_id,
                generation=entry.generation,
                custody_entry_digest=hashlib.sha256(entry.canonical_bytes()).hexdigest(),
                origin_recipient_id=entry.origin_recipient_id,
                conversation_id=entry.conversation_id,
                visible_channels=entry.visible_channels,
                accepted_policy_selector=entry.accepted_policy_selector,
                accepted_policy=scope.disclosure_policy.ref,
                accepted_policy_bytes_base64=base64.b64encode(
                    scope.disclosure_policy.canonical_source_bytes
                ).decode("ascii"),
                output_scope_anchor=cut.issued.anchor,
                output_scope_ref=cut.issued.scope_head,
                selected_resource_observation_ref=scope.selected_resource_observation_ref,
                admitted_authentication_ref=scope.admitted_authentication,
            )
            policy = H1WorkspacePolicyV2(
                tenant=identity.tenant,
                principal=identity.principal,
                channel=channel,
                database=database_id,
                endpoint=identity.endpoint,
                heads=H1WorkspacePolicyHeadsV1(
                    policy=identity.heads.policy,
                    credential=identity.heads.credential,
                    session=identity.heads.session,
                    contour=identity.heads.contour,
                    deletion=identity.heads.deletion,
                ),
                sources=tuple(
                    SourceReference.model_validate_json(source.model_dump_json())
                    for source in identity.sources
                ),
                registration=registration,
            )
            if cut.workspace_policy is None:
                self._cuts[id(selection)] = (selection, replace(cut, workspace_policy=policy))
            elif cut.workspace_policy != policy:
                raise H1PreissuanceSourceViolation(
                    "H1 workspace binding conflicts with retained cut"
                )

    def _advanced_original_run(self, cut: _SelectionCut) -> None:
        """Require a current descendant without reapplying initial freshness."""
        lineage = [
            record
            for record in read_execution_history(self._runtime).records
            if isinstance(record, ExecutionRunRecord) and record.run_id == cut.prepared.run.run_id
        ]
        initial = next(
            (index for index, record in enumerate(lineage) if record == cut.prepared.run),
            None,
        )
        if (
            initial is None
            or initial + 1 >= len(lineage)
            or lineage[-1].worker_session != cut.prepared.run.worker_session
            or lineage[-1].event == "TurnStarted"
        ):
            raise H1PreissuanceSourceViolation("H1 original has no current selected descendant")

    def _verify_selected_original(
        self, selection: H1PreissuanceSelection, cut: _SelectionCut, selected: H1SelectedPrepare
    ) -> None:
        if (
            type(selected) is not H1SelectedPrepare
            or selected.started_run != cut.prepared.run
            or selected.started_run.worker_session != cut.prepared.run.worker_session
            or cut.workspace_policy is None
        ):
            raise H1PreissuanceSourceViolation("H1 selected original differs from issuer cut")
        try:
            retained_entry = next(
                raw
                for decision_id, _, raw in self._runtime._loop_decisions().entries()
                if decision_id == selected.decision_id
            )
            retained = json.loads(retained_entry)
            encoded = retained["execution_transition"]
            matching_commands = [
                command
                for _decision_id, _raw, command in _selected_commands(self._runtime)
                if command == selected.publication
            ]
            retained_v3 = _decode_v3_prepare(selected.decision_bytes)
        except KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError:
            raise H1PreissuanceSourceViolation(
                "H1 selected original V3 provenance is absent or invalid"
            ) from None
        if retained_v3 is None or not isinstance(retained_v3.request, PrepareExecutionRequest):
            raise H1PreissuanceSourceViolation("H1 selected original V3 evidence is not Prepare")
        workspace_members = tuple(
            member
            for member in retained_v3.request.manifest.members
            if member.producer == "projections" and member.surface == "workspace"
        )
        if (
            selected.decision_bytes != retained_entry
            or encoded.encode() != selected.retained_bytes
            or len(matching_commands) != 1
            or retained_v3 != selected.retained
            or retained_v3.canonical_bytes() != selected.retained_bytes
            or retained_v3.workspace_issuance != selected.issuance_ref
            or transition_command(retained_v3) != matching_commands[0]
            or len(workspace_members) != 1
            or workspace_members[0].model_dump_json().encode() != selected.workspace_member_bytes
            or workspace_members[0].content.encode() != selected.proposal_context_bytes
        ):
            raise H1PreissuanceSourceViolation("H1 selected original V3 evidence differs")
        try:
            member = next(
                record
                for record in matching_commands[0].records
                if record.record_id == selected.physical_run.record_id
            )
            database = Path(self._runtime._database).resolve(strict=True)
            with closing(
                sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
            ) as connection:
                connection.execute("BEGIN")
                physical = _physical_run(connection, matching_commands[0], member)
        except OSError, StopIteration, ValueError, sqlite3.Error:
            raise H1PreissuanceSourceViolation(
                "H1 selected original physical V3 publication is absent or incomplete"
            ) from None
        if physical != selected.physical_run:
            raise H1PreissuanceSourceViolation("H1 selected original physical V3 evidence differs")
        workspace = R13Workspace(self._runtime)
        closure = verify_h1_original_workspace(
            selected.issuance_ref,
            selected.workspace_member_bytes,
            selected.proposal_context_bytes,
            workspace.open_h1_workspace_issuance(),
            workspace.open_dashboard_issuance(),
        )
        issued = workspace.open_h1_workspace_issuance().load(selected.issuance_ref)
        if type(issued) is not H1OriginalWorkspaceIssuanceV2:
            raise H1PreissuanceSourceViolation("H1 selected original is not physical V2")
        try:
            policy = decode_h1_workspace_policy_v2(
                base64.b64decode(
                    issued.sources.policy_payload_base64.encode("ascii"), validate=True
                )
            )
        except ValueError:
            raise H1PreissuanceSourceViolation("H1 physical V2 policy is malformed") from None
        if (
            closure.issuance != selected.issuance_ref
            or policy != cut.workspace_policy
            or issued.run_id != cut.prepared.run.run_id
            or issued.started_run_head != cut.prepared.run.head
            or issued.worker_session != cut.prepared.run.worker_session
        ):
            raise H1PreissuanceSourceViolation("H1 physical V2 original differs from issuer cut")
        self._advanced_original_run(cut)

    async def verify_selected_original(
        self, selected: H1SelectedPrepare
    ) -> H1VerifiedOriginalWorkspaceIssuance:
        """Authenticate a native selected V3 reference and issue one opaque original."""
        with self._gate.hold():
            if type(selected) is not H1SelectedPrepare:
                raise H1PreissuanceSourceViolation(
                    "H1 original requires native selected V3 evidence"
                )
            matches = [
                (selection, cut)
                for selection, cut in self._cuts.values()
                if cut.prepared.run == selected.started_run and cut.workspace_policy is not None
            ]
            if len(matches) != 1:
                raise H1PreissuanceSourceViolation(
                    "H1 selected original issuer cut is absent or ambiguous"
                )
            selection, cut = matches[0]
            self._assert_launch_and_trust()
            self._verify_selected_original(selection, cut, selected)
            trust = self._trust_observation()
            read = ReadCurrentHermeticExecutionScopeV1(
                expected_trust_observation=trust,
                source_anchor=cut.issued.anchor,
                expected_revision=cut.issued.revision,
                admitted_authentication_ref=cut.scope.admitted_authentication,
                authenticated_cli_ref=cut.prepared.source.verified.authenticated_cli_ref,
                tenant_id=cut.prepared.run.tenant,
                database_id=cut.scope.database_id,
                scope_id=cut.scope.scope_id,
                expected_scope_ref=cut.issued.scope_head,
                expected_worker_session_id=cut.prepared.run.worker_session,
                selected_resource_observation_ref=cut.scope.selected_resource_observation_ref,
            )
        current = await self._runtime.read_current_hermetic_output_scope(read)
        if type(current) is not CurrentHermeticExecutionScopeV1:
            raise H1PreissuanceSourceViolation(
                "H1 original scope owner did not confirm current scope: " + current.disposition
            )
        with self._gate.hold():
            self._assert_launch_and_trust()
            self._verify_selected_original(selection, cut, selected)
            if (
                self._trust_observation() != trust
                or current.scope_ref != cut.issued.scope_head
                or current.source_anchor != cut.issued.anchor
                or current.ordered_current_source_refs
                != (
                    cut.scope.admitted_authentication,
                    cut.issued.anchor.decision,
                    cut.issued.anchor.record,
                )
            ):
                raise H1PreissuanceSourceViolation("H1 original current-scope attestation is stale")
            original = _issue_verified_original_for_authenticated_port(issuer=self)
            self._originals[id(original)] = (
                original,
                _OriginalCut(selection, cut, selected, read, current),
            )
            return original

    def _original_cut(self, original: H1VerifiedOriginalWorkspaceIssuance) -> _OriginalCut:
        found = self._originals.get(id(original))
        if found is None or found[0] is not original:
            raise H1PreissuanceSourceViolation("H1 original is foreign or stale")
        return found[1]

    # The completion-observation member remains closed until the B owner wires
    # a retained successful completion exchange.
    def reopen_original(
        self, verified_v2_issuance: H1VerifiedOriginalWorkspaceIssuance
    ) -> H1PreissuanceSelection:
        with self._gate.hold():
            original = self._original_cut(verified_v2_issuance)
            self._assert_launch_and_trust()
            self._verify_selected_original(original.selection, original.cut, original.selected)
            if (
                original.current.scope_ref != original.cut.issued.scope_head
                or original.current.source_anchor != original.cut.issued.anchor
            ):
                raise H1PreissuanceSourceViolation("H1 original current-scope attestation is stale")
            selection = _issue_selection_for_authenticated_port(issuer=self)
            self._cuts[id(selection)] = (selection, original.cut)
            self._reopened[id(selection)] = (selection, verified_v2_issuance)
            return selection

    def resolved_workspace_policy(self, selection: H1PreissuanceSelection) -> object:
        with self._gate.hold():
            cut = self._cut(selection)
            state = _selection_state(selection, self)
            if state is None or state.consumed or cut.workspace_policy is None:
                raise H1PreissuanceSourceViolation("H1 workspace cut is not bound")
            return cut.workspace_policy

    def validate_original_selection(
        self,
        selection: H1PreissuanceSelection,
        verified_v2_issuance: H1VerifiedOriginalWorkspaceIssuance,
    ) -> bool:
        try:
            with self._gate.hold():
                original = self._original_cut(verified_v2_issuance)
                reopened = self._reopened.get(id(selection))
                if reopened is None or reopened != (selection, verified_v2_issuance):
                    return False
                if self._cut(selection) != original.cut:
                    return False
                self._assert_launch_and_trust()
                self._verify_selected_original(original.selection, original.cut, original.selected)
                return (
                    original.current.scope_ref == original.cut.issued.scope_head
                    and original.current.source_anchor == original.cut.issued.anchor
                )
        except H1PreissuanceSourceViolation, LoopRejected, ValueError:
            return False

    def _selected_scope_join(
        self, cut: _SelectionCut
    ) -> tuple[HermeticOutputScopeV1, H1SelectedOutputCapture, str, int]:
        """Reopen the P/custody/policy join shared by preseal and completion."""
        self._assert_launch_and_trust()
        scope = self._reopen_scope(cut.issued, cut.prepared)
        selected = H1SelectedOutputSources(self._runtime).capture_selected_current(
            cut.prepared.source.verified.selected_resource_observation_ref,
            cut.prepared.source.verified.admitted_authentication_ref,
            cut.prepared.source.verified.authenticated_cli_ref,
        )
        entry = self._custody.select(cut.prepared.run.tenant, cut.prepared.run.principal, _CHANNEL)
        policy = cut.workspace_policy
        try:
            accepted_policy = HermeticOutputPolicyV1.model_validate_json(
                scope.disclosure_policy.canonical_source_bytes
            )
        except ValueError:
            raise H1PreissuanceSourceViolation("H1 selected scope policy is malformed") from None
        if (
            selected is None
            or selected != cut.prepared.source
            or scope != cut.scope
            or policy is None
            or policy.registration.custody_entry_digest
            != hashlib.sha256(entry.canonical_bytes()).hexdigest()
            or policy.registration.generation != entry.generation
            or policy.registration.accepted_policy != scope.disclosure_policy.ref
            or policy.registration.accepted_policy_bytes_base64
            != base64.b64encode(scope.disclosure_policy.canonical_source_bytes).decode("ascii")
            or accepted_policy.endpoint_ref != scope.recipient.endpoint
            or scope.recipient != selected.verified.recipient
        ):
            raise H1PreissuanceSourceViolation("H1 selected scope source join differs")
        return scope, selected, hashlib.sha256(self._custody._raw).hexdigest(), entry.generation

    def _preseal_native_owner(self) -> H1PresealNativeSource:
        owner = getattr(self._runtime, "_h1_preseal_native_source", None)
        if type(owner) is not H1PresealNativeSource or owner._runtime is not self._runtime:
            raise H1PreissuanceSourceViolation("H1 preseal native source is not mounted")
        return owner

    def _preseal_scope_snapshot(
        self, native_cut: object, preflight: object | None = None
    ) -> tuple[
        H1PreissuanceSelection,
        _SelectionCut,
        HermeticOutputScopeV1,
        H1SelectedOutputCapture,
        str,
        int,
    ]:
        """Reopen one exact existing P selection for a native preseal cut."""
        self._gate.require_held()
        if type(native_cut) is not H1PresealNativeSourceCut:
            raise H1PreissuanceSourceViolation("H1 preseal scope requires native capability")
        native = self._preseal_native_owner()
        native.replay(native_cut)
        selected = native_cut._preflight.prepare
        if preflight is not None and native_cut._preflight is not preflight:
            raise H1PreissuanceSourceViolation("H1 preseal native preflight differs")
        matches = [
            (selection, cut)
            for selection, cut in self._cuts.values()
            if cut.prepared.run == selected.started_run and cut.workspace_policy is not None
        ]
        if len(matches) != 1:
            raise H1PreissuanceSourceViolation("H1 preseal selected P cut is absent or ambiguous")
        selection, cut = matches[0]
        if self._cut(selection) is not cut:
            raise H1PreissuanceSourceViolation("H1 preseal selected P cut is foreign")
        # This validates the complete V3 retained/physical/workspace provenance,
        # rather than treating the shared started Run as a sufficient key.
        self._verify_selected_original(selection, cut, selected)
        scope, source, custody_digest, custody_generation = self._selected_scope_join(cut)
        return selection, cut, scope, source, custody_digest, custody_generation

    @staticmethod
    def _validate_preseal_issue_wire(cut: _SelectionCut, scope: HermeticOutputScopeV1) -> None:
        wire = cut.scope_issue_wire
        if type(wire) is not _H1ScopeWire:
            raise H1PreissuanceSourceViolation("H1 preseal issue owner wire is absent")
        call, result = wire.sent, wire.returned
        if (
            type(result) is not PublicPortSuccess
            or result.request_id != call.request_id
            or result.responder != call.callee
            or result.schema_id != "chiplog.deployment-trust.issue-hermetic-output-scope-result.v1"
        ):
            raise H1PreissuanceSourceViolation("H1 preseal issue owner wire differs")
        try:
            outer = decode_trust_owner_call_canonical(call.canonical_payload)
            owner_call = H1OwnerCandidateCallV1.model_validate_json(outer.request_bytes)
            candidate = H1OwnerCandidateV1.model_validate_json(result.canonical_payload)
            candidate.check_pinned_call(owner_call)
        except ValueError:
            raise H1PreissuanceSourceViolation("H1 preseal issue owner wire is malformed") from None
        if (
            outer.canonical_bytes() != call.canonical_payload
            or outer.mode != "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
            or owner_call.canonical_bytes() != outer.request_bytes
            or candidate.canonical_bytes() != result.canonical_payload
            or candidate.scope != scope
        ):
            raise H1PreissuanceSourceViolation("H1 preseal issue owner wire differs")

    @staticmethod
    def _validate_preseal_current_wire(
        cut: _SelectionCut,
        current: object,
        wire: object,
    ) -> None:
        if type(wire) is not _H1ScopeWire or type(current) is not CurrentHermeticExecutionScopeV1:
            raise H1PreissuanceSourceViolation("H1 preseal current owner wire is absent")
        call, result = wire.sent, wire.returned
        if (
            type(result) is not PublicPortSuccess
            or result.request_id != call.request_id
            or result.responder != call.callee
            or result.schema_id
            != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
        ):
            raise H1PreissuanceSourceViolation("H1 preseal current owner wire differs")
        try:
            outer = decode_trust_owner_call_canonical(call.canonical_payload)
            owner_call = H1OwnerCurrentCallV1.model_validate_json(outer.request_bytes)
            candidate = H1OwnerCurrentCandidateV1.model_validate_json(result.canonical_payload)
            request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
                owner_call.read_request_bytes
            )
            candidate.check_pinned_call(owner_call)
        except ValueError:
            raise H1PreissuanceSourceViolation(
                "H1 preseal current owner wire is malformed"
            ) from None
        if (
            outer.canonical_bytes() != call.canonical_payload
            or outer.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
            or owner_call.canonical_bytes() != outer.request_bytes
            or request.canonical_bytes() != owner_call.read_request_bytes
            or candidate.canonical_bytes() != result.canonical_payload
            or candidate.current != current
            or request.source_anchor != cut.issued.anchor
            or request.expected_scope_ref != cut.issued.scope_head
        ):
            raise H1PreissuanceSourceViolation("H1 preseal current owner wire differs")

    async def _capture_preseal_p_residual(
        self, native: object
    ) -> _AcceptedH1PresealScopeCapability:
        """Capture P facts from an installed native preseal source without issuing scope."""
        if type(native) is not H1PresealNativeSourceCut:
            raise H1PreissuanceSourceViolation("H1 preseal scope requires native capability")
        preflight = native._preflight
        with self._gate.hold():
            before = self._preseal_scope_snapshot(native, preflight)
            cut, scope, source = before[1], before[2], before[3]
            trust = self._trust_observation()
            read = ReadCurrentHermeticExecutionScopeV1(
                expected_trust_observation=trust,
                source_anchor=cut.issued.anchor,
                expected_revision=cut.issued.revision,
                admitted_authentication_ref=scope.admitted_authentication,
                authenticated_cli_ref=source.verified.authenticated_cli_ref,
                tenant_id=cut.prepared.run.tenant,
                database_id=scope.database_id,
                scope_id=scope.scope_id,
                expected_scope_ref=cut.issued.scope_head,
                expected_worker_session_id=cut.prepared.run.worker_session,
                selected_resource_observation_ref=scope.selected_resource_observation_ref,
            )
        current, current_wire = await self._runtime._read_current_hermetic_output_scope_with_wire(
            read
        )
        if type(current) is not CurrentHermeticExecutionScopeV1:
            raise H1PreissuanceSourceViolation(
                "H1 preseal scope owner did not confirm current scope"
            )
        with self._gate.hold():
            self._validate_preseal_issue_wire(cut, scope)
            self._validate_preseal_current_wire(cut, current, current_wire)
            after = self._preseal_scope_snapshot(native, preflight)
            if (
                after != before
                or self._trust_observation() != trust
                or current.scope_ref != cut.issued.scope_head
                or current.source_anchor != cut.issued.anchor
                or current.ordered_current_source_refs
                != (
                    scope.admitted_authentication,
                    cut.issued.anchor.decision,
                    cut.issued.anchor.record,
                )
            ):
                raise H1PreissuanceSourceViolation("H1 preseal scope changed during owner read")
            capability = object.__new__(_AcceptedH1PresealScopeCapability)
            self._preseal_scopes[id(capability)] = (
                capability,
                _PresealScopeCut(
                    capability,
                    native,
                    preflight,
                    before[0],
                    cut,
                    scope,
                    current,
                    trust,
                    source,
                    before[4],
                    before[5],
                    current_wire,
                ),
            )
            return capability

    def _replay_preseal_scope(
        self, scope_cap: object, native_cut: object
    ) -> _AcceptedH1PresealScopeProjection:
        """Recheck P/native facts under the caller-held gate; no new owner IPC occurs."""
        self._gate.require_held()
        issued = self._preseal_scopes.get(id(scope_cap))
        if (
            type(scope_cap) is not _AcceptedH1PresealScopeCapability
            or issued is None
            or issued[0] is not scope_cap
            or native_cut is not issued[1].native
        ):
            raise H1PreissuanceSourceViolation(
                "H1 preseal scope capability/native identity differs"
            )
        captured = issued[1]
        self._validate_preseal_issue_wire(captured.cut, captured.scope)
        self._validate_preseal_current_wire(captured.cut, captured.current, captured.current_wire)
        returned = captured.current_wire.returned
        if type(returned) is not PublicPortSuccess:
            raise H1PreissuanceSourceViolation("H1 preseal current owner response differs")
        try:
            outer = decode_trust_owner_call_canonical(captured.current_wire.sent.canonical_payload)
            call = H1OwnerCurrentCallV1.model_validate_json(outer.request_bytes)
            candidate = H1OwnerCurrentCandidateV1.model_validate_json(returned.canonical_payload)
            replayed_current = self._runtime._replay_current_hermetic_output_scope_held(
                ReadCurrentHermeticExecutionScopeV1.model_validate_json(call.read_request_bytes),
                candidate,
                callee=captured.current_wire.sent.callee,
            )
        except (AttributeError, LoopRejected, ValueError) as error:
            raise H1PreissuanceSourceViolation(
                "H1 preseal current replay is unavailable"
            ) from error
        now = self._preseal_scope_snapshot(native_cut, captured.preflight)
        if (
            now
            != (
                captured.selection,
                captured.cut,
                captured.scope,
                captured.source,
                captured.custody_digest,
                captured.custody_generation,
            )
            or self._trust_observation() != captured.trust
            or replayed_current != captured.current
        ):
            raise H1PreissuanceSourceViolation("H1 preseal scope continuity is stale")
        entry = self._custody.select(
            captured.cut.prepared.run.tenant, captured.cut.prepared.run.principal, _CHANNEL
        )
        return _AcceptedH1PresealScopeProjection(
            scope=captured.scope,
            recipient=captured.source.verified.recipient,
            policy_bytes=captured.scope.disclosure_policy.canonical_source_bytes,
            scope_ref=captured.cut.issued.scope_head,
            scope_bytes=captured.scope.canonical_bytes(),
            policy_ref=captured.scope.disclosure_policy.ref,
            custody_entry_generation=entry.generation,
            custody_entry_digest=hashlib.sha256(entry.canonical_bytes()).hexdigest(),
            source_signature_digest=(
                captured.source.verified.selected_resource_observation_ref.signed_observation_fingerprint
            ),
        )

    def _replay_preseal_scope_wires(
        self, scope_cap: object, native_cut: object
    ) -> tuple[_H1ScopeWire, _H1ScopeWire]:
        """Expose P-retained ISSUE/CURRENT exchanges only after the full replay."""
        self._gate.require_held()
        issued = self._preseal_scopes.get(id(scope_cap))
        if (
            type(scope_cap) is not _AcceptedH1PresealScopeCapability
            or issued is None
            or issued[0] is not scope_cap
            or native_cut is not issued[1].native
        ):
            raise H1PreissuanceSourceViolation(
                "H1 preseal scope wire capability/native identity differs"
            )
        self._replay_preseal_scope(scope_cap, native_cut)
        issue_wire = issued[1].cut.scope_issue_wire
        if type(issue_wire) is not _H1ScopeWire:
            raise H1PreissuanceSourceViolation("H1 preseal scope issue wire is absent")
        return issue_wire, issued[1].current_wire

    def _completion_scope_snapshot(
        self,
        first_path_capture: object,
        native_cut: object,
        verified_original: object,
    ) -> tuple[
        H1FirstPathCapture,
        H1CurrentNativeMemberSourceCut,
        object,
        _OriginalCut,
        HermeticOutputScopeV1,
        H1SelectedOutputCapture,
        str,
        int,
    ]:
        """Replay the source-owned completion cut without treating a DTO as proof."""
        self._gate.require_held()
        if type(first_path_capture) is not H1FirstPathCapture:
            raise H1PreissuanceSourceViolation("H1 completion scope requires first-path capture")
        if type(native_cut) is not H1CurrentNativeMemberSourceCut:
            raise H1PreissuanceSourceViolation("H1 completion scope requires native capability")
        if type(verified_original) is not H1VerifiedOriginalWorkspaceIssuance:
            raise H1PreissuanceSourceViolation("H1 completion scope requires verified original")
        first_path = getattr(self._runtime, "_h1_first_path_sources", None)
        native_sources = getattr(self._runtime, "_h1_native_member_sources", None)
        if (
            type(first_path) is not H1FirstPathSources
            or type(native_sources) is not H1NativeMemberSources
        ):
            raise H1PreissuanceSourceViolation("H1 completion scope source owners are not mounted")
        if (
            native_sources._runtime is not self._runtime
            or native_sources._first_path is not first_path
        ):
            raise H1PreissuanceSourceViolation("H1 completion scope source owners differ")
        # `replay_current` is the native owner's nominal membership check.  Do
        # not inspect the public frozen carrier before that check succeeds.
        native = native_sources.replay_current(native_cut)
        if native_cut._capture is not first_path_capture:
            raise H1PreissuanceSourceViolation("H1 completion scope first-path capture differs")
        if first_path.replay_current_native_cut(first_path_capture) != native:
            raise H1PreissuanceSourceViolation("H1 completion scope first-path replay differs")
        original = self._original_cut(verified_original)
        self._assert_launch_and_trust()
        self._verify_selected_original(original.selection, original.cut, original.selected)
        post_seal = select_h1_v3_prepare_for_seal(
            self._runtime, selected_seal=native.source.selected_response_seal
        )
        workspace = reopen_selected_h1_workspace(self._runtime, post_seal.prepare)
        if (
            post_seal.prepare != original.selected
            or workspace.issuance != original.selected.issuance_ref
            or native.source.complete_ordered_run_lineage[-1] != post_seal.sealed_run
        ):
            raise H1PreissuanceSourceViolation("H1 completion scope native/original cut differs")
        scope, selected, custody_digest, custody_generation = self._selected_scope_join(
            original.cut
        )
        return (
            first_path_capture,
            native_cut,
            native,
            original,
            scope,
            selected,
            custody_digest,
            custody_generation,
        )

    async def _capture_completion_scope(
        self, first_path_capture: object, native_cut: object, verified_original: object
    ) -> _AcceptedH1CompletionScopeCapability:
        """Capture inert source continuity around one real current-scope read.

        This token deliberately does not establish final owner currentness.  A
        later async fence must perform its own current-scope IPC before using
        the projection to prepare or select work.
        """
        with self._gate.hold():
            before = self._completion_scope_snapshot(
                first_path_capture, native_cut, verified_original
            )
            original = before[3]
            trust = self._trust_observation()
            read = ReadCurrentHermeticExecutionScopeV1(
                expected_trust_observation=trust,
                source_anchor=original.cut.issued.anchor,
                expected_revision=original.cut.issued.revision,
                admitted_authentication_ref=before[4].admitted_authentication,
                authenticated_cli_ref=before[5].verified.authenticated_cli_ref,
                tenant_id=original.cut.prepared.run.tenant,
                database_id=before[4].database_id,
                scope_id=before[4].scope_id,
                expected_scope_ref=original.cut.issued.scope_head,
                expected_worker_session_id=original.cut.prepared.run.worker_session,
                selected_resource_observation_ref=before[4].selected_resource_observation_ref,
            )
        current, current_wire = await self._runtime._read_current_hermetic_output_scope_with_wire(
            read
        )
        call, result = current_wire.sent, current_wire.returned
        with self._gate.hold():
            if (
                type(result) is not PublicPortSuccess
                or result.request_id != call.request_id
                or result.responder != call.callee
                or result.schema_id
                != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 completion scope current wire/result differs"
                )
            try:
                owner_wire = decode_trust_owner_call_canonical(call.canonical_payload)
                owner_call = H1OwnerCurrentCallV1.model_validate_json(owner_wire.request_bytes)
                candidate = H1OwnerCurrentCandidateV1.model_validate_json(result.canonical_payload)
                candidate.check_pinned_call(owner_call)
            except ValueError:
                raise H1PreissuanceSourceViolation(
                    "H1 completion scope current wire payload differs"
                ) from None
            if (
                candidate.canonical_bytes() != result.canonical_payload
                or candidate.current != current
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 completion scope current wire/result differs"
                )
            after = self._completion_scope_snapshot(
                first_path_capture, native_cut, verified_original
            )
            if (
                before != after
                or self._trust_observation() != trust
                or type(current) is not CurrentHermeticExecutionScopeV1
                or current.scope_ref != after[3].cut.issued.scope_head
                or current.source_anchor != after[3].cut.issued.anchor
                or current.ordered_current_source_refs
                != (
                    after[4].admitted_authentication,
                    after[3].cut.issued.anchor.decision,
                    after[3].cut.issued.anchor.record,
                )
            ):
                raise H1PreissuanceSourceViolation("H1 completion scope changed during owner read")
            capability = object.__new__(_AcceptedH1CompletionScopeCapability)
            if (
                type(after[3].cut.scope_issue_wire) is not _H1ScopeWire
                or type(current_wire) is not _H1ScopeWire
            ):
                raise H1PreissuanceSourceViolation("H1 completion scope wire is absent")
            self._completion_scopes[id(capability)] = (
                capability,
                _CompletionScopeCut(
                    capability,
                    after[0],
                    after[1],
                    after[2],
                    cast(H1VerifiedOriginalWorkspaceIssuance, verified_original),
                    after[3],
                    after[4],
                    current,
                    trust,
                    after[5],
                    after[6],
                    after[7],
                    after[3].cut.scope_issue_wire,
                    current_wire,
                ),
            )
            return capability

    def _replay_completion_scope(
        self, capability: object, native_cut: object
    ) -> _AcceptedH1CompletionScopeProjection:
        """Replay source continuity only; this method performs no owner IPC."""
        with self._gate.hold():
            issued = self._completion_scopes.get(id(capability))
            if (
                type(capability) is not _AcceptedH1CompletionScopeCapability
                or issued is None
                or issued[0] is not capability
            ):
                raise H1PreissuanceSourceViolation("H1 completion scope capability is not P-issued")
            cut = issued[1]
            if native_cut is not cut.native_cap:
                raise H1PreissuanceSourceViolation("H1 completion scope native capability differs")
            current = self._completion_scope_snapshot(cut.first_path, native_cut, cut.original)
            if (
                current[2] != cut.native
                or current[3] != cut.original_cut
                or current[4] != cut.scope
                or current[5] != cut.source
                or current[6] != cut.custody_digest
                or current[7] != cut.custody_generation
                or self._trust_observation() != cut.trust
            ):
                raise H1PreissuanceSourceViolation("H1 completion scope source continuity is stale")
            entry = self._custody.select(
                cut.original_cut.cut.prepared.run.tenant,
                cut.original_cut.cut.prepared.run.principal,
                _CHANNEL,
            )
            source_signature_digest = current[
                5
            ].verified.selected_resource_observation_ref.signed_observation_fingerprint
            if (
                entry.generation != current[7]
                or source_signature_digest
                != cut.scope.selected_resource_observation_ref.signed_observation_fingerprint
            ):
                raise H1PreissuanceSourceViolation("H1 completion scope projection source differs")
            return _AcceptedH1CompletionScopeProjection(
                scope=cut.scope,
                recipient=cut.source.verified.recipient,
                policy_bytes=cut.scope.disclosure_policy.canonical_source_bytes,
                scope_ref=cut.original_cut.cut.issued.scope_head,
                scope_bytes=cut.scope.canonical_bytes(),
                policy_ref=cut.scope.disclosure_policy.ref,
                custody_entry_generation=entry.generation,
                custody_entry_digest=hashlib.sha256(entry.canonical_bytes()).hexdigest(),
                source_signature_digest=source_signature_digest,
            )

    def _replay_completion_scope_wires(
        self, capability: object, native_cut: object
    ) -> tuple[_H1ScopeWire, _H1ScopeWire]:
        """Return the exact ISSUE and current owner exchanges held by a P capability."""
        with self._gate.hold():
            issued = self._completion_scopes.get(id(capability))
            if (
                type(capability) is not _AcceptedH1CompletionScopeCapability
                or issued is None
                or issued[0] is not capability
                or native_cut is not issued[1].native_cap
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 completion scope wire capability/native identity differs"
                )
            cut = issued[1]
            self._replay_completion_scope(capability, native_cut)
            return cut.scope_issue_wire, cut.scope_current_wire

    def _replay_completion_effects_source(
        self,
        scope_cap: object,
        native_cap: object,
        delivery_receipt: object,
        first_path: object,
    ) -> _AuthenticatedCompletionEffectsSource:
        """Return P-held effects inputs only after reopening their one shared cut.

        The public selected-scope DTO is a carrier, never an input authority:
        all of its bytes are reread from P's physical anchor and all of its
        logical values are decoded from P-retained owner exchanges here.
        """
        with self._gate.hold():
            issued_scope = self._completion_scopes.get(id(scope_cap))
            issued_delivery = self._delivery_receipts.get(id(delivery_receipt))
            if (
                type(scope_cap) is not _AcceptedH1CompletionScopeCapability
                or type(native_cap) is not H1CurrentNativeMemberSourceCut
                or type(first_path) is not H1FirstPathCapture
                or type(delivery_receipt) is not _H1PreRequestDeliveryReceipt
                or issued_scope is None
                or issued_scope[0] is not scope_cap
                or issued_delivery is None
                or issued_delivery.receipt is not delivery_receipt
                or issued_delivery.scope_cap is not scope_cap
                or issued_delivery.native_cap is not native_cap
                or issued_delivery.first_path is not first_path
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 effects source scope/native/delivery identity differs"
                )
            cut = issued_scope[1]
            if cut.native_cap is not native_cap or cut.first_path is not first_path:
                raise H1PreissuanceSourceViolation(
                    "H1 effects source scope/native/first-path identity differs"
                )

            # These replays reauthenticate the live P/E data.  In particular
            # `_replay_delivery_inputs` deliberately remains usable after the
            # completion leg consumed the receipt; consumption prevents reuse
            # as completion input, not evidence replay by this same chain.
            scope = self._replay_completion_scope(scope_cap, native_cap)
            _observation, fence = self._replay_delivery_inputs(delivery_receipt, first_path)
            issue_wire, current_wire = self._replay_completion_scope_wires(scope_cap, native_cap)

            issue_sent, issue_returned = issue_wire.sent, issue_wire.returned
            current_sent, current_returned = current_wire.sent, current_wire.returned
            if (
                type(issue_returned) is not PublicPortSuccess
                or issue_returned.request_id != issue_sent.request_id
                or issue_returned.responder != issue_sent.callee
                or issue_returned.schema_id
                != "chiplog.deployment-trust.issue-hermetic-output-scope-result.v1"
                or type(current_returned) is not PublicPortSuccess
                or current_returned.request_id != current_sent.request_id
                or current_returned.responder != current_sent.callee
                or current_returned.schema_id
                != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
            ):
                raise H1PreissuanceSourceViolation("H1 effects source owner wire differs")
            try:
                issue_outer = decode_trust_owner_call_canonical(issue_sent.canonical_payload)
                current_outer = decode_trust_owner_call_canonical(current_sent.canonical_payload)
                issue_call = H1OwnerCandidateCallV1.model_validate_json(issue_outer.request_bytes)
                issue_result = H1OwnerCandidateV1.model_validate_json(
                    issue_returned.canonical_payload
                )
                current_call = H1OwnerCurrentCallV1.model_validate_json(current_outer.request_bytes)
                current_request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
                    current_call.read_request_bytes
                )
                current_result = H1OwnerCurrentCandidateV1.model_validate_json(
                    current_returned.canonical_payload
                )
                issue_result.check_pinned_call(issue_call)
                current_result.check_pinned_call(current_call)
            except ValueError as error:
                raise H1PreissuanceSourceViolation(
                    "H1 effects source owner wire is malformed"
                ) from error
            if (
                issue_outer.canonical_bytes() != issue_sent.canonical_payload
                or issue_outer.mode != "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
                or current_outer.canonical_bytes() != current_sent.canonical_payload
                or current_outer.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
                or issue_call.canonical_bytes() != issue_outer.request_bytes
                or issue_result.canonical_bytes() != issue_returned.canonical_payload
                or issue_result.scope != scope.scope
                or current_call.canonical_bytes() != current_outer.request_bytes
                or current_request.canonical_bytes() != current_call.read_request_bytes
                or current_result.canonical_bytes() != current_returned.canonical_payload
                or current_result.current != cut.current
                or current_request != cut.original_cut.read
                or current_request.source_anchor != cut.original_cut.cut.issued.anchor
                or current_result.current.source_anchor != cut.original_cut.cut.issued.anchor
            ):
                raise H1PreissuanceSourceViolation("H1 effects source issue/current join differs")

            anchor = cut.original_cut.cut.issued.anchor
            decision = next(
                (
                    raw
                    for key, _, raw in self._runtime._trust._journal.entries()
                    if key == anchor.decision.head
                ),
                None,
            )
            record = self._runtime._trust._materializer.record(
                anchor.decision.head, anchor.record_ordinal
            )
            if (
                decision is None
                or hashlib.sha256(decision).hexdigest() != anchor.decision.fingerprint
                or record is None
                or hashlib.sha256(record).hexdigest() != anchor.record.fingerprint
            ):
                raise H1PreissuanceSourceViolation("H1 effects source physical scope is stale")
            selected_scope = H1SelectedScopeSourceV1(
                anchor=anchor,
                scope=scope.scope,
                selected_decision_bytes=decision,
                selected_record_bytes=record,
                current_request=current_request,
                current_result=current_result.current,
            )
            retained = issue_call.evidence.retained
            source = cut.source
            expected_retained = H1RetainedSelectedWrapperV1(
                initialization_envelope_bytes=source.initialization_envelope_bytes,
                admitted_record_bytes=source.admitted_record_bytes,
                selected_admitted_record_ref=source.selected_admitted_record_ref,
                authentication_result_bytes=source.authentication_result_bytes,
                admitted_record_digest=hashlib.sha256(source.admitted_record_bytes).hexdigest(),
            )
            if retained != expected_retained:
                raise H1PreissuanceSourceViolation("H1 effects source retained origin differs")
            try:
                effects_fence = EffectsNonSchedulerFence.model_validate_json(
                    fence.canonical_bytes()
                )
            except ValueError as error:
                raise H1PreissuanceSourceViolation(
                    "H1 effects source worker fence cannot be projected"
                ) from error
            return _AuthenticatedCompletionEffectsSource(
                selected_scope=selected_scope,
                retained_origin=retained,
                fence=effects_fence,
            )

    def _final_fence_snapshot(
        self, session: object, sent: PublicPortCall | None = None
    ) -> tuple[object, _H1PreterminalScopeSnapshot]:
        """Authenticate the enrolled B chain and its exact terminal frame under P's gate."""
        from chiplog.composition.h1_completion_preparation_session import (
            H1CompletionPreparationSession,
            H1CompletionSessionCut,
            _H1FinalFenceInputs,
        )
        from chiplog.composition.h1_live_completion_enrollment import (
            _H1LiveCompletionEnrollment,
        )

        if type(session) is not H1CompletionPreparationSession or (
            sent is not None and type(sent) is not PublicPortCall
        ):
            raise H1PreissuanceSourceViolation("H1 final fence session or terminal call is foreign")
        enrollment = getattr(self._runtime, "_h1_live_completion_enrollment", None)
        cut = session._cut
        if (
            type(enrollment) is not _H1LiveCompletionEnrollment
            or session._sources._runtime is not self._runtime
            or session._sources._gate is not self._gate
            or type(cut) is not H1CompletionSessionCut
        ):
            raise H1PreissuanceSourceViolation("H1 final fence enrollment is unavailable")
        enrollment._require_live(session, cut)
        inputs = session._replay_final_fence_inputs(self)
        if type(inputs) is not _H1FinalFenceInputs:
            raise H1PreissuanceSourceViolation("H1 final fence inputs are not B-issued")
        if (
            inputs.first_path is not cut.first_path
            or inputs.native_cap._capture is not cut.first_path
            or inputs.completion_exchange is not session._completion_exchange
            or inputs.conversation_exchange is not session._conversation_exchange
            or inputs.effects_exchange is not session._effects_exchange
        ):
            raise H1PreissuanceSourceViolation("H1 final fence B identity chain differs")
        exchanges = (
            inputs.completion_exchange,
            inputs.conversation_exchange,
            inputs.effects_exchange,
        )
        if any(
            exchange.role != role
            or type(exchange.sent) is not PublicPortCall
            or type(exchange.returned) is not PublicPortSuccess
            or exchange.returned.request_id != exchange.sent.request_id
            or exchange.returned.responder != exchange.sent.callee
            or exchange.returned_at_ns < exchange.sent_at_ns
            or exchange.returned_at_ns >= exchange.sent.budget.absolute_deadline_ns
            for exchange, role in zip(
                exchanges, ("completion", "conversation", "effects"), strict=True
            )
        ) or (
            sent is not None
            and not (
                exchanges[0].returned_at_ns
                <= exchanges[1].sent_at_ns
                <= exchanges[1].returned_at_ns
                <= exchanges[2].sent_at_ns
                <= exchanges[2].returned_at_ns
                <= sent.budget.absolute_deadline_ns
            )
        ):
            raise H1PreissuanceSourceViolation("H1 final fence owner exchange order differs")
        effects_source = self._replay_completion_effects_source(
            inputs.scope_cap, inputs.native_cap, inputs.delivery_receipt, inputs.first_path
        )
        try:
            effects_call = H1LocalCommentaryOwnerCallV1.model_validate_json(
                inputs.effects_exchange.sent.canonical_payload
            )
        except ValueError as error:
            raise H1PreissuanceSourceViolation(
                "H1 final fence effects call is malformed"
            ) from error
        if (
            effects_call.canonical_bytes() != inputs.effects_exchange.sent.canonical_payload
            or effects_call.request.selected_scope != effects_source.selected_scope
        ):
            raise H1PreissuanceSourceViolation("H1 final fence effects scope differs")
        from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity

        fingerprint, owner_frame = (
            _terminal_call_identity(sent) if sent is not None else (None, None)
        )
        return enrollment, _H1PreterminalScopeSnapshot(
            session=session,
            first_path=inputs.first_path,
            native_cap=inputs.native_cap,
            scope_cap=inputs.scope_cap,
            delivery_receipt=inputs.delivery_receipt,
            effects_exchange=inputs.effects_exchange,
            effects_source=effects_source,
            terminal_sent=sent,
            terminal_call_fingerprint=fingerprint,
            terminal_owner_frame_bytes=owner_frame,
        )

    @staticmethod
    def _require_fresh_current_wire(wire: _H1ScopeWire, selected: H1SelectedScopeSourceV1) -> None:
        sent, returned = wire.sent, wire.returned
        if (
            type(returned) is not PublicPortSuccess
            or returned.request_id != sent.request_id
            or returned.responder != sent.callee
            or returned.schema_id
            != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
            or wire.returned_at_ns < wire.sent_at_ns
            or wire.returned_at_ns >= sent.budget.absolute_deadline_ns
        ):
            raise H1PreissuanceSourceViolation("H1 final fence current wire differs")
        try:
            outer = decode_trust_owner_call_canonical(sent.canonical_payload)
            call = H1OwnerCurrentCallV1.model_validate_json(outer.request_bytes)
            request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
                call.read_request_bytes
            )
            result = H1OwnerCurrentCandidateV1.model_validate_json(returned.canonical_payload)
            result.check_pinned_call(call)
        except ValueError as error:
            raise H1PreissuanceSourceViolation(
                "H1 final fence current wire is malformed"
            ) from error
        if (
            outer.canonical_bytes() != sent.canonical_payload
            or outer.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
            or call.canonical_bytes() != outer.request_bytes
            or request.canonical_bytes() != call.read_request_bytes
            or result.canonical_bytes() != returned.canonical_payload
            or request != selected.current_request
            or result.current != selected.current_result
        ):
            raise H1PreissuanceSourceViolation("H1 final fence current pair differs from effects")

    async def _prepare_terminal_scope_proof(self, session: object) -> _H1PreterminalCompletionProof:
        """Freshly prove call-independent P currentness before B builds terminal IPC."""
        with self._gate.hold():
            enrollment, snapshot = self._final_fence_snapshot(session)
        current, wire = await self._runtime._read_current_hermetic_output_scope_with_wire(
            snapshot.effects_source.selected_scope.current_request
        )
        with self._gate.hold():
            current_enrollment, replayed = self._final_fence_snapshot(session)
            if current_enrollment is not enrollment or replayed != snapshot:
                raise H1PreissuanceSourceViolation(
                    "H1 preterminal source changed during owner read"
                )
            self._require_fresh_current_wire(wire, snapshot.effects_source.selected_scope)
            if current != snapshot.effects_source.selected_scope.current_result:
                raise H1PreissuanceSourceViolation("H1 preterminal current result differs")
            proof = object.__new__(_H1PreterminalCompletionProof)
            self._preterminal_proofs[id(proof)] = _IssuedPreterminalCompletionProof(
                proof, session, enrollment, session._cut, snapshot, wire
            )
            return proof

    def _bind_terminal_clearance(
        self, session: object, proof: object, sent: PublicPortCall
    ) -> _H1TerminalClearance:
        """Consume a fresh P proof to reserve one exact, unrenewable terminal frame."""
        with self._gate.hold():
            issued = self._preterminal_proofs.pop(id(proof), None)
            if (
                type(proof) is not _H1PreterminalCompletionProof
                or issued is None
                or issued.proof is not proof
                or issued.session is not session
                or issued.consumed
                or type(sent) is not PublicPortCall
            ):
                raise H1PreissuanceSourceViolation("H1 preterminal proof is unavailable or expired")
            issued.consumed = True
            from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity

            fingerprint, owner_frame = _terminal_call_identity(sent)
            if (
                issued.enrollment
                is not getattr(self._runtime, "_h1_live_completion_enrollment", None)
                or issued.snapshot.session is not session
                or getattr(session, "_cut", None) is not issued.cut
                or getattr(session, "_effects_exchange", None)
                is not issued.snapshot.effects_exchange
                or issued.snapshot.terminal_sent is not None
                or issued.snapshot.terminal_call_fingerprint is not None
                or issued.snapshot.terminal_owner_frame_bytes is not None
                or issued.snapshot.effects_exchange.returned_at_ns
                > sent.budget.absolute_deadline_ns
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 preterminal proof source changed before bind"
                )
            snapshot = replace(
                issued.snapshot,
                terminal_sent=sent,
                terminal_call_fingerprint=fingerprint,
                terminal_owner_frame_bytes=owner_frame,
            )
            clearance = issued.enrollment._reserve_terminal_clearance(
                session=session,
                cut=issued.cut,
                sent=sent,
                scope_snapshot=snapshot,
                preterminal_wire=issued.wire,
            )
            self._preterminal_clearances[id(clearance)] = (clearance, snapshot, issued.wire)
            return clearance

    def _check_terminal_clearance_current(self, clearance: object, sent: PublicPortCall) -> None:
        """Broker-admission replay: only a P-held preterminal snapshot is accepted."""
        self._gate.require_held()
        entry = self._preterminal_clearances.get(id(clearance))
        if entry is None or entry[0] is not clearance or entry[1].terminal_sent is not sent:
            raise H1PreissuanceSourceViolation("H1 terminal clearance is not P-issued")
        session = entry[1].session
        from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity

        if _terminal_call_identity(sent) != (
            entry[1].terminal_call_fingerprint,
            entry[1].terminal_owner_frame_bytes,
        ):
            raise H1PreissuanceSourceViolation("H1 terminal call changed after clearance")
        _enrollment, replayed = self._final_fence_snapshot(session, sent)
        if replayed != entry[1]:
            raise H1PreissuanceSourceViolation("H1 terminal clearance source is stale")
        self._require_fresh_current_wire(entry[2], entry[1].effects_source.selected_scope)
        try:
            outer = decode_trust_owner_call_canonical(entry[2].sent.canonical_payload)
            call = H1OwnerCurrentCallV1.model_validate_json(outer.request_bytes)
            request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
                call.read_request_bytes
            )
            candidate = H1OwnerCurrentCandidateV1.model_validate_json(
                entry[2].returned.canonical_payload
            )
            candidate.check_pinned_call(call)
            current = self._runtime._replay_current_hermetic_output_scope_held(
                request, candidate, callee=entry[2].sent.callee
            )
        except (AttributeError, LoopRejected, ValueError) as error:
            raise H1PreissuanceSourceViolation(
                "H1 terminal clearance current replay is unavailable"
            ) from error
        if (
            type(current) is not CurrentHermeticExecutionScopeV1
            or current != entry[1].effects_source.selected_scope.current_result
        ):
            raise H1PreissuanceSourceViolation("H1 terminal clearance current replay is stale")

    async def _capture_final_completion_fence(self, session: object) -> _H1FinalCompletionFence:
        """Retain the distinct post-terminal owner read for one admitted B chain."""
        with self._gate.hold():
            from chiplog.composition.h1_completion_preparation_session import _H1FinalFenceInputs

            inputs = session._replay_final_fence_inputs(self)
            if type(inputs) is not _H1FinalFenceInputs or inputs.terminal_work_exchange is None:
                raise H1PreissuanceSourceViolation(
                    "H1 final fence terminal exchange is unavailable"
                )
            terminal = inputs.terminal_work_exchange
            clearance = getattr(session, "_terminal_clearance", None)
            entry = self._preterminal_clearances.get(id(clearance))
            if (
                entry is None
                or entry[0] is not clearance
                or entry[1].session is not session
                or entry[1].terminal_sent is not terminal.sent
                or terminal.role != "terminal_work"
                or type(terminal.returned) is not PublicPortSuccess
                or terminal.returned.request_id != terminal.sent.request_id
                or terminal.returned.responder != terminal.sent.callee
                or terminal.returned_at_ns < terminal.sent_at_ns
                or terminal.returned_at_ns >= terminal.sent.budget.absolute_deadline_ns
            ):
                raise H1PreissuanceSourceViolation("H1 final fence terminal clearance differs")
            enrollment, snapshot = self._final_fence_snapshot(session, terminal.sent)
            if snapshot != entry[1]:
                raise H1PreissuanceSourceViolation("H1 final fence source differs from clearance")
            enrollment._require_admitted_terminal(clearance, session=session, sent=terminal.sent)
            if terminal.returned_at_ns < entry[2].returned_at_ns:
                raise H1PreissuanceSourceViolation("H1 terminal precedes its P clearance")
        current, wire = await self._runtime._read_current_hermetic_output_scope_with_wire(
            snapshot.effects_source.selected_scope.current_request
        )
        with self._gate.hold():
            inputs = session._replay_final_fence_inputs(self)
            terminal = inputs.terminal_work_exchange
            if terminal is None or terminal.sent is not snapshot.terminal_sent:
                raise H1PreissuanceSourceViolation("H1 final fence terminal exchange changed")
            enrollment_now, replayed = self._final_fence_snapshot(session, terminal.sent)
            enrollment_now._require_admitted_terminal(
                clearance, session=session, sent=terminal.sent
            )
            if enrollment_now is not enrollment or replayed != snapshot:
                raise H1PreissuanceSourceViolation(
                    "H1 final fence source changed during owner read"
                )
            self._require_fresh_current_wire(wire, snapshot.effects_source.selected_scope)
            if (
                current != snapshot.effects_source.selected_scope.current_result
                or wire.sent is entry[2].sent
                or wire.sent.request_id == entry[2].sent.request_id
                or terminal.returned_at_ns > wire.sent_at_ns
                or any(value.session is session for value in self._final_completion_fences.values())
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 final fence current wire is not distinct/current"
                )
            fence = object.__new__(_H1FinalCompletionFence)
            self._final_completion_fences[id(fence)] = _IssuedFinalCompletionFence(
                fence, session, snapshot, clearance, wire
            )
            return fence

    def _replay_final_completion_fence(self, fence: object, session: object) -> _H1ScopeWire:
        """Consume the exact final P wire once for B's synchronous capability mint."""
        with self._gate.hold():
            issued = self._final_completion_fences.get(id(fence))
            if (
                type(fence) is not _H1FinalCompletionFence
                or issued is None
                or issued.fence is not fence
                or issued.session is not session
                or issued.consumed
            ):
                raise H1PreissuanceSourceViolation("H1 final completion fence is not P-issued")
            enrollment, replayed = self._final_fence_snapshot(
                session, issued.snapshot.terminal_sent
            )
            enrollment._require_admitted_terminal(
                issued.clearance, session=session, sent=issued.snapshot.terminal_sent
            )
            if replayed != issued.snapshot:
                raise H1PreissuanceSourceViolation("H1 final completion fence source is stale")
            self._require_fresh_current_wire(
                issued.wire, issued.snapshot.effects_source.selected_scope
            )
            issued.consumed = True
            return issued.wire

    def _replay_conversation_policy(
        self, scope_cap: object, native_cap: object
    ) -> _AuthenticatedConversationPolicyInputs:
        """Replay P-held original/scope policy material for A without IPC.

        Capability identity, rather than equality of public-shaped carriers,
        is the authority boundary.  The returned values are evidence only;
        the later A and owner-current fences still establish their own
        currentness before any conversation work.
        """
        with self._gate.hold():
            # Keep the existing complete scope replay as the first gate-held
            # check.  It authenticates the exact scope/native capability join
            # and reopens the selected source, custody, and policy chain.
            scope = self._replay_completion_scope(scope_cap, native_cap)
            issued = self._completion_scopes.get(id(scope_cap))
            if (
                type(scope_cap) is not _AcceptedH1CompletionScopeCapability
                or issued is None
                or issued[0] is not scope_cap
                or native_cap is not issued[1].native_cap
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 conversation policy capability/native identity differs"
                )
            cut = issued[1]
            # Do not project a caller DTO or merely reuse the scope carrier:
            # reopen the retained selected V3 -> physical V2 original here.
            self._completion_scope_snapshot(cut.first_path, native_cap, cut.original)
            workspace = R13Workspace(self._runtime)
            try:
                original = workspace.open_h1_workspace_issuance().load(
                    cut.original_cut.selected.issuance_ref
                )
            except ValueError as error:
                raise H1PreissuanceSourceViolation(
                    "H1 conversation policy original issuance is unavailable"
                ) from error
            if type(original) is not H1OriginalWorkspaceIssuanceV2:
                raise H1PreissuanceSourceViolation(
                    "H1 conversation policy original issuance is not physical V2"
                )
            original_bytes = original.canonical_bytes()
            try:
                policy = decode_h1_workspace_policy_v2(
                    base64.b64decode(
                        original.sources.policy_payload_base64.encode("ascii"), validate=True
                    )
                )
            except ValueError as error:
                raise H1PreissuanceSourceViolation(
                    "H1 conversation policy original V2 policy is malformed"
                ) from error
            entry = self._custody.select(
                cut.original_cut.cut.prepared.run.tenant,
                cut.original_cut.cut.prepared.run.principal,
                _CHANNEL,
            )
            if (
                policy != cut.original_cut.cut.workspace_policy
                or policy.registration.accepted_policy != scope.policy_ref
                or policy.registration.accepted_policy_bytes_base64
                != base64.b64encode(scope.policy_bytes).decode("ascii")
                or policy.registration.generation != entry.generation
                or policy.registration.custody_entry_digest
                != hashlib.sha256(entry.canonical_bytes()).hexdigest()
                or original.tenant != policy.tenant
                or original.run_id != cut.original_cut.selected.started_run.run_id
                or original.started_run_head != cut.original_cut.selected.started_run.head
                or original.turn_id != cut.original_cut.selected.started_run.turns[0].turn_id
                or original.worker_session != cut.original_cut.selected.started_run.worker_session
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 conversation policy original/scope/custody join differs"
                )
            return _AuthenticatedConversationPolicyInputs(
                workspace_policy=cut.original_cut.cut.workspace_policy,
                recipient=scope.recipient,
                scope_policy_ref=scope.policy_ref,
                scope_policy_bytes=scope.policy_bytes,
                custody_entry_generation=entry.generation,
                custody_entry_digest=hashlib.sha256(entry.canonical_bytes()).hexdigest(),
                original_issuance_ref=cut.original_cut.selected.issuance_ref,
                original_issuance_bytes=original_bytes,
                original_tenant=original.tenant,
                original_run_id=original.run_id,
                original_started_run_head=original.started_run_head,
                original_turn_id=original.turn_id,
                original_worker_session=original.worker_session,
            )

    def _prepare_delivery_inputs(
        self,
        first_path_capture: object,
        native_cap: object,
        accepted_scope_capability: object,
        member_receipt: object,
        worker_receipt: object,
    ) -> _H1PreRequestDeliveryReceipt:
        """Bind source-owned E receipts into one inert P delivery receipt.

        Member and worker journal appends are independently replay-safe.  A
        partial append therefore grants nothing here: this method issues no P
        receipt until both concrete owner receipts reopen against the one
        identity-held native capability under the installed shared gate.
        """
        with self._gate.hold():
            if (
                type(first_path_capture) is not H1FirstPathCapture
                or type(native_cap) is not H1CurrentNativeMemberSourceCut
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 delivery receipt requires issuer-owned first-path and native capabilities"
                )
            if native_cap._capture is not first_path_capture:
                raise H1PreissuanceSourceViolation(
                    "H1 delivery receipt first-path capability differs"
                )
            observation, fence = self._delivery_observation_from_owner_receipts(
                native_cap, accepted_scope_capability, member_receipt, worker_receipt
            )
            receipt = object.__new__(_H1PreRequestDeliveryReceipt)
            self._delivery_receipts[id(receipt)] = _IssuedPreRequestDelivery(
                receipt,
                first_path_capture,
                native_cap,
                accepted_scope_capability,
                member_receipt,
                worker_receipt,
                observation,
                fence,
            )
            return receipt

    def _replay_delivery_inputs(
        self, receipt: object, first_path_capture: object
    ) -> tuple[DeliveryObservation, NonSchedulerFence]:
        """Reopen the P receipt through the same live owners before B consumes it."""
        with self._gate.hold():
            issued = self._delivery_receipts.get(id(receipt))
            if (
                type(receipt) is not _H1PreRequestDeliveryReceipt
                or issued is None
                or issued.receipt is not receipt
            ):
                raise H1PreissuanceSourceViolation("H1 delivery receipt is not P-issued")
            if first_path_capture is not issued.first_path:
                raise H1PreissuanceSourceViolation(
                    "H1 delivery receipt first-path capability differs"
                )
            observation, fence = self._delivery_observation_from_owner_receipts(
                issued.native_cap,
                issued.scope_cap,
                issued.member_receipt,
                issued.worker_receipt,
            )
            if (
                observation != issued.observation
                or fence.canonical_bytes() != issued.fence.canonical_bytes()
            ):
                raise H1PreissuanceSourceViolation("H1 delivery receipt source continuity is stale")
            return observation, fence

    def _consume_delivery_inputs(
        self, receipt: object, first_path_capture: object
    ) -> tuple[DeliveryObservation, NonSchedulerFence]:
        """Atomically hand one P receipt to B after its final pre-IPC replay."""
        with self._gate.hold():
            issued = self._delivery_receipts.get(id(receipt))
            if (
                type(receipt) is not _H1PreRequestDeliveryReceipt
                or issued is None
                or issued.receipt is not receipt
                or issued.consumed
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 delivery receipt is absent or already consumed"
                )
            if first_path_capture is not issued.first_path:
                raise H1PreissuanceSourceViolation(
                    "H1 delivery receipt first-path capability differs"
                )
            observation, fence = self._delivery_observation_from_owner_receipts(
                issued.native_cap,
                issued.scope_cap,
                issued.member_receipt,
                issued.worker_receipt,
            )
            if (
                observation != issued.observation
                or fence.canonical_bytes() != issued.fence.canonical_bytes()
            ):
                raise H1PreissuanceSourceViolation("H1 delivery receipt source continuity is stale")
            issued.consumed = True
            return observation, fence

    def _delivery_observation_from_owner_receipts(
        self,
        native_cap: H1CurrentNativeMemberSourceCut,
        accepted_scope_capability: object,
        member_receipt: object,
        worker_receipt: object,
    ) -> tuple[DeliveryObservation, NonSchedulerFence]:
        """Build values only from live owner replays, never caller-shaped DTOs."""
        self._gate.require_held()
        from chiplog.composition.h1_pre_request_member_evidence import (
            H1PreRequestMemberEvidence,
            H1PreRequestMemberEvidenceReceipt,
        )
        from chiplog.composition.h1_pre_request_worker_evidence import (
            H1PreRequestWorkerEvidence,
            H1PreRequestWorkerEvidenceReceipt,
        )

        native_sources = getattr(self._runtime, "_h1_native_member_sources", None)
        member_owner = getattr(self._runtime, "_h1_pre_request_member_evidence", None)
        worker_owner = getattr(self._runtime, "_h1_pre_request_worker_evidence", None)
        if (
            type(native_sources) is not H1NativeMemberSources
            or type(member_owner) is not H1PreRequestMemberEvidence
            or type(worker_owner) is not H1PreRequestWorkerEvidence
            or member_owner._gate is not self._gate
            or worker_owner._gate is not self._gate
            or member_owner._native_sources is not native_sources
            or worker_owner._native_sources is not native_sources
        ):
            raise H1PreissuanceSourceViolation(
                "H1 delivery evidence owners are not mounted together"
            )
        if (
            type(member_receipt) is not H1PreRequestMemberEvidenceReceipt
            or type(worker_receipt) is not H1PreRequestWorkerEvidenceReceipt
        ):
            raise H1PreissuanceSourceViolation("H1 delivery evidence receipts are not owner-issued")

        native = native_sources.replay_current(native_cap)
        scope = self._replay_completion_scope(accepted_scope_capability, native_cap)
        members = member_owner._replay_members(member_receipt)
        _worker_locator, worker_record, worker_head = worker_owner._replay_worker(worker_receipt)
        parts = native_sources.project_current(native_cap)
        occurrences = native_cap._occurrences
        if len(parts) != len(occurrences) or len(members) != len(occurrences):
            raise H1PreissuanceSourceViolation("H1 delivery member evidence vector is incomplete")

        run = native.source.complete_ordered_run_lineage[-1]
        captured = native.source.complete_ordered_run_lineage[-2]
        if (
            len(captured.turns) != 1
            or len(captured.turns[0].attempts) != 1
            or run.tenant != scope.scope.tenant_id
            or run.principal != scope.scope.principal_id
            or run.origin.recipient != scope.recipient
        ):
            raise H1PreissuanceSourceViolation("H1 delivery native/scope identity differs")
        attempt = captured.turns[0].attempts[0]
        if attempt.response_base64 is None:
            raise H1PreissuanceSourceViolation("H1 delivery captured response is absent")
        try:
            captured_response = base64.b64decode(attempt.response_base64, validate=True)
        except ValueError:
            raise H1PreissuanceSourceViolation(
                "H1 delivery captured response is malformed"
            ) from None

        history: list[HistoricalEnvelope] = []
        for index, (part, occurrence, replayed) in enumerate(
            zip(parts, occurrences, members, strict=True)
        ):
            _locator, record, projection = replayed
            try:
                member = VisibilityMember.model_validate_json(occurrence.member_bytes)
            except ValueError:
                raise H1PreissuanceSourceViolation(
                    "H1 delivery native member bytes are malformed"
                ) from None
            value = record._value
            if (
                part.occurrence != occurrence
                or occurrence.member_index != index
                or member.canonical_bytes() != occurrence.member_bytes
                or member.label != occurrence.original_label
                or value["run_id"] != run.run_id
                or value["turn_id"] != occurrence.turn_id
                or value["attempt_id"] != occurrence.attempt_id
                or value["manifest_digest"] != occurrence.manifest_digest
                or value["member_index"] != index
                or value["member_digest"] != occurrence.member_digest
            ):
                raise H1PreissuanceSourceViolation(
                    "H1 delivery member receipt differs from native cut"
                )
            history.append(
                HistoricalEnvelope(
                    content=ExactHead(
                        identity=member.record_id,
                        head=member.revision_head,
                        fingerprint=hashlib.sha256(occurrence.member_bytes).hexdigest(),
                    ),
                    visibility=ExactHead(
                        identity=attempt.attempt_id,
                        head=occurrence.manifest_digest,
                        fingerprint=occurrence.manifest_digest,
                    ),
                    provenance=projection.provenance,
                    disclosure=projection.disclosure,
                    label=occurrence.original_label,
                    narrowing=(projection.narrowing,),
                )
            )

        worker = worker_record._value
        try:
            worker_fence_value = cast(dict[str, str], worker["fence"])
            worker_fence = NonSchedulerFence(
                lineage=NotApplicable(),
                physical_root=NotApplicable(),
                lease=NotApplicable(),
                clock_proof=NotApplicable(),
                run_id=worker_fence_value["run_id"],
                run_head=worker_fence_value["run_head"],
                worker_session_id=worker_fence_value["worker_session"],
                runtime_generation=worker_fence_value["runtime_generation"],
            )
        except KeyError, TypeError, ValueError:
            raise H1PreissuanceSourceViolation("H1 delivery worker receipt is malformed") from None
        if (
            worker.get("tenant") != run.tenant
            or worker.get("principal") != run.principal
            or worker_fence.run_id != run.run_id
            or worker_fence.run_head != run.head
            or worker_fence.worker_session_id != run.worker_session
        ):
            raise H1PreissuanceSourceViolation("H1 delivery worker receipt differs from native cut")
        return (
            DeliveryObservation(
                tenant=run.tenant,
                run=ExactHead(identity=run.run_id, head=run.head, fingerprint=run.digest()),
                turn_id=captured.turns[0].turn_id,
                captured_response=captured_response,
                source_frontier=native.source.tenant_commit_sequence,
                origin=run.origin,
                recipients=(scope.recipient,),
                history=tuple(history),
                queries=(),
                policy=scope.policy_ref,
                worker_fence=worker_head,
            ),
            worker_fence,
        )

    def build_delivery_observation(
        self,
        selection: H1PreissuanceSelection,
        authenticated_completion_inputs: H1AuthenticatedCompletionInputs,
    ) -> object:
        del authenticated_completion_inputs
        self._cut(selection)
        raise H1PreissuanceSourceViolation("H1 completion fence is not wired")

    @staticmethod
    def _historical_bytes(value: object) -> bytes:
        """Commit inert historical projections without admitting their DTO shape."""

        try:
            return json.dumps(
                value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        except (TypeError, ValueError) as error:
            raise _HistoricalRecoverySourceIntegrity(
                "historical P projection cannot be committed"
            ) from error

    @classmethod
    def _historical_conversation_bytes(cls, value: _AuthenticatedConversationPolicyInputs) -> bytes:
        return cls._historical_bytes(
            {
                "workspace_policy": value.workspace_policy.model_dump(mode="json"),
                "recipient": value.recipient.model_dump(mode="json"),
                "scope_policy_ref": value.scope_policy_ref.model_dump(mode="json"),
                "scope_policy_bytes_base64": base64.b64encode(value.scope_policy_bytes).decode(),
                "custody_entry_generation": value.custody_entry_generation,
                "custody_entry_digest": value.custody_entry_digest,
                "original_issuance_ref": value.original_issuance_ref.model_dump(mode="json"),
                "original_issuance_bytes_base64": base64.b64encode(
                    value.original_issuance_bytes
                ).decode(),
                "original_tenant": value.original_tenant,
                "original_run_id": value.original_run_id,
                "original_started_run_head": value.original_started_run_head,
                "original_turn_id": value.original_turn_id,
                "original_worker_session": value.original_worker_session,
            }
        )

    def _historical_recovery_projection(
        self, original_identity: object, original_fingerprint: object, selected_seal: object
    ) -> tuple[_AuthenticatedConversationPolicyInputs, _HistoricalEffectsSource]:
        """Reopen only immutable selected V2 P evidence under the installed gate.

        This intentionally never asks the live P owner for CURRENT and never
        reads current custody or inventory.  The authority-cut full source and
        the selected V3/original issuance authenticate the historical cut;
        the retained P sibling only supplies its two accepted owner exchanges.
        """

        try:
            native = H1V2RecoveryNativeSource(self._runtime).select(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
            )
            carrier = H1FirstPathSources(self._runtime).issue_historical_v2_cut(
                original_identity, original_fingerprint, selected_seal
            )
        except (TypeError, ValueError) as error:
            raise _HistoricalRecoverySourceIntegrity(
                "historical P native authority cut is invalid"
            ) from error
        if (
            carrier.seal.decision_id != native.seal.decision_id
            or carrier.seal.decision_fingerprint != native.seal.decision_fingerprint
            or carrier.seal.raw_bytes != native.seal.raw_bytes
            or carrier.source.selected_response_seal != selected_seal
        ):
            raise _HistoricalRecoverySourceIntegrity(
                "historical P authority cut differs from selected native seal"
            )
        try:
            decision = json.loads(native.seal.raw_bytes)
            if (
                not isinstance(decision, dict)
                or json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()
                != native.seal.raw_bytes
            ):
                raise ValueError("selected DECIDED bytes are noncanonical")
            raw_anchor = decision.get("h1_preseal_pe_anchor")
            if not isinstance(raw_anchor, str):
                raise _HistoricalRecoverySourceUnsupported("historical P anchor is unsupported")
            anchor = decode_h1_preseal_pe_anchor_record(raw_anchor.encode())
            raw_wires = decision.get("h1_preseal_p_scope_wires_v1")
            if raw_wires is None:
                raise _HistoricalRecoverySourceUnsupported(
                    "historical P scope-wire residual is unsupported"
                )
            p = cast(dict[str, object], anchor.as_dict()["p"])
            wires = decode_h1_preseal_p_scope_wires(
                self._historical_bytes(raw_wires),
                anchor_binding=anchor.binding,
                issue_digest=cast(str, p["accepted_issue_wire_digest"]),
                current_digest=cast(str, p["accepted_current_wire_digest"]),
            ).as_dict()
            issue = cast(dict[str, object], wires["issue"])
            current = cast(dict[str, object], wires["current"])
            issue_sent = base64.b64decode(cast(str, issue["sent_payload_base64"]), validate=True)
            issue_returned = base64.b64decode(
                cast(str, issue["returned_payload_base64"]), validate=True
            )
            current_sent = base64.b64decode(
                cast(str, current["sent_payload_base64"]), validate=True
            )
            current_returned = base64.b64decode(
                cast(str, current["returned_payload_base64"]), validate=True
            )
            issue_outer = decode_trust_owner_call_canonical(issue_sent)
            current_outer = decode_trust_owner_call_canonical(current_sent)
            issue_call = H1OwnerCandidateCallV1.model_validate_json(issue_outer.request_bytes)
            issue_result = H1OwnerCandidateV1.model_validate_json(issue_returned)
            current_call = H1OwnerCurrentCallV1.model_validate_json(current_outer.request_bytes)
            current_request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
                current_call.read_request_bytes
            )
            current_result = H1OwnerCurrentCandidateV1.model_validate_json(current_returned)
            issue_result.check_pinned_call(issue_call)
            current_result.check_pinned_call(current_call)
            if (
                issue_outer.mode != "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
                or current_outer.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
                or issue_outer.canonical_bytes() != issue_sent
                or current_outer.canonical_bytes() != current_sent
                or issue_call.canonical_bytes() != issue_outer.request_bytes
                or issue_result.canonical_bytes() != issue_returned
                or current_call.canonical_bytes() != current_outer.request_bytes
                or current_request.canonical_bytes() != current_call.read_request_bytes
                or current_result.canonical_bytes() != current_returned
            ):
                raise ValueError("retained P exchange is noncanonical")
            scope = issue_result.scope
            scope_bytes = base64.b64decode(cast(str, p["scope_bytes_base64"]), validate=True)
            policy_bytes = base64.b64decode(cast(str, p["policy_bytes_base64"]), validate=True)
            policy = HermeticOutputPolicyV1.model_validate_json(policy_bytes)
            scope_ref = ExactHead.model_validate(p["scope_ref"])
            policy_ref = ExactHead.model_validate(p["policy_ref"])
            raw_recipient = cast(dict[str, object], p["recipient"])
            canonical_address_base64 = cast(str, raw_recipient["canonical_address_base64"])
            canonical_address = base64.b64decode(canonical_address_base64, validate=True)
            if base64.b64encode(canonical_address).decode() != canonical_address_base64:
                raise ValueError("historical P recipient address base64 is noncanonical")
            recipient = ProviderRecipient.model_validate(
                {
                    "provider_id": raw_recipient["provider_id"],
                    "account_id": raw_recipient["account_id"],
                    "recipient_id": raw_recipient["recipient_id"],
                    "endpoint": raw_recipient["endpoint"],
                    "canonical_address": canonical_address,
                    "credential_binding": raw_recipient["credential_binding"],
                }
            )
            normalized_recipient = recipient.model_dump(mode="json")
            normalized_recipient["canonical_address_base64"] = base64.b64encode(
                recipient.canonical_address
            ).decode()
            del normalized_recipient["canonical_address"]
            if normalized_recipient != raw_recipient:
                raise ValueError("historical P recipient differs from retained anchor")
            if (
                scope.canonical_bytes() != scope_bytes
                or scope.disclosure_policy.canonical_source_bytes != policy_bytes
                or scope.disclosure_policy.ref != policy_ref
                or scope.recipient != recipient
                or policy.canonical_bytes() != policy_bytes
                or policy.endpoint_ref != recipient.endpoint
                or scope_ref
                != ExactHead(
                    identity=scope.scope_id,
                    head=scope.scope_id + "/" + hashlib.sha256(scope_bytes).hexdigest(),
                    fingerprint=hashlib.sha256(scope_bytes).hexdigest(),
                )
                or current_request.expected_scope_ref != scope_ref
                or current_request.expected_revision != scope.revision
                or current_request.expected_worker_session_id != scope.worker_session_id
                or current_request.source_anchor != current_result.current.source_anchor
                or current_result.current.scope_ref != scope_ref
                or current_request.admitted_authentication_ref != scope.admitted_authentication
                or current_request.selected_resource_observation_ref
                != scope.selected_resource_observation_ref
            ):
                raise ValueError("retained P scope/current join differs")
            entries = self._runtime._trust._journal.entries()
            anchor_ref = current_request.source_anchor
            trust_decision = next(
                (raw for key, _, raw in entries if key == anchor_ref.decision.head), None
            )
            trust_record = self._runtime._trust._materializer.record(
                anchor_ref.decision.head, anchor_ref.record_ordinal
            )
            observation = current_request.expected_trust_observation.physical_journal_head
            observed = next((raw for key, _, raw in entries if key == observation.head), None)
            if (
                trust_decision is None
                or trust_record is None
                or observed is None
                or hashlib.sha256(trust_decision).hexdigest() != anchor_ref.decision.fingerprint
                or hashlib.sha256(trust_record).hexdigest() != anchor_ref.record.fingerprint
                or hashlib.sha256(observed).hexdigest() != observation.fingerprint
            ):
                raise ValueError("retained P trust observation prefix differs")
            selected = select_h1_v3_prepare_for_seal(
                self._runtime, selected_seal=selected_seal, historical=True
            ).prepare
            run = carrier.source.complete_ordered_run_lineage[-1]
            captured = carrier.source.complete_ordered_run_lineage[-2]
            attempt = captured.turns[0].attempts[0]
            slot = self._launch._slot
            binding = anchor.binding
            publication = cast(dict[str, object], binding["publication"])
            selected_prepare = cast(dict[str, object], binding["selected_prepare"])
            command = self._runtime._publication(decision)
            expected_seal = {
                "identity": selected_seal.subject_id,
                "head": selected_seal.revision.head,
                "fingerprint": selected_seal.revision.fingerprint,
            }
            if (
                binding["deployment_id"] != slot.deployment_id
                or binding["database_id"] != original_identity.database_id
                or binding["database_genesis_digest"]
                != self._custody._registry.database_genesis_digest
                or binding["tenant_id"] != run.tenant
                or binding["principal_id"] != run.principal
                or binding["run_id"] != run.run_id
                or binding["turn_id"] != captured.turns[0].turn_id
                or binding["attempt_id"] != attempt.attempt_id
                or binding["prepared_run_head"] != captured.head
                or binding["manifest_digest"] != attempt.manifest.digest()
                or selected_prepare["entry_id"] != selected.decision_id
                or selected_prepare["payload_digest"]
                != hashlib.sha256(selected.decision_bytes).hexdigest()
                or publication
                != {
                    "operation_kind": command.operation_kind,
                    "operation_id": command.idempotency_key,
                    "expected_head": command.expected_head,
                    "request_fingerprint": command.request_fingerprint,
                }
                or binding["selected_response_seal"] != expected_seal
            ):
                raise ValueError("historical P anchor/native binding differs")
            reopen_selected_h1_workspace(self._runtime, selected)
            original = (
                R13Workspace(self._runtime).open_h1_workspace_issuance().load(selected.issuance_ref)
            )
            if type(original) is not H1OriginalWorkspaceIssuanceV2:
                raise _HistoricalRecoverySourceUnsupported(
                    "historical P original workspace issuance is unsupported"
                )
            original_bytes = original.canonical_bytes()
            workspace_policy = decode_h1_workspace_policy_v2(
                base64.b64decode(
                    original.sources.policy_payload_base64.encode("ascii"), validate=True
                )
            )
            if (
                workspace_policy.registration.accepted_policy != policy_ref
                or workspace_policy.registration.accepted_policy_bytes_base64
                != base64.b64encode(policy_bytes).decode("ascii")
                or workspace_policy.registration.generation != p["custody_entry_generation"]
                or workspace_policy.registration.custody_entry_digest != p["custody_entry_digest"]
                or original.tenant != workspace_policy.tenant
                or original.run_id != selected.started_run.run_id
                or original.started_run_head != selected.started_run.head
                or original.turn_id != selected.started_run.turns[0].turn_id
                or original.worker_session != selected.started_run.worker_session
                or scope.tenant_id != run.tenant
                or scope.principal_id != run.principal
                or scope.worker_session_id != run.worker_session
            ):
                raise ValueError("historical P original/native binding differs")
            selected_scope = H1SelectedScopeSourceV1(
                anchor=anchor_ref,
                scope=scope,
                selected_decision_bytes=trust_decision,
                selected_record_bytes=trust_record,
                current_request=current_request,
                current_result=current_result.current,
            )
            conversation = _AuthenticatedConversationPolicyInputs(
                workspace_policy=workspace_policy,
                recipient=recipient,
                scope_policy_ref=policy_ref,
                scope_policy_bytes=policy_bytes,
                custody_entry_generation=cast(int, p["custody_entry_generation"]),
                custody_entry_digest=cast(str, p["custody_entry_digest"]),
                original_issuance_ref=selected.issuance_ref,
                original_issuance_bytes=original_bytes,
                original_tenant=original.tenant,
                original_run_id=original.run_id,
                original_started_run_head=original.started_run_head,
                original_turn_id=original.turn_id,
                original_worker_session=original.worker_session,
            )
            return conversation, _HistoricalEffectsSource(
                selected_scope, issue_call.evidence.retained
            )
        except _HistoricalRecoverySourceUnsupported:
            raise
        except (TypeError, ValueError, KeyError, UnicodeDecodeError) as error:
            raise _HistoricalRecoverySourceIntegrity(
                "historical P residual has an integrity failure"
            ) from error

    def _issue_historical_recovery_source(
        self, *, original_identity: object, original_fingerprint: object, selected_seal: object
    ) -> object:
        """Mint one opaque P source after selecting the immutable historical cut."""

        with self._gate.hold():
            self._assert_launch_and_trust()
            conversation, effects = self._historical_recovery_projection(
                original_identity, original_fingerprint, selected_seal
            )
            receipt = object.__new__(_HistoricalRecoverySourceCapability)
            self._historical_recovery_sources[id(receipt)] = _HistoricalRecoverySourceState(
                receipt,
                original_identity,
                cast(str, original_fingerprint),
                selected_seal,
                self._historical_conversation_bytes(conversation),
                self._historical_bytes(
                    {
                        "selected_scope": effects.selected_scope.model_dump(mode="json"),
                        "retained_origin": effects.retained_origin.model_dump(mode="json"),
                    }
                ),
            )
            return receipt

    def _revoke_historical_recovery_source(self, capability: object) -> None:
        """Burn one exact P recovery capability when its B owner retires it."""

        with self._gate.hold():
            state = self._historical_recovery_sources.get(id(capability))
            if (
                type(capability) is not _HistoricalRecoverySourceCapability
                or state is None
                or state.capability is not capability
            ):
                raise H1PreissuanceSourceViolation("historical P source is not issuer-owned")
            self._historical_recovery_sources.pop(id(capability))

    def _replay_historical_conversation_policy(
        self, capability: object
    ) -> _AuthenticatedConversationPolicyInputs:
        with self._gate.hold():
            state = self._historical_recovery_sources.get(id(capability))
            if (
                type(capability) is not _HistoricalRecoverySourceCapability
                or state is None
                or state.capability is not capability
            ):
                raise H1PreissuanceSourceViolation("historical P source is not issuer-owned")
            conversation, _effects = self._historical_recovery_projection(
                state.original_identity, state.original_fingerprint, state.selected_seal
            )
            if self._historical_conversation_bytes(conversation) != state.conversation_bytes:
                raise _HistoricalRecoverySourceIntegrity("historical P conversation source changed")
            return conversation

    def _replay_historical_effects_source(self, capability: object) -> _HistoricalEffectsSource:
        with self._gate.hold():
            state = self._historical_recovery_sources.get(id(capability))
            if (
                type(capability) is not _HistoricalRecoverySourceCapability
                or state is None
                or state.capability is not capability
            ):
                raise H1PreissuanceSourceViolation("historical P source is not issuer-owned")
            _conversation, effects = self._historical_recovery_projection(
                state.original_identity, state.original_fingerprint, state.selected_seal
            )
            value = {
                "selected_scope": effects.selected_scope.model_dump(mode="json"),
                "retained_origin": effects.retained_origin.model_dump(mode="json"),
            }
            if self._historical_bytes(value) != state.effects_bytes:
                raise _HistoricalRecoverySourceIntegrity("historical P effects source changed")
            return effects


__all__ = ["_H1RuntimePreissuancePort"]
