"""Dispatch reducer counterhistories independent of physical storage admission."""

from pathlib import Path
from typing import Literal, cast, get_args

import pytest
from tests.support.effects import child, head, intent, semantics

from chiplog.capabilities.effects.contracts import (
    AdoptedAuthorityAct,
    AttemptState,
    CompensationPurpose,
    OriginalAmbiguity,
    SafeRetransmission,
    TransmissionAttempt,
)
from chiplog.capabilities.effects.domain import (
    ChildMemberEvidence,
    EffectRuleViolation,
    reduce_child_evidence,
    require_current_authority,
    require_fresh_recovery_originals,
    require_safe_retransmission,
    require_semantics,
    require_transition,
)

Outcome = Literal["OCCURRED", "PERMANENTLY_INCAPABLE", "POSSIBLE", "ABSENCE_OBSERVED"]


def test_every_parent_edge_is_bound_to_the_independent_vision_table() -> None:
    source = Path(__file__).resolve().parents[3] / "design-docs/VISION.md"
    text = source.read_text().split("Legal parent transitions and consequences are closed:", 1)[1]
    table = text.split("`CONFIRMED`,", 1)[0]
    allowed: set[tuple[str, str]] = set()
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        # Pipes inside code spans are state alternatives, not Markdown columns.
        source_state = line.split("`", 2)[1]
        target_column = line.split("`", 3)[3]
        target_states = target_column.split("`", 1)[0]
        if not target_states:
            continue
        prior = cast(AttemptState, source_state)
        retransmission = "new `TransmissionAttempt`" in line
        for target in target_states.split(" | "):
            proposed = cast(AttemptState, target)
            if not retransmission:
                allowed.add((prior, proposed))
            before_send = prior in {"INTENT_RECORDED", "HELD_BEFORE_SEND", "DISPATCH_AUTHORIZED"}
            require_transition(
                prior,
                proposed,
                writer="SEND_ADAPTER"
                if before_send or retransmission
                else "AUTHENTICATED_EVIDENCE",
                new_child=retransmission or proposed == "SEND_COMMITTED",
            )
    assert {prior for prior, _ in allowed} == {
        "INTENT_RECORDED",
        "HELD_BEFORE_SEND",
        "DISPATCH_AUTHORIZED",
        "SEND_COMMITTED",
        "SENT",
        "OUTCOME_UNKNOWN",
        "PARTIAL",
    }
    for prior in get_args(AttemptState):
        for proposed in get_args(AttemptState):
            if (prior, proposed) in allowed:
                continue
            before_send = prior in {"INTENT_RECORDED", "HELD_BEFORE_SEND", "DISPATCH_AUTHORIZED"}
            with pytest.raises(EffectRuleViolation):
                require_transition(
                    prior,
                    proposed,
                    writer="SEND_ADAPTER" if before_send else "AUTHENTICATED_EVIDENCE",
                    new_child=proposed == "SEND_COMMITTED" and before_send,
                )


@pytest.mark.parametrize(
    "state",
    [
        "CONFIRMED",
        "FAILED_NO_EFFECT",
        "PARTIAL_CONFIRMED",
        "CANCELLED_BEFORE_SEND",
        "SUPERSEDED_BEFORE_SEND",
    ],
)
def test_terminal_attempt_cannot_restart_even_with_new_child(state: AttemptState) -> None:
    with pytest.raises(EffectRuleViolation):
        require_transition(state, "DISPATCH_AUTHORIZED", writer="SEND_ADAPTER")
    with pytest.raises(EffectRuleViolation):
        require_transition(state, state, writer="SEND_ADAPTER", new_child=True)


