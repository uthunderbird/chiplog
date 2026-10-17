"""One native execution driver shared by admitted channels and scheduled Runs."""

from typing import Protocol

from chiplog.capabilities.agent_loop.contracts import LoopHold, LoopRejected
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionRun
from chiplog.capabilities.agent_loop.ingress_client_contracts import NativeIngressReceiptV1
from chiplog.composition.postgres_execution import PostgresExecutionRuntime


class SelectedIngress(Protocol):
    def receipt_for_inbox(self, peer: str, inbox_id: str) -> NativeIngressReceiptV1: ...


class PostgresApplicationDriver:
    def __init__(
        self, runtime: PostgresExecutionRuntime, ingress: SelectedIngress | None = None
    ) -> None:
        self.runtime, self.ingress = runtime, ingress

    async def drive(self, peer: str, inbox_id: str) -> ExecutionRun:
        self.runtime._snapshot_for_peer(peer)
        if self.ingress is None:
            raise LoopRejected("application driver has no registered ingress")
        receipt = self.ingress.receipt_for_inbox(peer, inbox_id)
        return await self.drive_run(peer, receipt.run_id)

    async def drive_run(self, peer: str, run_id: str) -> ExecutionRun:
        """Advance native transitions; user adoption and recovery remain explicit.

        Delivery intents use the existing separately gated SEND lifecycle.
        A model response never supplies authority to accept a proposed effect.
        """
        while True:
            snapshot = self.runtime._snapshot_for_peer(peer)
            runs = [run for run in self.runtime._runs(snapshot) if run.run_id == run_id]
            if not runs:
                raise LoopRejected("application Run is absent")
            run = runs[-1]
            if run.state in {"SUCCEEDED", "ABORTED", "CANCELLED", "SUPERSEDED"}:
                return run
            if run.state == "CREATED":
                await self.runtime.begin_execution(peer, run_id, run.head)
                continue
            if run.state != "ACTIVE" or not run.turns:
                raise LoopHold("application Run requires explicit recovery")
            turn = run.turns[-1]
            if run.event == "ModelCompletionPrepared":
                await self.runtime.accept_completion(peer, run_id, run.head)
            elif turn.state == "PREPARING" and not turn.attempts:
                await self.runtime.capture_execution(peer, run_id, run.head)
            elif turn.state == "RESPONSE_AVAILABLE" and turn.response_seal is None:
                await self.runtime.seal_execution(peer, run_id, run.head)
            elif turn.state == "ACCEPTED" and turn.response_seal is not None:
                for call in self.runtime._initialized(snapshot):
                    if (
                        call.call.original.original_turn_id != turn.turn_id
                        or call.call.original.original_run_id != run_id
                    ):
                        continue
                    state = self.runtime.call_state(call.original_call_id)
                    if state.state in {"TERMINAL", "CANCELLED"}:
                        continue
                    if call.call.classification != "READ_ONLY":
                        raise LoopHold(
                            "application call requires explicit user adoption or outcome"
                        )
                    await self.runtime.execute_readonly_call(peer, call.original_call_id)
                current = self.runtime.execution_record(run_id)
                await self.runtime.continue_execution(peer, run_id, current.head)
            else:
                raise LoopHold("application model attempt requires explicit recovery")
