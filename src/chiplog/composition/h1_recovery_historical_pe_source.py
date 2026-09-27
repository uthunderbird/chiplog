"""Installed reconstruction of a sealed H1 V2 P/E completion input.

The anchor is authenticated only as a sibling of the selected native DECIDED
entry.  This reader consequently never accepts a delivery-shaped DTO, a
current worker, or a recovery ROOT as evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Never, cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import DisclosureLabel
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.agent_loop.delivery_preparation import (
    DeliveryObservation,
    HistoricalEnvelope,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    FirstPathCompletionCutV2,
    PrepareExecutionCompletionFirstPathV2,
    decode_first_path_completion_request,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_first_path_sources import (
    H1FirstPathSources,
    H1HistoricalFirstPathNativeCut,
)
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
from chiplog.composition.h1_preseal_contracts import H1SelectedPrepare
from chiplog.composition.h1_preseal_pe_anchor_records import (
    H1PresealPEAnchorRecordError,
    H1PresealPEAnchorRecordV1,
    decode_h1_preseal_pe_anchor_record,
    h1_preseal_pe_anchor_projection_identity,
)
from chiplog.composition.h1_runtime_preissuance_port import (
    _AuthenticatedConversationPolicyInputs,
    _H1RuntimePreissuancePort,
    _HistoricalEffectsSource,
    _HistoricalRecoverySourceIntegrity,
    _HistoricalRecoverySourceUnsupported,
)
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_seal
from chiplog.composition.h1_v2_recovery_native_source import (
    H1V2RecoveryNativeSource,
    H1V2RecoveryNativeSourceAbsent,
    H1V2RecoveryNativeSourceError,
)


class H1RecoveryHistoricalPESourceError(ValueError):
    """The installed reader cannot issue or reopen this historical projection."""


class H1RecoveryHistoricalPESourceUnavailable(H1RecoveryHistoricalPESourceError):
    """An immutable historical source required by this V2 reader is absent."""


class H1RecoveryHistoricalPESourceIntegrityError(H1RecoveryHistoricalPESourceError):
    """Authenticated native history and its retained P/E anchor disagree."""


class _IssuedProjection:
    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("historical P/E projections are issued only by their reader")

    def __copy__(self) -> Never:
        raise H1RecoveryHistoricalPESourceError("historical P/E projection is not issuer-owned")

    def __deepcopy__(self, memo: object) -> Never:
        del memo
        raise H1RecoveryHistoricalPESourceError("historical P/E projection is not issuer-owned")

    def __reduce__(self) -> Never:
        raise H1RecoveryHistoricalPESourceError("historical P/E projection is not issuer-owned")


@dataclass(slots=True)
class _ProjectionState:
    receipt: _IssuedProjection
    original_identity: DriverCommandIdentityV1
    original_fingerprint: str
    selected_seal: CallSubjectHead
    canonical_request: bytes


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _loop_decision_canonical(value: object) -> bytes:
    """Match the authenticated loop-writer encoding for a selected DECIDED entry."""

    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _anchor_recipient_mapping(recipient: ProviderRecipient) -> dict[str, object]:
    """Match the P anchor's base64 wire shape for its typed recipient."""

    try:
        value = recipient.model_dump(mode="json")
        value["canonical_address_base64"] = base64.b64encode(recipient.canonical_address).decode()
        del value["canonical_address"]
    except (AttributeError, KeyError, TypeError) as error:
        raise H1RecoveryHistoricalPESourceIntegrityError(
            "historical P recipient projection is invalid"
        ) from error
    return value


def _strict_object(raw: bytes, *, name: str) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise H1RecoveryHistoricalPESourceIntegrityError(f"{name} has duplicate JSON keys")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise H1RecoveryHistoricalPESourceIntegrityError(f"{name} is invalid") from error
    if not isinstance(value, dict) or _loop_decision_canonical(value) != raw:
        raise H1RecoveryHistoricalPESourceIntegrityError(f"{name} is noncanonical")
    return value


