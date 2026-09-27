import hashlib
from typing import Any, cast

import pytest

from chiplog.composition.h1_delivery_evidence_contracts import (
    MEMBER_SCHEMA,
    H1DeliveryMemberEvidenceV1,
    H1DeliveryWorkerFenceV1,
    decode_h1_delivery_evidence,
)
from tests.support.h1_delivery_evidence import canonical as _canonical
from tests.support.h1_delivery_evidence import member as _raw_member
from tests.support.h1_delivery_evidence import worker as _worker


def _refresh_cut_digest(value: dict[str, object]) -> None:
    cut = cast(dict[str, Any], value["narrowing_cut"])
    unsigned = dict(cut)
    unsigned.pop("cut_digest")
    cut["cut_digest"] = hashlib.sha256(_canonical(unsigned)).hexdigest()


def _member(*args: object, **kwargs: object) -> dict[str, object]:
    """Make the direct-test fixture conform to E's closed narrowing wire form."""
    value = _raw_member(*args, **kwargs)  # type: ignore[arg-type]
    cut = cast(dict[str, Any], value["narrowing_cut"])
    records = cast(list[dict[str, Any]], cut["records"])
    records[0]["label"]["lattice_version"] = "chiplog.disclosure.v1"
    _refresh_cut_digest(value)
    return value


@pytest.mark.parametrize(
    ("value", "expected"),
    [(_member(), H1DeliveryMemberEvidenceV1), (_worker(), H1DeliveryWorkerFenceV1)],
)
def test_closed_delivery_evidence_records_decode_only_exact_canonical_bytes(
    value: dict[str, object], expected: type[object]
) -> None:
    raw = _canonical(value)
    decoded = decode_h1_delivery_evidence(raw)
    assert isinstance(decoded, expected)
    assert decoded.canonical_bytes() == raw


@pytest.mark.parametrize("source_kind", ("PROMPT", "SCHEMA", "WORKSPACE", "CONTEXT"))
def test_member_source_locator_variants_are_closed_and_linked(source_kind: str) -> None:
    decoded = decode_h1_delivery_evidence(_canonical(_member(source_kind)))
    assert isinstance(decoded, H1DeliveryMemberEvidenceV1)


@pytest.mark.parametrize("source_kind", ("WORKSPACE", "CONTEXT", "PROMPT"))
@pytest.mark.parametrize(
    ("label_value", "endpoints"),
    (("ENDPOINT_RESTRICTED", ["endpoint"]), ("DENY_ALL", [])),
)
def test_member_preserves_typed_native_original_label(
    source_kind: str, label_value: str, endpoints: list[str]
) -> None:
    decoded = decode_h1_delivery_evidence(
        _canonical(
            _member(
                source_kind,
                original_label_value=label_value,
                original_allowed_endpoints=endpoints,
            )
        )
    )
    assert isinstance(decoded, H1DeliveryMemberEvidenceV1)


@pytest.mark.parametrize(
    "value,endpoints",
    (
        ("UNKNOWN", []),
        ("ENDPOINT_RESTRICTED", []),
        ("UNRESTRICTED", ["endpoint"]),
        ("DENY_ALL", ["endpoint"]),
        ("ENDPOINT_RESTRICTED", ["z", "a"]),
        ("ENDPOINT_RESTRICTED", ["endpoint", "endpoint"]),
        ("ENDPOINT_RESTRICTED", [""]),
    ),
)
def test_member_rejects_malformed_native_original_label(value: str, endpoints: list[str]) -> None:
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(
            _canonical(
                _member("PROMPT", original_label_value=value, original_allowed_endpoints=endpoints)
            )
        )


def test_schema_member_requires_its_inherent_unrestricted_label() -> None:
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(
            _canonical(
                _member(
                    "SCHEMA",
                    original_label_value="ENDPOINT_RESTRICTED",
                    original_allowed_endpoints=["endpoint"],
                )
            )
        )


def test_member_digest_must_authenticate_exact_member_bytes() -> None:
    value = _member()
    value["member_bytes_base64"] = "b3RoZXI="

    with pytest.raises(ValueError, match="member digest"):
        decode_h1_delivery_evidence(_canonical(value))


