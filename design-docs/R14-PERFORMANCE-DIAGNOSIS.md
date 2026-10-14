# R14 multi-member latency: differential diagnosis

Status: OPEN. Snapshot date: 2026-10-14. This note records the current performance
problem and a bounded plan to identify its cause. It does not authorize a change
to R14 deadlines or integrity checks.

## Problem and scope

The mounted three-member R14 scenario becomes much slower with each accepted
member. Its V3 read and replay path also takes minutes. The observed progression
looks superlinear, but the current sample is not enough to call it exponential or
to identify a dominant operation.

Scope: diagnose the accepted-call/terminal path and the V3 read/replay path in
`test_v3_reads_three_accepted_members_across_delayed_dispatch_and_replay`.
Out of scope: load/stress testing, broad performance work, changing admission
horizons, weakening source authentication, or optimizing a path before a probe
shows it dominates.

Success means: (1) each stage interval has verified provenance and workload
cardinality; (2) one or more measurements identify the dominant repeated work or
wait; and (3) a small follow-up comparison can falsify the leading explanation.
If the first probe does not discriminate, keep the cause unresolved and add only
the next measurement needed.

## What was measured

Diagnostic command:

```sh
TMPDIR=/private/tmp/r14pytest PYTHONPATH=/private/tmp \
  uv --cache-dir /private/tmp/uvcache run pytest -s -q \
  tests/composition/test_r14_native_selected_accounting_source.py::test_v3_reads_three_accepted_members_across_delayed_dispatch_and_replay \
  -p r14_terminal_timing_plugin
```

The plugin wrapped `_accept_native_member`, `_close_accepted_member`,
`authorize_dispatch`, `commit_first_send`, `resolve_dispatch_outcome`, and
`publish_accepted_direct_terminal`. It reported elapsed wall time, not CPU time.
All intervals below are from this one mounted run; the scenario accepted three
`request_self_effect` calls, with dispatch outcomes `CONFIRM`,
`PERMANENT_NO_EFFECT`, `CONFIRM`.

| Member | Accept helper | Authorize | Send | Resolve | Terminal | Close helper total |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 1.94 s | 1.65 s | 2.06 s | 1.81 s | 6.50 s | 18.38 s |
| 2 | 8.17 s | 7.34 s | 8.06 s | 6.25 s | 22.93 s | 69.13 s |
| 3 | 24.81 s | 18.19 s | 19.47 s | 14.90 s | 54.67 s | 167.47 s |

The subsequent direct-terminal replay calls took 102.52 s, 103.87 s, and
80.93 s. The test was interrupted after 860.29 s while continuing through the
V3/replay portion (`KeyboardInterrupt`; pytest reported “no tests ran”). This is
not a passing or failing test result. A prior quiet run was also interrupted at
541.79 s and supplied no phase timings.

These data establish a large, rising wall-time cost in the intended mounted
scenario. They do not separate CPU from I/O or owner wait; they do not prove an
exponential growth law. The isolated timing calls are nested inside the helper
totals, so totals must not be added together.

After interruption, the pytest fixture remained available under
`/private/tmp/r14pytest/pytest-of-thunderbird/pytest-7/test_v3_reads_three_accepted_m0`.
`du -sh` reported 604 MiB for the fixture. `wc -l -c` reported:

| Artifact | Records | Bytes |
|---|---:|---:|
| `native-v23-prepare.sqlite.owners-journal` | 48 JSON lines | 501,464,722 |
| `native-v23-prepare.sqlite.loop-journal` | 26 JSON lines | 81,866,322 |
| `native-v23-prepare.call-previews` | 3 records | 37,320,090 |
| `native-v23-prepare.sqlite` | — | 5,738,496 |

The selected owner-journal records grow sharply across the three accepted calls.
In the `effects.accept_call` records, the retained effects command contains 0,
6, and 12 complete effect-history members respectively:

| Selected owner operation | Member 1 record | Member 2 record | Member 3 record |
|---|---:|---:|---:|
| `effects.accept_call` | 8.16 MB | 53.70 MB | 103.66 MB |
| `effects.authorize` | 16.00 MB | 48.30 MB | 83.64 MB |
| `effects.commit_send` | 18.52 MB | 51.06 MB | 86.64 MB |

Their serialized `applicability_bytes` fields alone were 3.07 / 22.88 / 44.63 MB
for `accept_call`, 6.13 / 19.29 / 33.70 MB for `authorize`, and 7.27 / 20.55 /
35.07 MB for `commit_send`. These field lengths are measured in the encoded
owner-journal JSON representation. The loop journal also contains a 62.3 MB V3
selection, whose retained `native_sealed_accounting_v3_evidence` field is 26.5
MB. These are observed serialized artifact sizes, not inferred read times.

This changes measurement priority: a few journal records carry tens to hundreds
of megabytes. Large retained payloads and the cost to serialize, authenticate,
transport, append, and reread them are now the leading hypothesis. The SQLite
file is much smaller than the journals, which lowers the priority of database
volume as the sole explanation but does not rule out repeated full-table
commitment capture. The fixture sizes also make a pure “many small journal rows”
explanation unlikely.

### Single-payload parse/canonicalize discriminator

To test whether one large retained issuance is itself enough to explain the
multi-second stages, the selected owner-journal records were decoded from their
serialized `applicability_bytes`, parsed with the exact `CallAcceptanceIssuance`
or `DispatchIssuance` model, and canonicalized again. Canonical bytes were
asserted equal to the retained bytes. This used one pass per record in one local
Python process, without opening SQLite, starting the owner, or sending IPC.
The script streamed the selected owner-journal lines (7, 9, 11, 35, 37, 39),
decoded the outer decision and base64url applicability field, timed model parse
and `canonical_bytes()` separately with `process_time_ns()`, and asserted the
canonical bytes equaled the decoded issuance. Base64url decode time is excluded
from the table.

| Issuance | Serialized input | Parse CPU | Canonicalize CPU |
|---|---:|---:|---:|
| accept call 1 | 2.30 MB | 4.6 ms | 10.3 ms |
| accept call 3 | 33.47 MB | 30.3 ms | 148.7 ms |
| authorize 1 | 4.59 MB | 4.5 ms | 19.7 ms |
| authorize 3 | 25.27 MB | 21.8 ms | 111.9 ms |
| send 1 | 5.46 MB | 4.9 ms | 23.4 ms |
| send 3 | 26.30 MB | 20.3 ms | 119.6 ms |

This rules out one ordinary parse/canonicalize pass as the explanation for a
15–55 s stage. It does not rule out many repeated passes, journal traversal, IPC
carrying large requests, or database/source validation. Large payloads are a
confirmed durable-data amplification problem, but not yet proof of the dominant
latency source.

### Frozen-snapshot read discriminator

A temporary mounted profiler reused the saved three-member fixture, opened the
runtime once, and called the synchronous, target-qualified
`read_selected_accepted_direct_terminal` for the first and third member. It did
not rebuild or dispatch the three-member scenario. The test passed. Command:

```sh
TMPDIR=/private/tmp/r14pytest PYTHONPATH=/private/tmp uv --cache-dir /private/tmp/uvcache run pytest -s -q /private/tmp/test_r14_profile_saved_snapshot.py
```

| Span | Wall | Process CPU |
|---|---:|---:|
| Runtime open / startup | 128.1 s | not captured as one outer interval |
| Read first target | 60.5 s | 59.9 s |
| Read third target on the same snapshot | 60.0 s | 59.7 s |

During these reads, repeated `_read_call_history` calls took about 14 s each;
`_current_cut` usually took about 2.0–2.5 s, with one observed call at 16.1 s.
`validate_selected_accepted_direct_terminal` took about 45.8 s for each read.
The profiler did not tag every nested helper with its enclosing target, so it
does not establish an exact per-read call count. The target-qualified reader
path is synchronous and does not make an owner IPC call. On this frozen
snapshot, the ~60 s read is therefore CPU-bound local history/source validation,
not socket waiting. The nearly equal first/third target times also show that
the expensive read does not materially depend on which of those two members was
requested.

This rules out owner IPC as the explanation for the read/replay latency. The
profile shows CPU-bound work, but provides no evidence of GIL contention: it did
not measure competing threads or lock wait. It does not yet say which
sub-operation inside history validation consumes the CPU.

### History-read CPU profile

A cProfile run during a separate mounted R14 test measured one
`read_execution_history` / `_read_call_history` interval at 10.06 s CPU. The
test later failed with
`DEADLINE_EXCEEDED` while publishing `AccumulateExecutionVisibility`; it is a
completed profiler interval, not a passing test result. The profile attributed
3.58 s cumulative to 990 `json.loads` calls, 3.37 s to 3,113 `json.dumps`
iterations through `canonical_bytes`, and 3.47 s to
`decode_selected_native_accounting`. Those costs overlap in the call tree and
must not be added. It also attributed 2.91 s to
`_verify_original_prepare_projection`, 1.56 s to deployment-journal entries,
and 1.46 s to contract digests; these are cumulative values, some nested under
the JSON/canonicalization work.

The profile narrows the CPU explanation: history reconstruction performs many
small JSON decode/canonicalization operations and selected-accounting
validation. It does not implicate one 25–33 MB issuance parse, which was
measured separately and took at most 150 ms for canonicalization. It also does
not yet show whether the 986 canonicalizations are repeated work over the same
objects or required checks across distinct records. The profiled 10.06 s
interval is only part of a ~60 s target read; startup and terminal validation
remain separate costs. No suffix-fallback count was captured.

The next discriminator is a bounded call-tree breakdown of the target read:
measure exclusive CPU and invocation count for selected-accounting decode,
original-prepare projection, workspace issuance scan, authority snapshot
capture, effects matching, and suffix fallback. Record visited record counts
and bytes once per helper. Compare first and third target reads on the same
frozen snapshot; equal cardinalities should produce comparable helper counts if
work is snapshot-wide. Avoid per-row logging or another full acceptance/replay
scenario.

### Current-source bounded target read

On 2026-10-14, one pytest-mounted read of the first member in the saved
three-member fixture passed against HEAD
`3715201f3663b2f2e08fcf0401cb242b9d30443d` plus the current uncommitted source
tree. A SHA-256 manifest over 572 tracked Python files under `src/chiplog` and
the target test is
`e635f3482cb5fbaf1d07015a18370f517851e14ec114420191a44ed6ada2f066`. The
fixture was opened at its bound original path and database identity; the
profiled target was
`call:6e8f398f9c05865e6b6c80ae802186b99464f33f86cf03fe0a6730288f730ca9`.

Startup took 122.9 s wall / 117.5 s process CPU and changed only the broker,
gate, and reads SQLite files plus SQLite WAL/SHM sidecars. The main database,
selected loop/owner/trust journals and heads, previews, custody files, and
target evidence stayed unchanged. The target read returned the selected result
in 61.3 s wall / 60.0 s process CPU. The outer `process_time_ns()` measurement
confirms a CPU-bound local read in the current source. The default cProfile
timer measures elapsed wall time, not process CPU; its total was 61.28 s and
the function-level attributions below are elapsed-time attribution. This is
one localization sample, not a scaling curve.

Top exclusive profiled-time attribution:

| Operation | Calls | Exclusive profiled time |
|---|---:|---:|
| JSON encoder `iterencode` | 18,690 | 24.22 s |
| JSON decoder `raw_decode` | 5,234 | 7.73 s |
| file reads | 428 | 4.83 s |
| Pydantic `validate_json` | 3,122 | 3.94 s |
| SHA-256 | 20,928 | 3.85 s |
| Pydantic `to_python` | 20,815 | 3.25 s |
| Pydantic `to_json` | 469 | 2.46 s |
| independent journal `_entries` | 201 | 2.28 s |

High-level cumulative profiled elapsed time overlaps and must not be summed: four
`_read_call_history` calls accounted for 57.27 s cumulative; within them,
`_read_materialized_effects_with_history` ran 13 times (17.30 s cumulative),
`verify_dispatch_history` ran 13 times (19.86 s cumulative), and
`decode_call_issuance` ran 51 times (16.14 s cumulative). This ranks repeated
R16 effects/dispatch history validation and its JSON/canonicalization work as
the leading explanation for the current target-read cost. The counts alone do
not show which operations visit the same decisions or how much serialization
is over repeated versus distinct evidence.

The H1 discriminator is negative: eight H1 issuance scans used the same
132,250-byte one-entry sequence digest and anchored head, with eight distinct
journal wrapper instances. Each scan took 1.64–1.90 ms CPU; this is negligible
against a 60-second read. They are separate wrapper validations, but the
current measurement does not justify changing them.

