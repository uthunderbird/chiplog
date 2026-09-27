# H1 post-seal recovery contract for the integrated R14–R17 path

Status: CONTRACT FROZEN, TESTS/IMPLEMENTATION OPEN. This contract extends the
[integrated contract graph](R14-R17-INTEGRATED-CONTRACTS.md) at the H1 V2 seal →
four owner preparations boundary and specifies separate installed finalization.
It does not establish implemented selected publication, external delivery, or
completion of R14–R17.

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
materialization/replay, preserves all required DECIDED siblings, reconciles the
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

## Historical later-stage source interfaces

CONVERSATION and EFFECTS additionally require the immutable
[`h1_preseal_p_scope_wires_v1` sibling](R14-R17-H1-PRESEAL-PE-ANCHOR.md#versioned-retained-p-scope-exchanges).
Its original accepted ISSUE/CURRENT payload pairs must match the P/E anchor's
existing exchange digests and the independently selected seal/command/database
bindings. The authority post-image and scope bytes cannot reconstruct these
pairs. Missing residual holds later-stage recovery as unsupported; malformed,
unknown-version or inconsistent present residual is an integrity failure.
These checks precede a fully durable shortcut as well as replay. Pending
recovery preserves the sibling exactly with the original DECIDED command;
no additional physical member or fresh CURRENT substitution is permitted.

The following are private installed-owner interfaces, not public wire edges.
P exposes `_issue_historical_recovery_source(*, original_identity:
DriverCommandIdentityV1, original_fingerprint: str, selected_seal:
CallSubjectHead) -> object`. These inputs are locators only. P independently
authenticates native history, authority cut, anchor, residual, original
workspace issuance and historical trust, then registers an opaque receipt by
exact object identity in that runtime. Copies, deserialization and caller
DTOs cannot issue authority. P exposes
`_replay_historical_conversation_policy(source_cap: object) ->
_AuthenticatedConversationPolicyInputs` and
`_replay_historical_effects_source(source_cap: object) ->
_AuthenticatedCompletionEffectsSource`. Each replay authenticates the same
immutable sources and compares its canonical projection to the issued one.
The returned private dataclasses are inert evidence, never caller authority.
These APIs do not replace the existing current-path methods.

A exposes `_prepare_historical_conversation_completion_request(*,
original_identity: DriverCommandIdentityV1, original_fingerprint: str,
selected_seal: CallSubjectHead, p_source_cap: object,
accepted_completion: object) -> PrepareConversationCompletionV1`.
`accepted_completion` is an opaque B-issued receipt whose request is
independently reconstructed and whose complete canonical result is validated.
A verifies its exact identity through the installed B issuer; arbitrary
request/result/native attributes are insufficient. Durable-result validation
may issue such evidence without IPC, but cannot issue an exchange identity.

A obtains the captured Run and selected/physical provenance from the full
historical native cut. It obtains the complete physical tenant inventory,
tenant sequence and commitment from the verified authority post-image, and
selected publication membership from the seal-bounded loop prefix and the
captured protected-owner-as-of prefix. It reconciles every snapshot row and
selected publication before deriving conversation history and the previous
entry. Native lineage members alone are not a complete conversation inventory.
Workspace policy and registration come from the original physical V2 workspace
issuance reached through selected Prepare; scope policy and recipient come
from the anchor. Original registration generation/digest must join the
anchor's custody coordinates, never current custody selection. A preserves
the existing conversation entry and command-seed derivation from these inputs
and the exact accepted completion request/result bytes.

P's effects projection contains `selected_scope`, `retained_origin` and
`fence`. The scope comes from anchor bytes joined to the authenticated
historical trust decision/record; `current_request` and `current_result` come
from the retained accepted CURRENT pair and its historical prefix. The
retained origin comes from selected H0/native initialization and admitted
authentication, compared with the retained ISSUE evidence. The non-scheduler
fence comes from the anchor's E worker joined to the sealed Run. B derives
intent and predecessor evidence from independently validated completion and
conversation results, then performs the complete pinned EFFECTS comparison
specified above. Historical CURRENT evidence is not current admission proof.

B's private pure builder is `_build_historical_terminal_work_request(*,
completion_request: PrepareExecutionCompletionFirstPathV2,
completion_result: PreparedExecutionCompletion,
effects_request: PrepareH1LocalCommentaryV1,
effects_result: PreparedH1LocalCommentaryV1) -> PrepareTerminalWork`.
Before invoking it, B validates the complete ordered predecessor chain,
including conversation. The builder checks canonical bytes, completion source
fingerprint, effects request/result fingerprint, and equality of the effects
request's original completion and prepared completion with those exact
predecessors. `AcceptedCompletionWorkSourceV1` contains the exact completion
request/result bytes, resulting terminal Run/head, terminal manifest/head and
its complete open obligations. Require zero obligations; terminal-work command
identity remains the terminal tenant and terminal manifest command ID. This
stage needs no further immutable residual and grants no terminal clearance.
Fresh terminal admission and P currentness remain separately mandatory.

## Driver and failure behavior

After a durable H1 V2 seal, installed `advance_execution` derives the exact
selected seal locator and enters recovery before any B IPC. Exact replay of
the original command finds that seal from authenticated native history and
resumes incomplete ROOT/stages without repeating native advancement. It does
not infer the locator from the recovery journal: the seal may be committed
while ROOT is absent. Fully durable preparation returns the established
running receipt with `EXACT_REPLAY` and no new B call. Caller authentication
through deployment trust still precedes this replay; retained identity and
fingerprint do not authorize access by themselves. A state that cannot
continue returns the existing HOLD/CONFLICT behavior with a precise cause;
it never claims terminal publication or fabricates an old current Run.
Cancellation drains/revokes the live continuation before releasing the
execution fence. This preparation operation does not mint completion issuance
or invoke finalization implicitly. Fresh terminal admission and P currentness
remain mandatory for the separate finalization operation below.

## Separate installed finalization

The installed coordinator exposes a separate private operation
`finalize_execution(original_identity: DriverCommandIdentityV1,
original_fingerprint: str) -> CommonExecutionResultV1`. Its arguments locate
original authenticated admission; they carry no publication authority. It does
not change the public `advance_execution` replay contract or accept a caller
batch, issuance, ROOT or selected-decision DTO. These are frozen obligations,
not a claim that finalization is implemented.

Finalization acquires the same enrolled recovery execution fence as preparation
before its initial owner-journal lookup and retains it through publication
reconciliation and actual writer settlement. This is mandatory for **every
positive H1 publication path**, including ordinary publication, finalization,
retry and exact selected recovery; no alternative entry point may bypass it.
The installed coordinator owns the task-bound lease; B enrollment and the
publication authority validate the exact installed source, context and lease,
including at writer admission. No competing B continuation or H1 publication
may run under the same installed recovery role. `AuthorityGate` still covers
short synchronous checks and writer admission, never an await or owner IPC.
Finalization independently derives
the native ROOT, scans the authenticated journal, reconstructs all four semantic
inputs and validates each complete durable result against its predecessors.
An incomplete chain holds until preparation completes.

Before fresh B replay, the publication owner independently locates the original
stable completion identity in authenticated history. Use the independently
validated ROOT's `publication_command_id` and `publication_command_fingerprint`, derived
from the original completion, never a fresh invocation identity; V1/V2 retries
retain that identity. Under the held lease, `OwnerDecisionJournal.lookup`
authenticates the journal and distinguishes selected from absent. This is an
absence proof only because all earlier admitted writers have settled and every
positive path obeys the same exclusion boundary. No separate in-flight identity
registry is required by this contract; an unfenced path invalidates that proof
and must remain unable to publish.

An already selected decision is reconciled/materialized and read back exactly,
then returned as `EXACT_REPLAY` without opening B, fresh CURRENT or minting
issuance. In particular, selected with absent physical materialization is still
selected: `lookup_exact` reporting HOLD for that state requires exact
`recover_selected`, not a new attempt. Recovery authenticates the retained
original version and decision, reconciles predecessor/resulting commitments,
and verifies exact physical bytes. Conflicting or unprovable materialization,
corrupt evidence and pending or uncertain outcomes hold before B; uncertainty
retains the existing uncertain/HOLD behavior. Only authoritative absence permits
a new attempt; a missing response or failed readback is not proof of absence.

With a validated complete chain and authenticated absence of selected or unresolved
completion, finalization opens fresh enrolled B and replays the four inert owner
preparations in canonical order. Requests retain their pinned semantic bytes but
use fresh admitted broker frames. Each actual result must equal its durable
canonical bytes exactly. Replay neither replaces stage records nor treats them
as live exchanges. The fourth call requires fresh terminal admission before IPC.
After its exact success, P obtains a distinct fresh final CURRENT and rechecks
source currentness after the await. Historical CURRENT and the preterminal check
cannot substitute for this post-terminal read. P must authenticate the recovery
provenance through its installed owner, without fabricating a live-path cut or
relaxing live-path validation.

One-use B issuance binds the independently verified ROOT, exact four live
exchanges, terminal admission, fresh final P CURRENT, mounted installation and
fence-owned continuation. Enrollment retains this material behind an opaque
identity marker. Durable records and public issuance DTOs cannot manufacture it.
The installed publication authority consumes the marker once at admission before
reentrant downstream work, independently derives authenticated invocation and
read-manifest inputs, and rechecks the complete source cut at final writer
admission without owner IPC under the writer lock. Failed or stale admission
burns transient authority; the marker is never retried.

Once a writer submission may have been accepted, the coordinator retains its
submission/commit task and drains it without cancelling that task, including on
caller cancellation, exceptions and runtime teardown. `EventAppender.submit`
shields its accepted result: cancelling its caller can leave a queued writer
able to select later. A cancelled wrapper, revoked marker or absent journal row
while that writer is outstanding therefore does not establish settlement.
Keep the lease and required enrolled resources live until the admitted writer
has finished or its termination is confirmed; only then reconcile the stable
identity and release ownership. Repeated cancellation must not shorten this
drain. Caller `CancelledError` propagates after cleanup, without a fabricated
success. If settlement cannot yet be established, retain exclusion rather than
permit another attempt. Process death releases the OS lease; the next holder
authenticates and reconciles any durable selected decision before B. These are
implementation obligations, not claims that existing broker-worker drain also
drains the publication writer.

The versioned [H1 completion issuance V2 freeze](H1-COMPLETION-ISSUANCE-V2.md)
separates historical selected scope evidence from final invocation evidence,
defines the writer-accepted admission witness and freezes dispatch/replay checks.
It is a tests-first contract, not a claim of implemented finalization.

Publication preserves the existing `CompleteDeliveryBatchV2` physical envelope
and fixed owner slots. Selection and exact physical readback precede a terminal
selected receipt. Cancellation or uncertain commit revokes transient authority
and reconciles the original publication identity before any later issuance;
unresolved state returns existing uncertain/HOLD behavior. Restart authenticates
and recovers an already selected batch without fresh B authority or current owner
regeneration. Terminal selection alone does not assert hermetic delivery, SEND
permission or outcome closure: those require their own selected intent and
observed evidence through the installed driver.

## Acceptance tests before implementation

Tests must first show the current missing behavior as RED and then prove:

1. Seal committed without ROOT recovers from the selected native prefix;
   forged but canonical ROOT fields, rival roots and missing native bytes hold.
2. An uncertain ROOT/input/result append is reconciled by reopening and
   scanning; no B/stage-owner IPC begins before input readback or from a losing
   CAS branch.
3. Each crash cut resumes with exact pinned semantic bytes and full result
   comparison; fresh B exchange identities are observed after restart.
4. Effects replay across a fresh broker generation uses the same inner bytes,
   intent and result but new outer sessions; stale frames, random replacement
   IDs and caller-shaped recovery authorization are denied before send.
5. Two processes and cancellation cannot maintain competing live B issuers;
   late pure replies cannot append or publish after ownership loss. Journal
   scan/append inside the separately enrolled fence does not self-deadlock.
6. Exact driver replay performs no second native seal. Once four stages are
   durable, it performs no B/stage-owner call after mandatory caller
   authentication and still reports only the established running receipt.
7. A digest-consistent but semantically substituted stage input and matching
   result under a valid ROOT are rejected, including on the fully durable
   path without B/stage-owner IPC.
8. The versioned V2 authority cut survives DECIDED-before-materialization and
   committed-before-finish crashes, and later unrelated publications leave
   the full historical cut and COMPLETION input byte-identical. Wrong cut
   bindings, missing/corrupt blobs and unknown versions hold; legacy V2
   without the extension cannot reconstruct full-cut completion. The physical
   seal still has exactly Run, seal and frontier members.

9. Fully durable `advance_execution` remains B/stage-owner-IPC-free
   `EXACT_REPLAY` after caller authentication; separate
   finalization without selected completion opens fresh B, replays exactly four
   inert calls and compares every result. Terminal admission precedes the fourth
   send; a distinct post-terminal P CURRENT precedes one-use issuance.
10. Copied/caller-shaped issuance, substituted ROOT/session/lease or result,
    stale terminal admission and historical CURRENT cannot authorize publication.
    Competing finalizations cannot consume one marker twice.
11. Selection-before-readback crash recovers the original exact batch without B
    IPC or reminting. Pending/ambiguous publication holds before fresh B;
    cancellation drains before fence release. A terminal selected receipt alone
    cannot report delivery/outcome success.
12. Ordinary publication, retry and finalization contend on the same enrolled
    lease across processes. Pause an accepted writer before selection, cancel
    its caller repeatedly and attempt a competing finalization: no second B or
    publication starts until actual writer settlement and reconciliation.
    Selected-but-physically-absent recovery materializes the exact original
    decision without B, fresh CURRENT or another marker consumption; an absent
    journal row is usable only after exclusion and settlement are established.

These tests observe authenticated journal bytes, physical publications,
actual broker frames and owner-call counts. A DTO roundtrip alone is not a
positive witness.
