"""Deployment-trust-owner handler; imported only by the trust worker."""

from __future__ import annotations

import hashlib
import json
from base64 import b64decode, b64encode
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

_H1_ROUTES = (
    (
        "deployment_trust.issue_hermetic_output_scope",
        "broker",
        "deployment_trust",
        "chiplog.deployment-trust.owner-call.v1",
        "chiplog.deployment-trust.issue-hermetic-output-scope-result.v1",
    ),
    (
        "deployment_trust.read_current_hermetic_output_scope",
        "broker",
        "deployment_trust",
        "chiplog.deployment-trust.owner-call.v1",
        "chiplog.deployment-trust.current-hermetic-output-scope-result.v1",
    ),
)

ROUTES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "deployment_trust.authenticate",
        "broker",
        "deployment_trust",
        "chiplog.deployment-trust.owner-call.v1",
        "chiplog.deployment-trust.owner-result.v1",
    ),
    (
        "deployment_trust.bootstrap",
        "broker",
        "deployment_trust",
        "chiplog.deployment-trust.owner-call.v1",
        "chiplog.deployment-trust.owner-result.v1",
    ),
    (
        "deployment_trust.revalidate",
        "broker",
        "deployment_trust",
        "chiplog.deployment-trust.owner-call.v1",
        "chiplog.deployment-trust.owner-result.v1",
    ),
    (
        "deployment_trust.runtime_admission",
        "broker",
        "deployment_trust",
        "chiplog.deployment-trust.owner-call.v1",
        "chiplog.deployment-trust.owner-result.v1",
    ),
)

ROUTES = tuple(sorted(ROUTES))


class _TrustOwnerCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: Literal[
        "AUTHENTICATE",
        "BOOTSTRAP",
        "REVALIDATE",
        "RUNTIME_ADMISSION",
        "ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
        "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1",
    ]
    snapshot_bytes: bytes
    request_bytes: bytes

    def canonical_bytes(self) -> bytes:
        return _canonical(
            {
                "mode": self.mode,
                "request_bytes": b64encode(self.request_bytes).decode("ascii"),
                "snapshot_bytes": b64encode(self.snapshot_bytes).decode("ascii"),
            }
        )


class _TrustOwnerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    disposition: Literal["VALID", "DENIED", "STALE", "INDETERMINATE"]
    reference_bytes: bytes | None
    reason: str | None

    def canonical_bytes(self) -> bytes:
        return _canonical(
            {
                "disposition": self.disposition,
                "reason": self.reason,
                "reference_bytes": None
                if self.reference_bytes is None
                else b64encode(self.reference_bytes).decode("ascii"),
            }
        )


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _snapshot(raw_entries: bytes) -> dict[str, object]:
    state: dict[str, object] = {
        "credential": None,
        "freshness": 0,
        "materialization_head": "",
        "phase": "UNAVAILABLE",
        "principal_id": None,
        "tenant_id": None,
        "trust_head": "",
    }
    for decision_id, _, encoded in json.loads(raw_entries):
        envelope = json.loads(b64decode(encoded))
        kind, payload = envelope["kind"], envelope["payload"]
        state["materialization_head"] = decision_id
        if kind == "INITIALIZE":
            binding = payload["binding"]
            state.update(
                tenant_id=binding["tenant_id"],
                trust_head=hashlib.sha256(_canonical(binding)).hexdigest(),
                phase="BOOTSTRAP_REQUIRED",
            )
        elif kind == "BOOTSTRAP":
            state.update(
                principal_id=payload["principal_id"],
                credential=payload["credential"],
                freshness=1,
                phase="ACTIVE",
            )
        elif kind in {"ROTATE_CREDENTIAL", "EMERGENCY_RECOVERY"}:
            state["credential"] = payload.get("credential", payload)
            state["freshness"] = int(cast(int, state["freshness"])) + 1
            state["phase"] = "ACTIVE"
        elif kind == "REVOKE" and isinstance(state["credential"], dict):
            state["credential"] = {**state["credential"], "revoked": True}
            state["freshness"] = int(cast(int, state["freshness"])) + 1
        elif kind == "PRINCIPAL_CONTOUR_PREREQUISITE":
            state["phase"] = "HOLD"
        elif kind == "TRUST_TRANSITION_PREPARED":
            state["phase"] = "PREPARED"
        elif kind == "TRUST_TRANSITION_READY":
            state["phase"] = "READY"
        elif kind == "TRUST_TRANSITION_ABORTED":
            state["phase"] = "ACTIVE"
        elif kind == "TRUST_TRANSITION_ACCEPTED":
            state["trust_head"] = hashlib.sha256(_canonical(payload["binding"])).hexdigest()
            state["phase"] = "ACTIVE"
        elif kind in {"OPERATOR_BINDING_KEY_ROTATION", "JOURNAL_ROOT_ROTATION"}:
            state["trust_head"] = hashlib.sha256(_canonical(payload)).hexdigest()
    return state


