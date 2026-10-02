"""Native cut consistency only; the broker independently authenticates sources."""

from __future__ import annotations

import hashlib

from .execution_recovery_observations import ExecutionRecoveryCut
from .native_recovery_profile_contracts import (
    SCHEMA,
    decode_native_one_call_recovery_profile,
    native_recovery_fingerprint,
)
from .recovery_contracts import Present


def validate_native_recovery_cut(cut: ExecutionRecoveryCut) -> None:
    """Reject altered complete DTOs without turning their construction into trust."""
    sources = cut.complete_sources
    profiles = tuple(row for row in sources if row.schema_id == SCHEMA)
    if len(profiles) != 1:
        raise ValueError("missing or duplicate selected native profile source")
    profile = decode_native_one_call_recovery_profile(profiles[0].canonical_record_bytes)
    if cut.frontier.registry != profile.registry:
        raise ValueError("native cut registry differs from selected profile")
    families = tuple(row.family for row in profile.registry.ordered_rows)
    if tuple(row.family for row in cut.frontier.ordered_members) != families:
        raise ValueError("native cut family cardinality or ordering differs")
    for row in sources:
        if (
            not isinstance(row.subject.revision, Present)
            or row.subject.revision.fingerprint
            != hashlib.sha256(row.canonical_record_bytes).hexdigest()
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
