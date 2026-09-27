"""Private installed-worker evidence source for H1 pre-request issuance.

The capability in this module is deliberately an in-process identity token.  It
cannot be reconstructed from a worker string, a fence, or persisted evidence.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import NoReturn

from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import NonSchedulerFence, NotApplicable
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_first_path_sources import H1CurrentFirstPathNativeCut
from chiplog.composition.h1_launch_enrollment import EnrolledH1EvidenceMount, InstalledH1Launch
from chiplog.composition.h1_native_member_sources import (
    H1CurrentNativeMemberSourceCut,
    H1NativeMemberSources,
)
from chiplog.composition.h1_preseal_native_source import (
    H1PresealNativeSource,
    H1PresealNativeSourceCut,
)
from chiplog.composition.r7_supervisor import R7RuntimeSupervisor
from chiplog.platform.broker import BrokerSession
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


class H1WorkerSourceCapability:
    """Nominal opaque token; only its installed owner can issue membership."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 worker source capabilities are issued only by their owner")

    def __copy__(self) -> H1WorkerSourceCapability:
        raise TypeError("H1 worker source capabilities cannot be copied")

    def __deepcopy__(self, memo: object) -> H1WorkerSourceCapability:
        raise TypeError("H1 worker source capabilities cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("H1 worker source capabilities cannot be copied")


@dataclass(frozen=True, slots=True)
class _H1VerifiedWorkerCut:
    """Inert immediate replay result for the installed E issuer."""

    run: ExecutionRunRecord
    fence: NonSchedulerFence
    worker_session_id: str
    owner_route_generation: str
    runtime_instance_id: str


@dataclass(frozen=True, slots=True)
class _IssuedWorkerSource:
    native_cap: H1CurrentNativeMemberSourceCut
    native_cut: H1CurrentFirstPathNativeCut
    engine: AuthorityBrokerRuntime
    session: BrokerSession
    verified: _H1VerifiedWorkerCut


class _H1InstalledWorkerEvidenceOwner:
    """One installed-runtime owner with a strong, identity-only token registry."""

    __slots__ = (
        "_closed",
        "_gate",
        "_issued",
        "_launch",
        "_lifetime_id",
        "_mount",
        "_native_sources",
        "_pid",
        "_runtime",
        "_supervisor",
    )

    def __init__(
        self,
        runtime: CommonCliExecutionRuntime,
        supervisor: R7RuntimeSupervisor,
        launch: InstalledH1Launch,
        mount: EnrolledH1EvidenceMount,
        native_sources: H1NativeMemberSources,
    ) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 worker evidence requires the canonical common CLI runtime")
        if type(supervisor) is not R7RuntimeSupervisor:
            raise TypeError("H1 worker evidence requires the installed runtime supervisor")
        if type(launch) is not InstalledH1Launch:
            raise TypeError("H1 worker evidence requires the installed launch")
        if type(mount) is not EnrolledH1EvidenceMount:
            raise TypeError("H1 worker evidence requires the enrolled evidence mount")
        if type(native_sources) is not H1NativeMemberSources:
            raise TypeError("H1 worker evidence requires the installed E3 native source owner")

        gate = runtime._authority_gate()
        if (
            runtime._supervisor is not supervisor
            or supervisor._authority_gate is not gate
            or mount.authority_gate is not gate
            or native_sources._runtime is not runtime
            or native_sources._first_path._gate is not gate
        ):
            raise ValueError("H1 worker evidence installed owners do not share one authority gate")
        launch.assert_current()
        mount.assert_current()

        self._runtime = runtime
        self._supervisor = supervisor
        self._launch = launch
        self._mount = mount
        self._native_sources = native_sources
        self._gate = gate
        self._pid = os.getpid()
        self._lifetime_id = secrets.token_urlsafe(32)
        self._issued: dict[int, tuple[H1WorkerSourceCapability, _IssuedWorkerSource]] = {}
        self._closed = False

    def _capture_for_pre_request(
        self, native_cap: H1CurrentNativeMemberSourceCut
    ) -> H1WorkerSourceCapability:
        if type(native_cap) is not H1CurrentNativeMemberSourceCut:
            raise ValueError("H1 worker evidence requires an issuer-owned native capability")
        with self._gate.hold():
            issued = self._current_issued(native_cap)
            cap = object.__new__(H1WorkerSourceCapability)
            self._issued[id(cap)] = (cap, issued)
            return cap

    def _replay_for_pre_request(
        self, cap: H1WorkerSourceCapability, native_cap: H1CurrentNativeMemberSourceCut
    ) -> _H1VerifiedWorkerCut:
        record = self._issued.get(id(cap)) if type(cap) is H1WorkerSourceCapability else None
        if record is None or record[0] is not cap:
            raise ValueError("H1 worker capability is not owner-issued")
        if self._closed:
            raise ValueError("H1 worker evidence owner is closed")
        if (
            type(native_cap) is not H1CurrentNativeMemberSourceCut
            or record[1].native_cap is not native_cap
        ):
            raise ValueError("H1 worker evidence native capability differs")
        with self._gate.hold():
            fresh = self._current_issued(native_cap)
            if fresh != record[1]:
                raise ValueError("H1 worker evidence source or route is stale")
            return record[1].verified

    def _revoke_all(self) -> None:
        gate = getattr(self, "_gate", None)
        if gate is None:
            self._issued.clear()
            self._closed = True
            return
        with gate.hold():
            self._issued.clear()
            self._closed = True

    def _preseal_worker(
        self, native_source: H1PresealNativeSource, native_cut: H1PresealNativeSourceCut
    ) -> dict[str, object]:
        """Return a live E worker residual for an issuer-owned preseal native cut.

        This deliberately does not accept a Run, session, fence, or worker DTO:
        those values are reread from the installed route under the shared gate.
        """
        if type(native_source) is not H1PresealNativeSource:
            raise TypeError("H1 preseal worker requires the installed native source owner")
        if type(native_cut) is not H1PresealNativeSourceCut:
            raise TypeError("H1 preseal worker requires an issuer-owned native source cut")
        if (
            native_source._runtime is not self._runtime
            or getattr(self._runtime, "_h1_preseal_native_source", None) is not native_source
        ):
            raise ValueError("H1 preseal native source belongs to another installed runtime")
        if getattr(self._runtime, "_h1_installed_worker_evidence_owner", None) is not self:
            raise ValueError("H1 preseal worker owner is not the installed owner")
        with self._gate.hold():
            verified = self._current_preseal_issued(native_source, native_cut)
            return {
                "runtime_instance_id": verified.runtime_instance_id,
                "owner_route_generation": verified.owner_route_generation,
                "worker_session_id": verified.worker_session_id,
                "owner_id": "agent_loop",
                "run_head": verified.run.head,
                "fence_kind": "NON_SCHEDULER",
            }

    def _current_issued(self, native_cap: H1CurrentNativeMemberSourceCut) -> _IssuedWorkerSource:
        if self._closed or os.getpid() != self._pid:
            raise ValueError("H1 worker evidence owner is closed or belongs to another process")
        self._gate.require_held()
        self._launch.assert_current()
        self._mount.assert_current()
        if (
            self._runtime._supervisor is not self._supervisor
            or self._supervisor._authority_gate is not self._gate
            or self._mount.authority_gate is not self._gate
            or self._native_sources._runtime is not self._runtime
            or self._native_sources._first_path._gate is not self._gate
        ):
            raise ValueError("H1 worker evidence installation is no longer canonical")

        native = self._native_sources.replay_current(native_cap)
        run = native.source.complete_ordered_run_lineage[-1]
        engine = self._supervisor.runtime()
        if self._runtime._supervisor is not self._supervisor:
            raise ValueError("H1 worker runtime supervisor changed")
        session = engine.session("agent_loop")
        worker_session_id = f"{session.broker_epoch}:{session.generation_id}:{session.session_id}"
        if (
            session.owner_id != "agent_loop"
            or session.tenant_id != run.tenant
            or self._launch._slot.tenant_id != run.tenant
            or self._mount.tenant_id != run.tenant
            or run.worker_session != worker_session_id
        ):
            raise ValueError("H1 worker session differs from selected prepared Run")
        fence = NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=worker_session_id,
            runtime_generation=session.generation_id,
        )
        return _IssuedWorkerSource(
            native_cap=native_cap,
            native_cut=native,
            engine=engine,
            session=session,
            verified=_H1VerifiedWorkerCut(
                run=run,
                fence=fence,
                worker_session_id=worker_session_id,
                owner_route_generation=session.generation_id,
                runtime_instance_id=self._lifetime_id,
            ),
        )

    def _current_preseal_issued(
        self, native_source: H1PresealNativeSource, native_cut: H1PresealNativeSourceCut
    ) -> _H1VerifiedWorkerCut:
        """Replay installed worker identity against the genuine captured preseal Run."""
        if self._closed or os.getpid() != self._pid:
            raise ValueError("H1 worker evidence owner is closed or belongs to another process")
        self._gate.require_held()
        self._launch.assert_current()
        self._mount.assert_current()
        if (
            self._runtime._supervisor is not self._supervisor
            or self._supervisor._authority_gate is not self._gate
            or self._mount.authority_gate is not self._gate
            or self._native_sources._runtime is not self._runtime
            or self._native_sources._first_path._gate is not self._gate
            or native_source._runtime is not self._runtime
            or getattr(self._runtime, "_h1_preseal_native_source", None) is not native_source
        ):
            raise ValueError("H1 preseal worker installation is no longer canonical")
        # The native source's identity registry and replay establish both the
        # selected V3 Prepare/captured Run lineage and owner-currentness.
        native_source.replay(native_cut)
        run = native_cut._preflight.captured_run
        engine = self._supervisor.runtime()
        if self._runtime._supervisor is not self._supervisor:
            raise ValueError("H1 worker runtime supervisor changed")
        session = engine.session("agent_loop")
        worker_session_id = f"{session.broker_epoch}:{session.generation_id}:{session.session_id}"
        if (
            session.owner_id != "agent_loop"
            or session.tenant_id != run.tenant
            or self._launch._slot.tenant_id != run.tenant
            or self._mount.tenant_id != run.tenant
            or run.worker_session != worker_session_id
            or native_cut._preflight.worker_session != worker_session_id
        ):
            raise ValueError("H1 preseal worker session differs from selected prepared Run")
        return _H1VerifiedWorkerCut(
            run=run,
            fence=NonSchedulerFence(
                lineage=NotApplicable(),
                physical_root=NotApplicable(),
                lease=NotApplicable(),
                clock_proof=NotApplicable(),
                run_id=run.run_id,
                run_head=run.head,
                worker_session_id=worker_session_id,
                runtime_generation=session.generation_id,
            ),
            worker_session_id=worker_session_id,
            owner_route_generation=session.generation_id,
            runtime_instance_id=self._lifetime_id,
        )


__all__ = ["H1WorkerSourceCapability"]
