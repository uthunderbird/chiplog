"""R7 broker-owned lifecycle for isolated, authority-empty owner processes."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import multiprocessing
import os
import socket
import struct
import sys
import tempfile
import threading
from asyncio import to_thread
from base64 import b64decode, b64encode
from collections.abc import Callable
from dataclasses import asdict, dataclass
from multiprocessing.process import BaseProcess
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast

from dishka import Provider, Scope, make_container, provide

from chiplog.architecture.r7_runtime import RuntimeAssemblyManifest, verify_runtime_manifest
from chiplog.composition.r7 import OwnerGeneration, RuntimeGraphGeneration
from chiplog.platform.broker import (
    BrokerSession,
    PublicPortCall,
    PublicPortFailure,
    PublicPortRejected,
    PublicPortResult,
    PublicPortSuccess,
)

_MAX_FRAME_BYTES = 8 * 1024 * 1024


class OwnerProcessFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class OwnerProcessIdentity:
    tenant_id: str
    broker_epoch: int
    owner_id: str
    generation_id: str
    session_id: str
    capability_ids: tuple[str, ...]


@dataclass(frozen=True)
class OwnerProcessAttestation:
    identity: OwnerProcessIdentity
    process_id: int
    parent_process_id: int
    dishka_scope: str
    provider_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    factory_ids: tuple[str, ...]
    loaded_policy_modules: tuple[str, ...]
    routes: tuple[tuple[str, str, str, str, str], ...]


class _OwnerService:
    def __init__(
        self,
        identity: OwnerProcessIdentity,
        handler: Callable[[str, bytes], dict[str, object]],
    ) -> None:
        self.identity = identity
        self._handler = handler

    def attest(self) -> dict[str, object]:
        handler_module = sys.modules[self._handler.__module__]
        routes = cast(tuple[tuple[str, str, str, str, str], ...], handler_module.__dict__["ROUTES"])
        filesystem_denied = False
        network_denied = False
        process_spawn_denied = False
        try:
            Path("/etc/hosts").read_bytes()
        except PermissionError:
            filesystem_denied = True
        try:
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except PermissionError:
            network_denied = True
        try:
            os.fork()
        except PermissionError:
            process_spawn_denied = True
        return {
            "authority_empty_profile": "python-audit-deny-raw-v1",
            "capability_ids": tuple(route[0] for route in routes),
            "dishka_scope": "APP",
            "generation_id": self.identity.generation_id,
            "owner_id": self.identity.owner_id,
            "parent_process_id": os.getppid(),
            "process_id": os.getpid(),
            "session_id": self.identity.session_id,
            "tenant_id": self.identity.tenant_id,
            "environment_empty": not os.environ,
            "filesystem_denied": filesystem_denied,
            "network_denied": network_denied,
            "process_spawn_denied": process_spawn_denied,
            "provider_ids": ("chiplog.platform.r7_runtime:_OwnerProvider",),
            "target_ids": (f"{self._handler.__module__}:dispatch",),
            "factory_ids": ("chiplog.platform.r7_runtime:_OwnerProvider.service",),
            "loaded_policy_modules": tuple(
                sorted(
                    name
                    for name in sys.modules
                    if name.startswith("chiplog.capabilities.")
                    and (name.endswith("_process") and name.rsplit(".", 1)[-1].startswith("_"))
                )
            ),
            "routes": routes,
        }

    def dispatch(self, request: dict[str, object]) -> dict[str, object]:
        target = request.get("target")
        expected_target = {
            "broker_epoch": self.identity.broker_epoch,
            "generation_id": self.identity.generation_id,
            "owner_id": self.identity.owner_id,
            "session_id": self.identity.session_id,
            "tenant_id": self.identity.tenant_id,
        }
        if target != expected_target:
            return {"failure": "STALE_SESSION", "reason": "callee identity mismatch"}
        operation = request.get("operation")
        if operation not in self.identity.capability_ids:
            return {"failure": "PROTOCOL_REJECTED", "reason": "operation is not registered"}
        return self._handler(str(operation), b64decode(str(request["payload"])))


class _OwnerProvider(Provider):
    def __init__(
        self,
        identity: OwnerProcessIdentity,
        handler: Callable[[str, bytes], dict[str, object]],
    ) -> None:
        super().__init__()
        self._identity = identity
        self._handler = handler

    @provide(scope=Scope.APP)
    def service(self) -> _OwnerService:
        return _OwnerService(self._identity, self._handler)


def _owner_module(identity: OwnerProcessIdentity) -> str:
    if identity.owner_id == "deployment_trust":
        legacy = (
            "deployment_trust.authenticate",
            "deployment_trust.bootstrap",
            "deployment_trust.revalidate",
            "deployment_trust.runtime_admission",
        )
        if identity.capability_ids == legacy:
            return "chiplog.capabilities.deployment_trust._r7_process"
        if identity.capability_ids == tuple(
            sorted(
                (
                    *legacy,
                    "deployment_trust.normalize_telegram_candidate",
                )
            )
        ):
            return "chiplog.capabilities.deployment_trust._r17_process"
        if identity.capability_ids == tuple(
            sorted(
                (
                    *legacy,
                    "deployment_trust.normalize_telegram_candidate",
                    "deployment_trust.authenticate_cli_custody",
                )
            )
        ):
            return "chiplog.capabilities.deployment_trust._cli_custody_process"
        raise OwnerProcessFailure("unknown deployment-trust capability partition")
    if identity.owner_id == "agent_loop":
        if identity.capability_ids == ("agent_loop.validate_transition",):
            return "chiplog.capabilities.agent_loop._r13_process"
        if identity.capability_ids == (
            "agent_loop.prepare_delivery_completion",
            "agent_loop.validate_delivery_completion",
            "agent_loop.validate_transition",
            "scheduler.prepare_configuration",
            "scheduler.prepare_interval",
            "scheduler.prepare_lease",
            "scheduler.prepare_rollover",
        ):
            return "chiplog.capabilities.agent_loop._r14_process"
        if identity.capability_ids == (
            "agent_loop.prepare_consequential_acceptance",
            "agent_loop.prepare_delivery_completion",
            "agent_loop.prepare_pre_accept_cancellation",
            "agent_loop.validate_delivery_completion",
            "agent_loop.validate_transition",
            "scheduler.prepare_configuration",
            "scheduler.prepare_interval",
            "scheduler.prepare_lease",
            "scheduler.prepare_rollover",
        ):
            return "chiplog.capabilities.agent_loop._r14_calls_process"
        if identity.capability_ids == (
            "agent_loop.prepare_captured_fan_out",
            "agent_loop.prepare_consequential_acceptance",
            "agent_loop.prepare_delivery_completion",
            "agent_loop.prepare_pre_accept_cancellation",
            "agent_loop.validate_delivery_completion",
            "agent_loop.validate_transition",
            "scheduler.prepare_configuration",
            "scheduler.prepare_interval",
            "scheduler.prepare_lease",
            "scheduler.prepare_rollover",
        ):
            return "chiplog.capabilities.agent_loop._r14_fanout_process"
        raise OwnerProcessFailure("unknown agent-loop capability partition")
    if identity.owner_id == "effects":
        if identity.capability_ids == ("effects.prepare_transition",):
            return "chiplog.capabilities.effects._process"
        if identity.capability_ids == ("effects.prepare_denial", "effects.prepare_transition"):
            return "chiplog.capabilities.effects._r16_process"
        if identity.capability_ids == (
            "effects.evaluate_dispatch_mandate_v2",
            "effects.prepare_denial",
            "effects.prepare_dispatch_v2",
            "effects.prepare_transition",
        ):
            return "chiplog.capabilities.effects._dispatch_process"
        raise OwnerProcessFailure("unknown effects capability partition")
    if identity.owner_id == "planning" and identity.capability_ids == (
        "planning.r8_create_intention_line",
    ):
        return "chiplog.capabilities.planning._r8_process"
    return {
        "deployment_trust": "chiplog.capabilities.deployment_trust._r7_process",
        "planning": "chiplog.capabilities.planning._r7_process",
        "projections": "chiplog.capabilities.projections._r7_process",
    }[identity.owner_id]


def _owner_module_closure(identity: OwnerProcessIdentity) -> tuple[str, ...]:
    module = _owner_module(identity)
    if module == "chiplog.capabilities.agent_loop._r14_fanout_process":
        return (
            "chiplog.capabilities.agent_loop._call_process",
            "chiplog.capabilities.agent_loop._delivery_process",
            "chiplog.capabilities.agent_loop._fan_out_process",
            "chiplog.capabilities.agent_loop._r13_process",
            "chiplog.capabilities.agent_loop._r14_calls_process",
            "chiplog.capabilities.agent_loop._r14_fanout_process",
            "chiplog.capabilities.agent_loop._r14_process",
            "chiplog.capabilities.agent_loop._scheduler_process",
        )
    if module == "chiplog.capabilities.agent_loop._r14_calls_process":
        return (
            "chiplog.capabilities.agent_loop._call_process",
            "chiplog.capabilities.agent_loop._delivery_process",
            "chiplog.capabilities.agent_loop._r13_process",
            "chiplog.capabilities.agent_loop._r14_calls_process",
            "chiplog.capabilities.agent_loop._r14_process",
            "chiplog.capabilities.agent_loop._scheduler_process",
        )
    if module == "chiplog.capabilities.effects._dispatch_process":
        return (
            "chiplog.capabilities.effects._dispatch_process",
            "chiplog.capabilities.effects._process",
            "chiplog.capabilities.effects._r16_process",
        )
    if module == "chiplog.capabilities.effects._r16_process":
        return (
            "chiplog.capabilities.effects._process",
            "chiplog.capabilities.effects._r16_process",
        )
    if module == "chiplog.capabilities.deployment_trust._cli_custody_process":
        return (
            "chiplog.capabilities.deployment_trust._cli_custody_process",
            "chiplog.capabilities.deployment_trust._ingress_process",
            "chiplog.capabilities.deployment_trust._r17_process",
            "chiplog.capabilities.deployment_trust._r7_process",
        )
    if module == "chiplog.capabilities.deployment_trust._r17_process":
        return (
            "chiplog.capabilities.deployment_trust._ingress_process",
            "chiplog.capabilities.deployment_trust._r17_process",
            "chiplog.capabilities.deployment_trust._r7_process",
        )
    if module == "chiplog.capabilities.agent_loop._r14_process":
        return (
            "chiplog.capabilities.agent_loop._delivery_process",
            "chiplog.capabilities.agent_loop._r13_process",
            "chiplog.capabilities.agent_loop._r14_process",
            "chiplog.capabilities.agent_loop._scheduler_process",
        )
    return (module,)


def _load_owner_handler(
    identity: OwnerProcessIdentity,
) -> Callable[[str, bytes], dict[str, object]]:
    module = importlib.import_module(_owner_module(identity))
    return cast(Callable[[str, bytes], dict[str, object]], module.dispatch)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _send_frame(connection: socket.socket, secret: bytes, payload: bytes) -> None:
    if len(payload) > _MAX_FRAME_BYTES:
        raise OwnerProcessFailure("IPC frame exceeds the registered bound")
    signature = hmac.digest(secret, payload, "sha256")
    connection.sendall(struct.pack("!I", len(payload)) + signature + payload)


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise OwnerProcessFailure("IPC peer closed an incomplete frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _receive_frame(connection: socket.socket, secret: bytes) -> bytes:
    size = struct.unpack("!I", _receive_exact(connection, 4))[0]
    if size > _MAX_FRAME_BYTES:
        raise OwnerProcessFailure("IPC frame exceeds the registered bound")
    signature = _receive_exact(connection, hashlib.sha256().digest_size)
    payload = _receive_exact(connection, size)
    if not hmac.compare_digest(signature, hmac.digest(secret, payload, "sha256")):
        raise OwnerProcessFailure("IPC authentication failed")
    return payload


def _verify_local_peer(connection: socket.socket, expected_pid: int) -> None:
    if hasattr(socket, "LOCAL_PEERCRED"):
        raw = connection.getsockopt(0, socket.LOCAL_PEERCRED, 80)
        _, peer_uid = struct.unpack("@II", raw[:8])
        peer_pid = struct.unpack("@i", connection.getsockopt(0, 2, 4))[0]
    elif hasattr(socket, "SO_PEERCRED"):
        peer_pid, peer_uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
    else:
        raise OwnerProcessFailure("Unix peer credentials are unavailable")
    if peer_uid != os.getuid() or peer_pid != expected_pid:
        raise OwnerProcessFailure("Unix peer credential identity mismatch")


def _owner_process_main(
    socket_path: str,
    secret: bytes,
    identity: OwnerProcessIdentity,
    ready: Any,
) -> None:
    handler = _load_owner_handler(identity)
    with (
        make_container(_OwnerProvider(identity, handler)) as container,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
    ):
        listener.bind(socket_path)
        os.chmod(socket_path, 0o600)
        listener.listen(1)
        ready.set()
        connection, _ = listener.accept()
        listener.close()
        with connection:
            _verify_local_peer(connection, os.getppid())
            os.environ.clear()

            def deny_raw_authority(event: str, _arguments: tuple[object, ...]) -> None:
                if (
                    event == "open"
                    or event == "socket.__new__"
                    or event.startswith(
                        (
                            "subprocess.",
                            "os.system",
                            "os.fork",
                            "os.posix_spawn",
                            "os.spawn",
                            "pty.spawn",
                            "ctypes.dlopen",
                        )
                    )
                ):
                    raise PermissionError("owner process raw authority is denied")

            sys.addaudithook(deny_raw_authority)
            while True:
                request = json.loads(_receive_frame(connection, secret))
                if request == {"operation": "runtime.shutdown"}:
                    _send_frame(connection, secret, _canonical({"status": "closed"}))
                    break
                service = container.get(_OwnerService)
                if request == {"operation": "runtime.attest"}:
                    response = service.attest()
                elif isinstance(request, dict):
                    response = service.dispatch(request)
                else:
                    response = {"failure": "PROTOCOL_REJECTED", "reason": "invalid request"}
                _send_frame(connection, secret, _canonical(response))


class AuthorityBrokerRuntime:
    """Own owner-process handles privately and expose only inert attestations."""

    def __init__(
        self,
        tenant_id: str,
        broker_epoch: int,
        generation_id: str,
        manifest: RuntimeAssemblyManifest,
        session_secret: bytes,
        realized_leaves: dict[str, object] | None = None,
    ) -> None:
        verify_runtime_manifest(manifest)
        if broker_epoch <= 0 or not session_secret:
            raise ValueError("broker epoch and session secret must be present")
        self._tenant_id = tenant_id
        self._broker_epoch = broker_epoch
        self._generation_id = generation_id
        self._manifest = manifest
        self._realized_leaves = self._realize_leaves(realized_leaves or {})
        self._clock: Any = self._realized_leaves["clock"]
        self._session_secret = session_secret
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._processes: dict[str, BaseProcess] = {}
        self._connections: dict[str, socket.socket] = {}
        self._identities: dict[str, OwnerProcessIdentity] = {}
        self._owner_secrets: dict[str, bytes] = {}
        self._connection_locks: dict[str, threading.Lock] = {}
        self._drain = threading.Condition()
        self._draining = False
        self._inflight = 0
        self._teardown_trace: list[str] = []

    def _realize_leaves(self, supplied: dict[str, object]) -> dict[str, object]:
        realized: dict[str, object] = {}
        for declaration in self._manifest.leaves:
            module_name, separator, class_name = declaration.implementation.partition(":")
            if not separator:
                raise OwnerProcessFailure("leaf implementation identity is malformed")
            implementation = getattr(importlib.import_module(module_name), class_name)
            instance = supplied.get(declaration.leaf_id)
            if instance is None:
                instance = implementation()
            if type(instance) is not implementation:
                raise OwnerProcessFailure("realized leaf type differs from manifest")
            realized[declaration.leaf_id] = instance
        if set(supplied) != set(realized) and supplied:
            raise OwnerProcessFailure("realized leaf set differs from manifest")
        return realized

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def start(self) -> None:
        if self._temporary is not None:
            raise OwnerProcessFailure("runtime generation already started")
        self._temporary = tempfile.TemporaryDirectory(prefix="chiplog-r7-")
        context = multiprocessing.get_context("spawn")
        for index, owner in enumerate(self._manifest.owners, start=1):
            identity = OwnerProcessIdentity(
                self._tenant_id,
                self._broker_epoch,
                owner.owner_id,
                self._generation_id,
                f"{self._generation_id}:{owner.owner_id}:{index}",
                owner.capability_ids,
            )
            path = str(Path(self._temporary.name) / f"{owner.owner_id}.sock")
            owner_secret = hmac.digest(
                self._session_secret,
                _canonical(asdict(identity)),
                "sha256",
            )
            ready = context.Event()
            process = context.Process(
                target=_owner_process_main,
                args=(path, owner_secret, identity, ready),
                name=f"chiplog-r7-{owner.owner_id}",
            )
            process.start()
            if not ready.wait(timeout=5):
                process.terminate()
                process.join(timeout=5)
                self.close()
                raise OwnerProcessFailure(f"owner {owner.owner_id} failed to start")
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.connect(path)
            if process.pid is None:
                raise OwnerProcessFailure(f"owner {owner.owner_id} has no process identity")
            _verify_local_peer(connection, process.pid)
            self._processes[owner.owner_id] = process
            self._connections[owner.owner_id] = connection
            self._identities[owner.owner_id] = identity
            self._owner_secrets[owner.owner_id] = owner_secret
            self._connection_locks[owner.owner_id] = threading.Lock()

    def attest(self) -> tuple[OwnerProcessAttestation, ...]:
        attestations: list[OwnerProcessAttestation] = []
        for owner_id in sorted(self._connections):
            connection = self._connections[owner_id]
            owner_secret = self._owner_secrets[owner_id]
            _send_frame(
                connection,
                owner_secret,
                _canonical({"operation": "runtime.attest"}),
            )
            value = json.loads(_receive_frame(connection, owner_secret))
            identity = self._identities[owner_id]
            if (
                value.get("tenant_id") != identity.tenant_id
                or value.get("owner_id") != identity.owner_id
                or value.get("generation_id") != identity.generation_id
                or value.get("session_id") != identity.session_id
                or tuple(value.get("capability_ids", ())) != identity.capability_ids
                or value.get("authority_empty_profile") != "python-audit-deny-raw-v1"
                or value.get("environment_empty") is not True
                or value.get("filesystem_denied") is not True
                or value.get("network_denied") is not True
                or value.get("process_spawn_denied") is not True
                or tuple(value.get("loaded_policy_modules", ())) != _owner_module_closure(identity)
            ):
                raise OwnerProcessFailure("owner attestation identity mismatch")
            attestations.append(
                OwnerProcessAttestation(
                    identity,
                    int(value["process_id"]),
                    int(value["parent_process_id"]),
                    str(value["dishka_scope"]),
                    tuple(value["provider_ids"]),
                    tuple(value["target_ids"]),
                    tuple(value["factory_ids"]),
                    tuple(value["loaded_policy_modules"]),
                    tuple(tuple(route) for route in value["routes"]),
                )
            )
        return tuple(attestations)

    def graph_generation(self) -> RuntimeGraphGeneration:
        attestations = self.attest()
        return RuntimeGraphGeneration(
            tenant_id=self._tenant_id,
            generation_id=self._generation_id,
            manifest_digest=self._manifest.fingerprint(),
            broker_epoch=self._broker_epoch,
            owners=tuple(
                OwnerGeneration(
                    owner_id=item.identity.owner_id,
                    process_identity=str(item.process_id),
                    generation_id=item.identity.generation_id,
                    session_id=item.identity.session_id,
                    capability_ids=item.identity.capability_ids,
                    provider_ids=item.provider_ids,
                    target_ids=item.target_ids,
                    factory_ids=item.factory_ids,
                    scopes=(item.dishka_scope,),
                )
                for item in attestations
            ),
            routes=tuple(route for item in attestations for route in item.routes),
            leaves=tuple(
                (
                    item.leaf_id,
                    item.owner_id,
                    f"{type(self._realized_leaves[item.leaf_id]).__module__}:"
                    f"{type(self._realized_leaves[item.leaf_id]).__name__}",
                )
                for item in self._manifest.leaves
            ),
            broker_capabilities=(
                "broker_clock",
                "channel_transport",
                "credential_store",
                "event_appender",
                "lease_authority",
                "provider_sdk",
                "raw_sqlite_connection",
                "socket_handle",
            ),
            application_loop_id=(
                "chiplog.agent-loop.v1"
                if "agent_loop" in self._identities
                else "chiplog.r8.application-loop.v1"
                if self._identities["planning"].capability_ids
                == ("planning.r8_create_intention_line",)
                else "chiplog.r7.application-loop.v1"
            ),
        )

    def session(self, owner_id: str) -> BrokerSession:
        identity = self._identities.get(owner_id)
        if identity is None:
            raise OwnerProcessFailure("owner session is unavailable")
        return BrokerSession(
            tenant_id=identity.tenant_id,
            broker_epoch=identity.broker_epoch,
            generation_id=identity.generation_id,
            owner_id=identity.owner_id,
            session_id=identity.session_id,
        )

    async def call(self, request: PublicPortCall) -> PublicPortResult:
        return await to_thread(self._call_sync, request)

    def call_sync(self, request: PublicPortCall) -> PublicPortResult:
        """Broker-local commit guards use the same routed admission synchronously."""
        return self._call_sync(request)

    def _call_sync(self, request: PublicPortCall) -> PublicPortResult:
        with self._drain:
            if self._draining:
                return self._rejected(request, "OWNER_DRAINING", "runtime generation is draining")
            self._inflight += 1
        try:
            return self._call_admitted(request)
        finally:
            with self._drain:
                self._inflight -= 1
                self._drain.notify_all()

    def _call_admitted(self, request: PublicPortCall) -> PublicPortResult:
        callee = self._identities.get(request.callee.owner_id)
        if callee is None or request.callee != self.session(request.callee.owner_id):
            return self._rejected(request, "STALE_SESSION", "callee session is not current")
        if (
            request.caller.tenant_id != self._tenant_id
            or request.caller.broker_epoch != self._broker_epoch
            or request.caller.generation_id != self._generation_id
        ):
            return self._rejected(request, "STALE_GENERATION", "caller generation is not current")
        if request.budget.absolute_deadline_ns <= self._clock.monotonic_ns():
            return self._rejected(request, "DEADLINE_EXCEEDED", "call deadline elapsed")
        held = set(request.held_resources) & set(self._manifest.exclusive_resources)
        if held:
            return self._rejected(
                request,
                "PROTOCOL_REJECTED",
                f"cross-owner await holds exclusive resources: {sorted(held)}",
            )
        owner = next(item for item in self._manifest.owners if item.owner_id == callee.owner_id)
        if request.operation_id not in owner.public_operations:
            return self._rejected(request, "PROTOCOL_REJECTED", "operation is not manifested")
        route = next(
            (
                item
                for item in self._manifest.routes
                if item.operation_id == request.operation_id
                and item.caller_owner_id == request.caller.owner_id
                and item.callee_owner_id == request.callee.owner_id
            ),
            None,
        )
        if route is None or route.request_schema_id != request.schema_id:
            return self._rejected(
                request, "PROTOCOL_REJECTED", "exact caller-to-callee schema edge is not manifested"
            )
        connection = self._connections[callee.owner_id]
        owner_secret = self._owner_secrets[callee.owner_id]
        wire_request = {
            "operation": request.operation_id,
            "payload": b64encode(request.canonical_payload).decode("ascii"),
            "request_id": request.request_id,
            "schema_id": request.schema_id,
            "target": request.callee.model_dump(),
        }
        with self._connection_locks[callee.owner_id]:
            _send_frame(connection, owner_secret, _canonical(wire_request))
            response = json.loads(_receive_frame(connection, owner_secret))
        if "failure" in response:
            return self._rejected(
                request,
                str(response["failure"]),
                str(response.get("reason", "owner rejected request")),
            )
        if str(response.get("schema_id")) != route.result_schema_id:
            return self._rejected(
                request, "PROTOCOL_REJECTED", "callee result schema differs from manifested edge"
            )
        return PublicPortSuccess(
            request_id=request.request_id,
            responder=request.callee,
            schema_id=str(response["schema_id"]),
            canonical_payload=b64decode(str(response["payload"])),
        )

    @staticmethod
    def _rejected(
        request: PublicPortCall,
        kind: str,
        reason: str,
    ) -> PublicPortRejected:
        return PublicPortRejected(
            request_id=request.request_id,
            responder=request.callee,
            failure=PublicPortFailure.model_validate({"kind": kind, "reason": reason}),
        )

    def close(self) -> None:
        with self._drain:
            self._draining = True
            drained = self._drain.wait_for(lambda: self._inflight == 0, timeout=5)
        if not drained:
            raise OwnerProcessFailure("accepted owner work exceeded the drain bound")
        owner_order = tuple(owner.owner_id for owner in reversed(self._manifest.owners))
        for owner_id in owner_order:
            connection = self._connections.get(owner_id)
            if connection is None:
                continue
            owner_secret = self._owner_secrets[owner_id]
            # resource-contexts: justified-manual-close — generation owns stored IPC connections
            try:
                _send_frame(
                    connection,
                    owner_secret,
                    _canonical({"operation": "runtime.shutdown"}),
                )
                _receive_frame(connection, owner_secret)
            except OSError, OwnerProcessFailure:
                pass
            finally:
                connection.close()
                self._connections.pop(owner_id, None)
                self._teardown_trace.append(owner_id)
        for owner_id in owner_order:
            process = self._processes.get(owner_id)
            if process is None:
                continue
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            self._processes.pop(owner_id, None)
        self._identities.clear()
        self._owner_secrets.clear()
        self._connection_locks.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    @property
    def teardown_trace(self) -> tuple[str, ...]:
        return tuple(self._teardown_trace)


__all__ = [
    "AuthorityBrokerRuntime",
    "OwnerProcessAttestation",
    "OwnerProcessFailure",
    "OwnerProcessIdentity",
]
