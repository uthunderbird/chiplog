"""Actual hermetic retained PRE_AUTH custody; no admission, authentication or ACK."""

from __future__ import annotations

import hmac
import json
import os
import stat
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from chiplog.adapters.driven.ingress_retained_source import (
    RetainedSourceAdapter,
    RetainedSourceObservation,
)
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R17_CUSTODY_PRODUCTION_MANIFEST
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r17_ingress_authority import IngressAuthority
from chiplog.composition.r17_ingress_history import (
    IngressHistory,
    is_ingress,
    read_ingress_history,
)
from chiplog.composition.r17_ingress_registry import retained_cli_profile
from chiplog.platform._ingress_contracts import Head, ReceiptToken, SourceBinding, UnknownEndpoint
from chiplog.platform._owner_publication_contracts import (
    BrokerPublicationResult,
    ExactReplayQuery,
    JournalSelectedPublication,
    NoSelectedDecision,
    PublicationRejected,
    SingleOwnerBatch,
)
from chiplog.platform.ingress_authenticated_contracts import AdmittedInboxObservation
from chiplog.platform.ingress_custody_records import (
    CustodyCommand,
    CustodyProfile,
    allocation_fingerprint,
    canonical,
    subject_id,
)
from chiplog.platform.owner_publications import BrokerPublicationCoordinator

if TYPE_CHECKING:
    from chiplog.composition.r17_authenticated_custody import AuthenticatedIngressAuthority

_SOURCE_SECRET = b"r17-hermetic-retained-source-only-v1"
_Operation = Literal[
    "ingress.allocate_receipt_token",
    "ingress.stage_raw_bytes",
    "ingress.publish_custody_successor",
]


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


@contextmanager
def _descriptor(fd: int) -> Iterator[int]:
    try:
        yield fd
    finally:
        os.close(fd)


