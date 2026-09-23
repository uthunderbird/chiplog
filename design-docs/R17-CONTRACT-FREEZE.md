# R17 — universal ingress custody and exact delivery

Status: contracts plus initial pure domain slice; integrated behavioral evidence HOLD. No deployment
authorization. Full R17 implementation, counterhistories and retrospective remain
required; the current stage stops at reviewed contracts and public shape checks.

## Context, plan and assumptions

Sources: IMPLEMENTATION-ROADMAP.md R17 and A10/A19/A28/A32/A34/A100–104/A107;
project-architecture/NORMATIVE.md ownership, persistence/admission, authenticated
ingress, conversation/channel delivery and executable conformance fixtures;
VISION.md adopted authority/disclosure/receipt boundaries; R13-CONTRACT-FREEZE.md;
platform/_sqlite.py existing evidence commit/follow-up primitives;
capabilities/agent_loop/contracts.py accepted completion and endpoint contracts.
The vision remains the adopted product boundary; architecture mechanisms require
their own evidence and do not establish production readiness.

Prepare the complete evidence map, agree shared seams, materialize owner-local
DTOs/protocols, then run public-consumer shape checks. Business logic starts only
after the common freeze. Subsequent implementation covers all four roadmap stages:
universal custody, bounded admission/recovery, exact delivery and channel parity.
Use the canonical R13 loop with registered offline leaves; retain historical
component fixture entrypoints and review affected source-bound inventories before
any repinning. No real services, staging or commits are authorized in this stage.

Ownership is authoritative: agent_loop owns accepted selection, completion and
render manifest; effects owns delivery/provider attempts, outbox, dispatch,
receipts and reconciliation; platform/broker owns receipt-token custody,
admission and the sole writer; deployment_trust authenticates independent source
bindings. There is no new delivery capability with a rival transport lifecycle.
Root owns common files and journal decisions. Owner-local representations cross
boundaries through declared inert bytes/mechanical bridges, not foreign private
imports or executable shared helpers. R14 owns shared lineage/physical epoch,
lease/clock/successor/current-Run/budget references; R16 supplies the effects port.

Initial worktree was clean (`git status --short`). Preparation estimate: this
document, agent_loop/delivery_contracts.py, broker-private ingress contracts and
public consumer tests, with test support only if required. The root coordinates
the combined <=35-file budget including inherited files, shared contracts,
inventories, evidence and retro. Actual harness hard cap is 40
(`.harness/scripts/thresholds.sh`). Review semantic slices before expansion;
do not combine unrelated code to evade the cap. No business logic is present.

Inspection found two shared seams requiring root changes: R13 EndpointSelection
currently hardcodes hermetic-local and requires an ingress head for both selection
variants; existing EvidenceFollowupKind omits CLI. Existing custody primitives
are reused and extended, not copied into a competing writer. An ingress manifest
does not prove paths exist: executable adapter/public-port reachability and the
independent inventory must agree at build, startup and generation replacement.

Cold preparation review required the ownership split above, complete token/FIFO/
quarantine order and deadline coverage, and explicitly empty dashboard ingress.
Those corrections are accepted. Common contract code awaits freeze. The root
records process decisions; this owner does not write competing handoff entries.

## Requirement and guarantee map

Every row is HOLD until its named observer executes against the actual public
path and broker boundary. DTO construction and successful imports prove shape
only. Planned paths below may be refined at implementation slicing without
dropping a requirement.

