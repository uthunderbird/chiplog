"""Operator-invoked import into the trusted per-user database mapping."""

from pathlib import Path

from chiplog.adapters.driven.sqlite_chat_import import import_sqlite_chat
from chiplog.platform.postgres import PostgresServer


def import_local_chat(source: Path, tenant: str) -> str:
    database = PostgresServer.from_environment().user_database(tenant)
    return import_sqlite_chat(source, database, tenant)
