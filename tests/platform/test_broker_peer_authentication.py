from __future__ import annotations

import os
import struct
from typing import Any, cast

import pytest

from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
from chiplog.platform.r7_runtime import (
    AuthorityBrokerRuntime,
    OwnerProcessFailure,
    _verify_local_peer,
)


def test_one_owner_session_key_cannot_authenticate_as_another_owner() -> None:
    with AuthorityBrokerRuntime(
        "tenant-1",
        1,
        "generation-keys",
        R7_PRODUCTION_MANIFEST,
        b"test-session-secret",
    ) as runtime:
        runtime._owner_secrets["planning"] = runtime._owner_secrets["deployment_trust"]
        with pytest.raises(OwnerProcessFailure, match=r"authentication|closed"):
            runtime.attest()


def test_foreign_local_peer_uid_is_rejected_before_ipc() -> None:
    class ForeignPeer:
        def getsockopt(self, _level: int, option: int, _size: int) -> bytes:
            if option == 2:
                return struct.pack("@i", os.getpid())
            return struct.pack("@II", 0, os.getuid() + 1) + bytes(72)

    with pytest.raises(OwnerProcessFailure, match="peer credential"):
        _verify_local_peer(cast(Any, ForeignPeer()), os.getpid())


def test_same_uid_foreign_peer_pid_is_rejected_before_ipc() -> None:
    class ForeignPeer:
        def getsockopt(self, _level: int, option: int, _size: int) -> bytes:
            if option == 2:
                return struct.pack("@i", os.getpid() + 1)
            return struct.pack("@II", 0, os.getuid()) + bytes(72)

    with pytest.raises(OwnerProcessFailure, match="peer credential"):
        _verify_local_peer(cast(Any, ForeignPeer()), os.getpid())
