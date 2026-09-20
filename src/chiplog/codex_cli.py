"""OAuth setup and interactive dialogue, separate from authenticated planning commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys

from chiplog.adapters.driven.codex_auth import (
    CodexTokenStorage,
    auth_status,
    credential_identity,
    current_token,
    login,
    state_directory,
)
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.live_contract import CodexError, LiveModelBinding
from chiplog.composition.codex_chat import open_codex_chat


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    auth = sub.add_parser("auth", help="Manage the separate Chiplog Codex OAuth session")
    auth.add_argument("auth_action", choices=("login", "status", "logout"))
    chat = sub.add_parser("chat", help="Talk to Chiplog using Codex OAuth")
    chat.add_argument("--model", default="gpt-5.6-terra")
    chat.add_argument(
        "--effort", choices=("none", "low", "medium", "high", "xhigh", "max"), default="low"
    )
    chat.add_argument("--message", help="Send one message instead of starting the REPL")


async def _chat(args: argparse.Namespace, storage: CodexTokenStorage) -> None:
    token = await asyncio.to_thread(current_token, storage)
    binding = LiveModelBinding(
        model=args.model,
        effort=args.effort,
        credential_identity=credential_identity(token),
    )
    # History is scoped to this explicit CLI session; old or interrupted runs are not replayed.
    history: list[dict[str, str]] = []
    async with open_codex_chat(storage, binding) as loop:
        if args.message is None:
            print(f"Chiplog · {binding.model} · {binding.effort}. /exit to quit.")
            print(
                "Messages in this dialogue are sent to OpenAI. "
                "Planning and external actions are proposals only."
            )
        while True:
            if args.message is not None:
                message = args.message
            else:
                try:
                    message = await _read_message()
                except EOFError:
                    return
            if message.strip() == "/exit":
                return
            if not message.strip():
                if args.message is not None:
                    raise CodexError("Message must not be empty")
                continue
            prompt = json.dumps({"history": history, "message": message}, ensure_ascii=False)
            run_id = "cli-" + secrets.token_hex(16)
            view = await loop.create(run_id, prompt, BudgetPolicy(max_turns=8, live_model=binding))
            view = await loop.activate(run_id, view.head)
            try:
                while view.state == "ACTIVE":
                    view = await loop.step(run_id, view.head)
            except CodexError, LoopRejected:
                print(f"Run retained: {run_id}. No automatic replay.")
                raise
            if view.state != "SUCCEEDED":
                raise CodexError(f"Run {run_id} ended as {view.state}")
            response = "\n".join(loop.record(run_id).accepted_text)
            print(response)
            history.extend(
                ({"role": "user", "text": message}, {"role": "assistant", "text": response})
            )
            if args.message is not None:
                return


async def _read_message() -> str:
    print("You> ", end="", flush=True)
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[str] = loop.create_future()

    def readable() -> None:
        if not ready.done():
            ready.set_result(sys.stdin.readline())

    try:
        loop.add_reader(sys.stdin, readable)
    except OSError, ValueError:
        # Regular redirected files cannot be registered with every selector.
        line = sys.stdin.readline()
    else:
        try:
            line = await ready
        finally:
            loop.remove_reader(sys.stdin)
    if not line:
        raise EOFError
    return line.rstrip("\r\n")


def run_command(args: argparse.Namespace) -> None:
    storage = CodexTokenStorage(state_directory())
    try:
        if args.action == "chat":
            asyncio.run(_chat(args, storage))
        elif args.auth_action == "login":
            login(storage)
            print("AUTHENTICATED: OAuth session saved for Chiplog")
        elif args.auth_action == "logout":
            storage.logout()
            print("SIGNED_OUT")
        else:
            print(auth_status(storage))
    except (CodexError, LoopRejected) as error:
        raise SystemExit(str(error)) from None
    except KeyboardInterrupt:
        raise SystemExit("Cancelled; no automatic replay") from None
