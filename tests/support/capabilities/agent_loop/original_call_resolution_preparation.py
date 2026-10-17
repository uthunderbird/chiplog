"""Shared builders extracted without changing their behavior."""

import hashlib

from tests.capabilities.effects.support.dispatch_outcomes import original_send
from tests.support.capabilities.effects.dispatch_outcomes import request as outcome_request

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    AcceptedOpenRecoveryOpeningV1,
    AcceptedOpenTerminalDispositionV1,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    IndependentResolverAuthority,
    LoopRecoveryStream,
    OriginalResolverCut,
    OriginalResolverInvocationV1,
    PrepareOriginalCallResolution,
    original_effect_evidence_reducer_policy_v1,
    original_resolver_authority_policy_v1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    OriginalObligationBinding,
    Present,
)
from chiplog.capabilities.effects.dispatch_outcomes import evidence_head, prepare_outcome


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(subject_id: str, head: str, fingerprint: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject_id, revision=Present(head=head, fingerprint=fingerprint)
    )


def _source(
    record,
    *,
    record_id: str,
    owner: str,
    schema: str,
    subject: CallSubjectHead | None = None,
):
    raw = record.canonical_bytes()
    digest = _sha(raw)
    source_subject = subject or _head(record_id, "record:" + digest, digest)
    return RecoverySourceRecord(
        owner=owner,
        subject=source_subject,
        schema_id=schema,
        canonical_record_bytes=raw,
        selected_decision=_head("selected:" + owner, "decision-head", "d" * 64),
        physical_record=_head(record_id, "record:" + digest, digest),
    )


def _policy_source(record, schema: str, decision: CallSubjectHead) -> RecoverySourceRecord:
    raw = record.canonical_bytes()
    digest = _sha(raw)
    ref = _head(record.policy_id, "record:" + digest, digest)
    return RecoverySourceRecord(
        owner="agent_loop",
        subject=ref,
        schema_id=schema,
        canonical_record_bytes=raw,
        selected_decision=decision,
        physical_record=ref,
    )


def _closed_r16(*, negative: bool = False):
    send = original_send()
    opened = prepare_outcome(outcome_request(send, send))
    evidence_one = prepare_outcome(outcome_request(opened, send, name="receipt-initial"))
    evidence_two = prepare_outcome(
        outcome_request(
            evidence_one,
            send,
            negative=("subject",) if negative else (),
            positive=() if negative else ("subject",),
            name="confirm" if not negative else "incapable",
        )
    )
    closed = prepare_outcome(outcome_request(evidence_two, send, close=True))
    return send, (opened, evidence_one, evidence_two), closed