class H1RecoveryHistoricalPESource:
    """Issue one runtime-owned receipt for an independently selected V2 anchor."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("historical P/E reader requires the canonical installed runtime")
        self._runtime = runtime
        self._native = H1V2RecoveryNativeSource(runtime)
        self._first_path = H1FirstPathSources(runtime)
        self._gate = runtime._authority_gate()
        self._issued: dict[int, _ProjectionState] = {}

    def issue_completion_projection(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> object:
        self._validate_inputs(original_identity, original_fingerprint, selected_seal)
        request = self._reconstruct(original_identity, original_fingerprint, selected_seal)
        receipt = object.__new__(_IssuedProjection)
        self._issued[id(receipt)] = _ProjectionState(
            receipt,
            original_identity,
            original_fingerprint,
            selected_seal,
            request.canonical_bytes(),
        )
        return receipt

    def reconstruct_completion_input(
        self, receipt: object
    ) -> PrepareExecutionCompletionFirstPathV2:
        state = self._issued.pop(id(receipt), None)
        if type(receipt) is not _IssuedProjection or state is None or state.receipt is not receipt:
            raise H1RecoveryHistoricalPESourceError("historical P/E projection is not issuer-owned")
        request = self._reconstruct(
            state.original_identity, state.original_fingerprint, state.selected_seal
        )
        if request.canonical_bytes() != state.canonical_request:
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "historical P/E projection differs on issuer replay"
            )
        return decode_first_path_completion_request(state.canonical_request)

    @staticmethod
    def _validate_inputs(
        original_identity: object, original_fingerprint: object, selected_seal: object
    ) -> None:
        if type(original_identity) is not DriverCommandIdentityV1:
            raise TypeError("historical P/E reader requires the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise H1RecoveryHistoricalPESourceError("original driver fingerprint is invalid")
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError("historical P/E reader requires an exact selected response seal")

    def _reconstruct(
        self,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> PrepareExecutionCompletionFirstPathV2:
        try:
            with self._gate.hold():
                native = self._native.select(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                issue = getattr(self._first_path, "issue_historical_v2_cut", None)
                if not callable(issue):
                    raise H1RecoveryHistoricalPESourceUnavailable(
                        "immutable historical V2 full-cut source is unavailable"
                    )
                carrier = issue(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                if (
                    carrier.seal.decision_id != native.seal.decision_id
                    or carrier.seal.decision_fingerprint != native.seal.decision_fingerprint
                    or carrier.seal.raw_bytes != native.seal.raw_bytes
                    or carrier.source.selected_response_seal != selected_seal
                ):
                    raise H1RecoveryHistoricalPESourceIntegrityError(
                        "full historical V2 cut differs from native selected decision"
                    )
                p_owner = getattr(
                    self._runtime, "_h1_preissuance_registration_source_port", None
                )
                if (
                    type(p_owner) is not _H1RuntimePreissuancePort
                    or p_owner._runtime is not self._runtime
                    or p_owner._gate is not self._gate
                ):
                    raise H1RecoveryHistoricalPESourceUnavailable(
                        "immutable historical P source owner is unavailable"
                    )
                p_capability = p_owner._issue_historical_recovery_source(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                replay_failed = False
                try:
                    conversation = p_owner._replay_historical_conversation_policy(p_capability)
                    effects = p_owner._replay_historical_effects_source(p_capability)
                except BaseException:
                    replay_failed = True
                    raise
                finally:
                    try:
                        p_owner._revoke_historical_recovery_source(p_capability)
                    except BaseException:
                        if not replay_failed:
                            raise
                decision = _strict_object(native.seal.raw_bytes, name="selected V2 decision")
                raw_anchor = decision.get("h1_preseal_pe_anchor")
                if not isinstance(raw_anchor, str):
                    raise H1RecoveryHistoricalPESourceUnavailable(
                        "selected V2 seal lacks a supported historical P/E anchor"
                    )
                anchor = decode_h1_preseal_pe_anchor_record(raw_anchor.encode())
                self._validate_anchor(
                    anchor=anchor,
                    decision=decision,
                    decision_id=native.seal.decision_id,
                    original_identity=original_identity,
                    selected_seal=selected_seal,
                    carrier=carrier,
                    conversation=conversation,
                    effects=effects,
                )
                return self._request(
                    anchor, native.seal.decision_id, carrier.source, conversation
                )
        except H1RecoveryHistoricalPESourceError:
            raise
        except H1V2RecoveryNativeSourceAbsent as error:
            raise H1RecoveryHistoricalPESourceUnavailable(
                "selected native V2 source is unavailable"
            ) from error
        except (H1V2RecoveryNativeSourceError, H1PresealPEAnchorRecordError) as error:
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "selected historical V2 P/E source has an integrity failure"
            ) from error
        except _HistoricalRecoverySourceUnsupported as error:
            raise H1RecoveryHistoricalPESourceUnavailable(
                "historical P owner does not support this selected V2 source"
            ) from error
        except (_HistoricalRecoverySourceIntegrity, H1PreissuanceSourceViolation) as error:
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "historical P owner source has an integrity failure"
            ) from error
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "selected historical V2 P/E source differs"
            ) from error

    def _validate_anchor(
        self,
        *,
        anchor: H1PresealPEAnchorRecordV1,
        decision: dict[str, object],
        decision_id: str,
        original_identity: DriverCommandIdentityV1,
        selected_seal: CallSubjectHead,
        carrier: H1HistoricalFirstPathNativeCut,
        conversation: _AuthenticatedConversationPolicyInputs,
        effects: _HistoricalEffectsSource,
    ) -> None:
        data = anchor.as_dict()
        binding = cast(dict[str, object], data["binding"])
        source = carrier.source
        run = source.complete_ordered_run_lineage[-1]
        captured = source.complete_ordered_run_lineage[-2]
        attempt = captured.turns[0].attempts[0]
        slot = self._native._mount._launch._slot
        if (
            binding["deployment_id"] != slot.deployment_id
            or binding["database_id"] != original_identity.database_id
            or binding["tenant_id"] != source.tenant_id
            or binding["principal_id"] != run.principal
            or binding["run_id"] != run.run_id
            or binding["turn_id"] != captured.turns[0].turn_id
            or binding["attempt_id"] != attempt.attempt_id
            or binding["prepared_run_head"] != captured.head
            or binding["manifest_digest"] != attempt.manifest.digest()
            or cast(dict[str, object], binding["selected_response_seal"])
            != {
                "identity": selected_seal.subject_id,
                "head": selected_seal.revision.head,
                "fingerprint": selected_seal.revision.fingerprint,
            }
        ):
            raise H1RecoveryHistoricalPESourceIntegrityError("anchor native binding differs")
        publication = cast(dict[str, object], binding["publication"])
        authenticated_publication = self._runtime._publication(decision)
        if publication != {
            "operation_kind": authenticated_publication.operation_kind,
            "operation_id": authenticated_publication.idempotency_key,
            "expected_head": authenticated_publication.expected_head,
            "request_fingerprint": authenticated_publication.request_fingerprint,
        }:
            raise H1RecoveryHistoricalPESourceIntegrityError("anchor publication binding differs")
        prepare = select_h1_v3_prepare_for_seal(
            self._runtime, selected_seal=selected_seal, historical=True
        ).prepare
        selected_prepare = cast(dict[str, object], binding["selected_prepare"])
        if selected_prepare["entry_id"] != prepare.decision_id or selected_prepare[
            "payload_digest"
        ] != _sha(prepare.decision_bytes):
            raise H1RecoveryHistoricalPESourceIntegrityError("anchor selected Prepare differs")
        self._validate_p(data, run, conversation, effects)
        self._validate_members(data, prepare, captured, conversation)
        worker = cast(dict[str, object], data["e_worker"])
        if (
            worker["run_head"] != captured.head
            or worker["worker_session_id"] != captured.worker_session
            or worker["worker_session_id"] != run.worker_session
            or worker["owner_id"] != "agent_loop"
            or worker["fence_kind"] != "NON_SCHEDULER"
        ):
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "anchor worker differs from captured preseal or selected completion Run"
            )
        # Deriving each identity here verifies its selected-decision domain before request assembly.
        h1_preseal_pe_anchor_projection_identity(anchor, selected_decision_id=decision_id, role="P")

    def _validate_p(
        self,
        data: dict[str, object],
        run: ExecutionRunRecord,
        conversation: _AuthenticatedConversationPolicyInputs,
        effects: _HistoricalEffectsSource,
    ) -> None:
        p = cast(dict[str, object], data["p"])
        try:
            scope_bytes = base64.b64decode(cast(str, p["scope_bytes_base64"]), validate=True)
            policy_bytes = base64.b64decode(cast(str, p["policy_bytes_base64"]), validate=True)
            scope_ref = ExactHead.model_validate(p["scope_ref"])
            policy_ref = ExactHead.model_validate(p["policy_ref"])
        except (TypeError, ValueError) as error:
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "anchor P scope or policy is invalid"
            ) from error
        selected_scope = effects.selected_scope
        scope = selected_scope.scope
        if (
            scope.canonical_bytes() != scope_bytes
            or selected_scope.current_result.scope_ref != scope_ref
            or conversation.scope_policy_ref != policy_ref
            or conversation.scope_policy_bytes != policy_bytes
            or scope.disclosure_policy.ref != policy_ref
            or scope.disclosure_policy.canonical_source_bytes != policy_bytes
            or scope.recipient != conversation.recipient
            or p["recipient"] != _anchor_recipient_mapping(conversation.recipient)
            or p["custody_entry_generation"] != conversation.custody_entry_generation
            or p["custody_entry_digest"] != conversation.custody_entry_digest
            or p["source_signature_digest"]
            != scope.selected_resource_observation_ref.signed_observation_fingerprint
            or scope.tenant_id != run.tenant
            or scope.principal_id != run.principal
            or scope.worker_session_id != run.worker_session
            or conversation.recipient != run.origin.recipient
        ):
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "anchor P scope/policy/recipient join differs"
            )

    def _validate_members(
        self,
        data: dict[str, object],
        prepare: H1SelectedPrepare,
        captured: ExecutionRunRecord,
        conversation: _AuthenticatedConversationPolicyInputs,
    ) -> None:
        members = cast(list[object], data["e_members"])
        attempt = captured.turns[0].attempts[0]
        manifest = attempt.manifest
        if len(members) != len(manifest.members):
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "anchor member vector differs from selected manifest"
            )
        endpoint_id = conversation.recipient.endpoint.identity
        for index, (raw_member, native) in enumerate(zip(members, manifest.members, strict=True)):
            row = cast(dict[str, object], raw_member)
            source_kind = {
                "workspace": "WORKSPACE",
                "context": "CONTEXT",
                "prompt": "PROMPT",
                "schema": "SCHEMA",
            }.get(native.surface)
            if source_kind is None:
                raise H1RecoveryHistoricalPESourceIntegrityError(
                    "selected manifest has an unknown member surface"
                )
            provenance = cast(dict[str, object], row["provenance"])
            disclosure = cast(dict[str, object], row["disclosure"])
            narrowing = cast(dict[str, object], row["narrowing"])
            if (
                row["member_index"] != index
                or row["member_digest"] != _sha(native.canonical_bytes())
                or provenance["original_head"] != native.provenance_head
                or provenance["source_kind"] != source_kind
                or disclosure["original_head"] != native.label_head
                or disclosure["original_label"] != native.label.model_dump(mode="json")
            ):
                raise H1RecoveryHistoricalPESourceIntegrityError("anchor member provenance differs")
            label = native.label
            narrowed = cast(dict[str, object], narrowing["label"])
            if (
                label.value == "DENY_ALL"
                or (
                    label.value == "ENDPOINT_RESTRICTED"
                    and endpoint_id not in label.allowed_endpoints
                )
                or narrowing["ordinal"] != 0
                or narrowed
                != {
                    "lattice_version": "chiplog.disclosure.v1",
                    "value": "ENDPOINT_RESTRICTED",
                    "allowed_endpoints": [endpoint_id],
                }
            ):
                raise H1RecoveryHistoricalPESourceIntegrityError("anchor member narrowing differs")
            locator = cast(dict[str, object], provenance["source_locator"])
            if source_kind == "WORKSPACE":
                expected = {
                    "kind": "WORKSPACE_ISSUANCE",
                    "entry_id": prepare.issuance_ref.entry_id,
                    "payload_digest": prepare.issuance_ref.payload_digest,
                }
            elif source_kind == "CONTEXT":
                expected = {
                    "kind": "STARTED_RUN",
                    "run_id": prepare.started_run.run_id,
                    "run_head": prepare.started_run.head,
                }
            else:
                expected = {
                    "kind": "PREPARE_ARTIFACT",
                    "prepare_entry_id": prepare.decision_id,
                    "prepare_payload_digest": _sha(prepare.decision_bytes),
                    "artifact_digest": manifest.artifact.digest(),
                }
            if locator != expected:
                raise H1RecoveryHistoricalPESourceIntegrityError(
                    "anchor member locator differs from selected history"
                )

    def _projection(
        self,
        anchor: H1PresealPEAnchorRecordV1,
        decision_id: str,
        *,
        role: Literal["P", "E_MEMBER", "E_WORKER"],
        member_index: int | None,
        facet: str,
        facts: object,
    ) -> ExactHead:
        identity = h1_preseal_pe_anchor_projection_identity(
            anchor,
            selected_decision_id=decision_id,
            role=role,
            member_index=member_index,
        )
        payload = {
            "schema_id": "chiplog.execution.h1-historical-pe-projection.v1",
            "anchor_digest": _sha(anchor.canonical_bytes()),
            "selected_decision_id": decision_id,
            "role": role,
            "member_index": member_index,
            "facet": facet,
            "facts": facts,
        }
        fingerprint = _sha(_canonical(payload))
        return ExactHead(
            identity=identity + ":" + facet, head="record:" + fingerprint, fingerprint=fingerprint
        )

    def _request(
        self,
        anchor: H1PresealPEAnchorRecordV1,
        decision_id: str,
        source: FirstPathCompletionCutV2,
        conversation: _AuthenticatedConversationPolicyInputs,
    ) -> PrepareExecutionCompletionFirstPathV2:
        data = anchor.as_dict()
        run = source.complete_ordered_run_lineage[-1]
        captured = source.complete_ordered_run_lineage[-2]
        attempt = run.turns[0].attempts[0]
        if attempt.response_base64 is None:
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "selected historical response is absent"
            )
        try:
            response = base64.b64decode(attempt.response_base64, validate=True)
        except (TypeError, ValueError) as error:
            raise H1RecoveryHistoricalPESourceIntegrityError(
                "selected historical response is invalid"
            ) from error
        history: list[HistoricalEnvelope] = []
        for index, (row, native) in enumerate(
            zip(cast(list[object], data["e_members"]), attempt.manifest.members, strict=True)
        ):
            member = cast(dict[str, object], row)
            history.append(
                HistoricalEnvelope(
                    content=ExactHead(
                        identity=native.record_id,
                        head=native.revision_head,
                        fingerprint=_sha(native.canonical_bytes()),
                    ),
                    visibility=ExactHead(
                        identity=attempt.attempt_id,
                        head=attempt.manifest.digest(),
                        fingerprint=attempt.manifest.digest(),
                    ),
                    provenance=self._projection(
                        anchor,
                        decision_id,
                        role="E_MEMBER",
                        member_index=index,
                        facet="provenance",
                        facts=member["provenance"],
                    ),
                    disclosure=self._projection(
                        anchor,
                        decision_id,
                        role="E_MEMBER",
                        member_index=index,
                        facet="disclosure",
                        facts=member["disclosure"],
                    ),
                    label=DisclosureLabel.model_validate_json(
                        _canonical(cast(dict[str, object], member["disclosure"])["original_label"])
                    ),
                    narrowing=(
                        self._projection(
                            anchor,
                            decision_id,
                            role="E_MEMBER",
                            member_index=index,
                            facet="narrowing",
                            facts=member["narrowing"],
                        ),
                    ),
                )
            )
        worker = cast(dict[str, object], data["e_worker"])
        fence = NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=cast(str, worker["worker_session_id"]),
            runtime_generation=cast(str, worker["owner_route_generation"]),
        )
        delivery = DeliveryObservation(
            tenant=run.tenant,
            run=ExactHead(identity=run.run_id, head=run.head, fingerprint=run.digest()),
            turn_id=captured.turns[0].turn_id,
            captured_response=response,
            source_frontier=source.tenant_commit_sequence,
            origin=run.origin,
            recipients=(conversation.recipient,),
            history=tuple(history),
            queries=(),
            policy=conversation.scope_policy_ref,
            worker_fence=self._projection(
                anchor,
                decision_id,
                role="E_WORKER",
                member_index=None,
                facet="worker",
                facts=worker,
            ),
        )
        return PrepareExecutionCompletionFirstPathV2(
            command_id="h1-first-path-completion:" + _sha(source.canonical_bytes()),
            run=run,
            selected_attempt=CallSubjectHead(
                subject_id=attempt.attempt_id,
                revision=Present(head=attempt.head, fingerprint=attempt.digest()),
            ),
            selector_generation=attempt.generation,
            visibility_manifest=CallSubjectHead(
                subject_id="visibility",
                revision=Present(
                    head="record:" + attempt.manifest.digest(),
                    fingerprint=attempt.manifest.digest(),
                ),
            ),
            exact_captured_response=response,
            source=source,
            delivery=delivery,
            fence=fence,
        )


__all__ = [
    "H1RecoveryHistoricalPESource",
    "H1RecoveryHistoricalPESourceError",
    "H1RecoveryHistoricalPESourceIntegrityError",
    "H1RecoveryHistoricalPESourceUnavailable",
]
