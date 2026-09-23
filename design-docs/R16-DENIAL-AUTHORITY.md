# R16 independent before-send disposition

Status: contracts, owner preparation, versioned isolated route, private runtime
issuance, durable publication and exact replay/recovery implemented for the closed
hermetic self-only policy. The existing v1 command, SEND binding and IPC route stay unchanged.
The owner entrypoint is `effects.denial.prepare_denial`.

## Plan and assumptions

The distinct denying authority and public preparation contract separate denial
from the original SEND grant. The owner checks exact current inputs, leases, bound
interpreter and legal transitions. Composition authenticates sources, calls the
isolated owner, reproduces inputs under the writer gate, preserves exact output
and recovers selected decisions before fresh preparation.

The caller constructing a DTO proves no authority. An authenticated principal is
not automatically authorized for every operation. The new operation needs its own
registered policy; the existing PlanEffect policy cannot provide it. Expiry of the
original SEND authority must not prevent a separately authorized denying decision.
No live provider, transmission allocation or successor publication belongs here.

This slice includes atomic PlanEffect publication, denying contracts and owner
logic, private runtime issuance, exact recovery, and their source-bound tests.
Dispatch observation contracts prepare future work and grant no SEND authority.

## Concrete source ownership

| Required source | Existing reader / missing implementation | Invalidators and writer reproduction |
| --- | --- | --- |
| Authenticated invocation | `R7PlanningRuntime._observed_trust_call` and `_trust_observation_guard` | Trust snapshot, session, generation, drain and request deadline; guard again at writer cut |
| Principal rights | Authenticated trust reference plus `HermeticDenialRegistry` checks the exact original principal/tenant | Trust revocation and policy changes; no reuse of old SEND grant |
| Operation registry | `HermeticDenialRegistry` registers `effects.before_send` for the original hermetic interpreter | Exact policy/version/source identity and complete interpreter binding; unknown profile holds |
| Effects history | `read_materialized_effects` authenticates complete selected materialization; it grants no operation rights | Latest attempt, original intent and latest authorization change; complete journal/materialization recapture |
| Runtime fence | Current worker from authenticated Run and supervisor/read ledger | Run, session, generation, lease and database identity; exact writer-cut reproduction |
| Clock | Fresh acquisition lease under a registered clock contract and epoch | Deadline equality, restart/epoch changes; original SEND epoch is not a fresh lease |
| Decision | Private `DenialAuthority` retains invocation and prepared-object identity; SUPERSEDE requires a separately adopted exact successor | Decision withdrawal/substitution and successor adoption changes; typed refs alone are insufficient |

Each source is mandatory. Missing source acquisition cannot produce a denial
authority. Captured descriptors are claims; only a private issuer with retained
provenance may admit them to the writer. The owner compares independent current
inputs and validates shape/lease constraints, never authenticates raw handles.

## Retirement and supersession

From DISPATCH_AUTHORIZED the command binds the complete latest authorization being
retired, including its original generation. A stale authorization cannot retire a
newer one. Current denying-worker authority is checked independently; old SEND
expiry or an old worker does not provide current authority. The future policy must
authenticate that retirement relation. The owner API alone cannot admit a durable
publication.

SUPERSEDE names a distinct exact successor and its separately authenticated adoption.
It preserves the original intent and all history. It does not create the successor,
authorize its dispatch, clear its blockers or rewrite an already crossed outcome.

## Evidence requirements

| Claim | Owned data and independent observable | Forbidden substitute / test |
| --- | --- | --- |
| Independent denying lease | New authority, current clock and every required source lease | Renewing old SEND fields; expired SEND + live denial succeeds, expiry equality rejects |
| Exact operation and subject | Command, authority, current inputs, full intent/attempt and actor scope | Arbitrary matching heads; substitute each subject/decision/fence/cut field |
| Complete interpreter | All five original semantic bindings equal supported binding | Missing or changed part; owner returns version hold |
| Latest retirement only | Full latest authorization, old generation and current denial fence | Old authorization retiring a newer one; both histories tested |
| No crossed state becomes no-send | VISION transition table, complete retained children/evidence | SEND_COMMITTED or later to cancellation; every forbidden state tested |
| Immutable history | Original intent, authorization list, children and evidence survive unchanged | Reconstructing original authority or clearing uncertainty; exact snapshot comparison |
| Actual runtime authorization | Private issuance and writer recapture, tested through real v7 IPC and writer | DTO construction or owner unit tests alone |