| Requirement / claim | Owned data and decision | Independent observable | Forbidden substitute | Boundary counterhistory | Planned evidence |
|---|---|---|---|---|---|
| A103 exact ingress universe | broker independently generated manifest; seven executable source classes | compare actual adapter/port/record registrations bidirectionally at build/startup/generation publication | caller-selected source label, generic DTO, static inventory alone | omit/add/duplicate/alias/reroute/substitute path after initialization; dashboard set remains empty | HOLD tests/platform/test_ingress_surface.py |
| A100 token before handoff | broker retained-source token or preallocated PRE_AUTH loss slot; exact endpoint/session/epoch/fence/slot/max bytes/version/predecessor/fingerprint | independent external transfer log joins tokens before each destructive receive | allocate after read; volatile queue; digest-only receipt | crash before read, after read before stage, after raw stage; shutdown races token allocation | HOLD tests/platform/test_ingress_custody.py |
| A101 exact durable custody | one successor ADMITTED_DURABLE, RETRY_WITH_SOURCE_CUSTODY, TERMINAL_REJECT, QUARANTINED_RAW_EVIDENCE or LOSS_OBLIGATION; exact replayable raw bytes | token/successor/loss bidirectional restart enumeration and byte comparison | success with digest alone; assumed redelivery; loss acknowledged | changed/rival successor, duplicate replay, N/N+1 raw/reserve bound, retained source unavailable | HOLD tests/platform/test_ingress_custody.py |
| A102 bounded FIFO admission | broker epoch fixed class order including ORDINARY, Q/M/D, nonborrowable physical item/byte/quarantine reserves and bounded integers | observe service trace versus commit-sequence/tie FIFO and independently calculated capped-deficit recurrence | wall-clock ordering, borrowed physical headroom, later overtaking | zero/overflow/N+1 accounting, backlog predecessor omission/reorder, exhausted reserve and ORDINARY starvation | HOLD tests/platform/test_ingress_admission.py |
| A102 absolute selection bound | immutable full predecessor/head/size snapshot, origin slot and bound lineage | recompute origin+C*sum(v_j) and remaining deadline against scheduler trace | numeric cap without progress/order/closure; restarting a deadline | block consumes one skip, exact deficit update and atomic descendant rebase; unblock is fresh tail generation | HOLD tests/platform/test_ingress_admission.py |
| A102 complete restart/drain | OPEN→QUIESCING→DRAINING→CLOSED shares writer order; immutable drain manifest | enumerate every winning token, successor, raw/loss slot, FIFO/blocked state, bound/deficit and fence before ordinary work | in-memory queue drain, cancellation drop, CLOSED with unresolved token | allocation versus quiesce both orders; cancel/timeout/crash during complete rebase or drain | HOLD tests/platform/test_ingress_admission.py |
| Poll raw-page-first cursor safety / A28 | authenticated complete page membership before parsing; one closed materialized/duplicate/terminal non-evidence/quarantine member disposition | compare raw framing membership to durable joins; next request observes durable applied cursor | parsed-only subset; authorization without application; parser error silently rejected | omitted/extra/reordered/duplicate member; crash each disposition/authorization/application/emission boundary | HOLD tests/platform/test_ingress_polling.py |
| A104 immutable quarantine | one custody-derived inbox identity and CAS parser lineage; terminal byte-proof independent of parser version | original raw bytes unchanged; exactly one selected attempt/successor and terminal | new parser overwrites custody/authentication; exception becomes terminal proof | rival parser/version/result; missing dependency/unknown retries; second terminal and parser-dependent proof reject | HOLD tests/platform/test_ingress_polling.py |
| A19/A28 authenticated ingress | deployment_trust independent current source/endpoint/account/key/audience/tenant/session/freshness and original correlation | wrong identity witness rejects before custody authority/ack; late evidence survives Run/worker death | payload syntax/correlation as authentication; worker lease as evidence authority | forged/stale transport, credential rotation, wrong account/tenant/subject; all seven ingress rows | HOLD tests/platform/test_ingress_authentication.py |
| A107 exact origin/selection | agent_loop explicit ORIGIN_EXACT or MODEL_SELECTED_EXACT before durable acceptance; complete ordered delivery manifest | accepted tuple/address/credential/render bytes equal emitted submission; duplicate exact tuple rejects | omission in durable state, alias resolution, endpoint fallback, model grants authority | wrong variant/head/recipient, duplicate recipient, origin normalization then endpoint replacement | HOLD tests/capabilities/agent_loop/test_delivery_contract.py |
| A107 last reversible boundary | effects SEND_COMMITTED in broker order with endpoint/contour/disclosure/deletion invalidators | independent emitted bytes log; invalidator-first emits none, send-first retains one immutable attempt | stale acceptance validation; enqueue on replay; same-human/provider-confidentiality claim | both serial orders, crash before/after commit/enqueue/reply; authenticated tuple reassignment uncertainty | HOLD tests/platform/test_delivery_dispatch.py |
| A32 disclosure and prospective narrowing | exact visibility/provenance/label/policy/narrowing heads and principal-authorized successor revision | old traces/summaries/cache/index/conversation/render/delivery retain old restriction | missing label as bottom; model narrows; transitive declassification | stale/omitted/rival provenance, changed endpoint/scope/expiry/policy, descendant creation races | HOLD tests/capabilities/agent_loop/test_delivery_contract.py |
| A10/A34 deterministic evidence rendering | agent_loop committed-query assertions and closed accepted evidence joins | rendered consequence matches current committed owner evidence; no success without closure | model prose as receipt, local send as remote receipt | missing/open/rival evidence, changed render digest, delivery ambiguity without model rerun | HOLD tests/composition/test_channel_parity.py |
| R17.4 channel parity / V4 V6 V7 | canonical R13 application loop and neutral projection with distinct CLI/Telegram authentication | equivalent authorized commands use same loop/projection; channel-specific rendering and no automatic history mirroring | eval-only orchestrator, Telegram-only coverage, copied CLI configuration as identity | duplicate/stale/forged ingress, source-specific failures, hermetic adapter isolation canaries | HOLD tests/composition/test_channel_parity.py |

Actual ingress closure includes Telegram webhook push, Telegram polling response,
CLI request, provider callback, provider poll, reconciliation observation and
tool-result evidence. Each must rerun the common custody/ack contract at its own
earliest irreversible handoff. Dashboard/screens have zero ingress routes,
capabilities or custody commands. Raw provider/tool evidence is distinct from a
model-generated tool proposal. Provider/reconciliation paths cannot be replaced
by a Telegram-shaped fixture or asserted covered by one generic source parameter.

