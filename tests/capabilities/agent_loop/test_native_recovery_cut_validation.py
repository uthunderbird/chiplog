from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.native_recovery_cut_validation import (
    _validate_frozen_binding_frontier_membership,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import FrozenRunBindings


def _head(label: str) -> Present:
    return Present(head=f"head:{label}", fingerprint=(label.encode().hex() * 64)[:64])


def test_frozen_binding_references_must_belong_to_their_registered_frontier_family():
    prompt = _head("p")
    tool = _head("t")
    schema = _head("s")
    semantic = _head("m")
    recipient = _head("r")
    authority = _head("a")
    policy = _head("y")
    families = (
        ("PROMPT", (prompt,)),
        ("TOOL_SCHEMA", (tool, schema)),
        ("SEMANTIC_BINDING", (semantic,)),
        ("RECIPIENT", (recipient,)),
        ("EFFECT", ()),
        ("AUTHORITY", (authority,)),
        ("POLICY", (policy,)),
        ("MANDATE", ()),
    )
    cut = SimpleNamespace(
        frontier=SimpleNamespace(
            ordered_members=tuple(
                SimpleNamespace(family=family, ordered_heads=heads)
                for family, heads in families
            )
        ),
        current_bindings=FrozenRunBindings(
            objective="objective",
            requested_work="work",
            prompt_artifact=prompt,
            ordered_tool_specs=(tool,),
            generated_schema=schema,
            semantic_bindings=(semantic,),
            recipient_effect_bindings=(recipient,),
            authority_scope=authority,
            authority_mandate_heads=(),
            policy=policy,
            no_retry_boundaries=(),
        ),
    )

    _validate_frozen_binding_frontier_membership(cut)

    cut.current_bindings = cut.current_bindings.model_copy(
        update={"semantic_bindings": (policy,)}
    )
    with pytest.raises(
        ValueError,
        match="native frozen binding is outside its registered family: semantic_bindings",
    ):
        _validate_frozen_binding_frontier_membership(cut)
