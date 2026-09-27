"""Canonical inert RECORD codec for the H1 preseal P/E anchor.

The authenticated decision journal, installed historical reader, and owner
admission paths establish provenance and authority.  This closed DTO does none
of those things: it only prevents malformed retained anchor bytes from being
mistaken for a valid RECORD.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Final, Literal, cast

ANCHOR_SCHEMA_ID: Final = "chiplog.execution.h1-preseal-pe-anchor.v1"
PROJECTION_IDENTITY_DOMAIN: Final = "chiplog.h1.preseal-pe-anchor-projection.v1"
_MAX_BYTES: Final = 1_000_000
# Base64 is ASCII, so a field within one canonical anchor cannot contain more
# text bytes than the anchor's complete byte bound.  Keep this separate from
# _text: retained byte payloads are intentionally larger than identity-like
# strings, while still bounded before decoding.
_MAX_BASE64_TEXT_BYTES: Final = _MAX_BYTES
_DIGEST_LENGTH: Final = 64
_ROLES: Final = frozenset(("P", "E_MEMBER", "E_WORKER"))
_LEGACY_PREFIXES: Final = ("h1-evidence:", "h1-delivery-evidence:", "journal:")


class H1PresealPEAnchorRecordError(ValueError):
    """The bytes do not encode the closed, inert H1 preseal anchor RECORD."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise H1PresealPEAnchorRecordError("anchor value is not canonical JSON") from error


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise H1PresealPEAnchorRecordError("anchor has a duplicate JSON key")
        result[key] = value
    return result


def _forbid_float(value: str) -> object:
    del value
    raise H1PresealPEAnchorRecordError("anchor JSON does not permit floats")


def _forbid_constant(value: str) -> object:
    del value
    raise H1PresealPEAnchorRecordError("anchor JSON does not permit non-finite numbers")


def _strict_json(raw: bytes, *, name: str = "anchor") -> dict[str, object]:
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_BYTES:
        raise H1PresealPEAnchorRecordError(f"{name} bytes are absent or exceed the bound")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_float=_forbid_float,
            parse_constant=_forbid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, H1PresealPEAnchorRecordError) as error:
        raise H1PresealPEAnchorRecordError(f"{name} is not strict canonical UTF-8 JSON") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise H1PresealPEAnchorRecordError(f"{name} is not canonical JSON")
    return value