After runtime close, no `chiplog-r7-*` owner child remained. The fixture was
restored in place and rechecked: all 45 files matched the recovery image by
path, size, SHA-256, and mode, and the main database retained device/inode
`16777234/145040521`. No production code changed during this probe.

### Repeat-prefix differential probe

A second pytest-mounted profile on 2026-10-14 instrumented one read of the same
target and exact source manifest. Runtime startup took 120.6 s wall / 117.4 s
process CPU. The target read took 62.9 s wall / 61.2 s process CPU; cProfile's
elapsed timer recorded 62.87 s. The small increase over the uninstrumented
61.3/60.0 s sample bounds the cost of this boundary instrumentation. The test
passed, no owner child remained, and fixture restoration again verified all 45
paths, contents, modes, and the main database device/inode.

The identity comparison found:

- Thirteen `verify_dispatch_history` calls received the same selected owner
  decisions and heads, the same materialized command set, the same physical DB
  identity, tenant frontier 37, materialization commitment, and ordered effect
  record IDs. This is one complete input group repeated 13 times. Their total
  inclusive CPU was 19.27 s and their exclusive CPU was 5.47 s. Inclusive time
  contains child request decoding and must not be added to it.
- Fifty-one `decode_call_issuance` calls reduced to three
  applicability-fingerprint/byte-length pairs, each repeated 17 times. The
  three inputs were 2.30 MB, 17.16 MB, and 33.47 MB. That is about 900 MB of
  retained applicability bytes presented to the decoder across this one read.
  The decoder itself recomputes SHA-256 and checks each fingerprint, so this is
  a content-addressed identity; instrumentation did not separately compare raw
  byte strings. Measured decoder CPU was 15.47 s. The profile call tree
  independently attributes about 15.8 s cumulative to these calls. This is
  roughly a quarter of total read CPU, before counting surrounding validation.
- Twelve effects acquisitions used the same owner/effects cut and the same
  selected execution-history head and record heads. Each still performed its
  own physical membership and current commitment checks. One additional
  acquisition had no authenticated execution-history argument; it caused a
  nested `read_execution_history` reconstruction of the same tenant head and
  eight record heads, taking about 14.0 s CPU. Source tracing corrected the
  initial reuse candidate: `_current_cut`'s recovery snapshot is collected
  with `selected_only=True`, while the nested reader runs the ordinary strict
  `_read_call_history` mode. Those snapshots must not be treated as
  interchangeable on their own. However,
  `read_selected_accepted_direct_terminal` immediately performs an ordinary
  strict `_read_call_history(runtime)` and discards its result before selected
  terminal validation. That result uses the same default mode as the nested
  `read_execution_history` call. A narrow experiment can capture that already
  authenticated snapshot and pass it through `authenticated_effects_history`,
  while retaining the effects reader's current physical comparisons.
- The eight workspace issuance scans remained negligible at roughly 2 ms
  apiece. The suffix callback was supplied on the effects acquisitions but the
  physical commitment equaled the anchor, so no suffix authentication ran.

This establishes repeated identical dispatch-history inputs and repeated
content-addressed applicability inputs, not that all enclosing calls are
redundant: target-specific joins and each fresh physical membership/currentness
check may still be required. Source tracing locates a narrower reuse seam:
`read_selected_accepted_direct_terminal` first runs the full strict
`_read_call_history(runtime)` and discards its returned `ExecutionHistorySnapshot`;
the ordinary `read_execution_history` invoked later by the effects reader runs
that same strict mode. The separately collected recovery snapshot in
`_current_cut` is selected-only and is not the candidate. The A/B treatment
should retain and forward only the first strict snapshot through the existing
`authenticated_effects_history` argument. The effects reader still checks
`snapshot.tenant_head == frontier`, exact selected record bytes against physical
records, current authority commitment, owner journal, and database identity.
The same-runtime paired read below tests this reuse seam. It does not support
a deadline change, cross-boundary cache, or durable evidence compaction.

The mounted differential ran on the exact original fixture path because
dispatch custody binds the physical database path and inode; an isolated copy is
correctly rejected. Treatment forwarded the exact outer strict snapshot, while
baseline left the reader unchanged. Both arms returned byte-identical terminal
results. Treatment performed 10 effects-history reads and baseline 13, so the
probe's original assertion that total call counts must be equal failed. That
assertion was too strict: the saved baseline call tree below identifies those
three acquisitions as one nested strict-history replay. Treatment took 43.15 s
wall / 42.84 s process CPU under instrumentation; this is not a paired net
savings estimate. The fixture was restored and verified after the run, with no
owner child left running.

A saved full baseline trace (`/private/tmp/r14repeat_profile.log`) resolves the
13-to-10 difference without another mounted run. It contains 198 parsed spans
and four `_read_call_history` nodes: `read_call_history:0` (strict), `:46`
(selected-only), `:92` (strict), and `:138` (strict, nested under
`effects_history:138`). Each history node contains three effects-history reads;
the trigger `effects_history:138` is the thirteenth acquisition. The nested
`:138` subtree adds three effects reads, three dispatch-history verifications,
27 dispatch request decodes, and 12 call-issuance decodes. The paired treatment
reported 10 effects reads, 10 dispatch verifications, 90 dispatch request
decodes, and 39 call-issuance decodes, matching removal of that one nested
subtree. All 12 history-child acquisitions in the baseline had equal
owner/cut/execution-history identity values. The treatment history modes were
strict, selected-only, strict; the nested strict replay is absent. Thus the
three removed acquisitions and their child work are exactly the recursive
strict-history replay subtree, whose input identity had already been
authenticated by the earlier strict replay.

That subtree took 14.00 s wall and 14.00 s process CPU inclusive in the saved
baseline. This bounds a concrete repeated-read opportunity at roughly 14 s CPU
for this fixture; it is not a stable benchmark. The paired treatment returned
byte-identical terminal output. Source inspection confirms the effects reader
still captures the current authority commitment, owner journal, complete
physical effects membership, exact records, and database identity. Its
`_reconstruct_registered_runs` compares the supplied immutable snapshot's
tenant head and exact Run bytes against the fresh physical cut. The removed
work is the nested reconstruction of those same member frontiers, not the
outer fresh physical effects acquisition. The original comparator was too
strict to require equal total effects-call counts; its count assertion fired
before the later signature assertion. This differential supports forwarding
the outer strict snapshot within this read boundary. Keep the separate
selected-only snapshot from `_current_cut` out of this reuse path, and do not
extend reuse across writer or owner IPC boundaries.

### Differential narrowing: repeated workspace issuance scans (historical lead, superseded)

The older cProfile output suggested repeated source reconstruction as a
read-path hypothesis, ahead of one large-payload parse. In the measured
`read_execution_history` interval it recorded 6 calls to
`H1WorkspaceIssuanceJournal._scan` (3.139 s cumulative), 4 calls to `load`
(2.091 s cumulative), and 2 calls to `open` (2.027 s cumulative). These are
nested values and must not be summed. The same interval spent 3.474 s cumulative
in selected-accounting decode and 2.906 s in original-Prepare projection; these
also overlap the journal work.

The profile artifact does not record the source revision or scan callsites, so
these counts do not yet establish redundant scans. Current source already
caches decoded journal entries when body bytes and the anchored head match
(`deployment_trust/_journal.py::_entries`). `H1WorkspaceIssuanceJournal.load`
reuses its validated tuple when the journal returns the same tuple object; a
new H1 journal wrapper performs its own initial scan, and changed source bytes
can also force a rescan. The reader and accounting projection both reopen
selected H1 evidence, but the profile does not show whether the six scans
visited the same byte snapshot, were cache misses, or authenticated distinct
selected sources.

The current-source eight-scan discriminator above supersedes this historical
lead: the scans read the same 132,250-byte sequence and each took under 2 ms.
That cost is negligible for the current 60-second read. No more workspace
issuance scan probe is justified unless a different workload shows a material
scan count or size.

### Probe execution boundary

An attempted direct-script profile did not reach the target read. First,
`spawn` replayed top-level profiler imports in owner children and introduced two
extra `*_process` modules into the exact owner-attestation closure. A read-only
dry load reproduced the mismatch: the loaded set had unexpected
`deployment_trust._j7_grant_process` and `deployment_trust._r7_process`, with no
missing expected modules. Moving composition imports inside guarded `main()`
fixed that harness issue, but the direct runner's preservation guard then
skipped the target read after startup changed broker, gate, and reads SQLite
files. A later pytest-mounted probe kept the normal spawn entry point and
successfully measured the current target read; its result is recorded above.

The direct attempt adds no target-read timing and its startup duration is not
isolated enough to rank against the read-path hypotheses. The later pytest
profile measures only the target read after startup and records that startup
separately.

The earlier cProfile artifact was last modified on 2026-10-12, while the
current HEAD is dated 2026-10-13. Its source revision was not recorded. Treat
its 6/4/2 H1 counts as historical observations; the current eight-scan result
above supersedes them for the current source and target.

## Competing hypotheses

Rank is for measurement order, not a conclusion about root cause.

## Current differential result

- **Ruled out for terminal reads:** owner IPC as the source of the ~60 s frozen
  read, because the path is synchronous and local; one parse/canonicalize pass
  over a single large issuance, because it costs at most 150 ms CPU.
- **Established:** the frozen terminal read is almost entirely local process
  CPU; the current mounted read spends about 60 s of process CPU. Its
  cProfile attribution places substantial elapsed time in JSON encoding and
  decoding, while the earlier history interval spent about 10 s in repeated
  JSON decode/canonicalization and selected-accounting validation. The persisted
  records also contain very large repeated evidence fields.
- **Established for this target read:** the baseline's 13 dispatch-history
  verifications received the same authenticated owner/effects cut; three
  applicability fingerprints were decoded 17 times each. The paired treatment
  returned identical result bytes and reduced counts from 13 to 10 effects
  reads, 13 to 10 dispatch verifications, 117 to 90 dispatch request decodes,
  and 51 to 39 call-issuance decodes. The saved call tree identifies the entire
  delta as one nested strict-history replay that revalidated the same member
  frontiers already covered by the outer strict snapshot. Other repeated
  validations remain in the 43-second treatment read and may still support
  distinct target-member joins. The slow writer path may still include owner
  IPC; the read measurement says nothing about that.
- **Not supported yet:** GIL contention. There is no thread or lock-wait
  measurement. The suffix callback ran zero times in this target read; its
  use in the slower writer phases remains unmeasured.

The three writer-stage observations do not support the word “exponential.”
Successive time multipliers decline in every measured phase:

| Phase | member 1 → 2 | member 2 → 3 |
|---|---:|---:|
| Accept | ×4.21 | ×3.04 |
| Authorize | ×4.45 | ×2.48 |
| Send | ×3.91 | ×2.42 |
| Resolve | ×3.45 | ×2.38 |
| Terminal | ×3.53 | ×2.38 |

That establishes steep growth over the observed range, but three naturally
ordered points cannot distinguish polynomial growth, changing work volume,
cache effects, or another curve. Accept-record bytes grow from 8.16 MB to
103.66 MB (×12.7) across the same endpoints, close to its ×12.8 wall-time
increase; this is consistent with payload volume amplifying cost, not proof
that it causes the writer delay. The authorize and send byte/time ratios do not
track as closely. Keep “exponential” as an unverified impression, not a fitted
complexity claim.

The remaining read-path discriminator is to separate repeated validations
that are still present after removing the identified nested replay from distinct
required target-member joins, then attribute JSON time to those calls. Do not
change deadlines or compact durable evidence: these measurements do not support
either change. The three-member writer progression remains a separate,
unprofiled question; three correlated observations do not establish an
exponential law.

### Differential triage: what evidence would eliminate each route?

The current evidence supports a local-CPU explanation for the frozen terminal
read, but it does not explain the accepted-call writer deadline. Keep those as
separate dependent variables. The reader has already had one verified redundant
strict-history reconstruction removed; the remaining repeated dispatch replay
and issuance decoding are candidates for later local reuse, not evidence that a
writer cache or looser deadline is safe.

