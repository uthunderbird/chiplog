"""Shared builders extracted without changing their behavior."""

from tests.support.effects import head, intent

from chiplog.capabilities.effects.contracts import (
    CommandIdentity,
    EffectRecord,
    EffectSnapshot,
)
from chiplog.composition.r16_effects_registry import (
    HermeticPlanEffectRegistry,
)


def record(state: str, name: str = "old") -> EffectRecord:
    registry = HermeticPlanEffectRegistry()
    original = intent()
    authority = original.authority.model_copy(
        update={
            "tenant_id": "hermetic-tenant",
            "principal_id": "hermetic-principal",
            "actor_id": "hermetic-principal",
            "recipient": registry.recipient,
        }
    )
    snapshot = EffectSnapshot.model_validate(
        {
            "intent": original.model_copy(
                update={"authority": authority, "semantics": registry.semantics}
            ),
            "attempt": head(name),
            "state": state,
            "authorizations": (),
            "transmissions": (),
            "evidence": (),
            "unresolved_obligations": (),
        }
    )
    return EffectRecord(
        record=head(name + "record"),
        command=CommandIdentity(command_id=name, fingerprint=name, expected_tenant_head=0),
        predecessor=None,
        kind="PLAN_EFFECT_PUBLISHED",
        snapshot=snapshot,
        source_command=b"owner bytes",
    )