def _evaluate(call: _TrustOwnerCall) -> _TrustOwnerResult:
    snapshot = _snapshot(call.snapshot_bytes)
    request = json.loads(call.request_bytes)
    if call.mode == "BOOTSTRAP":
        valid = snapshot["phase"] in {"UNAVAILABLE", "BOOTSTRAP_REQUIRED"}
        if not valid or request["peer"] != request["expected_peer"]:
            return _TrustOwnerResult(
                disposition="DENIED", reference_bytes=None, reason="bootstrap denied"
            )
        decisions: list[dict[str, object]] = []
        if snapshot["phase"] == "UNAVAILABLE":
            genesis = {
                "database_instance_id": request["database_instance_id"],
                "genesis_version": 1,
                "tenant_id": request["tenant_id"],
            }
            unsigned = {
                "database_instance_id": request["database_instance_id"],
                "genesis_digest": hashlib.sha256(_canonical(genesis)).hexdigest(),
                "journal_epoch": 1,
                "journal_identity": "tenant-journal-v1",
                "operator_key_id": "r6-local-operator",
                "predecessor": None,
                "tenant_id": request["tenant_id"],
            }
            decisions.append(
                {
                    "kind": "INITIALIZE",
                    "payload": {
                        "binding": {**unsigned, "signature": "__BROKER_HMAC_SHA256__"},
                        "genesis": genesis,
                    },
                }
            )
        bootstrap_credential = {
            "credential_id": request["credential_id"],
            "head": "credential:1",
            "peer_credential": request["peer"],
            "revoked": False,
            "session_head": "session:1",
            "session_id": request["session_id"],
        }
        decisions.append(
            {
                "kind": "BOOTSTRAP",
                "payload": {
                    "credential": bootstrap_credential,
                    "principal_id": request["principal_id"],
                    "recovery_verifier": hashlib.sha256(
                        b"r6-recovery-not-a-runtime-credential"
                    ).hexdigest(),
                    "token_fingerprint": request["token_fingerprint"],
                },
            }
        )
        return _TrustOwnerResult(
            disposition="VALID",
            reference_bytes=_canonical({"decisions": decisions}),
            reason=None,
        )
    if call.mode == "RUNTIME_ADMISSION":
        valid = snapshot["phase"] == "ACTIVE" and snapshot["tenant_id"] == request["tenant_id"]
        return _TrustOwnerResult(
            disposition="VALID" if valid else "DENIED",
            reference_bytes=None,
            reason=None if valid else "runtime trust is not active",
        )
    if snapshot["phase"] == "HOLD":
        return _TrustOwnerResult(
            disposition="INDETERMINATE",
            reference_bytes=None,
            reason="future contour prerequisite hold",
        )
    credential = snapshot["credential"]
    if (
        snapshot["phase"] != "ACTIVE"
        or not snapshot["principal_id"]
        or not isinstance(credential, dict)
    ):
        return _TrustOwnerResult(
            disposition="DENIED", reference_bytes=None, reason="single-principal contour inactive"
        )
    authentication = request
    if call.mode == "REVALIDATE":
        if request["operation"] != "CREATE_INTENTION_LINE":
            return _TrustOwnerResult(
                disposition="DENIED", reference_bytes=None, reason="operation is not admitted"
            )
        reference = request["reference"]
        authentication = {
            "contour": reference["contour"],
            "credential_id": credential["credential_id"],
            "peer_credential": credential["peer_credential"],
            "session_id": credential["session_id"],
        }
    for field in ("credential_id", "session_id", "peer_credential"):
        if credential.get("revoked") or authentication[field] != credential[field]:
            return _TrustOwnerResult(
                disposition="STALE", reference_bytes=None, reason=f"{field} is not current"
            )
    if authentication["contour"] != "CLI":
        return _TrustOwnerResult(
            disposition="DENIED", reference_bytes=None, reason="unsupported contour"
        )
    reference_value = {
        "contour": "CLI",
        "credential_head": credential["head"],
        "freshness_sequence": snapshot["freshness"],
        "materialization_head": snapshot["materialization_head"],
        "peer_credential": credential["peer_credential"],
        "principal_id": snapshot["principal_id"],
        "session_head": credential["session_head"],
        "source_head": "local",
        "tenant_id": snapshot["tenant_id"],
        "trust_head": snapshot["trust_head"],
    }
    if call.mode == "REVALIDATE" and request["reference"] != reference_value:
        return _TrustOwnerResult(
            disposition="STALE", reference_bytes=None, reason="trust reference no longer current"
        )
    return _TrustOwnerResult(
        disposition="VALID", reference_bytes=_canonical(reference_value), reason=None
    )


