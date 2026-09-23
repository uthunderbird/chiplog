"""Real owner preparations and independent cross-owner/physical mutations."""

import base64
import hashlib
import json

import pytest
from pydantic import BaseModel

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
    call_record_reference,
    prepare_consequential_acceptance,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.capabilities.effects import contracts as effects
from chiplog.capabilities.effects import fences
from chiplog.capabilities.effects.application import prepare_transition
from chiplog.composition import r14_acceptance_records as verifier
from chiplog.composition.r14_acceptance_contracts import (
    RetainedAcceptancePreparation,
    SemanticComponent,
    effects_dispatch_semantics,
    loop_dispatch_semantics,
)


@pytest.mark.parametrize("delimiter", ["\n", "\r"])
def test_real_owner_physical_identity_rejects_membership_delimiters(delimiter: str) -> None:
    retained = _prepared()
    command = retained.effects_command.model_copy(
        update={
            "identity": retained.effects_command.identity.model_copy(
                update={"command_id": "accept" + delimiter + "effects"}
            )
        }
    )
    retained = retained.model_copy(
        update={
            "effects_command": command,
            "effects_proposal": _prepare_effect(command, retained.effects_proposal.expected_store),
        }
    )
    with pytest.raises(ValueError, match="membership delimiter"):
        verifier.build_acceptance_envelope(retained)


def _head(name: str) -> effects.ExactHead:
    return effects.ExactHead(subject_id=name, head=name + "/head", fingerprint="a" * 64)


def _loop_ref(head: effects.ExactHead) -> call.CallSubjectHead:
    return call.CallSubjectHead(
        subject_id=head.subject_id,
        revision=Present(head=head.head, fingerprint=head.fingerprint),
    )


def _effect_ref(head: call.CallSubjectHead) -> effects.ExactHead:
    return effects.ExactHead(
        subject_id=head.subject_id, head=head.revision.head, fingerprint=head.revision.fingerprint
    )