def _exact(value: object, fields: frozenset[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise H1PresealPEAnchorRecordError(f"{name} fields differ from the closed schema")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise H1PresealPEAnchorRecordError(f"{name} is not a nonempty bounded string")
    if value.startswith(_LEGACY_PREFIXES):
        raise H1PresealPEAnchorRecordError(
            f"{name} looks like an old E identity or journal locator"
        )
    return value


def _digest(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) != _DIGEST_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise H1PresealPEAnchorRecordError(f"{name} is not a lowercase SHA-256 digest")
    return value


def _uint(value: object, name: str) -> int:
    if type(value) is not int or value < 0 or value > 2**64 - 1:
        raise H1PresealPEAnchorRecordError(f"{name} is not UInt64")
    return value


def _b64(value: object, name: str, *, nonempty: bool = True) -> bytes:
    if (
        not isinstance(value, str)
        or (nonempty and not value)
        or len(value) > _MAX_BASE64_TEXT_BYTES
    ):
        raise H1PresealPEAnchorRecordError(f"{name} is not a nonempty bounded base64 string")
    text = value
    try:
        decoded = base64.b64decode(text, validate=True)
    except (TypeError, ValueError) as error:
        raise H1PresealPEAnchorRecordError(f"{name} is not standard padded base64") from error
    if (nonempty and not decoded) or base64.b64encode(decoded).decode("ascii") != text:
        raise H1PresealPEAnchorRecordError(f"{name} is not canonical base64")
    return decoded


def _head(value: object, name: str) -> dict[str, object]:
    data = _exact(value, frozenset(("identity", "head", "fingerprint")), name)
    _text(data["identity"], f"{name}.identity")
    _text(data["head"], f"{name}.head")
    _digest(data["fingerprint"], f"{name}.fingerprint")
    return data


def _embedded_canonical_object(raw: bytes, name: str) -> None:
    _strict_json(raw, name=name)


def _label(value: object, name: str, endpoint: str, *, require_endpoint: bool) -> dict[str, object]:
    data = _exact(
        value,
        frozenset(("lattice_version", "value", "allowed_endpoints")),
        name,
    )
    if data["lattice_version"] != "chiplog.disclosure.v1":
        raise H1PresealPEAnchorRecordError(f"{name} lattice version differs")
    label = data["value"]
    if label not in {"UNRESTRICTED", "DENY_ALL", "ENDPOINT_RESTRICTED"}:
        raise H1PresealPEAnchorRecordError(f"{name} value differs")
    endpoints = data["allowed_endpoints"]
    if not isinstance(endpoints, list) or not all(
        isinstance(item, str) and item for item in endpoints
    ):
        raise H1PresealPEAnchorRecordError(f"{name} allowed endpoints differ")
    if len(set(endpoints)) != len(endpoints):
        raise H1PresealPEAnchorRecordError(f"{name} repeats an endpoint")
    if label == "ENDPOINT_RESTRICTED" and endpoint not in endpoints:
        raise H1PresealPEAnchorRecordError(f"{name} excludes the accepted endpoint")
    if label != "ENDPOINT_RESTRICTED" and endpoints:
        raise H1PresealPEAnchorRecordError(f"{name} has endpoints for a non-restricted label")
    if require_endpoint and (label != "ENDPOINT_RESTRICTED" or endpoints != [endpoint]):
        raise H1PresealPEAnchorRecordError(f"{name} is not the exact endpoint narrowing")
    return data


def _binding(value: object) -> dict[str, object]:
    data = _exact(
        value,
        frozenset(
            (
                "deployment_id",
                "database_id",
                "database_genesis_digest",
                "tenant_id",
                "principal_id",
                "run_id",
                "turn_id",
                "attempt_id",
                "prepared_run_head",
                "manifest_digest",
                "selected_prepare",
                "publication",
                "selected_response_seal",
                "owner_asof",
                "member_count",
                "member_vector_digest",
            )
        ),
        "binding",
    )
    for key in (
        "deployment_id",
        "database_id",
        "tenant_id",
        "principal_id",
        "run_id",
        "turn_id",
        "attempt_id",
        "prepared_run_head",
    ):
        _text(data[key], f"binding.{key}")
    for key in (
        "database_genesis_digest",
        "manifest_digest",
        "member_vector_digest",
    ):
        _digest(data[key], f"binding.{key}")
    prepare = _exact(data["selected_prepare"], frozenset(("entry_id", "payload_digest")), "prepare")
    _digest(prepare["entry_id"], "prepare.entry_id")
    _digest(prepare["payload_digest"], "prepare.payload_digest")
    publication = _exact(
        data["publication"],
        frozenset(("operation_kind", "operation_id", "expected_head", "request_fingerprint")),
        "publication",
    )
    _text(publication["operation_kind"], "publication.operation_kind")
    _text(publication["operation_id"], "publication.operation_id")
    _uint(publication["expected_head"], "publication.expected_head")
    _digest(publication["request_fingerprint"], "publication.request_fingerprint")
    _head(data["selected_response_seal"], "selected_response_seal")
    owner_asof = _exact(data["owner_asof"], frozenset(("tenant_id", "owner_head")), "owner_asof")
    if owner_asof["tenant_id"] != data["tenant_id"]:
        raise H1PresealPEAnchorRecordError("owner_asof tenant differs from binding")
    _text(owner_asof["tenant_id"], "owner_asof.tenant_id")
    if owner_asof["owner_head"] is not None:
        _digest(owner_asof["owner_head"], "owner_asof.owner_head")
    if _uint(data["member_count"], "binding.member_count") == 0:
        raise H1PresealPEAnchorRecordError("binding.member_count is empty")
    return data


def _p(value: object, binding: dict[str, object]) -> dict[str, object]:
    data = _exact(
        value,
        frozenset(
            (
                "scope_ref",
                "scope_bytes_base64",
                "policy_ref",
                "policy_bytes_base64",
                "recipient",
                "custody_entry_generation",
                "custody_entry_digest",
                "source_signature_digest",
                "accepted_issue_wire_digest",
                "accepted_current_wire_digest",
            )
        ),
        "p",
    )
    _head(data["scope_ref"], "p.scope_ref")
    scope = _b64(data["scope_bytes_base64"], "p.scope_bytes_base64")
    _head(data["policy_ref"], "p.policy_ref")
    policy = _b64(data["policy_bytes_base64"], "p.policy_bytes_base64")
    _embedded_canonical_object(scope, "p scope bytes")
    _embedded_canonical_object(policy, "p policy bytes")
    native_digests = {
        cast(dict[str, object], binding["selected_prepare"])["payload_digest"],
        cast(dict[str, object], binding["selected_response_seal"])["fingerprint"],
        cast(dict[str, object], binding["publication"])["request_fingerprint"],
    }
    if (
        hashlib.sha256(scope).hexdigest() in native_digests
        or hashlib.sha256(policy).hexdigest() in native_digests
    ):
        raise H1PresealPEAnchorRecordError("P residual bytes duplicate a native payload")
    recipient = _exact(
        data["recipient"],
        frozenset(
            (
                "provider_id",
                "account_id",
                "recipient_id",
                "endpoint",
                "canonical_address_base64",
                "credential_binding",
            )
        ),
        "p.recipient",
    )
    for key in ("provider_id", "account_id", "recipient_id"):
        _text(recipient[key], f"p.recipient.{key}")
    _head(recipient["endpoint"], "p.recipient.endpoint")
    _b64(recipient["canonical_address_base64"], "p.recipient.canonical_address_base64")
    _head(recipient["credential_binding"], "p.recipient.credential_binding")
    _uint(data["custody_entry_generation"], "p.custody_entry_generation")
    for key in (
        "custody_entry_digest",
        "source_signature_digest",
        "accepted_issue_wire_digest",
        "accepted_current_wire_digest",
    ):
        _digest(data[key], f"p.{key}")
    return data


def _member(
    value: object, index: int, binding: dict[str, object], endpoint: str
) -> dict[str, object]:
    data = _exact(
        value,
        frozenset(("member_index", "member_digest", "provenance", "disclosure", "narrowing")),
        f"e_members[{index}]",
    )
    if _uint(data["member_index"], f"e_members[{index}].member_index") != index:
        raise H1PresealPEAnchorRecordError("E member vector is not complete and ordered")
    _digest(data["member_digest"], f"e_members[{index}].member_digest")
    provenance = _exact(
        data["provenance"],
        frozenset(("original_head", "source_kind", "source_locator")),
        f"e_members[{index}].provenance",
    )
    _text(provenance["original_head"], f"e_members[{index}].provenance.original_head")
    source_kind = provenance["source_kind"]
    if source_kind not in {"WORKSPACE", "CONTEXT", "PROMPT", "SCHEMA"}:
        raise H1PresealPEAnchorRecordError("E member source kind differs")
    locator = provenance["source_locator"]
    if source_kind in {"PROMPT", "SCHEMA"}:
        locator_data = _exact(
            locator,
            frozenset(("kind", "prepare_entry_id", "prepare_payload_digest", "artifact_digest")),
            "member prepare artifact locator",
        )
        prepare = cast(dict[str, object], binding["selected_prepare"])
        if (
            locator_data["kind"] != "PREPARE_ARTIFACT"
            or locator_data["prepare_entry_id"] != prepare["entry_id"]
            or locator_data["prepare_payload_digest"] != prepare["payload_digest"]
        ):
            raise H1PresealPEAnchorRecordError("E member locator differs from selected Prepare")
        _digest(locator_data["artifact_digest"], "member artifact digest")
    elif source_kind == "WORKSPACE":
        locator_data = _exact(
            locator, frozenset(("kind", "entry_id", "payload_digest")), "workspace locator"
        )
        if locator_data["kind"] != "WORKSPACE_ISSUANCE":
            raise H1PresealPEAnchorRecordError("E member workspace locator kind differs")
        _digest(locator_data["entry_id"], "workspace locator entry")
        _digest(locator_data["payload_digest"], "workspace locator payload")
    else:
        locator_data = _exact(
            locator, frozenset(("kind", "run_id", "run_head")), "started run locator"
        )
        if locator_data["kind"] != "STARTED_RUN" or locator_data["run_id"] != binding["run_id"]:
            raise H1PresealPEAnchorRecordError("E member started-run locator differs")
        _text(locator_data["run_head"], "started run locator head")
    disclosure = _exact(
        data["disclosure"],
        frozenset(("original_head", "original_label", "rule")),
        f"e_members[{index}].disclosure",
    )
    _text(disclosure["original_head"], f"e_members[{index}].disclosure.original_head")
    expected_rule = {
        "WORKSPACE": "WORKSPACE_ORIGINAL",
        "CONTEXT": "CONTEXT_JOIN",
        "PROMPT": "PROMPT_JOIN",
        "SCHEMA": "SCHEMA_PUBLIC",
    }[source_kind]
    if disclosure["rule"] != expected_rule:
        raise H1PresealPEAnchorRecordError("E member disclosure rule differs from source kind")
    label = _label(
        disclosure["original_label"],
        f"e_members[{index}].disclosure.original_label",
        endpoint,
        require_endpoint=False,
    )
    if source_kind == "SCHEMA" and label["value"] != "UNRESTRICTED":
        raise H1PresealPEAnchorRecordError("schema E member label differs")
    narrowing = _exact(
        data["narrowing"], frozenset(("ordinal", "label")), f"e_members[{index}].narrowing"
    )
    if _uint(narrowing["ordinal"], f"e_members[{index}].narrowing.ordinal") != 0:
        raise H1PresealPEAnchorRecordError("E member narrowing ordinal differs")
    _label(
        narrowing["label"],
        f"e_members[{index}].narrowing.label",
        endpoint,
        require_endpoint=True,
    )
    return data


def _e_members(value: object, binding: dict[str, object], p: dict[str, object]) -> None:
    if not isinstance(value, list) or len(value) != binding["member_count"]:
        raise H1PresealPEAnchorRecordError("E member vector count differs from binding")
    if hashlib.sha256(_canonical(value)).hexdigest() != binding["member_vector_digest"]:
        raise H1PresealPEAnchorRecordError("E member vector digest differs from binding")
    recipient = cast(dict[str, object], p["recipient"])
    endpoint = cast(dict[str, object], recipient["endpoint"])["identity"]
    digests: set[str] = set()
    for index, item in enumerate(value):
        member = _member(item, index, binding, cast(str, endpoint))
        digest = cast(str, member["member_digest"])
        if digest in digests:
            raise H1PresealPEAnchorRecordError("E member vector repeats an occurrence")
        digests.add(digest)


def _e_worker(value: object, binding: dict[str, object]) -> None:
    data = _exact(
        value,
        frozenset(
            (
                "runtime_instance_id",
                "owner_route_generation",
                "worker_session_id",
                "owner_id",
                "run_head",
                "fence_kind",
            )
        ),
        "e_worker",
    )
    _text(data["runtime_instance_id"], "e_worker.runtime_instance_id")
    _text(data["owner_route_generation"], "e_worker.owner_route_generation")
    _text(data["worker_session_id"], "e_worker.worker_session_id")
    if data["owner_id"] != "agent_loop" or data["fence_kind"] != "NON_SCHEDULER":
        raise H1PresealPEAnchorRecordError("E worker role or fence differs")
    if data["run_head"] != binding["prepared_run_head"]:
        raise H1PresealPEAnchorRecordError("E worker run head differs from prepared Run")
    _text(data["run_head"], "e_worker.run_head")


@dataclass(frozen=True, slots=True)
class H1PresealPEAnchorRecordV1:
    """Closed retained data; decoding it confers neither authority nor trust."""

    _raw: bytes

    def __post_init__(self) -> None:
        _validate(_strict_json(self._raw))

    @classmethod
    def from_mapping(cls, value: object) -> H1PresealPEAnchorRecordV1:
        raw = _canonical(value)
        return cls(raw)

    def canonical_bytes(self) -> bytes:
        return self._raw

    def as_dict(self) -> dict[str, object]:
        return _strict_json(self._raw)

    @property
    def binding(self) -> dict[str, object]:
        return cast(dict[str, object], self.as_dict()["binding"])


def _validate(value: dict[str, object]) -> None:
    data = _exact(
        value,
        frozenset(("schema_id", "version", "binding", "p", "e_members", "e_worker")),
        "anchor",
    )
    if data["schema_id"] != ANCHOR_SCHEMA_ID or data["version"] != 1:
        raise H1PresealPEAnchorRecordError("anchor schema version differs")
    binding = _binding(data["binding"])
    p = _p(data["p"], binding)
    _e_members(data["e_members"], binding, p)
    _e_worker(data["e_worker"], binding)


def decode_h1_preseal_pe_anchor_record(raw: bytes) -> H1PresealPEAnchorRecordV1:
    """Decode exact canonical RECORD bytes; provenance must be checked by the caller."""

    return H1PresealPEAnchorRecordV1(raw)


def h1_preseal_pe_anchor_projection_identity(
    anchor: H1PresealPEAnchorRecordV1,
    *,
    selected_decision_id: str,
    role: Literal["P", "E_MEMBER", "E_WORKER"],
    member_index: int | None = None,
) -> str:
    """Return a new anchor-derived identity, never an E journal/exact-head identity."""

    if type(anchor) is not H1PresealPEAnchorRecordV1 or role not in _ROLES:
        raise H1PresealPEAnchorRecordError("anchor projection role differs")
    _digest(selected_decision_id, "selected decision ID")
    if (role == "E_MEMBER") != (member_index is not None):
        raise H1PresealPEAnchorRecordError("anchor projection member ordinal differs")
    if member_index is not None:
        count = _uint(anchor.binding["member_count"], "binding.member_count")
        if type(member_index) is not int or not 0 <= member_index < count:
            raise H1PresealPEAnchorRecordError("anchor projection member ordinal is invalid")
    binding = anchor.binding
    preimage = [
        PROJECTION_IDENTITY_DOMAIN,
        ANCHOR_SCHEMA_ID,
        1,
        binding["deployment_id"],
        binding["database_id"],
        binding["database_genesis_digest"],
        binding["tenant_id"],
        selected_decision_id,
        role,
        member_index,
    ]
    return hashlib.sha256(_canonical(preimage)).hexdigest()


__all__ = [
    "ANCHOR_SCHEMA_ID",
    "PROJECTION_IDENTITY_DOMAIN",
    "H1PresealPEAnchorRecordError",
    "H1PresealPEAnchorRecordV1",
    "decode_h1_preseal_pe_anchor_record",
    "h1_preseal_pe_anchor_projection_identity",
]