@pytest.mark.parametrize("source_kind", ("WORKSPACE", "CONTEXT"))
def test_member_source_locator_cannot_substitute_workspace_or_started_run(source_kind: str) -> None:
    value = _member(source_kind)
    provenance = cast(dict[str, Any], value["provenance"])
    locator = cast(dict[str, Any], provenance["source_locator"])
    if source_kind == "WORKSPACE":
        locator["workspace_issuance"] = {
            "schema_id": "chiplog.execution.h1-workspace-issuance-ref.v1",
            "tenant": "tenant",
            "batch_id": "other",
            "entry_id": "e" * 64,
            "payload_digest": "f" * 64,
        }
    else:
        locator["started_run"] = {"identity": "run", "head": "other", "fingerprint": "1" * 64}
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value["disclosure"]["original_label"].__setitem__("lattice_version", "other"),
        lambda value: value["disclosure"].__setitem__("rule", "PROMPT_JOIN"),
    ),
)
def test_member_label_lattice_and_source_rule_pairing_are_closed(mutate: object) -> None:
    value = _member("WORKSPACE")
    mutate(value)  # type: ignore[operator]
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("unknown", True),
        lambda value: value.__setitem__("schema_id", "unknown.schema"),
    ],
)
def test_top_level_unknown_fields_and_schemas_fail_closed(mutate: object) -> None:
    value = _member()
    mutate(value)  # type: ignore[operator]
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_id":"' + MEMBER_SCHEMA.encode() + b'","schema_id":"x"}',
        _canonical(_member()).replace(b",", b", ", 1),
        _canonical(_member()).replace(b'"deployment"', b'"\\u0064eployment"', 1),
    ],
)
def test_duplicate_or_noncanonical_json_is_rejected(raw: bytes) -> None:
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(raw)


def test_unknown_nested_key_duplicate_nested_key_and_malformed_base64_are_rejected() -> None:
    value = _member()
    value["provenance"]["unknown"] = True  # type: ignore[index]
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.__setitem__("member_index", True),
        lambda value: value["selected_prepare"].__setitem__("unknown", True),
        lambda value: value["provenance"].__setitem__("source_kind", "OTHER"),
    ),
)
def test_member_nested_refs_boolean_index_and_unknown_source_kinds_fail(
    mutate: object,
) -> None:
    value = _member()
    mutate(value)  # type: ignore[operator]
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))
    raw = _canonical(_member()).replace(
        b'"source_kind":"PROMPT",', b'"source_kind":"PROMPT","source_kind":"PROMPT",'
    )
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(raw)
    value = _member()
    value["member_bytes_base64"] = "not base64!"
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: cast(dict[str, Any], value["narrowing_cut"])["records"].append(
            cast(dict[str, Any], value["narrowing_cut"])["records"][0].copy()
        ),
        lambda value: cast(dict[str, Any], value["narrowing_cut"])["records"][0].__setitem__(
            "ordinal", 1
        ),
    ),
)
def test_member_narrowing_cut_is_singleton_ordinal_zero(mutate: object) -> None:
    value = _member()
    mutate(value)  # type: ignore[operator]
    _refresh_cut_digest(value)
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))


def test_member_narrowing_cut_requires_native_lattice_and_exact_digest() -> None:
    value = _member()
    record = cast(dict[str, Any], cast(dict[str, Any], value["narrowing_cut"])["records"][0])
    record["label"]["lattice_version"] = "other"
    _refresh_cut_digest(value)
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))

    value = _member()
    cast(dict[str, Any], value["narrowing_cut"])["cut_digest"] = "0" * 64
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))


def test_worker_fence_is_the_exact_non_scheduler_native_inverse() -> None:
    from chiplog.capabilities.agent_loop.recovery_contracts import NonSchedulerFence

    value = _worker()
    decoded = decode_h1_delivery_evidence(_canonical(value))
    assert isinstance(decoded, H1DeliveryWorkerFenceV1)
    fence = cast(dict[str, str], value["fence"])
    native = NonSchedulerFence.model_validate(
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
    assert native.model_dump(mode="json") == {
        "kind": "NON_SCHEDULER_NOT_APPLICABLE",
        "lineage": {"kind": "NOT_APPLICABLE"},
        "physical_root": {"kind": "NOT_APPLICABLE"},
        "lease": {"kind": "NOT_APPLICABLE"},
        "clock_proof": {"kind": "NOT_APPLICABLE"},
        "run_id": "run",
        "run_head": "head",
        "worker_session_id": "worker",
        "runtime_generation": "generation",
    }


@pytest.mark.parametrize(
    "field",
    ("scheduler_id", "scheduler_generation", "scheduler_lease", "scheduler_lease_generation"),
)
def test_worker_fence_rejects_non_native_not_applicable_constants(field: str) -> None:
    value = _worker()
    cast(dict[str, str], value["fence"])[field] = "invented"
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))


def test_worker_fence_route_generation_is_identical_to_owner_generation() -> None:
    value = _worker()
    cast(dict[str, str], value["fence"])["runtime_generation"] = "other"
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(value))
