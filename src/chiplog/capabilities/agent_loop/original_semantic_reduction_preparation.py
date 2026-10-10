"""Pure proposal for the current semantic reduction of one resolved obligation."""

from __future__ import annotations

import base64
import hashlib
import json

from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallPreparationRejected
from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import (
    translate_selected_outcome_v1,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    ConsumableReduction,
    HeldReduction,
    IndependentResolverAuthority,
    LoopSemanticReductionRecord,
    OriginalEffectEvidenceReducerPolicyV1,
    OriginalObligationClosureRecord,
    OriginalResolutionBasis,
    OriginalResolverAuthorityPolicyV1,
    OriginalResolverBatchRecord,
    OriginalResolverInvocationV1,
    PreparedLoopSemanticReduction,
    PrepareLoopSemanticReduction,
    RecoveredCallOutcomeRecord,
    ResolvedReductionAnchor,
    SelectedLoopSemanticReduction,
    original_effect_evidence_reducer_policy_v1,
    original_resolver_authority_policy_v1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_outcomes import evidence_head

_POLICY_SCHEMA = "chiplog.loop.original-effect-evidence-reducer-policy.v1"
_RESOLVER_POLICY_SCHEMA = "chiplog.loop.original-resolver-authority-policy.v1"
_INVOCATION_SCHEMA = "chiplog.loop.original-resolver-invocation.v1"
_BASIS_SCHEMA = "chiplog.loop.original-resolution-basis.v1"
_CLOSURE_SCHEMA = "chiplog.loop.original-obligation-closure.v1"
_OUTCOME_SCHEMA = "chiplog.loop.recovered-call-outcome.v1"
_BATCH_SCHEMA = "chiplog.loop.original-resolver-batch.v1"
_EVIDENCE_SCHEMA = "chiplog.effects.dispatch-outcome-record.v2"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode(source: RecoverySourceRecord, type_: type, owner: str, schema: str):
    if source.owner != owner or source.schema_id != schema:
        raise ValueError(
            "semantic reduction source is not an exact selected physical record: "
            + source.owner
            + "/"
            + source.schema_id
        )
    if source.physical_record.revision.kind != "PRESENT":
        raise ValueError("semantic reduction source physical revision is not selected")
    if source.selected_decision.revision.kind != "PRESENT":
        raise ValueError("semantic reduction source decision is not selected")
    try:
        value = TypeAdapter(type_).validate_json(source.canonical_record_bytes)
    except (TypeError, ValueError, ValidationError) as error:
        raise ValueError("semantic reduction source has an invalid registered shape") from error
    if value.canonical_bytes() != source.canonical_record_bytes:
        raise ValueError("semantic reduction source is noncanonical")
    if isinstance(value, DispatchOutcomeRecordV2):
        if (
            source.physical_record.subject_id != value.record.subject_id
            or source.physical_record.revision.head != value.record.head
            or source.physical_record.revision.fingerprint != value.record.fingerprint
        ):
            raise ValueError("semantic reduction dispatch carrier physical reference differs")
    elif source.physical_record.revision.fingerprint != _sha(source.canonical_record_bytes):
        raise ValueError("semantic reduction physical fingerprint differs from its bytes")
    return value


def prepare_loop_semantic_reduction(
    request: PrepareLoopSemanticReduction,
) -> PreparedLoopSemanticReduction | CallPreparationRejected:
    """Build a current-head proposal; broker writer admission remains authoritative."""
    try:
        request = PrepareLoopSemanticReduction.model_validate_json(request.canonical_bytes())
        original = request.original
        if (
            request.tenant_commit_sequence < 1
            or request.stream.owner != "agent_loop"
            or request.stream.schema_id != "chiplog.loop.semantic-reduction.v1"
            or request.stream.stream_id != original.evidence_stream_id
            or request.stream.subject.subject_id != original.obligation_id
            or not request.complete_ordered_evidence
            or not isinstance(request.authority, IndependentResolverAuthority)
        ):
            raise ValueError(
                "semantic reduction cut is incomplete or outside the registered stream"
            )
        policy = _decode(
            request.reducer_policy,
            OriginalEffectEvidenceReducerPolicyV1,
            "agent_loop",
            _POLICY_SCHEMA,
        )
        if (
            policy != original_effect_evidence_reducer_policy_v1(request.tenant_id)
            or original.reducer_id != policy.reducer_id
            or original.reducer_version != policy.reducer_version
        ):
            raise ValueError("semantic reduction policy differs from the obligation")
        authority_policy = _decode(
            request.authority.registered_authority,
            OriginalResolverAuthorityPolicyV1,
            "agent_loop",
            _RESOLVER_POLICY_SCHEMA,
        )
        invocation = _decode(
            request.authority.authenticated_invocation,
            OriginalResolverInvocationV1,
            "agent_loop",
            _INVOCATION_SCHEMA,
        )
        if (
            authority_policy != original_resolver_authority_policy_v1(request.tenant_id)
            or original.resolver_id != authority_policy.resolver_id
            or original.resolver_version != authority_policy.resolver_version
            or invocation.tenant_id != request.tenant_id
            or invocation.original != original
            or invocation.principal_id != authority_policy.principal_id
            or not invocation.trust_journal_head
            or not invocation.trust_response_fingerprint
            or request.stream.subject != invocation.accepted_opening_source.subject
            or invocation.resolver_policy_source != request.authority.registered_authority
            or invocation.reducer_policy_source != request.reducer_policy
        ):
            raise ValueError("semantic reduction resolver authority differs from the source cut")

        anchors: dict[str, object] = {}
        anchor_sources: dict[str, RecoverySourceRecord] = {}
        schemas = {
            _BASIS_SCHEMA: (OriginalResolutionBasis, "basis"),
            _CLOSURE_SCHEMA: (OriginalObligationClosureRecord, "closure"),
            _OUTCOME_SCHEMA: (RecoveredCallOutcomeRecord, "outcome"),
            _BATCH_SCHEMA: (OriginalResolverBatchRecord, "batch"),
            _EVIDENCE_SCHEMA: (DispatchOutcomeRecordV2, "witness"),
        }
        if len(request.complete_anchor_sources) != len(schemas):
            raise ValueError("semantic reduction anchor inventory is incomplete")
        for source in request.complete_anchor_sources:
            item = schemas.get(source.schema_id)
            if item is None or item[1] in anchors:
                raise ValueError("semantic reduction anchor inventory is duplicate or unknown")
            source_owner = "effects" if item[1] == "witness" else "agent_loop"
            anchors[item[1]] = _decode(source, item[0], source_owner, source.schema_id)
            anchor_sources[item[1]] = source
        basis = anchors["basis"]
        closure = anchors["closure"]
        outcome = anchors["outcome"]
        batch = anchors["batch"]
        witness = anchors["witness"]
        assert isinstance(basis, OriginalResolutionBasis)
        assert isinstance(closure, OriginalObligationClosureRecord)
        assert isinstance(outcome, RecoveredCallOutcomeRecord)
        assert isinstance(batch, OriginalResolverBatchRecord)
        assert isinstance(witness, DispatchOutcomeRecordV2)
        if (
            basis.original != original
            or basis.original_terminal != invocation.accepted_terminal_source.physical_record
            or closure.original != original
            or outcome.original != original
            or closure.basis.revision.fingerprint != _sha(basis.canonical_bytes())
            or outcome.basis.revision.fingerprint != _sha(basis.canonical_bytes())
            or outcome.closure.revision.fingerprint != _sha(closure.canonical_bytes())
            or batch.basis.revision.fingerprint != _sha(basis.canonical_bytes())
            or batch.closure.revision.fingerprint != _sha(closure.canonical_bytes())
            or batch.recovered_outcome.revision.fingerprint != _sha(outcome.canonical_bytes())
        ):
            raise ValueError("semantic reduction anchors do not form the selected resolver chain")

        evidence_refs = []
        for source, expected_head in zip(
            request.complete_ordered_evidence, witness.snapshot.evidence_heads, strict=True
        ):
            evidence = _decode(source, DispatchOutcomeRecordV2, "effects", _EVIDENCE_SCHEMA)
            command_evidence = evidence.command.evidence
            if (
                evidence.command.operation != "APPEND_EVIDENCE"
                or command_evidence is None
                or command_evidence != witness.snapshot.evidence[len(evidence_refs)]
                or source.subject.subject_id != expected_head.subject_id
                or source.subject.revision.head != expected_head.head
                or source.subject.revision.fingerprint != expected_head.fingerprint
                or evidence_head(command_evidence) != expected_head
                or evidence.snapshot.intent != witness.snapshot.intent
            ):
                raise ValueError("semantic reduction evidence belongs to another stream")
            evidence_refs.append(source.physical_record)
        if tuple(witness.snapshot.evidence_heads) != tuple(
            evidence_head(evidence.command.evidence)
            for evidence in (
                _decode(source, DispatchOutcomeRecordV2, "effects", _EVIDENCE_SCHEMA)
                for source in request.complete_ordered_evidence
            )
        ):
            raise ValueError("semantic reduction evidence inventory is incomplete")
        if (
            len(evidence_refs) < len(basis.complete_evidence_manifest)
            or tuple(evidence_refs[: len(basis.complete_evidence_manifest)])
            != basis.complete_evidence_manifest
        ):
            raise ValueError("semantic reduction no longer extends its accepted evidence manifest")
        hold_reason = None
        try:
            status = translate_selected_outcome_v1(witness)
        except ValueError:
            hold_reason = "CONTRADICTORY"
        else:
            if status.semantic_class != basis.accepted_semantic_class:
                hold_reason = "MEANING_CHANGED"

        predecessor = Absent()
        reduction_id = basis.reduction_id
        if isinstance(request.expected, SelectedLoopSemanticReduction):
            prior = request.expected.record
            decoded_prior = _decode(
                request.expected.source,
                LoopSemanticReductionRecord,
                "agent_loop",
                "chiplog.loop.semantic-reduction.v1",
            )
            if prior != decoded_prior or prior.stream != request.stream:
                raise ValueError("selected semantic reduction predecessor differs")
            if (
                not isinstance(prior.disposition, ConsumableReduction)
                or prior.disposition.semantic_class != basis.accepted_semantic_class
            ):
                raise ValueError("a held or changed semantic reduction cannot be refined")
            predecessor = Present(
                head=request.expected.source.physical_record.revision.head,
                fingerprint=request.expected.source.physical_record.revision.fingerprint,
            )
            if prior.reduction_id != reduction_id:
                raise ValueError("semantic reduction identity changed")

        record = LoopSemanticReductionRecord(
            stream=request.stream,
            reduction_id=reduction_id,
            reducer_id=policy.reducer_id,
            reducer_version=policy.reducer_version,
            predecessor=predecessor,
            complete_ordered_evidence=tuple(evidence_refs),
            anchor=ResolvedReductionAnchor(
                original=original,
                accepted_witness=basis.selected_witness,
                recovered_outcome=next(
                    source.physical_record
                    for source in anchor_sources.values()
                    if source.schema_id == _OUTCOME_SCHEMA
                ),
                closure=next(
                    source.physical_record
                    for source in anchor_sources.values()
                    if source.schema_id == _CLOSURE_SCHEMA
                ),
                resolver_batch=next(
                    source.physical_record
                    for source in anchor_sources.values()
                    if source.schema_id == _BATCH_SCHEMA
                ),
                accepted_semantic_class=basis.accepted_semantic_class,
            ),
            disposition=(
                HeldReduction(
                    reason=hold_reason,
                    conflicting_or_unclassified_evidence=tuple(evidence_refs),
                )
                if hold_reason is not None
                else ConsumableReduction(semantic_class=basis.accepted_semantic_class)
            ),
        )
        raw = record.canonical_bytes()
        record_fingerprint = _sha(raw)
        record_id = "chiplog.loop.semantic-reduction.v1:" + record_fingerprint
        manifest = [
            {
                "owner": "agent_loop",
                "record_kind": "LOOP_SEMANTIC_REDUCTION",
                "record_id": record_id,
                "schema_id": "chiplog.loop.semantic-reduction.v1",
                "canonical_bytes": base64.urlsafe_b64encode(raw).decode("ascii"),
                "fingerprint": record_fingerprint,
            }
        ]
        fingerprint = _sha(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        )
        result = PreparedLoopSemanticReduction(
            source_request_fingerprint=request.digest(),
            record=record,
            complete_batch_fingerprint=fingerprint,
        )
        return result
    except (
        AssertionError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        return CallPreparationRejected(
            command_id=request.command_id, code="HOLD", reason=str(error)
        )
