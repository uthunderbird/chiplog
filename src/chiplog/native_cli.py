"""Explicit native CLI command; OAuth authenticates only the model provider."""

import argparse
import asyncio
import sys
from pathlib import Path

from chiplog.adapters.driven.codex_auth import (
    CodexTokenStorage,
    credential_identity,
    current_token,
    state_directory,
)
from chiplog.adapters.driven.codex_model import CodexModel
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold, LoopRejected
from chiplog.capabilities.agent_loop.live_contract import CodexError, LiveModelBinding
from chiplog.composition.postgres_execution import ExecutionModelPort
from chiplog.composition.postgres_native_cli import NativeChatConfiguration, run_native_chat


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = sub.add_parser(
        "native-chat", help="Authenticated native input and durable local delivery"
    )
    for name in ("tenant", "principal", "credential", "session", "request-id", "message"):
        command.add_argument("--" + name, required=True)
    for name in ("state-dir", "entitlement", "entitlement-proof", "verification-key"):
        command.add_argument("--" + name, required=True, type=Path)
    command.add_argument("--model-provider", choices=("codex", "hermetic"), default="codex")
    command.add_argument("--response-file", type=Path, action="append", default=[])
    command.add_argument("--model", default="gpt-5.6-terra")
    command.add_argument(
        "--effort", choices=("none", "low", "medium", "high", "xhigh", "max"), default="low"
    )


def run_command(args: argparse.Namespace, operator_secret: bytes) -> None:
    try:
        model: ExecutionModelPort
        if args.model_provider == "hermetic":
            model = HermeticModel(tuple(path.read_bytes() for path in args.response_file))
            policy = BudgetPolicy(max_turns=8)
        else:
            if args.response_file:
                raise ValueError("response files require the explicit hermetic provider")
            storage = CodexTokenStorage(state_directory())
            token = current_token(storage)
            binding = LiveModelBinding(
                model=args.model, effort=args.effort, credential_identity=credential_identity(token)
            )
            model = CodexModel(storage, binding)
            policy = BudgetPolicy(max_turns=8, live_model=binding)
        config = NativeChatConfiguration(
            tenant=args.tenant,
            principal=args.principal,
            credential=args.credential,
            session=args.session,
            request_id=args.request_id,
            message=args.message,
            state_directory=args.state_dir,
            entitlement=args.entitlement,
            entitlement_proof=args.entitlement_proof,
            verification_key=args.verification_key,
        )
        rendered = asyncio.run(
            run_native_chat(config, operator_secret=operator_secret, model=model, policy=policy)
        )
        sys.stdout.buffer.write(rendered)
        sys.stdout.buffer.flush()
    except (CodexError, LoopHold, LoopRejected, PermissionError, ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