def test_first_child_is_atomic_and_evidence_cannot_authorize_send() -> None:
    for writer in ("AUTHENTICATED_EVIDENCE", "AUTHORIZED_RECONCILER"):
        with pytest.raises(EffectRuleViolation):
            require_transition(
                "DISPATCH_AUTHORIZED",
                "SEND_COMMITTED",
                writer=writer,
                new_child=True,
            )
    with pytest.raises(EffectRuleViolation):
        require_transition("DISPATCH_AUTHORIZED", "SEND_COMMITTED", writer="SEND_ADAPTER")
    with pytest.raises(EffectRuleViolation):
        require_transition("OUTCOME_UNKNOWN", "SENT", writer="AUTHENTICATED_EVIDENCE")


@pytest.mark.parametrize("durable", [False, True])
def test_each_version_component_mismatch_or_unavailability_denies(durable: bool) -> None:
    original = semantics()
    for name in type(original).model_fields:
        changed = original.model_copy(update={name: "changed"})
        with pytest.raises(EffectRuleViolation) as error:
            require_semantics(original, changed, durable=durable)
        assert error.value.code == (
            "DISPATCH_VERSION_HOLD" if durable else "DISPATCH_VERSION_DENIED"
        )
    with pytest.raises(EffectRuleViolation):
        require_semantics(original, None, durable=durable)


def test_later_unaccounted_child_prevents_success_or_no_effect() -> None:
    children = (child(0), child(1))
    member = head("member")
    for outcome in ("OCCURRED", "PERMANENTLY_INCAPABLE", "ABSENCE_OBSERVED"):
        evidence = ChildMemberEvidence(head("receipt"), children[0].transmission, member, outcome)
        assert (
            reduce_child_evidence("OUTCOME_UNKNOWN", children, (member,), (evidence,))
            == "OUTCOME_UNKNOWN"
        )
    with pytest.raises(EffectRuleViolation):
        reduce_child_evidence("SENT", tuple(reversed(children)), (member,), ())


def test_conflicting_evidence_and_point_in_time_absence_preserve_uncertainty() -> None:
    transmission = child(0)
    member = head("member")
    observations = tuple(
        ChildMemberEvidence(
            head(f"receipt-{n}"), transmission.transmission, member, cast(Outcome, outcome)
        )
        for n, outcome in enumerate(("OCCURRED", "PERMANENTLY_INCAPABLE"))
    )
    assert (
        reduce_child_evidence("SENT", (transmission,), (member,), observations) == "OUTCOME_UNKNOWN"
    )
    assert reduce_child_evidence("PARTIAL", (transmission,), (member,), ()) == "PARTIAL"


def test_full_mixed_bundle_uses_deny_state_before_terminal_mixed_result() -> None:
    transmission = child(0)
    first, second = head("first"), head("second")
    observations = (
        ChildMemberEvidence(head("receipt1"), transmission.transmission, first, "OCCURRED"),
        ChildMemberEvidence(
            head("receipt2"), transmission.transmission, second, "PERMANENTLY_INCAPABLE"
        ),
    )
    assert (
        reduce_child_evidence("SENT", (transmission,), (first, second), observations) == "PARTIAL"
    )
    assert (
        reduce_child_evidence("PARTIAL", (transmission,), (first, second), observations)
        == "PARTIAL_CONFIRMED"
    )
    with pytest.raises(EffectRuleViolation):
        reduce_child_evidence("PARTIAL", (transmission,), (first,), observations)


def test_whole_authority_and_expiry_must_match_at_the_cut() -> None:
    original = intent()
    require_current_authority(original, original.authority, 99)
    for field in (
        "planning_revision",
        "authorization_evidence",
        "hold_conflict_order",
        "consequence_scope",
        "communication_mandate",
        "disclosure_projection",
        "interaction_context",
        "authenticated_session",
    ):
        with pytest.raises(EffectRuleViolation):
            require_current_authority(
                original, original.authority.model_copy(update={field: head("changed")}), 99
            )
    for field in (
        "dependencies",
        "affected_party_constraints",
        "authority_sources",
        "verification_contradiction",
        "authority_applicability",
        "factual_assertion_evidence",
    ):
        with pytest.raises(EffectRuleViolation):
            require_current_authority(
                original, original.authority.model_copy(update={field: ()}), 99
            )
    with pytest.raises(EffectRuleViolation):
        require_current_authority(original, original.authority, 100)
    expired = original.authority.model_copy(
        update={"reads": (original.authority.reads[0].model_copy(update={"valid_until_ns": 50}),)}
    )
    expired_intent = original.model_copy(update={"authority": expired})
    with pytest.raises(EffectRuleViolation):
        require_current_authority(expired_intent, expired, 50)


