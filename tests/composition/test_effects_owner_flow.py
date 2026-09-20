"""Joint candidate-owner, isolated route and mechanical broker-adapter scenario.

No real broker writer, provider dispatch or runtime DoD is claimed.
"""

import hashlib
import json

import pytest

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.domain import EffectRuleViolation
from tests.support.effects import head, intent


def test_owner_candidate_send_and_unknown_preserve_child_without_any_transport() -> None:
    import base64

    from chiplog.capabilities.effects._process import dispatch
    from chiplog.capabilities.effects.application import prepare_transition
    from chiplog.capabilities.effects.contracts import (
        AdoptedAuthorityAct,
        AuthorizeDispatchCommand,
        BeforeSendDispositionCommand,
        CommandIdentity,
        CommitSendCommand,
        CurrentEffectInputs,
        DuplicateRiskPurpose,
        EffectCommand,
        EffectPreparationRequest,
        EffectStoreSnapshot,
        EvidenceAuthentication,
        FirstTransmission,
        OriginalAmbiguity,
        PublishPlanEffectCommand,
        PublishRecoveryIntentCommand,
        RecordEvidenceCommand,
        SafeRetransmission,
        TransportObservationBinding,
    )
    from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable

    original = intent()
    values = original.model_dump(mode="json")
    del values["fingerprint"]
    original = original.model_copy(
        update={
            "fingerprint": hashlib.sha256(
                json.dumps(
                    values,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
        }
    )
    intent_ref = ExactHead(
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
        worker_session_id="session",
        runtime_generation="gen",
    )
    store = EffectStoreSnapshot(tenant_id="tenant", tenant_head=0, records=())

    def identity(name: str) -> CommandIdentity:
        return CommandIdentity(
            command_id=name,
            fingerprint=hashlib.sha256((name + "-fingerprint").encode()).hexdigest(),
            expected_tenant_head=store.tenant_head,
        )

    def inputs(command: EffectCommand) -> CurrentEffectInputs:
        return CurrentEffectInputs(
            command_id=command.identity.command_id,
            command_fingerprint=command.identity.fingerprint,
            store_frontier=store.tenant_head,
            observed_time_ns=10,
            authority=original.authority,
            supported_semantics=original.semantics,
            fence=fence,
            authority_decision=head("decision"),
            blocking_effect_heads=(),
            current_original_ambiguity_heads=(),
            initialized_call=None,
            active_run_head="run-head",
            independently_verified_safe_proof=None,
            authenticated_evidence=None,
            original_reducer_semantics=original.semantics,
            authorized_reconciler=None,
        )

    create = PublishPlanEffectCommand(
        identity=identity("create"),
        intent=original,
        planning_publication=head("plan"),
        planning_owner_bytes=b"owner planning bytes",
        complete_publication_manifest=(head("plan"), intent_ref),
        fence=fence,
    )

    def assert_rival_blocked(blocker: ExactHead) -> PublishPlanEffectCommand:
        # New command, intent and idempotency identities must not erase an
        # already crossed action. Recompute the complete intent commitment.
        rival_values = original.model_dump(mode="json")
        rival_values.update(intent_id="rival-intent", idempotency_fence_key="rival-key")
        del rival_values["fingerprint"]
        rival = original.model_copy(
            update={
                "intent_id": "rival-intent",
                "idempotency_fence_key": "rival-key",
                "fingerprint": hashlib.sha256(
                    json.dumps(
                        rival_values, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                ).hexdigest(),
            }
        )
        rival_command = create.model_copy(
            update={"identity": identity("rival-create"), "intent": rival}
        )
        for inventory in ((), (blocker,), (blocker, blocker), (head("unknown-blocker"),)):
            with pytest.raises(EffectRuleViolation):
                prepare_transition(
                    rival_command,
                    store,
                    inputs(rival_command).model_copy(update={"blocking_effect_heads": inventory}),
                )
        # Different exact bytes can still conflict semantically. That broader
        # dependency is supplied by the broker, not guessed from payload equality.
        changed = rival.model_copy(
            update={
                "payload": b"different-conflicting-action",
                "effect_fingerprint": hashlib.sha256(b"different-conflicting-action").hexdigest(),
            }
        )
        changed_values = changed.model_dump(mode="json")
        del changed_values["fingerprint"]
        changed = changed.model_copy(
            update={
                "fingerprint": hashlib.sha256(
                    json.dumps(
                        changed_values, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                ).hexdigest()
            }
        )
        changed_command = rival_command.model_copy(update={"intent": changed})
        with pytest.raises(EffectRuleViolation, match="blocks replacement"):
            prepare_transition(
                changed_command,
                store,
                inputs(changed_command).model_copy(update={"blocking_effect_heads": (blocker,)}),
            )
        return rival_command

    publication = prepare_transition(create, store, inputs(create))
    wire = EffectPreparationRequest(
        operation="effects.publish_plan_effect",
        command_bytes=create.canonical_bytes(),
        expected=store,
        current=inputs(create),
    )
    owner_output = dispatch("effects.prepare_transition", wire.canonical_bytes())
    assert owner_output["payload"] == base64.b64encode(publication.canonical_bytes()).decode()
    assert "failure" in dispatch("effects.unregistered", wire.canonical_bytes())
    assert "failure" in dispatch("effects.prepare_transition", b" " + wire.canonical_bytes())
    assert publication.exact_companion_manifest == create.complete_publication_manifest
    store = store.model_copy(update={"tenant_head": 1, "records": (publication.record,)})
    authorize = AuthorizeDispatchCommand(
        identity=identity("authorize"),
        intent=intent_ref,
        expected_attempt=publication.record.snapshot.attempt,
        current_authority=original.authority,
        semantics=original.semantics,
        fence=fence,
    )
    publication = prepare_transition(authorize, store, inputs(authorize))
    store = store.model_copy(
        update={"tenant_head": 2, "records": (*store.records, publication.record)}
    )
    authorized = publication.record.snapshot
    hold = BeforeSendDispositionCommand(
        identity=identity("hold"),
        intent=intent_ref,
        expected_attempt=authorized.attempt,
        disposition="HELD_BEFORE_SEND",
        current_authority=original.authority,
        decision_evidence=head("decision"),
        fence=fence,
    )
    assert prepare_transition(hold, store, inputs(hold)).record.snapshot.state == "HELD_BEFORE_SEND"
    with pytest.raises(EffectRuleViolation):
        prepare_transition(
            hold.model_copy(update={"decision_evidence": head("stale-decision")}),
            store,
            inputs(hold),
        )
    send = CommitSendCommand(
        identity=identity("send"),
        expected_attempt=authorized.attempt,
        authorization=authorized.authorizations[-1],
        fence=fence,
        transmission=FirstTransmission(kind="FIRST_TRANSMISSION", ordinal=0),
        current_time_ns=10,
    )
    prepared_send = prepare_transition(send, store, inputs(send))
    assert CommitSendCommand.model_validate_json(send.canonical_bytes()) == send
    takeover = fence.model_copy(
        update={
            "worker_session_id": "takeover-worker",
            "runtime_generation": "takeover-generation",
        }
    )
    # A current worker cannot reuse an old authorization for the FIRST child.
    for proposed in (send, send.model_copy(update={"fence": takeover})):
        with pytest.raises(EffectRuleViolation):
            prepare_transition(
                proposed, store, inputs(proposed).model_copy(update={"fence": takeover})
            )
    assert prepared_send.record.snapshot.state == "SEND_COMMITTED"
    assert len(prepared_send.record.snapshot.transmissions) == 1
    assert authorized.transmissions == ()
    with pytest.raises(EffectRuleViolation):
        prepare_transition(
            send.model_copy(update={"expected_attempt": head("stale")}), store, inputs(send)
        )
    store = store.model_copy(
        update={"tenant_head": 3, "records": (*store.records, prepared_send.record)}
    )
    sent = prepared_send.record.snapshot
    assert_rival_blocked(sent.attempt)
    evidence = RecordEvidenceCommand(
        identity=identity("timeout"),
        expected_attempt=sent.attempt,
        evidence_id="timeout",
        raw_bytes=b"timeout",
        semantics=original.semantics,
        authentication=TransportObservationBinding(
            kind="BROKER_TRANSPORT_OBSERVATION",
            tenant_id="tenant",
            broker_epoch=head("epoch"),
            issued_operation=head("issued"),
            transmission=sent.transmissions[0].transmission,
            exact_recipient=original.authority.recipient,
            adapter_contract_version="fake-1",
            raw_digest=hashlib.sha256(b"timeout").hexdigest(),
        ),
        observation="TIMEOUT",
        covered_children=(sent.transmissions[0].transmission,),
        occurred_members=(),
        permanently_incapable_members=(),
    )
    with pytest.raises(EffectRuleViolation):
        prepare_transition(evidence, store, inputs(evidence))
    observed = inputs(evidence).model_copy(update={"authenticated_evidence": evidence})
    unknown = prepare_transition(evidence, store, observed)
    assert unknown.record.snapshot.state == "OUTCOME_UNKNOWN"
    assert unknown.record.snapshot.transmissions == sent.transmissions
    assert sent.state == "SEND_COMMITTED"
    fake_success = evidence.model_copy(
        update={
            "observation": "EXACT_EFFECT_CONFIRMED",
            "occurred_members": original.inseparable_bundle_members,
        }
    )
    with pytest.raises(EffectRuleViolation):
        prepare_transition(
            fake_success,
            store,
            observed.model_copy(update={"authenticated_evidence": fake_success}),
        )
    provider_auth = EvidenceAuthentication(
        kind="AUTHENTICATED_PROVIDER_EVIDENCE",
        broker_custody=head("custody"),
        ingress_surface="provider.callback",
        source_contract_version="fake-1",
        tenant_id="tenant",
        source_identity="provider",
        provider_account="account",
        endpoint=head("endpoint"),
        credential_key=head("credential"),
        audience="principal",
        raw_digest=hashlib.sha256(b"timeout").hexdigest(),
        source_replay_identity="receipt",
        intent=intent_ref,
        transmission=sent.transmissions[0].transmission,
        freshness=head("freshness"),
    )
    confirmed = fake_success.model_copy(update={"authentication": provider_auth})
    confirmed_inputs = observed.model_copy(update={"authenticated_evidence": confirmed})
    assert (
        prepare_transition(confirmed, store, confirmed_inputs).record.snapshot.state == "CONFIRMED"
    )
    for field, supplied_members in (
        ("occurred_members", (*original.inseparable_bundle_members, head("unknown-member"))),
        ("occurred_members", original.inseparable_bundle_members * 2),
        ("permanently_incapable_members", (head("unknown-member"),)),
        ("covered_children", confirmed.covered_children * 2),
        ("covered_children", (head("unknown-child"),)),
    ):
        mutated = confirmed.model_copy(update={field: supplied_members})
        with pytest.raises(EffectRuleViolation):
            prepare_transition(
                mutated,
                store,
                confirmed_inputs.model_copy(update={"authenticated_evidence": mutated}),
            )
    # Adapter replay must read the exact independent selected record before any
    # fresh owner snapshot/authority preparation. This fake tests wiring only.
    import asyncio

    from chiplog.adapters.driven.effects_broker import (
        BrokerEffectsStore,
        EffectsBatchContext,
        EffectsReplaySelected,
    )
    from chiplog.capabilities.effects.application import Effects
    from chiplog.capabilities.effects.contracts import (
        EffectPreparationRequest,
        PreparedEffectPublication,
    )
    from chiplog.platform._owner_publication_contracts import (
        ExactReplayQuery,
        InvocationProofRef,
        JournalSelectedPublication,
        NoSelectedDecision,
        OwnerCommandBytes,
        OwnerRecordBytes,
        PublicationIdentity,
        RegisteredPublication,
    )

    historical = EffectPreparationRequest(
        operation="effects.record_evidence",
        command_bytes=evidence.canonical_bytes(),
        expected=unknown.expected_store,
        current=observed,
    )
    raw = unknown.record.canonical_bytes()
    selected = JournalSelectedPublication(
        kind="EXACT_REPLAY",
        tenant_id="tenant",
        command_id=evidence.identity.command_id,
        decision_id="decision",
        decision_head="head",
        decision_fingerprint="a" * 64,
        predecessor_commitment="b" * 64,
        resulting_commitment="c" * 64,
        tenant_commit_sequence=4,
        complete_records=(
            OwnerRecordBytes(
                owner="effects",
                record_kind="effects.EVIDENCE_RECORDED",
                record_id=unknown.record.record.head,
                schema_id="chiplog.effects.record.v1",
                canonical_bytes=raw,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
        ),
    )

    class Journal:
        response: JournalSelectedPublication | NoSelectedDecision = selected

        def lookup_exact(
            self, query: ExactReplayQuery
        ) -> JournalSelectedPublication | NoSelectedDecision:
            assert query.original_commands[0].canonical_bytes == historical.canonical_bytes()
            return self.response

        async def commit(self, request: RegisteredPublication) -> JournalSelectedPublication:
            raise AssertionError("exact replay cannot commit another decision")

    class Queries:
        tenant_id = "tenant"

        def effects_snapshot(self) -> EffectStoreSnapshot:
            raise AssertionError("exact replay cannot prepare from a fresh snapshot")

        def publication_context(
            self, publication: PreparedEffectPublication
        ) -> EffectsBatchContext:
            raise AssertionError("exact replay cannot prepare a new publication")

        def replay_observation(self, command: EffectCommand) -> EffectsReplaySelected:
            query = ExactReplayQuery(
                identity=PublicationIdentity(
                    tenant_id="tenant",
                    command_id=command.identity.command_id,
                    command_fingerprint=command.identity.fingerprint,
                    canonicalization_version="chiplog.owner-publication.v1",
                ),
                operation="effects.record_evidence",
                current_invocation=InvocationProofRef(
                    issuance_id="issued",
                    issuance_fingerprint="d" * 64,
                    broker_epoch="epoch",
                    broker_session="session",
                    runtime_generation="generation",
                    operation_subject="intent",
                ),
                original_commands=(
                    OwnerCommandBytes(
                        owner="effects",
                        schema_id="chiplog.effects.prepare.v1",
                        canonical_bytes=historical.canonical_bytes(),
                        fingerprint=hashlib.sha256(historical.canonical_bytes()).hexdigest(),
                    ),
                ),
            )

            return EffectsReplaySelected(query, historical, unknown)

    class Observations:
        def observe(
            self, command: EffectCommand, expected: EffectStoreSnapshot
        ) -> CurrentEffectInputs:
            raise AssertionError("historical replay cannot reauthorize current inputs")

    journal = Journal()
    adapter = BrokerEffectsStore(journal, Queries())
    replay = asyncio.run(Effects(adapter, Observations()).submit(evidence))
    assert replay.disposition == "REPLAY"
    journal.response = NoSelectedDecision(
        tenant_id="tenant", command_id=evidence.identity.command_id
    )
    # Losing an already-selected decision cannot fall through to fresh preparation.
    with pytest.raises(ValueError, match="previously selected effects decision disappeared"):
        asyncio.run(Effects(adapter, Observations()).submit(evidence))
    journal.response = selected.model_copy(
        update={
            "complete_records": (
                selected.complete_records[0].model_copy(
                    update={"record_id": "aliased-physical-record"}
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="physical/logical identity"):
        adapter.replay(evidence)
    assert unknown.record.snapshot.recovery_obligation is not None
    assert unknown.record.snapshot.recovery_obligation.kind == "OPEN"
    opening = unknown.record.snapshot.recovery_obligation
    assert unknown.record.snapshot.unresolved_obligations == (opening.obligation,)
    store = store.model_copy(update={"tenant_head": 4, "records": (*store.records, unknown.record)})
    proof = SafeRetransmission(
        kind="SAFE_RETRANSMISSION",
        ordinal=1,
        prior_children=tuple(child.transmission for child in sent.transmissions),
        proof_kind="PROVIDER_IDEMPOTENCY_COVERS_ALL",
        proof=head("all-child-coverage"),
        covered_effect_fingerprint=original.effect_fingerprint,
        coverage_starts_ns=0,
        coverage_expires_ns=100,
    )
    retry = CommitSendCommand(
        identity=identity("safe-retry-after-takeover"),
        expected_attempt=unknown.record.snapshot.attempt,
        authorization=authorized.authorizations[-1],
        fence=takeover,
        transmission=proof,
        current_time_ns=10,
    )
    retry_inputs = inputs(retry).model_copy(
        update={
            "fence": takeover,
            "independently_verified_safe_proof": proof,
        }
    )
    retried = prepare_transition(retry, store, retry_inputs).record.snapshot
    assert retried.state == "OUTCOME_UNKNOWN"
    assert retried.intent == original
    assert retried.authorizations == unknown.record.snapshot.authorizations
    assert retried.transmissions[:-1] == sent.transmissions
    assert retried.transmissions[-1].ordinal == 1
    assert retried.recovery_obligation == opening
    assert retried.unresolved_obligations == (opening.obligation,)
    assert unknown.record.snapshot.transmissions == sent.transmissions
    assert CommitSendCommand.model_validate_json(retry.canonical_bytes()).fence == takeover
    for rejected_command, rejected_inputs in (
        (retry.model_copy(update={"fence": fence}), retry_inputs),
        (retry, retry_inputs.model_copy(update={"independently_verified_safe_proof": None})),
        (
            retry,
            retry_inputs.model_copy(
                update={
                    "authority": original.authority.model_copy(
                        update={"actor_id": "another-actor"}
                    ),
                }
            ),
        ),
    ):
        with pytest.raises(EffectRuleViolation):
            prepare_transition(rejected_command, store, rejected_inputs)
    for unsafe in (
        proof.model_copy(update={"prior_children": ()}),
        proof.model_copy(update={"coverage_expires_ns": 5}),
    ):
        with pytest.raises(EffectRuleViolation):
            prepare_transition(
                retry.model_copy(update={"transmission": unsafe}),
                store,
                retry_inputs.model_copy(update={"independently_verified_safe_proof": unsafe}),
            )
    rival = assert_rival_blocked(unknown.record.snapshot.attempt)
    with pytest.raises(EffectRuleViolation, match="not current"):
        prepare_transition(
            rival,
            store,
            inputs(rival).model_copy(update={"blocking_effect_heads": (sent.attempt,)}),
        )
    adoption = AdoptedAuthorityAct(
        kind="EXACT_PROPOSAL_ADOPTION",
        proposal=head("risk-preview"),
        display_digest="risk-display",
        adoption=head("risk-adoption"),
        ingress=head("risk-ingress"),
        interpretation=head("risk-interpretation"),
    )
    risk_intent = rival.intent.model_copy(
        update={
            "authority": original.authority.model_copy(update={"act": adoption}),
            "purpose": DuplicateRiskPurpose(
                kind="AUTHORIZE_DUPLICATE_RISK",
                unresolved_attempts=(
                    OriginalAmbiguity(
                        original_intent=intent_ref,
                        original_binding=original.semantics,
                        ambiguity_reconciliation_head=opening.obligation,
                    ),
                ),
                possible_duplicate_effects=(head("duplicate-effect"),),
                affected_parties_resources=(head("affected-party"),),
                commitment_consequences=(head("consequence"),),
                safer_alternatives=(head("wait-reconcile"),),
                preview_adoption=adoption,
            ),
        }
    )
    risk_values = risk_intent.model_dump(mode="json")
    del risk_values["fingerprint"]
    risk_intent = risk_intent.model_copy(
        update={
            "fingerprint": hashlib.sha256(
                json.dumps(
                    risk_values, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()
        }
    )
    risk_command = PublishRecoveryIntentCommand(
        identity=identity("risk-create"),
        intent=risk_intent,
        original_heads=(opening.obligation,),
        current_authority=risk_intent.authority,
        fence=fence,
    )
    risk_inputs = inputs(risk_command).model_copy(
        update={
            "authority": risk_intent.authority,
            "blocking_effect_heads": (unknown.record.snapshot.attempt,),
            "current_original_ambiguity_heads": (opening.obligation,),
        }
    )
    assert (
        prepare_transition(risk_command, store, risk_inputs).record.snapshot.state
        == "INTENT_RECORDED"
    )
    late = confirmed.model_copy(
        update={
            "identity": identity("late-confirmed"),
            "evidence_id": "late-confirmed",
            "expected_attempt": unknown.record.snapshot.attempt,
        }
    )
    late_inputs = inputs(late).model_copy(update={"authenticated_evidence": late})
    closed_publication = prepare_transition(late, store, late_inputs)
    closure = closed_publication.record.snapshot
    assert closure.state == "CONFIRMED"
    assert closure.recovery_obligation is not None
    assert closure.recovery_obligation.kind == "CLOSED"
    assert closure.recovery_obligation.opening == opening
    assert closure.recovery_obligation.obligation.subject_id == opening.obligation.subject_id
    assert closure.unresolved_obligations == ()
    assert unknown.record.snapshot.recovery_obligation == opening
    store = store.model_copy(
        update={"tenant_head": 5, "records": (*store.records, closed_publication.record)}
    )
    rival = rival.model_copy(update={"identity": identity("resolved-rival")})
    assert (
        prepare_transition(rival, store, inputs(rival)).record.snapshot.state == "INTENT_RECORDED"
    )
    risk_command = risk_command.model_copy(update={"identity": identity("stale-risk")})
    risk_inputs = risk_inputs.model_copy(
        update={
            "command_id": risk_command.identity.command_id,
            "command_fingerprint": risk_command.identity.fingerprint,
            "store_frontier": 5,
        }
    )
    with pytest.raises(EffectRuleViolation, match="not current"):
        prepare_transition(risk_command, store, risk_inputs)
