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

### Current-tree two-seal witness result (2026-10-14)

The exact mounted selector was run on the current tree without a profiler:

```sh
UV_CACHE_DIR=/private/tmp/r14-6-uv-cache GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=false \
uv run pytest -q tests/composition/test_r14_native_selected_accounting_source.py::test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay --durations=1
```

The sandboxed attempt stopped before owner startup because AF_UNIX bind was
denied. The same selector with local owner sockets permitted ran for 717.24 s
and failed at `runtime.accept_call(...)` with `call acceptance rejected: writer
STALE`. It had captured and sealed Turn 2 and prepared its acceptance preview;
the writer did not select the second call. Therefore the test did not reach
second-seal terminal publication, second-seal V3 publication, cross-order
replay, or reopen. The current result is a functional failure, not R14.6
completion evidence.

This wall duration is not a controlled comparison to the earlier 416.00 s and
420.61 s runs: those were separate invocations, and this run had no phase timer
or profile. The failed fixture's loop journal is 209,312,600 bytes, matching the
size recorded in the earlier writer-probe artifact; file size alone does not
explain the extra elapsed time. Do not call this a performance regression or an
exponential curve without matching per-phase measurements and workload counts.

#### Differential ranking

- **Leading mechanism: repeated authenticated semantic history work.** In the
  earlier 416 s invocation, 105 `_read_call_history` calls consumed 356.418 s
  wall / 356.059 s thread CPU; 216 inventory calls consumed 1.680 s total, and
  the writer's single history read took 13.062 s. In the separate 420.61 s
  invocation, `capture_dispatch` took 0.176 s. These measurements locate most
  of the earlier run in history reads, but do not attribute the current 717 s
  total to the same calls.
- **At the time of this unprofiled run, the `STALE` cause was not localized.**
  The later monitored follow-up below identifies the immediate rejection for
  that separate invocation as admission expiry at the preview mandate horizon.
  Neither result attributes the full selector runtime to that guard. Extending
  the horizon would neither reduce history-processing work nor establish a
  safe new authority duration.
- **GIL contention is unsupported.** The measured history work was almost all
  thread CPU time. That supports CPU-heavy Python/JSON processing; it does not
  establish contention from the GIL.
- **Test trimming and cross-call caching are not selected remedies.** Removing
  readiness, the real next-Turn transition, writer admission, or reopen would
  discard required evidence. Reusing results across reads would also erase the
  fresh authenticated-source check. Only exact repeated work within one
  authenticated cut remains a candidate, and each reuse must retain the
  existing source/head checks.

#### Next discriminator

Do not repeat the full selector solely to collect more timing. Use the preserved
current failed fixture for one bounded observation of the writer's history
path only after a non-mutating preflight proves that startup will not repair or
recover selected rows. This is currently unproven: `open_common_cli_execution_runtime`
enters `_open_runtime`, which calls `trust.recover_materialization()` and then
`runtime._prepare_startup()`; R14 startup can complete pending owner, loop, and
gate publications. A successful open could therefore change the exact fixture
being measured. Do not open the original fixture until a source-authenticated
preflight establishes that none of these recovery writes can occur and the
runtime will preserve the existing path/inode. If no supported read-only
preflight can establish this, defer the probe to the next required mounted R14
run rather than profile this saved fixture.

At that next required run, separate `read_execution_call_history` from
`capture_dispatch`, record the exact `_check_live` rejection predicate, and
count/time selected recovery, next-Turn, native-accounting, and historical-source
helpers without emitting payloads. A candidate optimization survives only if it
removes byte-identical duplicate work within one authenticated cut while
preserving all final currentness and deadline predicates.

#### Differential follow-up: second two-seal writer refusal (2026-10-14)

The mounted selector
`test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`
was repeated once with passive `sys.monitoring` probes. It again failed at the
second `runtime.accept_call(...)` with writer `STALE`, after preparing the
second acceptance preview. It did not reach the second terminal/V3 publication,
cross-order replay, or reopen. This is a reproduced functional failure, not
completion evidence.

The probe observed the exact rejection branch this time. During
`CallPublicationAuthority._check_live -> capture_dispatch -> require_invocation`,
`R7PlanningRuntime._trust_observation_guard` reached its admission-deadline
return. At that line, `admission_valid_until_ns` was already 6.165 seconds in
the past; `require_invocation` independently classified the refusal as
`STALE:admission-expired`. `prepare_call_exchange` sets this field from the
selected preview mandate's `horizon.expires_at_ns` (`r14_call_preparation.py`),
so this is mandate expiry, not evidence that the five-second authentication
RPC budget itself expired. The separate final deadline comparison in
`_check_live` was not reached. Do not conflate these two guards.

This discriminates the immediate refusal from the performance hypotheses:

| Hypothesis | Result from this run | Consequence |
|---|---|---|
| The current `STALE` is the five-second owner/RPC preparation horizon | Rejected for this invocation: the guard reported preview mandate expiry before `_check_live`'s final minimum-deadline predicate. | Do not extend or retune the five-second horizon to fix this refusal. |
| The current `STALE` is an owner session, ledger generation, trust snapshot, source-cut, or branch change | Not the observed immediate rejection: the admission-deadline branch returned first. Those later predicates were not reached in this invocation. | Keep these integrity checks; this run does not eliminate them as possible later failures. |
| Waiting on AF_UNIX or the GIL explains the delay | Disfavored by separate prior profiles where measured history work was mostly calling-thread CPU; this run itself only identifies the local expiry branch. | Do not change socket/thread policy from this evidence. The current run has no phase attribution. |
| One large retained record parse explains the delay | Disfavored by the existing single-payload discriminator and history profiles, which found one parse/canonicalization pass far below stage duration. | Repeated semantic history reconstruction remains the stronger hypothesis. |
| The runtime grows exponentially | Still unproven. These are separate test invocations, not a controlled series with matched starting state and workload cardinality. | Describe observed superlinear-looking growth only; do not fit an exponent. |

The result establishes the immediate refusal for this invocation, but does not
yet select a safe performance patch or explain the full selector duration. The
leading mechanism remains repeated authenticated history work; its stage costs
in this invocation were not measured. Cross-call caching, saved-cut reuse,
deadline changes, and skipped validation remain unsupported. Before editing
code, the next decisive evidence is a passive timeline around the mandate and
the subsequent acceptance stages, including source/currentness dependencies.
Capture it during the next required mounted functional run; do not rerun the
full selector solely to recover a lost report.

#### Differential boundary and narrow readback candidate (2026-10-14)

The mandate is issued with `not_before_ns=captured.observed_time_ns` and expires
60 seconds later. The monitored rejection reached its admission-deadline guard
at approximately 66.165 seconds of mandate age. This measures age at that
guard, not the duration of any individual stage and not the share of the
717.24-second selector spent in this interval. The five-second owner/RPC
preparation budget is a separate deadline and was not the deadline reported by
this rejection. Session, ledger, trust-snapshot, source-cut, and branch checks
after the deadline guard remain unexamined for this invocation.

Source inspection identifies work after mandate start, without apportioning its
runtime: precursor owner exchange; renewed authentication; fresh dispatch
capture and scope checks; `read_call_previews()` before append; append and
`read_call_previews()` after append; then later preview/history validation in
`prepare_call_exchange()` and a fresh history read at writer admission. Each of
the two preview reads reconstructs execution history. Separate profiles make
repeated history processing plausible, but do not quantify these calls in the
monitored two-seal invocation.

The initially proposed optimization—replace the post-append full semantic
preview/history read with exact raw journal readback—does not reduce the history
work in this failing selector. `_selected_v23_native_fanout` defaults to no
accepted calls and this test supplies no accepted-call IDs; it cancels the first
Turn's call and creates its first preview only for the second Turn. Therefore
the preview journal is empty before this issuance: pre-append
`read_call_previews()` skips history, while post-append `read_call_previews()`
performs the one fresh history reconstruction. Requiring history before append
and removing the post-append history read would move that one read, not remove
it. The possible saving is limited to repeated validation of the newly appended
preview, whose cost has not been measured. Do not select this as the fix for the
two-seal deadline failure.

The raw-readback idea also is not strictly equivalent under direct external
checkpoint mutation between pre-append validation and selection: the current
post-append reader would observe the changed checkpoint; raw journal equality
would not. Preparation and writer admission still perform fresh validation
before authority is granted, but that does not preserve the immediate
`preview_call_acceptance()` result. Keep the post-append semantic read unless a
measured benefit and a source-currentness proof justify changing this boundary.

