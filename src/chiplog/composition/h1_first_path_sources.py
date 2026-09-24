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

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from chiplog.adapters.driven.loop_sqlite import OWNER
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    FirstPathCompletionCutV2,
)
from chiplog.capabilities.agent_loop.execution_h1_frontier_profile_v2 import (
    H1WorkspaceClosure,
    derive_h1_frontier_profile_v2_members,
)
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    ExecutionRunCanonicalMember,
    decode_execution_run_member,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import PrepareExecutionRequest
from chiplog.capabilities.agent_loop.recovery_contracts import Present
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
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
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
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication


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
    """Read raw selected H0/Run/seal/registry inputs without issuing authority.

    This class is intentionally inert until an authoritative mapping for all
    recovery-frontier families is supplied.  It still validates the complete
    raw and physical candidate before failing closed, so a future profile cannot
    accidentally gain a fixture-derived source path.
    """

    def __init__(
        self,
        runtime: CommonCliExecutionRuntime,
        *,
        workspace_closure_resolver: H1WorkspaceClosureResolver | None = None,
    ) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 sources require the canonical common CLI runtime")
        self._runtime = runtime
        self._gate = runtime._authority_gate()
        self._workspace_closure_resolver = (
            workspace_closure_resolver or self._reopen_selected_workspace
        )
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
        raw = self._read_selected_cut(
            original_identity=original_identity,
            original_fingerprint=original_fingerprint,
            selected_seal=selected_seal,
        )
        if not raw.registry_is_v2:
            raise ValueError("H1 first-path V2 requires a genuinely selected V2 registry")
        self._derive_v2_frontier_members(
            raw, verified_workspace=self._resolve_workspace_closure(raw)
        )
        raise AssertionError("unreachable: missing workspace closure must fail closed")

    def check_current(self, capture: H1FirstPathCapture) -> bool:
        """Reject copied/unissued captures before any potentially stale reuse."""
        if not isinstance(capture, H1FirstPathCapture):
            return False
        return self._issued.get(id(capture)) is capture and False

    def validate_historical(
        self,
        source: FirstPathCompletionCutV2,
        *,
        initialization_envelope_bytes: bytes,
    ) -> None:
        """Reserved for raw historical replay once the family profile is frozen.

        A schema-valid DTO cannot stand in for independently selected raw bytes.
        Refusing it now is safer than using joined history or inventing family
        absences.  This method intentionally performs no call into history code.
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
        self._derive_v2_frontier_members(
            raw, verified_workspace=self._resolve_workspace_closure(raw)
        )
        raise AssertionError("unreachable: historical cut construction is not mounted")

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
            initialization = self._find_initialization(
                commands, original_identity, original_fingerprint
            )
            with closing(
                sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
            ) as connection:
                connection.execute("BEGIN")
                commitment = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
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
        connection: sqlite3.Connection,
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

    def _resolve_workspace_closure(self, raw: _RawFirstPath) -> H1WorkspaceClosure | None:
        """Pass only raw-selected V3 Prepare and exact W bytes to the broker port."""
        resolver = self._workspace_closure_resolver
        if resolver is None:
            return None
        matches: list[tuple[RetainedExecutionTransitionV3, bytes, bytes]] = []
        for command, run, _ in raw.lineage:
            entry = json.loads(command.raw)
            encoded = entry.get("execution_transition")
            if not isinstance(encoded, str):
                continue
            try:
                retained = RetainedExecutionTransitionV3.model_validate_json(encoded)
            except ValueError:
                continue
            if retained.canonical_bytes().decode() != encoded:
                raise ValueError("H1 selected V3 Prepare bytes are not canonical")
            if (
                not isinstance(retained.request, PrepareExecutionRequest)
                or retained.proposal.run != run
            ):
                continue
            workspace = tuple(
                member
                for member in retained.request.manifest.members
                if member.producer == "projections" and member.surface == "workspace"
            )
            if len(workspace) != 1:
                raise ValueError("H1 selected V3 Prepare lacks one workspace member")
            matches.append((retained, encoded.encode(), workspace[0].model_dump_json().encode()))
        if len(matches) != 1:
            raise ValueError("H1 raw cut has no unique selected V3 Prepare")
        retained, selected_bytes, workspace_bytes = matches[0]
        with self._gate.hold():
            closure = resolver(
                selected_prepare=retained,
                selected_prepare_bytes=selected_bytes,
                workspace_member_bytes=workspace_bytes,
            )
        if not isinstance(getattr(closure, "proposal_context_bytes", None), bytes):
            raise ValueError("H1 workspace closure resolver returned an invalid closure")
        return closure

    def _reopen_selected_workspace(
        self,
        *,
        selected_prepare: RetainedExecutionTransitionV3,
        selected_prepare_bytes: bytes,
        workspace_member_bytes: bytes,
    ) -> H1WorkspaceClosure:
        """Reopen the runtime-bound original issuance for raw-selected V3 only."""
        if selected_prepare.canonical_bytes() != selected_prepare_bytes:
            raise ValueError("H1 selected V3 Prepare bytes differ at workspace reopen")
        if not isinstance(selected_prepare.request, PrepareExecutionRequest):
            raise ValueError("H1 workspace reopen requires selected V3 Prepare")
        workspace = tuple(
            member
            for member in selected_prepare.request.manifest.members
            if member.producer == "projections" and member.surface == "workspace"
        )
        if len(workspace) != 1 or workspace[0].model_dump_json().encode() != workspace_member_bytes:
            raise ValueError("H1 selected V3 Prepare workspace bytes differ at reopen")
        # These journal paths derive solely from the canonical runtime database
        # and gate.  Neither a caller path nor an authority token enters here.
        from chiplog.composition.r13_workspace import R13Workspace
        from chiplog.composition.r14_h1_workspace_sources import reopen_h1_original_workspace

        workspace_port = R13Workspace(self._runtime)
        return reopen_h1_original_workspace(
            selected_prepare.workspace_issuance,
            workspace_member_bytes,
            workspace[0].content.encode(),
            workspace_port.open_h1_workspace_issuance(),
            workspace_port.open_dashboard_issuance(),
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
        connection: sqlite3.Connection,
        selected: _SelectedCommand,
        member: PhysicalRecord,
    ) -> PhysicalRecord:
        if (
            inspect_publication(connection, selected.command, selected.command.expected_head + 1)
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
