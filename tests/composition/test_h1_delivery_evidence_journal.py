import base64
import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.composition.h1_delivery_evidence_contracts import (
    H1DeliveryEvidenceLocatorV1,
    H1DeliverySelectionClosureV1,
    decode_h1_delivery_evidence,
)
from chiplog.composition.h1_delivery_evidence_journal import (
    H1DeliveryEvidenceJournal,
    H1DeliveryEvidenceTestStorage,
)
from chiplog.composition.h1_live_publication_authority import H1LivePublicationAuthority
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.h1_delivery_binding_contracts import H1DeliveryBinding
from tests.support.h1_delivery_evidence import canonical as _canonical
from tests.support.h1_delivery_evidence import member as _raw_member
from tests.support.h1_delivery_evidence import worker as _worker


def _refresh_cut_digest(value: dict[str, object]) -> None:
    cut = cast(dict[str, Any], value["narrowing_cut"])
    unsigned = dict(cut)
    unsigned.pop("cut_digest")
    cut["cut_digest"] = hashlib.sha256(_canonical(unsigned)).hexdigest()


def _refresh_member_key(value: dict[str, object]) -> None:
    key = [
        value[field]
        for field in (
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
        )
    ]
    provenance = cast(dict[str, Any], value["provenance"])
    disclosure = cast(dict[str, Any], value["disclosure"])
    cut = cast(dict[str, Any], value["narrowing_cut"])
    provenance["member_key"] = key
    disclosure["member_key"] = key
    disclosure["provenance_fingerprint"] = hashlib.sha256(_canonical(provenance)).hexdigest()
    cut["member_key"] = key
    for record in cast(list[dict[str, Any]], cut["records"]):
        record["member_key"] = key
    _refresh_cut_digest(value)


def _member() -> dict[str, object]:
    value = _raw_member()
    value["member_digest"] = hashlib.sha256(
        base64.b64decode(cast(str, value["member_bytes_base64"]), validate=True)
    ).hexdigest()
    cut = cast(dict[str, Any], value["narrowing_cut"])
    records = cast(list[dict[str, Any]], cut["records"])
    records[0]["label"]["lattice_version"] = "chiplog.disclosure.v1"
    _refresh_member_key(value)
    return value


def _open(path: Path, tenant: str = "tenant") -> H1DeliveryEvidenceTestStorage:
    return H1DeliveryEvidenceTestStorage.create(path, AuthorityGate(path), tenant)


def _root_closure(*, journal_instance_id: str) -> H1DeliverySelectionClosureV1:
    request = b"root-request"
    value = {
        "schema_id": "chiplog.execution.h1-delivery-selection-closure.v1",
        "deployment_id": "deployment",
        "database_id": "database",
        "database_genesis_digest": "a" * 64,
        "tenant_id": "tenant",
        "principal_id": "principal",
        "journal_role": "h1-delivery-evidence",
        "journal_instance_id": journal_instance_id,
        "command_id": "command",
        "command_fingerprint": "b" * 64,
        "request_digest": hashlib.sha256(request).hexdigest(),
        "predecessor_commitment": "c" * 64,
        "expected_tenant_frontier": 0,
        "request_bytes_base64": "cm9vdC1yZXF1ZXN0",
        "manifest": {
            "subject_id": "subject",
            "revision": {"kind": "PRESENT", "head": "head", "fingerprint": "d" * 64},
        },
        "original": {},
        "members": [],
        "worker": {},
        "observation_bytes_base64": "b2JzZXJ2YXRpb24=",
        "accepted_sources": {},
    }
    return H1DeliverySelectionClosureV1(_canonical(value), value)


def _binding(
    locator: H1DeliveryEvidenceLocatorV1, *, journal_instance_id: str
) -> H1DeliveryBinding:
    return H1DeliveryBinding(
        deployment_id="deployment",
        database_id="database",
        database_genesis_digest="a" * 64,
        tenant_id="tenant",
        journal_instance_id=journal_instance_id,
        closure_entry_id=locator.entry_id,
        closure_payload_digest=locator.payload_digest,
        command_id="command",
        command_fingerprint="b" * 64,
        request_digest=hashlib.sha256(b"root-request").hexdigest(),
    )


