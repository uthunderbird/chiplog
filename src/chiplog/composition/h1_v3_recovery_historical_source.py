"""Durable, selected V3 B reconstruction for historical prepared delivery.

This reader never obtains a live P ``CURRENT`` value.  It joins the enrolled
post-seal recovery journal to the already bounded historical P/E reader, then
rebuilds both durable B stages from their canonical inputs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
)
from chiplog.capabilities.effects.fences import (
    NonSchedulerFence as EffectsNonSchedulerFence,
)
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    PreparedH1ScopedDeliveryV1,
    PrepareH1ScopedDeliveryV1,
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
    from chiplog.composition.h1_completion_exchange_registry import (
        H1CompletionPreimages,
        H1PreparedDeliveryProjection,
    )
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
class H1V3RecoveredEffectsEvidence:
    """Inert selected V3 Effects pins, reconstructed from durable history.

    This evidence is deliberately insufficient to call an owner.  In
    particular, the attempt-specific broker route is not retained here.
    """

    original_identity: DriverCommandIdentityV1
    original_fingerprint: str
    selected_seal: CallSubjectHead
    pinned_records: tuple[H1PostSealRecoverySelectedRecord, ...]
    original_completion_request: PrepareExecutionCompletionFirstPathV2
    prepared_completion: PreparedExecutionCompletion
    effects_request: PrepareH1ScopedDeliveryV1
    effects_result: PreparedH1ScopedDeliveryV1 | None


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

    def read_effects(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1V3RecoveredEffectsEvidence:
        """Read an optional durable scoped Effects input/result suffix.

        The five-record B prefix remains the authority for original completion
        provenance.  The optional suffix only binds a previously persisted
        inner Effects request and result to that independently replayed prefix.
        """
        return self._read_effects(
            original_identity=original_identity,
            original_fingerprint=original_fingerprint,
            selected_seal=selected_seal,
            pinned=None,
        )

    def replay_effects(
        self, captured: H1V3RecoveredEffectsEvidence
    ) -> H1V3RecoveredEffectsEvidence:
        """Reauthenticate an inert Effects evidence snapshot exactly."""
        if type(captured) is not H1V3RecoveredEffectsEvidence:
            raise H1V3RecoveryHistoricalSourceError("V3 historical Effects capture is foreign")
        replayed = self._read_effects(
            original_identity=captured.original_identity,
            original_fingerprint=captured.original_fingerprint,
            selected_seal=captured.selected_seal,
            pinned=captured.pinned_records,
        )
        if replayed != captured:
            raise H1V3RecoveryHistoricalSourceError("V3 historical Effects replay differs")
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

    def _read_effects(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
        pinned: tuple[H1PostSealRecoverySelectedRecord, ...] | None,
    ) -> H1V3RecoveredEffectsEvidence:
        if (
            type(original_identity) is not DriverCommandIdentityV1
            or not isinstance(original_fingerprint, str)
            or len(original_fingerprint) != 64
            or type(selected_seal) is not CallSubjectHead
        ):
            raise H1V3RecoveryHistoricalSourceError("V3 historical Effects locator differs")
        try:
            with self._gate.hold():
                base = self._read(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                    pinned=None,
                )
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
                records = self._required_effects_records(selected.records)
                if pinned is not None and records != pinned:
                    raise ValueError("pinned recovery records differ")
                decoded = self._decode_effects_records(records, root=root)
                request_bytes = self._stage_payload(
                    decoded[5], kind="STAGE_INPUT", stage="EFFECTS"
                )
                request = PrepareH1ScopedDeliveryV1.model_validate_json(request_bytes)
                if request.canonical_bytes() != request_bytes:
                    raise ValueError("durable scoped Effects input differs")
                input_record = decoded[5]
                if input_record.effects_command_id != request.identity.command_id:
                    raise ValueError("durable scoped Effects command ID differs")
                preimages, projection = self._historical_completion_preimages(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                if (
                    request.original_completion_request.canonical_bytes()
                    != preimages.original_completion_request.canonical_bytes()
                    or request.prepared_completion.canonical_bytes()
                    != preimages.prepared_completion.canonical_bytes()
                    or not self._same_historical_selected_scope(
                        request.selected_scope, projection.selected_scope
                    )
                    or request.retained_origin != projection.retained_origin
                    or request.original_completion_request != base.completion_request
                    or request.prepared_completion != base.completion_result
                ):
                    raise ValueError("durable scoped Effects B preimages differ")
                result: PreparedH1ScopedDeliveryV1 | None = None
                if len(decoded) == 7:
                    result_bytes = self._stage_payload(
                        decoded[6], kind="STAGE_RESULT", stage="EFFECTS"
                    )
                    result = PreparedH1ScopedDeliveryV1.model_validate_json(result_bytes)
                    self._require_effects_result(request, result, result_bytes)
                return H1V3RecoveredEffectsEvidence(
                    original_identity,
                    original_fingerprint,
                    selected_seal,
                    records,
                    preimages.original_completion_request,
                    preimages.prepared_completion,
                    request,
                    result,
                )
        except H1V3RecoveryHistoricalSourceError:
            raise
        except (H1RecoveryHistoricalPESourceError, H1PostSealRecoveryRecordError) as error:
            raise H1V3RecoveryHistoricalSourceError(
                "V3 historical Effects evidence is invalid"
            ) from error
        except (AttributeError, TypeError, ValueError) as error:
            raise H1V3RecoveryHistoricalSourceError(
                "V3 historical Effects reconstruction differs"
            ) from error

    def _historical_completion_preimages(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> tuple[H1CompletionPreimages, H1PreparedDeliveryProjection]:
        """Read B preimages and retained origin only through the mounted registry."""
        from chiplog.composition.h1_completion_exchange_registry import (
            H1CompletionExchangeRegistry,
        )

        registry = getattr(self._runtime, "_h1_completion_exchange_registry", None)
        if type(registry) is not H1CompletionExchangeRegistry:
            raise ValueError("historical completion registry is unavailable")
        capture = registry.capture_recovered_prepared_delivery_historical(
            original_identity=original_identity,
            original_fingerprint=original_fingerprint,
            selected_seal=selected_seal,
        )
        return (
            registry.replay_prepared_delivery_preimages(capture),
            registry.replay_prepared_delivery_historical(capture),
        )

    @staticmethod
    def _same_historical_selected_scope(left: object, right: object) -> bool:
        """Compare only scope fields retained from the selected historical cut.

        A mounted producer deliberately substitutes fresh ``current_*`` scope
        observations into its inner request.  Those values are inert evidence
        here and cannot be treated as historical provenance.
        """
        return (
            getattr(left, "anchor", None) == getattr(right, "anchor", None)
            and getattr(left, "scope", None) == getattr(right, "scope", None)
            and getattr(left, "selected_decision_bytes", None)
            == getattr(right, "selected_decision_bytes", None)
            and getattr(left, "selected_record_bytes", None)
            == getattr(right, "selected_record_bytes", None)
        )

    @staticmethod
    def _require_effects_result(
        request: PrepareH1ScopedDeliveryV1,
        result: PreparedH1ScopedDeliveryV1,
        result_bytes: bytes,
    ) -> None:
        if (
            result.canonical_bytes() != result_bytes
            or result.disposition != "PREPARED"
            or result.source_request_fingerprint
            != hashlib.sha256(request.canonical_bytes()).hexdigest()
            or result.authority_evidence != request.authority_evidence
            or result.precursor_request != request.precursor_request
            or result.retained_sources != request.retained_sources
            or result.intent_request.identity != request.identity
            or result.intent_request.expected_intent != request.expected_intent
            or result.intent_request.current != request.current
            or result.intent_request.fence != request.fence
            or result.intent_request.intent.intent_id != request.intent_id
        ):
            raise H1V3RecoveryHistoricalSourceError("durable scoped Effects result differs")

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
    def _required_effects_records(
        records: tuple[H1PostSealRecoverySelectedRecord, ...],
    ) -> tuple[H1PostSealRecoverySelectedRecord, ...]:
        """Require the complete selected prefix through an Effects input/result.

        This deliberately does not select a subsequence: a later terminal
        record, a duplicate, or any alternate ordering changes the evidence.
        """
        sequence = tuple((record.kind, record.stage) for record in records)
        prefix = (
            ("ROOT", None),
            ("STAGE_INPUT", "COMPLETION"),
            ("STAGE_RESULT", "COMPLETION"),
            ("STAGE_INPUT", "CONVERSATION"),
            ("STAGE_RESULT", "CONVERSATION"),
        )
        allowed = (
            (*prefix, ("STAGE_INPUT", "EFFECTS")),
            (*prefix, ("STAGE_INPUT", "EFFECTS"), ("STAGE_RESULT", "EFFECTS")),
        )
        if sequence not in allowed:
            raise H1V3RecoveryHistoricalSourceError(
                "V3 recovery Effects prefix is absent or has extra records"
            )
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

    @classmethod
    def _decode_effects_records(
        cls,
        records: tuple[H1PostSealRecoverySelectedRecord, ...],
        *,
        root: H1PostSealRecoveryRootV1,
    ) -> tuple[
        H1PostSealRecoveryRecordV2,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
    ] | tuple[
        H1PostSealRecoveryRecordV2,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
        H1PostSealRecoveryRecordV1,
    ]:
        cls._required_effects_records(records)
        first_five = cls._decode_required_records(records[:5], root=root)
        decoded_suffix = tuple(
            cast(H1PostSealRecoveryRecordV1, _decode_record(record.canonical_bytes))
            for record in records[5:]
        )
        if (
            not decoded_suffix
            or any(type(record) is not H1PostSealRecoveryRecordV1 for record in decoded_suffix)
            or any(
                record.root_id != root.root_id()
                or record.kind != selected.kind
                or record.stage != selected.stage
                or record.predecessor_entry_id != records[index + 4].entry_id
                for index, (record, selected) in enumerate(
                    zip(decoded_suffix, records[5:], strict=True)
                )
            )
        ):
            raise H1V3RecoveryHistoricalSourceError("V3 recovery Effects envelopes differ")
        effects_input = decoded_suffix[0]
        if effects_input.kind != "STAGE_INPUT" or effects_input.stage != "EFFECTS":
            raise H1V3RecoveryHistoricalSourceError("V3 recovery Effects input differs")
        if len(decoded_suffix) == 1:
            prefix = cast(
                tuple[
                    H1PostSealRecoveryRecordV2,
                    H1PostSealRecoveryRecordV1,
                    H1PostSealRecoveryRecordV1,
                    H1PostSealRecoveryRecordV1,
                    H1PostSealRecoveryRecordV1,
                ],
                first_five,
            )
            return (*prefix, effects_input)
        effects_result = decoded_suffix[1]
        if effects_result.kind != "STAGE_RESULT" or effects_result.stage != "EFFECTS":
            raise H1V3RecoveryHistoricalSourceError("V3 recovery Effects result differs")
        prefix = cast(
            tuple[
                H1PostSealRecoveryRecordV2,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
                H1PostSealRecoveryRecordV1,
            ],
            first_five,
        )
        return (*prefix, effects_input, effects_result)

    @staticmethod
    def _stage_payload(record: H1PostSealRecoveryRecordV1, *, kind: str, stage: str) -> bytes:
        if record.kind != kind or record.stage != stage:
            raise H1V3RecoveryHistoricalSourceError("V3 recovery stage envelope differs")
        payload = record.semantic_input if kind == "STAGE_INPUT" else record.result_bytes
        if type(payload) is not bytes:
            raise H1V3RecoveryHistoricalSourceError("V3 recovery stage payload is absent")
        return payload

__all__ = [
    "H1V3RecoveredEffectsEvidence",
    "H1V3RecoveredPreparedDelivery",
    "H1V3RecoveryHistoricalSource",
    "H1V3RecoveryHistoricalSourceError",
]