def _fingerprint(model: BaseModel) -> str:
    body = model.model_dump(mode="json")
    del body["fingerprint"]
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _prepared(payload: bytes = b"hello") -> RetainedAcceptancePreparation:
    head = _head("authority")
    recipient = effects.ProviderRecipient(
        provider="hermetic",
        account="self",
        recipient="principal",
        endpoint=head,
        canonical_address=b"hermetic://self",
        credential_binding=head,
    )
    authority = effects.AuthorityBinding(
        tenant_id="tenant",
        principal_id="principal",
        actor_id="principal",
        authenticated_session=head,
        act=effects.DirectAuthorityAct(kind="DIRECT_AUTHENTICATED_ACT", act=head, ingress=head),
        planning_revision=head,
        authorization_evidence=head,
        authority_sources=(head,),
        affected_party_constraints=(),
        hold_conflict_order=head,
        dependencies=(),
        factual_assertion_evidence=(),
        verification_contradiction=(),
        authority_applicability=(),
        consequence_scope=head,
        communication_mandate=head,
        disclosure_projection=head,
        channel_class="hermetic",
        interaction_context=head,
        recipient=recipient,
        reads=(
            effects.AuthorityRead(
                source_id="actor",
                source_version="1",
                head=head,
                generation="generation",
                frontier="0",
                valid_until_ns=100,
                canonical_value=b"actor",
            ),
        ),
        registry_inputs=(("offline", "1"),),
        valid_until_ns=100,
    )
    intent = effects.ExternalActionIntent(
        intent_id="intent",
        fingerprint="pending",
        authority=authority,
        semantics=effects_dispatch_semantics(),
        payload=payload,
        effect_fingerprint=hashlib.sha256(payload).hexdigest(),
        idempotency_fence_key="intent-key",
        inseparable_bundle_members=(head,),
        purpose=effects.OrdinaryPurpose(kind="ORDINARY_EFFECT"),
    )
    intent = intent.model_copy(update={"fingerprint": _fingerprint(intent)})
    original = call.OriginalCallKey(
        tenant_id="tenant",
        original_run_id="original-run",
        original_turn_id="turn",
        captured_response=_loop_ref(_head("captured")),
        ordinal=0,
        model_call_label="tool",
    )
    initialized = call.InitializedCallRecord(
        original_call_id="call:" + original.digest(),
        predecessor=Absent(),
        call=call.SealedCallInput(
            original=original,
            classification="CONSEQUENTIAL",
            tool_schema=_loop_ref(_head("tool-schema")),
            tool_policy=_loop_ref(_head("tool-policy")),
            canonical_call_base64=base64.b64encode(b'{"tool":"self"}').decode(),
            retry_lineage=NotApplicable(),
        ),
    )
    initialized_ref = call_record_reference(initialized.original_call_id, initialized)
    inventory = call.CallInventorySnapshot(
        tenant_id="tenant",
        tenant_commit_sequence=0,
        ordered_calls=(
            call.CallLifecycleObservation(
                original_call_id=initialized.original_call_id,
                initialized=initialized_ref,
                initialized_record=initialized,
                acceptance=InitializedCall(initialized=initialized_ref.revision),
                terminal=Absent(),
            ),
        ),
    )
    current_run = _loop_ref(_head("execution-run"))
    fence = NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id=current_run.subject_id,
        run_head=current_run.revision.head,
        worker_session_id="worker",
        runtime_generation="gen",
    )
    cut = call.CallPreparationCut(
        tenant_id="tenant",
        current_run=current_run,
        run_state="ACTIVE",
        tenant_commit_sequence=0,
        materialization_commitment="a" * 64,
        predecessor_inventory=inventory,
        complete_call_inventory=call_record_reference("call-inventory:tenant", inventory),
        authority_registry=_loop_ref(_head("registry")),
        sources=(
            call.CallAuthorityObservation(
                source_id="actor",
                family="ACTOR",
                source=_loop_ref(head),
                generation="gen",
                frontier="0",
                canonical_value_base64=base64.b64encode(b"actor").decode(),
                observed_at_ns=1,
                valid_until_ns=100,
            ),
        ),
        fence=fence,
    )
    request = call.AcceptConsequentialCallRequest(
        command_id="accept-loop",
        initialized_record=initialized,
        binding=call.ConsequentialAcceptanceBinding(
            original_call_id=initialized.original_call_id,
            original=original,
            initialized=initialized_ref,
            tool_schema=initialized.call.tool_schema,
            tool_policy=initialized.call.tool_policy,
            external_intent=_loop_ref(
                effects.ExactHead(
                    subject_id=intent.intent_id,
                    head=intent.intent_id + "/" + intent.fingerprint,
                    fingerprint=intent.fingerprint,
                )
            ),
            dispatch_semantics=loop_dispatch_semantics(),
            cut=cut,
        ),
    )
    loop = prepare_consequential_acceptance(request)
    assert isinstance(loop, call.PreparedConsequentialAcceptance)
    manifest = tuple(_effect_ref(ref) for ref in loop.complete_acceptance_manifest)
    command = effects.AcceptEffectCommand(
        identity=effects.CommandIdentity(
            command_id="accept-effects", fingerprint="command-fingerprint", expected_tenant_head=0
        ),
        initialized_call=_effect_ref(initialized_ref),
        call_id=initialized.original_call_id,
        tool_schema_policy=(
            _effect_ref(request.binding.tool_schema),
            _effect_ref(request.binding.tool_policy),
        ),
        fence=fences.NonSchedulerFence.model_validate_json(fence.canonical_bytes()),
        intent=intent,
        execution_intent=manifest[1],
        complete_acceptance_manifest=manifest,
    )
    store = effects.EffectStoreSnapshot(tenant_id="tenant", tenant_head=0, records=())
    return RetainedAcceptancePreparation(
        loop_request=request,
        loop_proposal=loop,
        effects_command=command,
        effects_proposal=_prepare_effect(command, store),
    )


def _prepare_effect(
    command: effects.AcceptEffectCommand, store: effects.EffectStoreSnapshot
) -> effects.PreparedEffectPublication:
    assert isinstance(command.fence, fences.NonSchedulerFence)
    current = effects.CurrentEffectInputs(
        command_id=command.identity.command_id,
        command_fingerprint=command.identity.fingerprint,
        store_frontier=store.tenant_head,
        observed_time_ns=1,
        authority=command.intent.authority,
        supported_semantics=command.intent.semantics,
        fence=command.fence,
        authority_decision=_head("authority-decision"),
        blocking_effect_heads=(),
        current_original_ambiguity_heads=(),
        initialized_call=command.initialized_call,
        active_run_head=command.fence.run_head,
        independently_verified_safe_proof=None,
        authenticated_evidence=None,
        original_reducer_semantics=None,
        authorized_reconciler=None,
    )
    return prepare_transition(command, store, current)


