"""Installed, fail-closed owner of the H1 completion predecessor read plan."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from chiplog.composition.h1_completion_readplan_registry import current_registry
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    ExactRecordHead,
    ObservedAbsence,
    ObservedPresence,
)


class H1ReadPlanSourceUnavailable(ValueError):
    """The installed H1 source cannot prove one current predecessor cut."""


class _H1ReadPlanCapture:
    """Unserializable, source-owned and one-use predecessor evidence."""

    __slots__ = (
        "_checkpoint",
        "_consumed",
        "_heads",
        "_issued",
        "_manifest",
        "_owner_head",
        "_registry_bytes",
        "_run_id",
        "_session",
        "_tenant_id",
    )
    _session: object
    _checkpoint: object
    _owner_head: str | None
    _registry_bytes: bytes
    _manifest: AuthoritativeReadManifest
    _tenant_id: str
    _run_id: str
    _heads: tuple[ExactRecordHead, ...]
    _issued: bool
    _consumed: bool

    def __init__(self) -> None:
        raise TypeError("H1 read-plan captures are source-issued")

    def __copy__(self) -> _H1ReadPlanCapture:
        raise TypeError("H1 read-plan captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> _H1ReadPlanCapture:
        del memo
        raise TypeError("H1 read-plan captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 read-plan captures cannot be serialized")

    @property
    def predecessor_checkpoint(self) -> object:
        return self._checkpoint

    @property
    def predecessor_owner_head(self) -> str | None:
        return self._owner_head

    @property
    def registry_bytes(self) -> bytes:
        return self._registry_bytes

    @property
    def manifest(self) -> AuthoritativeReadManifest:
        return self._manifest


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _manifest_fingerprint(
    tenant_id: str,
    frontier: int,
    commitment: str,
    heads: tuple[ObservedPresence | ObservedAbsence, ...],
) -> str:
    registry = current_registry()
    return hashlib.sha256(
        _canonical(
            {
                "expected_materialization_commitment": commitment,
                "ordered_heads": [item.model_dump(mode="json") for item in heads],
                "registry_fingerprint": registry.fingerprint,
                "registry_head": registry.head,
                "tenant_frontier": frontier,
                "tenant_id": tenant_id,
            }
        )
    ).hexdigest()


class H1LiveReadPlanSource:
    """Resolve four closed roles only from the mounted recovery/session owner."""

    __slots__ = ("_captures", "_closed", "_runtime")

    def __init__(self, runtime: object) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 read-plan source requires the canonical installed runtime")
        self._runtime = runtime
        self._captures: dict[int, _H1ReadPlanCapture] = {}
        self._closed = False

    def _revoke_all(self) -> None:
        self._closed = True
        self._captures.clear()

    def _require_mounted(self) -> None:
        if self._closed or getattr(self._runtime, "_h1_live_readplan_source", None) is not self:
            raise H1ReadPlanSourceUnavailable("live H1 completion read-plan source is not mounted")

    def _recovery_record(self, session: object) -> Any:
        """Authenticate the exact enrolled finalization session, not caller data."""
        from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionEnrollment
        from chiplog.composition.h1_postseal_recovery_coordinator import (
            _H1PostSealRecoveryCoordinator,
        )

        enrollment = getattr(self._runtime, "_h1_live_completion_enrollment", None)
        coordinator = getattr(self._runtime, "_h1_postseal_recovery_coordinator", None)
        if (
            type(enrollment) is not _H1LiveCompletionEnrollment
            or type(coordinator) is not _H1PostSealRecoveryCoordinator
        ):
            raise H1ReadPlanSourceUnavailable("H1 completion recovery enrollment is not mounted")
        record: Any = enrollment._require_recovery_record(session)
        if record.preflight is None:
            raise H1ReadPlanSourceUnavailable("H1 completion session has no finalization preflight")
        enrollment._require_recovery_record_current(record)
        coordinator._require_complete_chain_preflight_held(record.lease, record.preflight)
        return record

    def _admission_recovery_record(self, session: object) -> Any:
        """Authenticate the same recovery continuation from the writer thread.

        The writer has no owning asyncio task.  It therefore checks the lease's
        thread-safe admission liveness rather than the coordinator's task-bound
        ``require_owned`` path.
        """
        from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionEnrollment
        from chiplog.composition.h1_postseal_recovery_coordinator import (
            _H1CompleteChainPreflight,
            _H1PostSealRecoveryCoordinator,
        )
        from chiplog.composition.h1_recovery_stage_source import H1RecoveryStageSource

        enrollment = getattr(self._runtime, "_h1_live_completion_enrollment", None)
        coordinator = getattr(self._runtime, "_h1_postseal_recovery_coordinator", None)
        if (
            type(enrollment) is not _H1LiveCompletionEnrollment
            or type(coordinator) is not _H1PostSealRecoveryCoordinator
        ):
            raise H1ReadPlanSourceUnavailable("H1 completion recovery enrollment is not mounted")
        record: Any = enrollment._require_recovery_record(session)
        preflight = record.preflight
        if (
            type(record.source) is not H1RecoveryStageSource
            or type(preflight) is not _H1CompleteChainPreflight
            or preflight._coordinator is not coordinator
            or preflight._source is not record.source
            or preflight._lease is not record.lease
            or preflight._context is not record.context
            or preflight._retired
        ):
            raise H1ReadPlanSourceUnavailable("H1 completion finalization preflight differs")
        record.lease._require_admission_current()
        record.source._context_state(record.context)
        record.source._require_current(record.context)
        return record

    @staticmethod
    def _member(
        members: tuple[object, ...], decision_id: str, schema_id: str, kind: str, subject: str
    ) -> ExactRecordHead:
        matches = [
            member
            for member in members
            if getattr(member, "decision_id", None) == decision_id
            and getattr(member, "owner", None) == "agent_loop"
            and getattr(member, "schema_id", None) == schema_id
        ]
        if len(matches) != 1:
            raise H1ReadPlanSourceUnavailable("H1 read-plan physical member is absent or ambiguous")
        member = matches[0]
        raw = getattr(member, "canonical_bytes", None)
        record_id = getattr(member, "record_id", None)
        if type(raw) is not bytes or type(record_id) is not str:
            raise H1ReadPlanSourceUnavailable("H1 read-plan physical member differs")
        return ExactRecordHead(
            owner="agent_loop",
            record_kind=kind,
            subject_id=subject,
            record_id=record_id,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        )

    def _observe_held(
        self, session: object, *, admission: bool = False
    ) -> tuple[str, str, str | None, int, str, tuple[ExactRecordHead, ...]]:
        """Read every selector and all competing H1 completion decisions under one gate."""
        from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
        from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
        )
        from chiplog.composition.h1_completion_issuance import decode_h1_completion_issuance
        from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
        from chiplog.composition.r14_fanout_contracts import SEAL_SCHEMA
        from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
        from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
        from chiplog.platform.workspace_snapshot import read_connection

        self._require_mounted()
        self._runtime._check_database_identity()
        self._runtime._require_no_pending()
        record = (
            self._admission_recovery_record(session)
            if admission
            else self._recovery_record(session)
        )
        state = record.source._context_state(record.context)
        native = state.native
        cut, run = native.native_cut, native.facts.run
        if not isinstance(run, ExecutionRunRecord) or not run.run_id:
            raise H1ReadPlanSourceUnavailable("H1 read-plan native Run facts differ")
        selected_seal = native.selected_seal
        heads = (
            self._member(
                cut.physical_members,
                cut.seal.decision_id,
                EXECUTION_RUN_SCHEMA,
                "Run",
                run.run_id,
            ),
            self._member(
                cut.physical_members,
                cut.seal.decision_id,
                SEAL_SCHEMA,
                "ResponseSeal",
                selected_seal.subject_id,
            ),
            self._member(
                cut.physical_members,
                cut.seal.decision_id,
                RECOVERY_FRONTIER_REGISTRY_SCHEMA,
                "RecoveryFrontierRegistry",
                selected_seal.subject_id,
            ),
        )
        snapshot = self._runtime._owner_decisions().snapshot()
        for decision in snapshot.decisions:
            batch = decision.prepared.request
            if type(batch) is not CompleteDeliveryBatchV2:
                continue
            try:
                issuance = decode_h1_completion_issuance(batch)
            except (TypeError, ValueError) as error:
                raise H1ReadPlanSourceUnavailable(
                    "H1 completion selection cannot be decoded"
                ) from error
            if issuance.assembly.original_completion_request.run.run_id == run.run_id:
                raise H1ReadPlanSourceUnavailable(
                    "H1 completion already exists for the selected Run"
                )
        with read_connection(self._runtime._database) as connection:
            commitment = capture_authority_snapshot_commitment(connection, cut.source.tenant_id)
            row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (cut.source.tenant_id,)
            ).fetchone()
        if row is None or len(row) != 1 or type(row[0]) is not int:
            raise H1ReadPlanSourceUnavailable("H1 read-plan tenant frontier is unavailable")
        self._runtime._check_database_identity()
        self._runtime._require_no_pending()
        if admission:
            self._admission_recovery_record(session)
        else:
            self._recovery_record(session)
        return cut.source.tenant_id, run.run_id, snapshot.head, row[0], commitment, heads

    def capture_predecessor(self, *, session: object) -> _H1ReadPlanCapture:
        """Stage the full authority preimage immediately before the owner append."""
        from chiplog.platform.authority_reads import capture_authority_snapshot_bytes
        from chiplog.platform.workspace_snapshot import read_connection

        try:
            with self._runtime._authority_gate().hold():
                observed = self._observe_held(session)
                tenant_id, run_id, owner_head, frontier, commitment, heads = observed
                with read_connection(self._runtime._database) as connection:
                    preimage = capture_authority_snapshot_bytes(connection, tenant_id)
                if hashlib.sha256(preimage).hexdigest() != commitment:
                    raise H1ReadPlanSourceUnavailable(
                        "H1 predecessor checkpoint commitment differs from its read cut"
                    )
                checkpoint = cast(Any, self._runtime._h1_checkpoint_store()).stage_verified(
                    preimage
                )
                if getattr(checkpoint, "blob_sha256", None) != commitment:
                    raise H1ReadPlanSourceUnavailable(
                        "H1 predecessor checkpoint reference differs from its read cut"
                    )
                if self._observe_held(session) != observed:
                    raise H1ReadPlanSourceUnavailable(
                        "H1 predecessor changed while checkpoint staged"
                    )
                registry = current_registry()
                ordered_heads = (
                    ObservedPresence(head=heads[0]),
                    ObservedPresence(head=heads[1]),
                    ObservedPresence(head=heads[2]),
                    ObservedAbsence(
                        owner="agent_loop", record_kind="RunCompletion", subject_id=run_id
                    ),
                )
                manifest = AuthoritativeReadManifest(
                    tenant_id=tenant_id,
                    tenant_frontier=frontier,
                    expected_materialization_commitment=commitment,
                    registry_head=registry.head,
                    registry_fingerprint=registry.fingerprint,
                    ordered_heads=ordered_heads,
                    complete_manifest_fingerprint=_manifest_fingerprint(
                        tenant_id, frontier, commitment, ordered_heads
                    ),
                )
                capture = object.__new__(_H1ReadPlanCapture)
                capture._session = session
                capture._checkpoint = checkpoint
                capture._owner_head = owner_head
                capture._registry_bytes = registry.canonical_bytes
                capture._manifest = manifest
                capture._tenant_id = tenant_id
                capture._run_id = run_id
                capture._heads = heads
                capture._issued = False
                capture._consumed = False
                self._captures[id(capture)] = capture
                return capture
        except H1ReadPlanSourceUnavailable:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1ReadPlanSourceUnavailable(
                "H1 predecessor read-plan capture is unavailable"
            ) from error

    def _require_capture(self, capture: object) -> _H1ReadPlanCapture:
        stored = self._captures.get(id(capture))
        if type(capture) is not _H1ReadPlanCapture or stored is not capture or capture._consumed:
            raise H1ReadPlanSourceUnavailable(
                "H1 read-plan capture is foreign, consumed, or revoked"
            )
        return capture

    def _require_issued_capture(self, capture: object, session: object) -> _H1ReadPlanCapture:
        stored = self._captures.get(id(capture))
        if (
            type(capture) is not _H1ReadPlanCapture
            or stored is not capture
            or capture._session is not session
            or not capture._consumed
            or not capture._issued
        ):
            raise H1ReadPlanSourceUnavailable(
                "H1 read-plan capture was not exactly issued for this session"
            )
        return capture

    @staticmethod
    def _manifest_matches(
        capture: _H1ReadPlanCapture,
        observed: tuple[str, str, str | None, int, str, tuple[ExactRecordHead, ...]],
    ) -> bool:
        tenant, run, owner, frontier, commitment, heads = observed
        registry = current_registry()
        ordered_heads = (
            ObservedPresence(head=heads[0]),
            ObservedPresence(head=heads[1]),
            ObservedPresence(head=heads[2]),
            ObservedAbsence(owner="agent_loop", record_kind="RunCompletion", subject_id=run),
        )
        manifest = capture._manifest
        return (
            (tenant, run, owner, frontier, commitment, heads)
            == (
                capture._tenant_id,
                capture._run_id,
                capture._owner_head,
                manifest.tenant_frontier,
                manifest.expected_materialization_commitment,
                capture._heads,
            )
            and registry.canonical_bytes == capture._registry_bytes
            and manifest.registry_head == registry.head
            and manifest.registry_fingerprint == registry.fingerprint
            and manifest.ordered_heads == ordered_heads
            and manifest.complete_manifest_fingerprint
            == _manifest_fingerprint(tenant, frontier, commitment, ordered_heads)
        )

    def check_current(self, *, capture: object) -> bool:
        try:
            with self._runtime._authority_gate().hold():
                self._require_mounted()
                stored = self._require_capture(capture)
                return self._manifest_matches(stored, self._observe_held(stored._session))
        except H1ReadPlanSourceUnavailable:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1ReadPlanSourceUnavailable(
                "H1 read-plan currentness check is unavailable"
            ) from error

    def issue_manifest(self, *, capture: object) -> AuthoritativeReadManifest:
        with self._runtime._authority_gate().hold():
            self._require_mounted()
            stored = self._require_capture(capture)
            if not self.check_current(capture=stored):
                raise H1ReadPlanSourceUnavailable("H1 read-plan capture is no longer current")
            stored._consumed = True
            stored._issued = True
            return stored._manifest

    def _admit_issued_manifest(
        self, *, capture: object, session: object
    ) -> AuthoritativeReadManifest:
        """Re-admit exactly one issued manifest from the non-task-affine writer thread."""
        try:
            with self._runtime._authority_gate().hold():
                self._require_mounted()
                stored = self._require_issued_capture(capture, session)
                if not self._manifest_matches(stored, self._observe_held(session, admission=True)):
                    raise H1ReadPlanSourceUnavailable(
                        "H1 issued read-plan manifest is no longer current"
                    )
                return stored._manifest
        except H1ReadPlanSourceUnavailable:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1ReadPlanSourceUnavailable(
                "H1 issued read-plan writer admission is unavailable"
            ) from error