| Candidate explanation / action | Evidence that distinguishes it | Eliminate or defer when |
|---|---|---|
| Common dispatch replay is blocking the accepted-call writer | Source tracing confirms the writer path reaches `verify_dispatch_history`: `check_prepared → _check_live → capture_dispatch`. On the third accepted call, time that verifier and its input identities inside admission; separately time call-specific joins. Compare with the remaining deadline at entry/exit. | Its exclusive CPU is too small to explain the deadline loss. Keep per-call joins regardless. |
| Large retained issuance decoding causes the growth | On that same call, attribute bytes and CPU to issuance decode, fingerprint verification, and surrounding validation; distinguish repeated identical bytes from different member-specific inputs. | Decodes are few/cheap on the writer path, or their total is below the measured excess. Do not drop durable evidence based on the reader profile. |
| Authority snapshot capture or source reconstruction dominates | Count captures and record rows/bytes; split SQL/fetch, canonicalization/hash, and Python reconstruction for the writer call. | Capture/reconstruction is a small fraction, or does not grow across the three already-observed call stages. |
| Owner IPC / lock waiting causes the timeout | Split caller wall/CPU, broker wait/serialization, and owner service wall/CPU for the writer operation. | Caller/owner CPU explains nearly all wall time and transport/lock wait is short. The local terminal-read result does not eliminate this writer-only route. |
| GIL contention causes the timeout | Record per-thread CPU and wait intervals while reproducing the same writer call; establish competing Python threads in the same process. | There is no concurrent Python work, or thread/lock wait is negligible. A single CPU-bound Python thread is CPU cost, not evidence of GIL contention. |
| Historical suffix fallback causes the timeout | Count callback invocations and time backup, suffix traversal, and commitment recapture on the writer path. | The callback count is zero. It was zero on the measured terminal read only. |
| Apparent exponential growth is a fixture/order artifact | First split setup from the operation and identify the actual per-call source counts on the existing 1/2/3 observations. Only if still blocking the horizontal scenario, repeat matched points with the same fixture/cache conditions. | The observed duration is explained by measured work counts, or matched points fail to reproduce the trend. Do not fit an exponential curve to three naturally ordered observations. |

### Deadline failure provenance discriminator

In `platform/r7_runtime.py::_call_admitted`, the `PublicPortRejected` failure
kind `DEADLINE_EXCEEDED` is produced at three caller-side checks: before entering
the guarded send path, after acquiring the authority/connection locks, and
after `_owner_request_bytes` plus `admission_guard`. In every case the function
returns before `_send_frame`. Repository search finds no second producer for
this exact failure kind. Therefore, if the slow auth failure observed by the
caller is this `PublicPortRejected` value, the owner has not received that
request; owner service time, owner-process GIL, and response transport cannot
explain that particular timeout.

That does not yet identify the caller-side delay. The second checkpoint follows
authority-gate and connection-lock acquisition, so it does not distinguish
those waits from each other. The final checkpoint follows request serialization
and `admission_guard`, so it does not distinguish either component. On the next
natural occurrence, record the exact failure type/kind and remaining monotonic
budget at entry, after gate acquisition, after connection-lock acquisition,
before/after `admission_guard`, and immediately before `_send_frame`. If the
failure is instead a socket/owner-process error, retain the owner/transport
hypothesis and trace frame-send to response separately. This provenance check
is cheaper and more discriminating than starting with GIL profiling.

Do not conflate that broker failure with the call-writer's `STALE` result.
`prepare_call_exchange` creates the loop acceptance-call deadline as the
minimum of the mandate expiry and `now + 5 s`, after the initial capture and
proposal construction. The later effects preparation call gets its own
`now + 5 s` deadline. `CallPublicationAuthority._check_live` later compares
fresh source time against the minimum of both owner-call deadlines, the effects
lease, mandate horizon, and all captured source-validity horizons;
`check_prepared` maps an expired/rejected live check to writer `STALE`. The
current code does not report which candidate won that minimum. Therefore the
existing read profile cannot establish whether the writer lost time in the
post-owner freshness reconstruction, entered it with little budget remaining,
or crossed a different source/lease horizon. Instrument each candidate's
remaining slack at owner-call issuance, writer-guard entry, after history
reconstruction, and at the rejection site; log the rejection class separately
from the R7 broker result. Correlate the owner request ID and command ID when
checking prior attempts, even though a pre-send rejection proves that particular
R7 request was not sent. Read deadline operands from the exact retained request
and record the clock source used at construction and comparison; do not
recompute an equivalent-looking horizon for the trace. Also record whether a
frame was sent and whether the matching owner/appender decision and tenant-head
advance exist. This distinguishes pre-publication rejection from late response
or acknowledgement after durable progress.

`check_prepared` maps every `LoopRejected` raised by `_check_live` to `STALE`,
not only deadline expiry. The same result can follow a changed captured cut,
inventory/acceptance mismatch, or failed dispatch scope. A `STALE` observation
alone therefore does not show that work ran too long. Record the exact failing
predicate and its expected/observed identity or version alongside deadline
slack. Use one monotonic timeline for request creation, guard entry, source
reconstruction, rejection, and send; source-validity values must be compared in
their declared clock domain. Also record whether another CPU-heavy read overlaps
the writer and, if so, queue/scheduling delay. If there is no overlap, shared
CPU starvation is retired as an explanation for that run.

Source tracing narrows the writer sequence further. The first `capture_dispatch`
collects the proposal's cut before the loop and effects owner exchanges. The
post-exchange `_check_live` obtains `read_execution_call_history`, passes that
snapshot into a fresh `capture_dispatch`, and then verifies the fresh effects
cut. The second pass is a freshness check across owner calls; reusing the first
cut across that boundary would erase the check. Within the second pass, however,
the exact history snapshot is already passed into `capture_dispatch`, so the
trace should look for duplicate work *inside* those calls rather than assume an
extra history read.

The narrow next probe, only if the horizontal user scenario still fails, is one
instrumented reproduction of the third accepted call with these stage boundaries:
initial cut and deadline issuance; each owner exchange; admission-gate wait;
post-exchange `read_execution_call_history`; fresh `capture_dispatch` split into
effects acquisition, `verify_dispatch_history`, and member-specific joins; final
physical publication; and remaining time against each deadline candidate at
every boundary. This single trace distinguishes CPU cost, owner wait, gate wait,
and which authority horizon actually wins the `min(...)`. It avoids a load
matrix and does not bypass any authority check. Until then, dispatch-result
reuse, issuance compaction, deadline changes, and GIL-oriented concurrency
changes are all deferred; each would otherwise target a different or unmeasured
cost.

The targeted third-accept script under `/private/tmp/r14_6_third_accept_probe.py`
points to a pytest fixture directory that no longer exists. Rebuilding that
pre-state would require another mounted multi-member run. Defer this dedicated
writer profile until the next ordinary horizontal scenario reaches the slow
accept phase; attach low-volume timing there rather than rerunning the full
scenario solely for diagnosis. Until that observation exists, writer CPU,
owner service/queue time, and GIL contention remain unresolved. The read-path
CPU profile must not be used to eliminate those writer-only explanations.

### H1 — Repeated JSON work over retained history dominates

Each accepted call carries a complete effect-history observation. The owner
journal records for `effects.accept_call`, `effects.authorize`, and
`effects.commit_send` grow from single-digit megabytes to 50–100 MB as accepted
members accumulate. The call issuance is canonicalized in
`r14_call_issuance.py`; the dispatch input captures `complete_effect_history` in
`r16_dispatch_inputs.py`; owner records preserve the selected request and its
authentication bytes. Current cProfile attribution shows 18,690 JSON encoder
calls with 24.22 s exclusive profiled elapsed time and 5,234 decoder calls with
7.73 s, but does not identify which callers process which bytes. This is a
leading hypothesis, not an established cause of member-to-member growth.

**Prediction:** caller-tagged JSON/canonicalization operations repeatedly
process the same applicability fingerprints or selected record heads; counts
times input bytes explain a substantial share of read CPU. A single
parse/canonicalization pass is too cheap to explain a stage by itself.

**Falsifier:** the large histories are each processed only once per stage and
their measured encode/decode/transport/append time remains below a small share
of total latency; another measured span then explains the increase.

### H2 — Repeated source/history reconstruction grows with the selected prefix

`publish_accepted_direct_terminal` validates selected terminal history, builds a
cut, and its publication guard rebuilds a cut. `_current_cut` collects selected
accounting and closed-outcome facts from execution and effects history. V3 also
revalidates source around owner IPC and visits accepted members to build the
result. Relevant code: `r14_accepted_direct_terminal_publication.py`,
`r14_native_sealed_accounting_v3_publication.py`, and
`r14_native_selected_accounting_source.py`.

**Prediction:** per-operation counts and time for history/source helpers rise
with history cardinality; repeated visits to the same prefix account for a
substantial share of wall or process CPU time.

**Falsifier:** helper counts, visited rows, and CPU remain nearly constant as
member/history cardinality grows, while another measured span explains the
increase.

### H3 — Effect row matching has a quadratic loop

In `R14NativeSelectedAccountingSource.collect_one_accepted_closed_outcome`, each
row in `cut.rows` searches `verified.v2_records` with a fresh `next(...)` scan
(`r14_native_selected_accounting_source.py`, around the `for row in cut.rows`
loop). This is a concrete candidate for O(rows × records) Python work.

**Prediction:** comparison count and process CPU grow close to the product of
those cardinalities, even when SQL and snapshot-capture time are small.

**Falsifier:** the loop has few iterations or negligible exclusive CPU time at
the slow cardinalities.

### H4 — Full authority snapshot capture is repeated over a growing database

`capture_authority_snapshot_commitment` materializes and hashes ordered rows
from registered authority tables (`platform/authority_reads.py`). History and
physical-publication validation call it at several boundaries.

**Prediction:** capture count × bytes/rows explains the rising time; SQL/fetch
and canonicalization/hash timings rise with authority-table size.

**Falsifier:** captures are infrequent and cheap relative to the slow spans, or
their cost stays flat while another span grows.

### H5 — Owner IPC or async waiting dominates

Owner calls cross the broker transport in `_exchange` and the R7 owner runtime.
The current wall timers include these stages but do not split broker wait, owner
service time, serialization, or filesystem/SQLite work inside the owner.

**Prediction:** in writer stages, wall time substantially exceeds caller and
owner process CPU; broker lock, send/receive, queue, or owner-service wait
accounts for the gap. This hypothesis is ruled out for the ~60 s synchronous
target-qualified terminal reads, which contain no owner IPC and use nearly all
their time as local process CPU.

**Falsifier:** owner and caller CPU account for most elapsed time, with short
transport waits.

GIL contention is a lower-priority sub-hypothesis: the current evidence does not
show competing Python threads in the same process, much less measure contention.
It should be revisited only if process/thread CPU and event-loop/lock timing point
to contention.

### H6 — Historical suffix authentication is an exceptional slow branch

`authenticate_selected_r14_suffix_held` backs up SQLite, removes suffix
publications in reverse, and recaptures the full authority commitment after
each removal (`r14_historical_suffix.py`). This can be expensive in both suffix
length and database size, but the effects reader reaches this fallback only
when physical and anchored commitments differ. Do not attribute the current run
to it unless a counter shows the fallback ran.

**Prediction:** suffix callback count is nonzero, with repeated backup and
commitment time proportional to suffix entries and table rows.

**Falsifier:** no fallback invocation in the slow stages.

### H7 — Measurement or fixture differences inflate the progression

The current series is one natural run where later members also see a larger
history and possibly different cache/process state. The plugin reports nested
wall intervals only.

**Prediction:** matched fixtures, controlled warm/cold state, or repeated runs
materially change the progression.

**Falsifier:** the same scaling recurs across matched fixtures with stable phase
boundaries and similar measurement overhead.

## Differential probe sequence

Start with one low-volume instrumented mounted run. Do not log per row. For each
span, record wall time, relevant process CPU time, call count, and input size.
Tag the span as fixture setup, accepted-call phase, terminal publication,
V3 publication, replay, or reopen.

