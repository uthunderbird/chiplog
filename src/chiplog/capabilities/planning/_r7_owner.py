"""Planning-owner-only policy for producing an inert R7 commit proposal."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict

from chiplog.domain_primitives import PermissionScope, PrincipalId, RecordId, TenantId

from ._planning import (
    CREATE_INTENTION_LINE,
    CREATE_SCOPE,
    CreateIntentionLine,
    InvocationContext,
    PlanningCommittedResult,
    PlanningTrustReference,
    _PlanningOwnerFactory,
    _request_fingerprint,
    _structural_error,
)
from .r7_boundary import R7PlanningCreateDTO, R7PlanningResultDTO


class R7PlanningOwnerViolation(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _record_id(value: object, tenant: TenantId) -> RecordId:
    if not isinstance(value, dict) or set(value) != {"tenant_id", "value"}:
        raise R7PlanningOwnerViolation("planning snapshot contains malformed record identity")
    encoded_tenant = value["tenant_id"]
    if encoded_tenant not in (tenant.value, {"value": tenant.value}) or not isinstance(
        value["value"], str
    ):
        raise R7PlanningOwnerViolation("planning snapshot contains foreign record identity")
    return RecordId(tenant, value["value"])


def _result(value: object, tenant: TenantId) -> PlanningCommittedResult:
    if not isinstance(value, dict):
        raise R7PlanningOwnerViolation("planning snapshot result is malformed")
    try:
        return PlanningCommittedResult(
            _record_id(value["command_id"], tenant),
            _record_id(value["result_id"], tenant),
            _record_id(value["intention_line_id"], tenant),
            _record_id(value["revision_id"], tenant),
            _record_id(value["authorization_evidence_id"], tenant),
            int(value["commit_sequence"]),
            tuple(
                (int(item[0]), str(item[1]), _record_id(item[2], tenant))
                for item in value["allocation_manifest"]
            ),
            tuple(
                (_record_id(item[0], tenant), str(item[1]), str(item[2]))
                for item in value["record_manifest"]
            ),
            str(value["batch_fingerprint"]),
            str(value["result_fingerprint"]),
            str(value["publication_fingerprint"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise R7PlanningOwnerViolation("planning snapshot result is malformed") from error


def _result_value(result: PlanningCommittedResult) -> dict[str, object]:
    return asdict(result)


class R7PlanningOwner:
    def __init__(self) -> None:
        self._factory = _PlanningOwnerFactory()

    def execute(self, request: R7PlanningCreateDTO) -> R7PlanningResultDTO:
        tenant = TenantId(request.tenant_id)
        principal = PrincipalId(request.principal_id)
        try:
            trust = json.loads(request.trust_reference_bytes)
            snapshot = json.loads(request.planning_snapshot_bytes)
        except json.JSONDecodeError as error:
            raise R7PlanningOwnerViolation("planning input bytes are not canonical JSON") from error
        if not isinstance(trust, dict) or not isinstance(snapshot, dict):
            raise R7PlanningOwnerViolation("planning trust or snapshot is not an object")
        expected_snapshot_keys = {"commands", "head", "record_ids"}
        if set(snapshot) != expected_snapshot_keys:
            raise R7PlanningOwnerViolation("planning snapshot exact-set mismatch")
        reference = PlanningTrustReference(
            tenant,
            principal,
            str(trust["contour"]),
            str(trust["credential_head"]),
            str(trust["session_head"]),
            str(trust["source_head"]),
            str(trust["trust_head"]),
            str(trust["materialization_head"]),
            int(trust["freshness_sequence"]),
            str(trust["peer_credential"]),
        )
        context = InvocationContext(tenant, principal, PermissionScope(CREATE_SCOPE), reference)
        command = CreateIntentionLine(
            RecordId(tenant, request.command_id),
            RecordId(tenant, request.intention_line_id),
            RecordId(tenant, request.revision_id),
            request.purpose,
            request.authority_act_id,
        )
        structural_error = _structural_error(context, command, None)
        if structural_error is not None:
            return R7PlanningResultDTO(
                disposition="DENIED", canonical_result_bytes=None, reason=structural_error
            )
        request_fingerprint = _request_fingerprint(context, command, None)
        commands = snapshot["commands"]
        record_ids = snapshot["record_ids"]
        head = snapshot["head"]
        if (
            not isinstance(commands, list)
            or not isinstance(record_ids, list)
            or not all(isinstance(item, str) for item in record_ids)
            or not isinstance(head, int)
            or head < 0
        ):
            raise R7PlanningOwnerViolation("planning snapshot shape is invalid")
        prior = next(
            (
                item
                for item in commands
                if isinstance(item, dict) and item.get("command_id") == request.command_id
            ),
            None,
        )
        if prior is not None:
            if prior.get("request_fingerprint") != request_fingerprint:
                return R7PlanningResultDTO(
                    disposition="CONFLICT",
                    canonical_result_bytes=None,
                    reason="command identity reused with changed bytes",
                )
            result = _result(prior.get("result"), tenant)
            return R7PlanningResultDTO(
                disposition="REPLAY",
                canonical_result_bytes=_canonical({"result": _result_value(result)}),
                reason=None,
            )
        records, result = self._factory.construct(context, command, head + 1, request_fingerprint)
        proposed_ids = {item.record_id.value for item in records}
        if len(proposed_ids) != len(records) or proposed_ids & set(record_ids):
            return R7PlanningResultDTO(
                disposition="CONFLICT",
                canonical_result_bytes=None,
                reason="record identity collision",
            )
        proposal = {
            "commit_sequence": head + 1,
            "operation_kind": CREATE_INTENTION_LINE,
            "records": [
                {
                    "canonical_bytes": base64.b64encode(item.canonical_bytes).decode("ascii"),
                    "fingerprint": item.fingerprint,
                    "record_id": item.record_id.value,
                    "record_type_id": item.record_type_id,
                }
                for item in records
            ],
            "request_fingerprint": request_fingerprint,
            "result": _result_value(result),
        }
        return R7PlanningResultDTO(
            disposition="COMMITTED",
            canonical_result_bytes=_canonical(proposal),
            reason=None,
        )


__all__ = ["R7PlanningOwner", "R7PlanningOwnerViolation"]
