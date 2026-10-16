"""Same-snapshot selected semantic evidence; expectations remain UNREGISTERED."""

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from chiplog.verification.authority_sql_corpus import CorpusNode, CurrentSqlCorpus
from chiplog.verification.selected_engine_contracts import (
    AMR_MEMBERS,
    EXPECTED_SQL,
    expected_actions,
    schema_contract,
)
from chiplog.verification.sql_expression_inventory import (
    SqlExpressionInventory,
    SqlExpressionResolution,
)
from chiplog.verification.sqlite_preparation import (
    ColumnInfo,
    PhysicalSchemaObservation,
    SqlAction,
    SqlPreparation,
    observe_registered_schema,
    observe_statement,
)

if TYPE_CHECKING:
    from chiplog.verification.planning_descriptor_chain import (
        PlanningDescriptorRow,
        ReaderScannerChain,
        ReadLedgerOriginChain,
    )


class SelectedEngineRefused(ValueError):
    pass


@dataclass(frozen=True)
class SelectedDomainSchemaEvidence:
    domain: str
    contract_id: str
    initial_case: str
    source_digests: tuple[tuple[str, str], ...]
    observation: PhysicalSchemaObservation


@dataclass(frozen=True)
class SelectedEngineEvidence:
    site: CorpusNode
    alternative_label: str
    unit_ordinal: int
    initial_case: str
    domain: str
    role: str
    expression_dependency_digests: tuple[tuple[str, str], ...]
    schema_contract_id: str
    runtime_recipe_id: str
    observer_recipe: str
    preparation: SqlPreparation
    business_reads: tuple[tuple[str, str, str], ...]
    catalog_reads: tuple[tuple[str, str, str], ...]
    metadata_requests: tuple[tuple[str, str, str | None], ...]
    metadata_schema_case: str | None
    metadata_result: tuple[ColumnInfo, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SelectedEngineRefused(message)


def verify_domain_schema(row: SelectedDomainSchemaEvidence) -> None:
    ddl, _, catalog, columns, indexes, index_columns = schema_contract(row.domain)
    observed = row.observation
    initial = ddl if row.initial_case == "EXISTING" else ""
    _require(
        row.initial_case in {"EMPTY", "EXISTING"}
        and (row.domain != "tenant_main" or row.initial_case == "EMPTY"),
        "unknown selected schema initial case",
    )
    _require(
        observed.schema_sha256 == sha256(ddl.encode()).hexdigest()
        and observed.initial_schema_sha256 == sha256(initial.encode()).hexdigest()
        and observed.foreign_keys is False
        and observed.recipe == "private-main-memory/cache=0/autocommit/sequential-create/metadata"
        and bool(observed.sqlite_version),
        "selected physical schema identity/profile differs",
    )
    _require(
        (observed.catalog, observed.columns, observed.indexes, observed.index_columns)
        == (catalog, columns, indexes, index_columns),
        "selected physical schema differs from independent contract",
    )


def verify_preparation(
    observed: SqlPreparation, *, sql: str, domain: str, actions: tuple[SqlAction, ...],
    initial_schema_sql: str | None = None,
) -> None:
    """Semantic comparison ignores host version; canonical rederivation binds provenance."""
    ddl = schema_contract(domain)[0] if initial_schema_sql is None else initial_schema_sql
    _require(
        observed.sql == sql
        and observed.bindings == sql.count("?")
        and observed.foreign_keys is False
        and observed.initial_schema_sha256 == sha256(ddl.encode()).hexdigest()
        and bool(observed.sqlite_version),
        "selected SQL/schema/bindings/profile differs",
    )
    _require(observed.actions == actions, "selected SQL actions differ from independent contract")


def build_selected_engine_evidence(
    sources: tuple[tuple[str, str], ...],
    corpus: CurrentSqlCorpus,
    expressions: SqlExpressionInventory,
    planning_rows: Sequence[PlanningDescriptorRow],
    scanner_chains: tuple[ReaderScannerChain, ...],
    ledger_chains: tuple[ReadLedgerOriginChain, ...],
    tenant_schema: str,
) -> tuple[tuple[SelectedDomainSchemaEvidence, ...], tuple[SelectedEngineEvidence, ...]]:
    texts = dict(sources)
    registry = ast.parse(texts["src/chiplog/architecture/r7_storage_surface.py"])
    assignments = [n.value for n in registry.body if isinstance(n, ast.Assign)
                   and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                   and n.targets[0].id == "AUTHORITY_STORAGE_MEMBERS"]
    fixed_members = ast.parse("(" + ",".join(
        f"StorageMember({table!r}, {columns!r}, {authority!r})"
        for table, columns, authority in AMR_MEMBERS) + ",)", mode="eval").body
    _require(len(assignments) == 1 and ast.dump(assignments[0]) == ast.dump(fixed_members),
             "candidate AMR differs from independent member contract")
    contracts = {
        domain: schema_contract(domain) for domain in ("tenant_main", "broker_read_ledger")
    }
    (schema_chain,) = (c for c in ledger_chains if c.role == "SCHEMA_SCRIPT")
    (schema_alternative,) = schema_chain.expression.alternatives
    candidates = {"tenant_main": tenant_schema, "broker_read_ledger": schema_alternative.sql}
    for domain, candidate in candidates.items():
        _require(
            candidate == contracts[domain][0],
            "candidate DDL differs from independent " + domain + " contract",
        )
    domain_schemas: list[SelectedDomainSchemaEvidence] = []
    for domain, schema_cases, paths in (
        (
            "tenant_main",
            ("EMPTY",),
            ("src/chiplog/platform/_sqlite.py", "src/chiplog/architecture/r7_storage_surface.py"),
        ),
        ("broker_read_ledger", ("EMPTY", "EXISTING"), ("src/chiplog/platform/read_ledger.py",)),
    ):
        ddl, objects, *_ = contracts[domain]
        for case in schema_cases:
            observation = observe_registered_schema(
                candidates[domain],
                initial_schema_sql=ddl if case == "EXISTING" else "",
                registered_objects=objects,
                foreign_keys=False,
            )
            row = SelectedDomainSchemaEvidence(
                domain,
                "UNREGISTERED/" + domain + "/v1",
                case,
                tuple(pair for pair in corpus.source_digests if pair[0] in paths),
                observation,
            )
            verify_domain_schema(row)
            domain_schemas.append(row)
    resolutions = {r.site: r for r in expressions.resolved}
    selected: list[tuple[CorpusNode, str, str, SqlExpressionResolution]] = []
    seen: set[CorpusNode] = set()
    for planning_row in planning_rows:
        if planning_row.site not in seen:
            selected.append((planning_row.site, "tenant_main", "PLANNING_READ",
                             resolutions[planning_row.site]))
            seen.add(planning_row.site)
    chains: tuple[ReaderScannerChain | ReadLedgerOriginChain, ...] = (
        *scanner_chains, *ledger_chains)
    for chain in chains:
        selected.append((chain.sink, chain.domain, chain.role, chain.expression))
    actual_sql = tuple(
        (site.qualname, site.ordinal, alternative.label, alternative.sql)
        for site, _, _, expression in selected
        for alternative in expression.alternatives
    )
    _require(
        len(actual_sql) == len(EXPECTED_SQL) and set(actual_sql) == set(EXPECTED_SQL),
        "selected SQL alternatives/labels differ from independent contract",
    )
    evidence: list[SelectedEngineEvidence] = []
    for site, domain, role, expression in selected:
        ddl, objects, *_ = contracts[domain]
        for alternative in expression.alternatives:
            if role == "SCHEMA_SCRIPT":
                cases = [
                    (row.initial_case, row.observation.units)
                    for row in domain_schemas
                    if row.domain == domain
                ]
            else:
                cases = [
                    (
                        "REGISTERED",
                        (
                            observe_statement(
                                alternative.sql,
                                registered_schema_sql=candidates[domain],
                                registered_objects=objects,
                                foreign_keys=False,
                            ),
                        ),
                    )
                ]
            for case, units in cases:
                _require(
                    len(units) == (2 if role == "SCHEMA_SCRIPT" else 1),
                    "selected schema units missing or duplicated",
                )
                for ordinal, preparation in enumerate(units, 1):
                    unit = ordinal if role == "SCHEMA_SCRIPT" else 0
                    expected_sql = alternative.sql
                    initial = None
                    if role == "SCHEMA_SCRIPT":
                        # Split only the independent reviewed ledger literal; never candidate SQL.
                        fixed_units = tuple(part + ";" for part in ddl.split(";")[:-1])
                        expected_sql = fixed_units[unit - 1]
                        initial = ddl if case == "EXISTING" else ""
                    actions = expected_actions(
                        site.qualname, site.ordinal, alternative.label, initial_case=case, unit=unit
                    )
                    verify_preparation(
                        preparation,
                        sql=expected_sql,
                        domain=domain,
                        actions=actions,
                        initial_schema_sql=initial,
                    )
                    business = tuple(
                        sorted(
                            (db, table, column)
                            for action, table, column, db, _ in preparation.actions
                            if action == "READ"
                            and db == "main"
                            and table is not None and column is not None
                            and table != "sqlite_master"
                            and role != "SCHEMA_SCRIPT"
                        )
                    )
                    catalog = tuple(
                        sorted(
                            (db, table, column)
                            for action, table, column, db, _ in preparation.actions
                            if action == "READ" and table == "sqlite_master"
                            and db is not None and column is not None
                        )
                    )
                    requests = tuple(
                        (first, second, db)
                        for action, first, second, db, _ in preparation.actions
                        if action == "PRAGMA" and first is not None and second is not None
                    )
                    metadata: tuple[ColumnInfo, ...] = ()
                    metadata_case: str | None = None
                    if role == "SCHEMA_COLUMN_METADATA":
                        _require(len(requests) == 1, "missing selected metadata request")
                        table = requests[0][1]
                        (main_schema,) = (r for r in domain_schemas if r.domain == "tenant_main")
                        metadata = dict(main_schema.observation.columns)[table]
                        # EXPLAIN proves this request only. Results come from the
                        # identical table_info SQL in the domain's physical observer.
                        metadata_case = main_schema.initial_case
                    evidence.append(
                        SelectedEngineEvidence(
                            site,
                            alternative.label,
                            unit,
                            case,
                            domain,
                            role,
                            expression.source_digests,
                            "UNREGISTERED/" + domain + "/v1",
                            (
                                "reader/ro-uri/autocommit/explicit-BEGIN"
                                if domain == "tenant_main"
                                else "ledger/default-connect/context-transactions/executescript"
                            ),
                            (
                                "private-main-memory/cache=0/autocommit/sequential-create"
                                if role == "SCHEMA_SCRIPT"
                                else "private-main-memory/cache=0/autocommit/EXPLAIN"
                            ),
                            preparation,
                            business,
                            catalog,
                            requests,
                            metadata_case,
                            metadata,
                        )
                    )
    return tuple(domain_schemas), tuple(evidence)
