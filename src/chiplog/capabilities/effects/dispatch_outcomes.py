"""Original-stream outcome interpreter; callers supply no authority to this reducer."""

from .contracts import ExactHead
from .dispatch_outcome_contracts import (
    DispatchEvidenceV2,
    DispatchObligationV2,
    DispatchOutcomePreparationV2,
    DispatchOutcomeRecordV2,
    DispatchOutcomeSnapshotV2,
)
from .dispatch_v2 import canonical, digest, reference, require_intent
from .domain import EffectRuleViolation

TERMINAL = {"CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"}


def evidence_head(evidence: DispatchEvidenceV2) -> ExactHead:
    return reference(evidence.evidence_id, evidence.canonical_bytes())


def prepare_outcome(request: DispatchOutcomePreparationV2) -> DispatchOutcomeRecordV2:
    send, previous, command = request.original_send, request.previous, request.command
    original, before = send.snapshot, previous.snapshot
    intent = original.intent
    require_intent(intent)
    children = tuple(child.transmission for child in original.transmissions)
    intent_ref = reference(intent.intent_id, intent.canonical_bytes())
    if (
        send.kind != "SEND_COMMITTED"
        or original.state != "SEND_COMMITTED"
        or len(children) != 1
        or original.transmissions[0].ordinal != 0
        or before.intent != intent
        or before.transmissions != original.transmissions
        or before.authorizations != original.authorizations
        or command.original_send != send.record
        or command.intent != intent_ref
        or command.expected_attempt != before.attempt
        or command.complete_children != children
        or (not isinstance(previous, DispatchOutcomeRecordV2) and previous != send)
    ):
        raise EffectRuleViolation("STALE", "outcome original SEND/intent/current child differs")
    old = previous.snapshot if isinstance(previous, DispatchOutcomeRecordV2) else None
    evidence = () if old is None else old.evidence
    heads = tuple(evidence_head(item) for item in evidence)
    if command.prior_evidence != heads or (old is not None and old.evidence_heads != heads):
        raise EffectRuleViolation("STALE", "complete ordered evidence inventory differs")
    obligation = (
        old.obligation
        if old is not None
        else DispatchObligationV2(
            obligation=reference(intent.intent_id + "/reconciliation", send.canonical_bytes()),
            original_send=send.record,
            original_intent=intent_ref,
            original_children=children,
            closure_predicate="ALL_ORIGINAL_CHILDREN_TERMINAL_V2",
            resolver_binding="AUTHENTICATED_ORIGINAL_PRINCIPAL_V2",
            state="OPEN",
            closure_evidence=(),
        )
    )
    if command.operation == "APPEND_EVIDENCE":
        item = command.evidence
        if item is None or command.resolver is not None:
            raise EffectRuleViolation("DENIED", "evidence append has wrong operation fields")
        if (
            item.transmission not in children
            or item.raw_digest != digest(item.raw_bytes)
            or any(item.evidence_id == prior.evidence_id for prior in evidence)
            or (
                item.observation != "PROVIDER_RECEIPT"
                and (item.occurred_members or item.permanently_incapable_members)
            )
        ):
            raise EffectRuleViolation("CONFLICT", "evidence identity, raw digest or child differs")
        evidence = (*evidence, item)
        kind = "EVIDENCE_RECORDED"
    else:
        if command.evidence is not None or command.resolver is None or obligation.state != "OPEN":
            raise EffectRuleViolation("CONFLICT", "resolver needs exact still-open obligation")
        kind = "RECONCILED"
    members = tuple(member.subject_id for member in intent.mandate.bundle_members)
    occurred: set[str] = set()
    incapable: set[str] = set()
    for item in evidence:
        positive, negative = set(item.occurred_members), set(item.permanently_incapable_members)
        if (
            len(positive) != len(item.occurred_members)
            or len(negative) != len(item.permanently_incapable_members)
            or positive & negative
            or not (positive | negative) <= set(members)
        ):
            raise EffectRuleViolation("DENIED", "evidence partition is malformed")
        occurred.update(positive)
        incapable.update(negative)
    conflict = bool(occurred & incapable) or (old is not None and old.conflicting_evidence)
    complete = not conflict and occurred | incapable == set(members)
    state = "OUTCOME_UNKNOWN"
    if complete:
        state = (
            "CONFIRMED"
            if not incapable
            else ("FAILED_NO_EFFECT" if not occurred else "PARTIAL_CONFIRMED")
        )
    elif occurred and not conflict:
        state = "PARTIAL"
    if old is not None and old.state in TERMINAL:
        state = old.state  # late rival evidence holds consumers without rewriting the old claim
    if command.operation == "RESOLVE_OBLIGATION":
        if not complete:
            raise EffectRuleViolation("RECOVERY_HOLD", "not every original child is terminal")
        obligation = obligation.model_copy(
            update={
                "state": "CLOSED",
                "closure_evidence": tuple(evidence_head(item) for item in evidence),
            }
        )
    elif old is None and state == "PARTIAL_CONFIRMED":
        # Direct late evidence at SEND (before an explicit opening) must take PARTIAL.
        # Normal emission first selects BOUNDARY_CROSSED/OUTCOME_UNKNOWN, whose
        # registered successor may be PARTIAL_CONFIRMED; its obligation stays OPEN.
        state = "PARTIAL"
    attempt = reference(intent.intent_id + "/attempt", command.canonical_bytes())
    snapshot = DispatchOutcomeSnapshotV2.model_validate(
        {
            "intent": intent,
            "attempt": attempt,
            "state": state,
            "authorizations": original.authorizations,
            "transmissions": original.transmissions,
            "evidence": evidence,
            "evidence_heads": tuple(evidence_head(item) for item in evidence),
            "obligation": obligation,
            "conflicting_evidence": conflict,
        }
    )
    body = canonical(
        {
            "command": command.model_dump(mode="json"),
            "snapshot": snapshot.model_dump(mode="json"),
            "kind": kind,
            "predecessor": previous.record.model_dump(mode="json"),
        }
    )
    return DispatchOutcomeRecordV2.model_validate(
        {
            "schema_id": "chiplog.effects.dispatch-outcome-record.v2",
            "record": reference("effect-record/" + command.identity.command_id, body),
            "predecessor": previous.record,
            "kind": kind,
            "command": command,
            "snapshot": snapshot,
        }
    )
