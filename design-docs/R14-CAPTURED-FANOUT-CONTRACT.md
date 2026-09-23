# R14 captured fan-out contract

Contracts, pure producer and isolated IPC verified. Runtime publication remains HOLD.

Initial plan: add a public owner-local wrapper DTO around unchanged FanOutPreparationRequest;
include actual captured RunRecord and a complete typed tool-policy registry snapshot.
Consumer shape tests will use only public DTOs. Add a design doc before code; inspect
and admit only new source hashes in the implementation catalog. Preflight after shape
checks. Stop skill at contracts; producer/runtime implementation follows separately.

Assumptions: RecoveryDTO ordering applies to the entire outer wire, while embedded
RunRecord/ToolSpec hashes use their own canonical_bytes(), not substrings of outer
JSON. Both are explicit domains. A supplied Run/registry is evidence to check, never
authentication. Composition proves source/physical identity and currentness.

Concrete proposed contract choices:
- CapturedFanOutRequest kind PREPARE_CAPTURED_CALL_FAN_OUT_V1 contains request:
  FanOutPreparationRequest, captured_run: RunRecord, tool_registry: FanOutToolRegistry,
  tool_registry_head: CallSubjectHead. No legacy DTO modifications.
- captured_response is the durable captured Run revision reference:
  subject_id=Run.run_id, revision.head=Run.head, fingerprint=SHA256(Run.canonical_bytes).
  Require ModelResponseCaptured ACTIVE Run, last Turn's selected RESPONSE_CAPTURED
  attempt and exact raw response base64, IDs and worker/cut. Preserve original provider bytes and parse through the exact artifact: legacy
  parser permits noncanonical JSON, delivery generator requires canonical JSON.
  Do not normalize either input before its generator-specific validation.
- FanOutToolRegistry has tagged kind, registry_id, version, ordered entries.
  Each entry names tool_name/version/schema_id and references exact ToolSpec bytes
  (subject schema_id, record:+ToolSpec.digest, fingerprint ToolSpec.digest), policy
  reference, classification PROPOSAL_ONLY/CONSEQUENTIAL/READ_ONLY, retry policy
  NotApplicable|ReadOnlyFanOutPolicy(max_attempts,budget_version,reducer_id/version).
  Entries lexical by (tool_name,tool_version,schema_id), unique and exactly cover
  selected artifact.tools. Registry head derives from its canonical bytes, subject
  registry_id. No implicit unknown tool fallback. Producer independently knows
  supported ToolSpec/classification combinations; a supplied registry cannot relabel
  existing proposal tools consequential. New tool kinds require registered parser
  schema changes before runtime support, not free-form supplied registry entries.
- Requested ordered_calls match all parsed Continue calls in original array order.
  ordinal zero-based; canonical_call_base64 contains each parsed ToolCall's exact
  canonical_bytes(); model_call_label=call_id. Original capture ref same as above.
  Tool policy entry determines classification and frozen readonly retry parameters;
  readonly lineage_id='retry:'+original_call_id, non-readonly NotApplicable.
- Legacy Complete and expanded DeliveryCompletion parse to zero calls for sealing;
  sealing does not complete delivery, grant publication or reconstruct its companions.
  Runtime integration must combine the seal and the appropriate completion pipeline.
- CapturedFanOutProposal wraps fan_out:PreparedCallFanOut plus source_request_fingerprint
  (whole wrapper) and proposal_fingerprint. Inner prepared source remains innerrequest
  digest; seal ID remains response-seal:+inner request digest; no hash cycles. Outer
  digest excludes only outer proposal_fingerprint, retaining inner fingerprint.
- Semantic complete manifest bytes: RecoveryDTO-tagged JSON array of exact
  CallSubjectHead.model_dump(mode=json), response seal first then all initialized
  records in ordinal order; max_manifest measures this entire array (even zero calls
  retain the seal). The seal body itself lists only initialized references. Complete physical bytes cannot be known by pure owner because
  Run and conversation/effects companions are finalized elsewhere: broker must bound
  the actual final physical envelope before selection and each replay. No claim pure
  output length enforces max_serialized_batch_bytes. Define physical envelope in
  runtime integration contract before implementing its writer, not here.
- Result union is CapturedFanOutProposal|CallPreparationRejected; preparation-only
  port returns this union, no committed/replay shape.

Inherited dirty baseline: 12 reviewed staged files from R14 copied exactly; original
R14/R15/R16/R17 worktrees untouched. New scope estimated doc, new public module,
consumer test and reviewed catalog entry (16 total baseline+new paths, below35/40).
No canonical entrypoint/legacy factory/Run schema edits in this stage. Source catalog
r8-implementation-v1.json already carries baseline entries; new entry only.

Evidence map: selected capture/schema -> typed full Run evidence -> consumer retains
all nested bytes -> no opaque reference substitute -> missing/extra nested fields,
unknown kind, roundtrip -> public contract test. Remaining semantic guarantees and
all publication/authority/physical bounds remain HOLD under fanout-next plan matrix.

## Required semantic evidence beyond these shapes

