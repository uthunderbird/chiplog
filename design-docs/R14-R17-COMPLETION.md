# R14–R17 completion ledger

Status: OPEN. Baseline `5b38362120c1223b019ea9ec3b099e017b455364`.
The user requested full completion after the bounded continuations were published.
The subsequent scope is every R14–R17 requirement in IMPLEMENTATION-ROADMAP.md,
including its named invariants and counterhistories. Earlier green slice checks
remain evidence only for their original scope. Retrospectives are excluded by the
user's later instruction; this does not waive implementation or verification.

## Sources and ownership

Read IMPLEMENTATION-ROADMAP.md R14–R17 and its common DoD; the corresponding
CONTRACT-FREEZE documents; R14-CALL-ACCEPTANCE-CONTRACT,
R14-CAPTURED-FANOUT-CONTRACT, R14-CANCELLATION-PUBLICATION, R15-TICK-CONTRACT,
R16-DISPATCH-V2-CONTRACT, and R17-AUTHENTICATED-CUSTODY. Normative requirements
remain in project-architecture/NORMATIVE.md. A discrepancy is resolved against
those requirements, not by reducing the milestone to an existing test suite.

Agent loop owns call accounting, continuation and recovery decisions. Effects
owns immutable intent, dispatch and outcome interpretation. Deployment trust
owns source authentication. Broker/platform owns authenticated capture and the
sole physical writer; composition bridges inert owner contracts without
reconstructing owner outputs. Existing journal/appender mechanisms are reused.

## Plan

1. Record every required behavior, its current implementation boundary and the
   observable that could prove completion. Freeze missing cross-owner contracts
   and exercise them from public consumers before adding their business logic.
2. Register a versioned executable tool and parser without reclassifying existing
   proposal tools. The existing captured fanout explicitly permits only proposal
   classifications; a caller-supplied registry cannot introduce an executable one.
   Integrate consequential acceptance with the v2 effect intent: exact initialized
   and current Run heads, authenticated adoption, complete atomic owner batch,
   acceptance/cancellation competition and historical readers.
3. Complete R16's versioned reducer and durable lifecycle: outcomes, evidence,
   ambiguity, reconciliation, restart continuity, proof-bound retry/recovery and
   compensation with fresh authority. Retry/recovery execution remains gated by
   the integrated R14 lineage and recovery proof; a contract is not permission.
4. Implement R14 reduction, frontier, resume/successor, read-only budget,
   post-terminal work, worker fencing and model-attempt recovery using original
   effect/evidence streams. Connect these records to authoritative continuation,
   terminalization and recovery readers, not only preparation APIs.
5. Complete scheduler execution/lease/takeover/rollover against the common fence;
   complete all ingress handoffs, admission/quarantine and exact delivery.
6. Mount the integrated lifecycle in the canonical CLI/Telegram loop. Discover
   executable writer and ingress surfaces bidirectionally after downstream work;
   execute all required histories and current canonical verifiers on frozen bytes.
   Audit every row below before claiming any full milestone complete.

Cold plan review found missing explicit R16 lifecycle/restart and canonical-loop
joins. Both are included above; the corrected plan received no remaining critical
findings. Cold context reduces shared-context bias, not the common-model ceiling.
Root owns implementation, reads actual delegated results and verifies integration.

## Assumptions and change boundary

- The new completion worktree starts clean from the published baseline. Old
  worktrees and their uncommitted work are preserved; no result is inherited merely
  because an old branch or report exists.
- Hermetic adapters provide milestone evidence. Real external exposure retains
  the separate R20/current-entitlement requirements; no live sending is implied.
- Implementation needs multiple semantic commits. Plan below 35 changed paths per
  slice, including tests/docs/catalog/review changes; the harness hard limit is 40.
  Do not collapse unrelated behavior into files or weaken contracts to meet it.
- Historical registered schemas and driver entrypoints retain their interpretation.
  New canonical routing requires checking old fixture drivers explicitly.
- Review `inert_shared/r8-implementation-v1.json` against exact source inventory
  before repinning. No bulk unexplained enrollment; tests run on frozen bytes.
