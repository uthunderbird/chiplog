"""Independent bundle preimages and exact closed-policy boundaries."""

import hashlib
import json

import pytest

from chiplog.capabilities.agent_loop.execution_contracts import SelfEffectArguments
from chiplog.composition.r14_call_dispatch_policy import bundle_references, policy_bytes
from chiplog.composition.r16_dispatch_registry import policy_bytes as legacy_policy_bytes


def test_every_original_label_is_bound_to_call_and_ordinal_without_normalization() -> None:
    labels = ("é", "e\u0301", "a/b", "a\x00b")
    arguments = SelfEffectArguments(payload=b"\x00\xff", bundle_members=labels)
    heads = bundle_references("original", arguments)
    assert len(heads) == len(labels)
    for ordinal, (label, head) in enumerate(zip(labels, heads, strict=True)):
        raw = json.dumps(
            {
                "schema_id": "chiplog.call.bundle-member.v1",
                "original_call_id": "original",
                "ordinal": ordinal,
                "label": label,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        assert head.subject_id == f"original/bundle/{ordinal}"
        assert head.head == f"original/bundle/{ordinal}/{digest}"
        assert head.fingerprint == digest
    assert set(heads).isdisjoint(bundle_references("other", arguments))
    reordered = arguments.model_copy(update={"bundle_members": tuple(reversed(labels))})
    assert all(a != b for a, b in zip(heads, bundle_references("original", reordered), strict=True))


@pytest.mark.parametrize("labels", [("a", "a"), tuple(str(i) for i in range(33)), ("é" * 2049,)])
def test_invalid_complete_bundle_rejects_without_dropping_members(labels: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        bundle_references("call", SelfEffectArguments(payload=b"x", bundle_members=labels))


def test_exact_limits_and_binary_payload() -> None:
    labels = ("é" * 2048, *(str(i) for i in range(31)))
    arguments = SelfEffectArguments(payload=b"\xff" * 65536, bundle_members=labels)
    assert len(bundle_references("call", arguments)) == 32
    with pytest.raises(ValueError):
        bundle_references("call", arguments.model_copy(update={"payload": b"x" * 65537}))
    with pytest.raises(ValueError):
        bundle_references("", arguments)


def test_new_call_policy_does_not_expand_legacy_plan_effect_operations() -> None:
    legacy = json.loads(legacy_policy_bytes())
    current = json.loads(policy_bytes())
    assert legacy["operations"] == ["PUBLISH_PLAN_EFFECT", "AUTHORIZE_SEND", "COMMIT_FIRST_SEND"]
    assert current["operations"] == [
        "ACCEPT_INITIALIZED_CALL",
        "AUTHORIZE_SEND",
        "COMMIT_FIRST_SEND",
    ]
    assert legacy["schema"] != current["schema"]
    assert current["real_provider_entitlement"] is False
