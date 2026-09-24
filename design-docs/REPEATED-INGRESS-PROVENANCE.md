# Repeated ingress: revised implementation plan

Goal: distinct messages and independent derived rows with identical bytes remain
usable through ingress, conversation history, workspace context and retained
screens, without allowing source substitution or incomplete disclosure closure.

Assumptions:
- Content digest authenticates bytes, not provenance identity.
- Existing conversation and screen serialization must remain byte-for-byte valid.
- An authenticated owner query is a closure producer; a candidate envelope is not.
- Source subject transport alone is not authority.

Plan:
1. Reproduce two distinct legacy R14 Runs with prompt Plan against published main.
   Preserve the failing command and result. Add a lasting regression in composition
   with duplicate inputs, history/context and reopen once implementation starts.
2. Introduce an explicit provenance subject value (tenant, producer/query family,
   record identity, revision). Prefer an additional guard argument for immediate
   entry/row validation, retaining the legacy DisclosureEnvelope wire unchanged.
   The caller derives the argument from the enclosing authenticated entry or row;
   candidate envelope sources never select the subject. Namespace queries so row
   IDs in different families cannot alias. Registry keys include subject and digest.
3. Seed principal closures from authenticated ingress registrations, matching exact
   entry ID to principal_ingress record ID and revision; resolve legacy entries by
   enclosing entry identity as well. For assistant rows derive closure from
   every manifest member in every historical attempt of every turn of the selected
   CompleteAcceptance Run and independently verify the original
   conversation companion membership and accepted bytes. Do this before history
   guard checks; never bootstrap from the stored conversation envelope sources.
   Distinguish legacy and executable Run schemas using registered historical readers.
4. Register query-produced complete closures at explicit trusted owner-query return
   sites. Validate enclosing row subject against that registration before rendering.
   Change both R12 _Sources and R3SourceHeads; unknown explicit subjects never fall
   back to digest search. Legacy callers without subject remain admissible only
   with a unique independently known closure.
5. Retained screens need immutable subject bindings. Add a separate versioned
   ScreenSnapshotV2, not a default field on legacy ScreenSnapshot. V2 includes the
   complete ordered row subjects aligned with envelopes, and includes them in its
   snapshot identity preimage. Creation consumes already verified owner rows.
   Stored/replayed V2 subjects must match independently registered owner closures.
   Add a dedicated issuance journal using the existing independently authenticated
   IndependentTenantDecisionJournal, bound to the same AuthorityGate in composed
   runtimes. Its selected record retains exact canonical WorkspaceState bytes,
   tenant, channel and sequence, thereby binding every ordered snapshot and subject.
   Only the workspace path after owner-row validation may select issuance; loading
   or saving arbitrary derivative cache bytes must never mint an issuance record.
   Under the gate, select with expected prior workspace sequence and journal head,
   durably append before cache materialization, then save SQLite derivative bytes.
   A crash in between leaves selected original state recoverable from the journal;
   it must not allow a different state at the same sequence. Recovery materializes
   exact retained bytes without rerunning owner producers or changing the cut.
   Every V2 replay/release checks exact selected state membership, including empty
   and calendar screens. Local cached bytes altered together with their digest must
   fail; missing cache may be rebuilt from the retained authenticated state.
   Fresh runtime still requires fresh context and rebuild for actual content release;
   journal authenticity does not renew old context or override disclosure checks.
   Pin sidecar identity and include missing/key/head/body substitution, rollback and
   crash behavior in the adapter tests. Reuse the existing independent trust root
   assumptions; do not claim protection against replacement of the entire root.
   Keep legacy snapshot decoding and identity computation unchanged; legacy screens
   with ambiguous independent closure fail closed and are rebuilt from owner reads.
6. Adapt public ports, actual producers, screen store codecs, source inventory and
   compiled disclosure inventory where signatures change. Do not widen historical
   schemas by defaults or silently rewrite retained rows. Keep each semantic commit
   under the path gate without using unsupported intermediate runtime routes.
