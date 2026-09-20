import base64
import json
from pathlib import Path

import httpx
import pytest
from oauth_cli_kit.models import OAuthToken  # type: ignore[import-untyped]

from chiplog.adapters.driven.codex_auth import CodexTokenStorage, current_token
from chiplog.capabilities.agent_loop.live_contract import CodexError


def test_expired_oauth_session_refreshes_and_persists_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = CodexTokenStorage(tmp_path)
    storage.save(OAuthToken("old", "refresh-canary", 1, "account"))
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "https://api.openai.com/auth": {"chatgpt_account_id": "account"},
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    access = "header." + payload + ".signature"
    original = httpx.Client
    calls = 0

    def refresh(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == "https://auth.openai.com/oauth/token"
        assert b"grant_type=refresh_token" in request.content
        assert b"refresh_token=refresh-canary" in request.content
        return httpx.Response(
            200, json={"access_token": access, "refresh_token": "rotated", "expires_in": 3600}
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(refresh), **kw)
    )
    assert current_token(storage).access == access
    assert CodexTokenStorage(tmp_path).load().refresh == "rotated"
    assert current_token(storage).access == access
    assert calls == 1


def test_refresh_failure_does_not_expose_provider_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = CodexTokenStorage(tmp_path)
    storage.save(OAuthToken("access-canary", "refresh-canary", 1, "account"))
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(
            transport=httpx.MockTransport(lambda _: httpx.Response(401, text="refresh-canary")),
            **kw,
        ),
    )
    with pytest.raises(CodexError) as error:
        current_token(storage)
    assert "canary" not in str(error.value)
    assert storage.load().refresh == "refresh-canary"
