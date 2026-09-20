"""Chiplog-owned OAuth storage; no import of another application's credentials."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from oauth_cli_kit import get_token, login_oauth_interactive  # type: ignore[import-untyped]
from oauth_cli_kit.models import OAuthToken  # type: ignore[import-untyped]

from chiplog.capabilities.agent_loop.live_contract import CodexError


def state_directory() -> Path:
    return Path(os.environ.get("CHIPLOG_HOME", str(Path.home() / ".chiplog"))).absolute()


class CodexTokenStorage:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def prepare(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise CodexError("Chiplog state must be a directory owned by the current user")
        self.directory.chmod(0o700)

    def get_token_path(self) -> Path:
        return self.directory / "codex-oauth.json"

    def load(self) -> Any:
        path = self.get_token_path()
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise CodexError("OAuth file must be private (0600), regular, and owned by this user")
        try:
            with path.open(encoding="utf-8") as stream:
                value = json.load(stream)
            if not isinstance(value, dict) or set(value) != {
                "access",
                "refresh",
                "expires",
                "account_id",
            }:
                raise ValueError
            if (
                any(
                    type(value[key]) is not str or not value[key]
                    for key in ("access", "refresh", "account_id")
                )
                or type(value["expires"]) is not int
            ):
                raise ValueError
            return OAuthToken(**value)
        except OSError, ValueError, TypeError:
            raise CodexError(
                "OAuth file is unreadable or corrupt; run chiplog auth login"
            ) from None

    def save(self, token: Any) -> None:
        self.prepare()
        if not token.account_id:
            raise CodexError("OAuth response has no ChatGPT account identity")
        path = self.get_token_path()
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory, delete=False
            ) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                json.dump(
                    {
                        "access": token.access,
                        "refresh": token.refresh,
                        "expires": token.expires,
                        "account_id": token.account_id,
                    },
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def logout(self) -> None:
        with session_lock(self):
            self.get_token_path().unlink(missing_ok=True)


@contextmanager
def session_lock(storage: CodexTokenStorage) -> Iterator[None]:
    storage.prepare()
    # Login, refresh and logout share one lock, so an in-flight refresh cannot
    # recreate credentials after logout has returned. Closing releases flock.
    with (storage.directory / "session.lock").open("a") as stream:
        os.fchmod(stream.fileno(), 0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def credential_identity(token: Any) -> str:
    if not isinstance(token.account_id, str) or not token.account_id:
        raise CodexError("Missing ChatGPT account identity")
    return hashlib.sha256(token.account_id.encode()).hexdigest()


def current_token(storage: CodexTokenStorage) -> Any:
    storage.prepare()
    try:
        with session_lock(storage):
            token = get_token(storage=storage)
            credential_identity(token)
            return token
    except CodexError:
        raise
    except Exception:
        raise CodexError("Codex OAuth unavailable; run chiplog auth login") from None


def login(storage: CodexTokenStorage) -> None:
    storage.prepare()
    try:
        with session_lock(storage):
            login_oauth_interactive(
                print_fn=lambda text: print(re.sub(r"\[/?(?:cyan|yellow|dim)\]", "", text)),
                prompt_fn=input,
                originator="chiplog",
                storage=storage,
            )
    except Exception:
        raise CodexError("Codex OAuth login failed; retry chiplog auth login") from None


def auth_status(storage: CodexTokenStorage) -> str:
    token = storage.load()
    if token is None:
        return "SIGNED_OUT"
    return "AUTHENTICATED" if token.expires > time.time() * 1000 else "REFRESH_REQUIRED"
