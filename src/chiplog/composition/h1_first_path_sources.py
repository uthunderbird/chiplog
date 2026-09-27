"""Raw, fail-closed selected-source reader for the H1 first-path cut.

This seam authenticates the physical pieces which a first-path completion cut
will need.  It deliberately does not emit a cut until the recovery-frontier
family profile defines how every registered family maps to selected evidence.
Registry row labels alone are not that definition.

The implementation intentionally does not call a joined history reader.  Such
readers eventually bind selected H1 issuance and would make historical
verification recursive.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from chiplog.adapters.driven.loop_sqlite import OWNER
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    FirstPathCompletionCutV2,
    PrepareExecutionCompletionFirstPathV2,
    first_path_frontier_fingerprint,
    first_path_inventory_fingerprint,
)
from chiplog.capabilities.agent_loop.execution_h1_frontier_profile_v2 import (
    H1WorkspaceClosure,
    derive_h1_frontier_profile_v2_members,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    ExecutionRunCanonicalMember,
    decode_execution_run_member,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import RecoveryFrontier
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    decode_frontier_registry,
    execution_h1_zero_call_frontier_registry_v2,
    execution_zero_call_frontier_registry,
    frontier_registry_reference,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import (
    DriveInputRequestV1,
    DriverCommandIdentityV1,
)
from chiplog.composition.h1_owner_inventory import read_h1_scoped_owner_inventory
from chiplog.composition.h1_preseal_contracts import (
    H1OwnerAsOfV1,
    H1SelectedPrepare,
    H1SelectedSeal,
)
from chiplog.composition.h1_selected_prepare import (
    _H1SelectedCut,
    _resolve_h1_selected_cut,
    _select_h1_prepare_at_cut,
    reopen_selected_h1_workspace,
    select_h1_v3_prepare_for_seal,
)
from chiplog.composition.h1_verified_snapshot_rows import (
    H1VerifiedSnapshotRows,
    H1VerifiedSnapshotRowsError,
)
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
    ExecutionCompleteSealPhysicalEnvelopeV2,
    ExecutionCompleteSealPhysicalEnvelopeV3,
    RetainedExecutionCompleteSealV2,
    RetainedExecutionCompleteSealV3,
    build_complete_seal_envelope,
    complete_seal_physical_command,
)
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
    RetainedInboxExecutionInitialization,
)
from chiplog.composition.r14_execution_transition_records import (
    RetainedExecutionTransitionV3,
)
from chiplog.composition.r14_fanout_contracts import SEAL_SCHEMA
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1VerifiedWorkspaceClosure
from chiplog.composition.r14_loop_history import _strict_json, resolve_h1_checkpoint
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_checkpoint import AuthorityCheckpointStore
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication

if TYPE_CHECKING:
    from chiplog.composition.h1_conversation_sources import _AuthenticatedConversationInventory


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class H1FirstPathPhysicalMember:
    """One SQL source member and its selected raw-journal locator."""

    decision_id: str
    decision_fingerprint: str
    operation_kind: str
    publication_id: str
    commit_sequence: int
    record_id: str
    owner: str
    schema_id: str
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class H1FirstPathRawDecision:
    """One exact selected journal entry retained by historical readback."""

    decision_id: str
    decision_fingerprint: str
    raw_bytes: bytes


@dataclass(frozen=True, slots=True)
class H1HistoricalFirstPathNativeCut:
    """Authenticated historical raw material for a selected first-path cut.

    This is a readback result, not a caller-constructible authority input.  Its
    values are only returned after the selected checkpoint replay has matched
    ``source`` exactly.
    """

    source: FirstPathCompletionCutV2
    initialization: H1FirstPathRawDecision
    lineage: tuple[H1FirstPathRawDecision, ...]
    seal: H1FirstPathRawDecision
    physical_members: tuple[H1FirstPathPhysicalMember, ...]
    database_path: str
    database_device: int
    database_inode: int


@dataclass(frozen=True, slots=True)
class H1CurrentFirstPathNativeCut:
    """Authenticated raw material from a freshly replayed issuer-owned capture."""

    source: FirstPathCompletionCutV2
    initialization: H1FirstPathRawDecision
    lineage: tuple[H1FirstPathRawDecision, ...]
    seal: H1FirstPathRawDecision
    physical_members: tuple[H1FirstPathPhysicalMember, ...]
    database_path: str
    database_device: int
    database_inode: int
    commitment: str


@dataclass(frozen=True, slots=True)
class H1FirstPathCapture:
    """Producer-owned current capture; object identity is its provenance token."""

    source: FirstPathCompletionCutV2
    initialization_envelope_bytes: bytes
    selected_envelopes: tuple[bytes, ...]
    physical_members: tuple[H1FirstPathPhysicalMember, ...]
    database_path: str
    database_device: int
    database_inode: int


@dataclass(frozen=True, slots=True)
class _SelectedCommand:
    decision_id: str
    raw: bytes
    command: PhysicalPublicationCommand


@dataclass(frozen=True, slots=True)
class _RawFirstPath:
    initialization: _SelectedCommand
    lineage: tuple[tuple[_SelectedCommand, ExecutionRunRecord, PhysicalRecord], ...]
    seal: _SelectedCommand
    seal_record: PhysicalRecord
    sealed_response: SealedResponseRecord
    registry_record: PhysicalRecord
    registry_is_v2: bool
    physical_members: tuple[H1FirstPathPhysicalMember, ...]
    commitment: str
    database_identity: tuple[str, int, int]
    historical_cut: _H1SelectedCut | None = None


class H1WorkspaceClosureResolver(Protocol):
    """Broker-private reopening port for one already selected V3 Prepare."""

    def __call__(
        self,
        *,
        selected_prepare: RetainedExecutionTransitionV3,
        selected_prepare_bytes: bytes,
        workspace_member_bytes: bytes,
    ) -> H1WorkspaceClosure: ...


class H1FirstPathSources:
    """Capture and replay a mounted, fail-closed H1 V2 post-seal cut.

    The public surface accepts only the original identity and selected seal.
    All Prepare, workspace, owner-inventory, and physical evidence is reopened
    from the runtime under its canonical authority gate.
    """

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 sources require the canonical common CLI runtime")
        self._runtime = runtime
        self._gate = runtime._authority_gate()
        self._issued: dict[int, H1FirstPathCapture] = {}

    @staticmethod
    def _require_frozen_family_mapping(family: str) -> None:
        """Reject unknown semantics rather than converting them to Absence."""
        raise ValueError(f"frontier family mapping is not frozen: {family}")

    def capture_current(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1FirstPathCapture:
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError("H1 sources require an exact selected response-seal locator")
        if type(original_identity) is not DriverCommandIdentityV1:
            raise TypeError("H1 sources require the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise ValueError("H1 sources require the exact original driver fingerprint")
        # This branch exists solely for the structural V1-registry rejection
        # test, which deliberately creates no runtime.  It cannot issue a V2
        # capture because all V2 work below requires the canonical gate.
        if not hasattr(self, "_gate"):
            raw = self._read_selected_cut(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
            )
            if not raw.registry_is_v2:
                raise ValueError("H1 first-path V2 requires a genuinely selected V2 registry")
            raise RuntimeError("H1 sources require the canonical authority gate")
        with self._gate.hold():
            raw = self._read_selected_cut(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
            )
            source = self._read_v2_source(raw, historical=False)
            capture = H1FirstPathCapture(
                source=source,
                initialization_envelope_bytes=raw.initialization.raw,
                selected_envelopes=(*(command.raw for command, _, _ in raw.lineage), raw.seal.raw),
                physical_members=raw.physical_members,
                database_path=raw.database_identity[0],
                database_device=raw.database_identity[1],
                database_inode=raw.database_identity[2],
            )
            self._issued[id(capture)] = capture
            return capture

    def check_current(self, capture: H1FirstPathCapture) -> bool:
        """Reject copied/unissued captures before any potentially stale reuse."""
        if not isinstance(capture, H1FirstPathCapture):
            return False
        if self._issued.get(id(capture)) is not capture:
            return False
        try:
            entry = json.loads(capture.initialization_envelope_bytes)
            initialization = RetainedInboxExecutionInitialization.model_validate_json(
                entry["inbox_initialization"]
            )
            with self._gate.hold():
                request = DriveInputRequestV1.model_validate_json(
                    initialization.driver_request_bytes
                )
                raw = self._read_selected_cut(
                    original_identity=request.identity,
                    original_fingerprint=initialization.driver_request_fingerprint,
                    selected_seal=capture.source.selected_response_seal,
                )
                return self._read_v2_source(raw, historical=False) == capture.source
        except (
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            sqlite3.Error,
        ):
            return False

    def replay_current_native_cut(self, capture: H1FirstPathCapture) -> H1CurrentFirstPathNativeCut:
        """Reopen one issuer-owned capture against the current selected cut.

        This readback is bounded to the capture issued by this instance.  It
        performs no owner IPC and creates no completion request; it only returns
        raw material after the current selected journal and physical projection
        exactly reproduce the issued capture.
        """
        with self._gate.hold():
            raw, source = self._replay_current_capture(capture)
            return H1CurrentFirstPathNativeCut(
                source=source,
                initialization=self._raw_decision(raw.initialization),
                lineage=tuple(self._raw_decision(command) for command, _, _ in raw.lineage),
                seal=self._raw_decision(raw.seal),
                physical_members=raw.physical_members,
                database_path=raw.database_identity[0],
                database_device=raw.database_identity[1],
                database_inode=raw.database_identity[2],
                commitment=raw.commitment,
            )

    def _replay_current_capture(
        self, capture: H1FirstPathCapture
    ) -> tuple[_RawFirstPath, FirstPathCompletionCutV2]:
        """Require all issuer-owned capture fields to match a fresh raw replay."""
        if type(capture) is not H1FirstPathCapture or self._issued.get(id(capture)) is not capture:
            raise ValueError("H1 completion requires this issuer-owned first-path capture")
        try:
            entry = json.loads(capture.initialization_envelope_bytes)
            initialization = RetainedInboxExecutionInitialization.model_validate_json(
                entry["inbox_initialization"]
            )
            request = DriveInputRequestV1.model_validate_json(initialization.driver_request_bytes)
            with self._gate.hold():
                raw = self._read_selected_cut(
                    original_identity=request.identity,
                    original_fingerprint=initialization.driver_request_fingerprint,
                    selected_seal=capture.source.selected_response_seal,
                )
                source = self._read_v2_source(raw, historical=False)
            expected_envelopes = (*(command.raw for command, _, _ in raw.lineage), raw.seal.raw)
            if (
                source != capture.source
                or source.materialization_commitment != raw.commitment
                or capture.initialization_envelope_bytes != raw.initialization.raw
                or capture.selected_envelopes != expected_envelopes
                or capture.physical_members != raw.physical_members
                or (
                    capture.database_path,
                    capture.database_device,
                    capture.database_inode,
                )
                != raw.database_identity
            ):
                raise ValueError("H1 completion requires a current first-path capture")
            self._validate_selected_completion_evidence(source)
            return raw, source
        except ValueError:
            raise
        except (
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            sqlite3.Error,
        ) as error:
            raise ValueError("H1 completion requires a current first-path capture") from error

    def _prepare_first_path_completion_request(
        self, capture: H1FirstPathCapture, delivery_receipt: object
    ) -> PrepareExecutionCompletionFirstPathV2:
        """Build one request solely from the current A cut and opaque P receipt.

        This synchronous seam never calls an owner.  P remains the only source
        of delivery/fence values; object identity of its receipt, rather than a
        caller-shaped DTO, binds those values to this capture.
        """
        from chiplog.composition.h1_runtime_preissuance_port import (
            _H1RuntimePreissuancePort,
        )

        if type(capture) is not H1FirstPathCapture or self._issued.get(id(capture)) is not capture:
            raise ValueError("H1 completion requires this issuer-owned first-path capture")
        with self._gate.hold():
            _raw, source = self._replay_current_capture(capture)
            port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
            if type(port) is not _H1RuntimePreissuancePort:
                raise ValueError("H1 completion requires the installed P delivery owner")
            delivery, fence = port._replay_delivery_inputs(delivery_receipt, capture)
            run = source.complete_ordered_run_lineage[-1]
            attempt = run.turns[0].attempts[0]
            if attempt.response_base64 is None:
                raise ValueError("H1 completion selected attempt lacks a captured response")
            try:
                response = base64.b64decode(attempt.response_base64, validate=True)
            except (TypeError, ValueError) as error:
                raise ValueError("H1 completion selected response is not strict base64") from error
            manifest = attempt.manifest
            request = PrepareExecutionCompletionFirstPathV2(
                command_id="h1-first-path-completion:" + _sha256(source.canonical_bytes()),
                run=run,
                selected_attempt=CallSubjectHead(
                    subject_id=attempt.attempt_id,
                    revision=Present(head=attempt.head, fingerprint=attempt.digest()),
                ),
                selector_generation=attempt.generation,
                visibility_manifest=CallSubjectHead(
                    subject_id="visibility",
                    revision=Present(
                        head="record:" + manifest.digest(), fingerprint=manifest.digest()
                    ),
                ),
                exact_captured_response=response,
                source=source,
                delivery=delivery,
                fence=fence,
            )
            # Construction validates the public DTO; canonicalization is also
            # pinned before B hands the request to the owner.
            PrepareExecutionCompletionFirstPathV2.model_validate_json(request.canonical_bytes())
            return request

    def _validate_selected_completion_evidence(self, source: FirstPathCompletionCutV2) -> None:
        """Check the selected Run/attempt/seal facts before any future assembly."""
        lineage = source.complete_ordered_run_lineage
        if len(lineage) < 3:
            raise ValueError("H1 completion selected Run lineage is incomplete")
        captured, run = lineage[-2:]
        if (
            source.current_run != self._run_reference(run)
            or source.selected_capture != self._run_reference(captured)
            or run.event != "ModelCompletionPrepared"
            or captured.event != "ModelResponseCaptured"
            or len(run.turns) != 1
            or len(captured.turns) != 1
        ):
            raise ValueError("H1 completion selected Run or capture differs")
        turn, captured_turn = run.turns[0], captured.turns[0]
        if (
            turn.turn_id != captured_turn.turn_id
            or turn.attempts != captured_turn.attempts
            or turn.selector != captured_turn.selector
            or len(turn.attempts) != 1
            or turn.response_seal != source.selected_response_seal
        ):
            raise ValueError("H1 completion selected attempt or seal differs")
        attempt = turn.attempts[0]
        if attempt.response_base64 is None:
            raise ValueError("H1 completion selected attempt lacks a captured response")
        try:
            base64.b64decode(attempt.response_base64, validate=True)
        except (TypeError, ValueError) as error:
            raise ValueError("H1 completion selected response is not strict base64") from error
        if (
            source.seal.captured_response != source.selected_capture
            or source.seal.original_run_id != run.run_id
            or source.seal.original_turn_id != turn.turn_id
            or source.seal.complete_ordered_initialized != ()
        ):
            raise ValueError("H1 completion selected seal differs from captured response")

    def validate_historical(
        self,
        source: FirstPathCompletionCutV2,
        *,
        initialization_envelope_bytes: bytes,
    ) -> None:
        """Require ``source`` to equal the selected verified-snapshot replay."""
        self.replay_selected_native_cut(
            source, initialization_envelope_bytes=initialization_envelope_bytes
        )

    def replay_selected_native_cut(
        self,
        source: FirstPathCompletionCutV2,
        *,
        initialization_envelope_bytes: bytes,
    ) -> H1HistoricalFirstPathNativeCut:
        """Replay one selected checkpoint and retain its exact native raw material.

        The returned carrier is bounded to the already selected H1 snapshot.
        It neither accepts a caller-supplied journal entry or physical member,
        nor issues a capture or calls owner IPC.  A schema-valid source DTO is
        useful only when the independently reopened selected bytes reproduce it.
        """
        if type(source) is not FirstPathCompletionCutV2:
            raise TypeError("H1 historical source requires an exact first-path cut")
        if (
            not isinstance(initialization_envelope_bytes, bytes)
            or not initialization_envelope_bytes
        ):
            raise ValueError("H1 historical source lacks a retained initialization envelope")
        try:
            entry = json.loads(initialization_envelope_bytes)
            encoded = entry["inbox_initialization"]
            initialization = RetainedInboxExecutionInitialization.model_validate_json(encoded)
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("H1 historical initialization envelope is invalid") from error
        request = DriveInputRequestV1.model_validate_json(initialization.driver_request_bytes)
        with self._gate.hold():
            raw = self._read_selected_cut(
                original_identity=request.identity,
                original_fingerprint=initialization.driver_request_fingerprint,
                selected_seal=source.selected_response_seal,
                historical=True,
            )
            if (
                raw.initialization.raw != initialization_envelope_bytes
                or initialization.request.admitted != source.selected_admitted_input
                or not raw.registry_is_v2
                or source.frontier.registry != execution_h1_zero_call_frontier_registry_v2()
            ):
                raise ValueError("H1 historical source differs from raw V2 selection")
            replay = self._read_v2_source(raw, historical=True)
        if replay != source:
            raise ValueError("H1 historical source differs from raw V2 replay")
        return self._native_cut(raw, replay)

    def issue_historical_v2_cut(
        self,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1HistoricalFirstPathNativeCut:
        """Issue the independently selected, immutable full V2 historical cut.

        The inputs are locators only.  The full completion DTO is constructed
        from the selected authenticated DECIDED entry and its bound authority
        post-image, never accepted from a caller or reconstructed from current
        SQLite state.
        """
        if type(original_identity) is not DriverCommandIdentityV1:
            raise TypeError("H1 historical V2 source requires the exact original identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise ValueError("H1 historical V2 source has an invalid original fingerprint")
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError("H1 historical V2 source requires an exact selected seal")
        with self._gate.hold():
            self._runtime._require_no_pending()
            raw = self._read_selected_cut(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
                historical=True,
            )
            self._require_historical_v2_seal(raw.seal.raw)
            source = self._read_v2_source(raw, historical=True)
            return self._native_cut(raw, source)

    def _read_historical_conversation_inventory(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> _AuthenticatedConversationInventory:
        """Return A's complete inventory from the selected V2 authority cut.

        This stays private to the two installed source owners.  Its local A
        imports avoid an import-time cycle while keeping the carrier out of
        public caller authority.  Both loop and owner selections are replayed
        from their seal-bounded authenticated prefixes; SQLite is never read.
        """
        from chiplog.composition.h1_conversation_sources import (
            H1ConversationPhysicalMember,
            H1ConversationSources,
            H1SelectedConversationPublication,
            _AuthenticatedConversationInventory,
        )
        from chiplog.composition.h1_inventory_owner_cut import _parse_h1_owner_asof
        from chiplog.composition.h1_owner_inventory import (
            _PhysicalRow,
            _reconcile_historical_owner_cut,
        )

        if type(original_identity) is not DriverCommandIdentityV1:
            raise TypeError("H1 historical conversation inventory requires the exact identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise ValueError("H1 historical conversation inventory has an invalid fingerprint")
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError("H1 historical conversation inventory requires an exact seal")
        with self._gate.hold():
            self._runtime._require_no_pending()
            raw = self._read_selected_cut(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
                historical=True,
            )
            self._require_historical_v2_seal(raw.seal.raw)
            source = self._read_v2_source(raw, historical=True)
            cut = raw.historical_cut
            if cut is None:
                raise ValueError("H1 historical conversation inventory lacks a selected cut")
            rows = cut.rows
            if (
                source.tenant_id != self._runtime._tenant_id
                or source.materialization_commitment != raw.commitment
                or rows.commitment != raw.commitment
                or rows.tenant_head(source.tenant_id) != source.tenant_commit_sequence
            ):
                raise ValueError("H1 historical conversation inventory differs from native cut")
            database = Path(self._runtime._database).resolve(strict=True)
            before = database.stat()
            identity = (str(database), before.st_dev, before.st_ino)
            if (
                identity != raw.database_identity
                or identity != cut.database_identity
                or identity != self._runtime._database_identity
            ):
                raise ValueError("H1 historical conversation inventory database identity differs")
            for _decision_id, _decision_bytes, command in cut.commands:
                rows.require_complete(command)
            publications = rows.publications(source.tenant_id)
            records = rows.records(source.tenant_id)
            physical_rows = tuple(
                _PhysicalRow(str(record_id), str(owner), str(schema), payload, sequence)
                for record_id, owner, schema, payload, sequence in records
                if type(payload) is bytes and type(sequence) is int
            )
            if len(physical_rows) != len(records):
                raise ValueError("H1 historical conversation inventory physical rows differ")
            seal = self._selected_seal(raw)
            owner_journal = self._runtime._owner_decisions()
            owner_snapshot, _owner_prefix = _reconcile_historical_owner_cut(
                source.tenant_id, owner_journal, seal, publications, physical_rows
            )
            locator = _parse_h1_owner_asof(seal, source.tenant_id)
            if owner_snapshot.head != locator.owner_head:
                raise ValueError("H1 historical conversation owner prefix differs")
            raw_owner_entries = tuple(owner_journal._raw.entries())
            raw_by_id = {
                decision_id: decision
                for decision_id, _previous, decision in raw_owner_entries
            }
            if len(raw_by_id) != len(raw_owner_entries):
                raise ValueError("H1 historical conversation owner decisions collide")
            selected = [
                H1SelectedConversationPublication(decision_id, decision_bytes, command, None)
                for decision_id, decision_bytes, command in cut.commands
            ]
            for decision in owner_snapshot.decisions:
                command = self._runtime._owner_command(decision)
                if command.tenant_id != source.tenant_id:
                    continue
                rows.require_complete(command)
                owner_raw = raw_by_id.get(decision.decision_id)
                if (
                    not isinstance(owner_raw, bytes)
                    or _sha256(owner_raw) != decision.decision_fingerprint
                ):
                    raise ValueError("H1 historical conversation owner decision differs")
                evidence = H1ConversationSources._complete_acceptance_evidence(
                    decision.prepared.request, command
                )
                selected.append(
                    H1SelectedConversationPublication(
                        decision.decision_id, owner_raw, command, evidence
                    )
                )
            if not selected or len({item.decision_id for item in selected}) != len(selected):
                raise ValueError("H1 historical conversation selected inventory differs")
            physical = tuple(
                H1ConversationPhysicalMember(
                    row.record_id, row.owner, row.schema, row.raw, row.sequence
                )
                for row in physical_rows
            )
            self._runtime._check_database_identity()
            after = database.stat()
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("H1 historical conversation inventory database changed")
            return _AuthenticatedConversationInventory(
                tuple(sorted(selected, key=lambda item: item.command.expected_head)),
                physical,
                rows.commitment,
                rows.tenant_head(source.tenant_id),
                identity,
            )

    def _native_cut(
        self, raw: _RawFirstPath, source: FirstPathCompletionCutV2
    ) -> H1HistoricalFirstPathNativeCut:
        """Project only raw material authenticated by a completed replay."""
        return H1HistoricalFirstPathNativeCut(
            source=source,
            initialization=self._raw_decision(raw.initialization),
            lineage=tuple(self._raw_decision(command) for command, _, _ in raw.lineage),
            seal=self._raw_decision(raw.seal),
            physical_members=raw.physical_members,
            database_path=raw.database_identity[0],
            database_device=raw.database_identity[1],
            database_inode=raw.database_identity[2],
        )

    @staticmethod
    def _require_historical_v2_seal(decision_bytes: bytes) -> None:
        """Keep the authority-cut route distinct from a V3 checkpoint seal."""
        try:
            entry = _strict_json(decision_bytes)
            retained_raw = entry["execution_complete_seal"]
            if not isinstance(retained_raw, str):
                raise TypeError("retained seal is absent")
            retained = RetainedExecutionCompleteSealV2.model_validate_json(retained_raw)
            if retained.canonical_bytes().decode() != retained_raw:
                raise ValueError("retained seal is noncanonical")
            if "h1_historical_checkpoint" in entry:
                raise ValueError("V2 authority cut cannot carry a checkpoint")
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("H1 historical source requires the exact V2 seal") from error

    @staticmethod
    def _raw_decision(command: _SelectedCommand) -> H1FirstPathRawDecision:
        return H1FirstPathRawDecision(
            decision_id=command.decision_id,
            decision_fingerprint=_sha256(command.raw),
            raw_bytes=command.raw,
        )

    def _read_v2_source(self, raw: _RawFirstPath, *, historical: bool) -> FirstPathCompletionCutV2:
        """Read the full V2 cut from mounted physical and owner sources.

        Callers hold the canonical authority gate.  The inventory adapter gets
        the exact selected seal decision and physical command, never a
        reconstructible locator supplied by a public caller.
        """
        if not raw.registry_is_v2:
            raise ValueError("H1 first-path V2 requires a genuinely selected V2 registry")
        if historical:
            self._decode_h1_owner_asof(raw.seal.raw, tenant_id=raw.lineage[-1][1].tenant)
        selected, workspace = self._resolve_workspace_closure(raw, historical=historical)
        seal = self._selected_seal(raw)
        receipt = read_h1_scoped_owner_inventory(
            self._runtime,
            captured=raw.lineage[-2][1],
            selected_prepare=selected,
            workspace=workspace,
            phase="HISTORICAL" if historical else "POST_SEAL",
            selected_seal=seal,
            historical_cut=raw.historical_cut,
        )
        self._verify_inventory_boundary(raw, selected, receipt)
        return self._build_v2_cut(raw, workspace, receipt)

    @staticmethod
    def _decode_h1_owner_asof(decision_bytes: bytes, *, tenant_id: str) -> H1OwnerAsOfV1:
        """Decode the V2-only historical owner prefix from exact seal bytes.

        The raw DECIDED envelope is retained by ``H1SelectedSeal`` and passed
        unchanged to the inventory reader.  This check makes its owner prefix
        explicit before an historical cut can use it; it never derives one from
        a present owner journal.
        """

        def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("H1 selected V2 seal has duplicate owner-as-of field")
                result[key] = value
            return result

        try:
            envelope = json.loads(decision_bytes, object_pairs_hook=no_duplicates)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("H1 selected V2 seal owner-as-of field is invalid") from error
        if not isinstance(envelope, dict) or "h1_owner_asof" not in envelope:
            raise ValueError("H1 selected V2 seal lacks owner-as-of field")
        if json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode() != decision_bytes:
            raise ValueError("H1 selected V2 seal decision bytes are noncanonical")
        value = envelope["h1_owner_asof"]
        if not isinstance(value, dict):
            raise ValueError("H1 selected V2 seal owner-as-of field is invalid")
        try:
            locator = H1OwnerAsOfV1.model_validate(value)
        except ValueError as error:
            raise ValueError("H1 selected V2 seal owner-as-of field is invalid") from error
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if locator.canonical_bytes() != encoded:
            raise ValueError("H1 selected V2 seal owner-as-of field is noncanonical")
        if locator.tenant_id != tenant_id:
            raise ValueError("H1 selected V2 seal owner-as-of tenant differs")
        return locator

    def _verify_inventory_boundary(
        self, raw: _RawFirstPath, selected: H1SelectedPrepare, receipt: object
    ) -> None:
        """Require the independent receipt to describe this exact raw cut."""
        from chiplog.composition.h1_preseal_contracts import H1InventoryReceipt

        if type(receipt) is not H1InventoryReceipt:
            raise ValueError("H1 post-seal inventory reader returned no exact receipt")
        final = raw.lineage[-1][1]
        sequence = raw.seal.command.expected_head + 1
        if (
            receipt.database_identity != raw.database_identity
            or receipt.tenant_sequence != sequence
            or receipt.commitment != raw.commitment
            or receipt.scope.tenant != final.tenant
            or receipt.scope.principal != final.principal
            or receipt.scope.run_id != final.run_id
            or receipt.scope.turn_id != final.turns[0].turn_id
            or selected.decision_id not in {item[0] for item in receipt.loop_entries}
            or raw.seal.decision_id not in {item[0] for item in receipt.loop_entries}
        ):
            raise ValueError("H1 post-seal inventory receipt differs from raw selected cut")
        decisions = {item[0]: item[2] for item in receipt.loop_entries}
        if (
            decisions.get(selected.decision_id) != selected.decision_bytes
            or decisions.get(raw.seal.decision_id) != raw.seal.raw
        ):
            raise ValueError("H1 post-seal inventory receipt lacks selected decision bytes")

    def _build_v2_cut(
        self,
        raw: _RawFirstPath,
        workspace: H1VerifiedWorkspaceClosure,
        receipt: object,
    ) -> FirstPathCompletionCutV2:
        """Construct and self-validate the DTO from exact selected records."""
        from chiplog.composition.h1_preseal_contracts import H1InventoryReceipt

        assert type(receipt) is H1InventoryReceipt
        entry = json.loads(raw.initialization.raw)
        initialization = RetainedInboxExecutionInitialization.model_validate_json(
            entry["inbox_initialization"]
        )
        registry = execution_h1_zero_call_frontier_registry_v2()
        members = derive_h1_frontier_profile_v2_members(
            final_run=raw.lineage[-1][1],
            selected_admitted_input=initialization.request.admitted,
            seal=raw.sealed_response,
            verified_workspace=workspace,
        )
        frontier = RecoveryFrontier(
            tenant_id=raw.lineage[-1][1].tenant,
            run_id=raw.lineage[-1][1].run_id,
            tenant_commit_sequence=receipt.tenant_sequence,
            registry=registry,
            ordered_members=members,
            ordered_calls=(),
            canonicalization_version="chiplog.recovery.frontier.v1",
            fingerprint="0" * 64,
        )
        frontier = frontier.model_copy(
            update={"fingerprint": first_path_frontier_fingerprint(frontier)}
        )
        sources = self._complete_sources(raw, registry.canonical_bytes())
        cut = FirstPathCompletionCutV2.model_construct(
            tenant_id=raw.lineage[-1][1].tenant,
            database_id=initialization.request.admitted.database_id,
            tenant_commit_sequence=receipt.tenant_sequence,
            materialization_commitment=receipt.commitment,
            selected_admitted_input=initialization.request.admitted,
            complete_ordered_run_lineage=tuple(run for _, run, _ in raw.lineage),
            current_run=self._run_reference(raw.lineage[-1][1]),
            selected_capture=self._run_reference(raw.lineage[-2][1]),
            selected_response_seal=self._seal_reference(raw.sealed_response),
            seal=raw.sealed_response,
            frontier=frontier,
            complete_sources=sources,
            complete_inventory_fingerprint="0" * 64,
        )
        cut = cut.model_copy(
            update={"complete_inventory_fingerprint": first_path_inventory_fingerprint(cut)}
        )
        return FirstPathCompletionCutV2.model_validate_json(cut.canonical_bytes())

    @staticmethod
    def _run_reference(run: ExecutionRunRecord) -> CallSubjectHead:
        return CallSubjectHead(
            subject_id=run.run_id,
            revision=Present(head=run.head, fingerprint=_sha256(run.canonical_bytes())),
        )

    @staticmethod
    def _seal_reference(seal: SealedResponseRecord) -> CallSubjectHead:
        digest = _sha256(seal.canonical_bytes())
        return CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        )

    def _complete_sources(
        self, raw: _RawFirstPath, registry_bytes: bytes
    ) -> tuple[RecoverySourceRecord, ...]:
        values: list[RecoverySourceRecord] = []
        for command, run, record in raw.lineage:
            values.append(self._source_record(command, record, self._run_reference(run)))
        values.append(
            self._source_record(
                raw.seal, raw.seal_record, self._seal_reference(raw.sealed_response)
            )
        )
        values.append(
            self._source_record(
                raw.seal,
                raw.registry_record,
                frontier_registry_reference(execution_h1_zero_call_frontier_registry_v2()),
            )
        )
        if raw.registry_record.canonical_bytes != registry_bytes:
            raise ValueError("H1 selected V2 registry bytes differ during source construction")
        return tuple(values)

    @staticmethod
    def _source_record(
        command: _SelectedCommand, record: PhysicalRecord, subject: CallSubjectHead
    ) -> RecoverySourceRecord:
        raw_digest = _sha256(command.raw)
        record_digest = _sha256(record.canonical_bytes)
        return RecoverySourceRecord(
            owner=record.owner,
            subject=subject,
            schema_id=record.schema_id,
            canonical_record_bytes=record.canonical_bytes,
            selected_decision=CallSubjectHead(
                subject_id=command.decision_id,
                revision=Present(head=command.decision_id, fingerprint=raw_digest),
            ),
            physical_record=CallSubjectHead(
                subject_id=record.record_id,
                revision=Present(head="record:" + record_digest, fingerprint=record_digest),
            ),
        )

    def _read_selected_cut(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
        historical: bool = False,
    ) -> _RawFirstPath:
        """Authenticate raw selection and physical membership in one read cut."""
        runtime = self._runtime
        with self._gate.hold():
            if not historical:
                runtime._require_no_pending()
            runtime._check_database_identity()
            database = Path(runtime._database).resolve(strict=True)
            before = database.stat()
            commands = self._selected_commands()
            snapshot_rows: H1VerifiedSnapshotRows | None = None
            historical_cut: _H1SelectedCut | None = None
            if historical:
                historical_cut = _resolve_h1_selected_cut(runtime, selected_seal=selected_seal)
                snapshot_rows = historical_cut.rows
                commands = tuple(
                    _SelectedCommand(decision_id, raw, command)
                    for decision_id, raw, command in historical_cut.commands
                )
            initialization = self._find_initialization(
                commands, original_identity, original_fingerprint
            )
            if snapshot_rows is not None:
                raw = self._select_lineage_and_seal(
                    snapshot_rows, commands, initialization, selected_seal
                )
                commitment = snapshot_rows.commitment
            else:
                with closing(
                    sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
                ) as connection:
                    connection.execute("BEGIN")
                    commitment = capture_authority_snapshot_commitment(
                        connection, runtime._tenant_id
                    )
                    if not historical and (
                        commitment != runtime._commitment_journal.load(runtime._tenant_id)
                    ):
                        raise ValueError("H1 raw cut differs from independent commitment anchor")
                    fence = connection.execute(
                        "SELECT generation, frontier FROM deletion_fences WHERE tenant_id=?",
                        (runtime._tenant_id,),
                    ).fetchone()
                    if not historical and fence != ("r6", 0):
                        raise ValueError("H1 raw cut lacks the current deletion fence")
                    raw = self._select_lineage_and_seal(
                        connection, commands, initialization, selected_seal
                    )
            runtime._check_database_identity()
            after = database.stat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("H1 raw cut database identity changed during read")
            return _RawFirstPath(
                initialization=initialization,
                lineage=raw[0],
                seal=raw[1],
                seal_record=raw[2],
                sealed_response=raw[5],
                registry_record=raw[3],
                registry_is_v2=raw[4],
                physical_members=(
                    *(self._source_member(command, record) for command, _, record in raw[0]),
                    self._source_member(raw[1], raw[2]),
                    self._source_member(raw[1], raw[3]),
                ),
                commitment=commitment,
                database_identity=(str(database), before.st_dev, before.st_ino),
                historical_cut=historical_cut,
            )

    def _selected_commands(self) -> tuple[_SelectedCommand, ...]:
        commands: list[_SelectedCommand] = []
        for decision_id, _, raw in self._runtime._loop_decisions().entries():
            try:
                entry = json.loads(raw)
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("H1 raw journal entry is undecodable") from error
            if entry.get("kind") != "DECIDED":
                continue
            try:
                command = self._runtime._publication(entry)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("H1 selected journal entry has no physical command") from error
            if command.tenant_id != self._runtime._tenant_id:
                raise ValueError("H1 selected journal entry crosses tenants")
            commands.append(_SelectedCommand(decision_id, raw, command))
        sequence = tuple(item.command.expected_head for item in commands)
        if len(sequence) != len(set(sequence)):
            raise ValueError("H1 selected journal has competing publication predecessors")
        return tuple(sorted(commands, key=lambda item: item.command.expected_head))

    def _find_initialization(
        self,
        commands: tuple[_SelectedCommand, ...],
        identity: DriverCommandIdentityV1,
        fingerprint: str,
    ) -> _SelectedCommand:
        found: list[_SelectedCommand] = []
        for item in commands:
            if item.command.operation_kind != EXECUTION_INBOX_INITIALIZATION_OPERATION:
                continue
            entry = json.loads(item.raw)
            encoded = entry.get("inbox_initialization")
            if not isinstance(encoded, str):
                raise ValueError("H1 initialization decision lacks retained evidence")
            evidence = RetainedInboxExecutionInitialization.model_validate_json(encoded)
            if evidence.driver_request_fingerprint != fingerprint:
                continue
            from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1

            request = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
            if request.identity == identity:
                found.append(item)
        if len(found) != 1:
            raise ValueError("H1 raw cut has no unique selected initialization")
        return found[0]

    def _select_lineage_and_seal(
        self,
        connection: sqlite3.Connection | H1VerifiedSnapshotRows,
        commands: tuple[_SelectedCommand, ...],
        initialization: _SelectedCommand,
        selected_seal: CallSubjectHead,
    ) -> tuple[
        tuple[tuple[_SelectedCommand, ExecutionRunRecord, PhysicalRecord], ...],
        _SelectedCommand,
        PhysicalRecord,
        PhysicalRecord,
        bool,
        SealedResponseRecord,
    ]:
        entry = json.loads(initialization.raw)
        evidence = RetainedInboxExecutionInitialization.model_validate_json(
            entry["inbox_initialization"]
        )
        root = evidence.proposal.run
        runs: list[tuple[_SelectedCommand, ExecutionRunRecord, PhysicalRecord]] = []
        for item in commands:
            for member in item.command.records:
                if member.owner != OWNER or member.schema_id != EXECUTION_RUN_SCHEMA:
                    continue
                physical = self._physical_member(connection, item, member)
                decoded = decode_execution_run_member(
                    ExecutionRunCanonicalMember(
                        record_id=physical.record_id,
                        schema_id=physical.schema_id,  # type: ignore[arg-type]
                        canonical_record_bytes=physical.canonical_bytes,
                        fingerprint=_sha256(physical.canonical_bytes),
                    )
                ).run
                if isinstance(decoded, ExecutionRunRecord) and decoded.run_id == root.run_id:
                    runs.append((item, decoded, physical))
        runs.sort(key=lambda item: item[0].command.expected_head)
        if not runs or runs[0][1] != root:
            raise ValueError("H1 selected Run lineage lacks the exact H0 root")
        for index, (_, run, _) in enumerate(runs):
            if index == 0:
                if run.predecessor is not None:
                    raise ValueError("H1 selected root has a predecessor")
            elif run.predecessor != runs[index - 1][1].head:
                raise ValueError("H1 selected Run lineage has a gap or competing descendant")

        matching: list[tuple[_SelectedCommand, PhysicalRecord, PhysicalRecord]] = []
        for item in commands:
            if item.command.operation_kind != EXECUTION_COMPLETE_SEAL_OPERATION:
                continue
            seals = [member for member in item.command.records if member.schema_id == SEAL_SCHEMA]
            registries = [
                member
                for member in item.command.records
                if member.schema_id == RECOVERY_FRONTIER_REGISTRY_SCHEMA
            ]
            if len(seals) != 1 or len(registries) != 1:
                continue
            seal = self._physical_member(connection, item, seals[0])
            digest = _sha256(seal.canonical_bytes)
            decoded_seal = SealedResponseRecord.model_validate_json(seal.canonical_bytes)
            if decoded_seal.canonical_bytes() != seal.canonical_bytes:
                raise ValueError("H1 selected response seal bytes are not canonical")
            if seal.record_id != "record:" + digest:
                raise ValueError("H1 selected response seal physical ID differs")
            reference = CallSubjectHead(
                subject_id=decoded_seal.response_seal_id,
                revision=Present(head="record:" + digest, fingerprint=digest),
            )
            if reference != selected_seal:
                continue
            matching.append((item, seal, self._physical_member(connection, item, registries[0])))
        if len(matching) != 1:
            raise ValueError("H1 raw cut has no unique selected response seal")
        seal_command, seal_record, registry_record = matching[0]
        final = next(
            (run for command, run, _ in runs if command.decision_id == seal_command.decision_id),
            None,
        )
        if final is None or final != runs[-1][1]:
            raise ValueError("H1 selected seal does not close the selected Run lineage")
        v1_registry = execution_zero_call_frontier_registry()
        v2_registry = execution_h1_zero_call_frontier_registry_v2()
        if registry_record.canonical_bytes == v1_registry.canonical_bytes():
            registry = v1_registry
        elif registry_record.canonical_bytes == v2_registry.canonical_bytes():
            registry = v2_registry
        else:
            raise ValueError("H1 selected registry bytes differ from a fixed profile")
        expected_registry = frontier_registry_reference(registry)
        decode_frontier_registry(
            registry_record.schema_id,
            registry_record.canonical_bytes,
            expected_reference=expected_registry,
        )
        self._verify_native_source_inventory(tuple(runs), seal_command)
        return (
            tuple(runs),
            seal_command,
            seal_record,
            registry_record,
            registry == v2_registry,
            SealedResponseRecord.model_validate_json(seal_record.canonical_bytes),
        )

    @staticmethod
    def _derive_v2_frontier_members(
        raw: _RawFirstPath, *, verified_workspace: H1WorkspaceClosure | None
    ) -> None:
        """Run the sole V2 extractor; only a broker verifier can supply closure."""
        entry = json.loads(raw.initialization.raw)
        encoded = entry.get("inbox_initialization")
        if not isinstance(encoded, str):
            raise ValueError("H1 initialization decision lacks retained evidence")
        initialization = RetainedInboxExecutionInitialization.model_validate_json(encoded)
        derive_h1_frontier_profile_v2_members(
            final_run=raw.lineage[-1][1],
            selected_admitted_input=initialization.request.admitted,
            seal=raw.sealed_response,
            verified_workspace=verified_workspace,
        )

    def _resolve_workspace_closure(
        self, raw: _RawFirstPath, *, historical: bool
    ) -> tuple[H1SelectedPrepare, H1VerifiedWorkspaceClosure]:
        """Reopen the actual original workspace for the raw selected capture.

        Selection and reopening come from lower broker readers.  This source
        seam neither accepts a caller closure nor reconstructs a Prepare from
        a schema-valid journal blob.
        """
        raw_seal = self._selected_seal(raw)
        if historical and raw.historical_cut is not None:
            selected_postseal = _select_h1_prepare_at_cut(self._runtime, cut=raw.historical_cut)
        else:
            selected_postseal = select_h1_v3_prepare_for_seal(
                self._runtime,
                selected_seal=self._seal_reference(raw.sealed_response),
                historical=historical,
            )
        captured = raw.lineage[-2][1]
        sealed = raw.lineage[-1][1]
        if (
            selected_postseal.captured_run != captured
            or selected_postseal.sealed_run != sealed
            or selected_postseal.seal != raw_seal
        ):
            raise ValueError("H1 post-seal selected Prepare differs from raw selected cut")
        selected = selected_postseal.prepare
        selected_commands = {command.decision_id: command for command, _, _ in raw.lineage}
        command = selected_commands.get(selected.decision_id)
        if (
            command is None
            or command.raw != selected.decision_bytes
            or command.command != selected.publication
            or selected.physical_run not in command.command.records
        ):
            raise ValueError("H1 selected V3 Prepare differs from raw selected lineage")
        closure = reopen_selected_h1_workspace(self._runtime, selected)
        if closure.proposal_context_bytes != selected.proposal_context_bytes:
            raise ValueError("H1 reopened workspace differs from selected Prepare")
        return selected, closure

    def _selected_seal(self, raw: _RawFirstPath) -> H1SelectedSeal:
        """Bind the inventory reader to the exact selected physical seal command."""
        try:
            entry = _strict_json(raw.seal.raw)
            retained_raw = entry["execution_complete_seal"]
            envelope_raw = entry["execution_complete_seal_envelope"]
            if not isinstance(retained_raw, str) or not isinstance(envelope_raw, str):
                raise ValueError("missing retained complete seal")
            kind = _strict_json(retained_raw, canonical=False).get("kind")
            retained: RetainedExecutionCompleteSealV2 | RetainedExecutionCompleteSealV3
            envelope: (
                ExecutionCompleteSealPhysicalEnvelopeV2 | ExecutionCompleteSealPhysicalEnvelopeV3
            )
            if kind == "R14_SELECTED_EXECUTION_COMPLETE_SEAL_V2":
                if "h1_historical_checkpoint" in entry:
                    raise ValueError("V2 seal cannot carry a checkpoint")
                retained = RetainedExecutionCompleteSealV2.model_validate_json(retained_raw)
                envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(envelope_raw)
            elif kind == "R14_SELECTED_EXECUTION_COMPLETE_SEAL_V3":
                retained = RetainedExecutionCompleteSealV3.model_validate_json(retained_raw)
                envelope = ExecutionCompleteSealPhysicalEnvelopeV3.model_validate_json(envelope_raw)
                resolve_h1_checkpoint(
                    entry,
                    cast(AuthorityCheckpointStore, self._runtime._h1_checkpoint_store()),
                    self._runtime._database_identity,
                    raw.seal.command,
                )
            else:
                raise ValueError("unregistered retained complete seal profile")
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("H1 selected V2 seal decision is invalid") from error
        if (
            retained.canonical_bytes().decode() != retained_raw
            or envelope.canonical_bytes().decode() != envelope_raw
            or build_complete_seal_envelope(retained) != envelope
            or complete_seal_physical_command(envelope) != raw.seal.command
        ):
            raise ValueError("H1 selected V2 seal decision differs from physical command")
        return H1SelectedSeal(
            command=raw.seal.command,
            decision_id=raw.seal.decision_id,
            decision_bytes=raw.seal.raw,
            commit_sequence=raw.seal.command.expected_head + 1,
        )

    @staticmethod
    def _verify_native_source_inventory(
        lineage: tuple[tuple[_SelectedCommand, ExecutionRunRecord, PhysicalRecord], ...],
        seal_command: _SelectedCommand,
    ) -> None:
        """Reject selected native publications with hidden source members.

        The cut intentionally retains only the Run lineage, response seal, and
        selected registry.  A selected source command carrying another managed
        member cannot be silently omitted from that inventory.
        """
        for command, _, run in lineage:
            if command is seal_command:
                continue
            if command.command.records != (run,):
                raise ValueError("H1 selected Run publication has hidden physical members")
        if len(seal_command.command.records) != 3 or {
            member.schema_id for member in seal_command.command.records
        } != {EXECUTION_RUN_SCHEMA, SEAL_SCHEMA, RECOVERY_FRONTIER_REGISTRY_SCHEMA}:
            raise ValueError("H1 selected seal publication has hidden physical members")

    def _physical_member(
        self,
        connection: sqlite3.Connection | H1VerifiedSnapshotRows,
        selected: _SelectedCommand,
        member: PhysicalRecord,
    ) -> PhysicalRecord:
        if isinstance(connection, H1VerifiedSnapshotRows):
            try:
                return connection.physical_member(selected.command, member)
            except H1VerifiedSnapshotRowsError as error:
                raise ValueError("H1 selected publication is not exactly materialized") from error
        else:
            if (
                inspect_publication(
                    connection, selected.command, selected.command.expected_head + 1
                )
                != "COMPLETE"
            ):
                raise ValueError("H1 selected publication is not exactly materialized")
            row = connection.execute(
                "SELECT owner, schema_id, canonical_bytes, commit_sequence FROM records "
                "WHERE tenant_id=? AND record_id=?",
                (selected.command.tenant_id, member.record_id),
            ).fetchone()
        if row is None:
            raise ValueError("H1 selected physical record is absent")
        owner, schema_id, raw, sequence = row
        if (
            owner != member.owner
            or schema_id != member.schema_id
            or raw != member.canonical_bytes
            or sequence != selected.command.expected_head + 1
        ):
            raise ValueError("H1 selected physical record differs from selected publication")
        return PhysicalRecord(member.record_id, owner, schema_id, raw, _sha256(raw))

    @staticmethod
    def _source_member(
        selected: _SelectedCommand, record: PhysicalRecord
    ) -> H1FirstPathPhysicalMember:
        """Retain the exact physical locator without serializing producer authority."""
        return H1FirstPathPhysicalMember(
            decision_id=selected.decision_id,
            decision_fingerprint=_sha256(selected.raw),
            operation_kind=selected.command.operation_kind,
            publication_id=selected.command.idempotency_key,
            commit_sequence=selected.command.expected_head + 1,
            record_id=record.record_id,
            owner=record.owner,
            schema_id=record.schema_id,
            canonical_bytes=record.canonical_bytes,
        )
