"""Durable, selected V3 B reconstruction for historical prepared delivery.

This reader never obtains a live P ``CURRENT`` value.  It joins the enrolled
post-seal recovery journal to the already bounded historical P/E reader, then
rebuilds both durable B stages from their canonical inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    H1PostSealRecoverySelectedRecord,
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
                completion_input = records[1].canonical_bytes
                completion_result_bytes = records[2].canonical_bytes
                conversation_input = records[3].canonical_bytes
                conversation_result_bytes = records[4].canonical_bytes
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
        records: tuple[H1PostSealRecoverySelectedRecord, ...]
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


__all__ = [
    "H1V3RecoveredPreparedDelivery",
    "H1V3RecoveryHistoricalSource",
    "H1V3RecoveryHistoricalSourceError",
]