| Claim | Owner input/decision | Independent observable | Forbidden substitute | Mutation boundary | Planned evidence |
|---|---|---|---|---|---|
| Exact selected response | captured Run, selector, raw bytes, artifact | owner reparses and matches all calls | caller subset or schema reference | omit/add/reorder/duplicate; wrong selected attempt | captured producer tests |
| Original identity | tenant/Run/Turn/capture/ordinal | exact stable initialized IDs | label alone or successor Run identity | repeated labels, foreign physical subject | identity tests and restart history |
| Registered class | complete tool registry and artifact specs | mismatch rejected by registered parser/classifier | caller declares proposal consequential | unknown/substituted policy/classification | policy mutation tests |
| Nonresettable read-only budget | original retry policy and derived lineage | same original budget/lineage after replay | allocate at retry | N/N+1 attempts, changed reducer/budget | original initialization tests |
| Complete bounded manifest | seal then all initialized refs | exact bytes/length/closure | count or initialized subset alone | zero calls, N/N+1 count and bytes | producer bounds tests |
| Atomic bounded physical fan-out | exact final Run+seal+calls+completion companions | journal/SQL identical one-sequence membership | semantic DTO length as physical bound | drop/add/reorder/oversize companion | canonical writer histories |
| Current authority | actual source cut and worker session | stale writer admission rejects | supplied evidence construction | mutate after IPC | writer race tests |
| Exact replay | independently selected full physical bytes | same membership/readback despite later head | matching Run-only fingerprint | changed companion, lost ack, later anchor | recovery/replay tests |

Public consumer tests establish immutable closed wire transport, preservation
of selected inputs and explicit failure/result variants. They grant no authorization.
The pure producer now checks selected capture/schema, exact ordered proposal calls,
registered proposal classification, original identities and semantic bounds. Runtime
rows, consequential/read-only tool schema support, retry budgets/lineage execution,
successor capture handling, scheduler admission and physical publication remain HOLD.

Validation commands: `uv run pytest -q tests/capabilities/agent_loop/test_fan_out_contracts.py`
(29 passed); `uv run pytest -q tests/capabilities/agent_loop/test_fan_out_preparation.py`
(56 passed). Producer cases cover both registered generators and Complete variants,
rehashed schema/identity substitutions, complete call coverage, original inventory
collision, count/response/manifest byte boundaries and scheduler root with a mismatched
fence classification. `sh .harness/scripts/lint.sh` and
`sh .harness/scripts/test.sh --preflight` passed. These are bounded checks, not a
new complete-runtime gate. The isolated code review found no confirmed P0/P1 within
pure scope; same-model review is not independent external verification.

## Source and compatibility map

Run capture/parser: agent_loop/contracts.py, domain.py, response_parsing.py,
delivery_preparation.py. Existing lifecycle DTOs: call_acceptance_contracts.py.
Runtime writer/history: composition/r14_fanout.py and r14_loop_history.py, using
the existing EventAppender and independent loop journal. Both R14LoopStore.snapshot
and composition/r13_planning.py::_proposal use the authenticated R14 history hook.
Materialization/replay: platform/_sqlite.py; extra schemas: R7PlanningRuntime's
_record_schema_variants. The existing LoopStore fingerprint covers only Run bytes
and substrate REPLAY bypasses its guards: full selected companion verification
is required above that substrate. The pure
`fan_out_preparation.prepare_captured_fan_out` producer is exposed by
`agent_loop.prepare_captured_fan_out` with request schema
`chiplog.call.captured-fanout-preparation.v1` and result schema
`chiplog.call.captured-fanout-result.v1`. The canonical R14 factory admits manifest
v6; explicit v4/v5 manifests retain their historical routes and exact targets.
Canonical open_r14_loop now routes ModelResponseReceived and legacy CompleteAcceptance
through the registered atomic fan-out publisher. Explicit R13 fixture entrypoints retain
the legacy representation; expanded delivery and executable tool authority are not enabled.

IPC evidence: `uv run pytest -q tests/platform/test_fan_out_owner_process.py`
passed 15 cases with real subprocesses, both parsers/Complete variants, malformed
transport and typed domain refusals, continued process usability, old route support,
leaf substitution rejection and canonical factory attestation. The broader targeted
manifest/process/recovery/finalization regression passed 47 cases; commands/logs are
recorded in `.artifacts/integration/fanout-ipc-checkpoint.json`. These results prove
preparation transport and existing runtime compatibility, not durable fan-out.
Preflight and isolated routing review passed; full final integration gate remains
required after the physical publisher and authenticated readers are implemented.

## Physical publication boundary

Broker-private `composition/r14_fanout_contracts.py` defines the retained preparation
and physical envelope. These values grant no authority. The operation is
`agent_loop.fanout.v1`; lifecycle schema IDs are `chiplog.call.response-seal.v1` and
`chiplog.call.initialized.v1`. Physical order is Run, seal, every initialized call,
then the existing Complete conversation companion when applicable. Lifecycle record
IDs are the content-reference heads, `record:` plus their canonical-byte SHA-256.

The physical fingerprint hashes the kind-first, recursively lexical envelope with
only `request_fingerprint` omitted. The physical byte limit counts the complete
envelope including that fingerprint and all base64 payloads. The independent journal
retains original request/proposal, accepted Run, companions, caller/callee sessions,
request ID, deadline and predecessor LoopSnapshot fingerprint separately. Nested Run
and DTO hashes always use the nested object's own canonical_bytes method.

Fresh admission must authenticate all inputs and invoke the caller's validation
predicate inside the writer guard. Historical replay compares the selected envelope
and original predecessor; it does not reevaluate current policy or regenerate owner
outputs. The canonical implementation is in r14_fanout.py and r14_fanout_records.py. Private
issuance constructs the complete registry/bound source observations; the generic value
verifier grants no authority and is not a substitute for that authenticated construction.
The shared history reader checks independently selected evidence and exact physical bytes.
Boundary evidence is tracked in the atomic-publication plan and checkpoint.

The immutable offline profile caps calls at 256 and also respects Run.policy.max_tool_calls,
semantic manifest bytes at 64 KiB, and the full physical envelope at 16 MiB. The IPC
transport's 8 MiB frame limit remains independent. Larger inputs may fail transport
earlier; they cannot bypass the physical publication limit. No supplied registry can
upgrade these proposal-only tools to executable or consequential authority.
