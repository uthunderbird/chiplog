"""Native cut consistency only; the broker independently authenticates sources."""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_outcomes import evidence_head

from .execution_recovery_observations import ExecutionRecoveryCut, RecoverySourceRecord
from .native_recovery_profile_contracts import (
    SCHEMA,
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_V4,
    SCHEMA_V5,
    decode_native_one_call_recovery_profile,
    decode_native_one_call_recovery_profile_v2,
    decode_native_one_call_recovery_profile_v3,
    decode_native_one_call_recovery_profile_v4,
    decode_native_one_call_recovery_profile_v5,
    native_recovery_fingerprint,
)
from .recovery_contracts import Present


def validate_native_recovery_cut(cut: ExecutionRecoveryCut) -> None:
    """Reject altered complete DTOs without turning their construction into trust."""
    sources = cut.complete_sources
    profile_schemas = (SCHEMA, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5)
    profiles = tuple(row for row in sources if row.schema_id in profile_schemas)
    if len(profiles) != 1:
        raise ValueError("missing or duplicate selected native profile source")
    profile = (
        decode_native_one_call_recovery_profile(profiles[0].canonical_record_bytes)
        if profiles[0].schema_id == SCHEMA
        else decode_native_one_call_recovery_profile_v3(profiles[0].canonical_record_bytes)
        if profiles[0].schema_id == SCHEMA_V3
        else decode_native_one_call_recovery_profile_v5(profiles[0].canonical_record_bytes)
        if profiles[0].schema_id == SCHEMA_V5
        else decode_native_one_call_recovery_profile_v4(profiles[0].canonical_record_bytes)
        if profiles[0].schema_id == SCHEMA_V4
        else decode_native_one_call_recovery_profile_v2(profiles[0].canonical_record_bytes)
    )
    if cut.frontier.registry != profile.registry:
        raise ValueError("native cut registry differs from selected profile")
    families = tuple(row.family for row in profile.registry.ordered_rows)
    if tuple(row.family for row in cut.frontier.ordered_members) != families:
        raise ValueError("native cut family cardinality or ordering differs")
    for row in sources:
        if (
            not _source_preimage_is_valid(row)
            or not isinstance(row.physical_record.revision, Present)
            or not isinstance(row.selected_decision.revision, Present)
        ):
            raise ValueError("native cut source preimage or selected/physical marker differs")
    identities = tuple((row.schema_id, row.subject.canonical_bytes()) for row in sources)
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate native source subject")
    for member in cut.frontier.ordered_members:
        if any(
            isinstance(head, Present) and not any(row.subject.revision == head for row in sources)
            for head in member.ordered_heads
        ):
            raise ValueError("native frontier member lacks authenticated source preimage")
        value = member.model_dump(mode="json")
        del value["fingerprint"]
        if member.fingerprint != native_recovery_fingerprint("member", value):
            raise ValueError("native member fingerprint differs")
    _validate_frozen_binding_frontier_membership(cut)
    value = cut.frontier.model_dump(mode="json")
    del value["fingerprint"]
    if cut.frontier.fingerprint != native_recovery_fingerprint("frontier", value):
        raise ValueError("native frontier fingerprint differs")
    for change in cut.complete_causal_changes:
        if (
            change.transition_registry != profiles[0].subject
            or change.transition_id not in profile.causal_transitions
            or change.source not in sources
            or change.successor != change.source.subject
        ):
            raise ValueError("unregistered native causal change or missing preimage")
    bindings = cut.current_bindings
    heads = (
        bindings.prompt_artifact,
        *bindings.ordered_tool_specs,
        bindings.generated_schema,
        *bindings.semantic_bindings,
        *bindings.recipient_effect_bindings,
        bindings.authority_scope,
        *bindings.authority_mandate_heads,
        bindings.policy,
        *bindings.no_retry_boundaries,
    )
    if any(not any(row.subject.revision == head for row in sources) for head in heads):
        raise ValueError("native frozen binding lacks authenticated source preimage")
    value = cut.model_dump(mode="json")
    del value["complete_inventory_fingerprint"]
    if cut.complete_inventory_fingerprint != native_recovery_fingerprint("cut", value):
        raise ValueError("native complete inventory fingerprint differs")


