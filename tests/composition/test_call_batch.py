"""Pure batch verification cannot substitute publication-envelope metadata."""

import pytest

from chiplog.capabilities.effects.dispatch_v2 import digest
from chiplog.composition.r14_call_batch import acceptance_records, verify_call_batch
from chiplog.composition.r14_call_dispatch_policy import policy_reference
from chiplog.composition.r16_effects_inputs import canonical
from chiplog.platform._owner_publication_contracts import CallEffectBatch
from tests.support.acceptance_v2 import prepared_acceptance


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "omit",
        "extra",
        "reorder",
        "kind",
        "schema",
        "payload",
        "command",
        "cut",
        "registry",
        "planning",
        "identity",
    ],
)
def test_exact_batch_graph_rejects_resigned_companion_and_header_mutations(mutation: str) -> None:
    retained = prepared_acceptance(command_id="accept")
    loop, effects = retained.loop_request, retained.effects_request
    cut = loop.binding.cut
    policy = policy_reference()
    records = acceptance_records(retained)
    data = {
        "identity": {
            "tenant_id": cut.tenant_id,
            "command_id": "accept",
            "command_fingerprint": digest(b"adoption"),
            "canonicalization_version": "chiplog.owner-publication.v1",
        },
        "authentication": {
            "kind": "WORKER",
            "invocation": {
                "issuance_id": "fixture",
                "issuance_fingerprint": digest(b"fixture"),
                "broker_epoch": "fixture",
                "broker_session": "fixture",
                "runtime_generation": "fixture",
                "operation_subject": "accept",
            },
            "applicability_schema": "fixture.unauthenticated",
            "applicability_bytes": b"fixture",
            "applicability_fingerprint": digest(b"fixture"),
        },
        "expected": {
            "tenant_id": cut.tenant_id,
            "tenant_frontier": cut.tenant_commit_sequence,
            "expected_materialization_commitment": cut.materialization_commitment,
            "registry_head": policy.head,
            "registry_fingerprint": policy.fingerprint,
            "ordered_heads": (),
            "complete_manifest_fingerprint": digest(b"fixture"),
        },
        "loop_command": {
            "owner": "agent_loop",
            "schema_id": "chiplog.call.acceptance-preparation.v1",
            "canonical_bytes": loop.canonical_bytes(),
            "fingerprint": digest(loop.canonical_bytes()),
        },
        "effects_command": {
            "owner": "effects",
            "schema_id": effects.schema_id,
            "canonical_bytes": effects.canonical_bytes(),
            "fingerprint": digest(effects.canonical_bytes()),
        },
        "planning": {"kind": "NOT_APPLICABLE"},
        "complete_records": records,
        "complete_batch_fingerprint": digest(canonical(records)),
    }
    batch = CallEffectBatch.model_validate(data)
    if mutation == "none":
        verify_call_batch(batch, retained)
        return
    if mutation == "omit":
        records = records[:2]
    elif mutation == "extra":
        records = (*records, records[0])
    elif mutation == "reorder":
        records = tuple(reversed(records))
    elif mutation in ("kind", "schema", "payload"):
        options: dict[str, dict[str, object]] = {
            "kind": {"record_kind": "other"},
            "schema": {"schema_id": "other"},
            "payload": {"canonical_bytes": b"{}", "fingerprint": digest(b"{}")},
        }
        changes = options[mutation]
        records = (records[0].model_copy(update=changes), *records[1:])
    elif mutation == "command":
        batch = batch.model_copy(update={"loop_command": batch.effects_command})
    elif mutation == "cut":
        batch = batch.model_copy(
            update={"expected": batch.expected.model_copy(update={"tenant_frontier": 1})}
        )
    elif mutation == "registry":
        batch = batch.model_copy(
            update={"expected": batch.expected.model_copy(update={"registry_head": "other"})}
        )
    elif mutation == "identity":
        batch = batch.model_copy(
            update={"identity": batch.identity.model_copy(update={"command_id": "other"})}
        )
    elif mutation == "planning":
        from chiplog.platform._owner_publication_contracts import PlanningParticipant

        batch = batch.model_copy(
            update={"planning": PlanningParticipant(command=batch.loop_command)}
        )
    batch = batch.model_copy(
        update={
            "complete_records": records,
            "complete_batch_fingerprint": digest(canonical(records)),
        }
    )
    with pytest.raises(ValueError):
        verify_call_batch(batch, retained)