The broader measured mechanism remains repeated semantic history replay. A
bounded same-reader candidate is the top-level `_require_no_pending()` followed
by `_read_call_history`'s own loop-journal read and parse. Earlier profiles
showed exact large decision bytes parsed repeatedly by `_pending()` through
distinct nested validators; they do not justify a cross-call cache. Before
considering only the top-level duplicate, prove that one captured journal tuple
can feed both pending-state validation and history replay while retaining the
same pending/H1 checks and a fresh exact source/head comparison before return.
Measure that local contribution on the next required mounted functional run;
do not rerun the selector solely for profiling.

#### Same-reader pending-reduction boundary (2026-10-14)

Cold review narrows the candidate further. Reordering `_pending()` before the
owner check is not equivalent: `_require_no_pending()` short-circuits, so a
pending owner currently wins without reading an unauthentic loop journal. The
established strict-read precedence is owner, loop/H1 validation, then gate
publications. Keep that order and retain selected-only's distinct behavior.

The plausible saving is one duplicate semantic JSON decode per ordinary
strict history read, not removal of authenticated source checks. A possible
shape is to reuse the decoded loop entries locally for pending reduction and
history replay, while retaining a fresh `entries()` call after gate-pending
inspection and before replay. Require that fresh call to authenticate the same
exact cut; if it differs, fail before semantic replay. This also preserves
visibility of a body/head/source mutation during gate inspection. The journal
already caches decoding only for byte-identical bodies, but validates physical
source identities, key bytes, and anchored head on every `entries()` call.

The old profile attributed about 0.19–0.22 seconds inclusive to each
`_pending()` invocation on its fixture; it did not measure an isolated current
strict read or show that this saving survives the full mounted path. Treat this
as a small local optimization candidate, not an explanation for a mandate that
expired by 6.165 seconds. Before implementation, the next mounted run must
measure exclusive pending-reduction and history-replay costs around the same
reader, and confirm the candidate can recover a material part of the observed
slack while preserving the order and cut checks. Required differential cases
include pending-owner precedence, malformed H1 validation before gate-pending,
mutation during gate inspection failing before semantic replay, and
selected-only behavior. If the measured saving is immaterial, retire this
candidate without changing the checks.

#### Full parsed-entry cache rejected on memory cost (2026-10-14)

Two cold reviews disagreed on whether to implement a read-local cache or measure
first. A read-only parse-only probe against the saved writer-cut loop journal at
`/private/tmp/r14-profile-9056/native-v23-prepare.sqlite.loop-journal` resolves
the main tradeoff without opening its runtime. The three large selected payloads
were 36,505,620, 22,324,899, and 12,820,575 bytes. One parse of the three took
0.189 seconds of thread CPU and left 112.0 MiB of traced Python allocations
retained (126.5 MiB peak). Artificially parsing those same three inputs 21 times
took 3.689 seconds of thread CPU.

The 21-call count and 3.140-second raw-decode figure came from a different saved
cut. Its payloads had the same lengths but different SHA-256 values, so neither
the call count nor the predicted saving is established for this writer cut. The
21-pass probe above is a bound on repeated parser work for its exact bytes, not
an observation that the failed writer read made 21 such passes. The 112 MiB
retained-object cost would also add substantial memory to a history reader, and
the cache would make mutable dictionaries returned by `_pending()` alias across
calls. No whole-dictionary cache is selected.

This retires the full parsed-entry cache as the current R14.6 fix. Reopen the
candidate only if a required mounted functional run attributes enough repeated
`_pending()` calls to exact input hashes to show material time savings, and a
bounded implementation can avoid retaining these full parsed objects or can
measure an acceptable memory tradeoff. Continue to rerun journal source/head and
H1 validation at every existing boundary. This candidate does not explain the
separate 6.165-second mandate overrun; that remains a writer-path failure, not
a parser-cache verdict.

#### Post-append freshness substitution rejected on deadline behavior (2026-10-14)

A possible narrower shortcut was to retain the authenticated execution history
used before append, enumerate and validate the newly selected preview, then
replace the post-append `read_execution_call_history()` with a fresh
`capture_dispatch()` compared to the original capture. Cold source review found
that a capture after enumeration would detect changes to the physical AMR
commitment/frontier, executable Run bytes, owner snapshot, and authenticated
loop head. It also found a behavioral difference: `capture_dispatch()` calls
`require_invocation()`, which enforces the authentication admission deadline;
the existing post-append preview reader does not. If append plus preview
enumeration crosses that deadline, the proposed path can reject after the
preview is already durable, where the current path returns it. No contract
currently authorizes that new failure boundary.

Ordering also matters independently of the deadline: a capture before preview
enumeration leaves a mutation injected during enumeration undetected, while the
current reader authenticates history after enumeration. A post-enumeration
capture addresses that ordering race, but not the additional late-deadline
failure. Therefore do not substitute the capture or implement this optimization
unless a contract change explicitly accepts that late refusal and recovery
behavior. The safe differential conclusion is that repeated history work is
plausible, but this candidate is not behavior-preserving as written; keep the
existing post-append reader and seek a measured optimization with equivalent
deadline semantics.

#### R14.6 acquisition discriminator and writer-read candidates (2026-10-14)

Command:
`uv run pytest -q tests/composition/test_r14_native_selected_accounting_source.py::test_v3_source_projects_mixed_terminal_members_in_seal_order`
Result: 1 passed in 42.25 seconds on the current tree. This mounted witness
selects a genuine two-member seal with one accepted and one cancelled member,
collects the authenticated source facts in seal order, and obtains the exact
V3 owner preparation. It confirms the mixed-member acquisition baseline. It
does not prove two seals in one Run, durable V3 publication/reopen, full
physical orphan detection, or writer-admission freshness; those remain open.

Two independent source reviews searched for a behavior-preserving reduction of
the recorded 12.9-second writer-side `read_execution_call_history()` that could
recover at least the 6.165 seconds by which the later mandate-expiry sample
missed its fixed horizon. These measurements came from separate invocations;
their difference does not show that a 12.9-second read caused the later expiry
or that shortening it by 6.165 seconds would make the flow pass. Neither review
established such a candidate. They located
three possible same-reader duplicates: V3 retained-evidence decode, V3 source
collection followed by request reconstruction, and pending reduction followed
by strict loop-entry parsing. The first two lack matched exclusive timings; the
last is estimated at only 0.19–0.22 seconds on a different fixture and still
requires strict owner/loop/H1/gate precedence and a fresh source/head check.
That estimate cannot be transferred to the two-seal writer cut.

Route decision: defer these optimizations until instrumentation can be captured
during the next required R14.6 mounted functional witness. Capture exclusive
time, caller, input identity/size, and authenticated cut/head for each candidate
while preserving all existing admission and deadline checks. Do not repeat the
long selector solely to profile it, and do not treat the focused acquisition
pass as a fix for the separate two-seal writer refusal.

A proposed split test that stopped after publishing the first seal and building
source facts for the second seal proved too expensive to use as a standalone
discriminator. Command:
`uv run pytest -q tests/composition/test_r14_native_selected_accounting_source.py::test_v3_collects_two_selected_seals_before_second_seal_acceptance`
It was interrupted after 472.96 seconds with `KeyboardInterrupt`; pytest reported
no tests ran, so it provides no semantic verdict and did not establish which
internal stage was active. The temporary test change was removed. This rejects
the proposed *separate setup-only run*, not the underlying two-seal requirement;
observe source acquisition during the next required functional witness instead.

#### Issuance decode count by authenticated reader boundary

The saved trace resolves the proposed intra-verifier candidate without another
mounted run. It contains 13 `verify_dispatch_history()` spans. Each span visits
nine selected effect rows, including exactly one row from each of the three
accepted-call batches. Each selected call issuance is decoded once per verifier
span; there is no repeated decode of one batch within a verifier invocation.
The same profile contains four strict `_read_call_history()` spans, each of
which validates each of the same three selected call issuances once. Thus each
issuance is decoded 17 times total: 13 times at separate effects-history
verification boundaries and 4 times at separate loop-history boundaries.
Across all three issuances, 39 decodes are children of the 13 verifier calls and
12 decodes are children of the four strict history calls. This exactly accounts
for the observed 51 decodes.

This falsifies the proposed local per-verifier memoization: its eligible
duplicate count is zero. The repeats are across reader boundaries, where each
call has freshly acquired effects/history state or independently validates the
selected loop history. Any reuse across those calls would need a proof that
preserves the current physical membership, commitment, owner-journal and
tenant-history checks. The trace establishes cost, not that those boundaries
are redundant. Keep cross-call reuse unselected.