- Full completion is unproven until current evidence exists for all rows. An
  omitted runtime path, typed HOLD or pure proposal is an open requirement.

## Requirement-to-evidence ledger

Every row is **OPEN** for full scope. The implementation column identifies reusable
baseline evidence, not a completion claim. Canonical evidence is a future bound
verifier result with tree/profile/fixture identity and observed durable state;
test-file existence and aggregate pass counts are insufficient. Concrete result
artifact paths must replace `pending` only after actual execution and inspection.

| Requirement | Baseline implementation / remaining boundary | Required independent observation and negatives | Canonical evidence |
|---|---|---|---|
| R14.1 sealed fanout; A44/A54 | Captured fanout writer/history exists; expanded executable tool registration remains | Selected response plus exact ordered initialized set; missing/extra/duplicate/reordered/oversize/crash yields no partial set | V5/V6 accounting + final recovery convergence; pending |
| R14.1 acceptance; A45/A48/A51 | v1 pure acceptance envelope; v2 effects wire and PlanEffect runtime; no integrated call acceptance | Initialized-head CAS atomically selects accepted/execution/external intent under current Run/lease; both cancel/accept orders; pre-accept dispatch observes zero emissions | V5/V6 call-acceptance runtime + dispatch observer; pending |
| R14.1 accounting and cancellation; A50/A55/A56 | Pre-accept cancellation and proposal-only joins exist | All earlier sealed calls accounted; accepted uncertainty never becomes NOT_EXECUTED; open obligations bar continuation while satisfying only permitted accounting | V5/V6 canonical continuation and terminalization; pending |
| R14.2 reduction; A46 | Pure recovery representations; no integrated resolver/reduction CAS | Original resolver alone closes exact evidence and selects recovered outcome; compatible/rival evidence before/after closure gates both consumers | V5/V6 recovery reduction; pending |
| R14.3 frontier/baseline; A15/A38/A39/A42 | Frontier DTOs and pure accounting | Independent complete subject enumeration; exact absent/N/A/present membership; immutable baseline; total fault/hold/successor/same classification | V5/V6 frontier mutation histories; pending |
| R14.3 resume/successor; A40/A41/A47 | Physical atomic mechanisms reusable; semantic runtime open | Transaction-local recomputation and complete CAS; original obligations/no-retry references preserved; no partial successor or renewed authority | V5/V6 resume/successor crash and race histories; pending |
| R14.3 read-only retry; A49/A52/A57 | Retry lineage contracts; executable branch open | Positive read-only proof, stable original call/counter through restart/takeover/successor; exact pending transition, N/N+1, no branchless state | V5/V6 read-only lineage histories; pending |
| R14.4 worker registry/fence; A99 | Shared applicability DTOs; scheduler writes held | Discover every real command/handler/record/applicability in both directions; stale lease, renewal, clock, generation or physical selector writes nothing | V2/V5/V6 writer registry and downstream convergence; pending |
| R14.4 post-terminal work; A59 | Contract boundary only | Stable work and original stream; independent lease; resolver then closure; uint64 exhaustion and one selected replacement epoch | V5/V6 post-terminal work and rollover; pending |
| R14.5 attempts; A108 | R13 attempt data/unknown retention; full replacement path open | Exact five states; durable emission precedes bytes; lost response never blindly reissues; independent no-exposure proof and fresh selector retain lineage | V5/V6 model-attempt emission/crash observer; pending |
| R15.1 interval decisions; A17/A53 | Explicit one-delivery policy and interval publication/readback | Automatic service authority, stable revision-bound identities, zero/one/N complete eligible manifests and bound heads; no duplicate Run | V5/V6 scheduler runtime; pending |
| R15.2 overflow | Overflow blocking exists; resolution runtime open | Unique sufficient-bound resolution parents all sub-batches and alone advances boundary; omitted/rival/duplicate/early amendment fails | V5/V6 overflow resolution; pending |
| R15.3 execution; A18/A40/A43/A47/A51/A58/A98 | Pure lease/rollover producers; scheduled writes held | All disposition CAS orders, lease expiry/takeover and authenticated rollover; exactly one physical epoch per stable lineage; integrated R14 recovery | V5/V6 scheduler execution/restart/rollover; pending |
| R16 atomic intent; A07/A48 | PlanEffect v2 publication exists; consequential integration open | Actual domain/acceptance plus owner intent all-or-none; immutable original mandate; no provider inside writer transaction | V5/V6 R14–R16 atomic publication; pending |
| R16 complete dispatch; A06/A35 | v2 authorize/first SEND/one-shot hermetic consumption | All registered dispatch states/transitions and original semantic binding; stale/rival/version mutation rejects; durable process restart preserves identity | V5/V6 full dispatch reducer/runtime; pending |
| R16 ambiguity/reconciliation; A08 | First-send consumption exists; durable outcome/evidence/reconciliation open | Lost response creates unknown/recovery obligation; exact reconciliation; T03 observes zero replacement attempts; late evidence and all crash edges | V6/V9 T03 through canonical driver; pending |
| R16 compensation/retry; A30/A37 | Original authority contracts; full runtime open | Fresh separately scoped compensation and registered all-child proof for any permitted retry; no timeout-as-no-exposure; T01 fake adapter path | V5/V6/V9 effect/recovery histories; pending |
| R17.1 complete ingress; A19/A28/A100/A101/A103 | Retained CLI token/custody plus authenticated Unix-peer slice | All seven source classes at their earliest handoff; exact raw bytes or enumerable loss obligation; forged/stale witness, destructive receive/crash, duplicates | V4/V6/V7 actual ingress inventory and custody observers; pending |
| R17.2 admission; A102 | Pure FIFO/reserve/drain proposals | Durable nonborrowable reserves, strict ready-generation FIFO, full bound snapshots and deadline-preserving rebase; restart/drain joins all state | V4/V6/V7 admission progress and crash histories; pending |
| R17.2 quarantine; A104/A28 | Pure parser/cursor proposals | Immutable raw custody, CAS-selected parser lineage, full raw page member disposition before applied cursor; rival parser and partial page reject | V4/V6/V7 polling/quarantine histories; pending |
| R17.3 exact delivery; A10/A32/A34/A107 | Pure owner delivery proposals; dispatch delivery purpose open | Origin/selected exact recipient, final endpoint/disclosure check, deterministic committed evidence rendering; ambiguous delivery never reruns model | V4/V6/V7 delivery boundary observer; pending |
| R17.4 parity | Early CLI dialogue only | CLI/Telegram use same canonical loop/projection with distinct authentication; full acceptance/recovery/delivery joins and counterhistories | V4/V6/V7 channel parity; pending |
| Cross-stage completion | Profiles 9/10 coexist, full bounded baseline tests pass | Above paths jointly executable; A99 and ingress inventory exact after integration; current bound result artifacts cover every required clause | Roadmap common DoD + final R14–R17 evidence audit; pending |

