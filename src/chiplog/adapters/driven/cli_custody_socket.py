"""One private accepted Unix peer confirms already retained bytes, never uploads them."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import secrets
import socket
import struct
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Self

from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    BsdSocketPeer,
    CliCustodyAuthenticationRequest,
    CliCustodyChallenge,
    CliCustodyHead,
    CliCustodyOffer,
    CliCustodyResponse,
    CliCustodyScope,
    CliSocketObservation,
    LinuxSocketPeer,
    SocketPeer,
)
from chiplog.capabilities.deployment_trust.cli_custody_validation import MAX_AGE_NS, policy_head


def _peer(fd: int) -> SocketPeer:
    if hasattr(socket, "SO_PEERCRED"):
        with socket.socket(fileno=os.dup(fd)) as duplicate:
            pid, uid, gid = struct.unpack(
                "3i", duplicate.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
        return LinuxSocketPeer(pid=pid, uid=uid, gid=gid)
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "getpeereid"):
        raise ValueError("unsupported OS peer mechanism")
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    call = libc.getpeereid
    call.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    call.restype = ctypes.c_int
    if call(fd, ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise OSError(ctypes.get_errno(), "getpeereid failed")
    return BsdSocketPeer(uid=uid.value, gid=gid.value)


class CliCustodySocket:
    """Composition-owned object; no supplied peer and no public observation adoption."""

    def __init__(
        self,
        scope: CliCustodyScope,
        raw: bytes,
        *,
        broker_epoch: str,
        broker_session: str,
        generation: str,
    ) -> None:
        self.scope, self.raw = scope, raw
        self._binding = (broker_epoch, broker_session, generation)
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request: CliCustodyAuthenticationRequest | None = None
        self._selected = False
        self._future: asyncio.Future[CliCustodyAuthenticationRequest] | None = None
        self._tasks: asyncio.TaskGroup | None = None
        self._exchange_task: asyncio.Task[None] | None = None

    @property
    def path(self) -> Path:
        if self._directory is None:
            raise ValueError("CLI custody endpoint not open")
        return Path(self._directory.name) / "peer.sock"

    async def __aenter__(self) -> Self:
        if self._selected or self._directory is not None:
            raise ValueError("CLI custody endpoint cannot be reused")
        self._directory = tempfile.TemporaryDirectory(prefix="chiplog-cli-", dir="/tmp")
        self._future = asyncio.get_running_loop().create_future()
        self._tasks = asyncio.TaskGroup()
        await self._tasks.__aenter__()
        try:
            self._server = await asyncio.start_unix_server(self._accepted, path=self.path)
            os.chmod(self.path, 0o600)
            self._identity = (self.path.stat().st_dev, self.path.stat().st_ino)
        except BaseException:
            await self.__aexit__()
            raise
        return self

    def _accepted(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._tasks is None or self._selected:
            writer.close()
            return
        self._selected = True
        self._writer = writer
        self._exchange_task = self._tasks.create_task(self._exchange(reader, writer))

    async def _exchange(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert self._future is not None
        try:
            async with asyncio.timeout(MAX_AGE_NS / 1_000_000_000):
                connection = writer.get_extra_info("socket")
                if connection is None:
                    raise ValueError("accepted socket missing")
                now = time.monotonic_ns()
                epoch, session, generation = self._binding
                registration_bytes = json.dumps(
                    {
                        "schema": "chiplog.cli.private-retained-endpoint.v1",
                        "tenant": self.scope.tenant_id,
                        "database": self.scope.database_id,
                        "path": str(self.path),
                        "device": self._identity[0],
                        "inode": self._identity[1],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                fingerprint = hashlib.sha256(registration_bytes).hexdigest()
                observation = CliSocketObservation(
                    endpoint_registration=CliCustodyHead(
                        identity="cli.custody.endpoint",
                        head="cli.custody.endpoint/" + fingerprint,
                        fingerprint=fingerprint,
                    ),
                    socket_path=str(self.path),
                    socket_device=self._identity[0],
                    socket_inode=self._identity[1],
                    connection_id=secrets.token_hex(32),
                    peer=_peer(connection.fileno()),
                    broker_epoch=epoch,
                    broker_session=session,
                    runtime_generation=generation,
                    clock_epoch=generation,
                    observed_at_ns=now,
                    valid_until_ns=now + MAX_AGE_NS,
                )
                challenge = CliCustodyChallenge(
                    nonce=secrets.token_bytes(32),
                    connection_id=observation.connection_id,
                    scope_fingerprint=hashlib.sha256(self.scope.canonical_bytes()).hexdigest(),
                    credential_id="hermetic-credential",
                    session_id="hermetic-session",
                    policy=policy_head(),
                    clock_epoch=generation,
                    valid_until_ns=now + MAX_AGE_NS,
                )
                offer = CliCustodyOffer(scope=self.scope, challenge=challenge)
                raw = offer.canonical_bytes()
                if not 0 < len(raw) <= 262144:
                    raise ValueError("CLI custody offer exceeds bound")
                writer.write(struct.pack("!I", len(raw)) + raw)
                await writer.drain()
                size = struct.unpack("!I", await reader.readexactly(4))[0]
                if not 0 < size <= 4096:
                    raise ValueError("CLI custody response exceeds bound")
                response_bytes = await reader.readexactly(size)
                response = CliCustodyResponse.model_validate_json(response_bytes)
                if (
                    response.canonical_bytes() != response_bytes
                    or response.challenge_fingerprint
                    != hashlib.sha256(challenge.canonical_bytes()).hexdigest()
                    or await reader.read(1) != b""
                ):
                    raise ValueError("CLI custody response changed or has trailing bytes")
                request = CliCustodyAuthenticationRequest(
                    offer=offer,
                    socket=observation,
                    response=response,
                    raw_command_bytes=self.raw,
                    observed_time_ns=time.monotonic_ns(),
                )
                self._request = request
                self.verify(request)
                if not self._future.done():
                    self._future.set_result(request)
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as error:
            if not self._future.done():
                self._future.set_exception(error)

    async def receive(self) -> CliCustodyAuthenticationRequest:
        if self._future is None:
            raise ValueError("CLI custody endpoint not open")
        async with asyncio.timeout(MAX_AGE_NS / 1_000_000_000):
            return await asyncio.shield(self._future)

    def verify(self, request: CliCustodyAuthenticationRequest) -> None:
        if self._request is not request or self._writer is None or self._writer.is_closing():
            raise ValueError("unissued or closed CLI observation")
        observed = request.socket
        current = self.path.stat()
        connection = self._writer.get_extra_info("socket")
        if (
            connection is None
            or (current.st_dev, current.st_ino) != self._identity
            or _peer(connection.fileno()) != observed.peer
            or str(self.path) != observed.socket_path
            or time.monotonic_ns() >= observed.valid_until_ns
            or request.offer.scope != self.scope
            or request.raw_command_bytes != self.raw
        ):
            raise ValueError("CLI peer, endpoint, raw scope or deadline changed")

    async def __aexit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.close()
        if self._writer is not None:
            self._writer.close()
        if self._exchange_task is not None:
            self._exchange_task.cancel()
        try:
            if self._tasks is not None:
                await self._tasks.__aexit__(None, None, None)
        finally:
            self._tasks = None
            try:
                if self._writer is not None:
                    with suppress(OSError):
                        await self._writer.wait_closed()
                if self._server is not None:
                    await self._server.wait_closed()
            finally:
                if (
                    self._future is not None
                    and self._future.done()
                    and not self._future.cancelled()
                ):
                    self._future.exception()
                if self._directory is not None:
                    self._directory.cleanup()
                    self._directory = None
