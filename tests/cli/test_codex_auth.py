import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from oauth_cli_kit.models import OAuthToken  # type: ignore[import-untyped]

from chiplog.adapters.driven.codex_auth import CodexTokenStorage, auth_status
from chiplog.capabilities.agent_loop.live_contract import CodexError


def test_session_persists_across_cli_processes_and_logout_is_local(tmp_path: Path) -> None:
    storage = CodexTokenStorage(tmp_path / "chiplog")
    personal = tmp_path / "personal-auth"
    personal.write_text("personal-session")
    storage.save(
        OAuthToken("access-canary", "refresh-canary", int(time.time() * 1000) + 600000, "account")
    )
    assert storage.get_token_path().stat().st_mode & 0o777 == 0o600
    assert storage.directory.stat().st_mode & 0o777 == 0o700
    for action, expected in (
        ("status", "AUTHENTICATED"),
        ("logout", "SIGNED_OUT"),
        ("status", "SIGNED_OUT"),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "chiplog.cli", "auth", action],
            env={
                **os.environ,
                "CHIPLOG_HOME": str(storage.directory),
                "OAUTH_CLI_KIT_TOKEN_PATH": str(personal),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
        assert "canary" not in result.stdout + result.stderr
    assert personal.read_text() == "personal-session"


def test_bad_credentials_are_not_silently_imported(tmp_path: Path) -> None:
    storage = CodexTokenStorage(tmp_path)
    assert auth_status(storage) == "SIGNED_OUT"
    storage.get_token_path().write_text('{"OPENAI_API_KEY":"canary"}')
    storage.get_token_path().chmod(0o600)
    with pytest.raises(CodexError, match="corrupt"):
        auth_status(storage)