| Probe | Measure | Rules out / distinguishes |
|---|---|---|
| 1. Provenance and stage split | Same command, test, manifest, runtime mode, warm/cold state, test-start/end; caller/owner PID; nested versus exclusive intervals; setup separated from operation | H7; prevents attributing overlapping timers twice |
| 2. Payload lifecycle | For accept/authorize/send/V3: encoded request bytes, evidence bytes by field, number of parse/validate/canonicalize passes, journal append bytes/time, transport bytes/time | H1 versus source scans or owner wait; one-pass CPU is already measured |
| 3. Source reconstruction counters | Calls and exclusive wall/CPU for `_current_cut`, `read_execution_recovery_history`, `R14OriginalNativePrepareSource.collect`, `R14NativeSelectedAccountingSource._collect_held`/`collect`, and terminal validation; loop/owner decisions, executions, members, and rows visited | H2 versus fixed per-call overhead or unrelated waits; split the observed 14 s history read and 16 s cut |
| 4. Effect reduction count | `len(cut.rows)`, `len(verified.v2_records)`, number of head comparisons in the nested matching loop, exclusive CPU | H3 versus SQLite/IPC explanations |
| 5. Snapshot capture | Capture count, table row/byte totals, SQL/fetch time, canonicalization/hash time | H4 versus Python source-rebuild and IPC work |
| 6. Owner IPC attribution | Caller CPU, broker lock/send/receive/serialization time, owner PID CPU and service time, request/response bytes | H5 and the GIL sub-hypothesis |
| 7. Conditional suffix branch | Callback/fallback count, backup time, suffix publications, commitment captures and rows | H6; stop investigating this branch if count is zero |

The following matched comparisons are backlog until the end-to-end user
prototype works; they are not current investigation steps. If still needed then,
compare accepted-call/terminal runs with 1, 2, and 3 accepted members, separating
fixture-build time and repeating each point under the same cache/process setup.
For V3 read/replay, request each member on a frozen snapshot while holding total
snapshot cardinality fixed. Vary unrelated journal history only if scan counts
rise; vary authority-table row counts only if commitment-capture counters rise.

The paired read ran on the original custody-bound fixture because the
physical path and inode are part of its custody. Treatment forwarded the strict
snapshot returned by the outer `read_selected_accepted_direct_terminal` history
read, not `_current_cut`'s selected-only recovery snapshot. Result bytes
matched, and the saved baseline trace identifies the three removed effects
acquisitions as the repeated nested strict-replay subtree. The remaining
physical effects acquisitions and source checks still ran. The treatment's
43.15 s wall / 42.84 s CPU is one order-biased observation; the approximately
14 s nested replay in the earlier baseline bounds the opportunity but does not
establish net savings in a controlled benchmark.

The accepted-call/terminal writer growth remains a separate unresolved
workflow; do not generalize the read result to it. Defer matched 1/2/3-member
scaling matrices and repeated runs to the backlog until the end-to-end user
prototype is working. If the bounded callsite probe does not explain the read,
preserve the unresolved status and instrument the largest unexplained interval.

## Conditional repair routes

Choose only after the probe:

- If H1 dominates, identify the exact repeated fields and keep only contractually
  necessary durable evidence. Any compact representation must still bind to the
  exact source bytes and remain independently verifiable after reopen; make a
  new record version if canonical retained meaning changes.
- If identical authenticated facts are recomputed within one unchanged source
  epoch, reuse that derived result within the epoch; retain fresh writer-side
  checks after owner IPC and at physical publication boundaries.
- If H3 dominates, build a local head-to-record lookup for this reduction and
  preserve duplicate/missing-record rejection.
- If H4 dominates, first remove duplicate captures within one held read phase;
  never reuse a snapshot across a writer/owner boundary without the existing
  commitment and source-cut checks.
- If H5 dominates, optimize the measured transport span (payload encoding,
  lock/queue, or owner work) specifically; do not change deadlines based on
  aggregate wall time.
- If H6 runs and dominates, reduce repeated suffix commitment reconstruction
  while preserving exact suffix membership and final commitment equality.
- If the matched matrix does not reproduce the progression, resolve H7 before
  changing production behavior.

## Open items

- [DONE FOR THE MEASURED READ] Grouped effects/dispatch validations by
  authenticated input identity. Thirteen dispatch validations shared one full
  owner/effects cut; three applicability fingerprints were decoded 17 times
  each. One effects acquisition reconstructed the same strict execution-history
  head and eight record heads already read by the outer terminal reader. The
  outer strict snapshot is the possible reuse input; `_current_cut`'s recovery
  snapshot is selected-only and is not interchangeable.
- [DONE FOR THE MEASURED READ] Differentially explain the 13-to-10 effects
  acquisition change. The saved baseline trace identifies the three removed
  calls as the nested strict replay's member validations; the same immutable
  snapshot was already reconstructed by the outer strict history reader. The
  physical effects reader still performs a fresh cut and checks current
  commitment, full membership, exact rows, owner journal, and DB identity.
- [DONE, MOUNTED NEGATIVE STILL UNVERIFIED HERE] `read_selected_accepted_direct_terminal`
  now forwards only the outer strict snapshot, inside the same authority gate,
  through `authenticated_effects_history`. A focused boundary test passes and
  the existing V3 supplied-snapshot test asserts rejection when physical Run
  membership is incomplete. Its mounted run in this environment stopped before
  that assertion because the spawned owner could not bind its AF_UNIX socket
  (`PermissionError`); no claim is made that the mounted negative passed in
  this session. The effects reader's fresh commitment, full membership, exact
  rows, owner journal, and DB identity checks remain in place by source review.
- [OPEN] Treat approximately 14 s CPU as a per-read opportunity bound, not a
  benchmark. Do not widen reuse across writers or owner IPC.
- [RESOLVED FOR CALLSITE CLASSIFICATION; REUSE STILL DEFERRED] The saved trace
  has 13 `verify_dispatch` spans: three beneath each of four instrumented
  `_read_call_history` spans, plus one without an instrumented parent. Their
  inclusive CPU totals 19.27 s in this fixture (about 1.4–1.7 s each); each
  decoded nine dispatch requests, 117 `_request` calls overall. The trace's
  owner/cut identity summaries agree, but it did not capture complete row bytes
  or metadata. In current source, `_collect_one_accepted_outcome` performs one
  common `verify_dispatch_history(cut, retained_journal)` call, then continues
  with member-specific acceptance-intent, OPEN/CLOSED outcome, historical
  sequence and locator joins. This explains why common full-history replay is
  repeated across member reads while the later joins remain call-specific. It
  does not prove that all separately acquired cuts and journals are byte-equal;
  preserve each fresh physical acquisition. Do not add a cross-read cache. A
  per-read verified-result reuse remains deferred until the horizontal user
  scenario passes and exact captured cut/journal equality can be enforced at
  the local boundary.
- [OPEN] Attribute the unprofiled accepted-call/terminal writer growth if it
  remains a blocker after the read-path result; do not infer it from the read
  profile.
- [OPEN] Verify whether the conditional suffix fallback runs in the slow
  writer phases; it ran zero times in the measured target read.
- [OPEN] Verify the targeted supplied-snapshot negative on a host where the
  mounted owner process can bind AF_UNIX sockets; no load or growth matrix is
  needed for this local read-path change.

## R14.6 two-seal acceptance writer `STALE`

Status: the timeout cause for this mounted selector is established; the work
inside the slow history check and the rest of the test duration are not yet
separated. This result is for
`test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`,
not the separate one/two/three accepted-member growth measurements above.

### Instrumented result (2026-10-14)

Command: `uv --cache-dir /private/tmp/uvcache run python
/private/tmp/r14_writer_probe.py`. The temporary runner wrapped runtime
acceptance, `CallPublicationAuthority.prepare_fresh`, `_check_live`,
`check_prepared`, and the publication coordinator in memory; it did not change
production predicates. The mounted pytest selector failed after 420.61 s with
`call acceptance rejected: writer STALE`.

The captured nested intervals were:

| Boundary | Wall time |
|---|---:|
| `preview_call_acceptance` | 26.93 s |
| `prepare_fresh` | 13.88 s |
| writer `check_live` | 13.34 s |
| writer `check_prepared` | 13.34 s |
| coordinator commit | 13.48 s |
| `accept_call` | 27.37 s |

`check_prepared` returned `STALE`. Before it discarded the detail,
`_check_live` raised `call acceptance original authority or owner preparation
expired`. At the final monotonic sample, every source observation horizon and
both owner-call deadlines had expired by about 8.99 s. The captured source
deadlines were the earliest: about 29 ms before the loop deadline and 39 ms
before the effects deadline. Mandate and lease still had about 19.18 s; the
original auth RPC budget had expired, but the effective admission override was
the mandate horizon and still had about 19.18 s. Thus this selector did not fail
because of trust admission, mandate/lease expiry, or a physical tenant-head
mismatch. The exact failing predicate was the explicit final authority/owner
deadline check in `_check_live`.

The monotonic check itself began about 4.35 s before the earliest source
deadline and finished about 8.99 s after it. Its full 13.34 s covers
`read_execution_call_history`, the fresh `capture_dispatch`, source/scope
comparisons and the final check. The trace did not split those operations, so it
does not yet identify which sub-operation consumes most of that interval.
`prepare_fresh` is also a 13.88 s interval, but its first owner deadline is
issued partway through that interval; its total alone does not show that the
source window expired there.

The same failed fixture contained a 209,312,600-byte loop journal,
4,762,078-byte call-preview journal, 3,223,014-byte H1 workspace-issuance
journal and 237,960-byte owner journal. These sizes are observed in the test
artifact; they are not a per-file timing profile. The 420.61 s test total minus
the two measured top-level preview/acceptance intervals leaves about 366.31 s
outside them. Test setup, sealed-history/V3 work and teardown were not
individually timed, so that remainder is not attributed to one stage.

### Differential result and next probe

- **Eliminated for this failure:** stale original AUTH admission, expired
  mandate/lease, physical tenant-head mismatch, and source/cut mismatch. The
  trace reaches `_check_live`'s explicit deadline rejection; it is not just a
  generic `STALE` classification.
- **Established:** the writer freshness check crosses its five-second source
  horizon while taking 13.34 s. Preserve the selected horizon; the measured
  problem is local work in the mandatory post-owner revalidation path.
- **Still open:** the split between history decoding and fresh capture, and the
  366.31 s outside preview/acceptance. No GIL, IPC, or exponential-growth claim
  follows from this run.

Do not rerun this long selector solely for finer timing. At the next necessary
mounted horizontal R14 run, attach nested low-volume timers around
`read_execution_call_history` and `capture_dispatch` inside `_check_live`, and
around the test's second-turn setup/V3 publication boundaries. Record counts and
bytes once per boundary. Optimize only the measured local work while retaining
the writer-side currentness check and all deadlines. Do not widen the horizon or
reuse a pre-owner cut as the writer check.

### Differential narrowing inside the writer read (2026-10-14)

The existing failure artifact was not rerun. `/private/tmp/r14_writer_probe.py`
invokes exactly
`test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`.
Its fixture was copied before runtime startup to `/private/tmp/r14-profile-9056`;
the copy and original loop-journal have the same SHA-256
`9992f13fc7be56923be53902b40d22e00510b64f409ea97101926eef4769917b`. A
temporary in-memory wrapper on that mounted selector measured the inner
writer-check operations directly:

| Boundary | Wall | Thread CPU |
|---|---:|---:|
| `read_execution_call_history` | 12.922 s | 12.911 s |
| fresh `capture_dispatch` | 0.176 s | 0.176 s |

The surrounding `_check_live` interval was 13.099 s in this run. A separate
parser-only probe used the exact saved loop-journal bytes (209,312,600 bytes,
40 entries): one ordinary JSON pass took about 0.104 s CPU, and strict decode
plus canonicalization took about 0.22–0.23 s CPU. These are bounded results for
that file and those successful payloads; they do not include history semantics,
Pydantic validation, hashing, SQLite reads, or repeated serialization.

Differential status:

| Candidate | Evidence | Status / implication |
|---|---|---|
| Fresh `capture_dispatch` dominates the 13.1 s writer check | It measured 0.176 s. | Eliminated as the main cost in this run. Keep its fresh capture. |
| One strict JSON decode/canonicalization of the loop journal dominates | The original parser-only pass took under 0.24 s; an independent exact decision-byte reproduction took 0.520 s; the full history reader took 12.92 s. | Eliminated as a sufficient explanation. Repeated decode/canonicalization inside semantic helpers remains possible. |
| Repeated `execution_inventory` reconstruction dominates | `_read_call_history` has eight call sites (lines 1241, 1279, 1570, 1620, 1858, 1913, 1946, 2011); each validates a snapshot by serializing then reparsing it in `r14_execution_inventory.py`. `read_execution_call_history` constructs the final inventory once more after `_read_call_history` returns. | Strongest code-grounded candidate, not measured yet. Prefix snapshots grow during replay, so repeated full reconstruction may be superlinear in selected history length; this does not establish exponential growth. Measure time and bytes per call before reusing any result. |
| Nested envelope build/validation dominates the inventory passes | `execution_inventory` calls `build_envelope` for each retained execution fanout. That builder serializes evidence and freshly parses the resulting envelope. Its byte helper has a one-pair reuse cache capped at 4 MiB; a cache hit still serializes the evidence and parses the public envelope, while larger pairs are not retained. | Strong code-grounded sub-hypothesis of the inventory cost, not independently timed. Count helper calls, bytes, and cache hits/misses; report exclusive time to avoid adding it to inclusive inventory time. |
| Accounting and H1 issuance validation dominates | The exact history contains one native-accounting and one V3 accounting selection. A different reader profile attributed 3.47 s cumulative to native-accounting decode and 2.91 s to original-Prepare projection, but that profile is not this writer read. | Live secondary candidate; do not transfer the older timing as a result for this run. Instrument these exact decoder/projection boundaries on a valid same-cut read. |
| GIL contention explains the CPU | Wall and thread CPU are nearly equal for the history call. No competing-thread or lock-wait measurement exists. | Unsupported; do not change thread or process policy. |

