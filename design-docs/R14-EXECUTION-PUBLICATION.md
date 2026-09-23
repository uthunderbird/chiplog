# Execution publication integration plan

Goal remains full R14–R17 completion per the repository roadmap, not only this
increment. Foundation commit `bf42f4503dcdd33a2b91c782d08fb4a179bbb082`
passed the ordinary commit gate and is the parent of this worktree.

Observed source boundaries:
- execution_fan_out_preparation returns exact sealed Run owner bytes. Continue
  accepts only the attempt/Turn; calls remain initialized. Complete remains
  RESPONSE_CAPTURED/RESPONSE_AVAILABLE pending full CompleteAcceptance.
- r14_fanout currently receives a legacy candidate Run from its caller, validates
  it, constructs only PROPOSAL_ONLY calls and retains legacy evidence.
- r14_loop_history decodes every first member as legacy RunRecord and checks
  every selected publication and physical record for exact complete membership.
- loop_sqlite also unconditionally decodes legacy RunRecord. Merely registering
  a new physical schema would leave startup/history/consumer behavior incomplete.

Plan for the next semantic implementation slice:
1. Freeze a versioned retained execution-fanout exchange and complete physical
   envelope. Retain exact request and owner output, session/capture commitment,
   expected snapshot; do not reconstruct or replace the owner's sealed Run.
2. Provide an independent verifier for the original capture, exact schema/tool
   interpretation, ordered initialized set, original identities and the complete
   Run transition. Compare submitted owner bytes; calling the producer again is
   not an independent verifier. Enforce complete serialized envelope bounds.
3. Introduce explicit schema-directed selected Run history that can enumerate
   old and new records together without silently dropping either. Preserve exact
   physical inventory, selected operation registration, predecessors and no orphan
   checks. Existing legacy consumers either receive explicitly legacy data or a
   rejection when a requested Run is v2; never reinterpret it as proposal-only.
   Run this semantic replay before startup materializes any pending execution
   decision, including execution-only histories. Trigger validation from schema,
   evidence and operation inventory together: disguised operation/schema must
   reject. Selected-only replay accepts an absent legitimate physical tail but
   never skips original predecessor or transition checks. A corrupt selected
   envelope at restart must yield zero newly materialized rows.
4. Implement publication through the existing sole writer/authority ordering
   domain: authenticated current capture and complete inventory, owner IPC outside
   lock, final Run/session/generation/registry/deadline/cut rechecks, one complete
   journal decision and physical transaction. Exact selected replay/recovery
   uses retained bytes and original interpretation, not current owner regeneration.
5. Integrate the executable Run's creation and model capture path sufficiently
   to reach this publisher through a runtime consumer, using profile12. Existing
   proposal entrypoints retain their schemas. Do not seed arbitrary Run bytes as
   evidence that this integrated path works. Admission/origin must come from the
   authenticated runtime boundary; no caller-supplied authority flag. Implement
   owner-produced and independently verified creation, Turn/request preparation,
   durable EMITTED_OUTCOME_UNKNOWN before model.invoke, then exact response capture.
   Preserve original manifest/request/attempt/worker identities throughout. Crash
   after emission before capture retains unknown outcome and cannot automatically
   invoke again; positive-proof replacement remains a later explicit operation.
6. Verify actual mixed proposal/consequential response, Complete-pending,
   original binary payload, process restart, crash between selection and
   materialization, full CAS races and missing/extra/duplicate/reordered members,
   stale worker/session/generation, unsupported schema, bound N/N+1 and orphan
   physical rows. Observe zero effect emission before actual acceptance and zero
   model invocation before selected and materialized emission; reopening emitted
   without captured response must not invoke a replacement.

Assumptions and scope controls:
- The established journal/appender and authority lock can be reused without
  widening generic writer privileges or weakening source attestation.
- New records require explicit supported transition verification; a generic
  string event or schema union alone is insufficient.
- Full acceptance/cancellation, continuation, scheduler applicability and
  CompleteAcceptance remain required follow-on implementation, explicitly OPEN;
  this increment must be usable by those paths without replacing original identity.
- Plan below35 changed paths, hard40. If the integrated path exceeds that, split
  coherent commits with the full integration goal retained; don't omit validation
  or collapse unrelated mechanisms merely to fit the count.
- Hermetic execution only, no external sends. Existing user commit/merge/push
  authorization persists; no retro.

Cold review request: inspect actual source and roadmap; identify critical missing
dependencies or unsafe assumptions before implementation. Root owns the result
and will inspect every proposed change. Do not edit source or run heavy suites.

## Current implementation evidence

The separate worktree starts from foundation commit `bf42f4503dcdd33a2b91c782d08fb4a179bbb082`, whose tree is `34000dfd2873fa49288f7913432391fb0695d992`. The inherited foundation is not a second implementation commit.

Cold plan review resolved both critical dependencies: startup semantic validation before materialization and durable emission before invocation. No remaining critical plan findings; actual implementation still required.

