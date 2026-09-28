"""Closed, canonical auxiliary H1 delivery-evidence records.

These records are authenticated *claims* once retained by the enrolled journal;
decoding them alone never proves selected-history validity or issues authority.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

MEMBER_SCHEMA = "chiplog.execution.h1-member-evidence.v1"
WORKER_SCHEMA = "chiplog.execution.h1-worker-fence.v1"
ROOT_SCHEMA = "chiplog.execution.h1-delivery-selection-closure.v1"
ROOT_V2_SCHEMA = "chiplog.execution.h1-delivery-selection-closure.v2"
_MAX_BYTES = 1_000_000


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _strict_json(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_BYTES:
        raise ValueError("evidence bytes are absent or exceed the bound")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("evidence is not strict UTF-8 JSON") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise ValueError("evidence bytes are not canonical")
    return value


def _exact(value: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{name} fields differ from the closed schema")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is not a nonempty string")
    return value


def _digest(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} is not a lowercase SHA-256 digest")
    return value


def _index(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**64 - 1:
        raise ValueError(f"{name} is not UInt64")
    return value


def _b64(value: object, name: str, *, nonempty: bool = True) -> bytes:
    text = _text(value, name) if nonempty else value
    if not isinstance(text, str):
        raise ValueError(f"{name} is not base64 text")
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{name} is not standard padded base64") from error
    if (nonempty and not decoded) or base64.b64encode(decoded).decode("ascii") != text:
        raise ValueError(f"{name} is noncanonical base64")
    return decoded


def _head(value: object, name: str) -> None:
    data = _exact(value, {"identity", "head", "fingerprint"}, name)
    _text(data["identity"], f"{name}.identity")
    _text(data["head"], f"{name}.head")
    _digest(data["fingerprint"], f"{name}.fingerprint")


def _workspace(value: object, name: str) -> None:
    data = _exact(value, {"schema_id", "tenant", "batch_id", "entry_id", "payload_digest"}, name)
    if data["schema_id"] != "chiplog.execution.h1-workspace-issuance-ref.v1":
        raise ValueError(f"{name} schema differs")
    _text(data["tenant"], f"{name}.tenant")
    _text(data["batch_id"], f"{name}.batch_id")
    _digest(data["entry_id"], f"{name}.entry_id")
    _digest(data["payload_digest"], f"{name}.payload_digest")


def _native_envelope(value: object, name: str) -> dict[str, object]:
    data = _exact(value, {"journal_role", "entry_id", "payload_digest"}, name)
    if data["journal_role"] != "loop-decisions":
        raise ValueError(f"{name} journal role differs")
    _digest(data["entry_id"], f"{name}.entry_id")
    _digest(data["payload_digest"], f"{name}.payload_digest")
    return data


def _locator(value: object, name: str) -> dict[str, object]:
    data = _exact(value, {"tenant", "entry_id", "payload_digest"}, name)
    _text(data["tenant"], f"{name}.tenant")
    _digest(data["entry_id"], f"{name}.entry_id")
    _digest(data["payload_digest"], f"{name}.payload_digest")
    return data


def _member(value: dict[str, object]) -> None:
    fields = {
        "schema_id",
        "deployment_id",
        "database_id",
        "database_genesis_digest",
        "tenant",
        "principal",
        "run_id",
        "turn_id",
        "attempt_id",
        "manifest_digest",
        "member_index",
        "member_digest",
        "selected_prepare",
        "workspace_issuance",
        "member_bytes_base64",
        "provenance",
        "disclosure",
        "narrowing_cut",
    }
    data = _exact(value, fields, "member")
    if data["schema_id"] != MEMBER_SCHEMA:
        raise ValueError("member schema differs")
    for key in (
        "deployment_id",
        "database_id",
        "tenant",
        "principal",
        "run_id",
        "turn_id",
        "attempt_id",
    ):
        _text(data[key], key)
    for key in ("database_genesis_digest", "manifest_digest", "member_digest"):
        _digest(data[key], key)
    _index(data["member_index"], "member_index")
    _workspace(data["workspace_issuance"], "workspace_issuance")
    selected_prepare = _native_envelope(data["selected_prepare"], "selected_prepare")
    member_bytes = _b64(data["member_bytes_base64"], "member_bytes_base64")
    if hashlib.sha256(member_bytes).hexdigest() != data["member_digest"]:
        raise ValueError("member digest differs from member bytes")
    member_key = [
        data["deployment_id"],
        data["database_id"],
        data["database_genesis_digest"],
        data["tenant"],
        data["principal"],
        data["run_id"],
        data["turn_id"],
        data["attempt_id"],
        data["manifest_digest"],
        data["member_index"],
        data["member_digest"],
    ]
    provenance = _exact(
        data["provenance"],
        {
            "schema_id",
            "member_key",
            "original_head",
            "source_kind",
            "source_bytes_base64",
            "source_locator",
        },
        "provenance",
    )
    if (
        provenance["schema_id"] != "chiplog.execution.h1-member-provenance.v1"
        or provenance["member_key"] != member_key
    ):
        raise ValueError("member provenance differs")
    _text(provenance["original_head"], "provenance.original_head")
    source_kind = provenance["source_kind"]
    if source_kind not in {"PROMPT", "SCHEMA", "CONTEXT", "WORKSPACE"}:
        raise ValueError("member provenance source kind differs")
    _b64(provenance["source_bytes_base64"], "provenance.source_bytes_base64")
    source_locator = provenance["source_locator"]
    if source_kind in {"PROMPT", "SCHEMA"}:
        locator = _exact(
            source_locator,
            {"kind", "selected_prepare", "artifact_digest"},
            "provenance.source_locator",
        )
        if locator["kind"] != "PREPARE_ARTIFACT" or locator["selected_prepare"] != selected_prepare:
            raise ValueError("member provenance source locator differs")
        _digest(locator["artifact_digest"], "provenance.source_locator.artifact_digest")
    elif source_kind == "WORKSPACE":
        locator = _exact(
            source_locator, {"kind", "workspace_issuance"}, "provenance.source_locator"
        )
        if locator["kind"] != "WORKSPACE_ISSUANCE":
            raise ValueError("member workspace source locator differs")
        _workspace(locator["workspace_issuance"], "provenance.source_locator.workspace_issuance")
        if locator["workspace_issuance"] != data["workspace_issuance"]:
            raise ValueError("member workspace source locator differs from member workspace")
    else:
        locator = _exact(
            source_locator,
            {"kind", "selected_prepare", "started_run"},
            "provenance.source_locator",
        )
        if locator["kind"] != "STARTED_RUN" or locator["selected_prepare"] != selected_prepare:
            raise ValueError("member context source locator differs")
        _head(locator["started_run"], "provenance.source_locator.started_run")
    disclosure = _exact(
        data["disclosure"],
        {
            "schema_id",
            "member_key",
            "original_head",
            "original_label",
            "provenance_fingerprint",
            "rule",
            "workspace_issuance",
            "started_run",
        },
        "disclosure",
    )
    if (
        disclosure["schema_id"] != "chiplog.execution.h1-member-disclosure.v1"
        or disclosure["member_key"] != member_key
    ):
        raise ValueError("member disclosure differs")
    rules = {
        "PROMPT": "PROMPT_JOIN",
        "SCHEMA": "SCHEMA_PUBLIC",
        "CONTEXT": "CONTEXT_JOIN",
        "WORKSPACE": "WORKSPACE_ORIGINAL",
    }
    if disclosure["rule"] != rules[source_kind]:
        raise ValueError("member disclosure rule differs from source kind")
    _text(disclosure["original_head"], "disclosure.original_head")
    label = _exact(
        disclosure["original_label"],
        {"lattice_version", "value", "allowed_endpoints"},
        "disclosure.original_label",
    )
    if label["lattice_version"] != "chiplog.disclosure.v1":
        raise ValueError("disclosure label lattice differs")
    if label["value"] not in {"UNRESTRICTED", "ENDPOINT_RESTRICTED", "DENY_ALL"}:
        raise ValueError("disclosure original label value differs")
    endpoints = label["allowed_endpoints"]
    if (
        not isinstance(endpoints, list)
        or any(not isinstance(endpoint, str) or not endpoint for endpoint in endpoints)
        or endpoints != sorted(set(endpoints))
        or (label["value"] == "ENDPOINT_RESTRICTED") != bool(endpoints)
    ):
        raise ValueError("disclosure allowed endpoints differ")
    if source_kind == "SCHEMA" and label["value"] != "UNRESTRICTED":
        raise ValueError("schema original label differs")
    if (
        _digest(disclosure["provenance_fingerprint"], "disclosure.provenance_fingerprint")
        != hashlib.sha256(_canonical(provenance)).hexdigest()
    ):
        raise ValueError("member provenance fingerprint differs")
    _workspace(disclosure["workspace_issuance"], "disclosure.workspace_issuance")
    _head(disclosure["started_run"], "disclosure.started_run")
    if disclosure["workspace_issuance"] != data["workspace_issuance"]:
        raise ValueError("member disclosure workspace differs")
    if source_kind == "CONTEXT" and locator["started_run"] != disclosure["started_run"]:
        raise ValueError("member context source locator differs from started run")
    cut = _exact(
        data["narrowing_cut"],
        {
            "schema_id",
            "member_key",
            "scope_ref",
            "scope_bytes_base64",
            "custody_generation",
            "custody_digest",
            "policy_ref",
            "policy_bytes_base64",
            "source_signature_digest",
            "records",
            "cut_digest",
        },
        "narrowing_cut",
    )
    if (
        cut["schema_id"] != "chiplog.execution.h1-member-narrowing-cut.v1"
        or cut["member_key"] != member_key
    ):
        raise ValueError("member narrowing cut differs")
    _head(cut["scope_ref"], "narrowing_cut.scope_ref")
    _b64(cut["scope_bytes_base64"], "narrowing_cut.scope_bytes_base64")
    _text(cut["custody_generation"], "narrowing_cut.custody_generation")
    _digest(cut["custody_digest"], "narrowing_cut.custody_digest")
    _head(cut["policy_ref"], "narrowing_cut.policy_ref")
    _b64(cut["policy_bytes_base64"], "narrowing_cut.policy_bytes_base64")
    _digest(cut["source_signature_digest"], "narrowing_cut.source_signature_digest")
    records = cut["records"]
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError("member narrowing cut must contain exactly one record")
    record = _exact(
        records[0],
        {"schema_id", "member_key", "ordinal", "source_ref", "source_bytes_base64", "label"},
        "narrowing record",
    )
    if (
        record["schema_id"] != "chiplog.execution.h1-member-narrowing.v1"
        or record["member_key"] != member_key
        or record["ordinal"] != 0
    ):
        raise ValueError("member narrowing record differs")
    _head(record["source_ref"], "narrowing record.source_ref")
    _b64(record["source_bytes_base64"], "narrowing record.source_bytes_base64")
    restricted = _exact(
        record["label"],
        {"lattice_version", "value", "allowed_endpoints"},
        "narrowing record.label",
    )
    if (
        restricted["lattice_version"] != "chiplog.disclosure.v1"
        or restricted["value"] != "ENDPOINT_RESTRICTED"
        or not isinstance(restricted["allowed_endpoints"], list)
        or len(restricted["allowed_endpoints"]) != 1
    ):
        raise ValueError("member narrowing label differs")
    _text(restricted["allowed_endpoints"][0], "narrowing record.allowed_endpoint")
    unsigned = dict(cut)
    cut_digest = unsigned.pop("cut_digest")
    if (
        _digest(cut_digest, "narrowing_cut.cut_digest")
        != hashlib.sha256(_canonical(unsigned)).hexdigest()
    ):
        raise ValueError("member narrowing cut digest differs")


def _worker(value: dict[str, object]) -> None:
    fields = {
        "schema_id",
        "deployment_id",
        "database_id",
        "database_genesis_digest",
        "tenant",
        "principal",
        "runtime_instance_id",
        "owner_id",
        "owner_route_generation",
        "run",
        "worker_session_id",
        "fence",
    }
    data = _exact(value, fields, "worker")
    if data["schema_id"] != WORKER_SCHEMA or data["owner_id"] != "agent_loop":
        raise ValueError("worker schema or owner differs")
    for key in (
        "deployment_id",
        "database_id",
        "tenant",
        "principal",
        "runtime_instance_id",
        "owner_route_generation",
        "worker_session_id",
    ):
        _text(data[key], key)
    _digest(data["database_genesis_digest"], "database_genesis_digest")
    _head(data["run"], "worker.run")
    run = _exact(data["run"], {"identity", "head", "fingerprint"}, "worker.run")
    fence = _exact(
        data["fence"],
        {
            "kind",
            "run_id",
            "run_head",
            "worker_session",
            "runtime_generation",
            "scheduler_id",
            "scheduler_generation",
            "scheduler_lease",
            "scheduler_lease_generation",
        },
        "worker.fence",
    )
    if (
        fence["kind"] != "NON_SCHEDULER"
        or fence["run_id"] != run["identity"]
        or fence["run_head"] != run["head"]
        or fence["worker_session"] != data["worker_session_id"]
        or fence["runtime_generation"] != data["owner_route_generation"]
    ):
        raise ValueError("worker fence differs from worker identity")
    for key in ("kind", "run_id", "run_head", "worker_session", "runtime_generation"):
        _text(fence[key], f"worker.fence.{key}")
    for key in (
        "scheduler_id",
        "scheduler_generation",
        "scheduler_lease",
        "scheduler_lease_generation",
    ):
        if fence[key] != "NOT_APPLICABLE":
            raise ValueError(f"worker.fence.{key} is not native NotApplicable")
    # This is E's named, lossless inverse of the native non-scheduler fence.
    # Equality against the live owner fence remains an issuer responsibility.
    from chiplog.capabilities.agent_loop.recovery_contracts import NonSchedulerFence

    try:
        NonSchedulerFence.model_validate(
            {
                "kind": "NON_SCHEDULER_NOT_APPLICABLE",
                "lineage": {"kind": fence["scheduler_id"]},
                "physical_root": {"kind": fence["scheduler_generation"]},
                "lease": {"kind": fence["scheduler_lease"]},
                "clock_proof": {"kind": fence["scheduler_lease_generation"]},
                "run_id": fence["run_id"],
                "run_head": fence["run_head"],
                "worker_session_id": fence["worker_session"],
                "runtime_generation": fence["runtime_generation"],
            }
        )
    except ValueError as error:
        raise ValueError("worker fence has no exact native inverse") from error


def _root(value: dict[str, object]) -> None:
    # Full selected-source replay is deliberately outside this bounded decoder.
    fields = {
        "schema_id",
        "deployment_id",
        "database_id",
        "database_genesis_digest",
        "tenant_id",
        "principal_id",
        "journal_role",
        "journal_instance_id",
        "command_id",
        "command_fingerprint",
        "request_digest",
        "predecessor_commitment",
        "expected_tenant_frontier",
        "request_bytes_base64",
        "manifest",
        "original",
        "members",
        "worker",
        "observation_bytes_base64",
        "accepted_sources",
    }
    data = _exact(value, fields, "root")
    if data["schema_id"] != ROOT_SCHEMA or data["journal_role"] != "h1-delivery-evidence":
        raise ValueError("root schema or journal role differs")
    for key in (
        "deployment_id",
        "database_id",
        "tenant_id",
        "principal_id",
        "journal_instance_id",
        "command_id",
    ):
        _text(data[key], key)
    for key in (
        "database_genesis_digest",
        "command_fingerprint",
        "request_digest",
        "predecessor_commitment",
    ):
        _digest(data[key], key)
    _index(data["expected_tenant_frontier"], "expected_tenant_frontier")
    request = _b64(data["request_bytes_base64"], "request_bytes_base64")
    if hashlib.sha256(request).hexdigest() != data["request_digest"]:
        raise ValueError("root request digest differs")
    try:
        from chiplog.composition.h1_completion_issuance import (
            SCHEMA as H1_COMPLETION_SCHEMA,
        )
        from chiplog.composition.h1_completion_issuance import (
            decode_h1_completion_issuance,
        )
        from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2

        decoded = CompleteDeliveryBatchV2.model_validate_json(request)
        if decoded.authentication.applicability_schema != H1_COMPLETION_SCHEMA:
            raise ValueError("root request has no H1 applicability")
        decode_h1_completion_issuance(decoded)
    except (ImportError, ValueError) as error:
        raise ValueError("root request is not a closed H1 complete-delivery request") from error
    if (
        decoded.identity.command_id != data["command_id"]
        or decoded.identity.command_fingerprint != data["command_fingerprint"]
    ):
        raise ValueError("root command identity differs from request")
    manifest = _exact(data["manifest"], {"subject_id", "revision"}, "root.manifest")
    _text(manifest["subject_id"], "root.manifest.subject_id")
    revision = _exact(
        manifest["revision"], {"kind", "head", "fingerprint"}, "root.manifest.revision"
    )
    if revision["kind"] != "PRESENT":
        raise ValueError("root manifest revision is not present")
    _text(revision["head"], "root.manifest.revision.head")
    _digest(revision["fingerprint"], "root.manifest.revision.fingerprint")
    original = _exact(
        data["original"],
        {
            "workspace_issuance",
            "initialization",
            "prepare",
            "seal",
            "started_run",
            "captured_run",
            "prepared_run",
        },
        "root.original",
    )
    _workspace(original["workspace_issuance"], "root.original.workspace_issuance")
    for key in ("initialization", "prepare", "seal"):
        _native_envelope(original[key], f"root.original.{key}")
    for key in ("started_run", "captured_run", "prepared_run"):
        _head(original[key], f"root.original.{key}")
    members = data["members"]
    if not isinstance(members, list) or not members:
        raise ValueError("root members are absent")
    occurrences: list[tuple[str, str, int]] = []
    for ordinal, occurrence in enumerate(members):
        occurrence = _exact(
            occurrence,
            {
                "turn_id",
                "attempt_id",
                "manifest_digest",
                "member_index",
                "member_digest",
                "evidence",
            },
            "root.member occurrence",
        )
        turn_id = _text(occurrence["turn_id"], "root.member occurrence.turn_id")
        attempt_id = _text(occurrence["attempt_id"], "root.member occurrence.attempt_id")
        index = _index(occurrence["member_index"], "root.member occurrence.member_index")
        _digest(occurrence["manifest_digest"], "root.member occurrence.manifest_digest")
        _digest(occurrence["member_digest"], "root.member occurrence.member_digest")
        locator = _locator(occurrence["evidence"], "root.member occurrence.evidence")
        if locator["tenant"] != data["tenant_id"]:
            raise ValueError("root member locator tenant differs")
        occurrence_key = (turn_id, attempt_id, index)
        if occurrence_key in occurrences or (ordinal and occurrence_key <= occurrences[-1]):
            raise ValueError("root member occurrence order differs")
        occurrences.append(occurrence_key)
    worker = _locator(data["worker"], "root.worker")
    if worker["tenant"] != data["tenant_id"]:
        raise ValueError("root worker locator tenant differs")
    accepted = _exact(
        data["accepted_sources"],
        {
            "scope_bytes_base64",
            "policy_bytes_base64",
            "registration_bytes_base64",
            "registration_digest",
            "registration_generation",
            "resource_observation",
        },
        "root.accepted_sources",
    )
    _b64(accepted["scope_bytes_base64"], "root.accepted_sources.scope_bytes_base64")
    _b64(accepted["policy_bytes_base64"], "root.accepted_sources.policy_bytes_base64")
    registration = _b64(
        accepted["registration_bytes_base64"], "root.accepted_sources.registration_bytes_base64"
    )
    if (
        _digest(accepted["registration_digest"], "root.accepted_sources.registration_digest")
        != hashlib.sha256(registration).hexdigest()
    ):
        raise ValueError("root registration digest differs")
    _index(accepted["registration_generation"], "root.accepted_sources.registration_generation")
    observation = _exact(
        accepted["resource_observation"],
        {
            "grant_bytes_base64",
            "credential_bytes_base64",
            "endpoint_bytes_base64",
            "clock_epoch",
            "signature",
        },
        "root.accepted_sources.resource_observation",
    )
    for key in (
        "grant_bytes_base64",
        "credential_bytes_base64",
        "endpoint_bytes_base64",
        "signature",
    ):
        _b64(observation[key], f"root.accepted_sources.resource_observation.{key}")
    _index(observation["clock_epoch"], "root.accepted_sources.resource_observation.clock_epoch")
    _b64(data["observation_bytes_base64"], "observation_bytes_base64")


def _root_v2(value: dict[str, object]) -> None:
    """Decode the compact, pre-selection closure for one V2 H1 batch."""
    fields = {
        "schema_id",
        "deployment_id",
        "database_id",
        "database_genesis_digest",
        "tenant_id",
        "principal_id",
        "journal_role",
        "journal_instance_id",
        "command_id",
        "command_fingerprint",
        "request_digest",
        "predecessor_commitment",
        "expected_tenant_frontier",
    }
    data = _exact(value, fields, "root v2")
    if data["schema_id"] != ROOT_V2_SCHEMA or data["journal_role"] != "h1-delivery-evidence":
        raise ValueError("root v2 schema or journal role differs")
    for key in (
        "deployment_id",
        "database_id",
        "tenant_id",
        "principal_id",
        "journal_instance_id",
        "command_id",
    ):
        _text(data[key], key)
    for key in (
        "database_genesis_digest",
        "command_fingerprint",
        "request_digest",
        "predecessor_commitment",
    ):
        _digest(data[key], key)
    _index(data["expected_tenant_frontier"], "expected_tenant_frontier")
    # The selected owner journal retains the exact request and authenticates
    # this digest during source replay; this decoder validates claims only.


@dataclass(frozen=True, slots=True)
class _Evidence:
    _raw: bytes
    _value: dict[str, object]

    @property
    def schema_id(self) -> str:
        return self._value["schema_id"]  # type: ignore[return-value]

    @property
    def tenant(self) -> str:
        return self._value.get("tenant", self._value.get("tenant_id", ""))  # type: ignore[return-value]

    def canonical_bytes(self) -> bytes:
        return self._raw


class H1DeliveryMemberEvidenceV1(_Evidence):
    pass


class H1DeliveryWorkerFenceV1(_Evidence):
    pass


class H1DeliverySelectionClosureV1(_Evidence):
    pass


class H1DeliverySelectionClosureV2(_Evidence):
    pass


@dataclass(frozen=True, slots=True)
class H1DeliveryEvidenceLocatorV1:
    tenant: str
    entry_id: str
    payload_digest: str

    def __post_init__(self) -> None:
        _text(self.tenant, "locator.tenant")
        _digest(self.entry_id, "locator.entry_id")
        _digest(self.payload_digest, "locator.payload_digest")


H1DeliveryEvidence = (
    H1DeliveryMemberEvidenceV1
    | H1DeliveryWorkerFenceV1
    | H1DeliverySelectionClosureV1
    | H1DeliverySelectionClosureV2
)


def decode_h1_delivery_evidence(raw: bytes) -> H1DeliveryEvidence:
    value = _strict_json(raw)
    schema = value.get("schema_id")
    if schema == MEMBER_SCHEMA:
        _member(value)
        return H1DeliveryMemberEvidenceV1(raw, value)
    if schema == WORKER_SCHEMA:
        _worker(value)
        return H1DeliveryWorkerFenceV1(raw, value)
    if schema == ROOT_SCHEMA:
        _root(value)
        return H1DeliverySelectionClosureV1(raw, value)
    if schema == ROOT_V2_SCHEMA:
        _root_v2(value)
        return H1DeliverySelectionClosureV2(raw, value)
    raise ValueError("unknown H1 delivery evidence schema")
