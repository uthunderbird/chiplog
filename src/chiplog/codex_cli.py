"""OAuth setup for the native client's Codex model transport."""

import argparse

from chiplog.adapters.driven.codex_auth import (
    CodexTokenStorage,
    auth_status,
    login,
    state_directory,
)
from chiplog.capabilities.agent_loop.live_contract import CodexError


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    auth = sub.add_parser("auth", help="Manage the Chiplog Codex OAuth session")
    auth.add_argument("auth_action", choices=("login", "status", "logout"))


def run_command(args: argparse.Namespace) -> None:
    storage = CodexTokenStorage(state_directory())
    try:
        if args.auth_action == "login":
            login(storage)
            print("AUTHENTICATED: OAuth session saved for Chiplog")
        elif args.auth_action == "logout":
            storage.logout()
            print("SIGNED_OUT")
        else:
            print(auth_status(storage))
    except CodexError as error:
        raise SystemExit(str(error)) from None
    except KeyboardInterrupt:
        raise SystemExit("Cancelled") from None