The acquisition map and call-chain source are now established below. A candidate
survives only if it removes repeated work while preserving fresh physical
membership and exact source/head checks. The four strict history decodes belong
to distinct `_read_call_history()` calls; the existing trace identifies one
nested strict replay already removed by the same-runtime forwarding experiment.
Do not infer further savings by dividing 15.47 seconds across 17 validations.

#### Acquisition boundary inside one strict history read

Source tracing maps the 12 seal-specific verifier calls to the deferred V3
loop in `_read_call_history()`: each selected V3 record is decoded separately;
that decoder rebuilds the retained source and calls
`prepare_sealed_accounting_v3()`. The source then invokes
`_read_materialized_effects_with_history()` and `verify_dispatch_history()`.
The 13th verifier call is the separate target-terminal validation.

The three seal validations do not currently share one SQLite transaction.
`_read_call_history()` holds its authority gate and a local `read_connection`,
but it does not establish `workspace_snapshot()`'s context. Each effects
acquisition enters `join_workspace_snapshot()` with no current snapshot, so
that helper opens a new query-only `BEGIN` transaction. Each acquisition also
captures the current materialization commitment, checks exact physical batch
membership, compares the full effects member set, and rechecks the owner
journal and commitment before returning. The repeated cuts had equal measured
owner/effects/execution-history identities, but they were independent physical
read transactions. That identity match does not prove their physical view was
unchanged between acquisitions.

This narrows, but does not close, the leading hypothesis. The measurable repeated
work is seal-independent full R16 history acquisition/verification followed by
seal-specific joins. Reusing one verified dispatch history across the three
seals could avoid two full acquisitions per strict read, but would change the
current observation from three separately fresh cuts to one cut shared by all
three. The code evidence does not establish that a read-start snapshot is the
required contract, nor that the held in-process authority gate excludes every
physical writer. Do not implement that reuse from equal identities alone.

The next discriminator is a contract-preserving differential test of the
physical freshness boundary: establish whether a mutation to effects membership
or the current commitment between seal validations is required to make the
whole history read HOLD. Compare the current separate-acquisition path with a
candidate that shares the first physical snapshot; if the candidate accepts a
mutation that the current path rejects, reuse is invalid under the present
contract. If the contract instead defines one snapshot for the whole read, that
definition must be grounded in the existing normative contract before treating
such a mutation as in-scope. This is a correctness discriminator, not a
performance benchmark; do not rerun the long three-member scenario merely to
measure call counts already present in the saved trace.

#### Cost bound for same-reader V3 verification reuse

The saved baseline trace gives an upper-bound estimate for the same-reader
candidate without another run. The 12 V3-related `verify_dispatch_history()`
spans account for about 17.9 seconds of inclusive CPU (the 13-span total is
19.27 seconds, including the separate terminal validation). Each V3 physical
acquisition accounts for about 0.28–0.38 seconds CPU; the remaining verifier
cost includes selected dispatch request/issuance validation. A strict history
pass currently performs three seal-specific acquisitions/verifications. If a
single common verification could soundly replace those three while preserving
seal-specific joins, the saved baseline suggests an upper bound of roughly
three seconds CPU per history pass, before accounting for any cost of sharing
or rechecking freshness. This is material but cannot explain the full ~60-second
target read. The four history passes include a nested strict replay already
removed by the same-runtime forwarding comparison, so this upper bound should
not be projected onto the current target read without a new matched profile.

Route state: intra-verifier issuance memoization is **closed / falsified** by
zero within-pass duplicates. Cross-reader cache reuse is **closed / rejected**
because each reader reacquires current physical membership and authority. A
single common snapshot/verification across the three V3 seals in one
`_read_call_history()` remains **open / unproven**: it has a bounded possible
saving, but it would replace three independent physical acquisitions with one.
The current next action is to ground the required freshness scope, then design a
negative mutation-between-seals probe. Do not implement or claim a speedup until
that boundary is resolved.

#### Keep the read hotspot separate from the two-seal writer failure

The same-reader three-V3 verification opportunity belongs to the saved
three-accepted-member target-read profile. It must not be used to explain the
separate `test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`
writer refusal: that invocation failed while preparing acceptance on Turn 2,
before its second terminal or V3 publication existed. At that point the selected
history contained only the first seal's V3 record, so there was no three-seal
fanout to collapse. The known 6.165-second mandate overrun and the ~60-second
terminal-read CPU hotspot are separate symptoms with different selected
histories and call paths. Any optimization proposed for one must be measured on
that exact path before claiming it addresses the other.

#### Mandate-age timeline hypotheses (2026-10-14)

Source inspection confirms where the 60-second clock starts and which work
follows it. `preview_call_acceptance()` first reads execution history,
authenticates, and captures dispatch. It then constructs the mandate with
`not_before_ns=captured.observed_time_ns` and expiry at
`captured.observed_time_ns + MAX_HORIZON_NS`. After that timestamp, preview
still calls the owner to evaluate the mandate, authenticates again, captures
dispatch again, checks scope and preview validity, and selects/readbacks the
preview journal entry. Only after the preview returns does `accept_call()`
start `prepare_call_exchange()`: fresh authentication, selected-preview and
history reads, another dispatch capture, loop-owner preparation, effects-owner
preparation, issuance validation, and finally writer admission. Writer
`_check_live()` reads history and captures dispatch again.

This ordering establishes **exposure**, not elapsed time: both completion of the
preview and the later acceptance consume the same mandate horizon. It does not
show which interval consumed the 66.165 seconds observed at the admission
rejection. In particular, do not assign the overrun to the writer history read,
the preview owner's IPC, repeated authentication, or time between calls from
the ordering alone. The focused trace establishes that admission expiry was
the first refusal observed in `require_invocation`; it does not establish that
no other freshness condition would also have failed later.

Differential hypotheses for the **next required mounted functional run**:

| Candidate source of mandate age | Discriminator | Interpretation |
|---|---|---|
| Preview before the caller can accept | Time the initial execution-history read, both selected-preview/history reads around append, both authentication calls, both dispatch captures, the effects owner call, and the whole preview. Record start/end offsets from one monotonic origin. | A large preview interval, with the inner span boundaries, locates horizon consumption before the caller receives the preview. |
| Acceptance before writer admission | Time initial replay authentication, outer `CallPublicationAuthority.prepare_fresh`, nested `prepare_call_exchange`, fresh authentication, preview/history reads, capture, effects owner call, and writer submit. | The direct loop-owner RPC and local preparation are inside `prepare_call_exchange`; the surrounding `prepare_fresh` span also includes issuance validation after that await. Their difference bounds the outside work. If the outer residual dominates, split it later. |
| Writer guard | Time `_check_live`, its existing history read and dispatch capture, and record mandate age at guard entry/exit plus the original `LoopRejected` reason before the existing `STALE` mapping. | This separates an admission-expiry reason from other stale causes and measures time actually inside the guard. |
| Time outside measured calls | Compare the start/end offsets for preview return, accept entry, wrapped calls, and writer entry in the same trace. | The remaining gaps locate caller/runtime work not covered by an inner wrapper; do not assign those gaps to a particular subsystem without another discriminator. |

Keep the existing mandate, RPC, source-observation and lease horizons fixed.
The test-local wrappers preserve each original call and exception, then restore
through `monkeypatch.context()`. Span rows contain start/end offsets, elapsed
wall time, and calling-thread CPU; nested spans overlap and their CPU values are
not additive. The trace emits only scalar timing data and the fixed refusal
reason, not request or journal payloads. Do not reread journals to count records
or measure bytes. Attach these marks to the next required functional run; do not
repeat the multi-minute selector solely to collect them. The result should
decide whether to optimize preview tail, authenticated history replay,
acceptance owner work, writer validation, or none of those. It must not presume
that speeding up one history read will recover the full 6.165 seconds.

#### Current-tree two-seal trace with phase boundaries (2026-10-14)

Command (the sandboxed attempt failed before test setup because the spawned
owner could not bind its AF_UNIX socket; the same test was then run with local
owner sockets permitted):

```sh
TMPDIR=/private/tmp UV_CACHE_DIR=/private/tmp/chiplog-uv-cache \
uv run pytest -q -s \
  tests/composition/test_r14_native_selected_accounting_source.py::test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay
```

The mounted test ran for 717.22 seconds and failed at second-turn writer
admission with `STALE:admission-expired`. It did not publish the second
terminal/V3 record or reach cross-order replay and reopen. The test-local trace
starts immediately before preview; measured preview plus acceptance was 85.46
seconds. Thus about 631.76 seconds of selector elapsed time lies outside those
two traced calls, predominantly in the required setup that precedes preview;
the trace does not split that setup into phases.

