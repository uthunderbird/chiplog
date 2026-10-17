"""Private Unix socket ingress; the observed OS peer precedes witness issuance."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import socket
import struct
from collections.abc import Callable

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.delivery_contracts import DeliveryDTO
from chiplog.capabilities.agent_loop.ingress_client_contracts import (
    NativeApplicationDriver,
    NativeIngressPort,
    NativeIngressSubmissionV1,
)


class CliIngressFrame(DeliveryDTO):
    replay_id: str = Field(min_length=1)
    raw_bytes: bytes


def peer_uid(fd: int) -> int:
    if hasattr(socket, "SO_PEERCRED"):
        with socket.socket(fileno=os.dup(fd)) as connection:
            _, uid, _ = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            return int(uid)
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "getpeereid"):
        raise PermissionError("OS peer authentication unsupported")
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    call = libc.getpeereid
    call.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    call.restype = ctypes.c_int
    if call(fd, ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise OSError(ctypes.get_errno(), "getpeereid failed")
    return uid.value


class NativeCliIngress:
    def __init__(
        self,
        ingress: NativeIngressPort,
        driver: NativeApplicationDriver,
        *,
        peer: str,
        uid: int,
        policy: BudgetPolicy,
        issue: Callable[[str, bytes, BudgetPolicy, str], NativeIngressSubmissionV1],
    ) -> None:
        self.ingress, self.driver = ingress, driver
        self.peer, self.uid = peer, uid
        self.policy, self._issue = policy, issue

    async def receive(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One framed request per accepted connection; ACK only selected custody.

        The server owner manages this accepted writer's lifetime. Driver errors
        after ACK leave the admitted Run recoverable through its stable identity.
        """
        connection = writer.get_extra_info("socket")
        if connection is None:
            raise PermissionError("CLI connection has no OS peer")
        observed_uid = peer_uid(connection.fileno())
        if observed_uid != self.uid:
            raise PermissionError("CLI OS peer differs from registered source")
        async with asyncio.timeout(5):
            size = int.from_bytes(await reader.readexactly(4), "big")
            if not 0 < size <= 262144:
                raise ValueError("CLI ingress frame size exceeds contract")
            frame = CliIngressFrame.model_validate_json(await reader.readexactly(size))
            # EOF binds one entire request and rejects a second hidden frame.
            if await reader.read(1):
                raise ValueError("CLI ingress permits exactly one request")
        submission = self._issue(
            frame.replay_id,
            frame.raw_bytes,
            self.policy,
            json.dumps(("uid", observed_uid), separators=(",", ":")),
        )
        admitted = await self.ingress.submit(self.peer, submission, self.policy)
        selected = self.ingress.lookup(self.peer, submission.identity)
        if selected != admitted or selected.raw_bytes != frame.raw_bytes:
            raise PermissionError("CLI ACK has no exact selected custody")
        raw = selected.canonical_bytes()
        writer.write(len(raw).to_bytes(4, "big") + raw)
        await writer.drain()
        await self.driver.drive(self.peer, selected.inbox_id)
