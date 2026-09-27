# H1 V2 preseal P/E anchor for recovery

Status: CONTRACT PROPOSED; implementation and mounted witnesses open. This
refines [post-seal recovery](R14-R17-H1-POSTSEAL-RECOVERY.md) at the crash cut
where the V2 seal is durable but no B preparation or E evidence was issued.

## Required boundary

The original driver initialization, V3 Prepare, prepared Run, physical V2 seal,
and seal-bounded lineage are already selected from authenticated native history.
They do not retain P's accepted scope, policy, custody and source signature or
E's member provenance, disclosure, narrowing and worker route identity. Those
facts are currently held by live, process-local capabilities. The post-seal E
selection closure requires a completed request and cannot be made before the
seal. A recovery input cannot be reconstructed by accepting a saved DTO, using
the new process's worker session, or substituting that later closure.

Before the V2 seal decision is appended, installed P/E owners authenticate and
capture the missing facts while their sources are live. Capture is seal-agnostic
and identity-owned. The seal writer binds that capture to its independently
computed command, selected seal and owner-as-of snapshot under the shared
authority gate. The canonical, versioned anchor is retained as a sibling of
the V2 decision's native fields in the **same authenticated DECIDED entry**.
The anchor is evidence of historical facts, never a fresh execution, terminal
or publication capability. No new physical seal member or public wire
operation is authorized by this contract.

The journal authenticates the complete decision. The physical seal fingerprint
does not authenticate the sibling anchor. DECIDED can precede SQLite
materialization: pending recovery must materialize the original retained
command and anchor, and may not recapture or replace P/E facts. The historical
reader refuses unresolved PENDING decisions and requires exact materialization.

## Capture and retained fields

An installed private owner exposes `capture(preflight)` followed by a
synchronous, no-IPC `recheck_and_bind(capture, preflight, command,
retained_v2, owner_asof)` at decision admission. Capture performs any owner
IPC before the SQLite transaction, checks the source cut before and after, and
cannot be copied, deserialized or supplied by a caller. Recheck consumes the
receipt once and joins the exact enrolled runtime, database, Prepare, Run,
owner-as-of and publication command. A failed or stale capture denies the seal;
it does not create a partial anchor.

The no-IPC recheck is valid only if P's authoritative scope, policy, custody,
source-version and supervisor-session mutations share the installed runtime's
gate through decision append. The installed CURRENT owner is a stateless
projection of the supplied authenticated snapshot (`selector_generation=0` is
not a pin); the enrolled trust, resource and supervisor mutation APIs use the
common gate. Custody's gate binding drains mutations admitted before bind, and
its close does not release the lifetime pair lock while an installed runtime
remains bound. Focused lifecycle tests cover these obligations. The anchor
issuer remains ineligible until the decision owner and writer replay are
mounted. Final P replay must recompute CURRENT and every source join under one
uninterrupted gate hold in the **DECIDED writer**, then
append the decision before releasing it. This assertion applies only to those
installed, enrolled mutation APIs; an unbound writer cannot use this issuer.
An unavailable/restarted session or changed source denies the seal.

The earlier publication admission guard and the DECIDED writer are separate
gate holds. A check in the guard alone cannot establish continuity. Native,
P and E replay must precede consumption of the one-use H1 preflight in the
writer's gate hold; after consumption, `_issued` can no longer authenticate
the preseal native cut. No `await` or gate release may occur between that
ordered recheck and `_append_decision`.

The anchor records only facts not independently recoverable from the native
prefix: P accepted scope and policy references plus canonical bytes, recipient,
custody generation and digest, source-signature digest, and the accepted
owner-exchange commitment; E's ordered complete member occurrence vector with
provenance, disclosure and narrowing; and E worker runtime instance, route
generation and original session. It binds the selected native identifiers,
principal, tenant, database genesis, command and physical seal locator without
duplicating native payload bytes. Every residual field that cannot be derived
from the bounded native cut must be explicit and canonical. An omitted field
is a contract error, not permission to take its current value.

Existing E exact-head IDs include the E journal instance. A compact anchor
with no E journal entry cannot claim those IDs. Historical P/E projections
use a new versioned anchor-derived identity domain: hash the canonical tuple
of domain, deployment, database, genesis, tenant, selected decision, role and
member ordinal (when applicable). No nonexistent E journal locator or old
exact-head ID may be inferred. A decoded anchor by itself grants no old or
current E capability.

The selected decision ID is supplied by the installed historical reader after
it authenticates the containing journal entry. It cannot be embedded in the
anchor: that ID hashes the entire DECIDED entry, including the anchor. Worker
route generation remains the exact string issued by the original broker
session. Disclosure uses the actual `UNRESTRICTED`, `DENY_ALL` and
`ENDPOINT_RESTRICTED` vocabulary; endpoint narrowing may be stricter than the
original label and must be checked as a valid narrowing, not forced equal.

## Historical read and use

An installed historical reader finds the anchor only through the independently
selected V2 decision. It checks canonical bytes, journal authentication,
enrollment, binding, full native cut, member order and endpoint rules, P
scope/policy/custody joins, and the worker's original route identity. It
reconstructs the exact `DeliveryObservation` and `NonSchedulerFence` through
P/E-owned historical projections. Their old worker session need not be live;
the projections are evidence for a pure preparation request only. The B
coordinator then builds the complete canonical COMPLETION `STAGE_INPUT`, writes
and reads it back before owner IPC, and compares all fields again on restart.

Missing anchor on a legacy V2 seal means historical P/E is unsupported unless
another independent immutable source proves every missing field. Malformed or
inconsistent present anchor is an integrity failure. Later unrelated committed
decisions do not change the selected historical cut. Fresh broker admission,
terminal P currentness, one-use B authorization and selected publication remain
separate checks.

## Acceptance witnesses

Tests first establish that an installed V3 Prepare followed by a direct V2
seal currently leaves no E member/worker evidence. The implementation must
then prove: capture before seal; seal and anchor in one authenticated decision;
no anchor from wrong issuer or changed P/custody/worker state; no duplicate or
incomplete member vector; exact replay of the same decision; crash before
DECIDED, after DECIDED but before materialization, and after materialization;
restart with ROOT and `STAGE_INPUT` absent; later unrelated publication;
tampered anchor rejection; copied/cross-runtime capability rejection; and
complete canonical request equality across restart. Physical V2 seal members
remain exactly Run, seal and frontier.