| Span | Offset from trace origin | Wall | Calling-thread CPU |
|---|---:|---:|---:|
| Preview, whole call | 0.000–42.388 s | 42.388 s | 41.492 s |
| Initial `preview.history` | 0.001–20.678 s | 20.677 s | 20.276 s |
| Post-append `preview.preview_history` | 21.539–42.386 s | 20.847 s | 20.412 s |
| Nested post-append `preview.history` | 21.616–42.385 s | 20.769 s | 20.298 s |
| Acceptance, whole call | 42.388–85.463 s | 43.075 s | 21.370 s |
| `accept.preview_history` | 42.427–62.768 s | 20.340 s | 20.014 s |
| `writer.check_live` | 64.497–85.462 s | 20.965 s | 20.491 s |
| Nested `writer.history` | 64.497–85.141 s | 20.644 s | 20.169 s |
| Nested `writer.capture` | 85.141–85.462 s | 0.321 s | 0.321 s |

The post-append history span is nested inside `preview.preview_history`; do not
add both durations. `writer.history` and `writer.capture` are nested inside
`writer.check_live`. The trace recorded mandate age 21.297 seconds when preview
returned, 43.406 seconds at writer-guard entry, and 64.371 seconds at exit. The
guard's exact first refusal was
`denying invocation is not currently authenticated (STALE:admission-expired)`;
its later source/scope/minimum-deadline checks were not reached. This proves the
immediate failure for this invocation, not that those later checks would pass.

This run separates three issues. First, the same authenticated history reader
costs about 20 seconds at acceptance preparation and again at writer admission.
The writer-side read is longer than five seconds, but this invocation expired
the 60-second mandate inside `require_invocation()` before `_check_live` reached
its later minimum-deadline comparison. The trace therefore does not establish
which owner-preparation deadline was limiting, or that this read alone caused
the observed refusal. Second, preview spends about 20.8 seconds after the
mandate is constructed in its selected-preview readback, which contributes to
the 60-second mandate age. Third, roughly 632 seconds before preview remain
unlocalized. They include
required R14.6 work: first-turn cancellation, cancellation accounting, first
V3 publication, readiness, same-Run next-Turn transition, and second capture
and seal. Source review found no demonstrated publication that can be removed
while retaining this witness. Do not attribute this setup interval to the
writer failure or remove the transitions without a contract-preserving
replacement.

The trace motivates reducing CPU inside a **fresh** history read, not extending
the mandate or reusing the preparation cut at writer admission. Previous saved-
cut profiles found repeated `_pending()` parsing of identical large decisions
and repeated inventory/canonicalization work, but those measurements came from
other cuts; they do not show which part of this 20-second read can be removed
while retaining its checks. The next implementation candidate must preserve
every live journal, physical membership, owner, source/head, branch and deadline
predicate, and demonstrate that the writer read fits its existing deadline.
The post-append preview readback is also not removed by this evidence: it
detects lifecycle changes during the owner call, and replacing it with raw
journal equality would change that behavior.

#### Cold differential review of prepared-history reuse (2026-10-14)

Three independent source reviews challenged a proposed writer fast path that
would reuse the prepared execution inventory after comparing a short list of
database and journal heads. All found the same missing dependency class:
`_read_call_history()` resolves retained H1 workspace issuance and dashboard
issuance through separate journals, in addition to validating selected loop
decisions, physical membership, and retained checkpoint bytes. Those external
inputs can disappear or change while SQLite identity, tenant frontier,
materialization commitment, owner head, and loop head remain constant. The
current full reader would reject; the proposed head-only stamp could use stale
inventory. A held in-process authority gate does not prove those files
immutable.

This rejects the **listed head-only stamp**, not every possible per-preparation
stamp. A safe stamp would need to bind or freshly verify the complete dependency
closure, physical insertions and omissions, and all live authority/resource
predicates. That is a new evidence boundary and race-test matrix, so it is not
the narrowest current change. Keep full fresh history replay at writer
admission. The smaller route is to profile and reduce work inside one fresh
reader, but only where a differential mutation test proves unchanged rejection
behavior.

The measured two-seal run now has a firmer split of the duration: 85.46 seconds
are inside preview plus acceptance; about 631.76 seconds are outside them,
mostly before the trace origin. The trace does not attribute that setup interval
to startup, fixture construction, recovery, SQLite, or any single R14
publication. Earlier reviewers found the current transition chain is required
for the same-Run two-seal witness, so deleting those publications is not a
supported shortcut. The next useful observation is phase timing at the
already-required functional run boundary: existing runtime startup; first Run
begin/capture/seal; cancellation and its accounting/V3; readiness and next-Turn;
second capture/seal; then preview and acceptance. Record elapsed time at each
boundary and use cardinalities already present in returned history views; do not
reread journals to gather counts or bytes. Until that evidence exists, the
632-second segment is localized only to pre-preview setup, and no claim that it
is history replay or exponential growth is supported.

The mounted test now emits `R14_SETUP_TRACE` from these existing boundaries
before beginning the second preview. It times runtime startup, first-turn
admission/begin/capture/seal/cancel, the two already-required history reads,
cancellation accounting, first V3, readiness, next-Turn transition, and second
capture/seal. It also records call and seal counts from the two already-returned
recovery views. The probe calls no additional readers and does not alter
ordering or outcomes. Cold source review found no critical issue. These phase
values are not available yet; the full selector was not repeated solely to
collect them.

#### Differential result from the instrumented two-seal run (2026-10-14)

The required mounted selector was then run once with the phase trace above:

```sh
TMPDIR=/private/tmp UV_CACHE_DIR=/private/tmp/chiplog-uv-cache \
uv run pytest -q -s \
  tests/composition/test_r14_native_selected_accounting_source.py::test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay
```

It failed once after 336.85 seconds at second-call acceptance. It did not reach
the second terminal/V3 publication, reverse-order replay, or reopen assertion.
This is the current instrumented result; the earlier 717.22-second run is a
separate attempt and must not be averaged with it. The returned recovery views
showed one call/one seal after Turn 1 and two calls/two seals after Turn 2.

| Setup phase, in execution order | Wall |
|---|---:|
| Runtime startup | 3.483 s |
| Publish recovery profile | 0.024 s |
| Admit initial slot / drive initial input / begin first Run | 0.327 s |
| Capture and seal first Turn | 2.056 s |
| First recovery history read | 0.151 s |
| Cancel first-Turn member | 1.071 s |
| Publish cancellation accounting | 9.204 s |
| Publish first-seal V3 | 10.267 s |
| Publish accounting readiness | 24.584 s |
| Read first execution history | 4.454 s |
| Start second Turn | 55.188 s |
| Capture second Turn | 144.423 s |
| Seal second Turn | 30.814 s |
| Second recovery history read | 9.631 s |

The setup phases are sequential and sum to about 295.68 seconds. Preview took
19.864 seconds wall / 19.812 seconds calling-thread CPU. Acceptance took
20.623 seconds wall / 10.309 seconds calling-thread CPU. These boundaries leave
the rest of the 336.85-second selector outside those measured calls; the
measured setup accounts for most of that remainder.

The acceptance trace separates the immediate refusal from the earlier
717-second attempt. The mandate was 9.916 seconds old when preview returned.
The writer guard began at mandate age 20.432 seconds and returned at 30.538
seconds, leaving 29.461 seconds before mandate expiry. Its fresh history read
took 9.914 seconds; dispatch capture took 0.191 seconds. `_check_live` then
raised `call acceptance original authority or owner preparation expired`,
which `check_prepared` mapped to `STALE`. Thus mandate expiry does not explain
this invocation. The existing check computes its effective deadline as the
minimum of both owner-call deadlines, effects lease, mandate horizon, and all
captured source-validity horizons. This trace does **not** retain those exact
operands or the winning minimum, so it cannot yet distinguish the loop/effects
RPC horizon from a source-validity horizon. It shows that writer work crossed
the five-second currentness window; it does not license increasing that window.

Source inspection gives a specific, testable explanation for the 144.423-second
second-Turn capture. `capture_execution` has three direct full history reads
and four `_publish_initial_transition` calls (Accumulate Visibility, Prepare,
Emit, Capture); each transition reads history before publication, in its
admission guard, and after publication. That is 15 full history replays in the
capture path. The independent writer/preview measurements show a fresh history
read at about 9.7–9.9 seconds. Multiplying that cost by 15 gives about 145
seconds, close to the measured capture duration. This is a strong source-based
explanation, not an exact attribution: the transition snapshots have different
cardinalities, and no nested per-call profile was collected. The next narrow
discriminator, if still needed after this diagnosis, is to time those existing
15 call boundaries during the next required functional witness, without
skipping any read or changing publication order.

