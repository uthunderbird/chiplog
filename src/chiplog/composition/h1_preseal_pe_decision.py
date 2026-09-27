"""Private, one-use P/E anchor issuer for the H1 V2 DECIDED seam.

The coordinator deliberately owns no authority of its own.  It keeps the live
P/E capabilities which were read before a seal and makes them usable exactly
once, while the installed runtime's :class:`AuthorityGate` is held by the
DECIDED writer.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Never, cast

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_preseal_contracts import H1OwnerAsOfV1, H1V2SealPreflight
from chiplog.composition.h1_preseal_native_source import (
    H1PresealNativeSource,
    H1PresealNativeSourceCut,
)
from chiplog.composition.h1_preseal_p_scope_wires import H1PresealPScopeWiresV1
from chiplog.composition.h1_preseal_pe_anchor_records import H1PresealPEAnchorRecordV1
from chiplog.composition.r14_execution_complete_seal_records import (
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
    complete_seal_physical_command,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand


class H1PresealPEDecisionError(ValueError):
    """A preseal P/E capture is absent, stale, or belongs to another owner."""


class H1PresealPEDecisionCapture:
    """Opaque process-local receipt; it cannot be copied or deserialized."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 preseal P/E captures are issued only by their installed owner")

    def __copy__(self) -> Never:
        raise TypeError("H1 preseal P/E captures cannot be copied")

    def __deepcopy__(self, memo: object) -> Never:
        del memo
        raise TypeError("H1 preseal P/E captures cannot be copied")

    def __reduce__(self) -> Never:
        raise TypeError("H1 preseal P/E captures cannot be serialized")


@dataclass(frozen=True, slots=True)
class H1PresealPEBoundDecision:
    """The two authenticated DECIDED siblings minted by one replay."""

    anchor: H1PresealPEAnchorRecordV1
    scope_wires: H1PresealPScopeWiresV1


@dataclass(frozen=True, slots=True)
class _CaptureState:
    receipt: H1PresealPEDecisionCapture
    preflight: H1V2SealPreflight
    native_cut: H1PresealNativeSourceCut
    scope_cap: object
    baseline: tuple[object, object, object, object, object]


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _head(value: ExactHead) -> dict[str, object]:
    return value.model_dump(mode="json")


def _wire_digest(wire: object) -> str:
    """Commit both authenticated directions of one accepted P owner exchange."""
    sent = getattr(wire, "sent", None)
    returned = getattr(wire, "returned", None)
    sent_payload = getattr(sent, "canonical_payload", None)
    returned_payload = getattr(returned, "canonical_payload", None)
    if not isinstance(sent_payload, bytes) or not isinstance(returned_payload, bytes):
        raise H1PresealPEDecisionError("H1 preseal P owner wire is unavailable")
    values = [
        base64.b64encode(sent_payload).decode(),
        base64.b64encode(returned_payload).decode(),
    ]
    return _digest(_canonical(values))


