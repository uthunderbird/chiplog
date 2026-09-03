"""R6 authenticated CLI planning slice."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.composition.r6 import open_r6_runtime
from chiplog.domain_primitives import RecordId, TenantId


def _operator_secret() -> bytes:
    value = os.environ.get("CHIPLOG_R6_OPERATOR_SECRET")
    if not value:
        raise SystemExit("INDETERMINATE: CHIPLOG_R6_OPERATOR_SECRET is required")
    return value.encode()


async def _bootstrap(args: argparse.Namespace) -> None:
    async with open_r6_runtime(Path(args.database), operator_secret=_operator_secret()) as runtime:
        await runtime.bootstrap(
            tenant_id=args.tenant,
            database_instance_id=args.database_instance,
            principal_id=args.principal,
            credential_id=args.credential,
            session_id=args.session,
            token=args.token,
        )
    print("BOOTSTRAPPED")


async def _create(args: argparse.Namespace) -> None:
    tenant = TenantId(args.tenant)
    outcome: object
    async with open_r6_runtime(Path(args.database), operator_secret=_operator_secret()) as runtime:
        outcome = await runtime.create(
            tenant_id=args.tenant,
            principal_id=args.principal,
            credential_id=args.credential,
            session_id=args.session,
            command=CreateIntentionLine(
                RecordId(tenant, args.command_id),
                RecordId(tenant, args.intention_id),
                RecordId(tenant, args.revision_id),
                args.purpose,
                args.authority_act,
            ),
        )
    disposition = getattr(outcome, "disposition", "INDETERMINATE")
    reason = getattr(outcome, "reason", None)
    result = getattr(outcome, "result", None)
    if disposition not in {"COMMITTED", "REPLAY"}:
        raise SystemExit(f"{disposition}: {reason or 'rejected'}")
    print(f"{disposition} commit_sequence={getattr(result, 'commit_sequence', None)}")


async def _render(args: argparse.Namespace) -> None:
    async with open_r6_runtime(Path(args.database), operator_secret=_operator_secret()) as runtime:
        try:
            rendered = runtime.render(
                tenant_id=args.tenant,
                principal_id=args.principal,
                credential_id=args.credential,
                session_id=args.session,
            )
        except PermissionError as error:
            raise SystemExit(str(error)) from error
    print("\n".join(rendered))


def _identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--credential", required=True)
    parser.add_argument("--session", required=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="chiplog")
    parser.add_argument("--database", default="chiplog.sqlite3")
    sub = parser.add_subparsers(dest="action", required=True)
    bootstrap = sub.add_parser("bootstrap")
    _identity_arguments(bootstrap)
    bootstrap.add_argument("--database-instance", required=True)
    bootstrap.add_argument("--token", required=True)
    create = sub.add_parser("create")
    create.add_argument("purpose")
    _identity_arguments(create)
    create.add_argument("--command-id", required=True)
    create.add_argument("--intention-id", required=True)
    create.add_argument("--revision-id", required=True)
    create.add_argument("--authority-act", required=True)
    show = sub.add_parser("show")
    _identity_arguments(show)
    args = parser.parse_args()
    if args.action == "bootstrap":
        asyncio.run(_bootstrap(args))
    elif args.action == "create":
        asyncio.run(_create(args))
    else:
        asyncio.run(_render(args))


if __name__ == "__main__":
    main()