For every relevant row execute omission, addition/unknown, substitution/alias,
duplicate/reorder, stale/race, N/N+1 and logical/physical identity mismatch.
The mutation matrix has no blanket N/A: any eventual N/A needs a concrete
row-specific reason. Logical/physical mismatches include token/source slot,
provider tuple/address identity, custody-derived inbox versus parser attempt,
ready generation versus stable work, and selected execution/work epoch versus
lineage/Run. Authentication always compares an independent source witness.

## Validation and remaining freeze items

Run `sh .harness/scripts/test.sh --preflight` before broad verification and after
contract/entrypoint/fixture changes. Public shape consumers import only declared
owner-local public DTOs/protocols; broker-private custody tests exercise the broker
public operation rather than importing private DTOs as a substitute contract.
No behavioral PASS follows from those shape checks. Later verification includes
appropriate pytest/lint/types, actual V4/V6/V7 lanes, cold result review and retro.

Prepared: owner-local delivery DTOs in agent_loop/delivery_contracts.py and
broker-private shapes in platform/_ingress_contracts.py. Public selection shape
checks passed: `uv run pytest -q tests/capabilities/agent_loop/test_delivery_contract.py`
(5 passed); ruff and mypy passed on those source files and the test. This is shape evidence
only; all behavioral map rows remain HOLD.

Cold contract review repaired unknown PRE_AUTH endpoint representation, source-
specific authenticated witness variants, exact URL-safe base64 JSON byte encoding,
closed publication results, and asynchronous effects-publication shape carrying an
exact authenticated worker-fence head. Broker verification of that reference is
required; a caller-provided reference itself grants no authority.

Post-change preflight reported the corrupt-authoritative-projection reproducer
failing at R8 implementation inventory equality after new modules were added.
Direct reproducer execution confirms `R8SurfaceViolation: audited implementation
inventory is incomplete or unknown`. The adapter exited zero despite that output;
it is not recorded as clean verification. Root must review and update the actual
source-bound inventory before broad verification; no evidence was repinned here.

After a separate reviewed plan, root released pure broker-private domain work.
platform/_ingress_domain.py now proposes immutable token allocation, exact raw
staging, one custody successor, quiesce/drain prerequisites, checked backlog-aware
selection recurrence and nonborrowable reserve checks. It performs no I/O and
issues no authentication or durable authority. Thirteen focused domain tests pass
(`uv run pytest -q tests/platform/test_ingress_domain.py`); targeted mypy and
ruff pass. Pure proposals additionally cover complete bounded FIFO block/rebase,
poll-member cursor completeness and quarantine parser selection/result CAS with
immutable raw custody. Full structural drain joins now cover token/custody,
per-class ready/blocked/bounds/deficits/reserves, physical occupancy, quarantine,
settled work and exact selected-parser durable remainder/old-producer fence.
Quiescence binds epoch/fence plus the complete inventory fingerprint; the old
custody-only CLOSED helper was replaced. Manifests retain exact remainder heads
and token mappings for restart. Exact registered proof validation, writer CAS,
durable cursor application and seven transport handoffs remain
unimplemented/HOLD. These pure tests do not close any actual ingress DoD row.
Cold domain review found identity-only custody predecessor comparison and
digest-only retained-source proof comparison. Both now bind full immutable heads;
allocation and raw-stage heads use separate canonical domains, and mutation tests
independently change identity/head/fingerprint and use a stale pre-stage head.
Full drain cold review additionally found missing raw-authentication digest
revalidation and missing restart bound arithmetic checks. Close now rechecks both
local custody variants against the staged/authenticated digest and recomputes each
ready bound with checked arithmetic. Rebases record the exact remaining evaluation
slot while preserving original admission origin, lineage and absolute deadline.
Reached mutants cover wrong authenticated digest, impossible deadline, excess
deficit and arithmetic overflow. Independent actual recheck passed all 13 domain
tests with zero scoped P0/P1; this does not establish live source enumeration.

The separately reviewed pure delivery proposal slice is in
`agent_loop/delivery_preparation.py`. It uses closed Plan/Fact/candidate/provider
query observations, exact subject/correlation/frontier and typed code entailment,
visible immutable commentary wrappers, exact recipient selection, historical
disclosure labels, and acyclic acceptance identities. It returns no proposal when
any segment fails; history bytes are returned only with the complete proposal.
Thirteen tests passed with targeted mypy/ruff; independent actual review found
zero scoped P0/P1. Query observations are not authenticated by constructing these
DTOs. Actual owner-query decoding, exhaustive history reconstruction, expanded
core response/Run schema, atomic Run/history/effects publication, and actual
SEND_COMMITTED validation remain within R17 and remain HOLD. The legacy R13
RunRecord cannot represent the expanded response, so this slice deliberately
returns an owner-local acceptance proposal rather than an incompatible Run.

Pending: common frozen R14/R16 references and their mechanical bridge, including
authenticated worker-fence plumbing for effects publication; exact broker
custody/authentication public-operation surface and consumer validation; actual
executable ingress inventory and source-bound evidence paths; cold contract review.
All can be resolved by code inspection/implementation within the authorized
scope. No decision has been converted into a request for production exposure.