The failed fixture's loop journal was 209,306,700 bytes by file metadata; the
call-preview journal was 4,761,526 bytes and H1 issuance journal 3,222,886
bytes. These are sizes only; the journals were not scanned to count records or
time parsing. The repeated full-history call path is a better-supported lead
than a generic “large request” explanation for second-Turn capture. One
ordinary parse/canonicalization of a single large record is already known to be
too cheap to explain tens of seconds. No observation here establishes
exponential growth: there is one instrumented workload, with stage durations
that vary by operation.

#### Hypothesis discrimination and safe next step

| Candidate | Evidence now | Status |
|---|---|---|
| Repeated strict history replay inside second-Turn capture | Source has 15 read callsites across the three direct reads and four three-read publication transitions; the 15 × observed reader cost predicts the measured capture time. | Leading, not yet isolated by nested timings. Preserve all checks; next measure existing read boundaries. |
| One unusually expensive owner request or socket wait causes the 144 s capture | Capture is a local runtime path containing multiple history reads; no owner wait breakdown was captured for this phase. | Weakened by source structure, not fully ruled out. Do not start with GIL or socket tuning. |
| A 209 MB journal by itself causes the full delay | File is large, but no read/parse attribution or record count was collected in this run. One parse pass is known to be cheap relative to multi-second phases. | Not established; size alone is not a causal measurement. |
| Mandate expiry caused this writer refusal | Mandate still had 29.461 s at guard return. | Ruled out for this invocation. |
| The five-second owner/currentness or source horizon caused the writer refusal | Fresh writer history took 9.914 s and guard ended with the minimum-deadline rejection; exact minimum operands were not recorded. | Strong candidate, exact winning horizon unresolved. Keep bounds unchanged. |
| GIL contention / exponential algorithmic growth | No competing-thread wait measurement; no controlled cardinality series in this selector. | Unsupported. Do not optimize or redesign for either claim. |

The tempting next experiment was to reopen the failed pytest fixture and profile
one `read_execution_call_history`. A cold review rejected treating that as
read-only: runtime startup can change custody, trust, owner, H1, preview, or
journal state without advancing the tenant head, and the proposed reconstruction
of `HermeticDispatchResources` had not yet been proven identical to the original
manifest and bindings. Copying the fixture would also change the custody-bound
path/inode and measure a rejected or different recovery path. Do not run that
profile on the lone artifact. The existing timing evidence is sufficient to
rank the next source-level probe; retain the artifact unchanged.

#### Differential result: request-local loop-cut reuse (2026-10-14)

The first in-reader experiment passes `_read_call_history()`'s already parsed
and authenticated loop-decision cut to `validate_selected_next_turn()`. Before
using it, the validator compares the complete ordered `(decision_id,
predecessor, raw)` journal entries again under the continuously held authority
gate. It retains candidate order, filters, exact raw bytes, per-candidate
accounting validation, and source/physical checks. This reuse is local to one
reader call; it does not cross a gate release, IPC, or publication.

A cold plan review found no critical issue with those conditions and warned
that the work is diagnostic: matching loop bytes does not authenticate physical,
owner, accounting, trust, or source state. Those checks remain in place. The
existing next-Turn mounted suite passed all five tests in 507.54 seconds,
including selected replay, foreign evidence hold, physical tamper rejection,
and reopen. The test command was:

```sh
TMPDIR=/private/tmp UV_CACHE_DIR=/private/tmp/chiplog-uv-cache \
uv run pytest -q tests/composition/test_r14_next_turn_publication.py
```

The two-seal mounted selector was then run once with its existing timing/profile
probe. It failed after 300.64 seconds at second-call writer admission, again
before second terminal/V3 publication and cross-order replay. The precise
refusal remains source freshness: all eleven `source_0` through `source_10`
deadlines were earliest; the effects lease and mandate each had about 29.56
seconds remaining. The five-second source horizons are unchanged.

| Writer-history measure | Earlier profiled attempt | Cut-reuse attempt |
|---|---:|---:|
| Wall / thread CPU | 10.656 / 10.640 s | 10.462 / 10.434 s |
| `json.loads` calls | 2,170 | 2,130 |
| `validate_selected_next_turn` cumulative | 4.538 s | 4.352 s |
| `_validate_eligible_accounting_candidates` cumulative | 3.738 s | 3.667 s |
| Writer refusal | source/currentness deadline | source/currentness deadline |

The roughly 0.2-second read difference is small relative to the 10.4-second
reader and is not a controlled benchmark. Removing 40 JSON decodes did not
change the result. This rejects duplicate loop-row decoding as the dominant
writer cost; it does not reject request-local cut reuse as a harmless small
reduction. Do not claim the writer budget is solved.

The profile instead keeps attention on retained accounting validation and
canonicalization: `_validate_eligible_accounting_candidates` still accounts
for 3.667 seconds, selected native accounting decoding for 3.340 seconds across
three calls, and `RecoveryDTO.canonical_bytes()` for 3.433 seconds across 2,183
calls. These are overlapping cumulative costs, not additive intervals. The
current evidence does not say which canonicalizations are repeated for the same
semantic object, or which retained source objects are safe to share. The next
discriminator is source-level call-path analysis of these exact profile rows,
then a narrowly instrumented required run only if the candidate operation is
still ambiguous. Any reduction must preserve each eligible candidate's
accounting, source, trust, and physical membership validation. No further
multi-minute run is justified until that analysis identifies a concrete
repeated computation and a differential rejection case.

#### Source trace and bounded same-scope reduction (2026-10-14)

Source tracing confirms that `validate_selected_next_turn()` walks every
selected accounting publication eligible through the requested sequence and
calls `validate_accounting_selected()` on each. It then returns the named,
independently validated evidence and compares its reconstructed physical pair,
source bytes, decision reference, and cut relationships. This is deliberately
candidate-complete validation; simply validating the named item would weaken
history eligibility checks. The source establishes the validation multiplicity,
but does not explain the 2,183 `RecoveryDTO.canonical_bytes()` calls: the
profile recorded neither receiver identity nor call sites, and the retained
graph includes many DTO types. Do not infer that all those calls target one
object or that candidate-level caching is safe.

One separate exact duplicate was found in
`_authenticated_scope_sources()`: for an immutable authenticated recipient DTO,
the same canonical bytes were generated four times and the same SHA-256 three
times to reconstruct the recipient child identity, head, fingerprint, and
physical child bytes. The helper first requires exactly four matching physical
trust children; after that gate, it now computes the bytes and digest once and
reuses those local values. The DTO is frozen and its fields are immutable. All
child membership, identity, head, fingerprint, selected-decision, and physical
record comparisons remain. This is same-call local reuse; it does not persist
across an authentication boundary.

The focused mounted-independent regression passed:

```sh
TMPDIR=/private/tmp UV_CACHE_DIR=/private/tmp/chiplog-uv-cache \
uv run pytest -q tests/composition/test_r14_native_recovery_cut.py::test_r17_scope_children_are_exactly_bound_to_authenticated_trust_carrier
```

This proves the scope binding still accepts its valid fixture, not that the
optimization materially changes the two-seal runtime. These bytes are produced
by a `DeliveryDTO` subtype, so this edit does not directly reduce the measured
`RecoveryDTO.canonical_bytes()` row. It is a bounded local cleanup, not the
writer-timeout fix. The next useful profile must record call site, receiver
type/object identity, input/output size, wall time, and thread CPU around
`RecoveryDTO.canonical_bytes()` and accounting validation, but only during the
next already-required mounted functional witness. Do not rerun the long failing
selector only to collect that trace; keep the five-second freshness constraints
and every candidate/source/physical check unchanged.

#### V3 retained-artifact reconstruction trace (2026-10-14)

The two-seal writer profile showed repeated work in a different DTO path from
the `RecoveryDTO.canonical_bytes()` row: the V3 selected-history decoder
validated one retained publication, built its physical command (validating it
again), then built its envelope through the public command builder (validating
it a third time). The historical selected-publication reader had the same
validate / command / envelope reconstruction chain. Source inspection confirms
these calls use the same immutable retained evidence within one decode; the
reconstructed record, command, envelope, source/head comparisons, and public
builder validation boundaries remain necessary.

The bounded change introduces one private helper that returns the validated
record, command, and envelope from one validation. Internal decoder sites use
that tuple once. The public command and envelope builders still validate
independently when called on their own. This removes two repeated retained
publication validations per internal decode without caching across calls or
changing any source, physical, ordering, or deadline checks. The focused
`test_r14_native_sealed_accounting_records.py` suite passes (6 tests), including
equivalence of the bundled outputs with both public builders.