class H1PresealPEDecisionOwner:
    """Bind live P/E residuals to one selected H1 V2 physical seal."""

    def __init__(
        self,
        runtime: CommonCliExecutionRuntime,
        native_source: H1PresealNativeSource,
        p_owner: object,
        member_owner: object,
        worker_owner: object,
    ) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 preseal P/E owner requires the canonical common CLI runtime")
        if (
            type(native_source) is not H1PresealNativeSource
            or native_source._runtime is not runtime
        ):
            raise TypeError("H1 preseal P/E owner requires the mounted native source")
        self._runtime = runtime
        self._native_source = native_source
        self._p_owner = p_owner
        self._member_owner = member_owner
        self._worker_owner = worker_owner
        self._issued: dict[int, _CaptureState] = {}
        self._closed = False

    async def capture(self, preflight: H1V2SealPreflight) -> H1PresealPEDecisionCapture:
        """Read P over its owner route and retain an immutable pre-seal baseline."""
        self._require_installed()
        if type(preflight) is not H1V2SealPreflight:
            raise H1PresealPEDecisionError("H1 preseal P/E capture requires a runtime preflight")
        native_cut = self._native_source.capture(preflight)
        capture_p = getattr(self._p_owner, "_capture_preseal_p_residual", None)
        if not callable(capture_p):
            raise H1PresealPEDecisionError("H1 preseal P owner capture is unavailable")
        scope_cap = await capture_p(native_cut)
        with self._runtime._authority_gate().hold():
            self._require_installed()
            baseline = self._replay(native_cut, scope_cap)
            receipt = object.__new__(H1PresealPEDecisionCapture)
            self._issued[id(receipt)] = _CaptureState(
                receipt, preflight, native_cut, scope_cap, baseline
            )
            return receipt

    def recheck_and_bind(
        self,
        capture: H1PresealPEDecisionCapture,
        preflight: H1V2SealPreflight,
        command: PhysicalPublicationCommand,
        retained_v2: RetainedExecutionCompleteSealV2,
        owner_asof: H1OwnerAsOfV1,
    ) -> H1PresealPEBoundDecision:
        """Consume ``capture`` and mint its closed RECORD under the writer's gate.

        This method intentionally contains no await and invokes no public-port
        operation.  It is called directly before the DECIDED journal append.
        """
        gate = self._runtime._authority_gate()
        gate.require_held()
        self._require_installed()
        state = self._issued.pop(id(capture), None)  # burn on every replay outcome
        if (
            type(capture) is not H1PresealPEDecisionCapture
            or state is None
            or state.receipt is not capture
            or state.preflight is not preflight
        ):
            raise H1PresealPEDecisionError("H1 preseal P/E capture is absent or belongs elsewhere")
        if type(command) is not PhysicalPublicationCommand:
            raise H1PresealPEDecisionError("H1 preseal P/E binding requires the physical command")
        if type(retained_v2) is not RetainedExecutionCompleteSealV2:
            raise H1PresealPEDecisionError("H1 preseal P/E binding requires the selected V2 seal")
        if type(owner_asof) is not H1OwnerAsOfV1:
            raise H1PresealPEDecisionError("H1 preseal P/E binding requires the selected owner cut")
        native, scope, wires, members, worker = self._replay(state.native_cut, state.scope_cap)
        if (native, scope, wires, members, worker) != state.baseline:
            raise H1PresealPEDecisionError("H1 preseal P/E source changed after capture")
        anchor = self._record(
            preflight, command, retained_v2, owner_asof, native, scope, wires, members, worker
        )
        data = anchor.as_dict()
        p = cast(dict[str, object], data["p"])
        if not isinstance(wires, tuple) or len(wires) != 2:
            raise H1PresealPEDecisionError("H1 preseal P owner wire pair differs")
        candidate = H1PresealPScopeWiresV1.from_wires(
            binding=anchor.binding, issue_wire=wires[0], current_wire=wires[1]
        )
        scope_wires = H1PresealPScopeWiresV1.from_mapping(
            candidate.as_dict(),
            anchor_binding=data["binding"],
            issue_digest=cast(str, p["accepted_issue_wire_digest"]),
            current_digest=cast(str, p["accepted_current_wire_digest"]),
        )
        return H1PresealPEBoundDecision(anchor=anchor, scope_wires=scope_wires)

    def revoke(self) -> None:
        """Permanently invalidate outstanding private P/E capture receipts."""
        gate = self._runtime._authority_gate()
        with gate.hold():
            self._issued.clear()
            self._closed = True

    def close(self) -> None:
        self.revoke()

    def _require_installed(self) -> None:
        if self._closed:
            raise H1PresealPEDecisionError("H1 preseal P/E owner is closed")
        runtime = self._runtime
        if (
            getattr(runtime, "_h1_preseal_native_source", None) is not self._native_source
            or getattr(runtime, "_h1_preissuance_registration_source_port", None)
            is not self._p_owner
            or getattr(runtime, "_h1_pre_request_member_evidence", None) is not self._member_owner
            or getattr(runtime, "_h1_installed_worker_evidence_owner", None)
            is not self._worker_owner
            or getattr(runtime, "_h1_preseal_pe_decision_owner", None) is not self
        ):
            raise H1PresealPEDecisionError("H1 preseal P/E owner is not installed on this runtime")

    def _replay(
        self, native_cut: H1PresealNativeSourceCut, scope_cap: object
    ) -> tuple[object, object, object, object, object]:
        self._runtime._authority_gate().require_held()
        native = self._native_source.replay(native_cut)
        replay_scope = getattr(self._p_owner, "_replay_preseal_scope", None)
        replay_wires = getattr(self._p_owner, "_replay_preseal_scope_wires", None)
        members = getattr(self._member_owner, "_preseal_members", None)
        worker = getattr(self._worker_owner, "_preseal_worker", None)
        if not all(callable(value) for value in (replay_scope, replay_wires, members, worker)):
            raise H1PresealPEDecisionError("H1 preseal P/E replay owners are unavailable")
        p = cast(Any, replay_scope)(scope_cap, native_cut)
        p_wires = cast(Any, replay_wires)(scope_cap, native_cut)
        e_members = cast(Any, members)(self._native_source, native_cut, scope_cap)
        e_worker = cast(Any, worker)(self._native_source, native_cut)
        return native, p, p_wires, e_members, e_worker

    def _record(
        self,
        preflight: H1V2SealPreflight,
        command: PhysicalPublicationCommand,
        retained: RetainedExecutionCompleteSealV2,
        owner_asof: H1OwnerAsOfV1,
        native: object,
        scope: object,
        wires: object,
        members: object,
        worker: object,
    ) -> H1PresealPEAnchorRecordV1:
        if not isinstance(native, tuple) or not native:
            raise H1PresealPEDecisionError("H1 preseal native occurrence vector is empty")
        if not isinstance(wires, tuple) or len(wires) != 2:
            raise H1PresealPEDecisionError("H1 preseal P owner wire pair differs")
        if not isinstance(members, tuple) or not all(isinstance(row, dict) for row in members):
            raise H1PresealPEDecisionError("H1 preseal E member projection differs")
        if not isinstance(worker, dict):
            raise H1PresealPEDecisionError("H1 preseal E worker projection differs")
        expected_command = complete_seal_physical_command(build_complete_seal_envelope(retained))
        if command != expected_command:
            raise H1PresealPEDecisionError(
                "H1 preseal physical command differs from selected V2 seal"
            )
        sealed = retained.exchange.proposal.sealed_run
        captured = preflight.captured_run
        if (
            retained.exchange.request.captured_run != captured
            or sealed.tenant != captured.tenant
            or sealed.run_id != captured.run_id
            or sealed.turns[-1].turn_id != captured.turns[-1].turn_id
            or sealed.predecessor != captured.head
            or preflight.worker_session != captured.worker_session
        ):
            raise H1PresealPEDecisionError("H1 preseal selected Prepare or Run differs")
        slot = getattr(getattr(self._p_owner, "_launch", None), "_slot", None)
        registry = getattr(getattr(self._p_owner, "_custody", None), "_registry", None)
        if slot is None or registry is None:
            raise H1PresealPEDecisionError("H1 preseal installed genesis is unavailable")
        response_seal = retained.exchange.proposal.fan_out.response_seal
        if (
            response_seal.tenant_id != captured.tenant
            or response_seal.original_run_id != captured.run_id
        ):
            raise H1PresealPEDecisionError("H1 preseal response seal differs from captured Run")
        try:
            scope_data = cast(Any, scope)
            recipient = scope_data.recipient
            p_recipient = {
                "provider_id": recipient.provider_id,
                "account_id": recipient.account_id,
                "recipient_id": recipient.recipient_id,
                "endpoint": _head(recipient.endpoint),
                "canonical_address_base64": base64.b64encode(recipient.canonical_address).decode(),
                "credential_binding": _head(recipient.credential_binding),
            }
            p_scope_ref = _head(scope_data.scope_ref)
            p_policy_ref = _head(scope_data.policy_ref)
        except AttributeError as error:
            raise H1PresealPEDecisionError("H1 preseal P residual projection differs") from error
        binding = {
            "deployment_id": slot.deployment_id,
            "database_id": slot.database_id,
            "database_genesis_digest": registry.database_genesis_digest,
            "tenant_id": captured.tenant,
            "principal_id": captured.principal,
            "run_id": captured.run_id,
            "turn_id": captured.turns[-1].turn_id,
            "attempt_id": captured.turns[-1].attempts[-1].attempt_id,
            "prepared_run_head": captured.head,
            "manifest_digest": native[0].manifest_digest,
            "selected_prepare": {
                "entry_id": preflight.prepare.decision_id,
                "payload_digest": _digest(preflight.prepare.decision_bytes),
            },
            "publication": {
                "operation_kind": command.operation_kind,
                "operation_id": command.idempotency_key,
                "expected_head": command.expected_head,
                "request_fingerprint": command.request_fingerprint,
            },
            "selected_response_seal": {
                "identity": response_seal.response_seal_id,
                "head": "record:" + response_seal.digest(),
                "fingerprint": response_seal.digest(),
            },
            "owner_asof": {
                "tenant_id": owner_asof.tenant_id,
                "owner_head": owner_asof.owner_head,
            },
            "member_count": len(members),
            "member_vector_digest": _digest(_canonical(list(members))),
        }
        record = {
            "schema_id": "chiplog.execution.h1-preseal-pe-anchor.v1",
            "version": 1,
            "binding": binding,
            "p": {
                "scope_ref": p_scope_ref,
                "scope_bytes_base64": base64.b64encode(scope_data.scope_bytes).decode(),
                "policy_ref": p_policy_ref,
                "policy_bytes_base64": base64.b64encode(scope_data.policy_bytes).decode(),
                "recipient": p_recipient,
                "custody_entry_generation": scope_data.custody_entry_generation,
                "custody_entry_digest": scope_data.custody_entry_digest,
                "source_signature_digest": scope_data.source_signature_digest,
                "accepted_issue_wire_digest": _wire_digest(wires[0]),
                "accepted_current_wire_digest": _wire_digest(wires[1]),
            },
            "e_members": list(members),
            "e_worker": worker,
        }
        try:
            return H1PresealPEAnchorRecordV1.from_mapping(record)
        except ValueError as error:
            raise H1PresealPEDecisionError("H1 preseal P/E anchor mapping is invalid") from error


__all__ = [
    "H1PresealPEBoundDecision",
    "H1PresealPEDecisionCapture",
    "H1PresealPEDecisionError",
    "H1PresealPEDecisionOwner",
]