def test_safe_retransmission_proof_covers_every_exact_child_and_current_authority() -> None:
    original = intent()
    children = tuple(
        child(n).model_copy(update={"payload_fingerprint": original.effect_fingerprint})
        for n in range(2)
    )
    proof = SafeRetransmission(
        kind="SAFE_RETRANSMISSION",
        ordinal=2,
        prior_children=tuple(c.transmission for c in children),
        proof_kind="PROVIDER_IDEMPOTENCY_COVERS_ALL",
        proof=head("provider-proof"),
        covered_effect_fingerprint=original.effect_fingerprint,
        coverage_starts_ns=1,
        coverage_expires_ns=90,
    )

    def check(
        request: SafeRetransmission,
        *,
        now: int = 50,
        issued: SafeRetransmission | None = proof,
        observed: tuple[TransmissionAttempt, ...] = children,
    ) -> None:
        require_safe_retransmission(
            "OUTCOME_UNKNOWN",
            original,
            observed,
            request,
            independently_verified_proof=issued,
            current_authority=original.authority,
            supported_semantics=original.semantics,
            now_ns=now,
        )

    check(proof)
    for mutation in (
        {"ordinal": 0},
        {"prior_children": proof.prior_children[:1]},
        {"prior_children": tuple(reversed(proof.prior_children))},
        {"covered_effect_fingerprint": "changed"},
    ):
        changed = proof.model_copy(update=mutation)
        with pytest.raises(EffectRuleViolation):
            check(changed, issued=changed)
    with pytest.raises(EffectRuleViolation):
        check(proof, issued=None)
    with pytest.raises(EffectRuleViolation):
        check(proof, now=90)
    excludes_original = proof.model_copy(update={"coverage_starts_ns": 2})
    with pytest.raises(EffectRuleViolation):
        check(excludes_original, issued=excludes_original)
    permanent = excludes_original.model_copy(
        update={"proof_kind": "ALL_PRIOR_PERMANENTLY_INCAPABLE"}
    )
    check(permanent, issued=permanent)
    with pytest.raises(EffectRuleViolation):
        check(
            proof, observed=(children[0].model_copy(update={"dispatch_time_ns": 51}), children[1])
        )
    with pytest.raises(EffectRuleViolation):
        check(
            proof, observed=(children[0].model_copy(update={"intent": head("other")}), children[1])
        )


def test_compensation_original_head_must_still_be_exact() -> None:
    original = intent()
    prior = head("unknown-original")
    reference = head("adoption")
    compensation = original.model_copy(
        update={
            "purpose": CompensationPurpose(
                kind="COMPENSATION",
                original=OriginalAmbiguity(
                    original_intent=head("original"),
                    original_binding=semantics(),
                    ambiguity_reconciliation_head=prior,
                ),
                consequence_addressed=head("consequence"),
                preview_adoption=AdoptedAuthorityAct(
                    kind="EXACT_PROPOSAL_ADOPTION",
                    proposal=reference,
                    display_digest="preview",
                    adoption=reference,
                    ingress=reference,
                    interpretation=reference,
                ),
            )
        }
    )
    require_fresh_recovery_originals(compensation, (prior,))
    for actual in ((), (head("resolved"),), (prior, prior)):
        with pytest.raises(EffectRuleViolation):
            require_fresh_recovery_originals(compensation, actual)
    assert compensation.purpose.kind == "COMPENSATION"
    assert compensation.purpose.original.ambiguity_reconciliation_head == prior
