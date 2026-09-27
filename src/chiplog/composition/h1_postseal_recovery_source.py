"""Fail-closed immutable H1 V2 source for a post-seal recovery root.

The recovery journal records a root; it is never evidence for that root.  This
adapter rereads the installed runtime's selected physical V2 seal while holding
the same authority gate as the enrolled recovery mount, then derives the root
identity from those bytes.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import (
    DriveInputRequestV1,
    DriverCommandIdentityV1,
)
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.h1_launch_enrollment import EnrolledH1RecoveryMount
from chiplog.composition.h1_postseal_recovery import H1PostSealRecoveryRootV1
from chiplog.composition.r14_execution_complete_seal_records import (
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
)
from chiplog.composition.r14_execution_inbox_records import RetainedInboxExecutionInitialization

_SOURCE_SCHEMA = "chiplog.h1.postseal-recovery-source.v1"
_PUBLICATION_SCHEMA = "chiplog.h1.postseal-recovery-publication.v1"
_OPERATION_KIND = "agent_loop.h1-postseal-recovery-root.v1"
_SOURCE_DOMAIN = b"chiplog.h1.postseal-recovery-source.v1\0"
_PUBLICATION_ID_DOMAIN = b"chiplog.h1.postseal-recovery-publication-id.v1\0"
_PUBLICATION_REQUEST_DOMAIN = b"chiplog.h1.postseal-recovery-publication-request.v1\0"


class H1PostSealRecoverySourceError(ValueError):
    """The selected installed V2 source cannot authenticate a recovery root."""


class H1PostSealRecoveryHistoricalSourceUnavailable(H1PostSealRecoverySourceError):
    """Installed H1 V2 has no immutable historical selected-source reader."""


@dataclass(frozen=True, slots=True)
class H1PostSealRecoveryPublicationIdentity:
    command_id: str
    command_fingerprint: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _selected_decision_canonical(value: object) -> bytes:
    """Match the installed loop-journal wire canonicalization exactly."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + _canonical(value)).hexdigest()


def _base64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _strict_object(raw: bytes) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise H1PostSealRecoverySourceError("selected decision has duplicate JSON key")
            result[key] = value
        return result

    try:
        decoded = json.loads(raw, object_pairs_hook=unique)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise H1PostSealRecoverySourceError("selected decision is invalid") from error
    if not isinstance(decoded, dict) or _selected_decision_canonical(decoded) != raw:
        raise H1PostSealRecoverySourceError("selected decision is noncanonical")
    return decoded


def source_commitment_for_preimage(preimage: dict[str, object]) -> str:
    """Hash exactly the frozen immutable source preimage.

    This is an encoding helper, not an authority constructor.  The only public
    root constructor below obtains this mapping from the installed runtime.
    """
    if not isinstance(preimage, dict) or preimage.get("schema_id") != _SOURCE_SCHEMA:
        raise H1PostSealRecoverySourceError("recovery source preimage schema differs")
    return _digest(_SOURCE_DOMAIN, preimage)


def publication_identity_for_source(
    *,
    tenant_id: str,
    database_id: str,
    database_identity: tuple[str, int, int],
    journal_instance_id: str,
    source_commitment: str,
) -> H1PostSealRecoveryPublicationIdentity:
    """Derive the acyclic logical publication intent from immutable source facts."""
    identity = {
        "schema_id": _PUBLICATION_SCHEMA,
        "operation_kind": _OPERATION_KIND,
        "tenant_id": tenant_id,
        "database_id": database_id,
        "database_identity": list(database_identity),
        "journal_instance_id": journal_instance_id,
        "source_commitment": source_commitment,
    }
    command_id = "h1-recovery-root:" + _digest(_PUBLICATION_ID_DOMAIN, identity)
    fingerprint = _digest(
        _PUBLICATION_REQUEST_DOMAIN,
        {"identity": identity, "publication_command_id": command_id},
    )
    return H1PostSealRecoveryPublicationIdentity(command_id, fingerprint)


