"""One immutable physical resolver dispatch for the installed H1 runtime."""

from __future__ import annotations

import sqlite3
from typing import Protocol

from chiplog.platform._sqlite import (
    PhysicalPublicationCommand,
    PublicationVerificationMode,
    StoreAdmissionError,
    VerifiedOwnerPublication,
)

_COMPLETION_OPERATION = "agent_loop.complete_acceptance.v2"
_GENESIS_OPERATION = "agent_loop.delivery_work.genesis.v1"


class _Resolver(Protocol):
    def verify(
        self,
        command: PhysicalPublicationCommand,
        mode: PublicationVerificationMode,
        connection: sqlite3.Connection,
    ) -> VerifiedOwnerPublication: ...


class H1PublicationResolverMux:
    """Dispatch only the two closed H1 writer operations before SQL mutation."""

    def __init__(self, completion: _Resolver, genesis: _Resolver) -> None:
        self._completion = completion
        self._genesis = genesis

    def verify(
        self,
        command: PhysicalPublicationCommand,
        mode: PublicationVerificationMode,
        connection: sqlite3.Connection,
    ) -> VerifiedOwnerPublication:
        if command.operation_kind == _COMPLETION_OPERATION:
            return self._completion.verify(command, mode, connection)
        if command.operation_kind == _GENESIS_OPERATION:
            return self._genesis.verify(command, mode, connection)
        raise StoreAdmissionError("H1 publication resolver operation is not registered")