## Guarantee preflight for the next acceptance boundary

| Claim | Owned data/decision | Independent observable | Forbidden substitute | Boundary fixture | Planned evidence |
|---|---|---|---|---|---|
| Exact original call | Agent-loop initialized record and sealed tool bytes; effects retains corresponding immutable origin | Selected fanout inventory and original byte comparison | Proposal ID or model label used as initialized identity | Cross-call adoption, changed schema/policy/arguments, historical/current Run substitution | Public v2 acceptance consumer, then runtime history |
| Complete atomic acceptance | Agent-loop accepted/execution and effects original v2 intent record | Independent journal selection plus exact SQL member bytes/order | Legacy v1 intent, caller manifest or reconstructed owner record | Omit/add/reorder/duplicate/substitute companions; N/N+1 complete envelope | Envelope tests, then writer crash tests |
| Current authorized acceptance | Broker private capture and authenticated adoption with exact current Run/fence | Mutate each bound source after owner preparation; observe no selection/effect | Constructed DTO, source digest or old owner result as credential | Expired/renewed lease, stale Run, trust/session/clock replacement | Private issuer and last-writer-boundary runtime |
| Immutable mandate | Effects-owned original mandate/acquisition and frozen horizon | Original selected bytes unchanged at acceptance/replay/send | Fresh observation replaces or renews old mandate | Changed display, adoption, recipient, payload, scope, horizon or origin | Consumer graph, owner reducer and retained history |
| Stable historical replay | Authenticated selected full envelope interpreted under original versions | Same bytes/IDs after materialization crash and process restart | Current owner regeneration or permissive schema fallback | Changed replay, pending malformed batch, unknown version | Selected-history and restart runtime |

