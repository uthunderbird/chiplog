"""Durable, selected V3 B reconstruction for historical prepared delivery.

This reader never obtains a live P ``CURRENT`` value.  It joins the enrolled
post-seal recovery journal to the already bounded historical P/E reader, then
rebuilds both durable B stages from their canonical inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.completion_terminal_work_sources import (
    AcceptedCompletionWorkSourceV1,
    decode_completion_terminal_work_source,
)
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
)
from chiplog.capabilities.agent_loop.post_terminal_contracts import (
    PreparedPostTerminalWork,
    PrepareTerminalWork,
)
from chiplog.capabilities.agent_loop.terminal_work_preparation import prepare_h1_terminal_work
from chiplog.capabilities.effects.fences import (
    NonSchedulerFence as EffectsNonSchedulerFence,
)
from chiplog.capabilities.effects.h1_scoped_preparation import prepare_h1_scoped_delivery
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryOwnerCallV1,
    PreparedH1ScopedDeliveryV1,
)
from chiplog.capabilities.projections.conversation_completion_owner import (
    ConversationCompletionOwner,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    PreparedConversationCompletionV1,
)
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_conversation_sources import H1ConversationSources
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryJournal,
    H1PostSealRecoveryRecordError,
    H1PostSealRecoveryRecordV1,
    H1PostSealRecoveryRecordV2,
    H1PostSealRecoveryRootV1,
    H1PostSealRecoverySelectedRecord,
    _decode_record,
)
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.h1_recovery_historical_pe_source import (
    H1RecoveryHistoricalPESource,
    H1RecoveryHistoricalPESourceError,
)

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.h1_runtime_preissuance_port import (
        _AuthenticatedConversationPolicyInputs,
        _HistoricalEffectsSource,
    )


class H1V3RecoveryHistoricalSourceError(ValueError):
    """The enrolled V3 recovery evidence cannot reconstruct a B projection."""


@dataclass(frozen=True, slots=True)
class H1V3RecoveredPreparedDelivery:
    """Inert reconstructed B/P facts plus exact durable journal pins."""

    original_identity: DriverCommandIdentityV1
    original_fingerprint: str
    selected_seal: CallSubjectHead
    pinned_records: tuple[H1PostSealRecoverySelectedRecord, ...]
    completion_request: PrepareExecutionCompletionFirstPathV2
    completion_result: PreparedExecutionCompletion
    conversation_result: PreparedConversationCompletionV1
    policy: _AuthenticatedConversationPolicyInputs
    effects_source: _HistoricalEffectsSource
    fence: EffectsNonSchedulerFence


@dataclass(frozen=True, slots=True)
class H1V3RecoveredCompletionStages:
    """All four canonical SCOPED_V3 recovery stages, still inert evidence.

    This type deliberately has no publication or dispatch method.  It is a
    durable-reader result for a future selected V3 recovery owner, not a route
    around the coordinator's explicit SCOPED_V3 hold.
    """

    prepared_delivery: H1V3RecoveredPreparedDelivery
    pinned_records: tuple[H1PostSealRecoverySelectedRecord, ...]
    effects_call: H1ScopedDeliveryOwnerCallV1
    effects_result: PreparedH1ScopedDeliveryV1
    terminal_work_request: PrepareTerminalWork
    terminal_work_result: PreparedPostTerminalWork


class H1V3RecoveryHistoricalSource:
    """Select, pin, and replay the first two durable SCOPED_V3 B stages."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("V3 historical B reader requires the installed runtime")
        self._runtime = runtime
        self._gate = runtime._authority_gate()

    def capture(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1V3RecoveredPreparedDelivery:
        return self._read(
            original_identity=original_identity,
            original_fingerprint=original_fingerprint,
            selected_seal=selected_seal,
            pinned=None,
        )

    def replay(self, captured: H1V3RecoveredPreparedDelivery) -> H1V3RecoveredPreparedDelivery:
        if type(captured) is not H1V3RecoveredPreparedDelivery:
            raise H1V3RecoveryHistoricalSourceError("V3 historical B capture is foreign")
        replayed = self._read(
            original_identity=captured.original_identity,
            original_fingerprint=captured.original_fingerprint,
            selected_seal=captured.selected_seal,
            pinned=captured.pinned_records,
        )
        if replayed != captured:
            raise H1V3RecoveryHistoricalSourceError("V3 historical B replay differs")
        return replayed

    def capture_completed_stages(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1V3RecoveredCompletionStages:
        """Read a complete four-stage V3 journal without enabling recovery.

        The existing ``capture`` remains the five-record B prefix used by J7.
        This stricter reader is intentionally separate so a partial V3 journal
        stays unavailable to any future completion/publication authority.
        """
        prepared_delivery = self.capture(
            original_identity=original_identity,
            original_fingerprint=original_fingerprint,
            selected_seal=selected_seal,
        )
        try:
            with self._gate.hold():
                root = H1PostSealRecoveryRootSource(self._runtime).derive_on_restart(
                    original_identity, original_fingerprint, selected_seal
                )
                journal = getattr(self._runtime, "_h1_postseal_recovery_journal", None)
                mount = getattr(self._runtime, "_h1_recovery_mount", None)
                if type(journal) is not H1PostSealRecoveryJournal or journal._mount is not mount:
                    raise ValueError("enrolled recovery journal is unavailable")
                selected = journal._read_selected_root_entries_held(root.root_id())
                if selected.state.root != root or selected.state.selected_producer != "SCOPED_V3":
                    raise ValueError("selected recovery root differs")
                records = self._required_completed_records(selected.records)
                if records[:5] != prepared_delivery.pinned_records:
                    raise ValueError("durable V3 completion prefix differs")
                decoded = self._decode_completed_records(records, root=root)
                effects_input = self._stage_payload(decoded[5], kind="STAGE_INPUT", stage="EFFECTS")
                effects_result_bytes = self._stage_payload(
                    decoded[6], kind="STAGE_RESULT", stage="EFFECTS"
                )
                terminal_input = self._stage_payload(
                    decoded[7], kind="STAGE_INPUT", stage="TERMINAL_WORK"
                )
                terminal_result_bytes = self._stage_payload(
                    decoded[8], kind="STAGE_RESULT", stage="TERMINAL_WORK"
                )
                effects_call = H1ScopedDeliveryOwnerCallV1.model_validate_json(effects_input)
                effects_result = PreparedH1ScopedDeliveryV1.model_validate_json(
                    effects_result_bytes
                )
                terminal_request = PrepareTerminalWork.model_validate_json(terminal_input)
                terminal_result = PreparedPostTerminalWork.model_validate_json(
                    terminal_result_bytes
                )
                effects_input_record = decoded[5]
                terminal_source = decode_completion_terminal_work_source(
                    terminal_request.original_terminalization_request
                )
                if (
                    type(terminal_source) is not AcceptedCompletionWorkSourceV1
                    or terminal_source.original_completion_request_bytes
                    != prepared_delivery.completion_request.canonical_bytes()
                    or terminal_source.prepared_completion_bytes
                    != prepared_delivery.completion_result.canonical_bytes()
                    or terminal_request.canonical_bytes() != terminal_input
                    or terminal_result.canonical_bytes() != terminal_result_bytes
                    or prepare_h1_terminal_work(terminal_request) != terminal_result
                    or effects_call.canonical_bytes() != effects_input
                    or effects_result.canonical_bytes() != effects_result_bytes
                    or effects_call.request.original_completion_request
                    != prepared_delivery.completion_request
                    or effects_call.request.prepared_completion
                    != prepared_delivery.completion_result
                    or effects_call.request.selected_scope
                    != prepared_delivery.effects_source.selected_scope
                    or effects_call.request.retained_origin
                    != prepared_delivery.effects_source.retained_origin
                    or effects_call.request.fence.canonical_bytes()
                    != prepared_delivery.fence.canonical_bytes()
                    or effects_input_record.effects_command_id
                    != effects_call.request.identity.command_id
                    or effects_result.source_request_fingerprint != effects_call.request_digest
                    or effects_result.authority_evidence != effects_call.request.authority_evidence
                    or prepare_h1_scoped_delivery(effects_call) != effects_result
                ):
                    raise ValueError("durable V3 completion stages differ")
                return H1V3RecoveredCompletionStages(
                    prepared_delivery,
                    records,
                    effects_call,
                    effects_result,
                    terminal_request,
                    terminal_result,
                )
        except H1V3RecoveryHistoricalSourceError:
            raise
        except (H1PostSealRecoveryRecordError, TypeError, ValueError) as error:
            raise H1V3RecoveryHistoricalSourceError(
                "V3 historical completion stages are unavailable"
            ) from error

    def replay_completed_stages(
        self, captured: H1V3RecoveredCompletionStages
    ) -> H1V3RecoveredCompletionStages:
        """Reconstruct a complete typed V3 journal and reject any drift."""
        if type(captured) is not H1V3RecoveredCompletionStages:
            raise H1V3RecoveryHistoricalSourceError("V3 historical completion capture is foreign")
        replayed = self.capture_completed_stages(
            original_identity=captured.prepared_delivery.original_identity,
            original_fingerprint=captured.prepared_delivery.original_fingerprint,
            selected_seal=captured.prepared_delivery.selected_seal,
        )
        if replayed != captured:
            raise H1V3RecoveryHistoricalSourceError("V3 historical completion replay differs")
        return replayed

    def _read(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
        pinned: tuple[H1PostSealRecoverySelectedRecord, ...] | None,
    ) -> H1V3RecoveredPreparedDelivery:
        if (
            type(original_identity) is not DriverCommandIdentityV1
            or not isinstance(original_fingerprint, str)
            or len(original_fingerprint) != 64
            or type(selected_seal) is not CallSubjectHead
        ):
            raise H1V3RecoveryHistoricalSourceError("V3 historical B locator differs")
        try:
            with self._gate.hold():
                root = H1PostSealRecoveryRootSource(self._runtime).derive_on_restart(
                    original_identity, original_fingerprint, selected_seal
                )
                journal = getattr(self._runtime, "_h1_postseal_recovery_journal", None)
                mount = getattr(self._runtime, "_h1_recovery_mount", None)
                if type(journal) is not H1PostSealRecoveryJournal or journal._mount is not mount:
                    raise ValueError("enrolled recovery journal is unavailable")
                selected = journal._read_selected_root_entries_held(root.root_id())
                if selected.state.root != root or selected.state.selected_producer != "SCOPED_V3":
                    raise ValueError("selected recovery root differs")
                records = self._required_records(selected.records)
                if pinned is not None and records != pinned:
                    raise ValueError("pinned recovery records differ")
                decoded_records = self._decode_required_records(records, root=root)
                completion_input = self._stage_payload(
                    decoded_records[1], kind="STAGE_INPUT", stage="COMPLETION"
                )
                completion_result_bytes = self._stage_payload(
                    decoded_records[2], kind="STAGE_RESULT", stage="COMPLETION"
                )
                conversation_input = self._stage_payload(
                    decoded_records[3], kind="STAGE_INPUT", stage="CONVERSATION"
                )
                conversation_result_bytes = self._stage_payload(
                    decoded_records[4], kind="STAGE_RESULT", stage="CONVERSATION"
                )
                completion, policy, effects = H1RecoveryHistoricalPESource(
                    self._runtime
                ).read_selected_projection(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                if completion.canonical_bytes() != completion_input:
                    raise ValueError("durable completion input differs")
                prepared = PreparedExecutionCompletion.model_validate_json(completion_result_bytes)
                if (
                    prepared.canonical_bytes() != completion_result_bytes
                    or prepare_first_path_execution_completion(completion) != prepared
                ):
                    raise ValueError("durable completion result differs")
                conversation_source = getattr(self._runtime, "_h1_conversation_source_port", None)
                if (
                    type(conversation_source) is not H1ConversationSources
                    or conversation_source._runtime is not self._runtime
                ):
                    raise ValueError("historical conversation source is unavailable")
                conversation = conversation_source._read_selected_historical_conversation_request(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                    policy=policy,
                    completion_request_bytes=completion_input,
                    completion_result_bytes=completion_result_bytes,
                )
                conversation_result = PreparedConversationCompletionV1.model_validate_json(
                    conversation_result_bytes
                )
                if (
                    conversation.canonical_json_bytes() != conversation_input
                    or conversation_result.canonical_json_bytes() != conversation_result_bytes
                    or ConversationCompletionOwner().prepare_completion(conversation)
                    != conversation_result
                ):
                    raise ValueError("durable conversation stage differs")
                fence = EffectsNonSchedulerFence.model_validate_json(
                    completion.fence.canonical_bytes()
                )
                if fence.canonical_bytes() != completion.fence.canonical_bytes():
                    raise ValueError("historical effects fence differs")
                return H1V3RecoveredPreparedDelivery(
                    original_identity,
                    original_fingerprint,
                    selected_seal,
                    records,
                    completion,
                    prepared,
                    conversation_result,
                    policy,
                    effects,
                    fence,
                )
        except H1V3RecoveryHistoricalSourceError:
            raise
        except (H1RecoveryHistoricalPESourceError, H1PostSealRecoveryRecordError) as error:
            raise H1V3RecoveryHistoricalSourceError(
                "V3 historical B evidence is invalid"
            ) from error
        except (AttributeError, TypeError, ValueError) as error:
            raise H1V3RecoveryHistoricalSourceError(
                "V3 historical B reconstruction differs"
            ) from error

    @staticmethod
    def _required_records(
        records: tuple[H1PostSealRecoverySelectedRecord, ...],
    ) -> tuple[H1PostSealRecoverySelectedRecord, ...]:
        wanted = (
            ("ROOT", None),
            ("STAGE_INPUT", "COMPLETION"),
            ("STAGE_RESULT", "COMPLETION"),
            ("STAGE_INPUT", "CONVERSATION"),
            ("STAGE_RESULT", "CONVERSATION"),
        )
        found = tuple(record for record in records if (record.kind, record.stage) in wanted)
        if len(found) != len(wanted) or tuple((item.kind, item.stage) for item in found) != wanted:
            raise H1V3RecoveryHistoricalSourceError(
                "V3 recovery completion/conversation pairs are absent"
            )
        return found

    @staticmethod
    def _required_completed_records(
        records: tuple[H1PostSealRecoverySelectedRecord, ...],
    ) -> tuple[H1PostSealRecoverySelectedRecord, ...]:
        wanted = (
            ("ROOT", None),
            ("STAGE_INPUT", "COMPLETION"),
            ("STAGE_RESULT", "COMPLETION"),
            ("STAGE_INPUT", "CONVERSATION"),
            ("STAGE_RESULT", "CONVERSATION"),
            ("STAGE_INPUT", "EFFECTS"),
            ("STAGE_RESULT", "EFFECTS"),
            ("STAGE_INPUT", "TERMINAL_WORK"),
            ("STAGE_RESULT", "TERMINAL_WORK"),
        )
        if tuple((item.kind, item.stage) for item in records) != wanted:
            raise H1V3RecoveryHistoricalSourceError("V3 recovery four-stage journal is absent")
        return records

    @staticmethod
    def _decode_required_records(
        records: tuple[H1PostSealRecoverySelectedRecord, ...], *, root: H1PostSealRecoveryRootV1
    ) -> tuple[
        H1PostSealRecoveryRecordV1 | H1PostSealRecoveryRecordV2,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
    ]:
        decoded = tuple(_decode_record(record.canonical_bytes) for record in records)
        if len(decoded) != 5:
            raise H1V3RecoveryHistoricalSourceError("V3 recovery record envelopes differ")
        root_record = decoded[0]
        completion_input_record = decoded[1]
        completion_result_record = decoded[2]
        conversation_input_record = decoded[3]
        conversation_result_record = decoded[4]
        if (
            type(root_record) is not H1PostSealRecoveryRecordV2
            or root_record.producer_choice != "SCOPED_V3"
            or root_record.root is None
            or root_record.root != root
        ):
            raise H1V3RecoveryHistoricalSourceError("V3 recovery root envelope differs")
        if (
            root_record.root_id != root.root_id()
            or root_record.predecessor_entry_id is not None
            or any(
                record.root_id != root.root_id()
                or record.kind != selected.kind
                or (record.stage if type(record) is H1PostSealRecoveryRecordV1 else None)
                != selected.stage
                for record, selected in zip(decoded, records, strict=True)
            )
            or any(
                record.predecessor_entry_id != records[index - 1].entry_id
                for index, record in enumerate(decoded[1:], start=1)
            )
            or type(completion_input_record) is not H1PostSealRecoveryRecordV1
            or type(completion_result_record) is not H1PostSealRecoveryRecordV1
            or type(conversation_input_record) is not H1PostSealRecoveryRecordV1
            or type(conversation_result_record) is not H1PostSealRecoveryRecordV1
        ):
            raise H1V3RecoveryHistoricalSourceError("V3 recovery record envelopes differ")
        return (
            root_record,
            completion_input_record,
            completion_result_record,
            conversation_input_record,
            conversation_result_record,
        )

    @staticmethod
    def _decode_completed_records(
        records: tuple[H1PostSealRecoverySelectedRecord, ...], *, root: H1PostSealRecoveryRootV1
    ) -> tuple[
        H1PostSealRecoveryRecordV1 | H1PostSealRecoveryRecordV2,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
    ]:
        if len(records) != 9:
            raise H1V3RecoveryHistoricalSourceError("V3 recovery record envelopes differ")
        prefix = H1V3RecoveryHistoricalSource._decode_required_records(records[:5], root=root)
        tail = tuple(_decode_record(record.canonical_bytes) for record in records[5:])
        if len(tail) != 4 or any(type(record) is not H1PostSealRecoveryRecordV1 for record in tail):
            raise H1V3RecoveryHistoricalSourceError("V3 recovery record envelopes differ")
        typed_tail = cast(tuple[H1PostSealRecoveryRecordV1, ...], tail)
        if any(
            record.root_id != root.root_id()
            or record.kind != selected.kind
            or record.stage != selected.stage
            or record.predecessor_entry_id != records[index - 1].entry_id
            for index, (record, selected) in enumerate(
                zip(typed_tail, records[5:], strict=True), start=5
            )
        ):
            raise H1V3RecoveryHistoricalSourceError("V3 recovery record envelopes differ")
        return cast(
            tuple[
                H1PostSealRecoveryRecordV1 | H1PostSealRecoveryRecordV2,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
            ],
            (*prefix, *typed_tail),
        )

    @staticmethod
    def _stage_payload(record: H1PostSealRecoveryRecordV1, *, kind: str, stage: str) -> bytes:
        if record.kind != kind or record.stage != stage:
            raise H1V3RecoveryHistoricalSourceError("V3 recovery stage envelope differs")
        payload = record.semantic_input if kind == "STAGE_INPUT" else record.result_bytes
        if type(payload) is not bytes:
            raise H1V3RecoveryHistoricalSourceError("V3 recovery stage payload is absent")
        return payload


__all__ = [
    "H1V3RecoveredCompletionStages",
    "H1V3RecoveredPreparedDelivery",
    "H1V3RecoveryHistoricalSource",
    "H1V3RecoveryHistoricalSourceError",
]
