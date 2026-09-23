# R15 one-delivery tick publication

Status: executable hermetic one-delivery allocation and historical readback.
Existing GENESIS/configuration policies grant no tick authority. A reusable
automatic service mandate, overflow resolution, lease takeover/rollover and
scheduled execution remain required full-R15 follow-on work. Scheduled worker
publication currently rejects until live lease fencing is integrated with R14.

The public module `composition.r15_tick_contracts` exports SchedulerTickPort,
TickPolicyDraft, TickPolicyPreview and TickPolicyAdoption. Preview expands one
delivery into exact adopted schedule/policy/bound heads and a versioned policy
containing the registered clock source. Consumers persist the preview's exact
canonical bytes and stable act ID. Adoption takes no cutoff, authority reference,
prepared records or source observation. Preview is not permission; construction
of any contract supplies no authority. Malformed bytes remain lossless transport
for runtime rejection. The one-delivery policy grants neither execution nor send.

`r15_tick_runtime` authenticates the caller through the existing isolated trust
operation, captures the clock and source cut, invokes isolated
`scheduler.prepare_interval`, validates its complete output, and publishes through
the existing private authority, journal and sole writer. Readback returns selected
Run initialization records together with ordinary loop history in the legacy
R14 production profile. R15 extends the inherited R14 record-schema registrations,
but its joint history reader does not yet admit canonical captured-fanout or
cancellation envelopes; those paths require the remaining R14 integration.

TickClockPort is a composition-only synchronous observation seam shared by
production and evaluation. A reading pairs unix coordinate time with monotonic
time. The broker captures source identity, observation ID, epoch/session and a
freshness deadline capped by authentication expiry. At final writer admission,
a fresh clock reading rejects backward time, expiry and source replacement.
This is the admission boundary; no further clock sample is taken during the
following SQL writes or independent journal selection. The registered system and
evaluation clocks live in `r15_tick_clock_v1.py`; that leaf's bytes define their
implementation fingerprint. Future publisher changes must preserve the leaf;
a changed clock implementation requires an explicitly versioned source and
historical validation route. The deployment profile remains hermetic-offline.

TickIssuanceEvidence is the strict `chiplog.scheduler.tick-issuance.v1`
interpretation of existing WorkerAuthentication.applicability_bytes. It retains
exact adoption, registered policy, original trust frames/physical observation,
generation preimages, complete physical source cut, clock observation, issuance
nonce, interval preparation and isolated owner frames. The historical verifier
parses these under closed schemas and checks their cross-links against selected
journal bytes and the registered deterministic owner compiler.

The authority hash commits adopted policy, authenticated principal/worker/
generation and registered source; context additionally commits clock observation,
source cut and issuance nonce. Neither includes the final command hash. The final
command commits derived context and original cutoff; private invocation commits
final command/publication identity and full retained evidence. The stable replay
key is tenant/principal/operation/act ID, excluding payload.

Retained tick evidence is validated on historical reads, exact replay, and before
inherited pending physical recovery. Missing/substituted clock or policy
provenance rejects even if owner output is internally consistent. Multiple schema
and operation signals classify tick batches, preventing operation relabeling from
bypassing validation. Exact historical replay uses the original observation and
selected bytes; current caller authentication remains separate. Configuration-only
adoption bytes fail at the tick boundary.

`test_scheduler_interval_runtime.py` exercises actual isolated owner preparation,
zero/one/multiple due occurrences, COALESCE/SKIP/MATERIALIZE_EACH, all three bound
dimensions, overflow blocking, exact restart replay, before/after SQL-commit
recovery, stale configuration, clock regression, generation replacement, malformed
retained evidence and unfenced scheduled-worker rejection. The coexistence case
publishes an ordinary planning-tool call through the legacy embedded fanout to a
terminal result and completes its Run alongside scheduled initialization history. Consumer tests independently
exercise transport shape and exact-byte persistence. These checks establish this
bounded allocation slice; they do not establish automatic service authority,
scheduled execution, effect sending or non-hermetic ingress authentication.
