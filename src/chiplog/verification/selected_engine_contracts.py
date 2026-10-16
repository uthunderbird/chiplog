"""Reviewed UNREGISTERED preparation expectations; no runtime admission.

Frozen from r14-selected-engine-golden-proposal.md after root review 2026-10-16.
Expected literals never come from candidate source or observations.
Engine version is candidate provenance, not a host allowlist.
"""

from collections.abc import Iterable

from chiplog.verification.sqlite_preparation import (
    ColumnInfo,
    IndexColumnInfo,
    IndexInfo,
    PhysicalCatalogRow,
    SqlAction,
)

ColumnDeclarations = tuple[tuple[str, tuple[tuple[str, str, int, int], ...]], ...]
SchemaContract = tuple[
    str, tuple[tuple[str, str], ...], tuple[PhysicalCatalogRow, ...],
    tuple[tuple[str, tuple[ColumnInfo, ...]], ...],
    tuple[tuple[str, tuple[IndexInfo, ...]], ...],
    tuple[tuple[str, tuple[IndexColumnInfo, ...]], ...],
]

TENANT_DDL = """
CREATE TABLE store_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1), store_version INTEGER NOT NULL
);
CREATE TABLE tenant_heads (tenant_id TEXT PRIMARY KEY, head INTEGER NOT NULL);
CREATE TABLE publications (
    tenant_id TEXT NOT NULL, operation_kind TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL, commit_sequence INTEGER NOT NULL, record_ids TEXT NOT NULL,
    PRIMARY KEY (tenant_id, operation_kind, idempotency_key)
);
CREATE TABLE records (
    tenant_id TEXT NOT NULL, record_id TEXT NOT NULL, owner TEXT NOT NULL,
    schema_id TEXT NOT NULL, canonical_bytes BLOB NOT NULL, commit_sequence INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, record_id)
);
CREATE TABLE evidence_inbox (
    tenant_id TEXT NOT NULL, source_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL, canonical_bytes BLOB NOT NULL, followup_kind TEXT NOT NULL,
    state TEXT NOT NULL, attempt_id TEXT, transport_version TEXT, cursor TEXT,
    PRIMARY KEY (tenant_id, source_id, evidence_id)
);
CREATE TABLE deletion_fences (
    tenant_id TEXT PRIMARY KEY, generation TEXT NOT NULL, frontier INTEGER NOT NULL
);
CREATE TABLE derivatives (
    tenant_id TEXT NOT NULL, sink TEXT NOT NULL, derivative_id TEXT NOT NULL,
    source_record_ids TEXT NOT NULL, source_epoch INTEGER NOT NULL,
    provenance_fingerprint TEXT NOT NULL, PRIMARY KEY (tenant_id, sink, derivative_id)
);
"""
LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS authority_read_state (
    tenant_id TEXT PRIMARY KEY,
    canonical_state BLOB NOT NULL,
    state_fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS authority_read_releases (
    tenant_id TEXT NOT NULL,
    read_attempt_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    initial_state_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'RELEASED', 'STALE_OR_INDETERMINATE_READ')),
    result_digest TEXT,
    result_bytes BLOB,
    recipient_fingerprint TEXT NOT NULL,
    proof_fingerprint TEXT,
    enqueued INTEGER NOT NULL CHECK (enqueued IN (0, 1)),
    dequeued INTEGER NOT NULL CHECK (dequeued IN (0, 1)),
    PRIMARY KEY (tenant_id, read_attempt_id)
);
"""

EXPECTED_SQL: tuple[tuple[str, int, str, str], ...] = (
    (
        'BrokerAuthorityReader._execute_planning_read', 39, 'branch=0',
        (
            'SELECT 1 FROM publications WHERE tenant_id = ? GROUP BY commit_sequence HAVING '
            'COUNT(*) > 1 LIMIT 1'
        ),
    ),
    (
        'BrokerAuthorityReader._execute_planning_read', 44, 'branch=0',
        (
            'SELECT tenant_id, operation_kind, idempotency_key, request_fingerprint, '
            'commit_sequence, record_ids FROM publications WHERE tenant_id = ? AND '
            "operation_kind = 'planning.create_intention_line' AND (commit_sequence, "
            'operation_kind, idempotency_key) > (?, ?, ?) ORDER BY commit_sequence, '
            'operation_kind, idempotency_key LIMIT ?'
        ),
    ),
    (
        'BrokerAuthorityReader._execute_planning_read', 44, 'branch=1',
        (
            'SELECT tenant_id, operation_kind, idempotency_key, request_fingerprint, '
            'commit_sequence, record_ids FROM publications WHERE tenant_id = ? AND '
            '(commit_sequence, operation_kind, idempotency_key) > (?, ?, ?) ORDER BY '
            'commit_sequence, operation_kind, idempotency_key LIMIT ?'
        ),
    ),
    (
        'BrokerAuthorityReader._execute_planning_read', 48, 'branch=0',
        (
            'SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, commit_sequence '
            'FROM records WHERE tenant_id = ? AND commit_sequence IN (SELECT commit_sequence '
            'FROM publications WHERE tenant_id = ? AND operation_kind = '
            "'planning.create_intention_line' AND (commit_sequence, operation_kind, "
            'idempotency_key) > (?, ?, ?) ORDER BY commit_sequence, operation_kind, '
            'idempotency_key LIMIT ?) ORDER BY record_id LIMIT ?'
        ),
    ),
    (
        'BrokerAuthorityReader._execute_planning_read', 48, 'branch=1',
        (
            'SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, commit_sequence '
            'FROM records WHERE tenant_id = ? AND commit_sequence IN (SELECT commit_sequence '
            'FROM publications WHERE tenant_id = ? AND (commit_sequence, operation_kind, '
            'idempotency_key) > (?, ?, ?) ORDER BY commit_sequence, operation_kind, '
            'idempotency_key LIMIT ?) ORDER BY record_id LIMIT ?'
        ),
    ),
    (
        'BrokerAuthorityReader.execute', 9, 'branch=0',
        (
            'BEGIN'
        ),
    ),
    (
        'BrokerAuthorityReader.execute', 24, 'branch=0',
        (
            'SELECT head FROM tenant_heads WHERE tenant_id = ?'
        ),
    ),
    (
        '_authority_snapshot_bytes', 12, "(('table', 'deletion_fences'),)",
        (
            'SELECT "tenant_id", "generation", "frontier" FROM main."deletion_fences" ORDER '
            'BY 1, 2, 3'
        ),
    ),
    (
        '_authority_snapshot_bytes', 12, "(('table', 'evidence_inbox'),)",
        (
            'SELECT "tenant_id", "source_id", "evidence_id", "fingerprint", '
            '"canonical_bytes", "followup_kind", "state", "attempt_id", "transport_version", '
            '"cursor" FROM main."evidence_inbox" ORDER BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10'
        ),
    ),
    (
        '_authority_snapshot_bytes', 12, "(('table', 'publications'),)",
        (
            'SELECT "tenant_id", "operation_kind", "idempotency_key", "request_fingerprint", '
            '"commit_sequence", "record_ids" FROM main."publications" ORDER BY 1, 2, 3, 4, 5,'
            ' 6'
        ),
    ),
    (
        '_authority_snapshot_bytes', 12, "(('table', 'records'),)",
        (
            'SELECT "tenant_id", "record_id", "owner", "schema_id", "canonical_bytes", '
            '"commit_sequence" FROM main."records" ORDER BY 1, 2, 3, 4, 5, 6'
        ),
    ),
    (
        '_authority_snapshot_bytes', 12, "(('table', 'store_metadata'),)",
        (
            'SELECT "singleton", "store_version" FROM main."store_metadata" ORDER BY 1, 2'
        ),
    ),
    (
        '_authority_snapshot_bytes', 12, "(('table', 'tenant_heads'),)",
        (
            'SELECT "tenant_id", "head" FROM main."tenant_heads" ORDER BY 1, 2'
        ),
    ),
    (
        '_schema_members', 4, 'branch=0',
        (
            "SELECT name FROM main.sqlite_master WHERE type = 'table' AND name NOT GLOB "
            "'sqlite_*' ORDER BY name"
        ),
    ),
    (
        '_schema_members', 12, "(('table', 'deletion_fences'),)",
        (
            'PRAGMA main.table_info("deletion_fences")'
        ),
    ),
    (
        '_schema_members', 12, "(('table', 'derivatives'),)",
        (
            'PRAGMA main.table_info("derivatives")'
        ),
    ),
    (
        '_schema_members', 12, "(('table', 'evidence_inbox'),)",
        (
            'PRAGMA main.table_info("evidence_inbox")'
        ),
    ),
    (
        '_schema_members', 12, "(('table', 'publications'),)",
        (
            'PRAGMA main.table_info("publications")'
        ),
    ),
    (
        '_schema_members', 12, "(('table', 'records'),)",
        (
            'PRAGMA main.table_info("records")'
        ),
    ),
    (
        '_schema_members', 12, "(('table', 'store_metadata'),)",
        (
            'PRAGMA main.table_info("store_metadata")'
        ),
    ),
    (
        '_schema_members', 12, "(('table', 'tenant_heads'),)",
        (
            'PRAGMA main.table_info("tenant_heads")'
        ),
    ),
    (
        'BrokerReadLedger.__init__', 16, 'literal-script',
        (
            '\nCREATE TABLE IF NOT EXISTS authority_read_state (\n    tenant_id TEXT PRIMARY '
            'KEY,\n    canonical_state BLOB NOT NULL,\n    state_fingerprint TEXT NOT NULL\n'
            ');\nCREATE TABLE IF NOT EXISTS authority_read_releases (\n    tenant_id TEXT NOT'
            ' NULL,\n    read_attempt_id TEXT NOT NULL,\n    request_fingerprint TEXT NOT '
            'NULL,\n    initial_state_fingerprint TEXT NOT NULL,\n    state TEXT NOT NULL '
            "CHECK (state IN ('PENDING', 'RELEASED', 'STALE_OR_INDETERMINATE_READ')),\n    "
            'result_digest TEXT,\n    result_bytes BLOB,\n    recipient_fingerprint TEXT NOT '
            'NULL,\n    proof_fingerprint TEXT,\n    enqueued INTEGER NOT NULL CHECK '
            '(enqueued IN (0, 1)),\n    dequeued INTEGER NOT NULL CHECK (dequeued IN (0, 1)),'
            '\n    PRIMARY KEY (tenant_id, read_attempt_id)\n);\n'
        ),
    ),
    (
        'BrokerReadLedger.begin', 6, 'branch=0',
        (
            'INSERT INTO authority_read_releases VALUES\n                       (?, ?, ?, ?, '
            "'PENDING', NULL, NULL, ?, NULL, 0, 0)"
        ),
    ),
    (
        'BrokerReadLedger.current_state_or_none', 7, 'branch=0',
        (
            'SELECT canonical_state, state_fingerprint FROM authority_read_state WHERE '
            'tenant_id = ?'
        ),
    ),
    (
        'BrokerReadLedger.dequeue', 4, 'branch=0',
        (
            'BEGIN IMMEDIATE'
        ),
    ),
    (
        'BrokerReadLedger.dequeue', 7, 'branch=0',
        (
            'SELECT result_bytes, recipient_fingerprint, state, enqueued, dequeued'
            '\n                   FROM authority_read_releases\n                   WHERE '
            'tenant_id = ? AND read_attempt_id = ?'
        ),
    ),
    (
        'BrokerReadLedger.dequeue', 13, 'branch=0',
        (
            'UPDATE authority_read_releases SET dequeued = 1\n                   WHERE '
            'tenant_id = ? AND read_attempt_id = ? AND dequeued = 0'
        ),
    ),
    (
        'BrokerReadLedger.release', 10, 'branch=0',
        (
            'BEGIN IMMEDIATE'
        ),
    ),
    (
        'BrokerReadLedger.release', 13, 'branch=0',
        (
            'SELECT state_fingerprint FROM authority_read_state WHERE tenant_id = ?'
        ),
    ),
    (
        'BrokerReadLedger.release', 16, 'branch=0',
        (
            'SELECT request_fingerprint, initial_state_fingerprint, state,'
            '\n                          result_digest, recipient_fingerprint, '
            'proof_fingerprint,\n                          enqueued, dequeued'
            '\n                   FROM authority_read_releases\n                   WHERE '
            'tenant_id = ? AND read_attempt_id = ?'
        ),
    ),
    (
        'BrokerReadLedger.release', 29, 'branch=0',
        (
            "UPDATE authority_read_releases SET state = 'RELEASED',\n                       "
            'result_digest = ?, result_bytes = ?, proof_fingerprint = ?, enqueued = 1'
            '\n                       WHERE tenant_id = ? AND read_attempt_id = ? AND state ='
            " 'PENDING'"
        ),
    ),
    (
        'BrokerReadLedger.release', 31, 'branch=0',
        (
            'UPDATE authority_read_releases\n                       SET state = '
            "'STALE_OR_INDETERMINATE_READ', proof_fingerprint = ?\n                       "
            "WHERE tenant_id = ? AND read_attempt_id = ? AND state = 'PENDING'"
        ),
    ),
)

# name, SQLite declared type, explicit NOT NULL, primary-key ordinal.
TENANT_COLUMNS: ColumnDeclarations = (
    (
        "deletion_fences",
        (("tenant_id", "TEXT", 0, 1), ("generation", "TEXT", 1, 0), ("frontier", "INTEGER", 1, 0)),
    ),
    (
        "derivatives",
        (
            ("tenant_id", "TEXT", 1, 1),
            ("sink", "TEXT", 1, 2),
            ("derivative_id", "TEXT", 1, 3),
            ("source_record_ids", "TEXT", 1, 0),
            ("source_epoch", "INTEGER", 1, 0),
            ("provenance_fingerprint", "TEXT", 1, 0),
        ),
    ),
    (
        "evidence_inbox",
        (
            ("tenant_id", "TEXT", 1, 1),
            ("source_id", "TEXT", 1, 2),
            ("evidence_id", "TEXT", 1, 3),
            ("fingerprint", "TEXT", 1, 0),
            ("canonical_bytes", "BLOB", 1, 0),
            ("followup_kind", "TEXT", 1, 0),
            ("state", "TEXT", 1, 0),
            ("attempt_id", "TEXT", 0, 0),
            ("transport_version", "TEXT", 0, 0),
            ("cursor", "TEXT", 0, 0),
        ),
    ),
    (
        "publications",
        (
            ("tenant_id", "TEXT", 1, 1),
            ("operation_kind", "TEXT", 1, 2),
            ("idempotency_key", "TEXT", 1, 3),
            ("request_fingerprint", "TEXT", 1, 0),
            ("commit_sequence", "INTEGER", 1, 0),
            ("record_ids", "TEXT", 1, 0),
        ),
    ),
    (
        "records",
        (
            ("tenant_id", "TEXT", 1, 1),
            ("record_id", "TEXT", 1, 2),
            ("owner", "TEXT", 1, 0),
            ("schema_id", "TEXT", 1, 0),
            ("canonical_bytes", "BLOB", 1, 0),
            ("commit_sequence", "INTEGER", 1, 0),
        ),
    ),
    ("store_metadata", (("singleton", "INTEGER", 0, 1), ("store_version", "INTEGER", 1, 0))),
    ("tenant_heads", (("tenant_id", "TEXT", 0, 1), ("head", "INTEGER", 1, 0))),
)
LEDGER_COLUMNS: ColumnDeclarations = (
    (
        "authority_read_releases",
        (
            ("tenant_id", "TEXT", 1, 1),
            ("read_attempt_id", "TEXT", 1, 2),
            ("request_fingerprint", "TEXT", 1, 0),
            ("initial_state_fingerprint", "TEXT", 1, 0),
            ("state", "TEXT", 1, 0),
            ("result_digest", "TEXT", 0, 0),
            ("result_bytes", "BLOB", 0, 0),
            ("recipient_fingerprint", "TEXT", 1, 0),
            ("proof_fingerprint", "TEXT", 0, 0),
            ("enqueued", "INTEGER", 1, 0),
            ("dequeued", "INTEGER", 1, 0),
        ),
    ),
    (
        "authority_read_state",
        (
            ("tenant_id", "TEXT", 0, 1),
            ("canonical_state", "BLOB", 1, 0),
            ("state_fingerprint", "TEXT", 1, 0),
        ),
    ),
)
AMR_MEMBERS = tuple(
    (table, tuple(c[0] for c in columns), table != "derivatives")
    for table, columns in TENANT_COLUMNS
)


def schema_contract(domain: str) -> SchemaContract:
    """Deterministic physical truth computed ONLY from the reviewed fixed literals."""
    columns: ColumnDeclarations
    creation_order: tuple[str, ...]
    if domain == "tenant_main":
        ddl, columns, creation_order = (
            TENANT_DDL,
            TENANT_COLUMNS,
            (
                "store_metadata",
                "tenant_heads",
                "publications",
                "records",
                "evidence_inbox",
                "deletion_fences",
                "derivatives",
            ),
        )
    elif domain == "broker_read_ledger":
        ddl, columns, creation_order = (
            LEDGER_DDL,
            LEDGER_COLUMNS,
            ("authority_read_state", "authority_read_releases"),
        )
    else:
        raise ValueError("unknown selected schema domain")
    units = tuple(part.strip() for part in ddl.split(";") if part.strip())
    catalog: list[PhysicalCatalogRow] = [
        ("table", table, table, unit.replace("IF NOT EXISTS ", "", 1))
        for table, unit in zip(creation_order, units, strict=True)
    ]
    objects: list[tuple[str, str]] = [("table", table) for table, _ in columns]
    table_info: list[tuple[str, tuple[ColumnInfo, ...]]] = []
    indexes: list[tuple[str, tuple[IndexInfo, ...]]] = []
    index_columns: list[tuple[str, tuple[IndexColumnInfo, ...]]] = []
    for table, cols in columns:
        table_info.append(
            (
                table,
                tuple(
                    (i, name, kind, null, None, pk) for i, (name, kind, null, pk) in enumerate(cols)
                ),
            )
        )
        if table == "store_metadata":
            indexes.append((table, ()))
            continue
        name = "sqlite_autoindex_" + table + "_1"
        objects.append(("index", name))
        catalog.append(("index", name, table, None))
        indexes.append((table, ((0, name, 1, "pk", 0),)))
        pk_cols = sorted(
            ((i, col) for i, col in enumerate(cols) if col[3]), key=lambda item: item[1][3]
        )
        index_columns.append(
            (
                name,
                (
                    *tuple(
                        (seq, cid, col[0], 0, "BINARY", 1) for seq, (cid, col) in enumerate(pk_cols)
                    ),
                    (len(pk_cols), -1, None, 0, "BINARY", 0),
                ),
            )
        )
    return (
        ddl,
        tuple(sorted(objects)),
        tuple(sorted(catalog)),
        tuple(table_info),
        tuple(indexes),
        tuple(sorted(index_columns)),
    )


def ordered_actions(rows: Iterable[SqlAction]) -> tuple[SqlAction, ...]:
    return tuple(
        sorted(set(rows), key=lambda row: tuple((item is not None, item or "") for item in row))
    )


def reads(table: str, columns: Iterable[str]) -> tuple[SqlAction, ...]:
    return tuple(("READ", table, column, "main", None) for column in columns)


SELECT: SqlAction = ("SELECT", None, None, None, None)
BEGIN: tuple[SqlAction, ...] = (("TRANSACTION", "BEGIN", None, None, None),)
_PUBLICATION = (
    "commit_sequence",
    "idempotency_key",
    "operation_kind",
    "record_ids",
    "request_fingerprint",
    "tenant_id",
)
_SELECTION = ("commit_sequence", "idempotency_key", "operation_kind", "tenant_id")
_RECORD = ("canonical_bytes", "commit_sequence", "owner", "record_id", "schema_id", "tenant_id")


def expected_actions(
    qualname: str, ordinal: int, label: str, *, initial_case: str = "REGISTERED", unit: int = 0,
) -> tuple[SqlAction, ...]:
    """Independent finite semantic action contracts; no candidate SQL is parsed."""
    if qualname == "BrokerAuthorityReader._execute_planning_read":
        if ordinal == 39:
            return ordered_actions(
                (
                    SELECT,
                    ("FUNCTION", None, "count", None, None),
                    *reads("publications", ("commit_sequence", "tenant_id")),
                )
            )
        if ordinal == 44:
            return ordered_actions((SELECT, *reads("publications", _PUBLICATION)))
        if ordinal == 48:
            return ordered_actions(
                (SELECT, *reads("publications", _SELECTION), *reads("records", _RECORD))
            )
    if qualname == "BrokerAuthorityReader.execute":
        if ordinal == 9:
            return BEGIN
        if ordinal == 24:
            return ordered_actions((SELECT, *reads("tenant_heads", ("head", "tenant_id"))))
    if qualname == "_schema_members":
        if ordinal == 4:
            return ordered_actions(
                (
                    SELECT,
                    ("FUNCTION", None, "glob", None, None),
                    *reads("sqlite_master", ("name", "type")),
                )
            )
        if ordinal == 12:
            (table,) = (t for t, _, _ in AMR_MEMBERS if label == repr((("table", t),)))
            return (("PRAGMA", "table_info", table, "main", None),)
    if qualname == "_authority_snapshot_bytes" and ordinal == 12:
        (columns,) = (
            c for t, c, authority in AMR_MEMBERS if authority and label == repr((("table", t),))
        )
        (table,) = (
            t for t, _, authority in AMR_MEMBERS if authority and label == repr((("table", t),))
        )
        return ordered_actions((SELECT, *reads(table, columns)))
    if qualname == "BrokerReadLedger.__init__" and ordinal == 16:
        table = ("authority_read_state", "authority_read_releases")[unit - 1]
        base: tuple[SqlAction, ...] = (
            ("CREATE_TABLE", table, None, "main", None),
            ("INSERT", "sqlite_master", None, "main", None),
        )
        if initial_case == "EXISTING":
            return ordered_actions(base)
        if initial_case != "EMPTY":
            raise ValueError("unknown schema initial case")
        pk_checks = (
            ("tenant_id",)
            if unit == 1
            else ("tenant_id", "read_attempt_id", "state", "enqueued", "dequeued")
        )
        return ordered_actions(
            (
                *base,
                ("CREATE_INDEX", "sqlite_autoindex_" + table + "_1", table, "main", None),
                *reads(table, pk_checks),
                *reads("sqlite_master", ("ROWID",)),
                *(
                    ("UPDATE", "sqlite_master", c, "main", None)
                    for c in ("type", "name", "tbl_name", "rootpage", "sql")
                ),
            )
        )
    if (qualname, ordinal) in {("BrokerReadLedger.release", 10), ("BrokerReadLedger.dequeue", 4)}:
        return BEGIN
    if qualname == "BrokerReadLedger.current_state_or_none" and ordinal == 7:
        return ordered_actions(
            (
                SELECT,
                *reads(
                    "authority_read_state", ("canonical_state", "state_fingerprint", "tenant_id")
                ),
            )
        )
    if qualname == "BrokerReadLedger.begin" and ordinal == 6:
        return (("INSERT", "authority_read_releases", None, "main", None),)
    if qualname == "BrokerReadLedger.release" and ordinal == 13:
        return ordered_actions(
            (SELECT, *reads("authority_read_state", ("state_fingerprint", "tenant_id")))
        )
    if qualname == "BrokerReadLedger.release" and ordinal == 16:
        return ordered_actions(
            (
                SELECT,
                *reads(
                    "authority_read_releases",
                    (
                        "request_fingerprint",
                        "initial_state_fingerprint",
                        "state",
                        "result_digest",
                        "recipient_fingerprint",
                        "proof_fingerprint",
                        "enqueued",
                        "dequeued",
                        "tenant_id",
                        "read_attempt_id",
                    ),
                ),
            )
        )
    if qualname == "BrokerReadLedger.dequeue" and ordinal == 7:
        return ordered_actions(
            (
                SELECT,
                *reads(
                    "authority_read_releases",
                    (
                        "result_bytes",
                        "recipient_fingerprint",
                        "state",
                        "enqueued",
                        "dequeued",
                        "tenant_id",
                        "read_attempt_id",
                    ),
                ),
            )
        )
    if (qualname, ordinal) in {
        ("BrokerReadLedger.release", 29),
        ("BrokerReadLedger.release", 31),
        ("BrokerReadLedger.dequeue", 13),
    }:
        updates, predicates = {
            ("BrokerReadLedger.release", 29): (
                ("state", "result_digest", "result_bytes", "proof_fingerprint", "enqueued"),
                ("tenant_id", "read_attempt_id", "state"),
            ),
            ("BrokerReadLedger.release", 31): (
                ("state", "proof_fingerprint"),
                ("tenant_id", "read_attempt_id", "state"),
            ),
            ("BrokerReadLedger.dequeue", 13): (
                ("dequeued",),
                ("tenant_id", "read_attempt_id", "dequeued"),
            ),
        }[qualname, ordinal]
        return ordered_actions(
            (
                *reads("authority_read_releases", predicates),
                *(("UPDATE", "authority_read_releases", c, "main", None) for c in updates),
            )
        )
    raise ValueError("unknown selected semantic identity")