_OPERATION_MODES = {
    "deployment_trust.issue_hermetic_output_scope": "ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
    "deployment_trust.read_current_hermetic_output_scope": "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1",
    "deployment_trust.authenticate": "AUTHENTICATE",
    "deployment_trust.bootstrap": "BOOTSTRAP",
    "deployment_trust.revalidate": "REVALIDATE",
    "deployment_trust.runtime_admission": "RUNTIME_ADMISSION",
}


def _dispatch(operation: str, payload: bytes) -> dict[str, object]:
    expected_mode = _OPERATION_MODES.get(operation)
    if expected_mode is None:
        return {"failure": "UNAVAILABLE", "reason": "trust operation has no handler"}
    values = json.loads(payload)
    values["request_bytes"] = b64decode(values["request_bytes"])
    values["snapshot_bytes"] = b64decode(values["snapshot_bytes"])
    call = _TrustOwnerCall.model_validate(values)
    if call.canonical_bytes() != payload:
        return {"failure": "PROTOCOL_REJECTED", "reason": "payload is not canonical"}
    if call.mode != expected_mode:
        return {"failure": "PROTOCOL_REJECTED", "reason": "payload mode differs from trust route"}
    if operation in {route[0] for route in _H1_ROUTES}:
        from .h1_broker_evidence_contracts import decode_h1_candidate_call
        from .hermetic_output_scope_contracts import (
            ReadCurrentHermeticExecutionScopeV1,
        )

        try:
            if call.mode == "ISSUE_HERMETIC_OUTPUT_SCOPE_V1":
                candidate = decode_h1_candidate_call(call.request_bytes)
                evidence = candidate.evidence
                if (
                    hashlib.sha256(call.snapshot_bytes).hexdigest()
                    != evidence.trust_snapshot_digest
                ):
                    raise ValueError("H1 outer snapshot digest differs")
                entries = json.loads(call.snapshot_bytes)
                if not isinstance(entries, list) or not entries:
                    raise ValueError("H1 snapshot must contain logical journal entries")
                predecessor = None
                for entry in entries:
                    if (
                        not isinstance(entry, list)
                        or len(entry) != 3
                        or not isinstance(entry[0], str)
                        or not entry[0]
                        or entry[1] != predecessor
                        or not isinstance(entry[2], str)
                    ):
                        raise ValueError("H1 logical journal entry is malformed")
                    raw = b64decode(entry[2], validate=True)
                    if not raw or b64encode(raw).decode("ascii") != entry[2]:
                        raise ValueError("H1 logical journal bytes are malformed")
                    predecessor = entry[0]
                if _canonical(entries) != call.snapshot_bytes:
                    raise ValueError("H1 snapshot is not canonical")
                if predecessor != evidence.trust_observation.logical_snapshot_head:
                    raise ValueError("H1 outer logical snapshot head differs")
            else:
                request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
                    call.request_bytes
                )
                if request.canonical_bytes() != call.request_bytes:
                    raise ValueError("H1 request is not canonical")
        except (ValueError, TypeError) as error:
            return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
        # Contract-only: no snapshot reduction, issuer, journal write or current permit.
        route = next(route for route in _H1_ROUTES if route[0] == operation)
        return {
            "payload": b64encode(_canonical({"disposition": "UNSUPPORTED"})).decode("ascii"),
            "schema_id": route[4],
        }
    result = _evaluate(call)
    return {
        "payload": b64encode(result.canonical_bytes()).decode("ascii"),
        "schema_id": "chiplog.deployment-trust.owner-result.v1",
    }


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation not in {route[0] for route in _H1_ROUTES}:
        return _dispatch(operation, payload)
    try:
        return _dispatch(operation, payload)
    except (ValueError, TypeError, KeyError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
