"""Private joined source of selected V4 Effects history and current delivery work."""

from __future__ import annotations

from dataclasses import dataclass

from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    CompletionCanonicalMember,
    make_terminal_manifest_member,
    make_terminal_run_member,
)
from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DeliveryWorkSourceMemberV1,
    DeliveryWorkSourceV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputScopeV1,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    ScopedIntentCanonicalMemberV3,
    make_scoped_intent_member,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV4
from chiplog.composition.h1_delivery_work_current_claim_reader import (
    H1DeliveryWorkCurrentClaimEvidence,
    H1DeliveryWorkCurrentClaimHold,
    H1DeliveryWorkCurrentClaimReader,
)
from chiplog.composition.h1_scoped_authorization_sources_v3 import (
    H1ScopedAuthorizationEffectsHistoryCaptureV1,
    H1ScopedAuthorizationEffectsHistoryReaderV1,
    H1ScopedAuthorizationSourceHold,
    _EffectsHistoryRecord,
)
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2, OwnerRecordBytes


class H1ScopedAuthorizationDeliveryWorkHold(RuntimeError):
    """The joined, current delivery-work source is unavailable."""


@dataclass(frozen=True, slots=True)
class H1ScopedAuthorizationDeliveryWorkEvidenceV1:
    """Inert exact evidence; this object has no authorization or SEND capability."""

    source: DeliveryWorkSourceV1
    current_claim: H1DeliveryWorkCurrentClaimEvidence


@dataclass(frozen=True, slots=True)
class _DeliveryWorkRecord:
    effects_capture: H1ScopedAuthorizationEffectsHistoryCaptureV1
    evidence: H1ScopedAuthorizationDeliveryWorkEvidenceV1


