"""Fail-closed composition seam for the combined selected H1 source reader."""

import hashlib
import json

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust._output_scope_profile import VerifiedH1SelectedSources
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
    RetainedInboxExecutionInitialization,
)
from chiplog.composition.r16_dispatch_registry import ResourceObservation
from chiplog.composition.r17_authenticated_records import decode_authentication
from chiplog.domain_primitives import PrincipalId, TenantId


class H1SelectedOutputSources:
    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 sources require the canonical common CLI runtime")
        self._runtime = runtime
        self._resources = runtime._require_dispatch_resources()
        self._gate = runtime._authority_gate()
        if self._resources._require_gate() is not self._gate:
            raise ValueError("H1 sources require the runtime's registered resource gate")

    def resolve_selected_current(
        self,
        resource_ref: SelectedHermeticResourceObservationRefV1,
        admitted_authentication_ref: ExactHead,
        authenticated_cli_ref: TrustReference,
    ) -> VerifiedH1SelectedSources | None:
        if type(resource_ref) is not SelectedHermeticResourceObservationRefV1:
            raise TypeError("H1 sources require an exact selected resource locator")
        if type(admitted_authentication_ref) is not ExactHead:
            raise TypeError("H1 sources require an exact admitted authentication locator")
        if type(authenticated_cli_ref) is not TrustReference:
            raise TypeError("H1 sources require a CLI trust reference")
        with self._gate.hold():
            selected = self._selected_initialization(resource_ref)
            if selected is None:
                return None
            evidence, decision_id = selected
            wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
            found = self._runtime._find(wire.identity, evidence.driver_request_fingerprint)
            if found != (evidence, decision_id) or evidence.proposal.run.state != "CREATED":
                return None

            observation = ResourceObservation(
                evidence.dispatch_grant_bytes,
                evidence.dispatch_credential_bytes,
                evidence.dispatch_endpoint_bytes,
                evidence.dispatch_clock_epoch,
                evidence.dispatch_signature,
            )
            if (
                self._resources.observation_digest(observation)
                != resource_ref.signed_observation_fingerprint
                or not self._resources.verify_historical(observation)
                or not self._resources.verify_current(observation)
            ):
                return None
            recipient = self._resources.recipient(observation)
            derived_recipient = ProviderRecipient(
                provider_id=recipient.provider,
                account_id=recipient.account,
                recipient_id=recipient.recipient,
                endpoint=ExactHead(
                    identity=recipient.endpoint.subject_id,
                    head=recipient.endpoint.head,
                    fingerprint=recipient.endpoint.fingerprint,
                ),
                canonical_address=recipient.canonical_address,
                credential_binding=ExactHead(
                    identity=recipient.credential_binding.subject_id,
                    head=recipient.credential_binding.head,
                    fingerprint=recipient.credential_binding.fingerprint,
                ),
            )
            if derived_recipient != evidence.request.admitted.origin.recipient:
                return None

            admitted = self._runtime.read_admitted_inbox(
                wire.identity.original_ingress_identity.command_id
            )
            if admitted is None:
                return None
            owner_call, authenticated = decode_authentication(admitted.record.command)
            proof = admitted.record.inbox.authentication.proof
            if ExactHead(**proof.model_dump()) != admitted_authentication_ref:
                return None
            derived_authentication = TrustReference(
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
            if derived_authentication != authenticated_cli_ref:
                return None
            if (
                self._runtime._selected_input(wire, observation, historical=True)
                != evidence.request.admitted
            ):
                return None
            if derived_recipient != evidence.proposal.run.origin.recipient:
                return None
            return VerifiedH1SelectedSources(
                recipient=derived_recipient,
                authenticated_cli_ref=derived_authentication,
                admitted_authentication_ref=admitted_authentication_ref,
                selected_resource_observation_ref=resource_ref,
            )

    def _selected_initialization(
        self, resource_ref: SelectedHermeticResourceObservationRefV1
    ) -> tuple[RetainedInboxExecutionInitialization, str] | None:
        matches: list[tuple[RetainedInboxExecutionInitialization, str]] = []
        for decision_id, _, raw in self._runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if (
                entry.get("kind") != "DECIDED"
                or entry.get("operation_kind") != EXECUTION_INBOX_INITIALIZATION_OPERATION
                or decision_id != resource_ref.selected_initialization.head
                or hashlib.sha256(raw).hexdigest()
                != resource_ref.selected_initialization.fingerprint
            ):
                continue
            value = entry.get("inbox_initialization")
            if not isinstance(value, str):
                raise LoopRejected("selected inbox initialization evidence is absent")
            evidence = RetainedInboxExecutionInitialization.model_validate_json(value)
            if evidence.proposal.run.head != resource_ref.selected_initialization.identity:
                return None
            matches.append((evidence, decision_id))
        return matches[0] if len(matches) == 1 else None
