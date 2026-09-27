"""Bounded storage for closed H1 delivery-evidence records.

`H1DeliveryEvidenceTestStorage` is intentionally a test-only provisioning seam.
Production opening accepts only R's nominal enrolled mount.  In particular this
module never turns a path and an ``AuthorityGate`` into production authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from chiplog.adapters.driven.deployment_trust._journal import IndependentTenantDecisionJournal
from chiplog.composition.h1_delivery_evidence_contracts import (
    H1DeliveryEvidence,
    H1DeliveryEvidenceLocatorV1,
    H1DeliveryMemberEvidenceV1,
    H1DeliverySelectionClosureV1,
    H1DeliveryWorkerFenceV1,
    decode_h1_delivery_evidence,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.h1_delivery_binding_contracts import H1DeliveryBinding

if TYPE_CHECKING:
    from chiplog.composition.h1_launch_enrollment import EnrolledH1EvidenceMount


def _sidecars(path: Path) -> tuple[Path, Path, Path]:
    return path, path.with_suffix(path.suffix + ".key"), path.with_suffix(path.suffix + ".head")


def _require_existing(path: Path) -> None:
    """Reject pre-primitive recovery before it can create body/key/head/lock."""
    for item in _sidecars(path):
        if item.is_symlink() or not item.is_file():
            raise RuntimeError("H1 evidence storage is not fully provisioned")


def _semantic_key(record: H1DeliveryEvidence) -> tuple[object, ...]:
    value = record._value
    enrollment = tuple(
        (value[key] if key != "tenant" else value.get("tenant", value.get("tenant_id")))
        for key in ("deployment_id", "database_id", "database_genesis_digest", "tenant")
    )
    if isinstance(record, H1DeliveryMemberEvidenceV1):
        return (
            record.schema_id,
            *enrollment,
            *(
                value[key]
                for key in (
                    "principal",
                    "run_id",
                    "turn_id",
                    "attempt_id",
                    "manifest_digest",
                    "member_index",
                    "member_digest",
                )
            ),
            value["narrowing_cut"]["cut_digest"],  # type: ignore[index]
        )
    if isinstance(record, H1DeliveryWorkerFenceV1):
        return (
            record.schema_id,
            *enrollment,
            *(
                value[key]
                for key in (
                    "principal",
                    "runtime_instance_id",
                    "owner_id",
                    "owner_route_generation",
                    "worker_session_id",
                    "run",
                )
            ),
        )
    return (record.schema_id, record.tenant, value.get("command_id"), value.get("request_digest"))


def _member_immutable_key(record: H1DeliveryMemberEvidenceV1) -> tuple[object, ...]:
    value = record._value
    return tuple(
        value[key]
        for key in (
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
    )


def _member_immutable_bytes(record: H1DeliveryMemberEvidenceV1) -> bytes:
    value = record._value
    return json.dumps(
        {"provenance": value["provenance"], "disclosure": value["disclosure"]},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class AuthenticatedH1DeliveryEvidenceRecord:
    raw_payload: bytes
    record: H1DeliveryEvidence
    entry_id: str
    journal_instance_id: str | None

    def canonical_bytes(self) -> bytes:
        return self.raw_payload


class _EvidenceStorage:
    def __init__(self, journal: IndependentTenantDecisionJournal, tenant: str) -> None:
        self._journal = journal
        self._tenant = tenant

    def _entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
        return self._journal.entries()

    def issue(self, record: H1DeliveryEvidence) -> H1DeliveryEvidenceLocatorV1:
        if record.tenant != self._tenant:
            raise ValueError("evidence tenant differs from storage tenant")
        raw = record.canonical_bytes()
        key = _semantic_key(record)
        entries = self._entries()
        for entry_id, _predecessor, payload in entries:
            existing = decode_h1_delivery_evidence(payload)
            if (
                isinstance(record, H1DeliveryMemberEvidenceV1)
                and isinstance(existing, H1DeliveryMemberEvidenceV1)
                and _member_immutable_key(existing) == _member_immutable_key(record)
                and _member_immutable_bytes(existing) != _member_immutable_bytes(record)
            ):
                raise ValueError("member immutable provenance or disclosure differs across cuts")
            if _semantic_key(existing) == key:
                if payload != raw:
                    raise ValueError("same evidence semantic key has different bytes")
                return H1DeliveryEvidenceLocatorV1(
                    self._tenant, entry_id, hashlib.sha256(raw).hexdigest()
                )
        predecessor = entries[-1][0] if entries else None
        entry_id = self._journal.append(raw, predecessor)
        # Re-read the complete authenticated chain before exposing a locator.
        authenticated = self._entries()
        if not any(
            candidate == entry_id and payload == raw
            for candidate, _previous, payload in authenticated
        ):
            raise RuntimeError("evidence append did not survive authenticated readback")
        return H1DeliveryEvidenceLocatorV1(self._tenant, entry_id, hashlib.sha256(raw).hexdigest())

    def read_exact(
        self, locator: H1DeliveryEvidenceLocatorV1
    ) -> AuthenticatedH1DeliveryEvidenceRecord:
        if locator.tenant != self._tenant:
            raise ValueError("evidence locator tenant differs")
        matches = [
            (entry_id, payload)
            for entry_id, _previous, payload in self._entries()
            if entry_id == locator.entry_id
        ]
        if len(matches) != 1:
            raise ValueError("evidence locator is absent or ambiguous")
        entry_id, raw = matches[0]
        if hashlib.sha256(raw).hexdigest() != locator.payload_digest:
            raise ValueError("evidence locator payload digest differs")
        record = decode_h1_delivery_evidence(raw)
        if record.tenant != self._tenant:
            raise ValueError("evidence payload tenant differs")
        return AuthenticatedH1DeliveryEvidenceRecord(raw, record, entry_id, None)


class H1DeliveryEvidenceTestStorage(_EvidenceStorage):
    """Explicit low-level test storage; it cannot be used as an enrolled mount."""

    @classmethod
    def create(cls, path: Path, gate: AuthorityGate, tenant: str) -> H1DeliveryEvidenceTestStorage:
        if type(gate) is not AuthorityGate or not isinstance(tenant, str) or not tenant:
            raise TypeError("test storage needs an exact gate and tenant")
        return cls(
            IndependentTenantDecisionJournal.for_authority_bundle(path, authority_gate=gate), tenant
        )

    @classmethod
    def reopen(cls, path: Path, gate: AuthorityGate, tenant: str) -> H1DeliveryEvidenceTestStorage:
        _require_existing(path)
        return cls.create(path, gate, tenant)


class H1DeliveryEvidenceJournal(_EvidenceStorage):
    """Production reader opened only from R's nominal enrolled mount."""

    def __init__(
        self, journal: IndependentTenantDecisionJournal, mount: EnrolledH1EvidenceMount
    ) -> None:
        self._mount = mount
        self._closed = False
        self._member_issuer: object | None = None
        self._root_issuer: object | None = None
        self._root_installation: object | None = None
        self._worker_issuer: object | None = None
        super().__init__(journal, mount.tenant_id)

    def _entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
        if self._closed:
            raise RuntimeError("H1 evidence reader is closed")
        self._mount.assert_current()
        return super()._entries()

    def read_exact(
        self, locator: H1DeliveryEvidenceLocatorV1
    ) -> AuthenticatedH1DeliveryEvidenceRecord:
        result = super().read_exact(locator)
        self._mount.assert_current()
        return AuthenticatedH1DeliveryEvidenceRecord(
            result.raw_payload,
            result.record,
            result.entry_id,
            self._mount.journal_instance_id,
        )

    def close(self) -> None:
        """Close retained existing-only descriptors; repeated close is harmless."""
        if self._closed:
            return
        self._closed = True
        self._member_issuer = None
        self._root_issuer = None
        self._root_installation = None
        self._worker_issuer = None
        self._journal.close()

    @classmethod
    def open_enrolled(cls, mount: object) -> H1DeliveryEvidenceJournal:
        # Runtime import keeps R's launch module independent from this module.
        if (
            type(mount).__module__ != "chiplog.composition.h1_launch_enrollment"
            or type(mount).__name__ != "EnrolledH1EvidenceMount"
        ):
            raise TypeError("H1 evidence requires the nominal enrolled mount")
        from chiplog.composition.h1_launch_enrollment import EnrolledH1EvidenceMount

        if type(mount) is not EnrolledH1EvidenceMount:
            raise TypeError("H1 evidence requires the nominal enrolled mount")
        mount.assert_current()
        journal = mount._open_existing_evidence_journal()
        if (
            type(journal) is not IndependentTenantDecisionJournal
            or journal.authority_gate is not mount.authority_gate
        ):
            raise RuntimeError("enrolled evidence mount returned an unbound journal")
        mount.assert_current()
        return cls(journal, mount)

    def issue(self, record: H1DeliveryEvidence) -> H1DeliveryEvidenceLocatorV1:
        raise RuntimeError("production evidence issuance awaits private owner capability")

    def issue_member(self, record: H1DeliveryMemberEvidenceV1) -> H1DeliveryEvidenceLocatorV1:
        if type(record) is not H1DeliveryMemberEvidenceV1:
            raise TypeError("member issuer accepts only member evidence")
        return self.issue(record)

    def issue_worker(self, record: H1DeliveryWorkerFenceV1) -> H1DeliveryEvidenceLocatorV1:
        if type(record) is not H1DeliveryWorkerFenceV1:
            raise TypeError("worker issuer accepts only worker evidence")
        return self.issue(record)

    def _bind_private_worker_issuer(self, issuer: object) -> None:
        """Install the one in-process issuer identity for this exact mounted reader."""
        # Runtime import avoids the reciprocal module import at definition time.
        from chiplog.composition.h1_pre_request_worker_evidence import H1PreRequestWorkerEvidence

        if type(issuer) is not H1PreRequestWorkerEvidence:
            raise TypeError("H1 evidence worker issuer is not canonical")
        if self._worker_issuer is not None:
            raise RuntimeError("H1 evidence worker issuer is already bound")
        self._mount.assert_current()
        self._worker_issuer = issuer

    def _bind_private_member_issuer(self, issuer: object) -> None:
        """Retain the one installed issuer identity for member evidence."""
        from chiplog.composition.h1_pre_request_member_evidence import H1PreRequestMemberEvidence

        if type(issuer) is not H1PreRequestMemberEvidence:
            raise TypeError("H1 evidence member issuer is not canonical")
        if self._member_issuer is not None:
            raise RuntimeError("H1 evidence member issuer is already bound")
        self._mount.assert_current()
        self._member_issuer = issuer

    def _bind_private_root_issuer(self, issuer: object, installation: object) -> None:
        """Bind only the live issuer installed with this runtime and writer."""
        from chiplog.composition.common_cli_execution_runtime import _H1LiveCompletionMount
        from chiplog.composition.h1_live_publication_authority import H1LivePublicationAuthority

        if self._closed:
            raise RuntimeError("H1 evidence root issuer reader is closed")
        if type(issuer) is not H1LivePublicationAuthority:
            raise TypeError("H1 evidence root issuer is not canonical")
        if type(installation) is not _H1LiveCompletionMount:
            raise TypeError("H1 evidence root installation is not canonical")
        if self._root_issuer is not None:
            raise RuntimeError("H1 evidence root issuer is already bound")
        self._mount.assert_current()
        gate = self._journal.authority_gate
        if gate is None:
            raise RuntimeError("H1 evidence root issuer has no authority gate")
        gate.require_held()
        installation._assert_root_binding(self, issuer)
        # The mount check can inspect the runtime/store graph.  Recheck the
        # enrolled role immediately before retaining that graph's identities.
        self._mount.assert_current()
        installation._assert_root_binding(self, issuer)
        self._root_issuer = issuer
        self._root_installation = installation

    def _issue_from_bound_member_owner(
        self, record: H1DeliveryMemberEvidenceV1, issuer: object
    ) -> H1DeliveryEvidenceLocatorV1:
        """Append a member record only for the exact gate-held member issuer."""
        if type(record) is not H1DeliveryMemberEvidenceV1:
            raise TypeError("private member append accepts only member evidence")
        if issuer is not self._member_issuer:
            raise ValueError("private member append issuer differs")
        self._mount.assert_current()
        gate = self._journal.authority_gate
        if gate is None:
            raise RuntimeError("private member append has no authority gate")
        gate.require_held()
        locator = super().issue(record)
        self._mount.assert_current()
        return locator

    def _unbind_private_member_issuer(self, issuer: object) -> None:
        """Release only the exact installed member issuer identity."""
        if issuer is not self._member_issuer:
            raise ValueError("private member unbind issuer differs")
        self._member_issuer = None

    def _issue_from_bound_root_owner(
        self, record: H1DeliverySelectionClosureV1, issuer: object
    ) -> H1DeliveryEvidenceLocatorV1:
        """Append a selected root only for the exact gate-held live issuer."""
        if type(record) is not H1DeliverySelectionClosureV1:
            raise TypeError("private root append accepts only selection closure evidence")
        if issuer is not self._root_issuer:
            raise ValueError("private root append issuer differs")
        installation = self._root_installation
        if installation is None:
            raise RuntimeError("private root append installation is absent")
        self._mount.assert_current()
        gate = self._journal.authority_gate
        if gate is None:
            raise RuntimeError("private root append has no authority gate")
        gate.require_held()
        installation._assert_root_binding(self, issuer)
        locator = super().issue(record)
        self._mount.assert_current()
        installation._assert_root_binding(self, issuer)
        return locator

    def _unbind_private_root_issuer(self, issuer: object) -> None:
        """Release only the exact live issuer bound to this mounted journal."""
        if issuer is not self._root_issuer:
            raise ValueError("private root unbind issuer differs")
        gate = self._journal.authority_gate
        if gate is None:
            raise RuntimeError("private root unbind has no authority gate")
        gate.require_held()
        self._root_issuer = None
        self._root_installation = None

    def _issue_from_bound_worker_owner(
        self, record: H1DeliveryWorkerFenceV1, issuer: object
    ) -> H1DeliveryEvidenceLocatorV1:
        """Append only while the exact installed E worker issuer holds its gate."""
        if type(record) is not H1DeliveryWorkerFenceV1:
            raise TypeError("private worker append accepts only worker evidence")
        if issuer is not self._worker_issuer:
            raise ValueError("private worker append issuer differs")
        self._mount.assert_current()
        gate = self._journal.authority_gate
        if gate is None:
            raise RuntimeError("private worker append has no authority gate")
        gate.require_held()
        locator = super().issue(record)
        self._mount.assert_current()
        return locator

    def _unbind_private_worker_issuer(self, issuer: object) -> None:
        """Release only the exact issuer that this mounted journal retained."""
        if issuer is not self._worker_issuer:
            raise ValueError("private worker unbind issuer differs")
        self._worker_issuer = None

    def read_closure(self, binding: object) -> AuthenticatedH1DeliveryEvidenceRecord:
        """Read one mounted, authenticated root matching its closed decision binding."""
        if type(binding) is not H1DeliveryBinding:
            raise TypeError("selected H1 delivery binding is not canonical")
        self._mount.assert_current()
        if binding.journal_instance_id != self._mount.journal_instance_id:
            raise ValueError("selected H1 delivery binding journal instance differs")
        result = self.read_exact(
            H1DeliveryEvidenceLocatorV1(
                binding.tenant_id,
                binding.closure_entry_id,
                binding.closure_payload_digest,
            )
        )
        if type(result.record) is not H1DeliverySelectionClosureV1:
            raise ValueError("selected H1 delivery binding does not locate a selection closure")
        value = result.record._value
        if (
            result.journal_instance_id != binding.journal_instance_id
            or result.entry_id != binding.closure_entry_id
            or hashlib.sha256(result.raw_payload).hexdigest() != binding.closure_payload_digest
            or result.record.schema_id != binding.closure_schema_id
            or value.get("deployment_id") != binding.deployment_id
            or value.get("database_id") != binding.database_id
            or value.get("database_genesis_digest") != binding.database_genesis_digest
            or value.get("tenant_id") != binding.tenant_id
            or value.get("journal_role") != binding.journal_role
            or value.get("journal_instance_id") != binding.journal_instance_id
            or value.get("command_id") != binding.command_id
            or value.get("command_fingerprint") != binding.command_fingerprint
            or value.get("request_digest") != binding.request_digest
        ):
            raise ValueError("selected H1 delivery binding differs from authenticated closure")
        return result