Two required mounted two-seal attempts after earlier bounded reductions remain
incomplete, with different observed refusal points. One ended after 299.12 s at
writer source freshness (`source_0..source_10`); a later attempt ended after
468.25 s at `loop_rpc` admission expiry. Neither reached the second terminal,
V3 publication, cross-order replay, or reopen. Their profiles reported
`read_execution_call_history` at 10.394 s and 20.476 s wall respectively, with
thread CPU nearly equal to wall, and 2,130 `json.loads` calls in each. The
variation and different failure points mean these attempts do not establish a
performance trend or prove this V3 reduction fixes the admission deadline.
They do establish that the required witness is still blocked before its
acceptance criterion.

Do not run another multi-minute test as a standalone diagnostic. Keep this
local reduction and its fast regression. Measure its end-to-end effect only in
the next required mounted R14.6 functional witness, which must complete the
second terminal, V3 publication, cross-order replay, and reopen. If that witness
still expires, record the exact winning admission-deadline operand and its
start/end timestamps; do not enlarge any freshness or lease horizon. Until the
full witness passes, R14.6 remains open.

#### Latest two-seal differential run (2026-10-14)

After the retained V3 artifact reconstruction change, the required two-seal
selector ran for 607.56 seconds and failed at the second `accept_call` with
`STALE:admission-expired`. It again did not reach second terminal publication,
second-seal V3 publication, cross-order replay, or reopen. This is a functional
failure, not a passing run. The mounted setup trace measured second-Turn capture
at 225.282 s and second-Turn sealing at 59.891 s. Preview took 38.270 s wall /
38.131 s calling-thread CPU; acceptance took 43.240 s wall / 20.607 s
calling-thread CPU.

The writer guard's fresh `read_execution_call_history` took 21.273 s wall /
21.157 s calling-thread CPU. Its cProfile sample attributed overlapping
cumulative costs of 14.365 s to two `validate_selected` calls, 12.602 s to two
`authenticate_native_recovery_selected` calls, 8.886 s to five
`read_historical` calls, 8.635 s to `validate_selected_next_turn`, 7.238 s to
three selected native-accounting decodes, 6.824 s to 2,127 JSON raw decodes,
and 6.170 s to 21 `_pending` calls. These sums overlap; they are not additive.
This supports repeated history/source validation and JSON/canonicalization as
the expensive local path. One sample cannot tell which same objects are
revisited or which repeated checks are removable.

The initial reading of the refusal trace incorrectly treated its earliest
saved deadline as the cause. The traceback and exact exception show the actual
path: `_check_live` first spent 21.273 s re-reading history, then
`capture_dispatch()` called `require_invocation()`, which rejected with
`STALE:admission-expired` before `_check_live` reached its final minimum-deadline
comparison. `prepare_call_exchange()` sets
`observed.admission_valid_until_ns` to the preview mandate expiry, and the trace
recorded `mandate_remaining_at_return_ns = -2.409 s`. Mandate expiry therefore
caused this invocation's refusal. The 21.618 s `_check_live` span includes both
the 21.273 s history read and a 0.327 s dispatch capture; the exception was
raised during that capture.

The diagnostic wrapper also calculated a saved candidate minimum and reported
`loop_rpc` with -17.872 s slack at the point of exception. That minimum was not
the throwing predicate: it is evaluated only after `capture_dispatch()` returns.
It may become the next constraint after mandate expiry is avoided, but this run
does not prove that. Keep it as a later candidate, not the current cause. The
effects lease and mandate were not both still valid: the mandate had expired.
No evidence here justifies changing either horizon.

Differential hypotheses, ordered by evidence:

1. **Repeated local history validation consumes the mandate horizon.** The
   writer history read took 21.273 s and was almost entirely calling-thread CPU;
   `require_invocation()` failed immediately after it because the mandate
   expiry had passed by 2.409 s. This is the strongest causal explanation for
   this invocation. Keep mandate and source horizons unchanged; identify
   repeated work within the authenticated current read.
2. **The saved `loop_rpc` deadline may be the next constraint.** The diagnostic
   wrapper calculated it as earliest at the exception timestamp, but the
   throwing nested check was mandate expiry and the final `_check_live`
   comparison had not run. Whether `loop_rpc` would refuse a faster check is
   unresolved. Preserve the existing bound for now and do not claim it caused
   this failure.
3. **Second-Turn capture is dominated by full history rereads.** The trace now
   measures 225.282 s, while source analysis already identified 15 full-history
   read callsites in its capture transitions. This remains a strong, separate
   cost. Exact attribution would need timings on the existing boundaries during
   a required functional witness; it does not explain which deadline wins at
   the later writer check.
4. **GIL contention, socket delay, or exponential growth.** The profiled writer
   read has wall and calling-thread CPU within 0.116 s. That weighs against a
   socket wait in this interval, but does not measure competing-thread lock
   wait. No controlled cardinality series exists, so neither GIL contention nor
   an exponential law is established.

Next action: continue source-level differential analysis of the 21.273 s
current-history read and remove only exact same-cut repeated work that preserves
current-source authentication. A later required functional witness must log
the throwing callsite separately from the post-capture saved-deadline minimum;
do not infer a winning bound from candidate values after an earlier nested
exception. Do not run a diagnostic-only multi-minute test or enlarge mandate,
source, RPC, or lease horizons.

#### Request-local loop-cut check reuse (2026-10-14)

The source trace then exposed an exact repeated read in
`authenticate_native_recovery_selected()`: with an authenticated loop-decision
cut, the function compared the complete current loop journal to that same cut
before selecting the entry, then repeated the comparison after opening the
SQLite read connection, before deriving historical publications. The function
requires the authority gate to be held. From the first comparison through the
second it executes synchronously, with no `await`, gate release, callback, or
write. The journal's canonical append/read path uses this same gate. A cold
review confirmed the second comparison is redundant for admitted mutations
under that gate; it also noted that direct external file mutation is outside
what either check can reliably serialize.

The second comparison was removed; exact full-journal equality is still checked
once per selected authentication, before selected entries and SQL history are
used. The helper-level regression still verifies rejection of foreign cuts,
unsealed inputs, changed marker bytes, truncation, and restored original bytes.
Fast verification passed (8 focused tests; Ruff and `git diff --check` clean).
The mounted positive recovery/reopen test also passed:
`test_selected_native_cut_reopens_and_legacy_prepare_holds[True]` (1 passed in
260.20 s). This confirms the selected recovery path still accepts the valid
historical cut after the repeated check is removed. It is not a full R14.6
two-seal witness and does not prove the reduction is large enough to meet the
current writer deadline.

This is a small, source-proven reduction in the 21.273 s writer-history path,
not a complete explanation for the remaining gap: the latest failure happened
after mandate expiry during nested dispatch capture. `loop_rpc` was only the
earliest saved candidate at the later exception timestamp; `_check_live` had
not reached that final predicate. A further long run is not justified by this
single small reduction. Continue source-level
analysis of the measured validation multiplicities and preserve every exact
owner, source, inventory, and physical check. Any next mounted run must be the
required full R14.6 witness after a material, independently reviewed reduction,
not a performance-only probe.

#### Candidate rejection: readiness versus next-Turn validation

The profile's two `validate_selected` calls suggested that a readiness record
might be validated once by the general reader and again as an eligible
candidate for a selected next Turn. Source inspection confirms the call paths
can reach the same selected readiness decision. However, the checks have
different scopes: the general reader authenticates the full current native
recovery cut; the next-Turn candidate walk authenticates each eligible
readiness decision against its historical prefix through the target sequence.
The latter also deliberately validates unnamed eligible candidates and keeps
going after the named candidate. Skipping the general validation would remove
the current-cut check, while skipping the candidate check would weaken the
candidate-complete guarantee. The apparent duplicate is therefore not safe to
remove as-is. A shared pure parse/command result could be considered only if
both current and historical authority checks still run; the present profile
does not isolate enough exclusive cost to justify that refactor.

#### Source-level differential audit of the writer read (2026-10-14)

Three tempting reductions do not explain the latest 21.273-second writer read:

1. **H1 issuance scans:** the earlier source finding of repeated complete scans
   no longer describes the current implementation. It opens one issuance
   journal, loads the selected issuance once, and reuses the verified projection
   for the later accounting check. The older mounted profile also measured
   issuance scanning as a small fraction of its much shorter read. Do not
   reintroduce an H1 scan optimization from that stale call graph.
