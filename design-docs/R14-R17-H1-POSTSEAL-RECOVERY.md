# H1 post-seal recovery contract for the integrated R14–R17 path

Status: CONTRACT FROZEN, TESTS/IMPLEMENTATION OPEN. This contract extends the
[integrated contract graph](R14-R17-INTEGRATED-CONTRACTS.md) at the H1 V2 seal →
four owner preparations boundary. It does not establish selected publication,
external delivery, or completion of R14–R17.

## Authority and identity

The installed runtime alone owns the enrolled recovery mount, authenticated
global recovery journal, historical V2 native selector, and recovery coordinator.
An arbitrary path, journal record, root DTO, or copied B session grants no
authority. The coordinator derives a complete `H1PostSealRecoveryRootV1` from
the selected physical V2 seal and its bounded original Run lineage. On every
start or restart it scans the full authenticated journal prefix and compares
**every** stored ROOT field with that independent derivation. A missing ROOT is
appended with the global journal tip as CAS predecessor and exact readback
before B opens. A competing ROOT, malformed prefix, changed source, uncertain
readback, or mount replacement holds the workflow. An uncertain append requires
reopening and reconciling the enrolled journal; blind retry is forbidden.

The selected V2 prefix is bounded at the seal. Later committed unrelated
publications must not change the derived root. Any `PENDING` loop-journal
decision holds historical selection until its outcome is resolved. Neither a
V3 checkpoint nor the current owner inventory substitutes for the V2 native
source. Missing retained native bytes hold recovery.