The one-line parser reuse in `r14_loop_history.py` removes a duplicate ordinary
`json.loads` after `_strict_json` has already decoded the row. It preserves the
same canonical and duplicate-key check, but the parser-only result bounds its
possible saving to a small part of the 12.92 s. It is not evidence that the
writer deadline now fits.

### Exact failed-artifact journal decode decomposition (2026-10-14)

A separate read-only script decomposed the exact 40-entry loop-journal artifact
from the failed writer selector above, without opening a runtime or changing
the fixture. The journal file contains 209,312,600 outer bytes; its hex-wrapped
selected decisions total 104,651,091 bytes.

| Work over the exact artifact | Wall | Thread CPU |
|---|---:|---:|
| Parse outer journal rows and hex-decode selected decisions | 0.518 s | 0.518 s |
| Duplicate-key decode and canonical re-encode each decision, asserting exact bytes | 0.520 s | 0.520 s |

These one-pass costs are a small fraction of the 12.922 s writer history read.
They rule out one outer-journal pass or one strict decision parse as a sufficient
explanation; they do **not** bound repeated parsing of the same decision bytes
inside semantic history decoders.
This independent reproduction differs from the earlier 0.22–0.23 s strict-pass
estimate recorded above; keeping the exact stages visible makes that measurement
difference resolvable instead of silently treating the numbers as interchangeable.

The selected `native-one-call-cancelled-accounting` evidence is 26,100,942
characters and contains 12 `selected_decision_bytes` references to 9 distinct
values. Three references repeat a value already present in the same evidence;
the repeated encoded values total 2,835,507 characters. Source inspection
confirms `decode_selected_native_accounting` enumerates the loop journal again,
then `json.loads` and reconstructs each referenced prior publication, including
the Prepare projection. This makes repeated semantic reconstruction an active
candidate; reference size alone does not attribute its runtime cost.

The journal's exact-body decode cache is also conditional: `.entries()` still
reads and compares the entire body and rechecks source identity, key, and head
on a hit; a newly appended body misses the cache and reauthenticates the full
prefix. On the saved 209,312,600-byte body, a hot `read_bytes()` plus exact byte
comparison took 0.030–0.046 s across three passes. That rules out the body
read/compare alone as a major part of the 12.9 s history read; the microprobe
did not include `_existing_sources`, key/head validation, or a cache miss after
an append. Another repeated path precedes this reader: with `selected_only=False`,
`_read_call_history` calls `_require_no_pending()`, which scans the owner
snapshot, parses loop `_pending()` entries (including H1 V2 durable validation),
and checks gate pending state. It then enumerates the loop journal and owner
snapshot again for history reconstruction. The individual `owner-journal` is
only 237,960 bytes in this fixture, but semantic validation is not bounded by
file size alone.

Therefore the next mounted measurement must use three non-overlapping blocks:
(1) `_require_no_pending`, split into owner snapshot, loop pending/H1 validation,
and gate pending; (2) history reconstruction, with `_strict_json`/`json.loads`
invocations and bytes, `_publication` invocations and decoded record bytes,
and native/V3/H1 decoder time; (3) each journal `.entries()` call, with body
bytes, decode/auth cache hit or miss, and source/head checks. Also record source
deadline slack at writer-check entry and exit. Keep an outer writer-history timer
and compare it with the sum of non-overlapping phase times; report any exclusive
remainder instead of allocating it by guess. Do not add nested inclusive timings
or infer an exponential growth law from this single artifact.

Next discriminator: on the next necessary valid mounted history read, collect
one low-volume `cProfile` call tree around `read_execution_call_history` and
separately time `execution_inventory`, native-accounting decode, V3 decode, and
original-Prepare/H1 issuance verification. For envelope builders and their
byte helpers, also record invocation count, input bytes, and cache hit/miss.
Record snapshot sizes once per inventory call; capture wall and thread CPU
outside the profiler. Compare inclusive and exclusive times rather than adding
nested totals. Preserve the same selected history, anchor, and
exact-historical-source mode for any compared reads. Do not build a new
multi-seal scenario just to obtain this profile. Until a valid same-cut profile
exists, inventory reconstruction is the leading hypothesis, not a proven cause.

### Short mixed-V3 mounted profile (2026-10-14)

This is a separate, shorter witness, not the failing 40-entry writer fixture. The
selector is
`tests/composition/test_r14_native_selected_accounting_source.py::test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once`.
It uses two native members (one `SUCCEEDED`, one `CANCELLED_BEFORE_ACCEPT`),
publishes V3 accounting, reads it after reopen, checks exact replay, rejects a
wrong physical cut, and rejects an orphan publication without moving the head.

The instrumentation commands were:

```sh
TMPDIR=/private/tmp/r14-v3-mixed-profile PYTHONPATH=src:/private/tmp PYTHONUNBUFFERED=1 uv --cache-dir /private/tmp/uvcache run pytest -s -q -p r14_inventory_probe tests/composition/test_r14_native_selected_accounting_source.py::test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once
TMPDIR=/private/tmp/r14-v3-mixed-profile PYTHONPATH=src:/private/tmp PYTHONUNBUFFERED=1 uv --cache-dir /private/tmp/uvcache run pytest -s -q -p r14_test_profile tests/composition/test_r14_native_selected_accounting_source.py::test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once
```

Both mounted runs passed: the history-inventory probe completed in 35.83 s; the
whole-test `cProfile` probe completed in 39.23 s (profiled test-call wall was
38.754 s). The latter profile reports 93 `_read_call_history` calls with 18.851 s
cumulative time; 47,079 JSON encodes with 10.134 s cumulative, 18,538 decodes with
5.582 s cumulative, 260 selector calls with 7.727 s cumulative, and 12 V3 decoder
calls with 6.260 s cumulative. These are inclusive totals from different, nested
branches and must not be summed. The fixture intentionally exercises reopen,
replay, wrong-cut and orphan negatives, so none of these totals represents the
ordinary user journey.

In one short reader call, four `execution_inventory` calls over snapshots of 7 or
8 records took about 27 ms combined. That weakens “inventory reconstruction is
already dominant at this short history size”; it does not eliminate the candidate
for the 40-entry/209 MB writer history. `selectors.select` time may include async
waiting, but this probe did not separate thread CPU from wait time or identify the
waited-on owner RPC, so it does not establish IPC as the cause. The whole-test
profile likewise does not establish GIL contention or a scaling curve.

Differential update: retain repeated semantic history validation, native/V3
accounting and H1 issuance checks as candidates for the slow writer read; mark
inventory-only dominance unproven and size-dependent. Keep the next discriminator
on the next necessary same-cut mounted read. Do not rerun this test or the long
two-seal selector solely to collect more profile detail.

### Saved-fixture validity check at HEAD `1a7670e` (2026-10-14)

A later attempt to reuse the saved three-member terminal-read fixture reached
`LoopIntegrityError` for its selected `native-sealed-accounting` record before
returning the target result. No timings from that failed read are used. The
fixture predates the current source state and is not a valid current-source
profile input; do not retry it or patch its evidence to force acceptance. The
one-seal mixed-V3 test above creates fresh state and remains the bounded current
baseline. The failed attempt changed only the fixture's gate SQLite bytes; those
bytes were restored in place from a complete backup, and all 46 paths, file
contents, modes, device/inode identities and sizes then matched the pre-run
manifest. The mounted two-seal fixture copy remains path/inode-incompatible.

### V3 reader reuses the current execution snapshot boundary (2026-10-14)

Source inspection found one exact repeated derivation on the deferred-V3 path:
`_read_call_history` built `execution_inventory` to place beside the current
snapshot for the V3 decoder, but the decoder consumed only the snapshot. The
outer `read_execution_call_history` then built the inventory again from the same
tenant head, ordered records and selected lifecycle tuples. The decoder now
receives the authenticated `ExecutionHistorySnapshot` directly; the outer reader
still builds and returns the final inventory once, and the V3 source and physical
membership checks remain in place.

`test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once` passed on the
mounted runtime in 32.98 s, including selected readback, reopen, exact replay and
the full-reader orphan negative. This verifies the V3 reader path, not a speedup
large enough to meet the five-second writer deadline. The simplified two-seal
experiment did not produce a verdict and was stopped after 213.06 s; its test
changes were reverted. Do not treat that run as pass/fail or repeat the long
scenario until a genuinely bounded writer witness is available.

### Preview authentication-order regression witness (2026-10-14)

The preview path now reads the selected execution-call history before starting
the fixed-budget AUTHENTICATE call, records the loop head under the same
authority gate, then checks that head after authentication. It reuses that
history for both `capture_dispatch` calls and performs a fresh authentication
and capture after the owner precursor exchange. This addresses expiry caused
by an earlier history read; it does not shorten or explain the separate
writer-side 12.92 s history read.

Two focused mounted tests passed on the current tree:

- `test_preview_authenticates_after_history_read_and_reuses_current_history` —
  1 passed in 9.38 s. The test delays after a successful history read by 5.1 s,
  then confirms the order `history → authenticate → authenticate` and confirms
  both capture calls receive the identical history object.
- `test_preview_selects_original_owner_result_without_changing_its_source_cut` —
  1 passed in 6.95 s. It verifies selected preview readback after reopen, stable
  source state, and zero provider transfers.

These are mounted behavior witnesses for this preview route. The first test
does not mutate authority between the two capture calls, so its second-call
assertion does not independently prove rejection of a changed final snapshot.

## Successor `STALE` versus expected `HOLD`

The mounted open-obligation scenario reached its final follow-up ingress and
returned `STALE`, where the test expected the existing open obligation to
produce `HOLD`. The exact rejection reason was not printed or retained, so the
test result identifies the public status but not the rejecting guard. The
scenario's roughly 680-second total duration does not establish the age of the
admission observation used by that final call.

Current hypotheses, ordered by what the evidence can discriminate:

1. **An earlier `LoopRejected` precedes semantic successor preparation.**
   `drive_input` maps every `LoopRejected` to `STALE` and preserves its text in
   the returned result. `r14_execution_successor._source_for` has a specific
   early rejection for an incomplete selected recovery cut; despite its text
   mentioning `HOLD`, it is a `LoopRejected` and therefore becomes `STALE`.
2. **Authentication or another freshness guard rejects the ingress.**
   `require_invocation` can distinguish admission expiry, trust-session change,
   trust-generation change, and other observation changes, but no such reason
   was captured for this result. The full scenario duration is not evidence
   that this particular observation expired.
3. **Owner preparation returns a classified recovery result instead of the
   expected preparation HOLD.** `_exchange` maps `CallPreparationRejected`
   with code `HOLD` to `LoopHold`; classified recovery replies become
   `LoopRejected`. The returned reason and reply kind would distinguish this
   from an earlier cut/authentication rejection.
4. **The ordinary `LoopHold` to public `HOLD` mapping is broken.** This is
   currently the weakest hypothesis: `drive_input` has a direct
   `except LoopHold` branch before its generic `LoopRejected` branch. It is
   not fully eliminated until the reason shows the semantic hold was reached.

Do not rerun the full mounted witness solely to identify this old result, and
do not change deadlines, retries, or exception mappings from aggregate
runtime. At the next required mounted run, make the failing assertion print
the scalar `held.reason`; pytest abbreviates the full DTO dictionary. If its reason identifies authentication
staleness, capture the first failed predicate in `_trust_observation_guard`
with that observation's deadline and check time. If it identifies an
incomplete cut, trace the specific `_source_for` cut result. If it identifies
owner classification, record the owner reply schema and decoded result type.
Only then choose and verify a local repair.

### Current-source V3 collect probe (2026-10-14)