2. **Execution inventory reconstruction:** the repeated calls inside
   `_read_call_history` use different prefix heads or different accumulated
   arrays. For example, an acceptance validates the inventory before adding
   itself, while later records validate the inventory after that acceptance.
   Equal Run identities do not make these inventory snapshots interchangeable.
3. **Readiness versus Next-Turn validation:** the general reader checks the
   current selected recovery cut; Next-Turn separately validates every
   eligible readiness candidate against its historical prefix. Keep both.

The latest profile's 21 `_pending()` calls parsed the same three large selected
decision payloads repeatedly. This confirms repeated CPU work on equal bytes,
but not that each caller has the same return-value need or authenticated cut.
Calls arrive through distinct semantic validators; each obtains fresh journal
entries and checks pending disposition plus durable H1 V2 evidence. The held
authority gate serializes admitted local writers during this synchronous
reader, but this alone does not prove that sharing a parsed result preserves
every caller's read contract. Do not add a general cache or skip those checks
without a caller-by-input profile tied to exact cut identity.

The two profiled `validate_selected` calls were then compared at their source
boundaries. The ordinary history pass authenticates the selected recovery
publication against the complete current cut. The nested Next-Turn pass
authenticates every eligible accounting candidate against a historical prefix
ending at `through_sequence`. The same decision may be visited, but these are
not interchangeable validations: removing either would drop a distinct
current-cut or historical-candidate guarantee. The authenticated loop cut is
now passed through to both, but remains independently checked for each
historical selection. Keep both validations.

One smaller exact-byte repeat is visible in the historical accounting path:
`decode_selected_native_accounting()` parses
`evidence.selected_source_bytes` as `R14NativeOneCallCancelledFacts`; after it
returns, `authenticate_native_recovery_selected()` parses those same bytes
again to obtain the native scope binding and record references. Both parses
occur synchronously under the held authority gate and concern the same retained
evidence object. This establishes duplicated decoding, not its exclusive cost
or whether avoiding it is large enough to change the admission outcome. Keep
it as the next source-grounded candidate only if a local return-value plumbing
change preserves both decoder canonicality checks and all subsequent native
scope, physical membership, and historical-cut checks. Do not infer that this
alone closes the measured ~15-second overrun.

One narrower source candidate remains: within one selected retained execution
transition, canonical serialization and digest computation occur at several
validation sites (`_read_call_history` and `transition_command`). The short
mounted profile measured many `canonical_bytes` and `transition_command`
calls, but it did not attribute exclusive time or byte volume to this DTO in
the failing two-seal writer read. Therefore this is not yet a material
optimization target. If a required functional witness is run, record per
callsite DTO family, payload length, invocation count and exclusive CPU while
keeping model validation, historical/native-scope checks, physical membership,
fresh dispatch capture and every deadline unchanged.

**Current decision:** do not change authority horizons, remove a semantic
validation, add a cross-boundary parsed-object cache, or rerun a diagnostic-only
multi-minute scenario. The latest concrete refusal remains mandate expiry
inside nested dispatch capture after the expensive history read. The
source-level candidates that could be removed without changing scope have not
yet been shown to save enough time. Keep R14.6 open; use the per-callsite
observation only on the next required two-seal functional witness, after any
independently reviewed material source reduction.

#### Owner-journal lookup differential (2026-10-14)

One structural read-amplification candidate was found in the current V3 path:
for each parsed owner decision, it called the full owner journal's `entries()`
and linearly searched for that decision's raw bytes. For N decisions this
repeats full journal enumeration N times. This is a concrete quadratic lookup
shape, but the preserved failed-run profile did not attribute time to this
loop, and the large owner-journal backup inspected nearby is not proven to be
the exact mounted input. It is therefore a source-level candidate, not an
established cause of the 21.273-second interval.

A narrow implementation change now captures and strictly authenticates one
owner-journal tuple, builds a first-match `decision_id → raw bytes` index, and
uses the authenticated decisions for the join. The V3 early-return branches
still perform a fresh owner-journal capture and snapshot comparison before
success; the later legacy-path capture and checks remain in place. Thus the
change removes per-decision rescans while retaining currentness checks. It
does not introduce a cache across calls or change any deadline.

Ruff passed. The focused test file reported 24 passed and two mounted tests
failed before entering the target code because this sandbox denied AF_UNIX
`bind` (`PermissionError: Operation not permitted`). This is an environment
block, not a functional verdict. No before/after timing exists yet, so do not
claim that this change materially reduces the writer read or resolves mandate
expiry. The next valid measurement belongs inside the required R14.6 witness;
do not run another multi-minute diagnostic-only scenario.

#### Current-tree required two-seal witness after owner lookup reduction (2026-10-14)

Command:
`./.venv/bin/python -m pytest -xq tests/composition/test_r14_native_selected_accounting_source.py::test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`

It ran in an AF_UNIX-capable local environment, reached the second call's real
writer admission, then failed: **1 failed in 594.53 s**. The refusal was
`writer STALE: denying invocation is not currently authenticated
(STALE:admission-expired)`. It did not reach the second terminal, the second
V3 publication, reverse-order replay, or final reopen assertions. R14.6 remains
open. Setup and reopen both use `native-v23-prepare.sqlite`; the earlier
path-mismatch hypothesis is not present in this tree.

Measured boundaries (wall time unless otherwise stated):

| Boundary | Duration |
|---|---:|
| Preview call | 37.88 s wall / 37.66 s calling-thread CPU |
| History reads inside preview | 18.53 s and 18.40 s, nested in preview |
| Acceptance preparation | 19.21 s wall / 19.07 s calling-thread CPU |
| History read inside preparation | 18.52 s, nested above |
| Writer freshness guard | 20.98 s wall / 20.86 s calling-thread CPU |
| History read inside writer guard | 20.64 s wall / 20.53 s calling-thread CPU |
| Fresh dispatch capture after that read | 0.316 s wall / 0.316 s calling-thread CPU |

Nested intervals overlap; they must not be summed. At writer refusal, the
mandate had 0.186 s expired; the saved `loop_rpc` deadline was 17.284 s expired.
The detailed `_check_live` frame was unavailable in the exception trace, so
the trace identifies the admission-expired refusal and competing deadline
values, not a uniquely proven winning predicate.

The writer-read profile shows the next areas to inspect: two
`validate_selected` calls took 13.97 s cumulative; two
`authenticate_native_recovery_selected` calls took 12.33 s cumulative;
`validate_selected_next_turn` took 8.60 s; selected-accounting decoding took
6.86 s; JSON `raw_decode` took 6.62 s; and 21 `_pending()` calls took 6.18 s.
These are overlapping cumulative times. Dispatch capture is not the dominant
part of this refusal. The trace does not prove which selected-accounting bytes,
prefixes or journal entries overlap between candidate validations.

One exact context-loss path was confirmed by source inspection: outer
`_read_call_history` builds and passes one authenticated loop-decision cut into
`validate_selected_next_turn`, but `_validate_eligible_accounting_candidates`
did not forward it to `validate_accounting_selected`. Each nested historical
validation therefore rebuilt and parsed loop entries before selecting its
`through_sequence` prefix. A narrow change now forwards that exact cut. It
retains per-candidate historical validation, the same `through_sequence`, the
current-journal comparison and the full unnamed-eligible-candidate walk.
Three focused tests passed (cut propagation, unnamed bad candidate, and
changed-current-loop-cut rejection); Ruff passed. This proves helper-level
behavior only. No timing or mounted functional result exists for this change;
the next required witness must show whether it shortens writer admission. Do
not infer a pass from those unit tests or alter any horizon.

The required witness was rerun once after that propagation change, using the
same selector. It again failed at writer admission: **1 failed in 575.58 s**;
the second terminal, V3 publication, replay and reopen remained unreached. Its
writer history interval was 19.72 s wall / 19.68 s calling-thread CPU, the
writer guard 20.18 s wall / 20.13 s CPU, and dispatch capture 0.435 s. The two
`authenticate_native_recovery_selected` calls were 11.56 s cumulative;
`validate_selected_next_turn` 7.97 s; candidate validation 6.68 s; and selected
accounting decode 6.92 s. These remain overlapping profile figures. Relative
to the preceding selector run, nested candidate/history values were somewhat
lower, but independently generated fixtures and runtime variance prevent
claiming that the loop-cut change caused the full difference. The specific
confirmed loss of authenticated parsing context is fixed; the unchanged
admission failure shows it is insufficient by itself.

#### Owner-prefix scan candidate rejected on the mounted input (2026-10-14)

