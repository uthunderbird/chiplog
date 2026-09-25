"""Focused contracts for the native legacy baseline inventory leaf."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection, RunRecord
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    DisclosureLabel,
    SourceReference,
)
from chiplog.composition.h1_inventory_baseline import (
    REGISTRATIONS,
    decode_h1_baseline_scope,
)
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
)


def _source() -> SourceReference:
    return SourceReference(
        tenant_id="tenant",
        owner="ingress",
        record_id="source-1",
        record_version="revision:content",
        content_digest="content",
        label_head="label",
        label=DisclosureLabel(
            lattice_version="chiplog.disclosure.v1",
            value="ENDPOINT_RESTRICTED",
            allowed_endpoints=("channel",),
        ),
    )


def _run() -> RunRecord:
    draft = RunRecord(
        tenant="tenant",
        principal="principal",
        run_id="run-1",
        state="CREATED",
        head="pending",
        predecessor=None,
        prompt="prompt",
        policy=BudgetPolicy(),
        origin=EndpointSelection(
            kind="ORIGIN_EXACT",
            ingress_binding_head="ingress-head",
            endpoint_head="endpoint-head",
            endpoint_id="channel",
            provider="hermetic-local",
            recipient="principal",
            canonical_address="local://principal",
            credential_binding_head="credential-head",
        ),
        contour_head="contour-head",
        policy_head="policy-head",
        worker_session="worker",
        event="RunCreated",
    )
    return draft.model_copy(update={"head": "loop:" + draft.digest()})


def _item(
    owner: str, schema: str, raw: bytes, record_id: str, *, tenant: str = "tenant"
) -> H1RawInventoryItem:
    return H1RawInventoryItem(
        "PHYSICAL",
        "records/" + record_id,
        tenant,
        owner,
        schema,
        None,
        record_id,
        hashlib.sha256(raw).hexdigest(),
        raw,
    )


def _conversation() -> tuple[ConversationEntry, bytes]:
    entry = ConversationEntry(
        tenant_id="tenant",
        conversation_id="conversation:tenant",
        entry_id="entry-1",
        sequence=1,
        origin_channel_id="channel",
        visible_channels=("channel",),
        role="principal",
        accepted_bytes=b"accepted",
        envelope=DisclosureEnvelope(
            tenant_id="tenant",
            content_digest=hashlib.sha256(b"accepted").hexdigest(),
            sources=(_source(),),
            label=_source().label,
            policy_head="policy-head",
            contour_head="contour-head",
            deletion_fence_head="fence-head",
        ),
    )
    entry_json = entry.model_dump_json()
    return entry, json.dumps(
        {"entry_json": entry_json, "fingerprint": hashlib.sha256(entry_json.encode()).hexdigest()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _policy() -> tuple[str, bytes]:
    payload = json.dumps(
        {
            "tenant": "tenant",
            "principal": "principal",
            "channel": "channel",
            "database": "database",
            "endpoint": "endpoint",
            "heads": {
                "policy": "policy-head",
                "credential": "credential-head",
                "session": "session-head",
                "contour": "contour-head",
                "deletion": "fence-head",
            },
            "sources": [_source().model_dump(mode="json")],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    identity = hashlib.sha256(json.dumps(["tenant", "principal", "channel"]).encode()).hexdigest()
    return "workspace-policy:" + identity + ":" + hashlib.sha256(payload).hexdigest(), payload


def test_run_decodes_exact_physical_identity_and_typed_record_edges() -> None:
    run = _run()
    raw = run.canonical_bytes()

    decoded = decode_h1_baseline_scope(
        _item("agent_loop", "chiplog.agent-loop.record.v1", raw, run.head)
    )

    assert {(key.namespace, key.identity, key.head) for key in decoded.identities} >= {
        ("record", run.head, hashlib.sha256(raw).hexdigest()),
        ("run", run.run_id, run.head),
    }
    assert {
        (edge.relation, edge.subject.namespace, edge.target.namespace) for edge in decoded.relations
    } >= {
        ("RECORD_RUN", "record", "run"),
    }
    assert decoded.families == ("RECORD", "RUN")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: replace(item, record_id="other-head"),
        lambda item: replace(item, fingerprint="0" * 64),
        lambda item: replace(item, tenant="other-tenant"),
        lambda item: replace(item, raw=item.raw + b" "),
    ],
)
def test_run_rejects_nonphysical_tenant_head_fingerprint_or_canonical_mismatch(
    mutate: object,
) -> None:
    run = _run()
    item = _item("agent_loop", "chiplog.agent-loop.record.v1", run.canonical_bytes(), run.head)

    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        decode_h1_baseline_scope(mutate(item))  # type: ignore[operator]


def test_run_rejects_a_content_addressed_but_invalid_genesis_transition() -> None:
    run = _run().model_copy(update={"event": "RunActive", "head": "pending"})
    invalid = run.model_copy(update={"head": "loop:" + run.digest()})

    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        decode_h1_baseline_scope(
            _item(
                "agent_loop",
                "chiplog.agent-loop.record.v1",
                invalid.canonical_bytes(),
                invalid.head,
            )
        )


def test_conversation_preserves_entry_sources_and_rejects_wrapper_tampering() -> None:
    entry, raw = _conversation()
    item = _item("conversation", "chiplog.conversation.v1", raw, entry.entry_id)

    decoded = decode_h1_baseline_scope(item)

    assert decoded.source_refs == entry.envelope.sources
    assert {
        (edge.relation, edge.subject.namespace, edge.target.namespace) for edge in decoded.relations
    } == {
        ("RECORD_SOURCE", "record", "source"),
    }
    tampered = json.loads(raw)
    tampered["fingerprint"] = "0" * 64
    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        decode_h1_baseline_scope(
            replace(item, raw=json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode())
        )


def test_workspace_policy_checks_derived_physical_id_and_preserves_sources() -> None:
    record_id, raw = _policy()

    decoded = decode_h1_baseline_scope(
        _item("workspace_policy", "chiplog.workspace.policy.v1", raw, record_id)
    )

    assert decoded.source_refs == (_source(),)
    assert {
        (edge.relation, edge.subject.namespace, edge.target.namespace) for edge in decoded.relations
    } == {
        ("RECORD_SOURCE", "record", "source"),
    }
    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        decode_h1_baseline_scope(
            _item("workspace_policy", "chiplog.workspace.policy.v1", raw, "other-policy")
        )


def test_unknown_pair_and_kind_are_unsupported() -> None:
    run = _run()
    item = _item("agent_loop", "chiplog.agent-loop.record.v1", run.canonical_bytes(), run.head)
    with pytest.raises(H1OwnerInventoryFailure, match="UNSUPPORTED"):
        decode_h1_baseline_scope(replace(item, record_kind="future.kind"))
    with pytest.raises(H1OwnerInventoryFailure, match="UNSUPPORTED"):
        decode_h1_baseline_scope(replace(item, schema="chiplog.agent-loop.future.v1"))


def test_registrations_are_exact_physical_baseline_pairs() -> None:
    assert {(row.surface, row.owner, row.schema, row.record_kind) for row in REGISTRATIONS} == {
        ("PHYSICAL", "agent_loop", "chiplog.agent-loop.record.v1", None),
        ("PHYSICAL", "conversation", "chiplog.conversation.v1", None),
        ("PHYSICAL", "workspace_policy", "chiplog.workspace.policy.v1", None),
    }