To test whether the repeated `R14NativeSelectedAccountingSource.collect` calls
could explain the short mixed-V3 runtime, the existing mounted witness was run
once with a temporary in-memory timer (no cProfile, no payload reserialization,
and no production predicates changed):

```sh
PYTHONPATH=src:/private/tmp PYTHONUNBUFFERED=1 uv --cache-dir /private/tmp/uvcache run pytest -s -q -p r14_v3_collect_probe tests/composition/test_r14_native_selected_accounting_source.py::test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once
```

The test passed (`1 passed in 32.74s`; instrumented test-call interval 32.31s).
The timer observed 12 selected V3 decoder calls. Each decoder took about
0.47–0.51s thread CPU. `prepare_sealed_accounting_v3` took about 0.34–0.37s
inclusive; its two nested `source_collect` calls took about 13–14ms each. The
decoder's direct source collection also took about 13–14ms. This bounds the
observed repeated source collection to a small part of this short witness; it
does not predict its share in the 40-entry writer history.

This probe weakens the route “remove repeated source collection to fix the
five-second writer deadline.” It does not eliminate source collection at larger
history sizes, nor identify the remaining 0.3s inside V3 preparation. Do not
reuse facts or remove any currentness check from this result. The earlier
writer-path read still needs a same-cut breakdown on a naturally required run;
the short mixed-V3 test is not a substitute for that witness.

### Two-accepted functional slice: repeated terminal replay decoding (2026-10-14)

The mounted selector
`test_v3_interleaves_two_sequentially_accepted_members_across_replay_and_reopen`
passed in 181.91 s, and passed again in the checkpoint hook in 184.59 s. It
covers two accepted members in one seal, an early incomplete-accounting HOLD,
terminal and V3 exact replay, reopen/readback, and unchanged tenant head. It
does not cover two seals, three accepted members, or full physical orphan
detection.

The first instrumented execution used `faulthandler_timeout=90`; at 90 s the
main thread was in `r14_runtime._loop_decision_materialized`, reached from
`_finish_decision` during terminal publication. The R13 method fully validates
the pending marker sequence, parses every loop decision, checks the unique exact
`DECIDED` entry and `expected`, and returns whether it is materialized. On a
materialized decision, the R14 override then parsed every loop decision again
only to recover that same selected entry before its independent physical
membership/anchor check. The selected entry and materialized flag now come from
one base validation pass. Each call still reads and validates the current
journal, runs `_pending()`, checks uniqueness and `expected`, and performs the
R14 physical check; no value is retained across calls.

This removes one provably duplicate full JSON parse from the materialized R14
branch. The stack sample shows that branch was active during the slow test, but
does not measure its exclusive cost or prove it is the dominant cause. This
change does not address the separate 12.922 s `read_execution_call_history`
writer interval or prove that the five-second writer horizon is met. Keep the
two-seal and three-member requirements open until their bounded functional
witnesses pass.

Source provenance: verified HEAD `1a7670ea48b024efe56cedd3fd0c6bb5285d0a7d`;
Python 3.14.5 and pytest 9.1.1 loaded the four modules from this checkout. SHA-256
was `83368366…` for `r14_loop_history.py`, `5483aeca…` for
`r14_native_sealed_accounting_v3_history.py`, `99a1c195…` for
`r14_native_selected_accounting_source.py`, and `dc771b0e…` for the target test.
The temporary timer hash was `cd5ca5cf…`; it lived under `/private/tmp` and was
not added to the repository.

### Differential correction: exact two-seal writer read (2026-10-14)

The next mounted execution of
`test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`
failed at second-turn `accept_call` with `writer STALE` after 416.00 s. A
temporary in-memory timer measured 105 `_read_call_history` calls at 356.418 s
wall / 356.059 s thread CPU, 216 `execution_inventory` calls at 1.680 s total,
and one writer `read_execution_call_history` call at 13.062 s. The test reached
the same explicit source-horizon rejection described above. The mounted result
is a failure, not a completed R14.6 witness.

This 416.00 s result is a separate invocation from the 420.61 s run above.
Only the 420.61 s run captured entry slack (about 4.35 s before the nearest
source deadline) and final expiry (about 8.99 s after it). The 416.00 s run
recorded the 13.062 s reader but not deadline slack at check entry; do not
transfer the first run's slack measurement to this second invocation.

This directly **eliminates inventory reconstruction as the dominant cost in
this run**; the earlier candidate ranking based on source call sites was not
enough. It also lowers the priority of GIL and generic journal-parser
hypotheses: thread CPU nearly equals wall CPU, while an earlier parse-only pass
over the exact 209 MB loop journal took under 0.24 s. Neither result proves the
absence of lock contention elsewhere, and neither accounts for semantic
decoding or repeated canonical serialization.

The strongest remaining class is repeated semantic validation during
`_read_call_history`. Plausible sub-hypotheses are (a) dispatch/effects
history reconstruction and canonical serialization, (b) native/V3 accounting
decoding plus original-Prepare projection, and (c) repeated H1 workspace
issuance journal scans. Existing code inspection confirms these operations
occur, but the exact 13.062 s interval has not split them. A separate short
profile confirms that accepted-terminal reads repeat full dispatch/effects
verification, but that is a different path and cannot assign this writer
interval.

The 105-call aggregate also mixes different moments and history sizes; it is
not a scaling curve. The evidence supports repeated full-history work and
deadline crossing, not exponential growth. Do not infer that the 13.062 s
writer check alone consumed all available source slack: its entry slack was not
captured in this execution. Do not widen the five-second source horizon or
reuse pre-owner authority. The next bounded discriminator is a low-overhead
mounted profile of the existing short mixed-V3 witness, timing these exact
helpers separately: native-accounting decoder/original-Prepare projection,
V3 decoder, `verify_native_prepare_scope`, and H1 issuance journal `_scan`.
Record counts and bytes per helper. Use the result only to rank local work; a
short one-seal profile cannot establish the two-seal writer's complete cost.

### Short mounted helper discriminator (2026-10-14)

The existing short selector
`test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once` was run
twice with in-memory timers around history, accounting, dispatch/effects and H1
helpers; both runs passed (32.66 s and 33.24 s). In the second run, 93
`_read_call_history` calls took 16.646 s total, the 12 V3 decodes took 5.981 s,
and `_read_materialized_effects_with_history` plus `verify_dispatch_history`
took 0.332 s and 0.108 s across eight calls each. Twelve original-Prepare
projections took 0.048 s. All 112 H1 workspace journal scans took 0.079 s.
The V3 decode interval is nested in history validation; do not add it to
`_read_call_history`.

For this small one-seal shape, these measurements eliminate H1 issuance scans,
original-Prepare projection, and dispatch/effects reconstruction as dominant
costs. V3 decoding is a measurable part, but leaves most history-reader time
unattributed. This is not the same history shape or the failing writer check;
none of these short-profile exclusions can be generalized to the 40-entry,
209 MB two-seal cut. Combined with the 13.062 s exact writer read and the 1.680
s total inventory time, the evidence now points to authenticated loop-history
semantic validation outside inventory/dispatch/V3/Prepare-specific helpers.
The remaining targeted comparison should count and time per-selected-command
retained decoding/canonicalization in `_read_call_history`, recording payload
size once per operation family. Repeating mounted scenarios without that
specific boundary data would not further discriminate the candidates.

A follow-up `cProfile` wrapped only `_read_call_history` in the same short
mounted witness (`1 passed in 34.56 s`). Across 93 history reads, the profile
captured 18.165 s and attributed 6.643 s exclusive to JSON `iterencode`, 2.793
s to JSON `raw_decode`, 1.373 s to Pydantic `validate_json`, and 0.823 s to
SHA-256. It counted 10,158 `canonical_bytes` calls, 450
`transition_command` calls, 450 `verify_execution_transition` calls, 68
`decode_call_issuance` calls and 1,116 journal `entries()` calls. The V3
decoder's 6.002 s cumulative is nested within this profile. These totals span
93 reads and overlap by call-tree nesting; they are not the cost of one writer
admission.

This makes repeated canonical JSON serialization and retained transition
verification the strongest short-history mechanism candidate. The profile
does not show which `canonical_bytes` payloads repeat byte-for-byte, so it does
not justify caching across history reads. It also does not assign the exact
13.062 s two-seal writer interval: the larger history has two seals and a much
larger journal. A future fix should first identify duplicate serialization
within one immutable authenticated snapshot, then demonstrate byte-identical
results and fewer encodes at that local boundary. Cross-call reuse and
deadline changes remain unsupported.

The same short mounted selector was then timed at the actual writer boundary:
one `_check_live` took 0.085 s and its `read_execution_call_history` took 0.070
s; the test passed in 32.52 s. Compared with the 13.062 s writer read in the
failed two-seal history, the reader cost differs by about 186x across these
two workloads. This rejects “writer admission is intrinsically slow” and
supports history shape/size as a necessary discriminator. The comparison is
not controlled for exact row count or bytes, so it does not establish a growth
law. The short profile also shows that the same writer freshness path fits its
five-second horizon on its smaller mounted cut.

### Reuse the authenticated outer loop-journal cut for V3 Prepare lookup

Source inspection found one exact duplicate scan within a single
`_read_call_history`: the outer reader already enumerates and authenticates all
loop-journal entries under `AuthorityGate`, while each deferred V3 decoder
enumerated the same journal again only to locate the original Prepare decision
bytes. The decoder now receives a map of **all** journal decision IDs to their
original raw bytes from that outer enumeration. It still compares the exact
Prepare bytes, verifies the retained SHA-256, rebuilds the original Prepare
projection, validates the V3 source and request, and checks the selected
physical publication. No value is reused across calls.

Cold review confirmed the lookup key is the same string journal decision ID,
and required retaining all entries rather than only selected `DECIDED`
envelopes. The enclosing authority gate excludes admitted concurrent journal
writes for the duration of this read; this change reuses that authenticated
read cut. The existing mounted mixed-V3 test passed after the change in 32.69
s, including reopen, exact replay, wrong-cut and orphan checks. Ruff passed.
This verifies the positive mounted path and its existing negatives; it does not
measure a material runtime gain or prove the two-seal five-second writer now
passes. R14.6 remains open.

### Latest writer-probe attempt (2026-10-14)

A new in-memory wrapper probe was attempted against the same mounted two-seal
selector. In the sandbox it first failed before owner startup because AF_UNIX
bind was denied. With local test sockets permitted, the instrumented run exited
in 7.79 s at `capture_execution` with `WorkspaceRejected: unregistered
workspace class dispatch`; it never entered `_check_live`. Its aggregate wrapper
counters cover only this early failing path and are not evidence about the
multi-minute history read or the deadline rejection. Do not use those counters
to rank R14.6 hot paths.

The early failure is explained by the probe itself: it monkeypatched
`IndependentTenantDecisionJournal.entries` and `_entries`, while
`r12._check_dispatch()` compares that class's method objects against its
registered `_DISPATCH` snapshot. The workspace guard correctly rejected the
modified class. This is a confirmed instrumentation defect, not evidence of a
product regression or nondeterminism. Any replacement probe must observe the
journal without changing a class in `_DISPATCH`.

The same selector was then run directly, without instrumentation, to test
whether the early rejection was probe-induced. That control remained silent and
was interrupted after 819.67 s (`no tests ran`), so it also yielded no writer
verdict. It neither falsifies nor confirms the earlier two-seal deadline
failure. The wrapper discrepancy is explained, so a short setup comparison is
no longer needed. No more full-selector reruns are justified until a
non-invasive measurement can isolate the target read.

### Exact saved-cut history profile (2026-10-14)

To avoid another full selector, one direct `read_execution_call_history`
invocation was profiled with `cProfile` against the preserved
`pytest-9067/test_v3_accounts_two_sequentia0` fixture. Before opening, the
fixture had 44 files, a 40-row, 209,312,600-byte loop journal, and the expected
R14 native recovery-profile first decision. The matching
`NativeRecoveryCutSource` and custody (`scenarios=["CONFIRM"]`, `cap=1`) were
provided. The runtime's selected source cut was checked unchanged before the
reader. The reader passed and returned 14 Run records and two ordered calls.
After the probe process exited, only broker/gate/reads SQLite state differed;
all 44 files were restored from the verified backup, with file set, content,
mode, and mtime checked. The database inode remained 145248473.

The profiled reader took 27.403 s wall / 27.392 s thread CPU; cProfile recorded
27.398 s and 4,417,456 calls. This was a direct call outside the five-second
writer predicate, so it measures the same history-reading function on the
saved source cut, not deadline admission. cProfile overhead was not calibrated;
the absolute profile time should not be substituted for the earlier 13.062 s
uninstrumented writer interval.

