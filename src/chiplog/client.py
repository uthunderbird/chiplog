"""Default programmatic client entrypoint: native per-user PostgreSQL execution.

Old versioned SQLite composition factories are legacy compatibility surfaces.
This client has no SQLite backend selector or fallback.
"""

from chiplog.composition.postgres_execution import (
    open_postgres_execution_runtime as open_execution_client,
)

__all__ = ["open_execution_client"]