All mutation families apply: omission, addition/unknown, substitution/alias,
duplicate/reorder, stale/race, N/N+1 and logical/physical mismatch. Bounds cover
complete serialized payload and closure, not only member count. These contracts
grant neither publication nor dispatch authority; runtime claims remain OPEN.

## Public acceptance transport

`composition.r14_call_acceptance_port` defines requested target, preview, explicit
adoption and committed receipt. The target names both original initialized call
and exact current Run; historical initialization Run remains in the effects-owned
mandate. Preview contains full display/mandate bytes, not only their digests.
Adoption retains the exact preview bytes and a stable act ID. The actual peer is
authenticated separately by composition. No caller cut, prepared batch, clock,
lease proof or permission flag is accepted. Malformed bytes remain lossless for
runtime rejection. Receipt references selected acceptance/execution/intent and
the complete publication; it never asserts provider success or grants SEND.

Preview identity is `call-acceptance-preview:` plus SHA256 of
`b"chiplog.call.acceptance-preview-identity.v2\x00"` followed by canonical
RecoveryDTO JSON containing only `kind`, `target`, `display_bytes` and
`mandate_bytes` from the preview. Bytes use the same URL-safe base64 encoding as
the transport. The preview fingerprint hashes
`b"chiplog.call.acceptance-preview.v2\x00"` followed by its canonical body
excluding only `preview_fingerprint`. Here `\x00` denotes one terminal NUL byte,
not the literal backslash characters. Both preimages preserve nested canonical
ordering. Identity therefore precedes fingerprint and neither references adoption.
An identical act replay returns the original receipt; changed bytes conflict.
Neither historical replay nor fresh current observations extend the mandate.

This is a public contract candidate. Its consumer checks prove binary transport
and forbidden-field rejection only. Private issuance, exact registered display
interpretation, owner exchanges, three-member v2 envelope, history and sole-writer
publication remain implementation obligations, not guarantees supplied by a DTO.

The pure `r14_call_acceptance_preview` interpreter now builds a registered display
containing the complete target and mandate as indented JSON. It verifies original
initialized identity, mandate structure and payload digest, both preview digest
domains, canonical submitted bytes and exact display reconstruction. It retains
the original mandate when the current Run changes. This does not authenticate
the selected original call, current Run, tool policy or caller. Recomputed valid
bytes still require independent runtime source verification; these functions issue
no credential. Public consumer and preview tests cover transport, forbidden fields,
each initialized-reference component, altered display/identity/fingerprint,
noncanonical bytes and current-versus-original Run separation.

## Executable Run compatibility seam

Source inspection found proposal-only literals in ToolSpec/ToolCall and nested
Run prompt/attempt/outcome types. Legacy continuation and retained fanout readers
decode Continue directly; canonical AgentLoop terminalizes every call as
PROPOSAL_ONLY; inventory_from_history initially assigns InitializedCall. Thus
adding a parser name or changing a registry classification is insufficient.

The next boundary is a separate versioned executable ToolSpec/call/response and
Run schema, preserving legacy types and generated schema bytes. Physical and
selected history decoding must select by registered schema and captured generator.
Consequential initialization must not allocate a proposal ID or terminal result.
Exact lifecycle readers must join selected acceptance/execution/effect records;
generic ToolTerminal strings cannot establish a consequential result. The original
call key and initialized head remain stable while acceptance checks current Run
and worker fencing. Cancellation uses the same branch CAS and cannot rewrite an
accepted unknown as NOT_EXECUTED.

Required integration witnesses: unchanged legacy schema/replay; old generator
rejecting new tools; actual consequential initialization remaining pending; both
accept/cancel orders; changed current Run with immutable original identity;
crash/reopen with complete companions; continuation rejecting missing/rival or
unsupported result evidence. These remain OPEN, including scheduler and successor
execution variants; the new preview helper does not discharge them.

