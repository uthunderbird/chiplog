# R10 journal contracts

Plan: freeze owner DTOs and public ports; validate consumer imports; implement journal owner and adapter using existing EventAppender; execute component vectors and combined checks.

Assumptions: isolated component wiring, no R12 promotion; independently supplied trusted ingress registry is hermetic transport authentication; tenant-global CAS is conservative current-head validation. No Plan mutation port exists in journal. Closed grammar supports `Я выполнил occurrence:<id> <YYYY-MM-DD>.` only; other forms require immutable confirmation.

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Evidence |
| --- | --- | --- | --- | --- | --- |
| Principal only | independently registered peer and exact ingress bytes | physical journal records | request principal or model parser | foreign peer, provider, all negative speech acts | J (current selectors below) |
| Exact confirmation | persisted display, all heads and canonical digest | SQL bytes, replay result | generic assent or mutable position | each binding substitution, head race, duplicate | J (current selectors below) |
| Current lineage | immutable family subject and predecessor | public lineage and physical CAS | timestamp winner or subject replacement | rivals, stale predecessor, retract | J (current selectors below) |
| No Plan writes | journal-only canonical batch | physical owner rows | response text assertion | direct and confirmed facts | J, R (current selectors below) |

Mutation coverage: omission/addition/unknown and substitution use strict frozen DTOs and fingerprints; duplicate/reorder uses complete canonical arrays and stable command ID; stale/race uses tenant-global head plus commit guard; logical/physical identity validates tenant/record IDs and digests. N/N+1 applies to the public JournalQueryPort: max_rows bounds the complete returned lineage closure; an indivisible closure above the limit returns INDETERMINATE with no rows. Cursors use strict record-ID ordering at the same exact Heads and cannot change snapshot. Unknown cursors reject. R12 owns composition with conversation, not this owner query implementation.

Public boundary: commands.py owns records, authentication/persistence protocols and async commands/query port; adapter consumes public ports only. Canonical bytes are owner authored; downstream may fill only physical sequence. Failure variants: COMMITTED, REPLAY, CONFLICT, STALE, DENIED, INDETERMINATE, NEEDS_CONFIRMATION, UNRESOLVED. Requirement map A04/A22/A30–A32 → current selectors below; authored T02 version 2 before executable use. Component behavior evidence is executed below; production integration/promotion remains HOLD.



## Current test selectors after placement migration

The historical commands below retain their original paths and reported results;
they are records carried by pre-migration commit
`d6339eed2bc19c92d126bda3aef3a0db0025a143`, not commands rerun on the new layout.
The current combined migration run passed 197 items; the audit compared all 591
setup/call/teardown records, including skip/xfail. Exact commands, full nodeid pairs,
and observed outcomes are in `quality/test-migration-r9-r11.json`.
This placement evidence does not grant runtime or deployment promotion.

| Key | Current selector | Scope |
| --- | --- | --- |
| J | `tests/capabilities/evidence_journal/test_journal.py` | Owner commands, confirmation, lineage, query and integrity |
| R | `tests/composition/test_journal_restart_and_plan.py` | Component reopen in the same process; existing Plan byte preservation |


## Historical evidence and precise limitations (pre-migration record)

Public shape import succeeded before business logic. `uv run pytest tests/r10 tests/evaluation/test_transcript_compiler.py tests/architecture/test_r9_r11_contract.py -q` checks component semantics, authored transcript compatibility and common DTO schemas. `uv run mypy src/chiplog/capabilities/evidence_journal/commands.py src/chiplog/capabilities/evidence_journal/journal.py src/chiplog/adapters/driven/journal_sqlite.py src/chiplog/composition/r10.py tests/r10` and the equivalent `uv run ruff check` pass.

Exact principal confirmation uses separately injected transport ConfirmationIngress identity plus full command digest; a command DTO and an authenticated peer alone cannot invent assent. Transport fixture injection is absent from JournalPort. Current identity, ingress provenance and source label registry are independently installed in hermetic composition. The public owner command boundary revalidates nested constructed DTOs, never trusts Pydantic construction provenance. Full per-predicate direct proof is persisted in the owner record.

Physical records are published exclusively through existing EventAppender. Owner canonical bytes have a SHA256 binding in the publication row; authoritative reads verify complete publication membership, canonical bytes, digest, tenant/record identity and exact deletion fence inside the same transaction as tenant head. This catches valid JSON record tampering and missing rows. It does not claim protection against an adversary rewriting both record bytes and independent publication binding; production broker authority commitments remain the R12 integration boundary.

Query status reduction exposes user reported, provider observed, user confirmed provider observation, disputed, corrected, retracted and unknown without timestamp selection. Candidate source records and exact superseded display references remain in the full lineage closure. Fresh exact displayed supersedes sets resolve rivals; consumed historical candidates do not block later correction/retraction. No REPLACE command exists. Disclosure source identity/version/digest/label-head and joined labels are independently validated at publication and ordinary release; missing/unverifiable lineage releases nothing. Content-bearing surfaces for R9 registry: JournalPort.execute/prepare Outcome.record, JournalPort.lineage and JournalQueryPort.project JournalView.rows.

The no-Plan fixture creates a real R5 planning publication via the public R6 composition, then performs direct and confirmed fact, correction and retraction in the same database and compares the pre-existing planning canonical bytes after each action. T02 version 2 explicitly displays the negative claim and waits for exact confirmation; affirmative grammar has a separate fixture. Full orchestration transcript execution remains R18/R19.

Cold review found and reproduced two P1 defects: unconfirmed candidates affected
personal-claim dispute status, and candidate supersession could cross principals.
The correction separates candidate and authoritative conflict axes, validates every
superseded candidate's principal/subject/predecessor, and verifies principal ownership
through complete projection lineage. At that historical repair checkpoint, both original witnesses rejected the forbidden
outcome; cold recheck reports zero remaining P0/P1 in that scope.
`uv run pytest -q tests/r10` → 40 passed after correction; targeted Ruff and mypy pass.