def test_real_owner_preparations_bind_exact_three_record_bytes_and_replay() -> None:
    retained = _prepared(bytes(range(256)))
    envelope = verifier.build_acceptance_envelope(retained)
    assert RetainedAcceptancePreparation.model_validate_json(retained.canonical_bytes()) == retained
    assert (
        verifier.build_acceptance_envelope(retained).canonical_bytes() == envelope.canonical_bytes()
    )
    verifier.verify_acceptance_envelope(retained, envelope)
    assert [row.owner for row in envelope.complete_records] == [
        "agent_loop",
        "agent_loop",
        "effects",
    ]
    for row, expected in zip(
        envelope.complete_records,
        (
            retained.loop_proposal.accepted.canonical_bytes(),
            retained.loop_proposal.execution_intent.canonical_bytes(),
            retained.effects_proposal.record.canonical_bytes(),
        ),
        strict=True,
    ):
        assert base64.b64decode(row.canonical_payload_base64) == expected
        assert row.fingerprint == hashlib.sha256(expected).hexdigest()
    assert (
        envelope.complete_records[2].fingerprint
        != retained.effects_proposal.record.record.fingerprint
    )
    assert (
        envelope.complete_records[2].record_id
        != retained.loop_request.binding.external_intent.subject_id
    )
    assert envelope.complete_records[2].record_id == retained.effects_proposal.record.record.head
    assert retained.effects_proposal.record.snapshot.transmissions == ()


@pytest.mark.parametrize(
    "path,value",
    (
        (("loop_request", "binding", "original_call_id"), "other"),
        (("loop_request", "binding", "cut", "tenant_id"), "foreign"),
        (("loop_request", "binding", "cut", "current_run", "subject_id"), "other"),
        (("loop_request", "binding", "cut", "predecessor_inventory", "ordered_calls"), []),
        (("loop_request", "binding", "dispatch_semantics", "reducer", "subject_id"), "caller-map"),
        (("loop_proposal", "source_request_fingerprint"), "b" * 64),
        (("loop_proposal", "accepted", "execution_intent_id"), "other"),
        (("loop_proposal", "execution_intent", "accepted", "revision", "head"), "other"),
        (("effects_command", "call_id"), "other"),
        (("effects_command", "initialized_call", "head"), "other"),
        (("effects_command", "execution_intent", "head"), "other"),
        (("effects_command", "fence", "worker_session_id"), "other"),
        (("effects_command", "intent", "payload"), "Y2hhbmdlZA=="),
        (("effects_command", "intent", "semantics", "reducer_version"), "self-registered"),
        (("effects_proposal", "expected_store", "tenant_id"), "foreign"),
        (("effects_proposal", "expected_store", "tenant_head"), 1),
        (("effects_proposal", "record", "kind"), "PLAN_EFFECT_PUBLISHED"),
        (("effects_proposal", "record", "snapshot", "state"), "SEND_COMMITTED"),
        (("effects_proposal", "record", "snapshot", "attempt", "head"), "other"),
        (("effects_proposal", "record", "record", "fingerprint"), "b" * 64),
        (("effects_proposal", "record", "source_command"), "Y2hhbmdlZA=="),
    ),
)
def test_reference_and_owner_byte_substitutions_reject(
    path: tuple[str, ...], value: object
) -> None:
    body = json.loads(_prepared().canonical_bytes())
    target = body
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] = value
    changed = RetainedAcceptancePreparation.model_validate_json(json.dumps(body))
    with pytest.raises(ValueError):
        verifier.build_acceptance_envelope(changed)


@pytest.mark.parametrize(
    "mutation",
    ("omit", "add", "duplicate", "reorder", "owner", "schema", "id", "payload", "fingerprint"),
)
def test_physical_membership_mutations_reject(mutation: str) -> None:
    retained = _prepared()
    envelope = verifier.build_acceptance_envelope(retained)
    members = envelope.complete_records
    if mutation == "omit":
        members = members[:-1]
    elif mutation == "add":
        members += (members[0],)
    elif mutation == "duplicate":
        members = (members[0], members[0], members[2])
    elif mutation == "reorder":
        members = tuple(reversed(members))
    else:
        field, value = {
            "owner": ("owner", "effects"),
            "schema": ("schema_id", "other"),
            "id": ("record_id", "other"),
            "payload": ("canonical_payload_base64", "eA=="),
            "fingerprint": ("fingerprint", "b" * 64),
        }[mutation]
        members = (members[0].model_copy(update={field: value}), *members[1:])
    with pytest.raises(ValueError):
        verifier.verify_acceptance_envelope(
            retained, envelope.model_copy(update={"complete_records": members})
        )