| Operation | Calls | Exclusive time | Cumulative time |
|---|---:|---:|---:|
| JSON `raw_decode` | 3,101 | 9.954 s | 9.954 s |
| JSON `iterencode` | 7,748 | 7.252 s | 7.252 s |
| File reads | 502 | 1.353 s | 1.353 s |
| SHA-256 | 6,551 | 1.339 s | 1.339 s |
| Journal `_entries` | 235 | 0.600 s | 2.026 s |

At the R14/R17 helper level, `_read_call_history` took 27.301 s. Three
`validate_selected` calls took 21.055 s cumulative, including 18.624 s in
`authenticate_native_recovery_selected`; one `validate_selected_next_turn`
took 15.431 s; seven R17 `read_historical` calls took 11.666 s; four
`decode_selected_native_accounting` calls took 9.220 s; and 29 `_pending`
calls took 8.150 s cumulative. These helper totals overlap. They describe one
history shape, not independent work to add together.

This changes the differential ranking. It directly rules out the simple claim
that the 209 MB outer file read or one JSON pass alone causes the delay: the
profile contains thousands of nested semantic decodes and canonical encodes.
The strongest measured contributors are selected recovery validation,
next-turn validation, and repeated historical source reads; canonical JSON
decode/encode is the dominant low-level CPU cost. This supports repeated
semantic validation over the growing authenticated cut. It still does not
prove a growth exponent or identify which individual validation can be reused
without changing a trust boundary.

At the time of this profile, the next design question was whether a local
duplicate existed among recovery validation, next-turn validation, and R17
historical reads. The later differential comparison below answers this for
`validate_selected_next_turn`; its ranking supersedes the earlier candidate
ranking above. Do not cache across calls, extend deadlines, or weaken recovery
source authentication.

### Differential hypothesis ranking after the saved-cut profile

This section supersedes the earlier provisional candidate ranking in this
document.

The target observation here is one successful `read_execution_call_history`
on `pytest-9067/test_v3_accounts_two_sequentia0`: 14 Run records, two ordered
calls, 27.403 s under uncalibrated cProfile. Other test durations and the
13.062 s writer interval are separate observations; they are not added to this
profile or used as controlled comparisons.

| Hypothesis or proposed remedy | Predicted signature | Evidence / falsifier | Status |
|---|---|---|---|
| One 209 MB outer journal read or one strict parse causes the cost | A single raw read or decode pass should approach the reader duration. | Exact standalone stages were 0.518 s outer parse/hex decode and 0.520 s strict decision decode/canonical re-encode; cProfile instead records 3,101 `raw_decode` and 7,748 `iterencode` calls. | Rejected as a sufficient cause; repeated semantic parsing remains supported. |
| `execution_inventory` reconstruction is the dominant cost | Inventory calls should dominate exclusive or cumulative time. | In the separate 416 s run, 216 inventory calls took 1.680 s; the exact saved-cut profile assigns large cumulative time to recovery, next-turn, and historical validation. | Rejected for the measured 416 s run; not generalized to every history shape. |
| Fresh dispatch capture is the writer bottleneck | `capture_dispatch` should consume most of the writer interval. | It measured 0.176 s in the failed writer run; the smaller mounted writer check passed in 0.085 s total. | Rejected as the cause in that writer run; larger-cut history remains a workload factor. |
| GIL contention explains the long run | Wall time should substantially exceed this thread's CPU time, or concurrent Python work should be observed. | Saved-cut read was 27.403 s wall / 27.392 s thread CPU; no concurrent workload attribution exists. | Unsupported; CPU-bound JSON work is observed, GIL contention is not. |
| Raise the five-second source horizon | The same authenticated work would finish without changing validation. | The saved-cut reader alone takes 27.4 s under cProfile, while the short cut's writer check fits. No acceptable replacement horizon is established. | Remedy rejected for now: it changes authority duration and does not reduce latency. |
| Reuse the result of the named accounting validation inside `validate_selected_next_turn` | One named readiness candidate is fully validated in the eligible-candidate loop and immediately validated again with the same runtime, candidate dict, command, and `through_sequence`. | Source at `r14_next_turn_records.py` establishes the equality condition and call order; the profile reports 15.431 s cumulative in the enclosing validator, but does not isolate the duplicate call's exclusive cost. | Strongest local candidate; duplication is established, savings and race behavior remain unmeasured. |
| Cache across history reads, combine R17 reads, or remove pending/source scans | Repeated inputs and freshness boundaries must be identical across calls or independently retained. | Profile counts calls but does not prove identical state/bytes across invocations; these reads authenticate independent journal prefixes and physical membership. | Rejected as premature; no candidate-specific evidence supports these broader remedies. |

The exact candidate is in `validate_selected_next_turn`: the named readiness
decision is required to satisfy
`prior.expected_head + 1 == request.cut.tenant_commit_sequence`. The later
loop validates every readiness whose resulting sequence is at or before that
cut, so the named candidate necessarily passes through
`validate_accounting_selected`. After that loop, the function calls
`validate_accounting_selected` again on the same local `prior` and
`prior_batch`, with the same `runtime` and `through_sequence`, then uses the
returned `original` to check the accounting/readiness pair, source bytes,
selected decision, result stamp, frontier fingerprint, and complete inventory
fingerprint. The duplicate's second invocation has no intervening write in
this function. The outer `validate_selected_next_turn` call in
`_read_call_history` is a distinct boundary with `through_sequence=None`; it
must remain.

This source comparison establishes repeated work and equal arguments, not
equivalent behavior under arbitrary out-of-band database or journal mutation,
nor the time saved by reusing the first result. The read is under the
`AuthorityGate`, which excludes cooperative writers; physical validation and
every eligible (including unnamed) readiness check must remain. A controlled
probe should instrument only the named candidate: confirm it is validated
twice before a local change and once after, compare the complete returned
history, and verify that a malformed eligible *unnamed* readiness still
rejects the read. Any intervening mutation, changed validation inputs,
changed failure outcome, or continued double validation falsifies this reuse
candidate. Record writer-entry deadline slack separately on the next required
mounted run; this profile was outside the writer predicate.

Do not repeat the full mounted selector for this question. The next design
step is a bounded call-level comparison of this exact duplicate and its
preserved negative case. Until it is measured, do not claim the candidate
explains the five-second rejection or quantifies the performance gain.

### Named readiness duplicate probe and local reuse (2026-10-14)

A bounded instrumented read of the same preserved 40-row history passed and
returned 14 Run records and two ordered calls. It observed five validations of
the same named-readiness pattern inside that top-level read. The fixture
contains exactly one selected next-turn publication and one accounting-
readiness publication. Each validation pair checked that same named readiness
twice, for ten named calls total. Every pair had the same journal operation
ID, expected head 16, `through_sequence` 17, identical entry and
physical-command object identities, and equal returned evidence. The exact
caller breakdown was not instrumented; these additional invocations occur
under retained historical validation. All calls ran on the same
`MainThread`; outer wall, thread CPU, and process CPU were 26.845 s, 26.773 s,
and 26.773 s respectively.

Each accounting-validator interval was about 6.8–7.1 s, but the ten intervals
must not be summed: calls are nested through historical validation on one
thread. These inclusive intervals do not predict the saving from removing a
call. This run shows CPU work in one thread and gives no signal that concurrent
GIL contention is the delay. The reader result and selected source cut matched
the earlier saved-cut read. The temporary fixture's three SQLite side files
were restored in place from the verified backup, and all 44 file hashes then
matched it.

The production change now captures the named candidate's successful
`validate_accounting_selected` result while retaining validation of every
eligible candidate in order, then uses that result for the existing pair and
C→C' checks. Matching is by the unique selected journal decision ID. The outer
`validate_selected_next_turn` call and its trust-prefix, physical, source-byte,
frontier, and inventory checks are unchanged. Two focused tests pass: one
asserts exact named-ID selection and that a later eligible candidate still
runs; the other confirms failure of that unnamed candidate still rejects the
read. Ruff passes.

The mounted restart/replay test was interrupted after 4.5 min without a
verdict; it is neither a pass nor a product failure. Its pytest cleanup
removed the older saved fixture before a post-change same-cut timing could be
collected. Do not claim an end-to-end speedup yet. The next necessary mounted
witness must establish restart/replay and foreign-evidence HOLD after this
change; keep that selector isolated and stop treating its long runtime as a
routine intermediate gate.

### Differential update: owner-prefix replay candidate (2026-10-14)

The preserved post-change `pytest-9070/test_selected_next_turn_restar0`
fixture was used for one bounded `read_execution_call_history` probe. It had
28 loop-journal rows (153,160,248 bytes), six owner-journal entries, and a
238,664-byte owner journal. The reader passed with nine Run records and one
ordered call. Wall time was 15.003 s; thread CPU was 14.956 s. This was a
direct authenticated history read, not a writer-admission timing.

The probe wrapped only the module-level
`snapshot_from_captured_entries` function. It observed 37 calls across runtime
startup plus the target read, visiting 162 captured entries in total and
passing 2,952,129 payload bytes through those prefix scans. The implementation
of `_scan_entries` traverses each supplied tuple once. The summed wrapper time
was 23.8 ms wall/thread CPU, with no nested snapshot calls. This is below 0.2%
of the reader's measured thread CPU. Therefore repeated owner-prefix snapshot
construction is directly present but is rejected as a material cause of this
fixture's 15 s reader interval. This does not establish its cost on larger
owner journals or the earlier two-seal writer cut.

The post-change cut again invoked named accounting validation four times, once
per observed invocation, with the same operation ID, expected head 15, and
`through_sequence=16`. The individual inclusive intervals were about 5.3–5.6
s, some nested in historical validation; they must not be summed or treated as
the saving from the local reuse. The run is not a controlled before/after
comparison: its 28-row/one-call cut differs from the earlier 40-row/two-call
cut. It confirms the changed call shape and preserved reader result, but does
not quantify speedup.

Updated differential status for the slow path:

| Candidate | Current evidence | Status / next discriminator |
|---|---|---|
| One outer journal parse or one strict JSON pass | Exact one-pass stages remain about 0.52 s; the profiled reader performs thousands of JSON decodes/encodes. | Rejected as sufficient cause. |
| Owner-journal growing-prefix snapshots | 37 observed scans, 2.95 MB aggregate payload input, 23.8 ms total CPU on this fixture. | Rejected as material for this cut; reopen only if owner-journal size is materially larger. |
| GIL contention | Latest read was 15.003 s wall / 14.956 s thread CPU. | Unsupported; observed cost is CPU work on one thread. |
| Repeated selected recovery / accounting / R17 semantic validation | Prior exact saved-cut profile assigns large overlapping cumulative time to these paths; post-change named readiness calls remain individually expensive, but their nested time is not attributable. | Live. Next measure must key calls by selected decision/source fingerprint and cut, and report exclusive CPU plus semantic input bytes. |
| Exponential growth | Current samples vary in history shape, rows, and bytes; there is no controlled size series. | Not established. Say “history-sensitive CPU growth” only as a hypothesis until equal-shape scaling points exist. |
| Five-second writer expiry caused by history read | A successful read outside admission does not identify writer entry slack or which deadline is minimum. | Unresolved. Capture deadline provenance and entry/exit slack on the next necessary writer call; do not raise the horizon. |

The selected next discriminator is a low-volume call tree for the already
ranked semantic validators, keyed by the exact decision identity, source
fingerprint, cut/head and `through_sequence`. If the same key recurs with
material exclusive CPU, inspect that local boundary for safe result reuse. If
keys differ or repeated-key exclusive time is small, retire reuse as the main
latency route and measure which distinct evidence bytes account for JSON CPU.
This is a diagnostic action only; no further optimization follows from the
current timing alone.

### Nested evidence size and exact repeated payloads (2026-10-14)

A second read-only pass over the same 28-row loop journal found 76,576,487
bytes of selected decision payload across 28 distinct decisions. Three unique
large JSON string fields account for 58,585,366 bytes: native accounting
evidence (26,109,165), recovery evidence (20,902,567), and next-turn evidence
(11,573,634). These are about 76% of all selected decision bytes. There were
17 top-level scalar strings of at least 50 KB, totalling 63,215,452 bytes;
those complete large strings were all distinct, so exact whole-string reuse
does not explain their size.

