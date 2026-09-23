# R15 configuration runtime boundary

Status: GENESIS and configuration-transition publication are implemented through
the actual trust owner, scheduler owner, independent journal and sole writer. Exact
restart replay/recovery and ordinary Run/planning coexistence are exercised below.
This is a prerequisite of R15 scheduling, not completion of R15. Retros are
excluded by the user's later instruction.

## Consumer contract

`composition.r15_scheduler_registry` exports `SchedulerGenesisDraft`,
`SchedulerGenesisAdoption`, `SchedulerGenesisPort` and the existing complete
`ConfigurationGenesisCommand`. The consumer supplies the registered ingress
identifier and a draft to `preview_scheduler_genesis`. A preview contains every
field of the command, including broker-owned Run inputs. It grants no authority
and writes no scheduler records. To accept it, persist its `canonical_bytes()`
and the original `adoption_act_id`, then pass both in `SchedulerGenesisAdoption`
to `adopt_scheduler_genesis`.

The draft requires an explicit schedule, start/period/end, prompt, BudgetPolicy,
missed-occurrence policy and interval bound. Existing BudgetPolicy defaults are
expanded into the full preview; they are not hidden choices made after adoption.
Both `MAX_TURNS` and `NO_POLICY` are allowed configuration data. The adoption DTO
preserves arbitrary nonempty bytes losslessly with base64 JSON encoding. Only
runtime validation may admit them as a supported canonical command.

The command ID must derive from a versioned tuple of tenant, principal, operation
and act ID. It must exclude payload, schedule, generation and current source cut.
Changing the schedule under the same act therefore reaches the same replay key
and must conflict. A retry after restart submits the original bytes; the broker
looks for an independently selected command before checking fresh absence or
current original worker bindings. Today's caller is authenticated again, while
the historical selected worker, authority epoch and source bytes remain intact.

`BrokerPublicationResult` distinguishes `COMMITTED`, `EXACT_REPLAY` and explicit
publication rejections. Unsupported ingress/configuration raises `LoopRejected`;
durable corruption retains the existing typed integrity error and root cause.
Neither a DTO, preview, returned hash nor a journal marker alone is a successful
physical publication.

## Registered first-write scope

Only `scheduler.genesis` and command schema
`chiplog.scheduler.configuration-genesis.v1` are admitted by the closed policy
`chiplog.scheduler.hermetic-genesis-policy.v1`. The service identity is
`r15-hermetic-scheduler-configuration-v1`. Canonicalization and coordinate codecs
remain the existing `chiplog.scheduler.canonical.v1` and
`chiplog.scheduler.unix-ns.v1`.

The exact existing offline profile is tenant `hermetic-tenant`, principal
`hermetic-principal`, CLI ingress `hermetic-ingress`, credential
`hermetic-credential`, session `hermetic-session`, and actual local uid peer
credential. Authentication uses actual deployment-trust `AUTHENTICATE`, retaining
its request, response and verified physical observation. `REVALIDATE` currently
supports another operation and cannot be relabeled as scheduler authorization.
An explicit exact adoption scoped by this policy supplies the configuration
mandate; `VALID` authentication alone does not.

Run origin is exactly `ORIGIN_EXACT`, ingress head `hermetic-ingress-v1`, endpoint
head `hermetic-endpoint-v1`, endpoint/provider `hermetic-local`, recipient
`hermetic-principal`, address `local://hermetic-principal`, and credential binding
`hermetic-v1`. Contour/policy heads remain `hermetic-contour-v1` and
`hermetic-policy-v1`. These are registered offline values, not substitutes for
trust credential heads. Worker session comes from the actual agent_loop session;
authority epoch derives from verified tenant/database/genesis/trust identity.
Broker and runtime graph generations retain their actual source preimages.

Fresh first-write admission requires complete admitted scheduler history to be
empty. Ordinary nonscheduler Runs may already exist. The existing compiler source
profile remains pinned. Unknown profiles and unsupported lineage mappings stay
unresolved. No automatic source repinning or schema-only row filtering is allowed.

## Required integration evidence

The implementation must retain exact adoption, private invocation issuance,
source preimages and actual owner request/response in the independently selected
publication. Writer admission rechecks the current physical cut, journal heads,
trust/session/generation, compiler registration and deadline without owner IPC
inside the transaction. The existing sole writer publishes the three complete
owner-authored GENESIS records atomically.

