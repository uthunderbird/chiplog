"""Selected native context issuance and exact trust/source readback under one gate."""

from __future__ import annotations

import hashlib
import json

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.capabilities.deployment_trust.native_one_call_scope_contracts import (
    IssueNativeOneCallContextScopeV1,
    NativeOneCallContextScopeAnchorV1,
    NativeOneCallContextScopeV1,
    NativeOneCallPolicySourceV1,
    NativeOneCallPolicyV1,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources
from chiplog.composition.r14_execution_inbox_records import RetainedInboxExecutionInitialization
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r16_dispatch_registry import ResourceObservation
from chiplog.composition.r17_authenticated_records import decode_authentication
from chiplog.platform.ingress_record_contracts import canonical_ingress_record_bytes
from chiplog.platform.native_one_call_scope_lineage import (
    AuthenticatedNativeOneCallContextScope,
    authenticated_native_one_call_scope_lineage,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _head(identity: str, body: bytes) -> ExactHead:
    digest = hashlib.sha256(body).hexdigest()
    return ExactHead(identity=identity, head=identity + "/" + digest, fingerprint=digest)


class NativeOneCallScopeService:
    """Canonical composition service. DTOs never act as a publication permit."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native context scope requires canonical common CLI runtime")
        from chiplog.capabilities.deployment_trust._native_one_call_scope_profile import (
            NativeOneCallContextSlotV1,
        )

        self._slot = NativeOneCallContextSlotV1()
        self._runtime = runtime
        self._gate = runtime._authority_gate()
        self._sources = H1SelectedOutputSources(runtime)

    def _observation(self) -> HermeticTrustObservationV1:
        trust = self._runtime._trust
        trust.capture_verified_observation()
        entries = trust._journal.entries()
        if not entries:
            raise LoopRejected("native context trust is absent")
        identity, _, raw = entries[-1]
        return HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=identity,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            logical_snapshot_head=trust.owner_snapshot_entries()[-1][0],
        )

    def _lineage(self) -> tuple[AuthenticatedNativeOneCallContextScope, ...]:
        self._gate.require_held()
        self._runtime._trust.capture_verified_observation()
        return authenticated_native_one_call_scope_lineage(
            self._runtime._trust._journal.entries(), self._runtime._trust._materializer.record
        )

    def _inventory(self) -> bytes:
        """Authenticate the complete anchored physical inventory before exclusion.

        The legacy scheduler reader refuses native Run schemas. This native
        profile therefore verifies the complete AMR cut directly and refuses
        any scheduler source, including mandates and unrecognized variants.
        """
        from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
        from chiplog.platform.workspace_snapshot import read_connection

        runtime = self._runtime
        snapshot = runtime._owner_decisions().snapshot()
        if {d.prepared.request.identity.command_id for d in snapshot.decisions} != set(
            snapshot.materialized_command_ids
        ):
            raise LoopRejected("native context has pending owner inventory")
        with read_connection(runtime._database) as connection:
            actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            if actual != runtime._commitment_journal.load(runtime._tenant_id):
                raise LoopRejected("native context inventory differs from anchored physical cut")
            rows = tuple(
                connection.execute(
                    "SELECT record_id, owner, schema_id, canonical_bytes "
                    "FROM main.records WHERE tenant_id = ? ORDER BY record_id",
                    (runtime._tenant_id,),
                )
            )
            if any(schema.startswith("chiplog.scheduler.") for _, _, schema, _ in rows) or any(
                d.prepared.request.operation.startswith("scheduler.") for d in snapshot.decisions
            ):
                raise LoopRejected("native context refuses scheduler/mandate inventory")
            return _canonical(
                {
                    "schema": "chiplog.native-cli-mandate-inventory.v1",
                    "registered_profile": "NATIVE_CLI_NO_SCHEDULER_SOURCE_V1",
                    "source_class": "CLI",
                    "authority_commitment": actual,
                    "issuance_frontier": connection.execute(
                        "SELECT head FROM main.tenant_heads WHERE tenant_id=?",
                        (runtime._tenant_id,),
                    ).fetchone()[0],
                    "selected_owner_head": snapshot.head,
                    "physical_source_refs": [
                        [identity, owner, schema, hashlib.sha256(raw).hexdigest()]
                        for identity, owner, schema, raw in rows
                    ],
                    "execution_mandates": [],
                }
            )

    def _check_cli_current(self, reference: TrustReference) -> None:
        # Replay the authenticated logical trust state; metadata-only equality
        # cannot substitute for the complete verified observation above.
        runtime = self._runtime
        entries = runtime._trust.owner_snapshot_entries()
        binding = None
        credential = None
        principal = None
        phase = None
        for _, _, raw in entries:
            envelope = json.loads(raw)
            kind, payload = envelope["kind"], envelope["payload"]
            if kind == "INITIALIZE":
                binding = payload["binding"]
                phase = "BOOTSTRAP_REQUIRED"
            elif kind == "BOOTSTRAP":
                credential, principal, phase = (
                    payload["credential"],
                    payload["principal_id"],
                    "ACTIVE",
                )
            elif kind not in {
                "HERMETIC_OUTPUT_SCOPE_V1",
                "NATIVE_ONE_CALL_CONTEXT_SCOPE_V1",
                "PREPARED_SELF_DELIVERY_POLICY_V1",
                "PREPARED_EXTERNAL_DELIVERY_GRANT_V2",
            }:
                raise LoopRejected("native context does not support this trust transition")
        if (
            not isinstance(binding, dict)
            or not isinstance(credential, dict)
            or phase != "ACTIVE"
            or credential.get("revoked")
            or principal != reference.principal_id.value
            or binding["tenant_id"] != reference.tenant_id.value
            or hashlib.sha256(_canonical(binding)).hexdigest() != reference.trust_head
            or credential["head"] != reference.credential_head
            or credential["session_head"] != reference.session_head
            or credential["peer_credential"] != reference.peer_credential
        ):
            raise LoopRejected("native context CLI session is stale")

    def issue_for_run(
        self, run: ExecutionRun, planned_prepare_command_id: str
    ) -> AuthenticatedNativeOneCallContextScope:
        with self._gate.hold():
            runtime = self._runtime
            matches = []
            for decision_id, _, raw in runtime._loop_decisions().entries():
                value = json.loads(raw)
                if value.get("kind") == "DECIDED" and isinstance(
                    value.get("inbox_initialization"), str
                ):
                    evidence = RetainedInboxExecutionInitialization.model_validate_json(
                        value["inbox_initialization"]
                    )
                    if evidence.proposal.run.run_id == run.run_id:
                        matches.append((decision_id, raw, evidence))
            if len(matches) != 1:
                raise LoopRejected("native context selected initialization is missing or rival")
            decision_id, raw, evidence = matches[0]
            wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
            admitted = runtime.read_admitted_inbox(
                wire.identity.original_ingress_identity.command_id
            )
            if admitted is None:
                raise LoopRejected("native context selected admission is absent")
            owner_call, authenticated = decode_authentication(admitted.record.command)
            from chiplog.domain_primitives import PrincipalId, TenantId

            reference = TrustReference(
                tenant_id=TenantId(authenticated.reference.tenant_id),
                principal_id=PrincipalId(authenticated.reference.principal_id),
                contour=authenticated.reference.contour,
                credential_head=authenticated.reference.credential_head,
                session_head=authenticated.reference.session_head,
                source_head="local",
                trust_head=authenticated.reference.trust_head,
                materialization_head=authenticated.reference.materialization_head,
                freshness_sequence=authenticated.reference.freshness_sequence,
                peer_credential=f"uid:{owner_call.request.socket.peer.uid}",
            )
            resources = ResourceObservation(
                evidence.dispatch_grant_bytes,
                evidence.dispatch_credential_bytes,
                evidence.dispatch_endpoint_bytes,
                evidence.dispatch_clock_epoch,
                evidence.dispatch_signature,
            )
            resource_ref = SelectedHermeticResourceObservationRefV1(
                signature_domain="dispatch-resources.v1",
                selected_initialization=ExactHead(
                    identity=evidence.proposal.run.head,
                    head=decision_id,
                    fingerprint=hashlib.sha256(raw).hexdigest(),
                ),
                signed_observation_fingerprint=runtime._require_dispatch_resources().observation_digest(
                    resources
                ),
            )
            scope_id = "native-context:" + planned_prepare_command_id
            prior = [
                item
                for item in self._lineage()
                if item.scope.database_id == wire.identity.database_id
                and item.scope.scope_id == scope_id
            ]
            if prior:
                raise LoopRejected("native context planned Prepare already has an issuance")
            intent = IssueNativeOneCallContextScopeV1(
                database_id=wire.identity.database_id,
                scope_id=scope_id,
                expected_trust_observation=self._observation(),
                expected_scope_predecessor=None,
                expected_revision=0,
                authenticated_cli_ref=reference,
                admitted_authentication_ref=ExactHead(
                    **admitted.record.inbox.authentication.proof.model_dump()
                ),
                selected_resource_observation_ref=resource_ref,
                worker_session_id=run.worker_session,
                planned_run_id=run.run_id,
                planned_run_head=ExactHead(
                    identity=run.run_id, head=run.head, fingerprint=run.digest()
                ),
                planned_prepare_command_id=planned_prepare_command_id,
            )
            return self.issue(intent)

    def issue(
        self, intent: IssueNativeOneCallContextScopeV1
    ) -> AuthenticatedNativeOneCallContextScope:
        if type(intent) is not IssueNativeOneCallContextScopeV1:
            raise TypeError("native context requires its distinct intent")
        intent = IssueNativeOneCallContextScopeV1.model_validate_json(intent.canonical_bytes())
        with self._gate.hold():
            runtime = self._runtime
            runtime._require_no_pending()
            runtime._check_database_identity()
            if intent.expected_trust_observation != self._observation():
                raise LoopRejected("native context trust predecessor is stale")
            captured = self._sources.capture_selected_current(
                intent.selected_resource_observation_ref,
                intent.admitted_authentication_ref,
                intent.authenticated_cli_ref,
            )
            if captured is None:
                raise LoopRejected("native context sources are not selected/current")
            self._check_cli_current(intent.authenticated_cli_ref)
            history = read_execution_history(runtime).records
            selected = [r for r in history if r.run_id == intent.planned_run_id]
            if not selected:
                raise LoopRejected("native context planned Run is absent")
            run = selected[-1]
            evidence = RetainedInboxExecutionInitialization.model_validate_json(
                json.loads(captured.initialization_envelope_bytes)["inbox_initialization"]
            )
            wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
            if (
                wire.identity.database_id != intent.database_id
                or run.run_id != evidence.proposal.run.run_id
                or run.worker_session != intent.worker_session_id
                or run.tenant != runtime._tenant_id
                or run.head != intent.planned_run_head.head
                or run.digest() != intent.planned_run_head.fingerprint
                or any(turn.attempts for turn in run.turns)
            ):
                raise LoopRejected("native context planned Run/worker/Prepare differs")
            if any(
                item.scope.planned_run_head == intent.planned_run_head for item in self._lineage()
            ):
                raise LoopRejected("native context already issued for this pre-Prepare Run")
            inventory = self._inventory()
            policy_bytes = NativeOneCallPolicyV1().canonical_bytes()
            observation = captured.resource_observation
            scope = NativeOneCallContextScopeV1(
                database_id=intent.database_id,
                scope_id=intent.scope_id,
                revision=intent.expected_revision,
                predecessor=intent.expected_scope_predecessor,
                worker_session_id=intent.worker_session_id,
                planned_run_id=intent.planned_run_id,
                planned_run_head=intent.planned_run_head,
                planned_prepare_command_id=intent.planned_prepare_command_id,
                authenticated_cli_ref=intent.authenticated_cli_ref,
                admitted_authentication=intent.admitted_authentication_ref,
                selected_resource_observation_ref=intent.selected_resource_observation_ref,
                recipient=captured.verified.recipient,
                disclosure_policy=NativeOneCallPolicySourceV1(
                    ref=_head("native-one-call-context-policy", policy_bytes),
                    canonical_source_bytes=policy_bytes,
                ),
                mandate_inventory_bytes=inventory,
                issuance_trust_observation=intent.expected_trust_observation,
                selected_initialization_bytes=captured.initialization_envelope_bytes,
                selected_admission_bytes=captured.admitted_record_bytes,
                selected_admission_ref=captured.selected_admitted_record_ref,
                selected_resource_grant_bytes=observation.grant_bytes,
                selected_resource_credential_bytes=observation.credential_bytes,
                selected_resource_endpoint_bytes=observation.endpoint_bytes,
                selected_resource_clock_epoch=observation.clock_epoch,
                selected_resource_signature=observation.signature,
            )
            return runtime._trust.append_native_one_call_context_scope(scope.canonical_bytes())

    def read_historical(
        self, anchor: NativeOneCallContextScopeAnchorV1
    ) -> AuthenticatedNativeOneCallContextScope:
        if type(anchor) is not NativeOneCallContextScopeAnchorV1:
            raise TypeError("native context requires its distinct trust anchor")
        with self._gate.hold():
            matches = [item for item in self._lineage() if item.anchor == anchor]
            if len(matches) != 1:
                raise LoopRejected("native context trust anchor is missing or rival")
            selected = matches[0]
            scope = selected.scope
            runtime = self._runtime
            # The selected issuance is pinned to its actual physical predecessor.
            trust_entries = runtime._trust._journal.entries()
            position = next(
                i for i, entry in enumerate(trust_entries) if entry[0] == selected.decision_id
            )
            if position == 0:
                raise LoopRejected("native context issuance has no trust predecessor")
            prior_id, _, prior_raw = trust_entries[position - 1]
            expected_prior = ExactHead(
                identity="deployment-trust/journal",
                head=prior_id,
                fingerprint=hashlib.sha256(prior_raw).hexdigest(),
            )
            logical_prefix = runtime._trust.owner_snapshot_entries()[:position]
            if (
                scope.issuance_trust_observation.physical_journal_head != expected_prior
                or scope.issuance_trust_observation.logical_snapshot_head != logical_prefix[-1][0]
            ):
                raise LoopRejected("native context issuance trust predecessor differs")
            if (
                scope.disclosure_policy.canonical_source_bytes
                != NativeOneCallPolicyV1().canonical_bytes()
                or scope.disclosure_policy.ref
                != _head(
                    "native-one-call-context-policy", NativeOneCallPolicyV1().canonical_bytes()
                )
            ):
                raise LoopRejected("native context registered disclosure source differs")
            wire_entry = json.loads(scope.selected_initialization_bytes)
            evidence = RetainedInboxExecutionInitialization.model_validate_json(
                wire_entry["inbox_initialization"]
            )
            wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
            from chiplog.composition.r14_execution_inbox_records import inbox_initialization_command

            command = inbox_initialization_command(evidence)
            state, actual, anchored = runtime._selected_physical_state(command)
            if state != "COMPLETE" or actual != anchored:
                raise LoopRejected("native context original initialization is not materialized")
            runtime._validate_historical(evidence, wire)
            entries = runtime._loop_decisions().entries()
            if not any(
                identity == scope.selected_resource_observation_ref.selected_initialization.head
                and raw == scope.selected_initialization_bytes
                for identity, _, raw in entries
            ):
                raise LoopRejected("native context original initialization bytes differ")
            admitted = runtime.read_admitted_inbox(
                wire.identity.original_ingress_identity.command_id
            )
            if (
                admitted is None
                or canonical_ingress_record_bytes(admitted.record) != scope.selected_admission_bytes
                or ExactHead(**admitted.physical_record.model_dump())
                != scope.selected_admission_ref
            ):
                raise LoopRejected("native context historical admission differs")
            observation = ResourceObservation(
                scope.selected_resource_grant_bytes,
                scope.selected_resource_credential_bytes,
                scope.selected_resource_endpoint_bytes,
                scope.selected_resource_clock_epoch,
                scope.selected_resource_signature,
            )
            expected_observation = ResourceObservation(
                evidence.dispatch_grant_bytes,
                evidence.dispatch_credential_bytes,
                evidence.dispatch_endpoint_bytes,
                evidence.dispatch_clock_epoch,
                evidence.dispatch_signature,
            )
            if (
                observation != expected_observation
                or wire.identity.database_id != scope.database_id
                or evidence.proposal.run.run_id != scope.planned_run_id
            ):
                raise LoopRejected("native context historical selected source join differs")
            _, authenticated = decode_authentication(admitted.record.command)
            from dataclasses import asdict

            reference = asdict(scope.authenticated_cli_ref)
            reference["tenant_id"] = scope.authenticated_cli_ref.tenant_id.value
            reference["principal_id"] = scope.authenticated_cli_ref.principal_id.value
            reference.pop("peer_credential")
            original_reference = authenticated.reference.model_dump(mode="json")
            original_reference = {
                key: original_reference[key] for key in reference if key in original_reference
            }
            if any(
                value != original_reference.get(key)
                for key, value in reference.items()
                if key != "source_head"
            ) or scope.admitted_authentication != ExactHead(
                **admitted.record.inbox.authentication.proof.model_dump()
            ):
                raise LoopRejected("native context historical CLI authentication differs")
            owner_call, _ = decode_authentication(admitted.record.command)
            if (
                scope.authenticated_cli_ref.source_head != "local"
                or scope.authenticated_cli_ref.peer_credential
                != f"uid:{owner_call.request.socket.peer.uid}"
            ):
                raise LoopRejected("native context historical CLI peer differs")
            resources = runtime._require_dispatch_resources()
            recipient = resources.historical_recipient(observation)
            if (
                not resources.verify_historical(observation)
                or resources.observation_digest(observation)
                != scope.selected_resource_observation_ref.signed_observation_fingerprint
                or scope.recipient != evidence.request.admitted.origin.recipient
                or recipient.recipient != scope.principal_id
            ):
                raise LoopRejected("native context historical resource/recipient differs")
            from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
                ExecutionRunCanonicalMember,
                decode_execution_run_member,
            )

            original = []
            for _, _, raw in entries:
                entry = json.loads(raw)
                if entry.get("kind") != "DECIDED" or not str(
                    entry.get("operation_kind", "")
                ).startswith("agent_loop"):
                    continue
                publication = runtime._publication(entry)
                for member in publication.records:
                    if member.owner != "agent_loop" or member.schema_id not in {
                        "chiplog.agent-loop.execution-record.v2",
                        "chiplog.agent-loop.execution-record.v3",
                    }:
                        continue
                    run = decode_execution_run_member(
                        ExecutionRunCanonicalMember.model_validate(
                            {
                                "record_id": member.record_id,
                                "schema_id": member.schema_id,
                                "canonical_record_bytes": member.canonical_bytes,
                                "fingerprint": member.fingerprint,
                            }
                        )
                    ).run
                    if (
                        run.run_id == scope.planned_run_id
                        and run.head == scope.planned_run_head.head
                        and run.digest() == scope.planned_run_head.fingerprint
                    ):
                        state, actual, anchored = runtime._selected_physical_state(publication)
                        if state != "COMPLETE" or actual != anchored:
                            raise LoopRejected("native context planned Run is not materialized")
                        original.append(run)
            if len(original) != 1 or original[0].worker_session != scope.worker_session_id:
                raise LoopRejected("native context historical planned Run differs")
            from chiplog.platform.workspace_snapshot import read_connection

            inventory = json.loads(scope.mandate_inventory_bytes)
            if (
                _canonical(inventory) != scope.mandate_inventory_bytes
                or inventory.get("schema") != "chiplog.native-cli-mandate-inventory.v1"
                or inventory.get("registered_profile") != self._slot.inventory_profile
                or inventory.get("source_class") != "CLI"
                or inventory.get("execution_mandates") != []
            ):
                raise LoopRejected("native context historical mandate inventory differs")
            with read_connection(runtime._database) as connection:
                row = connection.execute(
                    "SELECT commit_sequence FROM main.records WHERE tenant_id=? AND record_id=?",
                    (runtime._tenant_id, original[0].head),
                ).fetchone()
                if row is None:
                    raise LoopRejected("native context planned Run has no physical cut")
                frontier = inventory.get("issuance_frontier")
                current_frontier = connection.execute(
                    "SELECT head FROM main.tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
                ).fetchone()[0]
                if type(frontier) is not int or not row[0] <= frontier <= current_frontier:
                    raise LoopRejected("native context issuance inventory frontier differs")
                original_owners = runtime._owner_decisions().snapshot_at(
                    inventory.get("selected_owner_head")
                )
                if any(
                    d.tenant_commit_sequence > frontier
                    or d.prepared.request.operation.startswith("scheduler.")
                    for d in original_owners.decisions
                ):
                    raise LoopRejected("native context original selected mandate inventory differs")
                rows = tuple(
                    connection.execute(
                        "SELECT record_id, owner, schema_id, canonical_bytes FROM main.records "
                        "WHERE tenant_id=? AND commit_sequence<=? ORDER BY record_id",
                        (runtime._tenant_id, frontier),
                    )
                )
                refs = [
                    [identity, owner, schema, hashlib.sha256(raw).hexdigest()]
                    for identity, owner, schema, raw in rows
                ]
                if refs != inventory.get("physical_source_refs") or any(
                    schema.startswith("chiplog.scheduler.") for _, _, schema, _ in rows
                ):
                    raise LoopRejected("native context original mandate inventory source differs")
            return selected

    def read_current(
        self, anchor: NativeOneCallContextScopeAnchorV1
    ) -> AuthenticatedNativeOneCallContextScope:
        with self._gate.hold():
            selected = self.read_historical(anchor)
            scope = selected.scope
            latest = [
                item
                for item in self._lineage()
                if (item.scope.database_id, item.scope.scope_id)
                == (scope.database_id, scope.scope_id)
            ][-1]
            if latest != selected:
                raise LoopRejected("native context scope was superseded")
            captured = self._sources.capture_selected_current(
                scope.selected_resource_observation_ref,
                scope.admitted_authentication,
                scope.authenticated_cli_ref,
            )
            runs = [
                run
                for run in read_execution_history(self._runtime).records
                if run.run_id == scope.planned_run_id
            ]
            if (
                captured is None
                or captured.verified.recipient != scope.recipient
                or not runs
                or runs[-1].head != scope.planned_run_head.head
                or self._inventory() != scope.mandate_inventory_bytes
            ):
                raise LoopRejected("native context current sources changed")
            self._check_cli_current(scope.authenticated_cli_ref)
            return selected

    def require_execution_authority(self, anchor: NativeOneCallContextScopeAnchorV1) -> None:
        self.read_historical(anchor)
        raise LoopRejected(
            "native context scope grants no SEND, execution or disclosure; "
            "separate selected acceptance/mandate required"
        )