7. Tests: duplicate ingress and restart; principal versus assistant same output;
   same-byte different-label sources; substitute both subject and envelope sources;
   missing unrestricted source from a derived closure; unknown/wrong tenant, owner,
   revision and digest; two complete derivations with same bytes; retained-screen
   binding substitution; exact old conversation/snapshot fingerprint fixtures.
   Run affected real R12/R13/R14 paths, source inventory, types and normal gates.

Root retains ownership and will inspect all implementation and test results.
This is a corrected plan for cold review, not an implementation or completion claim.

## Current evidence and remaining work

The corrected plan received zero remaining critical findings in cold review.
The existing independent journal fails closed on interrupted body/head publication;
cache recovery applies only after successful durable issuance.

`uv run pytest -xq tests/composition/test_repeated_ingress.py` reproduced the
second identical-message rejection on the published baseline. Both duplicate
principal messages and identical principal/assistant output now pass the real R14
path and reopen checks; regression verification of the final tree is in progress.

R13/R14 now reconstruct complete conversation bindings from original selected
publications before R12 history reads. CompleteAcceptance includes both the legacy
transition and registered captured-fanout publication. Every historical attempt
member is retained. Legacy assistant source ordering remains in original bytes;
the new subject-bound read projection sorts without removing any member and checks
against the independently reconstructed closure.

V2 screens retain ordered subjects; an independently authenticated issuance journal
selects the exact complete workspace state before derivative cache writes. Replay
and predecessor reuse check that issuance. Recovery materializes only a missing
cache suffix from original selected bytes, never altered cache data or owner reissue.
R13 uses a separate `.screens.v2.sqlite` derivative file; legacy files remain intact.
The legacy fixture was generated by published bf42f45 and must roundtrip byte-for-byte.

For V2 conversation screens, physical derivative registration uses original
conversation entry IDs, not the virtual visibility-member IDs inside assistant
manifests. Full disclosure closure remains in the envelopes and issuance. A cold
review found no blocker for this fixed-fence hermetic runtime. This is not a
transitive deletion graph for future targeted deletion with renewed fences; such
a runtime requires an authenticated owner dependency resolver.

Final source admission, full regression, staged cold review and normal commit
gate remain required. This fixes a provenance boundary, not the full R17 milestone.

The first normal gate rejected the canonical CLI import closure: provenance had
pulled fanout command construction and raw-command readback into a read path.
The existing canonical-entrypoint test reproduced the failure independently.
Fanout semantic verification now resides separately from physical command
conversion and lifecycle inventory. Provenance compares every command field,
ordered member and default fault/guard against that verified envelope.
Physical inspection uses distinct read-only expectations, with no writer guards
or raw writer imports. The existing raw-command readback API retains nominal
type checks and delegates to the same validated SQL comparison. No R7 allowlist
or gate was weakened. Existing semantic helper and inventory ASTs are unchanged;
new malformed-expectation tests exercise rejection before any SQL lookup.

Integration with published executable lifecycle and R16 outcomes preserves both
branches. The only textual conflict was the source catalog; its three-way merge
retains each branch's independently changed entries and pins the reviewed combined
workspace module. The workspace keeps `RunRecord | ExecutionRunRecord`; executable
fanout has no assistant conversation companion and cannot be interpreted as legacy
CompleteAcceptance. Exact Python inventory and every source hash were verified.

The mixed-history witness creates a consequential execution, captures and seals its
fanout, then accepts the same ingress text for another execution. Context rendering
and reopening preserve both distinct original ingress sources and the initialized
call. `uv run pytest -xq tests/composition/test_repeated_ingress.py` passed all three
tests in 12.31 seconds; `uv run mypy src tests` passed for 507 files, and targeted
ruff passed. Combined regression, cold staged review and the normal merged-tree
gate remain required before publication; this does not close full R14-R17 scope.
