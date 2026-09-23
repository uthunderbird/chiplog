"""Pure owner preparation over immutable broker observations and a typed store port.

Publication adapters must reproduce inputs at the writer cut and invoke the
isolated owner again. This service never emits bytes or issues a send ticket.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from .contracts import (
    AcceptEffectCommand,
    AttemptState,
    AuthorizeDispatchCommand,
    BeforeSendDispositionCommand,
    ClosedEffectObligation,
    CommandIdentity,
    CommitSendCommand,
    CurrentEffectInputs,
    DispatchAuthorization,
    EffectCommand,
    EffectDenied,
    EffectRecord,
    EffectResult,
    EffectSnapshot,
    EffectsObservationPort,
    EffectsPublicationPort,
    EffectStoreSnapshot,
    ExactHead,
    ExternalActionIntent,
    OpenEffectObligation,
    PreparedEffectPublication,
    PublishDeliveryIntentCommand,
    PublishPlanEffectCommand,
    PublishRecoveryIntentCommand,
    ReconcileEffectCommand,
    RecordEvidenceCommand,
    TransmissionAttempt,
)
from .domain import (
    TERMINAL_STATES,
    ChildMemberEvidence,
    EffectRuleViolation,
    reduce_child_evidence,
    require_current_authority,
    require_fresh_recovery_originals,
    require_safe_retransmission,
    require_semantics,
    require_transition,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _ref(identity: str, value: object) -> ExactHead:
    digest = _digest(value)
    return ExactHead(subject_id=identity, head=identity + "/" + digest, fingerprint=digest)


def _initial(intent: ExternalActionIntent) -> EffectSnapshot:
    data = intent.model_dump(mode="json")
    del data["fingerprint"]
    if intent.fingerprint != _digest(data):
        raise EffectRuleViolation("DENIED", "intent fingerprint does not bind complete owner bytes")
    if (
        intent.purpose.kind in {"COMPENSATION", "AUTHORIZE_DUPLICATE_RISK"}
        and intent.authority.act != intent.purpose.preview_adoption
    ):
        raise EffectRuleViolation(
            "DENIED", "recovery intent authority does not bind its exact adopted preview"
        )
    return EffectSnapshot(
        intent=intent,
        attempt=_ref(intent.intent_id + "/attempt", data),
        state="INTENT_RECORDED",
        authorizations=(),
        transmissions=(),
        evidence=(),
        unresolved_obligations=(),
    )


def _require_no_replacement(
    intent: ExternalActionIntent, expected: EffectStoreSnapshot, current: CurrentEffectInputs
) -> None:
    """Corroborate broker scope; identities and previews cannot erase crossed work."""
    latest = {row.snapshot.intent.intent_id: row.snapshot for row in expected.records}
    by_head = {snapshot.attempt: snapshot for snapshot in latest.values()}
    supplied = current.blocking_effect_heads
    if len(set(supplied)) != len(supplied):
        raise EffectRuleViolation("STALE", "duplicate blocking effect heads")
    crossed = {"SEND_COMMITTED", "SENT", "OUTCOME_UNKNOWN", "PARTIAL"}
    blockers: dict[ExactHead, EffectSnapshot] = {}
    for ref in supplied:
        snapshot = by_head.get(ref)
        if snapshot is None or snapshot.state not in crossed | {"PARTIAL_CONFIRMED"}:
            raise EffectRuleViolation(
                "STALE", "blocking effect head is not current unresolved work"
            )
        blockers[ref] = snapshot
    # Independently catch the elementary identity-reset case even if broker scope
    # omitted it. Broader semantic/dependency scope remains broker responsibility.
    for snapshot in latest.values():
        if (
            snapshot.state in crossed
            and snapshot.intent.authority.recipient == intent.authority.recipient
            and (
                snapshot.intent.effect_fingerprint == intent.effect_fingerprint
                or snapshot.intent.payload == intent.payload
            )
        ):
            blockers[snapshot.attempt] = snapshot
    purpose = intent.purpose
    originals = (
        (purpose.original,)
        if purpose.kind == "COMPENSATION"
        else purpose.unresolved_attempts
        if purpose.kind == "AUTHORIZE_DUPLICATE_RISK"
        else ()
    )
    if originals:
        require_fresh_recovery_originals(intent, current.current_original_ambiguity_heads)
    matched = set()
    for snapshot in blockers.values():
        if snapshot.intent.intent_id == intent.intent_id:
            continue  # Same-attempt retransmission still requires its separate proof.
        original_ref = ExactHead(
            subject_id=snapshot.intent.intent_id,
            head=snapshot.intent.intent_id + "/" + snapshot.intent.fingerprint,
            fingerprint=snapshot.intent.fingerprint,
        )
        ambiguity = (
            snapshot.recovery_obligation.obligation
            if snapshot.recovery_obligation is not None
            else snapshot.attempt
        )
        matches = [
            original
            for original in originals
            if original.original_intent == original_ref
            and original.original_binding == snapshot.intent.semantics
            and original.ambiguity_reconciliation_head == ambiguity
        ]
        if len(matches) != 1:
            raise EffectRuleViolation(
                "RECOVERY_HOLD", "unresolved effect blocks replacement action"
            )
        matched.add(matches[0])
    if originals and set(originals) != matched:
        raise EffectRuleViolation(
            "STALE", "recovery preview does not match current blocking effects"
        )


def _bind_record(
    command: EffectCommand,
    expected: EffectStoreSnapshot,
    previous: EffectRecord | None,
    snapshot: EffectSnapshot,
    kind: str,
    companions: tuple[ExactHead, ...],
) -> PreparedEffectPublication:
    obligation = snapshot.recovery_obligation
    if snapshot.state in {"OUTCOME_UNKNOWN", "PARTIAL"} and obligation is None:
        if previous is None or not snapshot.transmissions:
            raise EffectRuleViolation(
                "RECOVERY_HOLD", "unknown outcome lacks original crossed lineage"
            )
        original_children = tuple(child.transmission for child in snapshot.transmissions)
        intent_ref = ExactHead(
            subject_id=snapshot.intent.intent_id,
            head=snapshot.intent.intent_id + "/" + snapshot.intent.fingerprint,
            fingerprint=snapshot.intent.fingerprint,
        )
        opening = _ref(
            snapshot.intent.intent_id + "/reconciliation",
            {
                "intent": intent_ref.model_dump(mode="json"),
                "original_attempt": previous.snapshot.attempt.model_dump(mode="json"),
                "children": [item.model_dump(mode="json") for item in original_children],
                "semantics": snapshot.intent.semantics.model_dump(mode="json"),
                "command_id": command.identity.command_id,
            },
        )
        obligation = OpenEffectObligation(
            obligation=opening,
            intent=intent_ref,
            original_attempt=previous.snapshot.attempt,
            original_crossed_children=original_children,
            semantics=snapshot.intent.semantics,
            safe_actions=(
                "AUTHENTICATED_PROVIDER_READ",
                "BOUND_RECONCILIATION",
                "EXPLICIT_RECOVERY_AUTHORIZATION",
            ),
            denied_actions=("BLIND_RETRY", "CONFLICTING_REPLACEMENT", "DEPENDENT_CONTINUATION"),
        )
    elif (
        snapshot.state in {"CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"}
        and obligation is not None
        and obligation.kind == "OPEN"
    ):
        closure = _ref(
            obligation.obligation.subject_id,
            {
                "opening": obligation.model_dump(mode="json"),
                "evidence": [item.model_dump(mode="json") for item in snapshot.evidence],
                "children": [
                    item.transmission.model_dump(mode="json") for item in snapshot.transmissions
                ],
                "outcome": snapshot.state,
            },
        )
        obligation = ClosedEffectObligation.model_validate(
            {
                "obligation": closure,
                "opening": obligation,
                "closure_evidence": snapshot.evidence,
                "complete_children": tuple(item.transmission for item in snapshot.transmissions),
                "outcome": snapshot.state,
                "semantics": snapshot.intent.semantics,
            }
        )
    snapshot = snapshot.model_copy(
        update={
            "recovery_obligation": obligation,
            "unresolved_obligations": (obligation.obligation,)
            if obligation is not None and obligation.kind == "OPEN"
            else (),
        }
    )
    data = snapshot.model_dump(mode="json")
    del data["attempt"]
    attempt = _ref(
        snapshot.intent.intent_id + "/attempt",
        {
            "command": command.identity.model_dump(mode="json"),
            "predecessor": None if previous is None else previous.record.model_dump(mode="json"),
            "snapshot": data,
        },
    )
    snapshot = snapshot.model_copy(update={"attempt": attempt})
    body = {
        "command": command.identity.model_dump(mode="json"),
        "predecessor": None if previous is None else previous.record.model_dump(mode="json"),
        "kind": kind,
        "snapshot": snapshot.model_dump(mode="json"),
        "source_command": command.canonical_bytes().hex(),
    }
    record = EffectRecord.model_validate(
        {
            "record": _ref("effects/" + command.identity.command_id, body),
            "command": command.identity,
            "predecessor": None if previous is None else previous.record,
            "kind": kind,
            "snapshot": snapshot,
            "source_command": command.canonical_bytes(),
        }
    )
    return PreparedEffectPublication(
        record=record, exact_companion_manifest=companions, expected_store=expected
    )


def _previous(command: EffectCommand, expected: EffectStoreSnapshot) -> EffectRecord:
    supplied_intent: ExactHead | None = None
    if isinstance(command, CommitSendCommand):
        intent_id, attempt = command.authorization.intent.subject_id, command.expected_attempt
        supplied_intent = command.authorization.intent
    elif isinstance(command, RecordEvidenceCommand):
        matches = [
            row
            for row in expected.records
            if row.snapshot.attempt == command.expected_attempt
            and any(
                child.transmission == command.authentication.transmission
                for child in row.snapshot.transmissions
            )
        ]
        if len(matches) != 1:
            raise EffectRuleViolation("STALE", "evidence lacks exact attempt/transmission subject")
        intent_id, attempt = matches[0].snapshot.intent.intent_id, command.expected_attempt
        if command.authentication.kind == "AUTHENTICATED_PROVIDER_EVIDENCE":
            supplied_intent = command.authentication.intent
    elif isinstance(
        command, (AuthorizeDispatchCommand, BeforeSendDispositionCommand, ReconcileEffectCommand)
    ):
        intent_id, attempt = command.intent.subject_id, command.expected_attempt
        supplied_intent = command.intent
    else:
        raise EffectRuleViolation("DENIED", "initial operation cannot read existing attempt")
    matches = [row for row in expected.records if row.snapshot.intent.intent_id == intent_id]
    if not matches or matches[-1].snapshot.attempt != attempt:
        raise EffectRuleViolation("STALE", "exact current logical attempt head missing")
    if supplied_intent is not None:
        intent = matches[-1].snapshot.intent
        exact = ExactHead(
            subject_id=intent.intent_id,
            head=intent.intent_id + "/" + intent.fingerprint,
            fingerprint=intent.fingerprint,
        )
        if supplied_intent != exact:
            raise EffectRuleViolation("STALE", "logical intent aliases different immutable bytes")
    return matches[-1]


def _evidence_state(snapshot: EffectSnapshot) -> AttemptState:
    observations: list[ChildMemberEvidence] = []
    for event in snapshot.evidence_records:
        members = snapshot.intent.inseparable_bundle_members
        children = tuple(child.transmission for child in snapshot.transmissions)
        for supplied, allowed in (
            (event.covered_children, children),
            (event.occurred_members, members),
            (event.permanently_incapable_members, members),
        ):
            if len(set(supplied)) != len(supplied) or any(item not in allowed for item in supplied):
                raise EffectRuleViolation(
                    "CONFLICT", "evidence contains unknown or duplicate exact member"
                )
        ref = _ref(event.evidence_id, event.model_dump(mode="json"))
        for child in event.covered_children:
            for member in snapshot.intent.inseparable_bundle_members:
                outcome: Literal[
                    "OCCURRED", "PERMANENTLY_INCAPABLE", "POSSIBLE", "ABSENCE_OBSERVED"
                ]
                if member in event.occurred_members:
                    outcome = "OCCURRED"
                    observations.append(ChildMemberEvidence(ref, child, member, outcome))
                if member in event.permanently_incapable_members:
                    observations.append(
                        ChildMemberEvidence(ref, child, member, "PERMANENTLY_INCAPABLE")
                    )
                if (
                    member not in event.occurred_members
                    and member not in event.permanently_incapable_members
                ):
                    observations.append(ChildMemberEvidence(ref, child, member, "POSSIBLE"))
    return reduce_child_evidence(
        snapshot.state,
        snapshot.transmissions,
        snapshot.intent.inseparable_bundle_members,
        tuple(observations),
    )


def prepare_transition(
    command: EffectCommand,
    expected: EffectStoreSnapshot,
    current: CurrentEffectInputs,
) -> PreparedEffectPublication:
    """Produce exact owner candidate; broker must authenticate every input again."""
    identity: CommandIdentity = command.identity
    if (
        current.command_id != identity.command_id
        or current.command_fingerprint != identity.fingerprint
        or current.store_frontier != expected.tenant_head
        or identity.expected_tenant_head != expected.tenant_head
    ):
        raise EffectRuleViolation("STALE", "command/current snapshot temporal cut differs")
    if any(row.command.command_id == identity.command_id for row in expected.records):
        raise EffectRuleViolation(
            "CONFLICT", "existing identity requires authenticated broker replay"
        )
    companions: tuple[ExactHead, ...] = ()
    if isinstance(
        command,
        (
            AcceptEffectCommand,
            PublishPlanEffectCommand,
            PublishDeliveryIntentCommand,
            PublishRecoveryIntentCommand,
        ),
    ):
        if any(
            row.snapshot.intent.intent_id == command.intent.intent_id for row in expected.records
        ):
            raise EffectRuleViolation("CONFLICT", "intent identity already exists")
        if command.fence != current.fence:
            raise EffectRuleViolation("STALE", "initial publication fence differs")
        require_semantics(command.intent.semantics, current.supported_semantics, durable=False)
        require_current_authority(command.intent, current.authority, current.observed_time_ns)
        if command.intent.authority.tenant_id != expected.tenant_id:
            raise EffectRuleViolation("DENIED", "foreign tenant intent")
        snapshot = _initial(command.intent)
        _require_no_replacement(command.intent, expected, current)
        if isinstance(command, AcceptEffectCommand):
            if (
                command.initialized_call != current.initialized_call
                or current.active_run_head is None
                or command.fence.kind == "POST_TERMINAL_RECOVERY_WORK"
                or command.fence.run_head != current.active_run_head
            ):
                raise EffectRuleViolation(
                    "STALE", "acceptance requires exact initialized call and ACTIVE Run"
                )
            companions = command.complete_acceptance_manifest
            kind = "INTENT_ACCEPTED"
        elif isinstance(command, PublishPlanEffectCommand):
            companions, kind = command.complete_publication_manifest, "PLAN_EFFECT_PUBLISHED"
        elif isinstance(command, PublishDeliveryIntentCommand):
            if (
                command.intent.purpose.kind != "DELIVERY"
                or command.intent.purpose.binding.acceptance != command.complete_acceptance
            ):
                raise EffectRuleViolation(
                    "DENIED", "delivery requires exact CompleteAcceptance companion"
                )
            companions, kind = command.complete_delivery_manifest, "DELIVERY_PREPARED"
        else:
            require_fresh_recovery_originals(
                command.intent, current.current_original_ambiguity_heads
            )
            if command.original_heads != current.current_original_ambiguity_heads:
                raise EffectRuleViolation("STALE", "recovery creation uses stale original heads")
            kind = "RECOVERY_INTENT_PUBLISHED"
        return _bind_record(command, expected, None, snapshot, kind, companions)

    previous = _previous(command, expected)
    snapshot = previous.snapshot
    if isinstance(command, (AuthorizeDispatchCommand, CommitSendCommand)):
        _require_no_replacement(snapshot.intent, expected, current)
    state: AttemptState
    if isinstance(command, AuthorizeDispatchCommand):
        require_semantics(snapshot.intent.semantics, current.supported_semantics, durable=True)
        require_semantics(command.semantics, snapshot.intent.semantics, durable=True)
        require_current_authority(snapshot.intent, current.authority, current.observed_time_ns)
        if command.current_authority != current.authority or command.fence != current.fence:
            raise EffectRuleViolation("STALE", "authorization current inputs differ")
        require_transition(snapshot.state, "DISPATCH_AUTHORIZED", writer="SEND_ADAPTER")
        authorization = DispatchAuthorization(
            authorization=_ref(
                "authorization/" + identity.command_id, command.model_dump(mode="json")
            ),
            intent=command.intent,
            expected_attempt=command.expected_attempt,
            semantics=command.semantics,
            current_authority=current.authority,
            fence=current.fence,
        )
        snapshot = snapshot.model_copy(
            update={
                "state": "DISPATCH_AUTHORIZED",
                "authorizations": (*snapshot.authorizations, authorization),
            }
        )
        kind = "DISPATCH_AUTHORIZED"
    elif isinstance(command, BeforeSendDispositionCommand):
        if (
            command.fence != current.fence
            or command.current_authority != current.authority
            or command.decision_evidence != current.authority_decision
        ):
            raise EffectRuleViolation("STALE", "disposition inputs differ")
        require_transition(snapshot.state, command.disposition, writer="SEND_ADAPTER")
        snapshot = snapshot.model_copy(update={"state": command.disposition})
        kind = "BEFORE_SEND_DISPOSITION"
    elif isinstance(command, CommitSendCommand):
        authorization = command.authorization
        if not snapshot.authorizations or snapshot.authorizations[-1] != authorization:
            raise EffectRuleViolation("STALE", "send authorization is stale or retired")
        if command.fence != current.fence or command.current_time_ns != current.observed_time_ns:
            raise EffectRuleViolation("STALE", "send fence or trusted time differs")
        require_semantics(authorization.semantics, current.supported_semantics, durable=True)
        require_semantics(snapshot.intent.semantics, authorization.semantics, durable=True)
        require_current_authority(snapshot.intent, current.authority, current.observed_time_ns)
        if snapshot.intent.purpose.kind in {"COMPENSATION", "AUTHORIZE_DUPLICATE_RISK"}:
            require_fresh_recovery_originals(
                snapshot.intent, current.current_original_ambiguity_heads
            )
        if command.transmission.kind == "FIRST_TRANSMISSION":
            if authorization.fence != command.fence:
                raise EffectRuleViolation(
                    "STALE", "first send authorization belongs to another fence"
                )
            if snapshot.transmissions:
                raise EffectRuleViolation("CONFLICT", "first transmission already allocated")
            require_transition(
                snapshot.state, "SEND_COMMITTED", writer="SEND_ADAPTER", new_child=True
            )
            state, proof, kind = "SEND_COMMITTED", None, "SEND_COMMITTED"
        else:
            require_safe_retransmission(
                snapshot.state,
                snapshot.intent,
                snapshot.transmissions,
                command.transmission,
                independently_verified_proof=current.independently_verified_safe_proof,
                current_authority=current.authority,
                supported_semantics=current.supported_semantics,
                now_ns=current.observed_time_ns,
            )
            state, proof, kind = (
                snapshot.state,
                command.transmission.proof,
                "TRANSMISSION_ALLOCATED",
            )
        child = TransmissionAttempt(
            transmission=_ref(
                snapshot.intent.intent_id + "/transmission/" + str(command.transmission.ordinal),
                command.model_dump(mode="json"),
            ),
            intent=authorization.intent,
            ordinal=command.transmission.ordinal,
            semantics=authorization.semantics,
            dispatch_time_ns=current.observed_time_ns,
            payload_fingerprint=snapshot.intent.effect_fingerprint,
            recipient=snapshot.intent.authority.recipient,
            idempotency_fence_key=snapshot.intent.idempotency_fence_key,
            send_commit=_ref("send/" + identity.command_id, command.model_dump(mode="json")),
            coverage_proof=proof,
        )
        snapshot = snapshot.model_copy(
            update={"state": state, "transmissions": (*snapshot.transmissions, child)}
        )
    elif isinstance(command, RecordEvidenceCommand):
        if current.authenticated_evidence != command:
            raise EffectRuleViolation(
                "DENIED", "evidence not independently authenticated for exact command"
            )
        if hashlib.sha256(command.raw_bytes).hexdigest() != command.authentication.raw_digest:
            raise EffectRuleViolation("DENIED", "observation raw bytes do not match authentication")
        if command.authentication.tenant_id != expected.tenant_id:
            raise EffectRuleViolation("DENIED", "foreign tenant evidence")
        if command.authentication.kind == "BROKER_TRANSPORT_OBSERVATION" and (
            command.observation
            not in {"TRANSPORT_SENT", "TIMEOUT", "CONNECTION_LOST", "MALFORMED_RESPONSE"}
            or command.occurred_members
            or command.permanently_incapable_members
            or command.authentication.exact_recipient != snapshot.intent.authority.recipient
            or command.authentication.adapter_contract_version
            != snapshot.intent.semantics.adapter_contract_version
        ):
            raise EffectRuleViolation(
                "DENIED", "local transport observation cannot claim provider outcome"
            )
        if command.semantics != snapshot.intent.semantics:
            raise EffectRuleViolation(
                "DISPATCH_VERSION_HOLD", "evidence binds another original reducer"
            )
        evidence = _ref(command.evidence_id, command.model_dump(mode="json"))
        if any(item.subject_id == command.evidence_id for item in snapshot.evidence):
            raise EffectRuleViolation("CONFLICT", "existing evidence requires exact journal replay")
        snapshot = snapshot.model_copy(
            update={
                "evidence": (*snapshot.evidence, evidence),
                "evidence_records": (*snapshot.evidence_records, command),
            }
        )
        # Terminal original state remains immutable. A missing bound reducer
        # does not prevent authenticated independent evidence from being stored.
        if (
            snapshot.state not in TERMINAL_STATES
            and current.original_reducer_semantics == command.semantics
        ):
            if command.observation == "TRANSPORT_SENT" and snapshot.state == "SEND_COMMITTED":
                state = "SENT"
            else:
                state = _evidence_state(snapshot)
            if state != snapshot.state:
                require_transition(snapshot.state, state, writer="AUTHENTICATED_EVIDENCE")
            snapshot = snapshot.model_copy(update={"state": state})
        kind = "EVIDENCE_RECORDED"
    elif isinstance(command, ReconcileEffectCommand):
        if (
            command.authorized_reconciler != current.authorized_reconciler
            or command.fence != current.fence
            or command.complete_children
            != tuple(item.transmission for item in snapshot.transmissions)
            or command.durable_evidence != snapshot.evidence
        ):
            raise EffectRuleViolation("STALE", "reconciliation lacks exact complete current inputs")
        require_semantics(command.semantics, snapshot.intent.semantics, durable=True)
        require_semantics(command.semantics, current.original_reducer_semantics, durable=True)
        state = _evidence_state(snapshot)
        if state != snapshot.state:
            require_transition(snapshot.state, state, writer="AUTHORIZED_RECONCILER")
        snapshot = snapshot.model_copy(update={"state": state})
        kind = "RECONCILED"
    else:
        raise EffectRuleViolation("DENIED", "unregistered effect operation")
    return _bind_record(command, expected, previous, snapshot, kind, companions)


class Effects:
    def __init__(self, store: EffectsPublicationPort, observations: EffectsObservationPort) -> None:
        self._store, self._observations = store, observations

    def prepare(self, command: EffectCommand) -> PreparedEffectPublication:
        expected = self._store.snapshot()
        return prepare_transition(command, expected, self._observations.observe(command, expected))

    async def submit(self, command: EffectCommand) -> EffectResult:
        if isinstance(command, PublishDeliveryIntentCommand):
            raise EffectRuleViolation(
                "DENIED", "delivery must join CompleteAcceptance atomic publication"
            )
        selected = self._store.replay(command)
        if selected is not None:
            return selected
        try:
            prepared = self.prepare(command)
        except EffectRuleViolation as error:
            return EffectDenied.model_validate(
                {
                    "disposition": error.code,
                    "command_id": command.identity.command_id,
                    "reason": str(error),
                }
            )
        return await self._store.publish(prepared)
