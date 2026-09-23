"""Public denial contract and owner algebra; these fixtures prove no source provenance."""

import hashlib
import json

from chiplog.capabilities.effects.contracts import (
    CommandIdentity,
    EffectRecord,
    EffectSnapshot,
    EffectStoreSnapshot,
    ExactHead,
)
from chiplog.capabilities.effects.denial_contracts import (
    BeforeSendDispositionV2Command,
    CancelDecision,
    CurrentDenialInputs,
    DenialPreparationRequest,
    DenyingAuthority,
    DenyingSources,
)
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable
from tests.support.effects import head, intent


def make_denial_request() -> DenialPreparationRequest:
    original = intent()
    values = original.model_dump(mode="json")
    del values["fingerprint"]
    original = original.model_copy(
        update={
            "fingerprint": hashlib.sha256(
                json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        }
    )
    reference = ExactHead(
        subject_id=original.intent_id,
        head=original.intent_id + "/" + original.fingerprint,
        fingerprint=original.fingerprint,
    )
    fence = NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id="run",
        run_head="run-head",
        worker_session_id="current-denying-session",
        runtime_generation="current-generation",
    )
    source = CapturedSource(
        source_id="source",
        source_version="1",
        owner_id="source-owner",
        reader_id="registered-reader",
        invalidation_manifest=head("invalidators"),
        head=head("source"),
        canonical_value=b"\xffsource-evidence",
        clock_contract="monotonic-acquisition-v1",
        clock_epoch="new-epoch",
        valid_until_ns=300,
    )
    authority = DenyingAuthority(
        schema_id="chiplog.effects.denying-authority.v1",
        tenant_id="tenant",
        principal_id="principal",
        actor_id="principal",
        authenticated_session=head("fresh-session"),
        operation_profile=head("denial-policy"),
        intent=reference,
        expected_attempt=head("attempt"),
        decision=CancelDecision(evidence=head("decision"), direct_act=head("cancel-act")),
        fence=fence,
        clock_contract=source.clock_contract,
        clock_epoch=source.clock_epoch,
        valid_until_ns=300,
        sources=DenyingSources(
            **{
                name: source.model_copy(update={"source_id": name})
                for name in DenyingSources.model_fields
            }
        ),
    )
    command = BeforeSendDispositionV2Command(
        schema_id="chiplog.effects.before-send-command.v2",
        identity=CommandIdentity(
            command_id="cancel", fingerprint="cancel-fingerprint", expected_tenant_head=1
        ),
        intent=reference,
        expected_attempt=head("attempt"),
        authority=authority,
        authorization_to_retire=None,
    )
    prior = EffectRecord(
        record=head("historical-record"),
        command=CommandIdentity(
            command_id="original", fingerprint="original-command", expected_tenant_head=0
        ),
        predecessor=None,
        kind="PLAN_EFFECT_PUBLISHED",
        snapshot=EffectSnapshot(
            intent=original,
            attempt=command.expected_attempt,
            state="INTENT_RECORDED",
            authorizations=(),
            transmissions=(),
            evidence=(),
            unresolved_obligations=(),
        ),
        source_command=b"historical bytes: provenance belongs to composition",
    )
    return DenialPreparationRequest(
        schema_id="chiplog.effects.before-send-preparation.v2",
        command=command,
        expected=EffectStoreSnapshot(tenant_id="tenant", tenant_head=1, records=(prior,)),
        current=CurrentDenialInputs(
            command_id=command.identity.command_id,
            command_fingerprint=command.identity.fingerprint,
            store_frontier=1,
            authority=authority,
            supported_semantics=original.semantics,
            fence=fence,
            clock_contract=authority.clock_contract,
            clock_epoch=authority.clock_epoch,
            observed_time_ns=200,
        ),
    )
