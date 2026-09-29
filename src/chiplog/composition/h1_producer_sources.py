"""Authenticated physical H1 producer sources for the effects-history slice.

This reader owns only the selected effects history and the generation derived
from it.  It does not manufacture the remainder of the H1 producer inventory
or an owner request.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.h1_normative_conflict_generation import (
    H1EffectsHistoryMemberV1,
    H1EffectsHistoryV1,
    H1NormativeConflictGenerationV1,
    h1_effects_history_capture,
    h1_normative_conflict_generation,
    h1_normative_conflict_generation_capture,
)
from chiplog.composition.h1_producer_history import (
    H1ProducerEffectsHistory,
    interpret_h1_producer_effects_history,
)
from chiplog.composition.h1_scoped_delivery_authority import (
    H1ScopedDeliveryAuthorityCapture,
    H1ScopedDeliveryAuthorityReader,
    H1ScopedDeliveryAuthorityViolation,
)
from chiplog.composition.r16_effects import _read_materialized_effects_with_history

if TYPE_CHECKING:
    from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.r16_effects import MaterializedEffectsCut
    from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot


_CLOCK_CONTRACT = "chiplog.dispatch.monotonic.v2"
_OBSERVATION_HORIZON_NS = 60_000_000_000


class H1ProducerSourceViolation(PermissionError):
    """The installed physical history reader cannot issue or retain this cut."""


@dataclass(frozen=True, slots=True)
class _SourceRecord:
    authority_capture: H1ScopedDeliveryAuthorityCapture
    intent_id: str
    physical_cut: MaterializedEffectsCut
    physical_cut_head: ExactHead
    owner_snapshot: OwnerJournalSnapshot
    history: H1EffectsHistoryV1
    generation: H1NormativeConflictGenerationV1
    effects_history: CapturedSource
    normative_conflict_generation: CapturedSource
    clock_epoch: str
    observed_time_ns: int
    valid_until_ns: int


class H1ProducerSourceCapture:
    """Identity-held result issued by :class:`H1ProducerSourceReader`."""

    __slots__ = ("_record",)
    _record: _SourceRecord

    def __init__(self) -> None:
        raise TypeError("H1 producer source captures are reader-issued")

    def __copy__(self) -> H1ProducerSourceCapture:
        raise TypeError("H1 producer source captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ProducerSourceCapture:
        del memo
        raise TypeError("H1 producer source captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 producer source captures cannot be serialized")

    @property
    def history(self) -> H1EffectsHistoryV1:
        return self._record.history

    @property
    def generation(self) -> H1NormativeConflictGenerationV1:
        return self._record.generation

    @property
    def physical_cut(self) -> ExactHead:
        """Authenticated physical-cut observation; it confers no SEND authority."""
        return self._record.physical_cut_head

    @property
    def effects_history(self) -> CapturedSource:
        return self._record.effects_history

    @property
    def normative_conflict_generation(self) -> CapturedSource:
        return self._record.normative_conflict_generation

    @property
    def clock(self) -> tuple[str, int, int]:
        return (
            self._record.clock_epoch,
            self._record.observed_time_ns,
            self._record.valid_until_ns,
        )


class H1ProducerSourceReader:
    """Issue authenticated H1 effects-history sources from the mounted runtime.

    ``H1ScopedDeliveryAuthorityCapture`` deliberately has no public issuer
    reference.  The authority reader is therefore an explicit constructor
    dependency, allowing this reader to inspect its held issuance registry
    without weakening the authority capture into a caller-assembled DTO.
    """

    __slots__ = ("_authority_reader", "_closed", "_issued", "_runtime")

    def __init__(
        self, runtime: CommonCliExecutionRuntime, authority_reader: H1ScopedDeliveryAuthorityReader
    ) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 producer sources require the installed common CLI runtime")
        if (
            type(authority_reader) is not H1ScopedDeliveryAuthorityReader
            or authority_reader._runtime is not runtime
        ):
            raise TypeError("H1 producer sources require their runtime's scoped authority reader")
        self._runtime = runtime
        self._authority_reader = authority_reader
        self._issued: dict[int, tuple[H1ProducerSourceCapture, _SourceRecord]] = {}
        self._closed = False

    async def read(
        self, *, authority_capture: H1ScopedDeliveryAuthorityCapture, intent_id: str
    ) -> H1ProducerSourceCapture:
        """Read, interpret, and retain the one current physical effects cut."""
        if type(intent_id) is not str or not intent_id:
            raise TypeError("H1 producer intent identity must be a nonempty string")
        with self._runtime._authority_gate().hold():
            self._require_mounted_held()
            self._recheck_authority_held(authority_capture)
            record = self._read_record_held(authority_capture, intent_id)
            capture = object.__new__(H1ProducerSourceCapture)
            capture._record = record
            self._issued[id(capture)] = (capture, record)
            return capture

    def recheck_held(self, capture: H1ProducerSourceCapture) -> H1ProducerSourceCapture:
        """Require the gate-held physical reread to equal the retained cut."""
        self._runtime._authority_gate().require_held()
        self._require_mounted_held()
        record = self._issued_record_held(capture)
        self._recheck_authority_held(record.authority_capture)
        current = self._read_record_held(record.authority_capture, record.intent_id)
        if not _same_physical_sources(record, current):
            raise H1ProducerSourceViolation("H1 producer physical effects source changed")
        if current.clock_epoch != record.clock_epoch:
            raise H1ProducerSourceViolation("H1 producer source clock epoch changed")
        if not record.observed_time_ns <= current.observed_time_ns < record.valid_until_ns:
            raise H1ProducerSourceViolation("H1 producer source capture lease expired")
        return capture

    def release(self, capture: H1ProducerSourceCapture) -> None:
        """Revoke one exact capture; a released identity cannot be rechecked."""
        with self._runtime._authority_gate().hold():
            self._issued_record_held(capture)
            del self._issued[id(capture)]

    def close(self) -> None:
        """Revoke every outstanding capture when this reader leaves its owner."""
        with self._runtime._authority_gate().hold():
            self._closed = True
            self._issued.clear()

    def _require_mounted_held(self) -> None:
        from chiplog.architecture.r7_runtime import R14_R17_H1_SCOPED_EFFECTS_J7_PRODUCTION_MANIFEST

        self._runtime._authority_gate().require_held()
        if self._closed:
            raise H1ProducerSourceViolation("H1 producer source reader is closed")
        # These are installed only by the scoped H1 runtime opener and removed
        # before it yields a closed runtime back to its caller.
        if (
            getattr(getattr(self._runtime, "_supervisor", None), "_manifest", None)
            is not R14_R17_H1_SCOPED_EFFECTS_J7_PRODUCTION_MANIFEST
            or
            getattr(self._runtime, "_h1_preissuance_registration_source_port", None) is None
            or getattr(self._runtime, "_h1_completion_exchange_registry", None) is None
            or getattr(self._runtime, "_h1_live_completion_enrollment", None) is None
        ):
            raise H1ProducerSourceViolation(
                "H1 producer sources require an open mounted scoped runtime"
            )

    def _recheck_authority_held(self, capture: H1ScopedDeliveryAuthorityCapture) -> None:
        self._runtime._authority_gate().require_held()
        try:
            record = self._authority_reader._issued_record_held(capture)
            self._authority_reader._verify_record_held(record)
        except (H1ScopedDeliveryAuthorityViolation, TypeError, ValueError) as error:
            raise H1ProducerSourceViolation("H1 scoped authority capture is not current") from error

    def _read_record_held(
        self, authority_capture: H1ScopedDeliveryAuthorityCapture, intent_id: str
    ) -> _SourceRecord:
        self._runtime._authority_gate().require_held()
        cut, journal = _read_materialized_effects_with_history(
            self._runtime, self._runtime._owner_decisions(), run_id=None
        )
        interpreted = interpret_h1_producer_effects_history(cut, journal)
        interpreted.require_intent_absent(intent_id)
        history = _effects_history(interpreted)
        if history.digest != interpreted.digest:
            raise H1ProducerSourceViolation("H1 effects history digest parity failed")
        generation = h1_normative_conflict_generation(history)
        epoch, now_ns = self._runtime._require_dispatch_resources().clock()
        valid_until_ns = now_ns + _OBSERVATION_HORIZON_NS
        effects_history = h1_effects_history_capture(
            history,
            clock_contract=_CLOCK_CONTRACT,
            clock_epoch=epoch,
            valid_until_ns=valid_until_ns,
        )
        normative_conflict_generation = h1_normative_conflict_generation_capture(
            generation,
            clock_contract=_CLOCK_CONTRACT,
            clock_epoch=epoch,
            valid_until_ns=valid_until_ns,
        )
        return _SourceRecord(
            authority_capture=authority_capture,
            intent_id=intent_id,
            physical_cut=cut,
            physical_cut_head=_physical_cut_head(cut, history),
            owner_snapshot=journal,
            history=history,
            generation=generation,
            effects_history=effects_history,
            normative_conflict_generation=normative_conflict_generation,
            clock_epoch=epoch,
            observed_time_ns=now_ns,
            valid_until_ns=valid_until_ns,
        )

    def _issued_record_held(self, capture: H1ProducerSourceCapture) -> _SourceRecord:
        self._runtime._authority_gate().require_held()
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ProducerSourceCapture
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ProducerSourceViolation("H1 producer source capture is not reader-issued")
        return issued[1]


def _effects_history(interpreted: H1ProducerEffectsHistory) -> H1EffectsHistoryV1:
    """Losslessly move the complete physical ledger into its Effects DTO."""
    return H1EffectsHistoryV1(
        tenant_id=interpreted.tenant_id,
        owner_journal_head=interpreted.owner_journal_head,
        ordered_members=tuple(
            H1EffectsHistoryMemberV1(
                selected_decision=member.selected_decision,
                tenant_commit_sequence=member.tenant_commit_sequence,
                publication_ordinal=member.publication_ordinal,
                record_id=member.record_id,
                record_kind=member.record_kind,
                schema_id=member.schema_id,
                fingerprint=member.fingerprint,
                canonical_record_bytes=member.canonical_record_bytes,
                intent_id=member.intent_id,
            )
            for member in interpreted.ordered_members
        ),
        digest=interpreted.digest,
    )


_PHYSICAL_CUT_SUBJECT = "chiplog.h1-producer.physical-cut.v1"


def _physical_cut_head(
    cut: MaterializedEffectsCut, history: H1EffectsHistoryV1
) -> ExactHead:
    """Commit the complete authenticated physical cut under a fixed domain.

    The SHA-256 preimage is UTF-8 compact, sorted JSON with the fixed
    ``domain`` below.  It contains every ``MaterializedEffectsCut`` field:
    tenant, frontier, SQLite materialization commitment, selected owner head,
    physical database identity, all effects rows (including their exact record
    bytes), current worker, and every reconstructed latest Run.  It also
    contains the selected decision heads paired with the authenticated effects
    history records.  Bytes are represented only as lower-case hexadecimal;
    nested DTOs use their own canonical bytes.  All values come from the
    authenticated materialized-effects reader and interpreter, never a caller
    DTO.  This is an observation commitment, not an owner-currentness or SEND
    authorization assertion.
    """
    worker = cut.worker
    preimage = {
        "domain": _PHYSICAL_CUT_SUBJECT,
        "tenant_id": cut.tenant_id,
        "tenant_frontier": cut.tenant_frontier,
        "materialization_commitment": cut.materialization_commitment,
        "owner_journal_head": cut.owner_journal_head,
        "physical_database": {
            "path": cut.physical_path,
            "device": cut.physical_device,
            "inode": cut.physical_inode,
        },
        "rows": [
            {
                "commit_sequence": row.commit_sequence,
                "publication_ordinal": row.publication_ordinal,
                "batch_record_ids": list(row.batch_record_ids),
                "record": {
                    "owner": row.record.owner,
                    "record_kind": row.record.record_kind,
                    "record_id": row.record.record_id,
                    "schema_id": row.record.schema_id,
                    "canonical_bytes_hex": row.record.canonical_bytes.hex(),
                    "fingerprint": row.record.fingerprint,
                },
            }
            for row in cut.rows
        ],
        "worker": None
        if worker is None
        else {
            "run_canonical_bytes_hex": worker.run.canonical_bytes().hex(),
            "fence_canonical_bytes_hex": worker.fence.canonical_bytes().hex(),
            "owner_session": {
                "tenant_id": worker.owner_session.tenant_id,
                "broker_epoch": worker.owner_session.broker_epoch,
                "generation_id": worker.owner_session.generation_id,
                "owner_id": worker.owner_session.owner_id,
                "session_id": worker.owner_session.session_id,
            },
        },
        "latest_runs_canonical_bytes_hex": [
            run.canonical_bytes().hex() for run in cut.latest_runs
        ],
        "selected_effects_history": {
            "digest": history.digest,
            "members": [
                {
                    "record_id": member.record_id,
                    "selected_decision": member.selected_decision.model_dump(mode="json"),
                }
                for member in history.ordered_members
            ],
        },
    }
    canonical = json.dumps(
        preimage, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    return ExactHead(
        subject_id=_PHYSICAL_CUT_SUBJECT,
        head=f"{_PHYSICAL_CUT_SUBJECT}/{digest}",
        fingerprint=digest,
    )


def _same_physical_sources(original: _SourceRecord, current: _SourceRecord) -> bool:
    """Compare the immutable physical source result apart from the live clock read."""
    return (
        original.authority_capture is current.authority_capture
        and original.intent_id == current.intent_id
        and original.physical_cut == current.physical_cut
        and original.physical_cut_head == current.physical_cut_head
        and original.owner_snapshot == current.owner_snapshot
        and original.history == current.history
        and original.generation == current.generation
        and original.effects_history.canonical_value == current.effects_history.canonical_value
        and original.effects_history.invalidation_manifest
        == current.effects_history.invalidation_manifest
        and original.normative_conflict_generation.canonical_value
        == current.normative_conflict_generation.canonical_value
        and original.normative_conflict_generation.invalidation_manifest
        == current.normative_conflict_generation.invalidation_manifest
    )


__all__ = [
    "H1ProducerSourceCapture",
    "H1ProducerSourceReader",
    "H1ProducerSourceViolation",
]