class H1ScopedAuthorizationDeliveryWorkCaptureV1:
    """Opaque reader-issued capture of the joined V4/current-g1 evidence."""

    __slots__ = ("_record",)
    _record: _DeliveryWorkRecord

    def __init__(self) -> None:
        raise TypeError("H1 authorization delivery-work captures are reader-issued")

    def __copy__(self) -> H1ScopedAuthorizationDeliveryWorkCaptureV1:
        raise TypeError("H1 authorization delivery-work captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ScopedAuthorizationDeliveryWorkCaptureV1:
        del memo
        raise TypeError("H1 authorization delivery-work captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 authorization delivery-work captures cannot be serialized")


class H1ScopedAuthorizationDeliveryWorkReaderV1:
    """Join a reader-issued Effects cut to an exact, current materialized g1."""

    __slots__ = ("_closed", "_current", "_effects", "_issued", "_runtime")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 authorization delivery-work reader requires installed runtime")
        effects = getattr(runtime, "_h1_scoped_authorization_effects_history_reader_v3", None)
        current = getattr(runtime, "_h1_delivery_work_current_claim_reader", None)
        if (
            type(effects) is not H1ScopedAuthorizationEffectsHistoryReaderV1
            or effects._runtime is not runtime
            or type(current) is not H1DeliveryWorkCurrentClaimReader
        ):
            raise TypeError("H1 authorization delivery-work reader requires mounted dependencies")
        self._runtime = runtime
        self._effects = effects
        self._current = current
        self._issued: dict[
            int, tuple[H1ScopedAuthorizationDeliveryWorkCaptureV1, _DeliveryWorkRecord]
        ] = {}
        self._closed = False

    def read(
        self, effects_capture: H1ScopedAuthorizationEffectsHistoryCaptureV1
    ) -> H1ScopedAuthorizationDeliveryWorkCaptureV1:
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._read_held(effects_capture)
            capture = object.__new__(H1ScopedAuthorizationDeliveryWorkCaptureV1)
            capture._record = record
            self._issued[id(capture)] = (capture, record)
            return capture

    def recheck_held(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1ScopedAuthorizationDeliveryWorkCaptureV1:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        reread = self._read_held(record.effects_capture)
        if reread != record:
            raise H1ScopedAuthorizationDeliveryWorkHold(
                "selected V4 Effects or current delivery-work evidence changed"
            )
        return capture

    def evidence_held(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1ScopedAuthorizationDeliveryWorkEvidenceV1:
        self._runtime._authority_gate().require_held()
        self.recheck_held(capture)
        return self._issued_record_held(capture).evidence

    def scope_held(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> HermeticOutputScopeV1:
        """Return the exact V4 scope retained by this reader's Effects cut.

        The scope is deliberately not accepted from a caller.  It is recovered
        only after the opaque capture has been rechecked under the runtime gate.
        """
        self._runtime._authority_gate().require_held()
        self.recheck_held(capture)
        return self.scope_from_evidence_held(capture)

    def scope_from_evidence_held(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> HermeticOutputScopeV1:
        """Read the retained V4 scope after a caller has rechecked evidence.

        This validates capture ownership but deliberately does not perform a
        second current-g1 read.  It is for one outer gate-held composition cut.
        """
        self._runtime._authority_gate().require_held()
        effects = self._effects._issued_record_held(
            self._issued_record_held(capture).effects_capture
        )
        issuance = effects.selected.selected_h1.issuance
        if type(issuance) is not H1CompletionIssuanceV4:
            raise H1ScopedAuthorizationDeliveryWorkHold("selected Effects issuance is not V4")
        scope = issuance.effects_call.request.selected_scope.scope
        if type(scope) is not HermeticOutputScopeV1:
            raise H1ScopedAuthorizationDeliveryWorkHold("selected V4 scope is unavailable")
        return scope

    def release(self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1) -> None:
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            self._issued_record_held(capture)
            del self._issued[id(capture)]

    def close(self) -> None:
        with self._runtime._authority_gate().hold():
            if self._closed:
                return
            self._closed = True
            self._issued.clear()

    def _require_open_held(self) -> None:
        self._runtime._authority_gate().require_held()
        if self._closed:
            raise H1ScopedAuthorizationDeliveryWorkHold(
                "H1 authorization delivery-work reader is closed"
            )

    def _issued_record_held(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> _DeliveryWorkRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedAuthorizationDeliveryWorkCaptureV1
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedAuthorizationDeliveryWorkHold(
                "H1 authorization delivery-work capture is not reader-issued"
            )
        return issued[1]

    def _read_held(
        self, effects_capture: H1ScopedAuthorizationEffectsHistoryCaptureV1
    ) -> _DeliveryWorkRecord:
        self._runtime._authority_gate().require_held()
        try:
            self._effects.recheck_held(effects_capture)
            effects = self._effects._issued_record_held(effects_capture)
            source = self._source_from_effects_held(effects)
            current = self._current.read(source.intent_id)
            self._effects.recheck_held(effects_capture)
        except (
            H1ScopedAuthorizationSourceHold,
            H1DeliveryWorkCurrentClaimHold,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            raise H1ScopedAuthorizationDeliveryWorkHold(
                "selected V4 Effects or current delivery-work evidence is unavailable"
            ) from error
        if current.source != source:
            raise H1ScopedAuthorizationDeliveryWorkHold(
                "selected V4 Effects source differs from current delivery-work evidence"
            )
        return _DeliveryWorkRecord(
            effects_capture=effects_capture,
            evidence=H1ScopedAuthorizationDeliveryWorkEvidenceV1(source, current),
        )

    @staticmethod
    def _source_from_effects_held(effects: _EffectsHistoryRecord) -> DeliveryWorkSourceV1:
        selected = effects.selected
        completion, issuance = selected.selected_h1.decision, selected.selected_h1.issuance
        if (
            type(completion.prepared.request) is not CompleteDeliveryBatchV2
            or type(issuance) is not H1CompletionIssuanceV4
            or effects.locator.intent_id != selected.intent.subject.subject_id
        ):
            raise H1ScopedAuthorizationDeliveryWorkHold(
                "selected V4 Effects issuance is unavailable"
            )
        prepared = issuance.assembly.prepared_completion
        members = (
            H1ScopedAuthorizationDeliveryWorkReaderV1._record(
                "agent_loop", make_terminal_run_member(prepared.run)
            ),
            H1ScopedAuthorizationDeliveryWorkReaderV1._record(
                "agent_loop", make_terminal_manifest_member(prepared.terminal_manifest)
            ),
            H1ScopedAuthorizationDeliveryWorkReaderV1._record(
                "effects", make_scoped_intent_member(issuance.effects_result.intent_result.intent)
            ),
        )
        source = DeliveryWorkSourceV1(
            tenant_id=completion.prepared.request.identity.tenant_id,
            intent_id=members[2].record_id,
            selected_decision_id=completion.decision_id,
            selected_decision_fingerprint=completion.decision_fingerprint,
            terminal_run=members[0],
            terminal_manifest=members[1],
            intent=members[2],
        )
        if (
            source.intent_id != effects.locator.intent_id
            or source.intent_id != selected.intent.subject.subject_id
            or any(
                H1ScopedAuthorizationDeliveryWorkReaderV1._member_count(
                    member, completion.prepared.request.complete_records
                )
                != 1
                for member in members
            )
        ):
            raise H1ScopedAuthorizationDeliveryWorkHold("selected V4 Effects members differ")
        return source

    @staticmethod
    def _record(
        owner: str, member: CompletionCanonicalMember | ScopedIntentCanonicalMemberV3
    ) -> DeliveryWorkSourceMemberV1:
        return DeliveryWorkSourceMemberV1(
            owner=owner,
            record_kind=member.record_kind,
            record_id=member.record_id,
            schema_id=member.schema_id,
            canonical_record_bytes=member.canonical_record_bytes,
            fingerprint=member.fingerprint,
        )

    @staticmethod
    def _member_count(
        member: DeliveryWorkSourceMemberV1, records: tuple[OwnerRecordBytes, ...]
    ) -> int:
        return sum(
            (
                row.owner,
                row.record_kind,
                row.record_id,
                row.schema_id,
                row.canonical_bytes,
                row.fingerprint,
            )
            == (
                member.owner,
                member.record_kind,
                member.record_id,
                member.schema_id,
                member.canonical_record_bytes,
                member.fingerprint,
            )
            for row in records
        )


__all__ = [
    "H1ScopedAuthorizationDeliveryWorkCaptureV1",
    "H1ScopedAuthorizationDeliveryWorkEvidenceV1",
    "H1ScopedAuthorizationDeliveryWorkHold",
    "H1ScopedAuthorizationDeliveryWorkReaderV1",
]