The candidate `agent_loop.execution_contracts` now carries that separate wire:
`request_self_effect` v2 has explicit binary payload and bundle arguments; it
accepts no authority, recipient or horizon from the model. The later full display
shows the independently resolved self recipient and all mandate terms before
adoption. Proposal ToolSpec/ToolCall objects retain their old classes. A new
execution generator has versioned prompt, visibility, attempt, Turn and Run data;
legacy models are not widened. A Turn stores the exact response seal and ordered
initialized-call references instead of free embedded consequential results.
Current delivery acceptance, original obligations and no-retry references are
separate explicit Run fields. The origin uses the existing owner-local exact
recipient contract, permitting later CLI/Telegram integration without a legacy
provider literal change. Shape acceptance alone verifies neither existence nor
authority of any referenced record.

| New wire guarantee | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Evidence |
|---|---|---|---|---|---|
| Historical separation | Legacy models unchanged; v2 generator and Run schema selected explicitly | Old consumer rejects new tool; old exact schema hashes unchanged | Widen legacy literals or relabel propose_intent | Wrong generator/tool/version and historical record substitution | Public shape checks; schema/decoder runtime pending |
| No consequential proposal terminal | v2 Turn retains original initialized references; lifecycle owners publish results | Actual initialized call remains pending until branch CAS and result/obligation join | Embedded result string or allocated proposal ID | Forged terminal, missing intent/result, both cancel/accept orders | Shape forbids free result; runtime pending |
| Complete tool request | Owner-local v2 arguments retain payload and ordered bundle | Exact captured call bytes equal original intent origin and adopted payload | Model-provided recipient/grant or later argument normalization | Omit/add/reorder/duplicate/substitute and total serialized N/N+1 | Public binary/extra-field checks; parser/fanout runtime pending |

Validation is targeted: public acceptance/preview and executable wire consumers,
parser/owner preparation and isolated routing, source admission, types and formatting.
Full integration and physical publication are not inferred from those checks.
Required preflight and cold staged review remain separate checks for each final tree.

`execution_parsing` and `adapters.driven.execution_prompts` now implement the
separate `chiplog.turn-schema.execution.v2` generator. Its fixed ordered tools are
the two unchanged proposal tools and `request_self_effect` v2. The actual
promptstrings schema equals the parser schema; wrong generator, schema bytes,
missing/duplicated/reordered tools, duplicate model-call IDs, duplicate requested
bundle members and relabelled consequential arguments reject. Binary payload and
bundle order are preserved. Existing parser/prompt modules remain unchanged and
reject the new call wire. This generator is not yet mounted into a runtime profile.
The complete new Run wire has consumer roundtrips for all five model-attempt states
and original scheduler/recovery/delivery references; this does not establish their
semantic state combinations or selected provenance.

Next implementation boundary: owner transition and fanout preparation for this
Run schema, schema-directed physical/history readers and canonical runtime routing,
then exact v2 acceptance publication. Do not route the new calls through legacy
AgentLoop's unconditional proposal terminalization. Full R14–R17 completion remains
the entire ledger above, not this prerequisite.

The `execution_fan_out_contracts` boundary now accepts the versioned Run with the
complete original preparation request, registry body and registry reference. Its
distinct request/result discriminators prevent legacy producer routing. The result
retains the owner-produced sealed Run, response seal and complete ordered initialized
set. Composition must retain this Run rather than reconstructing its transition;
the result introduces no terminal result or publication credential. Shape validation intentionally does
not authenticate a capture or prove that its references agree: the producer and
writer must reject an unsealed, mismatched or stale Run. The public consumer checks
lossless transport, legacy discrimination and forbidden authority fields; runtime
fanout preparation and publication remain OPEN.

Current bounded validation: `uv run pytest -q
tests/capabilities/agent_loop/test_execution_fan_out_contracts.py
tests/capabilities/agent_loop/test_fan_out_contracts.py` passed (30 tests). Explicit
source-plus-consumer mypy passed for this boundary. The earlier 136-test combined
suite and preflight passed before this new boundary. After adding the owner-produced
sealed Run to the result, the contract suites plus
`tests/architecture/test_r8_current_surfaces.py` and
`tests/verification/test_r8_surface.py` passed (87 tests), explicit source-plus-consumer
mypy passed, and `sh .harness/scripts/test.sh --preflight` completed with exit 0 on
the frozen registered source bytes. No full milestone row is discharged by these
shape and source-admission checks.

