"""One orchestration service shared by production and evaluation adapters."""

from __future__ import annotations

import asyncio
import base64
import json

from . import domain
from .contracts import (
    BudgetPolicy,
    Complete,
    ContextPort,
    Continue,
    DisclosureLabel,
    EndpointSelection,
    LocalPlanningReceipt,
    LoopRejected,
    LoopSnapshot,
    LoopStore,
    ModelPort,
    PlanningProposalPort,
    PromptPort,
    ProposalDisplay,
    RunRecord,
    RunView,
    VisibilityManifest,
    VisibilityMember,
    WorkerSessionPort,
)


class AgentLoop:
    def __init__(
        self,
        store: LoopStore,
        model: ModelPort,
        prompts: PromptPort,
        *,
        tenant: str,
        principal: str,
        origin: EndpointSelection,
        contour_head: str,
        policy_head: str,
        worker_session: str,
        planning: PlanningProposalPort | None = None,
        workspace: ContextPort | None = None,
        session: WorkerSessionPort | None = None,
    ) -> None:
        self._store, self._model, self._prompts = store, model, prompts
        self._tenant, self._principal, self._origin = tenant, principal, origin
        self._contour, self._policy, self._worker = contour_head, policy_head, worker_session
        self._planning = planning
        self._workspace = workspace
        self._session = session
        self._owner_session = session.current_worker() if session is not None else worker_session
        self._lane = asyncio.Lock()

    def _live(self) -> None:
        if self._session is not None and self._session.current_worker() != self._owner_session:
            raise LoopRejected("stale loop worker generation; open a fresh registered instance")

    async def display(self, proposal_id: str) -> ProposalDisplay:
        self._live()
        if self._planning is None:
            raise LoopRejected("planning proposal port unavailable")
        return await self._planning.display(proposal_id)

    async def adopt(
        self, peer: str, display_id: str, digest: str, adoption_act_id: str
    ) -> LocalPlanningReceipt:
        async with self._lane:
            return await self._adopt(peer, display_id, digest, adoption_act_id)

    async def _adopt(
        self, peer: str, display_id: str, digest: str, adoption_act_id: str
    ) -> LocalPlanningReceipt:
        self._live()
        if self._planning is None:
            raise LoopRejected("planning adoption port unavailable")
        receipt = await self._planning.adopt(peer, display_id, digest, adoption_act_id)
        runs = {record.run_id: record for record in self._store.snapshot().records}
        matches = [
            run
            for run in runs.values()
            if any(
                outcome.proposal_id == receipt.proposal_id
                for turn in run.turns
                for outcome in (turn.sealed_calls or ())
            )
        ]
        if len(matches) != 1:
            raise LoopRejected("receipt has no unique original Run")
        run = matches[0]
        existing = [
            value for value in run.planning_receipts if value.evidence_id == receipt.evidence_id
        ]
        if existing:
            if existing != [receipt]:
                raise LoopRejected("receipt identity changed")
        else:
            await self._publish(run, domain.observe_receipt(run, receipt))
        return receipt

    def record(self, run_id: str) -> RunRecord:
        self._live()
        matches = [r for r in self._store.snapshot().records if r.run_id == run_id]
        if not matches:
            raise LoopRejected("Run is absent")
        run = matches[-1]
        if (
            run.tenant != self._tenant
            or run.principal != self._principal
            or run.origin != self._origin
            or run.contour_head != self._contour
            or run.policy_head != self._policy
            or run.worker_session != self._worker
        ):
            raise LoopRejected("current authenticated binding differs from Run")
        return run

    def status(self, run_id: str) -> RunView:
        run = self.record(run_id)
        return RunView(
            run_id=run.run_id,
            state=run.state,
            head=run.head,
            turn_ordinal=len(run.turns),
            turn_head=run.turns[-1].head if run.turns else None,
        )

    async def create(self, run_id: str, prompt: str, policy: BudgetPolicy) -> RunView:
        async with self._lane:
            return await self._create(run_id, prompt, policy)

    async def _create(self, run_id: str, prompt: str, policy: BudgetPolicy) -> RunView:
        self._live()
        if not run_id or not prompt:
            raise LoopRejected("empty Run identity or prompt")
        snapshot = self._store.snapshot()
        run = RunRecord(
            tenant=self._tenant,
            principal=self._principal,
            run_id=run_id,
            state="CREATED",
            head="pending",
            predecessor=None,
            prompt=prompt,
            policy=policy,
            origin=self._origin,
            contour_head=self._contour,
            policy_head=self._policy,
            worker_session=self._worker,
            event="RunCreated",
        )
        run = run.model_copy(update={"head": "loop:" + run.digest()})
        existing = [record for record in snapshot.records if record.run_id == run_id]
        if existing:
            if existing[0] != run:
                raise LoopRejected("Run identity reuse conflicts")
        else:
            if self._workspace is not None:
                await self._workspace.ingest(run_id, prompt)
                snapshot = self._store.snapshot()
            await self._store.publish(run, snapshot, lambda _: self._live())
        return self.status(run_id)

    async def _publish(self, previous: RunRecord, result: RunRecord) -> None:
        self._live()
        snapshot = self._store.snapshot()
        records = [r for r in snapshot.records if r.run_id == previous.run_id]
        if not records or records[-1] != previous:
            raise LoopRejected("stale exact Run head")

        def validate(current: LoopSnapshot) -> None:
            self._live()
            # The appender invokes the owner predicate under its writer lock.
            # Re-enumerate authoritative lineage records rather than accepting
            # the earlier TurnStarted or an aggregate's cached success bit.
            lineage = [record for record in current.records if record.run_id == previous.run_id]
            if not lineage or lineage[-1] != previous:
                raise LoopRejected("Run changed inside writer transaction")
            observed = lineage[-1]
            if result.event == "CompleteAcceptance":
                attempt = domain.current_attempt(observed, "RESPONSE_CAPTURED")
                if attempt.response_base64 is None:
                    raise LoopRejected("missing captured response")
                response = Complete.model_validate_json(
                    base64.b64decode(attempt.response_base64, validate=True)
                )
                if domain.complete(observed, response) != result:
                    raise LoopRejected("completion decision changed inside writer transaction")
            elif result.event in ("TurnStarted", "RunBudgetSuspended"):
                if domain.start_turn(observed) != result:
                    raise LoopRejected("continuation changed inside writer transaction")

        await self._store.publish(result, snapshot, validate)

    def _expected(self, run_id: str, expected_head: str) -> RunRecord:
        run = self.record(run_id)
        if run.head != expected_head:
            raise LoopRejected("stale exact Run head")
        return run

    async def activate(self, run_id: str, expected_head: str) -> RunView:
        run = self._expected(run_id, expected_head)
        await self._publish(run, domain.transition(run, "ACTIVE"))
        return self.status(run_id)

    async def step(self, run_id: str, expected_head: str) -> RunView:
        async with self._lane:
            return await self._step(run_id, expected_head)

    async def _step(self, run_id: str, expected_head: str) -> RunView:
        run = self._expected(run_id, expected_head)
        started = domain.start_turn(run)
        await self._publish(run, started)
        if started.state == "SUSPENDED":
            return self.status(run_id)
        workspace = await self._workspace.context(started) if self._workspace is not None else None
        content = json.dumps(
            {
                "prompt": started.prompt,
                "workspace": workspace.content if workspace is not None else None,
                "planning_receipts": [
                    receipt.model_dump(mode="json") for receipt in started.planning_receipts
                ],
                "prior_turns": [
                    {
                        "turn_id": turn.turn_id,
                        "response_base64": turn.attempts[turn.selector].response_base64,
                        "rejection": turn.attempts[turn.selector].rejection,
                        "visibility": turn.attempts[turn.selector].manifest.digest(),
                        "results": [
                            outcome.model_dump(mode="json") for outcome in (turn.sealed_calls or ())
                        ],
                    }
                    for turn in started.turns[:-1]
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        artifact = await self._prompts.render(content)
        restricted = domain.context_label(started)
        members = (
            *((workspace,) if workspace is not None else ()),
            VisibilityMember(
                record_id=run_id + "/context/" + str(len(started.turns)),
                revision_head=started.head,
                content=content,
                provenance_head=started.head,
                label_head=started.contour_head,
                label=restricted,
                producer="agent_loop",
                surface="context",
            ),
            VisibilityMember(
                record_id=artifact.prompt_id + "/" + artifact.version,
                revision_head=artifact.content_hash,
                content=artifact.rendered,
                provenance_head=artifact.content_hash,
                label_head=started.policy_head,
                label=restricted,
                producer="promptstrings",
                surface="prompt",
            ),
            VisibilityMember(
                record_id="turn-schema/" + str(len(started.turns)),
                revision_head=artifact.digest(),
                content=artifact.response_schema_json,
                provenance_head=artifact.digest(),
                label_head=started.policy_head,
                label=DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
                producer="agent_loop",
                surface="schema",
            ),
        )
        accumulated = domain.accumulate(started, members)
        await self._publish(started, accumulated)
        manifest = VisibilityManifest(
            tenant=run.tenant,
            principal=run.principal,
            contour_head=run.contour_head,
            run_id=run_id,
            turn_id=accumulated.turns[-1].turn_id,
            generation=0,
            worker_session=self._owner_session,
            members=members,
            joined_label=domain.join_labels(tuple(member.label for member in members)),
            artifact=artifact,
        )
        prepared = domain.prepare(accumulated, manifest)
        await self._publish(accumulated, prepared)
        emitted = domain.emit(prepared)
        await self._publish(prepared, emitted)
        # No request bytes reach even the hermetic leaf before the durable emission CAS.
        raw, receipt = await self._model.invoke(emitted.turns[-1].attempts[-1])
        captured = domain.capture(emitted, raw, receipt)
        await self._publish(emitted, captured)
        try:
            response = self._prompts.parse(raw, artifact)
            accepted = (
                domain.accept_tools(captured, response)
                if isinstance(response, Continue)
                else domain.complete(captured, response)
            )
        except ValueError as error:
            await self._publish(captured, domain.reject(captured, str(error)))
            return self.status(run_id)
        await self._publish(captured, accepted)
        if isinstance(response, Continue):
            for call in response.tool_calls:
                current = self.record(run_id)
                # Proposal text is never a command or evidence of provider success.
                result = json.dumps(
                    {"kind": "PROPOSAL_ONLY", "tool": call.tool, "text": call.text}, sort_keys=True
                )
                await self._publish(current, domain.terminal_tool(current, call.call_id, result))
        return self.status(run_id)