@pytest.mark.parametrize("side", ("loop", "effects", "companions"))
@pytest.mark.parametrize("mutation", ("omit", "add", "duplicate", "reorder"))
def test_semantic_manifests_reject_membership_changes(side: str, mutation: str) -> None:
    retained = _prepared()
    body = json.loads(retained.canonical_bytes())
    parent, key = {
        "loop": ("loop_proposal", "complete_acceptance_manifest"),
        "effects": ("effects_command", "complete_acceptance_manifest"),
        "companions": ("effects_proposal", "exact_companion_manifest"),
    }[side]
    items = body[parent][key]
    body[parent][key] = {
        "omit": items[:-1],
        "add": [*items, items[0]],
        "duplicate": [items[0], items[0], items[2]],
        "reorder": list(reversed(items)),
    }[mutation]
    with pytest.raises(ValueError):
        verifier.build_acceptance_envelope(
            RetainedAcceptancePreparation.model_validate_json(json.dumps(body))
        )


def test_whole_serialized_physical_byte_bound_at_n_and_n_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = _prepared()
    size = len(verifier.build_acceptance_envelope(retained).canonical_bytes())
    monkeypatch.setattr(verifier, "MAX_ACCEPTANCE_BYTES", size)
    verifier.build_acceptance_envelope(retained)
    monkeypatch.setattr(verifier, "MAX_ACCEPTANCE_BYTES", size - 1)
    with pytest.raises(ValueError, match="physical byte bound"):
        verifier.build_acceptance_envelope(retained)


def test_unchecked_nested_model_copy_is_revalidated() -> None:
    retained = _prepared()
    bad = retained.model_copy(
        update={"effects_command": retained.effects_command.model_copy(update={"identity": None})}
    )
    with pytest.raises(ValueError):
        verifier.build_acceptance_envelope(bad)


def test_self_consistent_unregistered_semantics_is_rejected_after_both_owners_prepare() -> None:
    retained = _prepared()
    descriptor = SemanticComponent(
        component="reducer", effects_field="reducer_version", version="caller.reducer.v2"
    )
    unsupported = retained.loop_request.binding.dispatch_semantics.model_copy(
        update={
            "reducer": call_record_reference(
                "call-effects:semantic-component:reducer:v1", descriptor
            )
        }
    )
    intent = retained.effects_command.intent.model_copy(
        update={
            "semantics": retained.effects_command.intent.semantics.model_copy(
                update={"reducer_version": descriptor.version}
            )
        }
    )
    intent = intent.model_copy(update={"fingerprint": _fingerprint(intent)})
    request = retained.loop_request.model_copy(
        update={
            "binding": retained.loop_request.binding.model_copy(
                update={
                    "dispatch_semantics": unsupported,
                    "external_intent": _loop_ref(
                        effects.ExactHead(
                            subject_id=intent.intent_id,
                            head=intent.intent_id + "/" + intent.fingerprint,
                            fingerprint=intent.fingerprint,
                        )
                    ),
                }
            )
        }
    )
    loop = prepare_consequential_acceptance(request)
    assert isinstance(loop, call.PreparedConsequentialAcceptance)
    manifest = tuple(_effect_ref(ref) for ref in loop.complete_acceptance_manifest)
    command = retained.effects_command.model_copy(
        update={
            "intent": intent,
            "execution_intent": manifest[1],
            "complete_acceptance_manifest": manifest,
        }
    )
    candidate = RetainedAcceptancePreparation(
        loop_request=request,
        loop_proposal=loop,
        effects_command=command,
        effects_proposal=_prepare_effect(command, retained.effects_proposal.expected_store),
    )
    with pytest.raises(ValueError, match="unregistered loop semantics"):
        verifier.build_acceptance_envelope(candidate)


@pytest.mark.parametrize("wrong_domain", ("whole_record", "base64_command", "loop_tag_order"))
def test_effect_record_identity_rejects_alternate_self_consistent_preimages(
    wrong_domain: str,
) -> None:
    retained = _prepared()
    record = retained.effects_proposal.record
    body = record.model_dump(mode="json")
    del body["record"]
    if wrong_domain == "whole_record":
        raw = record.canonical_bytes()
    elif wrong_domain == "base64_command":
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    else:
        body["source_command"] = record.source_command.hex()
        raw = json.dumps(
            {key: body[key] for key in sorted(body, key=lambda k: (k != "kind", k))},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    digest = hashlib.sha256(raw).hexdigest()
    changed = record.model_copy(
        update={
            "record": effects.ExactHead(
                subject_id=record.record.subject_id,
                head=record.record.subject_id + "/" + digest,
                fingerprint=digest,
            )
        }
    )
    retained = retained.model_copy(
        update={
            "effects_proposal": retained.effects_proposal.model_copy(update={"record": changed})
        }
    )
    with pytest.raises(ValueError, match="effects record preimage"):
        verifier.build_acceptance_envelope(retained)
