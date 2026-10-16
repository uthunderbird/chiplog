"""Only explicitly configured test instances; destroy only UUID-owned databases."""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from chiplog.platform.postgres import PostgresServer, UserDatabase


@pytest.fixture
def postgres_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[PostgresServer]:
    dsn = os.environ.get("CHIPLOG_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set CHIPLOG_TEST_POSTGRES_DSN for real PostgreSQL integration")
    server = PostgresServer(dsn)
    prefix = "test:" + uuid.uuid4().hex + ":"
    databases: list[UserDatabase] = []
    original = PostgresServer.user_database

    def mapped(self: PostgresServer, identity: str) -> UserDatabase:
        database = original(self, prefix + identity)
        databases.append(database)
        return database

    monkeypatch.setattr(PostgresServer, "user_database", mapped)
    monkeypatch.setattr(PostgresServer, "from_environment", classmethod(lambda cls: server))
    yield server
    with psycopg.connect(dsn, autocommit=True) as connection:
        for name in {database.name for database in databases}:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
