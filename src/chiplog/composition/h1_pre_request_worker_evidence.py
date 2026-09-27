"""Private E issuance for one live installed H1 worker fence.

The issuer owns no caller-provided worker fields.  It only converts the current
E3 and worker-owner replays into E's closed record while their shared authority
gate is held.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import NoReturn

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import NonSchedulerFence, NotApplicable
from chiplog.composition.h1_delivery_evidence_contracts import (
    H1DeliveryEvidenceLocatorV1,
    H1DeliveryWorkerFenceV1,
    decode_h1_delivery_evidence,
)
from chiplog.composition.h1_delivery_evidence_journal import (
    AuthenticatedH1DeliveryEvidenceRecord,
    H1DeliveryEvidenceJournal,
)
from chiplog.composition.h1_first_path_sources import H1CurrentFirstPathNativeCut
from chiplog.composition.h1_launch_enrollment import H1EvidenceMountEnrollmentV1
from chiplog.composition.h1_native_member_sources import (
    H1CurrentNativeMemberSourceCut,
    H1NativeMemberSources,
)
from chiplog.composition.h1_worker_evidence import (
    H1WorkerSourceCapability,
    _H1InstalledWorkerEvidenceOwner,
    _H1VerifiedWorkerCut,
)

_PROJECTION_DOMAIN = "chiplog.h1.evidence-exact-head.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


class H1PreRequestWorkerEvidenceReceipt:
    """Opaque, issuer-owned locator receipt; copying it grants no replay right."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 worker evidence receipts are issued only by their owner")

    def __copy__(self) -> NoReturn:
        raise TypeError("H1 worker evidence receipts cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("H1 worker evidence receipts cannot be copied")


@dataclass(frozen=True, slots=True)
class _IssuedWorkerReceipt:
    receipt: H1PreRequestWorkerEvidenceReceipt
    native_cap: H1CurrentNativeMemberSourceCut
    worker_cap: H1WorkerSourceCapability
    locator: H1DeliveryEvidenceLocatorV1
    raw: bytes


class H1PreRequestWorkerEvidence:
    """The sole private bridge from live installed worker facts to E storage."""

    __slots__ = ("_closed", "_gate", "_journal", "_native_sources", "_owner", "_receipts")

    def __init__(
        self,
        journal: H1DeliveryEvidenceJournal,
        native_sources: H1NativeMemberSources,
        worker_owner: _H1InstalledWorkerEvidenceOwner,
    ) -> None:
        if type(journal) is not H1DeliveryEvidenceJournal:
            raise TypeError("H1 worker evidence requires the canonical enrolled journal")
        if type(native_sources) is not H1NativeMemberSources:
            raise TypeError("H1 worker evidence requires the canonical native source owner")
        if type(worker_owner) is not _H1InstalledWorkerEvidenceOwner:
            raise TypeError("H1 worker evidence requires the canonical worker owner")
        gate = worker_owner._gate
        if (
            journal._mount is not worker_owner._mount
            or journal._journal.authority_gate is not gate
            or journal._mount.authority_gate is not gate
            or native_sources is not worker_owner._native_sources
            or native_sources._runtime is not worker_owner._runtime
            or native_sources._first_path._gate is not gate
        ):
            raise ValueError("H1 worker evidence dependencies are not one installed mount and gate")
        journal._mount.assert_current()
        self._journal = journal
        self._native_sources = native_sources
        self._owner = worker_owner
        self._gate = gate
        self._receipts: dict[int, _IssuedWorkerReceipt] = {}
        self._closed = False
        journal._bind_private_worker_issuer(self)

    def _issue_worker(
        self, native_cap: H1CurrentNativeMemberSourceCut, worker_cap: H1WorkerSourceCapability
    ) -> H1PreRequestWorkerEvidenceReceipt:
        if type(native_cap) is not H1CurrentNativeMemberSourceCut:
            raise TypeError("H1 worker evidence requires a native owner capability")
        if type(worker_cap) is not H1WorkerSourceCapability:
            raise TypeError("H1 worker evidence requires a worker owner capability")
        with self._gate.hold():
            if self._closed:
                raise ValueError("H1 worker evidence issuer is closed")
            first_native = self._native_sources.replay_current(native_cap)
            first_verified = self._owner._replay_for_pre_request(worker_cap, native_cap)
            self._require_verified_native(first_native, first_verified.run)
            # Replaying immediately before the single append rejects any stale
            # source/route without treating an inert verified DTO as authority.
            native = self._native_sources.replay_current(native_cap)
            verified = self._owner._replay_for_pre_request(worker_cap, native_cap)
            if native != first_native or verified != first_verified:
                raise ValueError("H1 worker source changed before evidence append")
            record = self._record_from_verified(native, verified)
            locator = self._journal._issue_from_bound_worker_owner(record, self)
            readback = self._readback(locator, record.canonical_bytes())
            self._native_sources.replay_current(native_cap)
            if self._owner._replay_for_pre_request(worker_cap, native_cap) != verified:
                raise ValueError("H1 worker source changed after evidence readback")
            receipt = object.__new__(H1PreRequestWorkerEvidenceReceipt)
            self._receipts[id(receipt)] = _IssuedWorkerReceipt(
                receipt, native_cap, worker_cap, locator, readback.raw_payload
            )
            return receipt

    def _replay_worker(
        self, receipt: H1PreRequestWorkerEvidenceReceipt
    ) -> tuple[H1DeliveryEvidenceLocatorV1, H1DeliveryWorkerFenceV1, ExactHead]:
        issued = (
            self._receipts.get(id(receipt))
            if type(receipt) is H1PreRequestWorkerEvidenceReceipt
            else None
        )
        if issued is None or issued.receipt is not receipt:
            raise ValueError("H1 worker evidence receipt is not issuer-owned")
        with self._gate.hold():
            if self._closed:
                raise ValueError("H1 worker evidence issuer is closed")
            self._native_sources.replay_current(issued.native_cap)
            self._owner._replay_for_pre_request(issued.worker_cap, issued.native_cap)
            readback = self._readback(issued.locator, issued.raw)
            if type(readback.record) is not H1DeliveryWorkerFenceV1:
                raise RuntimeError("H1 worker evidence receipt reopened another record type")
            return issued.locator, readback.record, self._project(readback.record)

    def _revoke_all(self) -> None:
        """Invalidate all private receipts before its worker owner is revoked."""
        with self._gate.hold():
            if self._closed:
                return
            self._receipts.clear()
            self._closed = True
            self._journal._unbind_private_worker_issuer(self)

    def _readback(
        self, locator: H1DeliveryEvidenceLocatorV1, raw: bytes
    ) -> AuthenticatedH1DeliveryEvidenceRecord:
        readback = self._journal.read_exact(locator)
        if (
            readback.journal_instance_id != self._journal._mount.journal_instance_id
            or readback.raw_payload != raw
            or readback.entry_id != locator.entry_id
            or hashlib.sha256(readback.raw_payload).hexdigest() != locator.payload_digest
        ):
            raise RuntimeError("H1 worker evidence authenticated readback differs")
        return readback

    def _record_from_verified(
        self, native: H1CurrentFirstPathNativeCut, verified: _H1VerifiedWorkerCut
    ) -> H1DeliveryWorkerFenceV1:
        run = verified.run
        self._require_verified_native(native, run)
        mount = self._journal._mount
        slot = mount._launch._slot
        enrollment = H1EvidenceMountEnrollmentV1.model_validate_json(mount._marker_raw)
        trust = slot._trust.verify()
        if (
            enrollment.deployment_id != slot.deployment_id
            or enrollment.database_id != slot.database_id
            or enrollment.tenant_id != slot.tenant_id
            or enrollment.journal_instance_id != mount.journal_instance_id
            or mount.tenant_id != enrollment.tenant_id
            or run.tenant != enrollment.tenant_id
            or trust is None
            or trust.tenant_id != enrollment.tenant_id
            or trust.database_instance_id != enrollment.database_id
            or trust.genesis_head != enrollment.database_genesis_digest
        ):
            raise ValueError("H1 worker evidence enrollment genesis is unavailable")
        if (
            mount.tenant_id != run.tenant
            or slot.tenant_id != run.tenant
            or verified.fence.run_id != run.run_id
            or verified.fence.run_head != run.head
            or verified.fence.worker_session_id != verified.worker_session_id
            or verified.fence.runtime_generation != verified.owner_route_generation
        ):
            raise ValueError("H1 worker owner facts differ from the enrolled source")
        inverse = NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=verified.worker_session_id,
            runtime_generation=verified.owner_route_generation,
        )
        if inverse.canonical_bytes() != verified.fence.canonical_bytes():
            raise ValueError("H1 worker fence has no exact native inverse")
        raw = _canonical(
            {
                "schema_id": "chiplog.execution.h1-worker-fence.v1",
                "deployment_id": slot.deployment_id,
                "database_id": slot.database_id,
                "database_genesis_digest": enrollment.database_genesis_digest,
                "tenant": run.tenant,
                "principal": run.principal,
                "runtime_instance_id": verified.runtime_instance_id,
                "owner_id": "agent_loop",
                "owner_route_generation": verified.owner_route_generation,
                "run": {
                    "identity": run.run_id,
                    "head": run.head,
                    "fingerprint": hashlib.sha256(run.canonical_bytes()).hexdigest(),
                },
                "worker_session_id": verified.worker_session_id,
                "fence": {
                    "kind": "NON_SCHEDULER",
                    "run_id": run.run_id,
                    "run_head": run.head,
                    "worker_session": verified.worker_session_id,
                    "runtime_generation": verified.owner_route_generation,
                    "scheduler_id": "NOT_APPLICABLE",
                    "scheduler_generation": "NOT_APPLICABLE",
                    "scheduler_lease": "NOT_APPLICABLE",
                    "scheduler_lease_generation": "NOT_APPLICABLE",
                },
            }
        )
        record = decode_h1_delivery_evidence(raw)
        if type(record) is not H1DeliveryWorkerFenceV1:
            raise RuntimeError("H1 worker evidence codec returned a foreign record")
        return record

    @staticmethod
    def _require_verified_native(
        native: H1CurrentFirstPathNativeCut, run: ExecutionRunRecord
    ) -> None:
        if not native.source.complete_ordered_run_lineage:
            raise ValueError("H1 worker native source has no prepared Run")
        prepared = native.source.complete_ordered_run_lineage[-1]
        if prepared != run:
            raise ValueError("H1 worker verified Run differs from native prepared Run")

    def _project(self, record: H1DeliveryWorkerFenceV1) -> ExactHead:
        value = record._value
        journal = [
            value["deployment_id"],
            value["database_id"],
            value["database_genesis_digest"],
            value["tenant"],
            "h1-delivery-evidence",
            self._journal._mount.journal_instance_id,
        ]
        worker_key = [
            value["principal"],
            value["runtime_instance_id"],
            value["owner_id"],
            value["owner_route_generation"],
            value["run"],
            value["worker_session_id"],
        ]
        identity = "h1-evidence:worker:" + hashlib.sha256(
            _canonical([_PROJECTION_DOMAIN, journal, "worker", worker_key])
        ).hexdigest()
        digest = hashlib.sha256(record.canonical_bytes()).hexdigest()
        return ExactHead(identity=identity, head="record:" + digest, fingerprint=digest)


__all__ = ["H1PreRequestWorkerEvidence", "H1PreRequestWorkerEvidenceReceipt"]