def request(*, negative: bool = False) -> PrepareOriginalCallResolution:
    send, evidence_records, witness = _closed_r16(negative=negative)
    tenant = send.snapshot.intent.mandate.tenant_id
    binding = OriginalObligationBinding(
        original_run_id="run-original",
        original_call_id="call-original",
        obligation_id="obligation-original",
        obligation_stream_id="stream-original-obligation",
        obligation_head="open-head",
        closure_predicate_id="R14_ORIGINAL_EFFECT_CHILDREN_TERMINAL",
        closure_predicate_version="r14-original-effect-evidence.v1",
        resolver_id="R14_AUTHENTICATED_ORIGINAL_PRINCIPAL_RESOLVER",
        resolver_version="r14-original-resolver.v1",
        reducer_id="R14_ORIGINAL_EFFECT_EVIDENCE_REDUCER",
        reducer_version="r14-effect-evidence.v1",
        evidence_stream_id="stream-original-evidence",
        evidence_head=Absent(),
    )
    opening_record = AcceptedOpenRecoveryOpeningV1(
        opening_id="opening-original",
        source_cut_fingerprint="a" * 64,
        original=binding,
        evidence_sought=(),
        safe_actions=("RESOLVE_ORIGINAL_OBLIGATION",),
        denied_actions=("SEND", "RETRY", "APPEND_EVIDENCE"),
    )
    opening_raw = opening_record.canonical_bytes()
    opening_digest = _sha(opening_raw)
    opening_physical = _head("opening-original", "record:" + opening_digest, opening_digest)
    opening_source = RecoverySourceRecord(
        owner="agent_loop",
        subject=_head(binding.obligation_id, binding.obligation_head, opening_digest),
        schema_id="chiplog.execution.accepted-open-recovery-opening.v1",
        canonical_record_bytes=opening_raw,
        selected_decision=_head("selected:opening", "decision", "1" * 64),
        physical_record=opening_physical,
    )
    intent_ref = send.command.intent
    terminal = AcceptedOpenTerminalDispositionV1(
        kind="RECOVERY_REQUIRED",
        terminal_id="terminal-original",
        source_cut_fingerprint="b" * 64,
        original_call_id=binding.original_call_id,
        response_id="response-original",
        accepted=_head("accepted", "accepted-head", "2" * 64),
        external_effect_intent=CallSubjectHead(
            subject_id=intent_ref.subject_id,
            revision=Present(head=intent_ref.head, fingerprint=intent_ref.fingerprint),
        ),
        obligation_opening=opening_physical,
    )
    terminal_source = _source(
        terminal,
        record_id=terminal.terminal_id,
        owner="agent_loop",
        schema="chiplog.execution.accepted-open-terminal-disposition.v1",
        subject=_head(
            binding.original_call_id,
            "record:" + _sha(terminal.canonical_bytes()),
            _sha(terminal.canonical_bytes()),
        ),
    )

    decision = _head("selected:policy", "decision", "3" * 64)
    resolver_policy = original_resolver_authority_policy_v1(tenant)
    reducer_policy = original_effect_evidence_reducer_policy_v1(tenant)
    resolver_source = _policy_source(
        resolver_policy, "chiplog.loop.original-resolver-authority-policy.v1", decision
    )
    reducer_source = _policy_source(
        reducer_policy, "chiplog.loop.original-effect-evidence-reducer-policy.v1", decision
    )
    invocation = OriginalResolverInvocationV1(
        invocation_id="r14-resolver-invocation:" + tenant + ":" + binding.obligation_id,
        tenant_id=tenant,
        original=binding,
        accepted_opening_source=opening_source,
        accepted_terminal_source=terminal_source,
        resolver_policy_source=resolver_source,
        reducer_policy_source=reducer_source,
        trust_journal_head="trust-head",
        trust_response_fingerprint="4" * 64,
    )
    invocation_source = _source(
        invocation,
        record_id=invocation.invocation_id,
        owner="agent_loop",
        schema="chiplog.loop.original-resolver-invocation.v1",
    )
    witness_ref = _head(witness.record.subject_id, witness.record.head, witness.record.fingerprint)
    witness_source = RecoverySourceRecord(
        owner="effects",
        subject=witness_ref,
        schema_id=witness.schema_id,
        canonical_record_bytes=witness.canonical_bytes(),
        selected_decision=_head("selected:witness", "decision", "5" * 64),
        physical_record=witness_ref,
    )
    evidence_sources = []
    for index, record in enumerate(evidence_records):
        evidence = record.command.evidence
        assert evidence is not None
        carrier_ref = _head(record.record.subject_id, record.record.head, record.record.fingerprint)
        evidence_sources.append(
            RecoverySourceRecord(
                owner="effects",
                subject=CallSubjectHead(
                    subject_id=evidence_head(evidence).subject_id,
                    revision=Present(
                        head=evidence_head(evidence).head,
                        fingerprint=evidence_head(evidence).fingerprint,
                    ),
                ),
                schema_id=record.schema_id,
                canonical_record_bytes=record.canonical_bytes(),
                selected_decision=_head("selected:evidence:" + str(index), "decision", "6" * 64),
                physical_record=carrier_ref,
            )
        )
    return PrepareOriginalCallResolution(
        command_id="resolve-original",
        cut=OriginalResolverCut(
            tenant_id=tenant,
            database_id="database",
            tenant_commit_sequence=3,
            materialization_commitment="7" * 64,
            original=binding,
            obligation_stream=LoopRecoveryStream(
                stream_id=binding.obligation_stream_id,
                registry=_head("registry", "registry-head", "8" * 64),
                subject=_head(binding.obligation_stream_id, "stream-head", "9" * 64),
                schema_id="chiplog.loop.original-obligation-stream.v1",
            ),
            current_obligation=opening_source,
            call_terminal=terminal_source,
            selected_witness=witness_source,
            complete_ordered_evidence=tuple(evidence_sources),
            complete_selected_effects_reductions=(),
            expected_closure=Absent(),
            expected_recovered_outcome=Absent(),
            resolver_policy=resolver_source,
            reducer_policy=reducer_source,
            authority=IndependentResolverAuthority(
                registered_authority=resolver_source,
                authenticated_invocation=invocation_source,
            ),
        ),
    )