def test_issue_readback_retry_and_reopen_are_exact(tmp_path: Path) -> None:
    path = tmp_path / "journal"
    record = decode_h1_delivery_evidence(_canonical(_member()))
    journal = _open(path)
    locator = journal.issue(record)
    assert journal.read_exact(locator).canonical_bytes() == record.canonical_bytes()
    assert journal.issue(record) == locator
    reopened = H1DeliveryEvidenceTestStorage.reopen(path, AuthorityGate(path), "tenant")
    assert reopened.issue(record) == locator
    with pytest.raises(ValueError, match="member digest differs"):
        reopened.issue(
            decode_h1_delivery_evidence(_canonical(_member() | {"member_bytes_base64": "b3RoZXI="}))
        )


def test_test_storage_readback_has_no_enrolled_instance_id(tmp_path: Path) -> None:
    journal = _open(tmp_path / "journal")
    locator = journal.issue(decode_h1_delivery_evidence(_canonical(_member())))
    assert journal.read_exact(locator).journal_instance_id is None


def test_production_reader_readback_binds_mounted_instance_id(tmp_path: Path) -> None:
    storage = _open(tmp_path / "journal")
    locator = storage.issue(decode_h1_delivery_evidence(_canonical(_member())))

    class _MountedTestSeam:
        tenant_id = "tenant"
        journal_instance_id = "h1-delivery-evidence:mounted"

        def assert_current(self) -> None:
            return None

    reader = H1DeliveryEvidenceJournal(storage._journal, _MountedTestSeam())  # type: ignore[arg-type]
    try:
        assert reader.read_exact(locator).journal_instance_id == "h1-delivery-evidence:mounted"
    finally:
        reader.close()


def test_private_root_issuer_rejects_an_uninstalled_exact_authority(tmp_path: Path) -> None:
    """Only R's installed mount can bind a root issuer to a production reader."""
    path = tmp_path / "journal"
    gate = AuthorityGate(path)
    storage = H1DeliveryEvidenceTestStorage.create(path, gate, "tenant")

    class _MountedTestSeam:
        tenant_id = "tenant"
        journal_instance_id = "h1-delivery-evidence:mounted"

        def assert_current(self) -> None:
            return None

    reader = H1DeliveryEvidenceJournal(storage._journal, _MountedTestSeam())  # type: ignore[arg-type]
    issuer = H1LivePublicationAuthority()
    root = _root_closure(journal_instance_id="h1-delivery-evidence:mounted")
    try:
        with pytest.raises(RuntimeError, match="production evidence issuance"):
            reader.issue(root)

        with gate.hold():
            with pytest.raises(TypeError, match="installation is not canonical"):
                reader._bind_private_root_issuer(issuer, object())
        assert reader._root_issuer is None
        assert reader._root_installation is None
        with pytest.raises(ValueError, match="issuer differs"):
            reader._issue_from_bound_root_owner(root, issuer)
    finally:
        reader.close()


def test_worker_new_run_or_generation_is_a_distinct_semantic_key(tmp_path: Path) -> None:
    journal = _open(tmp_path / "journal")
    first = _worker()
    first_locator = journal.issue(decode_h1_delivery_evidence(_canonical(first)))
    successor = _worker()
    successor["owner_route_generation"] = "generation-2"
    successor["run"] = {"identity": "run-2", "head": "head-2", "fingerprint": "c" * 64}
    successor["fence"] = successor["fence"] | {
        "run_id": "run-2",
        "run_head": "head-2",
        "runtime_generation": "generation-2",
    }  # type: ignore[operator]
    assert journal.issue(decode_h1_delivery_evidence(_canonical(successor))) != first_locator


def test_member_changed_narrowing_is_a_distinct_semantic_key(tmp_path: Path) -> None:
    journal = _open(tmp_path / "journal")
    first = journal.issue(decode_h1_delivery_evidence(_canonical(_member())))
    changed = _member()
    cut = cast(dict[str, Any], changed["narrowing_cut"])
    cut["source_signature_digest"] = "9" * 64
    _refresh_cut_digest(changed)
    assert journal.issue(decode_h1_delivery_evidence(_canonical(changed))) != first


