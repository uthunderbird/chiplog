import hashlib
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.native_recovery_cut_validation import (
    _source_preimage_is_valid,
    _validate_frozen_binding_frontier_membership,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import FrozenRunBindings


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "subject",
        "carrier-id",
        "carrier-head",
        "carrier-fingerprint",
        "body",
        "marker",
        "schema",
        "owner",
    ],
)
@pytest.mark.parametrize("record_kind", ["evidence", "closed"])
def test_r16_outcome_record_marker_requires_exact_body_and_physical_carrier(damage, record_kind):
    from tests.capabilities.effects.support.dispatch_outcomes import original_send
    from tests.support.capabilities.effects.dispatch_outcomes import request

    from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome

    send = original_send()
    record = prepare_outcome(request(send, send))
    if record_kind == "closed":
        from tests.support.capabilities.agent_loop.original_call_resolution_preparation import (
            _closed_r16,
        )

        _, _, record = _closed_r16()
    if damage == "body":
        record = record.model_copy(
            update={
                "snapshot": record.snapshot.model_copy(
                    update={
                        "state": "CONFIRMED"
                        if record.snapshot.state != "CONFIRMED"
                        else "OUTCOME_UNKNOWN"
                    }
                )
            }
        )
    elif damage == "marker":
        record = record.model_copy(
            update={
                "record": record.record.model_copy(
                    update={
                        "fingerprint": "f" * 64,
                        "head": record.record.subject_id + "/" + "f" * 64,
                    }
                )
            }
        )
    raw = record.canonical_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    marker = CallSubjectHead(
        subject_id=record.record.subject_id,
        revision=Present(head=record.record.head, fingerprint=record.record.fingerprint),
    )
    source = RecoverySourceRecord(
        owner="effects",
        schema_id=record.schema_id,
        subject=marker,
        canonical_record_bytes=raw,
        physical_record=CallSubjectHead(
            subject_id=record.record.subject_id + "/" + record.record.fingerprint,
            revision=Present(head="record:" + digest, fingerprint=digest),
        ),
        selected_decision=_head_ref("decision", "d" * 64),
    )
    if damage == "subject":
        source = source.model_copy(
            update={"subject": _head_ref("foreign", marker.revision.fingerprint)}
        )
    elif damage is not None and damage.startswith("carrier-"):
        physical = source.physical_record
        if damage == "carrier-id":
            physical = physical.model_copy(update={"subject_id": "foreign"})
        else:
            changes = {"head": "foreign"} if damage == "carrier-head" else {"fingerprint": "f" * 64}
            revision = physical.revision.model_copy(update=changes)
            physical = physical.model_copy(update={"revision": revision})
        source = source.model_copy(update={"physical_record": physical})
    elif damage in {"schema", "owner"}:
        source = source.model_copy(
            update={"schema_id" if damage == "schema" else "owner": "foreign"}
        )
    assert _source_preimage_is_valid(source) is (damage is None)


def _head_ref(label: str, fingerprint: str) -> CallSubjectHead:
    return CallSubjectHead(subject_id=label, revision=Present(head=label, fingerprint=fingerprint))


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
                SimpleNamespace(family=family, ordered_heads=heads) for family, heads in families
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

    cut.current_bindings = cut.current_bindings.model_copy(update={"semantic_bindings": (policy,)})
    with pytest.raises(
        ValueError,
        match="native frozen binding is outside its registered family: semantic_bindings",
    ):
        _validate_frozen_binding_frontier_membership(cut)