Omission, unknown additions, substitution, duplicate history identities, stale/race,
clock boundary and logical identity mismatch require witnesses. The owner does not
claim physical identity or source completeness; those remain composition tests.
The isolated route is `effects.prepare_denial` with explicit v2 request/result
schemas. Runtime manifest v7 registers its exact owner partition and module closure;
v4 remains unchanged. Versions v5/v6 are reserved by the independent R14 calls/fanout
worktree. The standard R14 composition is not switched to v7 in this slice.

Retained source decoding dispatches explicit v2 commands separately from schema-less
legacy commands. Unknown versions reject without fallback. Both history readers
check the v2 command's target against the predecessor attempt, while the output
contains a newly generated attempt. Rehashed kind/state/subject substitutions reject.
These structural checks do not authenticate the original issuer by themselves.
The runtime additionally validates complete selected prefixes, the original worker
and trust interpretation, and exact singleton owner output before recovery writes.
Fresh preparation uses retained authenticated invocation and writer recapture;
historical recovery never acquires a new lease or substitutes new owner output.

## Executed runtime evidence

`tests/composition/test_denial_runtime.py` passes 20 scenarios in the full test adapter:
HOLD/CANCEL/SUPERSEDE, unchanged expired SEND authority, exact restart replay,
fresh denial of an old intent by a new Run/generation, conflicting act reuse,
source mutation during IPC, generation/drain changes at the writer, unissued object
clones, malformed historical sources and operation/schema aliases, and recovery
after selection but before physical publication. Pending public replay cannot
override a current authentication denial, and competing pending journal families
prevent owner recovery. No scenario allocates a transmission.

Complete effects records occur once in the request. Source evidence retains exact
ordered references; the current Run body is retained in `runtime_fence`. The decoded
cut's singleton Run list is an internal rebuilding convenience, not a claim about
complete Run inventory. Live writer comparison still uses the complete captured cut.
PlanEffect materialized-cut and conflict-order evidence use explicit source v2
encodings to avoid repeated history bytes; old v1 records retain their original bytes.
The common IPC bound is unchanged.

`sh .harness/scripts/test.sh` passes: 945 tests, with all 420 delegated stage0 cases
confirmed (892.40 seconds). Full lint/type checks and the adapter preflight pass.
The final regression preserves unrelated mechanical owner recovery while detecting
denial signals independently across operation, schema, retained command and output.
A malformed non-object output test failed before the routing fix and passes after it.
Repeated pure legacy Run validation reuses only successful checks keyed by both
exact record byte strings, bounded to 32 pairs of at most 256 KiB each. Expanded
delivery acceptance retains its independent observation check. Owner snapshot
reuse still requires freshly authenticated complete journal entries. Changed bytes,
sidecar replacement, key/head corruption and a mismatched middle read are tested.
The supersession scenario passes with the original five-second preparation deadline.
SEND, provider dispatch, post-send evidence/reconciliation,
scheduler-backed denying workers and general non-hermetic policies remain outside
this implemented slice.

## Executed owner evidence

`uv run pytest -q tests/capabilities/effects tests/composition/test_effects_owner_flow.py
tests/platform/test_effects_queries.py` passes 172 tests, including existing owner
and adapter scenarios. The new tests cover each VISION state against all three
dispositions, expiry equality for authority and every required source, clock epoch
and contract mismatch, each semantic-binding part, exact latest retirement,
supersession aliases, current-input substitutions and selected-command replay.
Targeted Ruff and mypy pass. These are owner-algebra and shape checks, not evidence
of an authenticated runtime or durable denial. Physical ordering, source provenance,
revocation races, real current policy and durable route integration remain HOLD.
The shared record helper only broadens its input typing; its behavior is
unchanged and still assumes a valid authenticated predecessor.

The extended route/reader suite passed 205 tests using:

```sh
uv run pytest -q tests/capabilities/effects tests/platform/test_effects_denial_boundary.py \
  tests/platform/test_effects_queries.py tests/platform/test_owner_process_preparation.py \
  tests/architecture/test_runtime_manifest.py tests/composition/test_effects_owner_flow.py
```

It exercises real IPC for legacy and denial routes, expired denying authority and
source leases, schema/route confusion, v4 rejection of the new route, and observed
v7 production/evaluation graphs. A subsequent query-reader check passed 34 tests,
including two new cases that send a valid denial and a rehashed predecessor alias
through `BoundEffectsQueries.effects_snapshot` itself. Existing denial test node IDs
are retained; only their shared fixture builder moved to `tests/support`.

At the earlier owner/route boundary, `sh .harness/scripts/test.sh` completed with
exit 0: 916 passed, with all
420 delegated stage0 cases independently confirmed by the test adapter. Full
`lint.sh`, preflight and `python -m chiplog.verification fast` also passed. These
results cover the integrated working tree, not just the frozen staged PlanEffect
candidate. Authenticated denial publication remains explicitly outside this result.