The historical `through_sequence` path appeared to rebuild a growing owner
journal prefix for each row. The cold-reviewed linear-boundary rewrite kept
all selected-prefix checks but did not change the required two-seal witness:
**1 failed in 581.69 s**, still at the second call's writer admission. The
current failed fixture contains 6 owner-journal rows totaling 237,960 bytes
(`wc -l -c`). Even if the old loop reached the last row, it would decode only
the six-entry prefixes over this small image. This cannot plausibly account
for the approximately 20 s writer-history interval in this witness. Revert the
rewrite and do not count it as a performance fix for the current failure.

The same writer-read cProfile gives stronger current leads: five
`NativeOneCallScopeService.read_historical()` calls took 8.76 s cumulative;
ten `read_historical_admitted_inbox()` calls took 6.74 s; 21 `_pending()` calls
took 6.13 s; and 2,038 JSON `loads()` calls took 6.96 s. These figures overlap.
The read itself took 20.004 s, almost entirely calling-thread CPU. This profile
does not show which scope anchors, inbox records, or selected raw bytes repeat
across the calls, so it does not yet justify sharing their results.

Next differential step: identify the exact repeated identities and source cuts
for the five historical-scope and ten admitted-inbox reads in this writer
history. Keep each currentness and historical-prefix validation. Do not change
deadlines, add a general cache, or run another full witness until source
inspection finds a same-call repeat whose removal is material for this
six-row fixture.

#### Same-call historical inbox repeat confirmed (2026-10-14)

Source inspection resolved the 5-to-10 call-count question. Each
`NativeOneCallScopeService.read_historical()` invokes
`CommonCliExecutionRuntime._validate_historical()`, which reads the historical
admitted inbox, passes that observation into `_selected_input()`, and compares
the decoded input with the retained request. The service then read the same
inbox again to compare its canonical record bytes and physical record head
with the selected scope. Both reads use the same command ID while the same
runtime authority gate is held; no publication occurs between them. The later
comparison adds no second freshness boundary, so retaining the already
authenticated observation preserves both sets of predicates.

The narrow change returns that `AdmittedInboxObservation` from
`_validate_historical()` and reuses it for the canonical-bytes and physical-head
comparisons. It adds no cache and leaves all comparisons and rejection paths
in place. Ruff passed, and the two mounted historical-scope tests passed. The
broader focused command passed 7 tests and had 3 failures elsewhere: one
concurrent selected-ingress race and two strict-xfail terminal tests returning
`EXECUTION_DRIVER_REJECTED_V1`. Those failure traces do not point to the changed
reuse assertion; record them separately rather than attributing them to this
change or treating them as evidence for it.

This confirms one redundant authenticated source read per historical-scope
call. The profile reports 6.74 s over ten inbox reads and 8.76 s over five
whole scope reads, but inclusive timings overlap; it cannot be used to claim a
specific saving for the five eliminated reads. The writer history has not yet
been rerun, so admission impact and the required two-seal outcome are unknown.
Next: use the next required mounted witness with the current source and
low-volume counters to verify the inbox count falls from two to one per scope
call, then check whether the writer history interval changes. Preserve every
deadline and do not launch a separate long witness solely for this candidate.

The two `authenticate_native_recovery_selected()` invocations are not a reuse
candidate by function-name/count alone, but source inspection does not yet
prove their actual work differs. One route is
`validate_selected(..., through_sequence=None)` through
`authenticate_selected_held()`; the other is the per-candidate route with a
`through_sequence` value. Both reconstruct the retained original C at
`cut.tenant_commit_sequence` and verify its publication delta. For the
direct-terminal and accepted-open V3 branches, the helper returns before its
later sequence-bounded owner-prefix branch, so the argument difference alone
does not establish a different reconstruction. Conversely, the current profile
does not establish identical evidence or inputs. Keep both until attribution
records caller identity, retained recovery operation ID, selected branch,
`through_sequence`, cut tenant/head/commitment and visited row counts. This
will distinguish duplicate work from separate selected evidence without
removing either validation speculatively.

#### Live cross-seal native sample (2026-10-15)

During the already-required three-member/cross-seal invocation, the first selector
had passed and the second was still running. `sample 2601 2 10 -file
/private/tmp/chiplog-r14-cross-seal-live-sample.txt` sampled the existing process
without stopping it or altering source. The report lists 4.4G physical footprint
and 6.0G peak footprint, and captures Python execution and native Pydantic frames.
These are one-process observations, not a memory-growth curve or a leak diagnosis.
The native sample does not expose the needed Python caller identities and cannot
prove repeated authentication of the same source. It therefore justifies no
validation removal or caching. The test continues with unchanged source; no target
timeout was introduced. A Python-level attribution profile remains necessary
before choosing any further reuse boundary.

### Native SUSP phase attribution, 2026-10-15

Original-code sys/threading observer (no callable/dispatch replacement):
`/private/tmp/chiplog-r14-suspension-phase-observer-run.log`, 204.23s STALE;
trace `chiplog-r14-suspension-phase-observer-trace.json` has no observer errors
or incomplete synchronous frames. Source/profile manifest unchanged. Issued
actor budget remains 30 seconds. Instrumented `_command` ~1.67s, guard-held cut
collection ~41.50s, same cut sequence 19/commitment/run as initial acquisition.
No production-latency estimate is inferred from profiled durations.

After preserving the whole closed fixture, one original held collection after
reopen (without actor issuance/publication/SEND) completed with the same cut.
`chiplog-r14-suspension-collect.prof`: 37.212s instrumented; source manifest
unchanged. Obtain attribution with `pstats.Stats(path).sort_stats('cumulative')`.
The collector traverses `_read_call_history` 10 times (~25.55s cumulative),
including `_selected_accepted_recovery_inputs` → public OPEN frontier reader
(~20.33s), which independently reacquires source facts already present in the
fresh subset. This identifies a candidate repeated acquisition, not permission
to reuse across owner IPC or skip currentness/trust/physical validation.
Next: inspect whether the continuously held subset already carries every fact
required by that frontier's retained source and resolved-outcome checks.

### Held-history reuse experiment, 2026-10-15

Separate tree `/private/tmp/chiplog-r14-suspension-history-reuse`: HEAD `5d16f4c`
plus five inherited source WIP files (manifest
`chiplog-r14-suspension-history-inherited.json`) and the narrow three-module
candidate. A fresh unresolved subset's exact authenticated history is forwarded
through OPEN frontier validation to `_current_cut` and its historical builder,
under the continuously held gate. Only two duplicate outer-history acquisitions
are removed. Original Prepare/acceptance and source-sequence/locator selection,
marker/envelope checks, cut equality, suspension preservation and pre/post
currentness remain. No source facts are borrowed across owner IPC; each native
cut acquisition rebuilds its subset. Closed/default paths retain their reads.

Five focused boundary cases first reached RED (unexpected history argument),
then passed: identical history object forwarded while original Prepare/outcome
selection still runs; changed commitment/identity/tenant rejected before Run join.
Three-file focused selection returned 170 passed in 1.48s. Ruff passed; cold
actual review found no critical/blocking defect. This establishes plumbing and
currentness boundaries, not a production timing result.

Fresh uninstrumented native-clock selector is running via
`chiplog-r14-history-reuse-native-runner.py`; log
`chiplog-r14-history-reuse-native.log`, manifest
`chiplog-r14-history-reuse-native-profile.json`. The same interpreter checks
builtin clocks at test entry/teardown and source hashes before/after. This
candidate is not integrated. An independent diagnostic-clock cross-seal
continuation also runs on MAIN; do not interpret elapsed times as isolated-load
benchmarks. A renewed STALE requires fresh attribution, not a budget increase.

Result: `chiplog-r14-history-reuse-native.log` returned **1 passed in 209.62s**,
execution session 12777 exit 0. The same interpreter's builtin-clock checks
passed before pytest, at test entry and teardown. The before/after source
manifest is identical (1207 tracked source/test/config files; obtain count with
`len(json.load(open(profile))['after']['files'])`), and its pytest exit is 0.
The four candidate source/test files additionally have retained SHA256 values in
`chiplog-r14-history-reuse-candidate-hashes.json`. This is the isolated WIP source
profile at HEAD `5d16f4c`, not clean HEAD or an elapsed-time speedup claim.

The fresh before-COMMIT SUSP case passed its actual pending outer read, reopen,
original-obligation/pair/baseline preservation and exact replay/one-transfer
assertions with ordinary clocks and unchanged admission budget. A passing first
slice does not close the production-clock multi-seal route or the full pending
negative inventory. MAIN integration remains pending while its frozen cross-seal
continuation runs; the successful isolated files stay unchanged.