def test_member_immutable_provenance_or_disclosure_drift_rejects_across_cuts(
    tmp_path: Path,
) -> None:
    journal = _open(tmp_path / "journal")
    journal.issue(decode_h1_delivery_evidence(_canonical(_member())))
    changed = _member()
    provenance = cast(dict[str, Any], changed["provenance"])
    provenance["original_head"] = "other-provenance"
    disclosure = cast(dict[str, Any], changed["disclosure"])
    disclosure["provenance_fingerprint"] = hashlib.sha256(_canonical(provenance)).hexdigest()
    cut = cast(dict[str, Any], changed["narrowing_cut"])
    cut["source_signature_digest"] = "9" * 64
    _refresh_cut_digest(changed)
    with pytest.raises(ValueError, match="immutable"):
        journal.issue(decode_h1_delivery_evidence(_canonical(changed)))


@pytest.mark.parametrize("field", ("deployment_id", "database_id", "database_genesis_digest"))
def test_member_enrollment_boundary_j_is_part_of_the_semantic_key(
    tmp_path: Path, field: str
) -> None:
    journal = _open(tmp_path / "journal")
    first = journal.issue(decode_h1_delivery_evidence(_canonical(_member())))
    changed = _member()
    changed[field] = "b" * 64 if field == "database_genesis_digest" else f"other-{field}"
    _refresh_member_key(changed)
    assert journal.issue(decode_h1_delivery_evidence(_canonical(changed))) != first


def test_worker_owner_and_enrollment_boundary_are_part_of_semantic_key(tmp_path: Path) -> None:
    journal = _open(tmp_path / "journal")
    first = journal.issue(decode_h1_delivery_evidence(_canonical(_worker())))
    different_owner = _worker()
    different_owner["owner_id"] = "other-owner"
    with pytest.raises(ValueError):
        decode_h1_delivery_evidence(_canonical(different_owner))

    different_boundary = _worker()
    different_boundary["database_id"] = "other-database"
    assert journal.issue(decode_h1_delivery_evidence(_canonical(different_boundary))) != first


def test_retry_locator_still_reads_exact_bytes_after_later_valid_append(tmp_path: Path) -> None:
    journal = _open(tmp_path / "journal")
    member = decode_h1_delivery_evidence(_canonical(_member()))
    locator = journal.issue(member)
    journal.issue(decode_h1_delivery_evidence(_canonical(_worker())))
    assert journal.issue(member) == locator
    assert journal.read_exact(locator).canonical_bytes() == member.canonical_bytes()


def test_read_exact_rejects_each_foreign_locator_component_and_corruption(tmp_path: Path) -> None:
    path = tmp_path / "journal"
    journal = _open(path)
    record = decode_h1_delivery_evidence(_canonical(_worker()))
    locator = journal.issue(record)
    for foreign in (
        H1DeliveryEvidenceLocatorV1(
            tenant="other", entry_id=locator.entry_id, payload_digest=locator.payload_digest
        ),
        H1DeliveryEvidenceLocatorV1(
            tenant="tenant", entry_id="0" * 64, payload_digest=locator.payload_digest
        ),
        H1DeliveryEvidenceLocatorV1(
            tenant="tenant", entry_id=locator.entry_id, payload_digest="0" * 64
        ),
    ):
        with pytest.raises(ValueError):
            journal.read_exact(foreign)
    path.write_bytes(b"corrupt")
    with pytest.raises((RuntimeError, ValueError)):
        journal.read_exact(locator)


@pytest.mark.parametrize("missing", ("body", "key", "head"))
def test_recovery_missing_body_key_or_head_never_creates_files(
    tmp_path: Path, missing: str
) -> None:
    path = tmp_path / "journal"
    _open(path)
    target = {"body": path, "key": path.with_suffix(".key"), "head": path.with_suffix(".head")}[
        missing
    ]
    target.unlink()
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    with pytest.raises(RuntimeError):
        H1DeliveryEvidenceTestStorage.reopen(path, AuthorityGate(path), "tenant")
    assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == before


def test_production_open_rejects_unmounted_object_without_creating_files(tmp_path: Path) -> None:
    before = tuple(tmp_path.iterdir())
    with pytest.raises(TypeError, match="nominal enrolled mount"):
        H1DeliveryEvidenceJournal.open_enrolled(object())
    assert tuple(tmp_path.iterdir()) == before