Parsing those embedded JSON strings one level further exposed exact repeated
leaf payloads across authenticated evidence: `canonical_record_bytes` occurred
72 times but had only 12 distinct byte values, with 10,079,428 repeated bytes
beyond the first copy. `canonical_payload` had 584,308 repeated bytes;
`content`, 2,201,350; `request`, 527,436; and `trust_snapshot_bytes`, 218,952.
These counts describe repeated serialized values in the saved decision
artifact. They do not show which validators parse each occurrence, how much
CPU they consume, or whether any copy can be removed without weakening a
distinct historical proof.

The two available journal cuts also have similar outer bytes per row:
5,232,815 bytes for the 40-row cut and 5,470,009 for the 28-row cut. Their
history shapes differ, so this is not a controlled scaling curve, but it
provides no observed exponential signature. Keep “exponential” unproven.

This updates the live explanation: large evidence envelopes contain a mix of
unique source bytes and exact repeated canonical records. The leading route is
now the CPU cost of decoding/canonicalizing those embedded payloads, whether
the work repeats the same bytes or processes unique large sources. Repeated
validator calls remain a separate candidate; inclusive nested timings cannot
rank it against payload volume.

The next discriminating profile should attribute decode/encode CPU to the
exact input byte hash and caller boundary for the three large evidence
envelopes and repeated `canonical_record_bytes`. Report distinct bytes,
occurrence bytes, repeated bytes, and exclusive CPU separately. If exact
repeated values account for material exclusive CPU, test only a local reuse at
that authenticated boundary. If unique source bytes dominate, do not pursue
reuse as the main route; inspect the evidence producer and its contract before
proposing a storage or schema change.

### Input-hash profile of the authenticated reader (2026-10-14)

The same saved cut was read once more with passive `sys.setprofile` around
`read_execution_call_history`, recording exact input hashes at Python
`JSONDecoder.raw_decode`, `TypeAdapter.validate_json`, and
`BaseModel.model_validate_json` frames. The reader passed with nine Run records
and one ordered call. The profile covered only the calling thread and added
material overhead: its reader interval was 21.916 s wall / 21.842 s thread CPU,
versus 15.003 s / 14.956 s in the preceding unprofiled read. Use the input
identities and call counts below; do not treat profiled durations as baseline
latency. C/Rust internals are not individually visible beyond the Python
wrapper's inclusive interval.

The strongest exact repeated-input signal is `r13_runtime._pending`. Within
this one history read it called `json.loads` on each of the same three large
decision payloads 21 times apiece:

| Selected decision | Exact input bytes | Repeated decodes | Raw-decode CPU (inclusive, profiled) |
|---|---:|---:|---:|
| Native cancelled accounting | 36,513,791 | 21 | 1.813 s |
| Accounting readiness | 22,307,799 | 21 | 0.822 s |
| Next Turn | 12,812,361 | 21 | 0.505 s |

These three inputs alone amount to 1,504,312,971 bytes presented to
`raw_decode` across the 21 `_pending` passes, with 3.140 s of measured
inclusive raw-decode CPU under the profiler. Their hashes and lengths exactly
match the three corresponding selected loop-journal decisions in the saved
artifact. Source confirms `_pending` authenticates `.entries()`, then performs
`json.loads(payload)` for every row to derive pending state and validate the
DECIDED/MATERIALIZED sequence.

The same three hashes also recur through distinct validation callers in this
reader: `R17 read_historical` parsed each five times and
`authenticate_native_recovery_selected` parsed each four times. Their measured
inclusive intervals overlap with caller work and each other; do not add them
to the `_pending` total. This directly establishes repeated parsing of the
same large decision bytes across multiple reader paths. It does not yet show
that every occurrence has the same semantic purpose or that a result can
safely cross those boundaries.

This is stronger than the earlier call-count-only hypothesis: one measurable
source of the long CPU path is repeated full JSON parsing of three 13–37 MB
selected decisions, especially in `_pending`. It is not the entire 15 s reader
cost; other validators and canonical encoders remain live. The owner-prefix
snapshot route remains dismissed for this cut (23.8 ms total across startup
and read). Exponential scaling remains unproven.

Before changing code, the next discriminator is to attribute the 21
`_pending` invocations to their caller stacks and check which occur inside the
same held `AuthorityGate` / captured loop-journal cut. If they are repeated
within one immutable read cut, the smallest candidate is local reuse of the
already authenticated entries or their parsed pending projection, while
retaining each public boundary's required source/head check. If the invocations
cross independently admitted boundaries or observe different cuts, they must
remain separate. Preserve the current negative behavior for orphan markers,
rival pending publications, malformed entries, and H1 V2 durable evidence.

### `_pending` caller-stack differential (2026-10-14)

A passive profile of only the inherited `R13Runtime._pending` code object was
run twice on the same saved cut. Both reader runs passed (nine Run records,
one ordered call), at about 15.7 seconds wall / thread CPU. The profile found
21 `_pending` calls in one `read_execution_call_history` invocation. Their
caller stacks divide into nested R17 ingress history reads, historical
recovery/native-scope validation, and one top-level `_require_no_pending`
check. Several ingress-history reads sit under distinct historical validation
paths; they are not 21 calls from one loop in a single function.

Each `_pending` invocation took roughly 0.19–0.22 seconds inclusive thread
CPU on this fixture. Those values include nested work and can overlap, so
they must not be summed as exclusive cost. The earlier hash profile
independently attributes 3.140 seconds of inclusive raw JSON decode CPU to
the three repeated decision inputs inside `_pending`. This makes repeated
authenticated pending-state reconstruction a real contributor, but it does
not account for the full ~15.7-second reader path. The four named readiness
accounting validations each still took about 5.2–5.6 seconds inclusive; those
intervals nest in the same historical checks and are not additive.

The stack evidence rules out the simplest optimization claim: there is no
single loop that calls `_pending` 21 times on one already captured tuple.
Calls are reached through multiple semantic validators, and each obtains
entries through the authority-gated journal path. A cross-call cache would
therefore need to preserve each call's authenticated source/head observation;
this profile alone does not show that those observations share one lease or
captured cut. Keep cross-boundary reuse rejected until that equivalence is
proved. The remaining measurable hypotheses are (a) repeated JSON decode of
the same authenticated decisions across those boundaries, and (b) separate
canonicalization/validation work over large evidence. They can be separated
only with an exclusive call tree keyed by decision hash and captured source
cut. No evidence here supports exponential growth, GIL wait, or a safe
cross-boundary cache.

One sandboxed attempt failed before the reader because the owner process could
not bind its AF_UNIX socket. Re-running the same bounded probe with the
required permission completed; after each process, the temporary 44-file
fixture was restored from the verified backup and checked for content, mode,
and mtime. The main SQLite file inode remained 145264204. Each profile was
limited to one reader call; this is not a load test or a controlled scaling
series.


### Differential decision before another optimization (2026-10-14)

The current evidence separates several claims that had been conflated:

| Claim | Differential result | What it rules out / leaves open |
|---|---|---|
| The long read is waiting on the GIL or I/O | On the saved cut, wall time and calling-thread CPU were nearly equal; low-level CPU is concentrated in JSON decode/canonical encode. | GIL waiting is unsupported. A CPU-bound Python path is observed. This does not identify which semantic validation is redundant. |
| The large journal file alone explains the delay | One outer parse/hex pass was about 0.52 s; a hot exact-body read/compare was 0.03–0.05 s. | One physical read or one parse is insufficient. Repeated semantic parsing and validation remain live. |
| Runtime grows exponentially with history | Two available cuts differ in history shape and have similar outer bytes per row; there is no controlled size series. | Exponential growth is not established. The observed repeated full-prefix work is compatible with superlinear growth, but does not prove an exponent. |
| Repeated `_pending()` decoding is the whole bottleneck | Three exact selected decisions contribute 3.140 s inclusive `raw_decode` CPU across 21 calls, within a roughly 15.7 s reader. | This is a real, bounded contributor, not a sufficient explanation. An empty-result memo could at most target part of this measured work; it cannot explain or certify the remaining deadline failure. |
| One generic history snapshot cache is justified | `_pending()` calls occur through distinct semantic validators; the reentrant gate alone does not freeze state. Fresh journal `entries()` rechecks source/head, while an append returns a different tuple object. | Reject cross-boundary/general caching. A narrowly scoped reuse would need fresh `entries()` on each call and exact tuple identity; this still does not address recovery/readiness validation. |
| Readiness validation is the next candidate to remove | Four calls each cost about 5.2–5.6 s inclusive, but they are nested; a separate exact-input profile shows the same decision bytes also enter distinct recovery and R17 readers. | Keep this as the leading unresolved route. Inclusive totals cannot show whether calls duplicate the same semantic obligation or merely share bytes. Do not remove a call based on byte identity alone. |

Decision: do not broaden the memo proposal or alter the five-second horizon. The
next probe should make a caller-by-input matrix for the four readiness
validations and the nested recovery/R17 readers. For every boundary, record the
decision hash, cut identity, caller stack, exclusive CPU, decoded/canonicalized
bytes, and the semantic checks performed. The decisive discriminator is
whether two calls validate the same obligation over the same authenticated cut
with the same dynamic dependencies. If yes, test reuse at that named boundary
while preserving all other eligible-candidate checks and the source/head
recheck. If no, retain both and profile the largest unique payload path. Do not
rerun the multi-minute selector to obtain this matrix; use one bounded direct
reader call on the preserved fixture and restore/verify the fixture afterward.


### Differential follow-up: saved-cut validity and live one-seal attribution (2026-10-14)

The old `pytest-9070` fixture root had been removed. Its 44-file backup was
restored under the same pathname and matched by size, SHA-256, mode, and mtime,
but the current runtime rejected it during startup with the shared error
`selected native accounting source differs from historical cut`. The validator
uses this error for a compound predicate over source bytes/stamp, tenant/head,
authority, preparation bindings, lineage, inventory, seal and fingerprint; the
trace did not identify which field differed. The only established result is that
matching the restored file attributes did not produce a cut accepted by this
current runtime, so the probe is not a valid measurement of the target read. It
briefly left a SQLite handle while unwinding; after process exit `lsof +D` showed
no open handles, and the temporary copy was restored in-place from the backup
and reverified across all 44 files. Do not retry this backup as though matching
file bytes were sufficient; first create a current-valid fixture or isolate the
rejected predicate.

A passive `cProfile` hook then ran inside the existing short mounted mixed-V3
selector on its live current-code fixture. Command:

```sh
PYTHONPATH=/private/tmp:/Users/thunderbird/Projects/chiplog/src .venv/bin/python -m pytest -s -q -p r14_short_cprofile_plugin tests/composition/test_r14_native_selected_accounting_source.py::test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once
```

The selector passed (`1 passed in 110.48 s`). Profiling covered the test's
calling thread, including setup and reopen operations: wall 109.44 s, thread
CPU 85.82 s, cProfile thread time 85.75 s. It is not comparable to the earlier
unprofiled 32–33 s duration or to a single direct reader call.

| Function group | Calls | Exclusive thread CPU | Inclusive thread CPU |
|---|---:|---:|---:|
| JSON `iterencode` | 46,714 | 19.84 s | 19.84 s |
| JSON `raw_decode` | 18,454 | 10.35 s | 10.36 s |
| `R13Runtime._pending` | 292 | 0.028 s | 5.05 s |
| `R14Runtime._scan` | 557 | 0.023 s | 6.45 s |
| H1 workspace issuance `_scan` | 112 | 0.001 s | 0.261 s |
| `_read_call_history` | 93 | 0.115 s | 46.48 s |
| journal `_entries` | 2,834 | 1.38 s | 7.11 s |

These rows overlap by call-tree nesting except for exclusive CPU, so inclusive
times must not be added. The short mounted shape confirms the same broad
profile signature: JSON encoding/decoding is substantial; H1 issuance scanning
is small here; `_pending` is not all the cost. It does not establish a saving
from memoizing `_pending`, nor does this one-seal test explain the failed
40-entry two-seal writer admission.

The best-supported live hypothesis is now repeated canonical serialization
inside authenticated history reconstruction, with `RecoveryDTO.canonical_bytes`
accounting for 11,867 calls and 18.92 s inclusive in this profile. That is a
candidate boundary, not proof that calls repeat the same object or can be
removed. The next attribution should occur inside a fresh current-code fixture
and identify the caller family and represented DTO size without hashing or
serializing again inside the timed region. For the two-seal deadline, attach
that passive probe to the next required mounted run; do not reconstruct the
removed historical fixture or rerun the multi-minute selector solely for
profiling.