class H1PostSealRecoveryRootSource:
    """Read one current installed H1 V2 seal and derive its immutable root."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("recovery root source requires the canonical installed runtime")
        mount = getattr(runtime, "_h1_recovery_mount", None)
        if type(mount) is not EnrolledH1RecoveryMount:
            raise H1PostSealRecoverySourceError("recovery root source requires an enrolled mount")
        gate = runtime._authority_gate()
        if mount.authority_gate is not gate:
            raise H1PostSealRecoverySourceError("recovery mount gate differs from runtime gate")
        self._runtime = runtime
        self._mount = mount
        self._gate = gate

    def derive_immediate(
        self,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1PostSealRecoveryRootV1:
        """Derive only from the still-current physical H1 V2 selected cut."""
        if type(original_identity) is not DriverCommandIdentityV1:
            raise TypeError("recovery root source requires the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise H1PostSealRecoverySourceError("original driver fingerprint is invalid")
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError("recovery root source requires an exact selected response-seal locator")
        try:
            with self._gate.hold():
                self._mount.assert_current()
                self._runtime._check_database_identity()
                native_reader = H1FirstPathSources(self._runtime)
                capture = native_reader.capture_current(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                native = native_reader.replay_current_native_cut(capture)
                root = self._root_from_native(
                    native=native,
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                self._runtime._check_database_identity()
                self._mount.assert_current()
                return root
        except H1PostSealRecoverySourceError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1PostSealRecoverySourceError(
                "installed selected H1 V2 source differs"
            ) from error

    def derive_on_restart(
        self,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1PostSealRecoveryRootV1:
        """Recompute a sealed root from the bounded historical V2 native prefix."""
        from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource

        if type(original_identity) is not DriverCommandIdentityV1:
            raise TypeError("recovery root source requires the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise H1PostSealRecoverySourceError("original driver fingerprint is invalid")
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError("recovery root source requires an exact selected response-seal locator")
        try:
            with self._gate.hold():
                self._mount.assert_current()
                self._runtime._check_database_identity()
                native = H1V2RecoveryNativeSource(self._runtime).select(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                root = self._root_from_native(
                    native=native,
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                self._runtime._check_database_identity()
                self._mount.assert_current()
                return root
        except H1PostSealRecoverySourceError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1PostSealRecoverySourceError(
                "installed historical H1 V2 source differs"
            ) from error

    def _root_from_native(
        self,
        *,
        native: Any,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1PostSealRecoveryRootV1:
        source = native.source
        self._require_exact_v2(native.seal.raw_bytes)
        if source.selected_response_seal != selected_seal:
            raise H1PostSealRecoverySourceError(
                "selected seal locator differs from selected source"
            )
        if (
            native.seal.decision_id == ""
            or hashlib.sha256(native.seal.raw_bytes).hexdigest()
            != native.seal.decision_fingerprint
        ):
            raise H1PostSealRecoverySourceError("selected decision bytes differ")
        initialization = self._initialization(native.initialization.raw_bytes)
        request = DriveInputRequestV1.model_validate_json(initialization.driver_request_bytes)
        if (
            request.identity != original_identity
            or request.original_driver_command_fingerprint() != original_fingerprint
            or initialization.driver_request_fingerprint != original_fingerprint
        ):
            raise H1PostSealRecoverySourceError("original driver initialization differs")
        if request.canonical_bytes() != initialization.driver_request_bytes:
            raise H1PostSealRecoverySourceError("original driver request bytes are noncanonical")
        database_identity = (native.database_path, native.database_device, native.database_inode)
        launch = self._mount._launch
        slot = launch._slot
        if (
            source.tenant_id != self._runtime._tenant_id
            or source.tenant_id != self._mount.tenant_id
            or source.tenant_id != original_identity.tenant_id
            or source.database_id != original_identity.database_id
            or source.database_id != slot.database_id
            or database_identity != self._runtime._database_identity
            or database_identity
            != (str(launch.database_path.resolve(strict=True)), *launch.database_identity)
        ):
            raise H1PostSealRecoverySourceError("installed database or tenant identity differs")
        physical = self._physical_members(native.physical_members)
        lineage = tuple(native.lineage)
        if not lineage or native.initialization.decision_id != lineage[0].decision_id:
            raise H1PostSealRecoverySourceError("selected Run lineage differs from initialization")
        if lineage[-1].decision_id != native.seal.decision_id:
            raise H1PostSealRecoverySourceError("selected seal does not close selected Run lineage")
        lineage_ids = {item.decision_id for item in lineage}
        if (
            any(member["decision_id"] not in lineage_ids for member in physical)
            or not lineage_ids.issubset({member["decision_id"] for member in physical})
        ):
            raise H1PostSealRecoverySourceError("physical members differ from selected Run lineage")
        selected_physical = [
            member
            for member in physical
            if member["record_id"] == selected_seal.revision.head
            and member["fingerprint"] == selected_seal.revision.fingerprint
        ]
        if (
            selected_seal.revision.head != "record:" + selected_seal.revision.fingerprint
            or len(selected_physical) != 1
        ):
            raise H1PostSealRecoverySourceError("selected seal physical member differs")
        run_head = source.complete_ordered_run_lineage[-1].head
        source_preimage = {
            "schema_id": _SOURCE_SCHEMA,
            "tenant_id": source.tenant_id,
            "database_id": source.database_id,
            "database_identity": list(database_identity),
            "journal_instance_id": self._mount.journal_instance_id,
            "original_driver_request_base64": _base64(initialization.driver_request_bytes),
            "initialization": self._decision(native.initialization),
            "run_lineage": [self._decision(item) for item in lineage],
            "selected_seal_decision": self._decision(native.seal),
            "physical_members": physical,
        }
        commitment = source_commitment_for_preimage(source_preimage)
        publication = publication_identity_for_source(
            tenant_id=source.tenant_id,
            database_id=source.database_id,
            database_identity=database_identity,
            journal_instance_id=self._mount.journal_instance_id,
            source_commitment=commitment,
        )
        return H1PostSealRecoveryRootV1(
            tenant_id=source.tenant_id,
            database_id=source.database_id,
            database_identity=database_identity,
            journal_instance_id=self._mount.journal_instance_id,
            selected_seal_subject_id=selected_seal.subject_id,
            selected_seal_head=selected_seal.revision.head,
            selected_seal_fingerprint=selected_seal.revision.fingerprint,
            selected_decision_id=native.seal.decision_id,
            selected_decision_digest=native.seal.decision_fingerprint,
            selected_run_head=run_head,
            original_command_id=original_identity.driver_command_id,
            original_command_fingerprint=original_fingerprint,
            source_commitment=commitment,
            publication_command_id=publication.command_id,
            publication_command_fingerprint=publication.command_fingerprint,
        )

    @staticmethod
    def _initialization(raw: bytes) -> RetainedInboxExecutionInitialization:
        entry = _strict_object(raw)
        try:
            value = entry["inbox_initialization"]
            if not isinstance(value, str):
                raise TypeError("initialization is absent")
            return RetainedInboxExecutionInitialization.model_validate_json(value)
        except (KeyError, TypeError, ValueError) as error:
            raise H1PostSealRecoverySourceError("selected initialization differs") from error

    @staticmethod
    def _require_exact_v2(raw: bytes) -> None:
        entry = _strict_object(raw)
        if "h1_historical_checkpoint" in entry:
            raise H1PostSealRecoverySourceError("H1 V2 recovery root rejects a checkpoint")
        try:
            retained_raw = entry["execution_complete_seal"]
            envelope_raw = entry["execution_complete_seal_envelope"]
            if not isinstance(retained_raw, str) or not isinstance(envelope_raw, str):
                raise TypeError("seal fields are absent")
            retained = RetainedExecutionCompleteSealV2.model_validate_json(retained_raw)
            envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(envelope_raw)
        except (KeyError, TypeError, ValueError) as error:
            raise H1PostSealRecoverySourceError(
                "recovery root requires exact H1 V2 seal"
            ) from error
        if (
            retained.canonical_bytes().decode() != retained_raw
            or envelope.canonical_bytes().decode() != envelope_raw
            or build_complete_seal_envelope(retained) != envelope
        ):
            raise H1PostSealRecoverySourceError("H1 V2 seal envelope differs")

    @staticmethod
    def _decision(value: Any) -> dict[str, str]:
        if (
            not isinstance(value.decision_id, str)
            or not isinstance(value.raw_bytes, bytes)
            or hashlib.sha256(value.raw_bytes).hexdigest() != value.decision_fingerprint
        ):
            raise H1PostSealRecoverySourceError("selected decision differs")
        _strict_object(value.raw_bytes)
        return {"decision_id": value.decision_id, "raw_base64": _base64(value.raw_bytes)}

    @staticmethod
    def _physical_members(values: tuple[Any, ...]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for value in values:
            payload_fingerprint = hashlib.sha256(value.canonical_bytes).hexdigest()
            if (
                not isinstance(value.decision_id, str)
                or not isinstance(value.record_id, str)
                or not isinstance(value.canonical_bytes, bytes)
                or not isinstance(value.decision_fingerprint, str)
                or (value.decision_id, value.record_id) in seen
            ):
                raise H1PostSealRecoverySourceError("selected physical member differs")
            seen.add((value.decision_id, value.record_id))
            result.append(
                {
                    "decision_id": value.decision_id,
                    "record_id": value.record_id,
                    "owner": value.owner,
                    "schema_id": value.schema_id,
                    "canonical_payload_base64": _base64(value.canonical_bytes),
                    "fingerprint": payload_fingerprint,
                }
            )
        if not result:
            raise H1PostSealRecoverySourceError("selected physical members are absent")
        return result


__all__ = [
    "H1PostSealRecoveryHistoricalSourceUnavailable",
    "H1PostSealRecoveryPublicationIdentity",
    "H1PostSealRecoveryRootSource",
    "H1PostSealRecoverySourceError",
    "publication_identity_for_source",
    "source_commitment_for_preimage",
]
