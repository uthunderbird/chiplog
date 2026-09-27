"""Pure, non-authorizing shapes for H1 delivery-evidence decoder tests."""

import base64
import hashlib
import json

from chiplog.composition.h1_delivery_evidence_contracts import MEMBER_SCHEMA, WORKER_SCHEMA


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def member(
    source_kind: str = "PROMPT",
    *,
    original_label_value: str = "UNRESTRICTED",
    original_allowed_endpoints: list[str] | None = None,
) -> dict[str, object]:
    key = [
        "deployment",
        "database",
        "a" * 64,
        "tenant",
        "principal",
        "run",
        "turn",
        "attempt",
        "b" * 64,
        0,
        "c" * 64,
    ]
    workspace = {
        "schema_id": "chiplog.execution.h1-workspace-issuance-ref.v1",
        "tenant": "tenant",
        "batch_id": "batch",
        "entry_id": "e" * 64,
        "payload_digest": "f" * 64,
    }
    prepare = {
        "journal_role": "loop-decisions",
        "entry_id": "7" * 64,
        "payload_digest": "8" * 64,
    }
    started_run = {"identity": "run", "head": "started", "fingerprint": "1" * 64}
    source_locator: dict[str, object]
    rule: str
    if source_kind in {"PROMPT", "SCHEMA"}:
        source_locator = {
            "kind": "PREPARE_ARTIFACT",
            "selected_prepare": prepare,
            "artifact_digest": "d" * 64,
        }
        rule = "PROMPT_JOIN" if source_kind == "PROMPT" else "SCHEMA_PUBLIC"
    elif source_kind == "WORKSPACE":
        source_locator = {"kind": "WORKSPACE_ISSUANCE", "workspace_issuance": workspace}
        rule = "WORKSPACE_ORIGINAL"
    elif source_kind == "CONTEXT":
        source_locator = {
            "kind": "STARTED_RUN",
            "selected_prepare": prepare,
            "started_run": started_run,
        }
        rule = "CONTEXT_JOIN"
    else:
        raise ValueError("unsupported fixture source kind")
    allowed_endpoints = [] if original_allowed_endpoints is None else original_allowed_endpoints
    provenance = {
        "schema_id": "chiplog.execution.h1-member-provenance.v1",
        "member_key": key,
        "original_head": "provenance",
        "source_kind": source_kind,
        "source_bytes_base64": base64.b64encode(b"prompt").decode(),
        "source_locator": source_locator,
    }
    disclosure = {
        "schema_id": "chiplog.execution.h1-member-disclosure.v1",
        "member_key": key,
        "original_head": "label",
        "original_label": {
            "lattice_version": "chiplog.disclosure.v1",
            "value": original_label_value,
            "allowed_endpoints": allowed_endpoints,
        },
        "provenance_fingerprint": hashlib.sha256(canonical(provenance)).hexdigest(),
        "rule": rule,
        "workspace_issuance": workspace,
        "started_run": started_run,
    }
    narrowing = {
        "schema_id": "chiplog.execution.h1-member-narrowing.v1",
        "member_key": key,
        "ordinal": 0,
        "source_ref": {"identity": "endpoint", "head": "policy", "fingerprint": "2" * 64},
        "source_bytes_base64": base64.b64encode(b"policy").decode(),
        "label": {"value": "ENDPOINT_RESTRICTED", "allowed_endpoints": ["endpoint"]},
    }
    cut = {
        "schema_id": "chiplog.execution.h1-member-narrowing-cut.v1",
        "member_key": key,
        "scope_ref": {"identity": "scope", "head": "scope-head", "fingerprint": "3" * 64},
        "scope_bytes_base64": base64.b64encode(b"scope").decode(),
        "custody_generation": "generation",
        "custody_digest": "4" * 64,
        "policy_ref": {"identity": "policy", "head": "policy-head", "fingerprint": "5" * 64},
        "policy_bytes_base64": base64.b64encode(b"policy").decode(),
        "source_signature_digest": "6" * 64,
        "records": [narrowing],
    }
    cut["cut_digest"] = hashlib.sha256(canonical(cut)).hexdigest()
    return {
        "schema_id": MEMBER_SCHEMA,
        "deployment_id": "deployment",
        "database_id": "database",
        "database_genesis_digest": "a" * 64,
        "tenant": "tenant",
        "principal": "principal",
        "run_id": "run",
        "turn_id": "turn",
        "attempt_id": "attempt",
        "manifest_digest": "b" * 64,
        "member_index": 0,
        "member_digest": "c" * 64,
        "selected_prepare": prepare,
        "workspace_issuance": workspace,
        "member_bytes_base64": base64.b64encode(b"member").decode(),
        "provenance": provenance,
        "disclosure": disclosure,
        "narrowing_cut": cut,
    }


def worker() -> dict[str, object]:
    return {
        "schema_id": WORKER_SCHEMA,
        "deployment_id": "deployment",
        "database_id": "database",
        "database_genesis_digest": "a" * 64,
        "tenant": "tenant",
        "principal": "principal",
        "runtime_instance_id": "instance",
        "owner_id": "agent_loop",
        "owner_route_generation": "generation",
        "run": {"identity": "run", "head": "head", "fingerprint": "b" * 64},
        "worker_session_id": "worker",
        "fence": {
            "kind": "NON_SCHEDULER",
            "run_id": "run",
            "run_head": "head",
            "worker_session": "worker",
            "runtime_generation": "generation",
            "scheduler_id": "NOT_APPLICABLE",
            "scheduler_generation": "NOT_APPLICABLE",
            "scheduler_lease": "NOT_APPLICABLE",
            "scheduler_lease_generation": "NOT_APPLICABLE",
        },
    }