The inert retained-exchange and physical-envelope contracts have been introduced. Exact owner Run bytes have one source, proposal.sealed_run; caller replacement Run/companions/authority fields reject. Public contract checks: `uv run pytest -q tests/composition/test_execution_fanout_contracts.py` — 5 passed. No physical publication, authenticated runtime or restart guarantee follows from these shape checks. Remaining plan steps stay OPEN.

The independent execution fanout verifier and physical envelope builder now check
exact retained capture, ordered calls, schema/classification, source references and
deadlines, owner fingerprints and the Run/Turn/attempt transition. Continue changes
only the permitted fields; Complete preserves the captured attempt byte-for-byte.
The first physical member contains the actual owner-produced Run bytes. The verifier
does not invoke owner preparation. Negative tests recompute hashes before changing
states, identities, calls, manifests, classification and sessions/deadlines.
Validation: `uv run pytest -q tests/composition/test_execution_fanout_contracts.py
tests/composition/test_execution_fanout_records.py` — 30 passed; mypy on both new
sources and consumers passed (4 files); Ruff passed. Source-catalog admission has
not yet been updated. Authenticated publication and startup replay remain pending;
these pure checks do not establish a runtime guarantee or complete any milestone.

Executable attempt transport transitions now implement prepared → emitted unknown
and emitted unknown → captured, preserving original manifest/request/lineage and
exact response bytes. There is no re-emission transition for unknown or captured
attempts. Their public tests exercise serialization/reload, out-of-order capture,
recapture, manifest substitutions with recomputed Run hash, exact response N/N+1
and missing receipt. `uv run pytest -q tests/capabilities/agent_loop/test_execution_attempts.py`
passed 7 tests; mypy source+consumer passed. These are pure transitions, not durable
restart or actual emission observations. Preparation/creation, owner routing,
independent transition verification, physical writer and runtime remain required.

Execution request preparation now checks exact accumulated visibility and original
manifest identities, registered schema/tools, prompt/schema/context closure and
joined disclosure labels, before forming the first PREPARED_NOT_EMITTED attempt.
The complete serialized request is byte-bounded. The current execution transport is
explicitly hermetic; the legacy live-model route is unchanged. `uv run pytest -q
tests/capabilities/agent_loop/test_execution_preparation.py` passed 8 tests, including
preparation→emission→capture, duplicate preparation, coherent accumulator substitution,
and request N/N+1. Mypy source+consumer passed. This still does not authenticate input
visibility or prove a durable write-before-invoke boundary; runtime must supply and
recheck the original sources. Creation and history integration remain OPEN.

Ingress Run creation, activation, first Turn and visibility accumulation now feed
the preparation→emission→capture consumer test through actual owner functions,
not by fabricating the prepared state. Original origin and policy are retained;
first-Turn creation rejects any existing Turn/recovery references, and sealed
visibility cannot be rewritten. This initial path is not continuation authorization.
`uv run pytest -q tests/capabilities/agent_loop/test_execution_preparation.py
tests/capabilities/agent_loop/test_execution_attempts.py` passed 16 tests. Mypy on
lifecycle+consumer passed. These functions still need registered owner IPC, selected
history verification and runtime publication; no durability claim is made.

Closed tagged transition requests and the owner interpreter now cover creation,
activation, initial Turn, visibility, request preparation, emission and capture.
They issue inert proposals only. Profile12 adds one exact lifecycle route and router;
profile11 retains its original declaration. The process closure is explicitly
registered rather than loosened. Actual subprocess IPC exercised creation through
first Turn and rejected a caller-added authority field while preserving the session.
Source/consumer mypy passed (4 files). Source catalog is still pending final source
review; profiles are not yet mounted in a durable execution runtime.

Independent transition verification covers all seven command variants without
calling any transition producer. It checks source fingerprint, exact changed-field
sets, initialization emptiness, complete original visibility/schema/disclosure,
request byte bound, first attempt identity, selected generation and exact binary
capture. Rehashed owner mutations across every command reject. Full-chain testing
found that CaptureExecutionResponse inherited UTF-8 byte serialization; that command
now explicitly encodes/decodes base64. `uv run pytest -q
tests/composition/test_execution_transition_verification.py` passed 5 tests; source
and consumer mypy passed. Journal/source authentication and startup pre-materialization
validation remain integration obligations.

Retained transition records now bind original request/proposal, original exchange,
full mixed-schema snapshot digest, tenant head and predecessor commitment. Their
physical command contains exactly the owner Run bytes. The selected-history reader
now validates execution transition entries alongside every legacy Run, rejects schema
changes within a lineage and preserves exact physical inventory checks. Startup
invokes execution semantic validation before recovery; execution schema/evidence
under a disguised operation is rejected explicitly. This branch currently admits
transition envelopes only; execution fanout publication/history is still pending.
The source catalog was updated for exactly 14 reviewed source paths, asserting all
other hashes unchanged and complete inventory equality. R8 actual-surface and
negative verification suites passed 57 tests. The first legacy runtime test was
blocked by the stale catalog before runtime behavior was reached; after registration
its suite is running with output in /tmp/chiplog-execution-legacy-history.log.
Runtime publication, actual malformed-selected restart tests and mixed execution
fanout inventory remain required before an integration claim.

