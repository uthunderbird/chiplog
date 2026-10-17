"""Telegram webhook request boundary; no network listener or live credentials."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.ingress_client_contracts import (
    NativeApplicationDriver,
    NativeIngressPort,
    NativeIngressReceiptV1,
    NativeIngressSubmissionV1,
)
from chiplog.platform.telegram_candidates import project_retained_telegram


class NativeTelegramIngress:
    def __init__(
        self,
        ingress: NativeIngressPort,
        driver: NativeApplicationDriver,
        *,
        peer: str,
        tenant_id: str,
        endpoint_id: str,
        account_id: str,
        secret: bytes,
        user_id: int,
        chat_id: int,
        policy: BudgetPolicy,
        issue: Callable[[str, bytes, BudgetPolicy, str], NativeIngressSubmissionV1],
    ) -> None:
        if not secret:
            raise ValueError("Telegram webhook requires an independently configured secret")
        self.ingress, self.driver = ingress, driver
        self.peer, self.tenant_id = peer, tenant_id
        self.endpoint_id, self.account_id, self._secret = endpoint_id, account_id, secret
        self.user_id, self.chat_id = user_id, chat_id
        self.policy, self._issue = policy, issue

    async def receive(
        self, *, endpoint_id: str, account_id: str, secret_header: bytes, raw: bytes
    ) -> NativeIngressReceiptV1:
        """HTTP mounting supplies observed route/account/header separately from body.

        The returned receipt permits a custody ACK only after selected lookup.
        This is a hermetically mounted webhook boundary, not live Telegram proof.
        """
        if (
            endpoint_id != self.endpoint_id
            or account_id != self.account_id
            or not hmac.compare_digest(secret_header, self._secret)
        ):
            raise PermissionError("Telegram webhook transport is not authenticated")
        candidate = project_retained_telegram(
            tenant_id=self.tenant_id, candidate_id="telegram-webhook", raw_bytes=raw
        )
        fields = candidate.candidate_fields
        if fields.kind != "PRIVATE_TEXT":
            raise LoopRejected("unsupported Telegram update")
        if fields.telegram_user_id != self.user_id or fields.chat_id != self.chat_id:
            raise PermissionError("Telegram sender/chat differs from registered source")
        submission = self._issue(
            str(candidate.parsed_update_identity),
            raw,
            self.policy,
            json.dumps(
                ("telegram", endpoint_id, account_id, fields.telegram_user_id, fields.chat_id),
                separators=(",", ":"),
            ),
        )
        admitted = await self.ingress.submit(self.peer, submission, self.policy)
        selected = self.ingress.lookup(self.peer, submission.identity)
        if selected != admitted or selected.raw_bytes != raw:
            raise PermissionError("Telegram ACK has no exact selected custody")
        await self.driver.drive(self.peer, selected.inbox_id)
        return selected
