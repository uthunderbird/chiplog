"""Historical, native-only selector for one installed H1 V2 completion seal.

The selector deliberately stops at the selected seal's authenticated loop
journal prefix.  Decisions committed afterwards therefore cannot alter the
returned evidence.  A pending decision anywhere in the installed journal is
ambiguous about the durable cut and is rejected conservatively.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from chiplog.adapters.driven.loop_sqlite import OWNER
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    ExecutionRunCanonicalMember,
    decode_execution_run_member,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import (
    DriveInputRequestV1,
    DriverCommandIdentityV1,
)
from chiplog.composition.h1_launch_enrollment import EnrolledH1RecoveryMount
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
    complete_seal_physical_command,
)
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
    RetainedInboxExecutionInitialization,
)
from chiplog.composition.r14_fanout_contracts import SEAL_SCHEMA
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.publication_readback import inspect_publication


class H1V2RecoveryNativeSourceError(ValueError):
    """The installed loop journal cannot prove the requested V2 native cut."""


@dataclass(frozen=True, slots=True)
class H1V2RecoveryRawDecision:
    decision_id: str
    decision_fingerprint: str
    raw_bytes: bytes


@dataclass(frozen=True, slots=True)
class H1V2RecoveryPhysicalMember:
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
class H1V2RecoverySourceFacts:
    tenant_id: str
    database_id: str
    selected_response_seal: CallSubjectHead
    complete_ordered_run_lineage: tuple[ExecutionRunRecord, ...]


@dataclass(frozen=True, slots=True)
class H1V2RecoveryNativeCut:
    """Raw fields intentionally shaped for the post-seal root constructor."""

    source: H1V2RecoverySourceFacts
    initialization: H1V2RecoveryRawDecision
    lineage: tuple[H1V2RecoveryRawDecision, ...]
    seal: H1V2RecoveryRawDecision
    physical_members: tuple[H1V2RecoveryPhysicalMember, ...]
    database_path: str
    database_device: int
    database_inode: int


@dataclass(frozen=True, slots=True)
class _Command:
    decision_id: str
    raw: bytes
    command: PhysicalPublicationCommand


def _strict_object(raw: bytes) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise H1V2RecoveryNativeSourceError("journal decision has duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise H1V2RecoveryNativeSourceError("journal decision is invalid") from error
    if (
        not isinstance(value, dict)
        or json.dumps(value, sort_keys=True, separators=(",", ":")).encode() != raw
    ):
        raise H1V2RecoveryNativeSourceError("journal decision is noncanonical")
    return value


class H1V2RecoveryNativeSource:
    """Reopen one V2 selected seal without a checkpoint or owner inventory."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("historical V2 selector requires the canonical installed runtime")
        mount = getattr(runtime, "_h1_recovery_mount", None)
        if type(mount) is not EnrolledH1RecoveryMount:
            raise H1V2RecoveryNativeSourceError("historical V2 selector requires an enrolled mount")
        gate = runtime._authority_gate()
        if mount.authority_gate is not gate:
            raise H1V2RecoveryNativeSourceError("historical V2 selector mount gate differs")
        self._runtime, self._mount, self._gate = runtime, mount, gate

    def select(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1V2RecoveryNativeCut:
        if type(original_identity) is not DriverCommandIdentityV1:
            raise TypeError("historical V2 selector requires the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise H1V2RecoveryNativeSourceError("original driver fingerprint is invalid")
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError(
                "historical V2 selector requires an exact selected response-seal locator"
            )
        try:
            with self._gate.hold():
                self._mount.assert_current()
                self._runtime._check_database_identity()
                result = self._select_held(original_identity, original_fingerprint, selected_seal)
                self._runtime._check_database_identity()
                self._mount.assert_current()
                return result
        except H1V2RecoveryNativeSourceError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as error:
            raise H1V2RecoveryNativeSourceError("installed historical V2 source differs") from error

    def _select_held(
        self,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1V2RecoveryNativeCut:
        self._gate.require_held()
        entries = self._runtime._loop_decisions().entries()
        decoded = [(decision_id, raw, _strict_object(raw)) for decision_id, _, raw in entries]
        if any(entry.get("kind") == "PENDING" for _, _, entry in decoded):
            raise H1V2RecoveryNativeSourceError(
                "pending later publication leaves historical cut ambiguous"
            )
        matches: list[int] = []
        for index, (decision_id, raw, entry) in enumerate(decoded):
            if (
                entry.get("kind") != "DECIDED"
                or entry.get("operation_kind") != EXECUTION_COMPLETE_SEAL_OPERATION
            ):
                continue
            # A later V3 seal is unrelated to the selected V2 prefix.  Locate
            # candidates from their native physical seal before decoding the
            # V2-only retained envelope of the selected candidate.
            candidate = _Command(decision_id, raw, self._runtime._publication(entry))
            if self._seal_locator(candidate) == selected_seal:
                matches.append(index)
        if len(matches) != 1:
            raise H1V2RecoveryNativeSourceError("historical V2 source has no unique selected seal")
        prefix = self._commands(decoded[: matches[0] + 1])
        seal_command = prefix[-1]
        self._require_v2_command(seal_command)
        initialization = self._initialization(prefix, original_identity, original_fingerprint)
        database = Path(self._runtime._database).resolve(strict=True)
        before = database.stat()
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        ) as connection:
            connection.execute("BEGIN")
            self._materialized_prefix(connection, prefix)
        after = database.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise H1V2RecoveryNativeSourceError("database identity changed during historical read")
        runs, run_members = self._lineage(prefix, initialization)
        if not runs or runs[-1][0].decision_id != seal_command.decision_id:
            raise H1V2RecoveryNativeSourceError("selected seal does not close selected Run lineage")
        # The final Run is already represented by ``run_members``.  Retain
        # only the two non-Run seal companions, in native command order.
        seal_tail = tuple(
            member
            for member in seal_command.command.records
            if member.schema_id != EXECUTION_RUN_SCHEMA
        )
        physical = (*run_members, *self._members(seal_command, only=seal_tail))
        return H1V2RecoveryNativeCut(
            source=H1V2RecoverySourceFacts(
                tenant_id=original_identity.tenant_id,
                database_id=original_identity.database_id,
                selected_response_seal=selected_seal,
                complete_ordered_run_lineage=tuple(run for _, run in runs),
            ),
            initialization=self._raw(initialization),
            lineage=tuple(self._raw(command) for command, _ in runs),
            seal=self._raw(seal_command),
            physical_members=physical,
            database_path=str(database),
            database_device=before.st_dev,
            database_inode=before.st_ino,
        )

    @staticmethod
    def _decode_v2_seal(raw: bytes) -> RetainedExecutionCompleteSealV2:
        entry = _strict_object(raw)
        if "h1_historical_checkpoint" in entry:
            raise H1V2RecoveryNativeSourceError("historical V2 selector rejects a checkpoint")
        try:
            retained_raw, envelope_raw = (
                entry["execution_complete_seal"],
                entry["execution_complete_seal_envelope"],
            )
            if not isinstance(retained_raw, str) or not isinstance(envelope_raw, str):
                raise TypeError
            retained = RetainedExecutionCompleteSealV2.model_validate_json(retained_raw)
            envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(envelope_raw)
        except (KeyError, TypeError, ValueError) as error:
            raise H1V2RecoveryNativeSourceError(
                "historical source requires exact V2 seal"
            ) from error
        if (
            retained.canonical_bytes().decode() != retained_raw
            or envelope.canonical_bytes().decode() != envelope_raw
            or build_complete_seal_envelope(retained) != envelope
        ):
            raise H1V2RecoveryNativeSourceError("V2 retained seal envelope differs")
        return retained

    def _commands(
        self, entries: list[tuple[str, bytes, dict[str, object]]]
    ) -> tuple[_Command, ...]:
        commands: list[_Command] = []
        for decision_id, raw, entry in entries:
            if entry.get("kind") != "DECIDED":
                continue
            command = self._runtime._publication(entry)
            if command.tenant_id != self._runtime._tenant_id:
                raise H1V2RecoveryNativeSourceError("historical prefix crosses tenants")
            commands.append(_Command(decision_id, raw, command))
        if not commands or len({item.command.expected_head for item in commands}) != len(commands):
            raise H1V2RecoveryNativeSourceError("historical prefix has competing publications")
        if tuple(sorted(commands, key=lambda item: item.command.expected_head)) != tuple(commands):
            raise H1V2RecoveryNativeSourceError("historical journal prefix ordering differs")
        return tuple(commands)

    @staticmethod
    def _require_v2_command(command: _Command) -> None:
        retained = H1V2RecoveryNativeSource._decode_v2_seal(command.raw)
        entry = _strict_object(command.raw)
        envelope_raw = entry.get("execution_complete_seal_envelope")
        if not isinstance(envelope_raw, str):
            raise H1V2RecoveryNativeSourceError("V2 retained seal envelope is absent")
        envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(envelope_raw)
        if complete_seal_physical_command(envelope) != command.command:
            raise H1V2RecoveryNativeSourceError(
                "V2 physical command differs from retained envelope"
            )
        if len(command.command.records) != 3 or {
            member.schema_id for member in command.command.records
        } != {EXECUTION_RUN_SCHEMA, SEAL_SCHEMA, RECOVERY_FRONTIER_REGISTRY_SCHEMA}:
            raise H1V2RecoveryNativeSourceError("selected V2 seal has hidden physical members")
        del retained

    @staticmethod
    def _seal_locator(command: _Command) -> CallSubjectHead:
        records = [member for member in command.command.records if member.schema_id == SEAL_SCHEMA]
        if len(records) != 1:
            raise H1V2RecoveryNativeSourceError(
                "complete seal command has no unique physical response seal"
            )
        record = records[0]
        try:
            seal = SealedResponseRecord.model_validate_json(record.canonical_bytes)
        except ValueError as error:
            raise H1V2RecoveryNativeSourceError("physical response seal is invalid") from error
        digest = hashlib.sha256(record.canonical_bytes).hexdigest()
        if (
            seal.canonical_bytes() != record.canonical_bytes
            or record.record_id != "record:" + digest
        ):
            raise H1V2RecoveryNativeSourceError("physical response seal differs")
        return CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        )

    @staticmethod
    def _initialization(
        commands: tuple[_Command, ...], identity: DriverCommandIdentityV1, fingerprint: str
    ) -> _Command:
        found: list[_Command] = []
        for command in commands:
            if command.command.operation_kind != EXECUTION_INBOX_INITIALIZATION_OPERATION:
                continue
            entry = _strict_object(command.raw)
            try:
                encoded = entry["inbox_initialization"]
                if not isinstance(encoded, str):
                    raise TypeError
                retained = RetainedInboxExecutionInitialization.model_validate_json(encoded)
                request = DriveInputRequestV1.model_validate_json(retained.driver_request_bytes)
            except (KeyError, TypeError, ValueError) as error:
                raise H1V2RecoveryNativeSourceError("historical initialization differs") from error
            if (
                retained.canonical_bytes().decode() != encoded
                or request.canonical_bytes() != retained.driver_request_bytes
            ):
                raise H1V2RecoveryNativeSourceError(
                    "historical initialization bytes are noncanonical"
                )
            if (
                request.identity == identity
                and retained.driver_request_fingerprint == fingerprint
                and request.original_driver_command_fingerprint() == fingerprint
            ):
                found.append(command)
        if len(found) != 1:
            raise H1V2RecoveryNativeSourceError(
                "historical source has no unique original initialization"
            )
        return found[0]

    @staticmethod
    def _materialized_prefix(
        connection: sqlite3.Connection, commands: tuple[_Command, ...]
    ) -> None:
        for command in commands:
            if (
                inspect_publication(connection, command.command, command.command.expected_head + 1)
                != "COMPLETE"
            ):
                raise H1V2RecoveryNativeSourceError(
                    "historical selected publication is not exactly materialized"
                )

    @staticmethod
    def _lineage(
        commands: tuple[_Command, ...], initialization: _Command
    ) -> tuple[
        tuple[tuple[_Command, ExecutionRunRecord], ...], tuple[H1V2RecoveryPhysicalMember, ...]
    ]:
        entry = _strict_object(initialization.raw)
        initialization_raw = entry.get("inbox_initialization")
        if not isinstance(initialization_raw, str):
            raise H1V2RecoveryNativeSourceError("historical initialization is absent")
        retained = RetainedInboxExecutionInitialization.model_validate_json(initialization_raw)
        root = retained.proposal.run
        runs: list[tuple[_Command, ExecutionRunRecord]] = []
        members: list[H1V2RecoveryPhysicalMember] = []
        for command in commands:
            for member in command.command.records:
                if member.owner != OWNER or member.schema_id != EXECUTION_RUN_SCHEMA:
                    continue
                try:
                    decoded = decode_execution_run_member(
                        ExecutionRunCanonicalMember(
                            record_id=member.record_id,
                            schema_id=member.schema_id,  # type: ignore[arg-type]
                            canonical_record_bytes=member.canonical_bytes,
                            fingerprint=hashlib.sha256(member.canonical_bytes).hexdigest(),
                        )
                    ).run
                except ValueError as error:
                    raise H1V2RecoveryNativeSourceError("historical Run member differs") from error
                if isinstance(decoded, ExecutionRunRecord) and decoded.run_id == root.run_id:
                    if (
                        command.command.records != (member,)
                        and command.command.operation_kind != EXECUTION_COMPLETE_SEAL_OPERATION
                    ):
                        raise H1V2RecoveryNativeSourceError(
                            "selected Run publication has hidden physical members"
                        )
                    runs.append((command, decoded))
                    members.extend(H1V2RecoveryNativeSource._members(command, only=(member,)))
        runs.sort(key=lambda item: item[0].command.expected_head)
        if not runs or runs[0][1] != root:
            raise H1V2RecoveryNativeSourceError(
                "historical Run lineage lacks original initialization"
            )
        for index, (_, run) in enumerate(runs):
            if (index == 0 and run.predecessor is not None) or (
                index and run.predecessor != runs[index - 1][1].head
            ):
                raise H1V2RecoveryNativeSourceError("historical Run lineage is not contiguous")
        return tuple(runs), tuple(members)

    @staticmethod
    def _raw(command: _Command) -> H1V2RecoveryRawDecision:
        return H1V2RecoveryRawDecision(
            command.decision_id, hashlib.sha256(command.raw).hexdigest(), command.raw
        )

    @staticmethod
    def _members(
        command: _Command, *, only: tuple[PhysicalRecord, ...] | None = None
    ) -> tuple[H1V2RecoveryPhysicalMember, ...]:
        records = command.command.records if only is None else only
        return tuple(
            H1V2RecoveryPhysicalMember(
                decision_id=command.decision_id,
                decision_fingerprint=hashlib.sha256(command.raw).hexdigest(),
                operation_kind=command.command.operation_kind,
                publication_id=command.command.idempotency_key,
                commit_sequence=command.command.expected_head + 1,
                record_id=member.record_id,
                owner=member.owner,
                schema_id=member.schema_id,
                canonical_bytes=member.canonical_bytes,
            )
            for member in records
        )


__all__ = [
    "H1V2RecoveryNativeCut",
    "H1V2RecoveryNativeSource",
    "H1V2RecoveryNativeSourceError",
]