Legacy history suite completed: `uv run pytest -xq
tests/composition/test_r14_loop_history.py` — 12 passed in129.90s. New restart tests
inject malformed decoded selections after the authenticated journal interface:
semantic Run substitution with recomputed hashes, disguised operation, and unknown
record schema. Each rejects before EventAppender.submit, and actual SQL records and
execution publications remain absent. This does not claim bypassing journal MAC or
constructing a legitimate valid selected transaction. `uv run pytest -xq
tests/composition/test_execution_history_startup.py` — 3 passed in9.93s. A test-only
mypy selector first lacked source-package context; the subsequent incremental run
retained bad module analysis. `uv run mypy --no-incremental src/chiplog
tests/composition/test_execution_history_startup.py` passed253files without code or
configuration suppression. Real execution publication and positive crash recovery
remain pending.

Actual profile12 publication now authenticates the registered hermetic ingress,
derives its origin from the selected principal conversation record, and publishes
owner-produced creation, activation and initial Turn through the sole writer.
Current source snapshot, worker, owner session, deadline and commitment are checked
again at admission. `uv run pytest -xq tests/composition/test_execution_runtime.py`
passed 2 tests in 11.51s: exact physical owner bytes survive reopen; a selected
creation interrupted before SQL commit recovers from original retained bytes while
owner reissue is forbidden. The initial fault fixture patched the writer too early
and was rejected by workspace dispatch protection; the corrected fixture injects
the fault only at execution publication without weakening that protection.
This proves initial publication and one recovery edge, not durable model emission,
capture, fanout, continuation or full acceptance. Those integration steps remain OPEN.

The runtime now reads the actual workspace visibility, renders the registered v2
prompt, checks the unchanged source cut, and publishes accumulation, preparation,
emission and capture through the same owner and writer path. The registered hermetic
model accepts the distinct v2 attempt without changing legacy attempt interpretation.
Before invocation, selected/materialized history, current worker, registered model
identity, caller and absence of pending decisions are checked. Unknown attempts
have no automatic retry in this entrypoint or at startup.
`uv run pytest -xq tests/composition/test_execution_runtime.py` passed 5 tests in
31.58s, including an observer that reads actual SQL emission bytes before the model
receives the attempt. Lost response retains emitted unknown; a crash after selected
emission but before SQL commit reaches no model and recovers the original unknown
attempt without invoking it. Successful capture retains exact binary bytes and
manifest through reopen. `uv run mypy src/chiplog tests` passed 478 files; targeted
Ruff passed. These results do not establish full fanout, acceptance, model replacement
or successor recovery, and the complete increment still requires its normal gate
and cold staged review.

Capture regression: `uv run pytest -xq
tests/capabilities/agent_loop/test_durable_transitions.py
tests/capabilities/agent_loop/test_execution_preparation.py
tests/capabilities/agent_loop/test_execution_attempts.py
tests/composition/test_execution_transition_verification.py
tests/platform/test_execution_lifecycle_owner_process.py
tests/architecture/test_r8_current_surfaces.py tests/verification/test_r8_surface.py`
passed 136 tests in 26.87s. This covers legacy transition compatibility, v2 pure
checks, actual owner IPC and positive/negative source admission on the current
reviewed catalog. It is not a full integration gate.

Authenticated executable fanout publication now retains the actual isolated owner's
sealed Run and publishes it with the response seal and every initialized call in
one physical transaction. The mixed history reader validates both schemas and
reconstructs a complete inventory; legacy publication and cancellation consumers
also receive that inventory. Original initialized calls remain pending; no effect
dispatch or CompleteAcceptance is supplied by this step.
`uv run pytest -xq tests/composition/test_execution_fanout_runtime.py` passed the
initial three cases in 27.34s: mixed proposal/consequential response, Complete with
unchanged captured attempt, and selected-before-SQL crash recovery with owner
reissue forbidden. A later mixed legacy→execution→legacy case passed separately
(`-k legacy`, 1 passed, 3 deselected, 36.52s), verifying both directions retain all
original initialized calls through reopen.

The mixed case exposed two separate boundaries. Workspace preparation can publish
its policy before reading; capture now validates the exact issued workspace batch
and its frontier, then checks that cut again before publication instead of requiring
the earlier pre-preparation frontier. The same test also exposed the existing
R12 content-digest-only lookup rejecting distinct ingress records with identical
text. That reproducible defect remains mandatory work before R17 closure and is
recorded in handoff; mixed-schema testing uses distinct ingress text to isolate its
own criterion. No full ingress correctness claim follows from this increment.
