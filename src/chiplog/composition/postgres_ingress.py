"""Native exact-byte inbox admission through registered transport issuers.

The issuer callback is installed only in trusted transport leaves by composition.
Its key is stable operator configuration, never a value supplied by a request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Literal

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.delivery_contracts import (
    DeliveryDTO,
    ExactHead,
    Identity,
    OriginSelection,
)
from chiplog.capabilities.agent_loop.ingress_client_contracts import (
    NativeIngressIdentityV1,
    NativeIngressReceiptV1,
    NativeIngressSubmissionV1,
    NativeTransportWitnessV1,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.platform.postgres import PostgresIntegrityError, Record, Snapshot
from chiplog.platform.telegram_candidates import project_retained_telegram


class RegisteredIngressSource(DeliveryDTO):
    """Composition-selected binding; no field is adopted from message authority."""

    tenant_id: Identity
    channel: Literal["CLI", "TELEGRAM"]
    source_id: Identity
    principal_id: Identity
    issuer_id: Identity
    binding: ExactHead
    origin: OriginSelection
    transport_subject: Identity


class NativeInboxRecord(DeliveryDTO):
    schema_id: Literal["chiplog.agent-loop.native-inbox.v1"] = "chiplog.agent-loop.native-inbox.v1"
    inbox_id: Identity
    run_id: Identity
    source: RegisteredIngressSource
    submission: NativeIngressSubmissionV1
    policy: BudgetPolicy


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def subject(*parts: str | int) -> str:
    """Unambiguous registered transport tuple, independent of payload aliases."""
    return canonical(parts).decode()


def ingress_prompt(submission: NativeIngressSubmissionV1) -> str:
    if submission.identity.channel == "CLI":
        return submission.raw_bytes.decode("utf-8")
    projected = project_retained_telegram(
        tenant_id=submission.identity.tenant_id,
        candidate_id="native-ingress",
        raw_bytes=submission.raw_bytes,
    )
    if projected.candidate_fields.kind != "PRIVATE_TEXT":
        raise LoopRejected("unsupported Telegram input")
    return projected.candidate_fields.text


type IssueIngress = Callable[[str, bytes, BudgetPolicy, str], NativeIngressSubmissionV1]


class PostgresIngressService:
    def __init__(
        self,
        runtime: PostgresExecutionRuntime,
        sources: tuple[RegisteredIngressSource, ...],
        *,
        issuer_key: bytes,
    ) -> None:
        if not issuer_key or not sources:
            raise ValueError("registered ingress requires sources and a stable issuer key")
        self.runtime, self._key = runtime, issuer_key
        self.sources = {source.source_id: source for source in sources}
        if len(self.sources) != len(sources):
            raise ValueError("duplicate registered ingress source")
        for source in sources:
            if (
                source.tenant_id != runtime.tenant_id
                or source.principal_id != runtime.principal_id
                or source.origin.ingress_binding != source.binding
            ):
                raise ValueError("registered ingress source scope differs from runtime")

    def _source(self, identity: NativeIngressIdentityV1) -> RegisteredIngressSource:
        source = self.sources.get(identity.registered_source_id)
        if source is None or (identity.tenant_id, identity.channel) != (
            source.tenant_id,
            source.channel,
        ):
            raise PermissionError("ingress source scope is not registered")
        return source

    def _proof(
        self,
        source: RegisteredIngressSource,
        identity: NativeIngressIdentityV1,
        raw: bytes,
        policy: BudgetPolicy,
    ) -> bytes:
        body = canonical(
            {
                "schema": "chiplog.native-ingress.transport-proof.v1",
                "source": source.model_dump(mode="json"),
                "identity": identity.model_dump(mode="json"),
                "raw_digest": digest(raw),
                "policy_digest": policy.digest(),
            }
        )
        return hmac.digest(self._key, body, "sha256")

    def issuer(self, source_id: str) -> IssueIngress:
        """Trusted composition wiring only; never expose this callback to a client.

        The leaf calls it after observing and checking the real transport peer.
        A public NativeIngressPort consumer receives submit/lookup, not this issuer.
        """
        source = self.sources[source_id]

        def issue(
            replay_id: str, raw: bytes, policy: BudgetPolicy, observed: str
        ) -> NativeIngressSubmissionV1:
            if observed != source.transport_subject or self.sources.get(source_id) != source:
                raise PermissionError("transport observation differs from current source")
            identity = NativeIngressIdentityV1(
                tenant_id=source.tenant_id,
                channel=source.channel,
                registered_source_id=source.source_id,
                replay_id=replay_id,
            )
            return NativeIngressSubmissionV1(
                identity=identity,
                raw_bytes=raw,
                witness=NativeTransportWitnessV1(
                    issuer_id=source.issuer_id,
                    source_binding=source.binding,
                    evidence=self._proof(source, identity, raw, policy),
                ),
            )

        return issue

    def _authenticate(
        self,
        submission: NativeIngressSubmissionV1,
        policy: BudgetPolicy,
        source: RegisteredIngressSource,
    ) -> None:
        witness = submission.witness
        if (
            witness.issuer_id != source.issuer_id
            or witness.source_binding != source.binding
            or not hmac.compare_digest(
                witness.evidence,
                self._proof(source, submission.identity, submission.raw_bytes, policy),
            )
        ):
            raise PermissionError("ingress transport witness is not authenticated")

    def _records(self, snapshot: Snapshot) -> tuple[tuple[NativeInboxRecord, Record, int], ...]:
        result = []
        for batch in snapshot.batches:
            for record in batch.records:
                if not record.schema_id.startswith("chiplog.agent-loop.native-inbox"):
                    continue
                try:
                    inbox = NativeInboxRecord.model_validate_json(record.canonical_bytes)
                    if (
                        record.schema_id != "chiplog.agent-loop.native-inbox.v1"
                        or inbox.canonical_bytes() != record.canonical_bytes
                        or record.owner != "agent_loop"
                        or record.record_id != inbox.inbox_id
                        or inbox.source.tenant_id != self.runtime.tenant_id
                        or inbox.source.principal_id != self.runtime.principal_id
                        or (
                            inbox.submission.identity.tenant_id,
                            inbox.submission.identity.channel,
                            inbox.submission.identity.registered_source_id,
                        )
                        != (inbox.source.tenant_id, inbox.source.channel, inbox.source.source_id)
                    ):
                        raise ValueError("inbox identity or canonical record differs")
                    self._authenticate(inbox.submission, inbox.policy, inbox.source)
                    suffix = digest(inbox.submission.identity.canonical_bytes())
                    runs = [
                        value
                        for value in self.runtime._runs(snapshot)
                        if value.run_id == inbox.run_id
                    ]
                    if (
                        not runs
                        or inbox.inbox_id != "inbox:" + suffix
                        or inbox.run_id != "ingress-run:" + suffix
                        or runs[0].origin != inbox.source.origin
                        or runs[0].prompt != ingress_prompt(inbox.submission)
                        or runs[0].policy != inbox.policy
                        or not any(item.record_id == runs[0].head for item in batch.records)
                    ):
                        raise ValueError("inbox lacks its same-publication native Run")
                    result.append((inbox, record, batch.sequence))
                except (ValueError, PermissionError) as error:
                    raise PostgresIntegrityError(
                        f"operation=ingress.read tenant={self.runtime.tenant_id} "
                        f"record_id={record.record_id}"
                    ) from error
        return tuple(result)

    def _lookup(
        self, snapshot: Snapshot, identity: NativeIngressIdentityV1
    ) -> NativeIngressReceiptV1 | None:
        source = self._source(identity)
        matches = [row for row in self._records(snapshot) if row[0].submission.identity == identity]
        if len(matches) > 1:
            raise PostgresIntegrityError(
                f"operation=ingress.lookup tenant={identity.tenant_id} "
                f"record_id={identity.replay_id}"
            )
        if not matches:
            return None
        inbox, record, sequence = matches[0]
        if inbox.source != source:
            raise PermissionError("selected ingress source binding changed")
        return NativeIngressReceiptV1(
            identity=identity,
            inbox_id=inbox.inbox_id,
            run_id=inbox.run_id,
            raw_bytes=inbox.submission.raw_bytes,
            origin=inbox.source.origin,
            selected_record=ExactHead(
                identity=record.record_id,
                head=record.record_id,
                fingerprint=digest(record.canonical_bytes),
            ),
            commit_sequence=sequence,
        )

    def lookup(self, peer: str, identity: NativeIngressIdentityV1) -> NativeIngressReceiptV1 | None:
        return self._lookup(self.runtime._snapshot_for_peer(peer), identity)

    def receipt_for_inbox(self, peer: str, inbox_id: str) -> NativeIngressReceiptV1:
        """Resolve selected custody for the shared driver; no caller DTO is adopted."""
        snapshot = self.runtime._snapshot_for_peer(peer)
        matches = [row[0] for row in self._records(snapshot) if row[0].inbox_id == inbox_id]
        if len(matches) != 1:
            raise LoopRejected("exact admitted inbox is absent")
        receipt = self._lookup(snapshot, matches[0].submission.identity)
        if receipt is None:
            raise LoopRejected("admitted inbox has no selected custody")
        return receipt

    async def submit(
        self, peer: str, submission: NativeIngressSubmissionV1, policy: BudgetPolicy
    ) -> NativeIngressReceiptV1:
        async with self.runtime._lane:
            snapshot = self.runtime._snapshot_for_peer(peer)
            source = self._source(submission.identity)
            self._authenticate(submission, policy, source)
            previous = self._lookup(snapshot, submission.identity)
            if previous is not None:
                inbox = next(
                    row[0]
                    for row in self._records(snapshot)
                    if row[0].inbox_id == previous.inbox_id
                )
                if inbox.submission.raw_bytes != submission.raw_bytes or inbox.policy != policy:
                    raise LoopRejected("ingress replay bytes or policy changed")
                return previous
            suffix = digest(submission.identity.canonical_bytes())
            inbox_id, run_id = "inbox:" + suffix, "ingress-run:" + suffix
            prompt = ingress_prompt(submission)
            _, run_records = self.runtime.prepare_execution_records(
                snapshot,
                run_id=run_id,
                prompt=prompt,
                policy=policy,
                origin=source.origin,
                worker=self.runtime._worker,
            )
            inbox = NativeInboxRecord(
                inbox_id=inbox_id,
                run_id=run_id,
                source=source,
                submission=submission,
                policy=policy,
            )

            def guard(current: Snapshot) -> None:
                if self._source(submission.identity) != source:
                    raise PermissionError("ingress source changed before publication")
                self._authenticate(submission, policy, source)
                if self._lookup(current, submission.identity) is not None:
                    raise LoopRejected("ingress already admitted")

            await self.runtime.publish_execution_batch(
                peer,
                "ingress.admit",
                suffix,
                snapshot.head,
                (
                    Record(inbox_id, "agent_loop", inbox.schema_id, inbox.canonical_bytes()),
                    *run_records,
                ),
                guard,
            )
            receipt = self.lookup(peer, submission.identity)
            if receipt is None:
                raise LoopRejected("ingress publication has no selected custody")
            return receipt