Runtime tests must observe actual authentication, isolated preparation and SQL
publication; exact retry after restart; changed adoption conflict; rejection of
forged authority and changed sources during IPC; omitted, added, reordered or
substituted owner records; exact recovery after selection before commit and lost
acknowledgement; and ordinary loop plus planning display/adopt after GENESIS.
Unknown or unselected scheduler rows must fail in both readers. Partial or extra
physical publication members must never become a successful replay.

The prerequisite shared-cut reader has executed tests for ambient connection
identity, old WAL snapshots against a new independent anchor, changed path/inode,
ended transactions and TEMP table shadowing. These tests do not establish the
runtime guarantees above. Contract tests likewise prove only expressibility.

The source-binding test invokes actual deployment-trust authentication and checks
the admitted graph, owner session and retained generation preimages. Fresh command
validation rejects substituted principal, worker, authority epoch and contour;
changing the broker generation invalidates the old authentication observation.
This tests source capture, not private issuance or scheduler selection. The shared
reader also reads ordinary Run/Complete history across restart without regenerating
companions, and rejects reanchored orphan publications. Mechanical readback tests
reject partial membership, extra records/publications at the selected sequence and
changed bytes. Combined verification used:

```sh
uv run pytest -q tests/composition/test_scheduler_genesis_contracts.py tests/composition/test_scheduler_genesis_runtime.py tests/platform/test_scheduler_reads.py --tb=short
```

This passed 83 tests. The existing planning-journey and architecture regression
passed 20 tests with:

```sh
uv run pytest -q tests/composition/test_planning_journeys.py tests/architecture/test_owner_imports.py tests/architecture/test_runtime_manifest.py tests/architecture/test_canonical_entrypoint.py --tb=short
```

The full lint adapter and test preflight passed on the same source state. No full
commit gate or milestone-completion claim follows from these prerequisite checks.

## Actual publication and recovery

`open_r15_runtime` exposes the preview/adoption API; `open_r15_loop` assembles
ordinary AgentLoop with the authenticated shared reader. The R13 historical
factory stays available. The public API accepts no context, observed trust call,
invocation handle or owner-prepared batch from its caller.

The publisher invokes AUTHENTICATE itself, retains that observation in the private
authority, and looks for an original selection before fresh global-absence checks.
It sends the exact preparation request to the isolated scheduler owner and compares
the complete returned bytes against the original pinned compiler input. A private
issued request is rechecked against the captured sources inside writer admission.
The selected request retains adoption, policy, actual IPC frames and source/mandate
preimages. A copied proof, request or prepared object is not a privately issued
object and cannot authorize the coordinator.

Physical readback uses a fresh read-only SQLite transaction with the entire
authority commitment and exclusive membership at the selected commit sequence.
Pending materialization must match its selected result and unique pending tail.
Historical replay must match the current independent anchor and keeps later
commits intact. Startup replays protected original bytes and validates the complete
shared history before exposing the runtime; no current scheduler-preparation IPC
is used to recreate a historical decision.

`tests/composition/test_scheduler_genesis_runtime.py` exercises one actual atomic
GENESIS triple, followed by ordinary Run, planning display/adoption and exact
planning replay, Complete, then scheduler replay after restart and a later anchor.
Changed schedule bytes under the same act conflict. Other executed cases cover
owner-generation change during IPC; reordered output; fully rehashed substituted
owner output; before-commit interruption; lost commit acknowledgement; original
selection recovery without scheduler-preparation IPC; copied issuer handles;
foreign ingress and malformed bytes; and missing records or extra publications
despite a materialized marker and a deliberately refreshed outer anchor.

Both restart paths and the private-issuance test passed with:

```sh
uv run pytest -q tests/composition/test_scheduler_genesis_runtime.py::test_selected_genesis_recovers_original_bytes_after_restart_without_owner_preparation tests/composition/test_scheduler_genesis_runtime.py::test_copied_issuer_handles_and_unregistered_input_do_not_grant_publication --tb=short
```

Publication/coordinator contract and architecture regression passed 77 tests:

```sh
uv run pytest -q tests/platform/test_owner_publications.py tests/platform/test_owner_publication_contracts.py tests/composition/test_scheduler_genesis_contracts.py tests/architecture/test_owner_imports.py tests/architecture/test_runtime_manifest.py tests/architecture/test_canonical_entrypoint.py --tb=short
```

The runtime suite passed 17 tests before the final public-boundary adjustment:

```sh
uv run pytest -q tests/composition/test_scheduler_genesis_runtime.py --tb=short
```