def _validate_frozen_binding_frontier_membership(cut: ExecutionRecoveryCut) -> None:
    """Require supported frozen references to occur in their registered family.

    Source authentication alone proves that bytes exist.  This cross-link also
    requires the selected recovery registry to classify each supported binding
    reference in the family that owns it, so a valid source from another family
    cannot be substituted into a frozen binding.
    """

    family_heads = {
        member.family: frozenset(member.ordered_heads)
        for member in cut.frontier.ordered_members
    }
    bindings = cut.current_bindings
    exact_families = (
        ("prompt_artifact", (bindings.prompt_artifact,), ("PROMPT",)),
        (
            "tool_schema",
            (*bindings.ordered_tool_specs, bindings.generated_schema),
            ("TOOL_SCHEMA",),
        ),
        ("semantic_bindings", bindings.semantic_bindings, ("SEMANTIC_BINDING",)),
        ("authority_scope", (bindings.authority_scope,), ("AUTHORITY",)),
        ("policy", (bindings.policy,), ("POLICY",)),
    )
    for name, references, families in exact_families:
        allowed = frozenset(
            head for family in families for head in family_heads.get(family, ())
        )
        if any(reference not in allowed for reference in references):
            raise ValueError(f"native frozen binding is outside its registered family: {name}")

    recipient_or_effect = family_heads.get("RECIPIENT", frozenset()) | family_heads.get(
        "EFFECT", frozenset()
    )
    if any(
        reference not in recipient_or_effect
        for reference in bindings.recipient_effect_bindings
    ):
        raise ValueError(
            "native frozen binding is outside RECIPIENT/EFFECT registered families"
        )

    mandate_heads = family_heads.get("MANDATE", frozenset())
    if any(reference not in mandate_heads for reference in bindings.authority_mandate_heads):
        raise ValueError("native frozen mandate is outside its registered family")


def _source_preimage_is_valid(source: RecoverySourceRecord) -> bool:
    """Validate bytes for physical rows and logical R16 evidence carrier rows."""
    if not isinstance(source.subject.revision, Present):
        return False
    digest = hashlib.sha256(source.canonical_record_bytes).hexdigest()
    if source.subject.revision.fingerprint == digest:
        return True
    if (
        source.owner != "effects"
        or source.schema_id != "chiplog.effects.dispatch-outcome-record.v2"
        or source.subject.subject_id == source.physical_record.subject_id
        or not isinstance(source.physical_record.revision, Present)
        or source.physical_record.revision.head != "record:" + digest
        or source.physical_record.revision.fingerprint != digest
    ):
        return False
    try:
        record = DispatchOutcomeRecordV2.model_validate_json(source.canonical_record_bytes)
    except (ValidationError, ValueError):
        return False
    evidence = record.command.evidence
    if (
        record.canonical_bytes() != source.canonical_record_bytes
        or source.physical_record.subject_id
        != record.record.subject_id + "/" + record.record.fingerprint
    ):
        return False
    if (
        source.subject.subject_id == record.record.subject_id
        and source.subject.revision.head == record.record.head
        and source.subject.revision.fingerprint == record.record.fingerprint
    ):
        if (record.kind, record.command.operation) not in {
            ("EVIDENCE_RECORDED", "APPEND_EVIDENCE"),
            ("RECONCILED", "RESOLVE_OBLIGATION"),
        }:
            return False
        from chiplog.capabilities.effects.dispatch_v2 import canonical, reference

        body = canonical(
            {
                "command": record.command.model_dump(mode="json"),
                "snapshot": record.snapshot.model_dump(mode="json"),
                "kind": record.kind,
                "predecessor": record.predecessor.model_dump(mode="json"),
            }
        )
        return record.record == reference(
            "effect-record/" + record.command.identity.command_id, body
        )
    if (
        record.kind != "EVIDENCE_RECORDED"
        or record.command.operation != "APPEND_EVIDENCE"
        or evidence is None
    ):
        return False
    reference = evidence_head(evidence)
    return source.subject.subject_id == reference.subject_id and source.subject.revision == Present(
        head=reference.head, fingerprint=reference.fingerprint
    )
