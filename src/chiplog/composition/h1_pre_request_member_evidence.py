"""Private E issuance of the ordered H1 native-member evidence vector.

The P capability remains the sole authority for the accepted scope.  This
module only retains closed, authenticated claims while P and E3 both replay the
same current source cut under their shared gate.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import NoReturn

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.composition.h1_delivery_evidence_contracts import (
    H1DeliveryEvidenceLocatorV1,
    H1DeliveryMemberEvidenceV1,
    decode_h1_delivery_evidence,
)
from chiplog.composition.h1_delivery_evidence_journal import H1DeliveryEvidenceJournal
from chiplog.composition.h1_native_member_sources import (
    H1CurrentNativeMemberSourceCut,
    H1NativeMemberSourcePart,
    H1NativeMemberSources,
)
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
from chiplog.composition.h1_preseal_native_source import (
    H1PresealNativeSource,
    H1PresealNativeSourceCut,
)
from chiplog.composition.h1_runtime_preissuance_port import (
    _AcceptedH1CompletionScopeProjection,
    _H1RuntimePreissuancePort,
)

_PROJECTION_DOMAIN = "chiplog.h1.evidence-exact-head.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class H1PreRequestMemberEvidenceReceipt:
    """Opaque issuer-held receipt for an ordered vector of member locators."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 member evidence receipts are issued only by their owner")

    def __copy__(self) -> NoReturn:
        raise TypeError("H1 member evidence receipts cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        raise TypeError("H1 member evidence receipts cannot be copied")


@dataclass(frozen=True, slots=True)
class H1MemberEvidenceProjection:
    provenance: ExactHead
    disclosure: ExactHead
    narrowing: ExactHead


@dataclass(frozen=True, slots=True)
class _IssuedMemberReceipt:
    receipt: H1PreRequestMemberEvidenceReceipt
    native_cap: H1CurrentNativeMemberSourceCut
    scope_cap: object
    locators: tuple[H1DeliveryEvidenceLocatorV1, ...]
    raws: tuple[bytes, ...]


class H1PreRequestMemberEvidence:
    """The private P/E3-to-journal bridge for every live native occurrence."""

    __slots__ = ("_closed", "_gate", "_journal", "_native_sources", "_port", "_receipts")

    def __init__(
        self,
        journal: H1DeliveryEvidenceJournal,
        native_sources: H1NativeMemberSources,
        port: _H1RuntimePreissuancePort,
    ) -> None:
        if type(journal) is not H1DeliveryEvidenceJournal:
            raise TypeError("H1 member evidence requires the canonical enrolled journal")
        if type(native_sources) is not H1NativeMemberSources:
            raise TypeError("H1 member evidence requires the canonical native source owner")
        if type(port) is not _H1RuntimePreissuancePort:
            raise TypeError("H1 member evidence requires the canonical P owner")
        gate = port._gate
        runtime = port._runtime
        if (
            native_sources._runtime is not runtime
            or native_sources._first_path._gate is not gate
            or getattr(runtime, "_h1_native_member_sources", None) is not native_sources
            or getattr(runtime, "_h1_preissuance_registration_source_port", None) is not port
            or journal._journal.authority_gate is not gate
            or journal._mount.authority_gate is not gate
            or journal._mount._launch is not port._launch
        ):
            raise ValueError("H1 member evidence dependencies are not one installed mount and gate")
        journal._mount.assert_current()
        self._journal = journal
        self._native_sources = native_sources
        self._port = port
        self._gate = gate
        self._receipts: dict[int, _IssuedMemberReceipt] = {}
        self._closed = False
        journal._bind_private_member_issuer(self)

    def _issue_members(
        self, native_cap: H1CurrentNativeMemberSourceCut, accepted_scope_capability: object
    ) -> H1PreRequestMemberEvidenceReceipt:
        if type(native_cap) is not H1CurrentNativeMemberSourceCut:
            raise TypeError("H1 member evidence requires a native owner capability")
        with self._gate.hold():
            self._require_open()
            first_native, first_parts, first_scope = self._replay(
                native_cap, accepted_scope_capability
            )
            records = tuple(self._record(part, first_native, first_scope) for part in first_parts)
            # The replay immediately before each append preserves the source
            # fence even if a prior member retry is reopened from storage.
            locators: list[H1DeliveryEvidenceLocatorV1] = []
            raws: list[bytes] = []
            for part, record in zip(first_parts, records, strict=True):
                native, parts, scope = self._replay(native_cap, accepted_scope_capability)
                if native != first_native or parts != first_parts or scope != first_scope:
                    raise ValueError("H1 member source changed before evidence append")
                if self._record(part, native, scope).canonical_bytes() != record.canonical_bytes():
                    raise ValueError("H1 member record changed before evidence append")
                locator = self._journal._issue_from_bound_member_owner(record, self)
                readback = self._journal.read_exact(locator)
                if (
                    readback.journal_instance_id != self._journal._mount.journal_instance_id
                    or readback.raw_payload != record.canonical_bytes()
                    or type(readback.record) is not H1DeliveryMemberEvidenceV1
                ):
                    raise RuntimeError("H1 member evidence authenticated readback differs")
                locators.append(locator)
                raws.append(readback.raw_payload)
            native, parts, scope = self._replay(native_cap, accepted_scope_capability)
            if native != first_native or parts != first_parts or scope != first_scope:
                raise ValueError("H1 member source changed after evidence readback")
            receipt = object.__new__(H1PreRequestMemberEvidenceReceipt)
            self._receipts[id(receipt)] = _IssuedMemberReceipt(
                receipt, native_cap, accepted_scope_capability, tuple(locators), tuple(raws)
            )
            return receipt

    def _replay_members(
        self, receipt: H1PreRequestMemberEvidenceReceipt
    ) -> tuple[
        tuple[H1DeliveryEvidenceLocatorV1, H1DeliveryMemberEvidenceV1, H1MemberEvidenceProjection],
        ...,
    ]:
        issued = self._issued(receipt)
        with self._gate.hold():
            self._require_open()
            _native, parts, scope = self._replay(issued.native_cap, issued.scope_cap)
            if len(parts) != len(issued.locators):
                raise ValueError("H1 member receipt vector differs from current sources")
            result: list[
                tuple[
                    H1DeliveryEvidenceLocatorV1,
                    H1DeliveryMemberEvidenceV1,
                    H1MemberEvidenceProjection,
                ]
            ] = []
            for part, locator, raw in zip(parts, issued.locators, issued.raws, strict=True):
                readback = self._journal.read_exact(locator)
                if (
                    readback.journal_instance_id != self._journal._mount.journal_instance_id
                    or readback.raw_payload != raw
                    or type(readback.record) is not H1DeliveryMemberEvidenceV1
                ):
                    raise RuntimeError("H1 member receipt authenticated readback differs")
                expected = self._record(part, _native, scope)
                if expected.canonical_bytes() != raw:
                    raise ValueError("H1 member receipt record differs from current sources")
                result.append((locator, readback.record, self._project(readback.record)))
            return tuple(result)

    def _revoke_all(self) -> None:
        with self._gate.hold():
            if self._closed:
                return
            self._receipts.clear()
            self._closed = True
            self._journal._unbind_private_member_issuer(self)

    def _preseal_members(
        self,
        native_source: H1PresealNativeSource,
        native_cut: H1PresealNativeSourceCut,
        scope_cap: object,
    ) -> tuple[dict[str, object], ...]:
        """Project the E residuals before a response seal or E append exists.

        ``scope_cap`` remains opaque here.  The installed P owner must replay it
        against this exact native cut; accepting an endpoint or policy DTO at
        this seam would manufacture P authority from caller data.
        """
        if type(native_source) is not H1PresealNativeSource:
            raise TypeError("H1 preseal members require the installed native source owner")
        if type(native_cut) is not H1PresealNativeSourceCut:
            raise TypeError("H1 preseal members require an issuer-owned native source cut")
        if (
            native_source._runtime is not self._port._runtime
            or getattr(self._port._runtime, "_h1_preseal_native_source", None) is not native_source
        ):
            raise ValueError("H1 preseal native source belongs to another installed runtime")
        if getattr(self._port._runtime, "_h1_pre_request_member_evidence", None) is not self:
            raise ValueError("H1 preseal member owner is not the installed owner")
        replay_scope = getattr(self._port, "_replay_preseal_scope", None)
        if not callable(replay_scope):
            raise ValueError("H1 preseal P owner capability is unavailable")

        with self._gate.hold():
            self._require_open()
            occurrences = native_source.replay(native_cut)
            # This call is deliberately the only source of the endpoint.  Its
            # private P implementation authenticates the exact owner-issued cap.
            scope = replay_scope(scope_cap, native_cut)
            endpoint = scope.recipient.endpoint.identity
            if not isinstance(endpoint, str) or not endpoint:
                raise ValueError("H1 preseal P scope has no authenticated endpoint")
            preflight = native_cut._preflight
            prepare = preflight.prepare
            artifact_digest = preflight.captured_run.turns[0].attempts[0].manifest.artifact.digest()
            prepare_digest = hashlib.sha256(prepare.decision_bytes).hexdigest()
            rows: list[dict[str, object]] = []
            for index, occurrence in enumerate(occurrences):
                if occurrence.member_index != index:
                    raise ValueError("H1 preseal member vector is not complete and ordered")
                label = occurrence.original_label.model_dump(mode="json")
                if occurrence.original_label.value == "DENY_ALL" or (
                    occurrence.original_label.value == "ENDPOINT_RESTRICTED"
                    and endpoint not in occurrence.original_label.allowed_endpoints
                ):
                    raise H1PreissuanceSourceViolation(
                        "H1 preseal native label excludes accepted endpoint"
                    )
                if occurrence.source_kind == "WORKSPACE":
                    locator: dict[str, object] = {
                        "kind": "WORKSPACE_ISSUANCE",
                        "entry_id": prepare.issuance_ref.entry_id,
                        "payload_digest": prepare.issuance_ref.payload_digest,
                    }
                    rule = "WORKSPACE_ORIGINAL"
                elif occurrence.source_kind == "CONTEXT":
                    locator = {
                        "kind": "STARTED_RUN",
                        "run_id": prepare.started_run.run_id,
                        "run_head": prepare.started_run.head,
                    }
                    rule = "CONTEXT_JOIN"
                elif occurrence.source_kind in {"PROMPT", "SCHEMA"}:
                    locator = {
                        "kind": "PREPARE_ARTIFACT",
                        "prepare_entry_id": prepare.decision_id,
                        "prepare_payload_digest": prepare_digest,
                        "artifact_digest": artifact_digest,
                    }
                    rule = "PROMPT_JOIN" if occurrence.source_kind == "PROMPT" else "SCHEMA_PUBLIC"
                else:
                    raise ValueError("H1 preseal member source kind is unknown")
                rows.append(
                    {
                        "member_index": occurrence.member_index,
                        "member_digest": occurrence.member_digest,
                        "provenance": {
                            "original_head": occurrence.provenance_head,
                            "source_kind": occurrence.source_kind,
                            "source_locator": locator,
                        },
                        "disclosure": {
                            "original_head": occurrence.label_head,
                            "original_label": label,
                            "rule": rule,
                        },
                        "narrowing": {
                            "ordinal": 0,
                            "label": {
                                "lattice_version": "chiplog.disclosure.v1",
                                "value": "ENDPOINT_RESTRICTED",
                                "allowed_endpoints": [endpoint],
                            },
                        },
                    }
                )
            if not rows:
                raise ValueError("H1 preseal member vector is empty")
            return tuple(rows)

    def _issued(self, receipt: H1PreRequestMemberEvidenceReceipt) -> _IssuedMemberReceipt:
        issued = (
            self._receipts.get(id(receipt))
            if type(receipt) is H1PreRequestMemberEvidenceReceipt
            else None
        )
        if issued is None or issued.receipt is not receipt:
            raise ValueError("H1 member evidence receipt is not issuer-owned")
        return issued

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("H1 member evidence issuer is closed")

    def _replay(
        self, native_cap: H1CurrentNativeMemberSourceCut, scope_cap: object
    ) -> tuple[object, tuple[H1NativeMemberSourcePart, ...], _AcceptedH1CompletionScopeProjection]:
        native = self._native_sources.replay_current(native_cap)
        parts = self._native_sources.project_current(native_cap)
        scope = self._port._replay_completion_scope(scope_cap, native_cap)
        if not parts or any(
            part.occurrence.member_index != index for index, part in enumerate(parts)
        ):
            raise ValueError("H1 member source vector is not complete and ordered")
        return native, parts, scope

    def _record(
        self,
        part: H1NativeMemberSourcePart,
        native: object,
        scope: _AcceptedH1CompletionScopeProjection,
    ) -> H1DeliveryMemberEvidenceV1:
        del native
        occurrence = part.occurrence
        run = part.started_run_id
        endpoint = scope.recipient.endpoint.identity
        expected_locator_kind = {
            "WORKSPACE": "WORKSPACE_ISSUANCE",
            "CONTEXT": "STARTED_RUN",
            "PROMPT": "PREPARE_ARTIFACT",
            "SCHEMA": "PREPARE_ARTIFACT",
        }.get(occurrence.source_kind)
        if part.source_locator_kind != expected_locator_kind:
            raise ValueError("H1 member source locator kind differs")
        if occurrence.original_label.value == "DENY_ALL" or (
            occurrence.original_label.value == "ENDPOINT_RESTRICTED"
            and endpoint not in occurrence.original_label.allowed_endpoints
        ):
            raise H1PreissuanceSourceViolation("H1 native label excludes accepted endpoint")
        mount = self._journal._mount
        enrollment = json.loads(mount._marker_raw)
        member_key = [
            enrollment["deployment_id"],
            enrollment["database_id"],
            enrollment["database_genesis_digest"],
            mount.tenant_id,
            occurrence.turn_id.split("/turn/", 1)[0] if "/turn/" in occurrence.turn_id else run,
            run,
            occurrence.turn_id,
            occurrence.attempt_id,
            occurrence.manifest_digest,
            occurrence.member_index,
            occurrence.member_digest,
        ]
        # The native occurrence owns the principal through its selected captured
        # Run.  P's scope is independently bound to that same run by replay.
        principal = scope.scope.principal_id
        member_key[4] = principal
        workspace = part.workspace_issuance.model_dump(mode="json")
        selected_prepare = {
            "journal_role": "loop-decisions",
            "entry_id": part.selected_prepare_id,
            "payload_digest": part.selected_prepare_digest,
        }
        started = {
            "identity": part.started_run_id,
            "head": part.started_run_head,
            "fingerprint": part.started_run_fingerprint,
        }
        if occurrence.source_kind == "WORKSPACE":
            locator: dict[str, object] = {
                "kind": "WORKSPACE_ISSUANCE",
                "workspace_issuance": workspace,
            }
            rule = "WORKSPACE_ORIGINAL"
        elif occurrence.source_kind == "CONTEXT":
            locator = {
                "kind": "STARTED_RUN",
                "selected_prepare": selected_prepare,
                "started_run": started,
            }
            rule = "CONTEXT_JOIN"
        elif occurrence.source_kind in {"PROMPT", "SCHEMA"}:
            locator = {
                "kind": "PREPARE_ARTIFACT",
                "selected_prepare": selected_prepare,
                "artifact_digest": part.artifact_digest,
            }
            rule = "PROMPT_JOIN" if occurrence.source_kind == "PROMPT" else "SCHEMA_PUBLIC"
        else:
            raise ValueError("H1 member source kind is unknown")
        provenance = {
            "schema_id": "chiplog.execution.h1-member-provenance.v1",
            "member_key": member_key,
            "original_head": occurrence.provenance_head,
            "source_kind": occurrence.source_kind,
            "source_bytes_base64": _b64(occurrence.source_bytes),
            "source_locator": locator,
        }
        disclosure = {
            "schema_id": "chiplog.execution.h1-member-disclosure.v1",
            "member_key": member_key,
            "original_head": occurrence.label_head,
            "original_label": occurrence.original_label.model_dump(mode="json"),
            "provenance_fingerprint": _digest(provenance),
            "rule": rule,
            "workspace_issuance": workspace,
            "started_run": started,
        }
        narrowing_record = {
            "schema_id": "chiplog.execution.h1-member-narrowing.v1",
            "member_key": member_key,
            "ordinal": 0,
            "source_ref": scope.policy_ref.model_dump(mode="json"),
            "source_bytes_base64": _b64(scope.policy_bytes),
            "label": {
                "lattice_version": "chiplog.disclosure.v1",
                "value": "ENDPOINT_RESTRICTED",
                "allowed_endpoints": [endpoint],
            },
        }
        narrowing = {
            "schema_id": "chiplog.execution.h1-member-narrowing-cut.v1",
            "member_key": member_key,
            "scope_ref": scope.scope_ref.model_dump(mode="json"),
            "scope_bytes_base64": _b64(scope.scope_bytes),
            "custody_generation": str(scope.custody_entry_generation),
            "custody_digest": scope.custody_entry_digest,
            "policy_ref": scope.policy_ref.model_dump(mode="json"),
            "policy_bytes_base64": _b64(scope.policy_bytes),
            "source_signature_digest": scope.source_signature_digest,
            "records": [narrowing_record],
        }
        narrowing["cut_digest"] = _digest(narrowing)
        raw = _canonical(
            {
                "schema_id": "chiplog.execution.h1-member-evidence.v1",
                "deployment_id": enrollment["deployment_id"],
                "database_id": enrollment["database_id"],
                "database_genesis_digest": enrollment["database_genesis_digest"],
                "tenant": mount.tenant_id,
                "principal": principal,
                "run_id": run,
                "turn_id": occurrence.turn_id,
                "attempt_id": occurrence.attempt_id,
                "manifest_digest": occurrence.manifest_digest,
                "member_index": occurrence.member_index,
                "member_digest": occurrence.member_digest,
                "selected_prepare": selected_prepare,
                "workspace_issuance": workspace,
                "member_bytes_base64": _b64(occurrence.member_bytes),
                "provenance": provenance,
                "disclosure": disclosure,
                "narrowing_cut": narrowing,
            }
        )
        record = decode_h1_delivery_evidence(raw)
        if type(record) is not H1DeliveryMemberEvidenceV1:
            raise RuntimeError("H1 member codec returned a foreign record")
        return record

    def _project(self, record: H1DeliveryMemberEvidenceV1) -> H1MemberEvidenceProjection:
        value = record._value
        journal = [
            value["deployment_id"],
            value["database_id"],
            value["database_genesis_digest"],
            value["tenant"],
            "h1-delivery-evidence",
            self._journal._mount.journal_instance_id,
        ]
        key = value["provenance"]["member_key"]  # type: ignore[index]
        provenance = value["provenance"]
        disclosure = value["disclosure"]
        narrowing = value["narrowing_cut"]["records"][0]  # type: ignore[index]

        def head(
            role: str, original: str, payload: object, *, record_head: bool = False
        ) -> ExactHead:
            digest = _digest(payload)
            return ExactHead(
                identity="h1-evidence:member:"
                + role
                + ":"
                + _digest([_PROJECTION_DOMAIN, journal, role, key]),
                head="record:" + digest if record_head else original,
                fingerprint=digest,
            )

        return H1MemberEvidenceProjection(
            head("provenance", provenance["original_head"], provenance),  # type: ignore[index]
            head("disclosure", disclosure["original_head"], disclosure),  # type: ignore[index]
            head("narrowing", "", narrowing, record_head=True),
        )


__all__ = [
    "H1MemberEvidenceProjection",
    "H1PreRequestMemberEvidence",
    "H1PreRequestMemberEvidenceReceipt",
]