The pure `execution_fan_out_preparation` producer now interprets the actual v2
capture, checks the complete registered tool set and exact ordered calls, and
returns the seal, every initialized record and an owner-produced sealed Run.
Original response bytes are retained. For Continue only, the selected model attempt
becomes TERMINAL_ACCEPTED. No call becomes terminal and no proposal ID is allocated
by fanout. Complete instead prepares an empty initialized set while retaining the
exact unchanged RESPONSE_CAPTURED attempt and an unaccepted RESPONSE_AVAILABLE
Turn; the Run event is ModelCompletionPrepared. CompleteAcceptance must still
consume that exact captured attempt and atomically accept delivery, accept the Turn
and advance Run to SUCCEEDED after every required predicate passes. This producer
cannot establish that conjunction. Existing recovery references remain unchanged.
Independent pure fixtures exercise mixed proposal/consequential calls, binary payload,
zero calls, omissions/additions/reorder/duplicates, registry reclassification,
stale capture/worker/manifest, and exact call/response/manifest/owner-output bounds.
The physical writer still must bind the authenticated registry and original capture,
check the complete physical-envelope byte bound, and publish all members atomically.
Scheduler captures remain explicitly unsupported until the registered lease path is
integrated; this is not evidence of full R14 or R15 completion. The next required
boundary is the retained envelope and physical/history integration for these outputs.

The new preparation operation is now registered in an isolated owner process under
combined profile 11. Profiles 1–10 and their entrypoints remain unchanged: the new
profile uses R14 fanout owner routes plus the new preparation route, the R16
dispatch effects owner, and the R17 custody trust owner. Other owners and leaves
retain their existing registered identities. This makes the complete existing
preparation graph available for integration without selecting it in the canonical
loop prematurely. The first topology test disproved identical leaf sets: R16 has
the additional registered effects_transport leaf. The combined profile therefore
retains R16's exact leaf set, with all R14/R17 leaves unchanged and only that one
addition. Resources and broker capabilities must still match all source profiles. Required
witnesses are exact production/evaluation topology, actual isolated v2 preparation,
old-route compatibility, old-profile rejection of the new route, malformed/cross-schema
requests, and rejection of missing/extra capabilities. Runtime publication remains
pending until the envelope, writer and historical reader are connected.

Validation of these frozen bytes: the combined producer/parser/legacy-fanout and
R8 source-admission suite passed 152 tests; `uv run pytest -xq
tests/platform/test_execution_fan_out_owner_process.py` passed 8 tests, including
actual isolated IPC, canonical request rejection, stable owner session after
rejection, legacy route compatibility, old-profile rejection and exact topology
mutations. The first IPC run rejected the new process because its exact module
closure was not registered; the broker now names precisely the old fanout process
closure plus `_execution_process`, retaining the strict attestation comparison.
Mypy on the six new/changed process, producer and consumer targets and ruff passed;
`sh .harness/scripts/test.sh --preflight` completed with exit 0 after the final
source catalog update. These results establish preparation and process routing,
not authenticated capture publication or full milestone completion.

Cold-review repair: Complete preparation retains the captured/unaccepted state
above, with direct and isolated-owner assertions for unchanged attempt bytes.
Prompt tests moved from the unregistered `tests/adapters/test_execution_prompts.py`
path to `tests/platform/test_execution_prompts.py`. Shared shape, captured-fanout
and executable-fanout builders now live under tests/support; new tests import no
test module. All 122 collected nodeids in the affected suites were preserved under
that single path substitution (before/after collection saved in /tmp during review).
Helper ASTs and legacy test bodies were checked unchanged during extraction.
The corrected producer and affected shape/legacy/prompt tests passed 114 tests;
source-plus-consumer mypy passed for the 11 affected files. These are repair checks,
not a replacement for review of the revised staged tree or the normal commit gate.
