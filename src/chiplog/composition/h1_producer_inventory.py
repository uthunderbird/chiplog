"""Issuer-held, complete H1 producer inventory.

This is the narrow broker seam between the current scoped-authority reader and
the authenticated Effects-history reader.  It deliberately retains opaque
issuer captures as well as the DTOs sent to Effects: DTO equality alone is not
authority or currentness.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.h1_normative_conflict_generation import (
    h1_effects_history_capture,
    h1_effects_history_head,
    h1_normative_conflict_generation_capture,
)
from chiplog.capabilities.effects.h1_producer_semantics import (
    H1_PRODUCER_SEMANTICS,
    h1_producer_semantic_registry_capture,
)
from chiplog.capabilities.effects.h1_producer_source_contracts import (
    H1ProducerCurrentInputsV1,
    H1ProducerNotApplicableV1,
    H1ProducerSourceInventoryV1,
)
from chiplog.capabilities.effects.h1_scoped_preparation import derive_h1_scoped_delivery
from chiplog.composition.h1_producer_sources import (
    H1ProducerSourceCapture,
    H1ProducerSourceReader,
    H1ProducerSourceViolation,
)
from chiplog.composition.h1_scoped_delivery_authority import (
    H1ScopedDeliveryAuthorityCapture,
    H1ScopedDeliveryAuthorityReader,
    H1ScopedDeliveryAuthorityViolation,
    _AuthorityRecord,
)

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.r16_effects import CurrentEffectsWorker
    from chiplog.platform.broker import BrokerSession


_CLOCK = "chiplog.dispatch.monotonic.v2"
_IDENTITY_DOMAIN = "chiplog.h1-scoped-delivery.identity.v1"


class H1ProducerInventoryViolation(PermissionError):
    """A producer inventory is foreign, expired, or no longer current."""


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _head(subject: str, raw: bytes) -> ExactHead:
    digest = hashlib.sha256(raw).hexdigest()
    return ExactHead(subject_id=subject, head=f"{subject}/{digest}", fingerprint=digest)


def _source(role: str, raw: bytes, *, epoch: str, deadline: int, owner: str) -> CapturedSource:
    head = _head(f"chiplog.h1-producer.{role}.v1", raw)
    return CapturedSource(
        source_id=head.subject_id,
        source_version="v1",
        owner_id=owner,
        reader_id="chiplog.composition.h1-producer-inventory.v1",
        invalidation_manifest=head,
        head=head,
        canonical_value=raw,
        clock_contract=_CLOCK,
        clock_epoch=epoch,
        valid_until_ns=deadline,
    )


@dataclass(frozen=True, slots=True)
class _InventoryRecord:
    authority_capture: H1ScopedDeliveryAuthorityCapture
    physical_capture: H1ProducerSourceCapture
    original_completion_request: object
    prepared_completion: object
    selected_scope: object
    retained_origin: object
    projection: object
    evidence: object
    derivation: object
    original_sources: H1ProducerSourceInventoryV1
    current: H1ProducerCurrentInputsV1
    intent_id: str
    command_id: str
    command_fingerprint: str
    expected_tenant_head: int
    current_worker: CurrentEffectsWorker
    runtime_sessions: tuple[BrokerSession, BrokerSession, BrokerSession]
    clock: tuple[str, int, int]


class H1ProducerInventoryCapture:
    """Non-copyable inventory issued by :class:`H1ProducerInventoryReader`."""

    __slots__ = ("_record",)
    _record: _InventoryRecord

    def __init__(self) -> None:
        raise TypeError("H1 producer inventory captures are reader-issued")

    def __copy__(self) -> H1ProducerInventoryCapture:
        raise TypeError("H1 producer inventory captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ProducerInventoryCapture:
        del memo
        raise TypeError("H1 producer inventory captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 producer inventory captures cannot be serialized")

    # These properties are intentionally DTO/read-only views.  The issuer
    # registry remains the authority for lifetime and currentness.
    authority_capture = property(lambda self: self._record.authority_capture)
    physical_capture = property(lambda self: self._record.physical_capture)
    original_completion_request = property(lambda self: self._record.original_completion_request)
    prepared_completion = property(lambda self: self._record.prepared_completion)
    selected_scope = property(lambda self: self._record.selected_scope)
    retained_origin = property(lambda self: self._record.retained_origin)
    projection = property(lambda self: self._record.projection)
    evidence = property(lambda self: self._record.evidence)
    derivation = property(lambda self: self._record.derivation)
    original_sources = property(lambda self: self._record.original_sources)
    current = property(lambda self: self._record.current)
    intent_id = property(lambda self: self._record.intent_id)
    command_id = property(lambda self: self._record.command_id)
    command_fingerprint = property(lambda self: self._record.command_fingerprint)
    expected_tenant_head = property(lambda self: self._record.expected_tenant_head)
    current_worker = property(lambda self: self._record.current_worker)
    runtime_sessions = property(lambda self: self._record.runtime_sessions)
    clock = property(lambda self: self._record.clock)


class H1ProducerInventoryReader:
    """Read one full H1 producer input inventory from mounted owners."""

    __slots__ = ("_authority_reader", "_closed", "_issued", "_physical_reader", "_runtime")

    def __init__(
        self,
        runtime: CommonCliExecutionRuntime,
        authority_reader: H1ScopedDeliveryAuthorityReader,
        physical_reader: H1ProducerSourceReader,
    ) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 producer inventory requires the installed common CLI runtime")
        if (
            type(authority_reader) is not H1ScopedDeliveryAuthorityReader
            or authority_reader._runtime is not runtime
        ):
            raise TypeError("H1 producer inventory requires its runtime authority reader")
        if (
            type(physical_reader) is not H1ProducerSourceReader
            or physical_reader._runtime is not runtime
            or physical_reader._authority_reader is not authority_reader
        ):
            raise TypeError("H1 producer inventory requires its runtime physical reader")
        self._runtime = runtime
        self._authority_reader = authority_reader
        self._physical_reader = physical_reader
        self._issued: dict[int, tuple[H1ProducerInventoryCapture, _InventoryRecord]] = {}
        self._closed = False

    async def read(
        self,
        *,
        authority_capture: H1ScopedDeliveryAuthorityCapture,
        pinned_lease_expires_at_ns: int | None = None,
        pinned_observed_time_ns: int | None = None,
    ) -> H1ProducerInventoryCapture:
        """Derive identity before the physical read, then authenticate the final join."""
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            authority = self._authority_record_held(authority_capture)
            intent_id, command_id = self._identity_held(authority)
        # No await is performed while this reader holds AuthorityGate.
        physical_capture = await self._physical_reader.read(
            authority_capture=authority_capture,
            intent_id=intent_id,
            pinned_valid_until_ns=pinned_lease_expires_at_ns,
        )
        with self._runtime._authority_gate().hold():
            try:
                self._require_open_held()
                self._physical_reader.recheck_held(physical_capture)
                record = self._build_record_held(
                    authority_capture,
                    physical_capture,
                    intent_id,
                    command_id,
                    observed_time_ns=pinned_observed_time_ns,
                )
                capture = object.__new__(H1ProducerInventoryCapture)
                capture._record = record
                self._issued[id(capture)] = (capture, record)
                return capture
            except Exception:
                self._physical_reader.release(physical_capture)
                raise

    def recheck_held(self, capture: H1ProducerInventoryCapture) -> H1ProducerInventoryCapture:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        try:
            self._authority_reader._verify_record_held(
                self._authority_reader._issued_record_held(record.authority_capture)
            )
            self._physical_reader.recheck_held(record.physical_capture)
        except (
            H1ScopedDeliveryAuthorityViolation,
            H1ProducerSourceViolation,
            TypeError,
            ValueError,
        ) as error:
            raise H1ProducerInventoryViolation(
                "H1 producer inventory backing capture is not current"
            ) from error
        fresh_epoch, fresh_now = self._runtime._require_dispatch_resources().clock()
        rebuilt = self._build_record_held(
            record.authority_capture,
            record.physical_capture,
            record.intent_id,
            record.command_id,
            observed_time_ns=record.clock[1],
        )
        if (
            rebuilt.original_sources != record.original_sources
            or rebuilt.current != record.current
            or rebuilt.runtime_sessions != record.runtime_sessions
            or rebuilt.expected_tenant_head != record.expected_tenant_head
            or fresh_epoch != record.clock[0]
            or not record.clock[1] <= fresh_now < record.clock[2]
        ):
            raise H1ProducerInventoryViolation("H1 producer inventory changed or lease expired")
        return capture

    def release(self, capture: H1ProducerInventoryCapture) -> None:
        with self._runtime._authority_gate().hold():
            record = self._issued_record_held(capture)
            del self._issued[id(capture)]
            self._physical_reader.release(record.physical_capture)

    def close(self) -> None:
        with self._runtime._authority_gate().hold():
            for _, record in tuple(self._issued.values()):
                self._physical_reader.release(record.physical_capture)
            self._closed = True
            self._issued.clear()

    def _require_open_held(self) -> None:
        self._runtime._authority_gate().require_held()
        if self._closed:
            raise H1ProducerInventoryViolation("H1 producer inventory reader is closed")

    def _issued_record_held(self, capture: H1ProducerInventoryCapture) -> _InventoryRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ProducerInventoryCapture
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ProducerInventoryViolation("H1 producer inventory capture is not reader-issued")
        return issued[1]

    def _authority_record_held(self, capture: H1ScopedDeliveryAuthorityCapture) -> _AuthorityRecord:
        try:
            record = self._authority_reader._issued_record_held(capture)
            self._authority_reader._verify_record_held(record)
            return record
        except (H1ScopedDeliveryAuthorityViolation, TypeError, ValueError) as error:
            raise H1ProducerInventoryViolation(
                "H1 scoped authority capture is not current"
            ) from error

    @staticmethod
    def _identity_held(authority: _AuthorityRecord) -> tuple[str, str]:
        projection = authority.projection
        evidence = authority.evidence
        preimage = _json(
            {
                "domain": _IDENTITY_DOMAIN,
                "tenant_id": projection.tenant_id,
                "database_id": projection.database_id,
                "basis": projection.basis.model_dump(mode="json"),
                "grant_anchor": evidence.grant_anchor.model_dump(mode="json"),
                "policy_anchor": evidence.policy_anchor.model_dump(mode="json"),
            }
        )
        digest = hashlib.sha256(preimage).hexdigest()
        return "h1-scoped-intent:" + digest, "h1-scoped-delivery:" + digest

    def _sessions_held(self) -> tuple[BrokerSession, BrokerSession, BrokerSession]:
        engine = self._runtime._supervisor.runtime()
        return (
            engine.session("effects"),
            engine.session("agent_loop"),
            engine.session("deployment_trust"),
        )

    def _build_record_held(
        self,
        authority_capture: H1ScopedDeliveryAuthorityCapture,
        physical_capture: H1ProducerSourceCapture,
        intent_id: str,
        command_id: str,
        *,
        observed_time_ns: int | None = None,
    ) -> _InventoryRecord:
        self._runtime._authority_gate().require_held()
        authority = self._authority_record_held(authority_capture)
        physical = self._physical_reader._issued_record_held(physical_capture)
        if physical.authority_capture is not authority_capture:
            raise H1ProducerInventoryViolation(
                "H1 producer physical capture has a foreign authority issuer"
            )
        projection = authority.projection
        registry = self._runtime._h1_completion_exchange_registry
        if registry is None:
            raise H1ProducerInventoryViolation("H1 completion registry is unavailable")
        preimages = registry.replay_prepared_delivery_preimages(authority.historical_capture)
        epoch, actual_now_ns = self._runtime._require_dispatch_resources().clock()
        grant = __import__(
            "chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts",
            fromlist=["PreparedExternalDeliveryGrantV2"],
        ).PreparedExternalDeliveryGrantV2.model_validate_json(
            authority.evidence.canonical_grant_bytes
        )
        policy = __import__(
            "chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts",
            fromlist=["PreparedExternalSelfDeliveryPolicyV1"],
        ).PreparedExternalSelfDeliveryPolicyV1.model_validate_json(
            authority.evidence.canonical_policy_bytes
        )
        deadline = min(
            physical.valid_until_ns, grant.scope.mandate.expires_at_ns, policy.terms.expires_at_ns
        )
        if (
            epoch != physical.clock_epoch
            or not policy.terms.not_before_ns <= actual_now_ns < deadline
            or not grant.scope.mandate.not_before_ns <= actual_now_ns < deadline
        ):
            raise H1ProducerInventoryViolation(
                "H1 producer inventory clock or mandate lease is invalid"
            )
        now_ns = actual_now_ns if observed_time_ns is None else observed_time_ns
        if observed_time_ns is not None and (
            type(observed_time_ns) is not int
            or observed_time_ns > actual_now_ns
            or observed_time_ns < policy.terms.not_before_ns
            or observed_time_ns < grant.scope.mandate.not_before_ns
        ):
            raise H1ProducerInventoryViolation(
                "H1 producer pinned observation time is invalid"
            )
        sessions = self._sessions_held()
        if (
            physical.worker.owner_session != sessions[1]
            or physical.worker.fence.runtime_generation != sessions[1].generation_id
        ):
            raise H1ProducerInventoryViolation(
                "H1 producer runtime session differs from physical worker"
            )
        selected_scope = projection.selected_scope.model_copy(
            update={
                "current_request": authority.fresh_scope_request,
                "current_result": authority.fresh_scope_result,
            }
        )
        semantic = h1_producer_semantic_registry_capture(
            clock_contract=_CLOCK, clock_epoch=epoch, valid_until_ns=deadline
        )
        history = h1_effects_history_capture(
            physical.history, clock_contract=_CLOCK, clock_epoch=epoch, valid_until_ns=deadline
        )
        generation = h1_normative_conflict_generation_capture(
            physical.generation, clock_contract=_CLOCK, clock_epoch=epoch, valid_until_ns=deadline
        )
        selected = authority.selected_sources
        runtime_raw = _json(
            {
                "run": physical.worker.run.model_dump(mode="json"),
                "fence": physical.worker.fence.model_dump(mode="json"),
                "sessions": [item.model_dump(mode="json") for item in sessions],
            }
        )
        clock_raw = _json({"contract": _CLOCK, "epoch": epoch, "observed_time_ns": now_ns})
        trust_raw = _json(
            {
                "evidence": authority.evidence.model_dump(mode="json"),
                "scope_request": authority.fresh_scope_request.model_dump(mode="json"),
                "scope_result": authority.fresh_scope_result.model_dump(mode="json"),
            }
        )
        # First derive the immutable mandate candidate from authenticated B,
        # authority, and physical history; only then place it in both exceptions.
        derivation = derive_h1_scoped_delivery(
            basis=projection.basis,
            original_run=ExactHead(
                subject_id=projection.original_run.identity,
                head=projection.original_run.head,
                fingerprint=projection.original_run.fingerprint,
            ),
            captured_attempt=ExactHead(
                subject_id=projection.captured_attempt.identity,
                head=projection.captured_attempt.head,
                fingerprint=projection.captured_attempt.fingerprint,
            ),
            delivery=projection.delivery,
            evidence=authority.evidence,
            grant=grant,
            policy=policy,
            target_intent_id=intent_id,
            original_history=history,
            current_history=history,
            original_generation=generation,
            current_generation=generation,
            history_observation=h1_effects_history_head(physical.history),
            clock_contract=_CLOCK,
            clock_epoch=epoch,
            valid_until_ns=deadline,
        )
        candidate = derivation.candidate
        sources = H1ProducerSourceInventoryV1(
            trust=_source(
                "trust", trust_raw, epoch=epoch, deadline=deadline, owner="deployment_trust"
            ),
            planning=H1ProducerNotApplicableV1(role="planning", candidate=candidate),
            semantic_registry=semantic,
            original_adoption=H1ProducerNotApplicableV1(
                role="original_adoption", candidate=candidate
            ),
            runtime_and_fence=_source(
                "runtime-and-fence", runtime_raw, epoch=epoch, deadline=deadline, owner="broker"
            ),
            endpoint=_source(
                "endpoint",
                selected.resource_observation.endpoint_bytes,
                epoch=epoch,
                deadline=deadline,
                owner="deployment_trust",
            ),
            credential_lifecycle=_source(
                "credential-lifecycle",
                selected.resource_observation.credential_bytes,
                epoch=epoch,
                deadline=deadline,
                owner="deployment_trust",
            ),
            deployment_entitlement=_source(
                "deployment-entitlement",
                selected.resource_observation.grant_bytes,
                epoch=epoch,
                deadline=deadline,
                owner="deployment_trust",
            ),
            clock=_source(
                "clock", clock_raw, epoch=epoch, deadline=deadline, owner="deployment_trust"
            ),
            effects_history=history,
            normative_conflict_generation=generation,
        )
        fingerprint = hashlib.sha256(command_id.encode()).hexdigest()
        current = H1ProducerCurrentInputsV1(
            command_fingerprint=fingerprint,
            immutable_mandate_candidate=candidate,
            physical_cut=physical.physical_cut_head,
            history_observation=h1_effects_history_head(physical.history),
            sources=sources,
            supported_semantics=H1_PRODUCER_SEMANTICS,
            clock_contract=_CLOCK,
            clock_epoch=epoch,
            observed_time_ns=now_ns,
            lease_expires_at_ns=deadline,
        )
        return _InventoryRecord(
            authority_capture,
            physical_capture,
            preimages.original_completion_request,
            preimages.prepared_completion,
            selected_scope,
            projection.retained_origin,
            projection,
            authority.evidence,
            derivation,
            sources,
            current,
            intent_id,
            command_id,
            fingerprint,
            physical.physical_cut.tenant_frontier,
            physical.worker,
            sessions,
            (epoch, now_ns, deadline),
        )


__all__ = [
    "H1ProducerInventoryCapture",
    "H1ProducerInventoryReader",
    "H1ProducerInventoryViolation",
]