Full historical COMPLETION reconstruction additionally requires the explicit
[`h1_v2_authority_cut_v1` extension](R14-R17-H1-PRESEAL-PE-ANCHOR.md#versioned-v2-authority-cut).
It is a versioned sibling in the same authenticated V2 DECIDED entry as the
P/E anchor: the predecessor commitment and an `AuthorityCheckpointRefV1`
retain the exact writer post-image and resulting commitment, bound to the
native seal, exact command/envelope, database identity and head. No predecessor
snapshot bytes are required. Exactly three physical seal members remain;
neither the retained V2 seal nor its physical envelope becomes V3.

This extension supplements independent native ROOT derivation with the
complete historical authority rows needed by `FirstPathCompletionCutV2`.
Readers must validate its explicit version, bindings, immutable bytes,
selected publication membership, complete inventory and owner-as-of joins.
Neither a V3 checkpoint descriptor nor current rows may substitute. Legacy
V2 without the extension remains unsupported for full-cut historical
completion, even where native ROOT selection succeeds. A present malformed,
unknown-version or inconsistent descriptor is an integrity failure.

Pending recovery validates the authenticated extension and blob before exact
materialization/replay, preserves both DECIDED siblings, reconciles the
recorded predecessor/resulting, and finishes the original decision before
historical selection. It never recaptures a historical cut or P/E facts.
Selected missing/corrupt blobs hold recovery; unselected staged blobs grant
no authority. Later committed publications cannot alter the selected cut.
These are newly frozen obligations; the existing V2 no-checkpoint readers
require explicit implementation changes, not silent V3 relabeling.

## Execution ownership and stage records

One private, task-owned, cross-process execution fence covers ROOT readback,
fresh B enrollment, all preparation calls, and result readback. It is separate
from `AuthorityGate`: the gate protects short synchronous source checks and
journal CAS; it is never held across `await` or owner IPC. The execution fence
uses a separate enrolled inode from the recovery journal's primitive `.lock`;
the role marker binds its identity and runtime opening never creates it.
Acquisition is nonblocking and cancellable, with exact task ownership and
fresh file descriptions. It is non-expiring and released only after the
coordinator has drained its broker worker or confirmed owner termination;
canceling the coroutine alone does not stop a broker executor thread. Runtime
teardown drains active recovery before closing the journal or mount. Process death releases
the local fence. A late orphaned owner preparation may finish, but cannot
append a result or obtain publication authority. No second live B issuer may
run for the same installed recovery role while the fence is owned.

The stage order is `COMPLETION → CONVERSATION → EFFECTS → TERMINAL_WORK`.
Before any owner call, the coordinator constructs the complete canonical
semantic request from authenticated source and prior exact results, appends
`STAGE_INPUT`, and verifies readback. On restart and before a fully durable
shortcut, the coordinator independently reconstructs every stage request from
the verified native ROOT source and independently validated predecessor
results. It compares complete canonical bytes with each existing input; a
mismatch holds even when the input and result records are internally well
formed. An existing input is immutable: recovery uses its exact bytes only
after this comparison. After a real broker exchange, the coordinator
validates the full canonical result, appends `STAGE_RESULT` by global-tip CAS,
and verifies readback before advancing. A durable result is evidence, not a B
exchange: a fresh B session replays earlier inert preparations in order,
compares complete result bytes, and retains its own live sent/returned frame
identities. A mismatch holds and revokes the continuation. The four preparation
operations are inert; their replay may execute twice across process death.
These records do not authorize an external SEND or selected publication.

Effects `STAGE_INPUT` stores the complete canonical inner
`PrepareH1LocalCommentaryV1`, including its `CommandIdentity`, selected scope,
retained origin, fence, final intent and complete predecessor evidence.
`effects_command_id` equals that inner command ID. The input is pinned after
final intent derivation and before IPC. On restart, the existing manifested
`effects.prepare_h1_local_commentary` operation may be used with fresh broker
epoch, generation, sessions and budget, while both transport request IDs equal
the pinned inner command ID. The outer route/frame changes; the inner request
and pure result bytes must remain equal. An installed B-only, one-use recovery
authorization binds the full independently verified ROOT, exact input bytes and
digest, predecessor, current continuation/lease and broker session. It is
consumed before send. The broker admission boundary validates the fresh nested
route against the actual frame and checks current source authority. An old
outer frame, caller-shaped DTO, changed inner ID, or stale authorization is
rejected before IPC. The existing manifested wire edge need not change for
this contract; a new edge requires separate evidence that its privilege is
needed.

For Effects reconstruction, the pinned inner command ID is the stable seed,
not a substitute for validation. The coordinator recomputes its fingerprint,
intent, scope, origin, fence and predecessor joins from independent selected
sources, then requires equality with the entire pinned request before replay
or a completed-state shortcut.

## Driver and failure behavior

After a durable H1 V2 seal, installed `advance_execution` derives the exact
selected seal locator and enters recovery before any B IPC. Exact replay of
the original command finds that seal from authenticated native history and
resumes incomplete ROOT/stages without repeating native advancement. It does
not infer the locator from the recovery journal: the seal may be committed
while ROOT is absent. Fully durable preparation returns the established
running receipt with `EXACT_REPLAY` and no new B call. A state that cannot
continue returns the existing HOLD/CONFLICT behavior with a precise cause;
it never claims terminal publication or fabricates an old current Run.
Cancellation drains/revokes the live continuation before releasing the
execution fence. The terminal admission guard and P currentness proof remain
mandatory even when all preparation stages are durable.

## Acceptance tests before implementation

Tests must first show the current missing behavior as RED and then prove:

1. Seal committed without ROOT recovers from the selected native prefix;
   forged but canonical ROOT fields, rival roots and missing native bytes hold.
2. An uncertain ROOT/input/result append is reconciled by reopening and
   scanning; no IPC begins before input readback or from a losing CAS branch.
3. Each crash cut resumes with exact pinned semantic bytes and full result
   comparison; fresh B exchange identities are observed after restart.
4. Effects replay across a fresh broker generation uses the same inner bytes,
   intent and result but new outer sessions; stale frames, random replacement
   IDs and caller-shaped recovery authorization are denied before send.
5. Two processes and cancellation cannot maintain competing live B issuers;
   late pure replies cannot append or publish after ownership loss. Journal
   scan/append inside the separately enrolled fence does not self-deadlock.
6. Exact driver replay performs no second native seal. Once four stages are
   durable, it performs no extra B/owner call and still reports only the
   established running receipt.
7. A digest-consistent but semantically substituted stage input and matching
   result under a valid ROOT are rejected, including on the fully durable
   no-IPC path.
8. The versioned V2 authority cut survives DECIDED-before-materialization and
   committed-before-finish crashes, and later unrelated publications leave
   the full historical cut and COMPLETION input byte-identical. Wrong cut
   bindings, missing/corrupt blobs and unknown versions hold; legacy V2
   without the extension cannot reconstruct full-cut completion. The physical
   seal still has exactly Run, seal and frontier members.

These tests observe authenticated journal bytes, physical publications,
actual broker frames and owner-call counts. A DTO roundtrip alone is not a
positive witness.