class R17IngressRuntime(R14PlanningRuntime):
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = (
        *R14PlanningRuntime._record_schema_variants,
        ("broker_ingress", "chiplog.ingress.authenticated-record.v2"),
    )
    _record_contracts: ClassVar[dict[str, str]] = {
        **R14PlanningRuntime._record_contracts,
        "broker_ingress": "chiplog.ingress.custody-record.v1",
    }

    def _bind_appender(self) -> None:
        super()._bind_appender()
        self._source_adapter: RetainedSourceAdapter | None = None

    def _ingress_profile(self) -> CustodyProfile:
        return retained_cli_profile(self._tenant_id, "hermetic-database")

    def _source_paths(self) -> tuple[Path, Path]:
        return (
            self._database.with_suffix(self._database.suffix + ".ingress-source"),
            self._database.with_suffix(self._database.suffix + ".ingress-source-root"),
        )

    def _source_sentinel(self, directory: Path, sentinel: Path, *, create: bool) -> None:
        profile = self._ingress_profile()
        value = directory.stat()
        body = {
            "schema": "chiplog.ingress.retained-root.v1",
            "path": str(directory),
            "device": value.st_dev,
            "inode": value.st_ino,
            "profile": json.loads(canonical(profile)),
        }
        payload = _json(
            {"value": body, "signature": hmac.digest(_SOURCE_SECRET, _json(body), "sha256").hex()}
        )
        if create:
            with _descriptor(
                os.open(sentinel, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            ) as fd:
                if os.write(fd, payload) != len(payload):
                    raise ValueError("partial retained source root sentinel")
                os.fsync(fd)
            with _descriptor(os.open(sentinel.parent, os.O_RDONLY | os.O_DIRECTORY)) as parent:
                os.fsync(parent)
        with _descriptor(os.open(sentinel, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as fd:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size != len(payload)
                or os.read(fd, len(payload) + 1) != payload
                or _file_identity(os.fstat(fd)) != _file_identity(before)
                or _file_identity(os.stat(sentinel, follow_symlinks=False))
                != _file_identity(before)
            ):
                raise ValueError("retained source root sentinel differs")

    def _retained_source(self) -> RetainedSourceAdapter:
        with self._authority_gate().hold():
            self._check_database_identity()
            directory, sentinel = self._source_paths()
            sentinel_present = sentinel.exists() or sentinel.is_symlink()
            if self._source_adapter is not None:
                if not sentinel_present:
                    raise ValueError("retained source root sentinel vanished")
                self._source_sentinel(directory, sentinel, create=False)
                return self._source_adapter
            selected = self._owner_decisions().snapshot()
            if not sentinel_present and any(is_ingress(item) for item in selected.decisions):
                raise ValueError("selected custody lost its retained source root sentinel")
            if sentinel_present:
                self._source_sentinel(directory, sentinel, create=False)
            profile = self._ingress_profile()
            adapter = RetainedSourceAdapter(
                directory,
                profile.tenant_id,
                profile.database_id,
                secret=_SOURCE_SECRET,
                allow_create=not sentinel_present,
                maximum_items=profile.maximum_items,
                maximum_total_bytes=profile.maximum_total_bytes,
                maximum_item_bytes=profile.maximum_item_bytes,
                token_validator=self._materialized_token,
            )
            self._source_sentinel(directory, sentinel, create=not sentinel_present)
            self._source_adapter = adapter
            return adapter

    def provision_retained(self, slot_id: str, raw: bytes) -> None:
        """Source-side provisioning only; publishes no token or authenticated input."""
        with self._authority_gate().hold():
            self._retained_source().provision(slot_id, raw)

    def _materialized_token(self, token_id: str, observed: RetainedSourceObservation) -> bool:
        with self._authority_gate().hold():
            history = read_ingress_history(self)
            allocations = [
                item.command
                for item in history.records
                if item.command.operation == "ingress.allocate_receipt_token"
                and item.command.token.token_id == token_id
            ]
            return len(allocations) == 1 and (
                allocations[0].retention.observation_bytes == observed.canonical_bytes()
                and allocations[0].retention.proof == observed.proof
                and allocations[0].token.receive_slot == observed.slot_id
            )

    @asynccontextmanager
    async def cli_custody(self, slot_id: str) -> AsyncIterator[AuthenticatedIngressAuthority]:
        """Open one real-peer control exchange for already retained staged bytes."""
        from chiplog.composition.r17_authenticated_custody import AuthenticatedIngressAuthority

        authority = AuthenticatedIngressAuthority(self, slot_id)
        async with authority.socket:
            yield authority

    def read_admitted_inbox(self, token_id: str) -> AdmittedInboxObservation | None:
        from chiplog.composition.r17_ingress_history import record_head
        from chiplog.platform.ingress_authenticated_contracts import (
            AdmittedInboxObservation,
            AuthenticatedCustodyRecord,
        )

        history = self.ingress_history()
        matches = [
            r
            for r in history.records
            if isinstance(r, AuthenticatedCustodyRecord) and r.command.token.token_id == token_id
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("multiple selected admitted inboxes")
        record = matches[0]
        selected = self._owner_decisions().lookup(self._tenant_id, record.command.command_id)
        if selected is None:
            raise ValueError("selected inbox decision disappeared")
        return AdmittedInboxObservation(
            selected_decision=Head(
                identity=selected.decision_id,
                head=selected.decision_head,
                fingerprint=selected.decision_fingerprint,
            ),
            physical_record=record_head(record),
            commit_sequence=selected.tenant_commit_sequence,
            record=record,
        )

    def ingress_history(self) -> IngressHistory:
        return read_ingress_history(self)

    async def _prepare_startup(self) -> None:
        # Historical bytes must validate before R14 may submit recovery writes.
        read_ingress_history(self, allow_pending=True)
        await super()._prepare_startup()
        read_ingress_history(self)

    async def _custody_operation(
        self,
        operation: _Operation,
        slot_id: str,
        maximum_bytes: int | None = None,
    ) -> BrokerPublicationResult:
        if maximum_bytes is not None and (type(maximum_bytes) is not int or maximum_bytes <= 0):
            raise ValueError("retained token maximum must be a positive integer")
        authority = IngressAuthority(self, slot_id)
        journal = self._owner_decisions()
        coordinator = BrokerPublicationCoordinator(self._appender, authority, journal)
        profile = self._ingress_profile()
        command_id = subject_id(profile, slot_id, operation)
        with self._authority_gate().hold():
            read_ingress_history(self, allow_pending=True)
            selected = journal.lookup(self._tenant_id, command_id)
            if selected is not None:
                batch = selected.prepared.request
                if not isinstance(batch, SingleOwnerBatch):
                    raise ValueError("selected retained custody has another batch kind")
                if batch.command.schema_id != "chiplog.ingress.retained-command.v1":
                    return PublicationRejected(
                        kind="CONFLICT",
                        tenant_id=self._tenant_id,
                        command_id=command_id,
                        reason="receipt already has another immutable successor",
                    )
                original = CustodyCommand.model_validate_json(batch.command.canonical_bytes)
                if (
                    original.operation != operation
                    or original.retention != authority.claim
                    or original.profile != profile
                    or (maximum_bytes is not None and original.token.maximum_bytes != maximum_bytes)
                ):
                    return PublicationRejected(
                        kind="CONFLICT",
                        tenant_id=self._tenant_id,
                        command_id=command_id,
                        reason="retained receipt identity already names different immutable inputs",
                    )
                proof = authority.invocation(batch.identity, batch.command)
                replay = coordinator.lookup_exact(
                    ExactReplayQuery(
                        identity=batch.identity,
                        operation=operation,
                        current_invocation=proof,
                        original_commands=(batch.command,),
                    )
                )
                if isinstance(replay, PublicationRejected) and replay.kind != "HOLD":
                    return replay
                if isinstance(replay, NoSelectedDecision):
                    raise ValueError("selected retained custody disappeared")
                if authority.materialization_state(selected) != "ABSENT":
                    return replay
        if selected is not None:
            return await coordinator.recover_selected(self._tenant_id, command_id)

        captured = authority.capture()
        if operation == "ingress.allocate_receipt_token":
            if (
                type(maximum_bytes) is not int
                or not 0 < maximum_bytes <= profile.maximum_item_bytes
            ):
                raise ValueError("invalid retained token maximum bytes")
            token = ReceiptToken(
                token_id=subject_id(profile, slot_id, "ingress.receipt"),
                source=SourceBinding(
                    manifest_row=profile.head(),
                    source_class="CLI",
                    tenant_id=profile.tenant_id,
                    database_id=profile.database_id,
                    source_identity=profile.source_identity,
                    endpoint_account_binding=UnknownEndpoint(),
                    broker_epoch=str(captured.session.broker_epoch),
                    broker_session=captured.session.session_id,
                    admission_epoch=profile.epoch(),
                    admission_fence=0,
                    transport_version=profile.transport_version,
                ),
                receive_slot=slot_id,
                maximum_bytes=maximum_bytes,
                predecessor=None,
                command_fingerprint=allocation_fingerprint(profile, authority.claim, maximum_bytes),
            )
        else:
            allocations = [
                record.command
                for record in captured.history.records
                if record.command.operation == "ingress.allocate_receipt_token"
                and record.command.retention.slot_id == slot_id
            ]
            if len(allocations) != 1 or allocations[0].retention != authority.claim:
                raise ValueError("retained receipt lacks exact independently selected allocation")
            token = allocations[0].token
        raw = None
        if operation == "ingress.stage_raw_bytes":
            entries = [entry for entry in captured.history.custody.entries if entry.token == token]
            if len(entries) != 1 or entries[0].custody is not None:
                raise ValueError("retained receipt cannot transfer after custody successor")
            raw = authority.source.read(authority.observed, token.token_id)
        batch = authority.issue(operation, token, raw)
        return await coordinator.commit(batch)

    async def allocate_receipt(
        self, slot_id: str, maximum_bytes: int = 65536
    ) -> BrokerPublicationResult:
        return await self._custody_operation(
            "ingress.allocate_receipt_token", slot_id, maximum_bytes
        )

    async def stage_receipt(self, slot_id: str) -> BrokerPublicationResult:
        return await self._custody_operation("ingress.stage_raw_bytes", slot_id)

    async def retry_receipt(self, slot_id: str) -> BrokerPublicationResult:
        return await self._custody_operation("ingress.publish_custody_successor", slot_id)

    async def receive_retained(
        self, slot_id: str, maximum_bytes: int = 65536
    ) -> BrokerPublicationResult:
        for operation in ("allocate", "stage", "retry"):
            result = (
                await self.allocate_receipt(slot_id, maximum_bytes)
                if operation == "allocate"
                else await self.stage_receipt(slot_id)
                if operation == "stage"
                else await self.retry_receipt(slot_id)
            )
            if not isinstance(result, JournalSelectedPublication):
                return result
        return result


@asynccontextmanager
async def open_r17_runtime(
    database: Path, *, model: HermeticModel | None = None
) -> AsyncIterator[R17IngressRuntime]:
    async with _open_runtime(
        database,
        tenant_id="hermetic-tenant",
        operator_secret=b"r13-hermetic-only",
        runtime_type=R17IngressRuntime,
        manifest=R17_CUSTODY_PRODUCTION_MANIFEST,
        extra_leaves={"model": model if model is not None else HermeticModel()},
    ) as opened:
        runtime = cast(R17IngressRuntime, opened)
        if runtime._trust.verify() is None:
            await runtime.bootstrap(
                database_instance_id="hermetic-database",
                principal_id="hermetic-principal",
                credential_id="hermetic-credential",
                session_id="hermetic-session",
                token="hermetic-bootstrap",
            )
        yield runtime