An additional real authentication-expiry test passed. The competing-adoption test
identified an internal `OwnerPublicationPending` escaping to the caller; the public
boundary now returns `HOLD` for that exception only. Uncertain selected outcomes
and integrity exceptions still propagate. On the final source, the race and both
restart recovery cases passed (3 tests):

```sh
uv run pytest -q tests/composition/test_scheduler_genesis_runtime.py::test_competing_genesis_adoptions_select_only_one_atomic_triple tests/composition/test_scheduler_genesis_runtime.py::test_selected_genesis_recovers_original_bytes_after_restart_without_owner_preparation --tb=short
```

The complete expanded runtime file was subsequently rerun on the final source
and passed all 19 tests in 80.83s using the full-file command above. The log is
`.artifacts/scheduler/genesis-publication-runtime-complete-final.log`.

The next configuration-transition slice now has an independent authority-head
preimage. It binds the registered configuration policy, actual trust reference and
runtime generation descriptions, excluding command/adoption bytes and per-call
frames. This avoids a self-reference in REPLACE_BOUND; the adoption mandate stays
separate. A real pair of AUTHENTICATE calls confirmed stable bytes with unchanged
sources; source-description mutations change the head. The focused runtime test
and existing contract tests passed (38 tests). The helper grants no authority by
itself; transition publication now revalidates the privately captured sources. Evidence and the exact
command are in `.artifacts/scheduler/configuration-authority-checkpoint.json`.

Configuration-transition drafts and exact-byte adoption contracts are now present
for schedule amendments, missed-policy amendments and bound replacements. Fresh
command validation reproduces broker-owned bindings and exact observed heads.
Shared history explicitly maps the four configuration operations while preserving
one global GENESIS and exact selected physical membership. A configuration snapshot
projects schedule, policy, bound and hold from the admitted startup index.

The combined contracts, GENESIS runtime and scheduler-reader suite passed 105 tests:

```sh
uv run pytest -q tests/composition/test_scheduler_genesis_contracts.py tests/composition/test_scheduler_genesis_runtime.py tests/platform/test_scheduler_reads.py --tb=short
```

Full lint and preflight passed on those prerequisite source bytes. Those checks
established the boundary and GENESIS regression before publication integration;
the historical checkpoint is in
`.artifacts/scheduler/configuration-boundary-checkpoint.json`.

## Configuration transition publication

`preview_scheduler_configuration` and `adopt_scheduler_configuration` now support
AMEND_SCHEDULE, AMEND_POLICY and REPLACE_BOUND. Closed drafts let consumers choose
new configuration values; the runtime supplies observed heads and authenticated
bindings. The exact accepted command bytes and stable act ID are retained. One
configuration act namespace covers all three operations, so changing operation
under an already selected act conflicts as well as changing its payload.

Fresh preparation projects the admitted current configuration, records presence
of exact schedule/policy/bound heads and hold presence/absence, retains independent
authority preimages, and rechecks the complete cut before the sole writer selects.
The isolated owner response must equal complete original-request compilation.
Private invocation proofs bind the exact original operation. Existing GENESIS
preparation and historical policy bytes remain unchanged.

Selected replay precedes fresh-state validation, uses original preparation bytes
and generation preimages, and authenticates only the current caller anew. It
reconstructs the original complete epoch/generation/session worker identity.
Restart recovery uses retained selected bytes without live owner preparation.
The three-transition journey passed, including immediate replay, later revisions,
restart replay and malformed changed-byte conflicts. Both before-commit and
lost-acknowledgement bound recovery passed without live owner preparation. A stale
policy command and cross-operation reuse of a selected act leave the journal
unchanged. The combined command above passed 109 tests in 98.20s; full lint and
preflight passed. Exact source hashes, logs and scope are recorded in
`.artifacts/scheduler/configuration-publication-checkpoint.json`. No full staged
commit gate or commit/push has been performed for this continuation.

The full test adapter subsequently passed on those same source hashes:
`sh .harness/scripts/test.sh` reported 991 passed and 429 deselected in 321.76s,
with delegated stage0 coverage confirmed for all 429 cases. The complete log is
`.artifacts/scheduler/configuration-full-test-adapter.log`. This is test-adapter
success, not a full staged commit-gate result.

`start_ns` is adopted coordinate data, not a trusted observation of now. Clock
issuance, lease acquisition/renewal/takeover, due intervals, rollover and actual
scheduled Runs remain required later R15 work. No configuration command grants
external-effect send authority.
