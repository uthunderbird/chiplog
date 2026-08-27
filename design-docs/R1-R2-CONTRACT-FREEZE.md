# R1/R2 parallel contract freeze

Status: frozen for the Stage-0 R1/R2 implementation lanes.

This file is the common predecessor for R1 and R2. It separates their ownership so
both lanes can be implemented and verified without importing uncommitted work from
the other branch.

## R1-owned contract

R1 owns the `chiplog.domain_primitives` package and its public value contracts for
tenant, principal, permission scope, record identity, stable namespaced record and
schema identities, producing schema/codec/canonicalization versions, fingerprints,
owner tags, and preservation of original/unknown bytes. The package contains no
domain policy, executable shared validators, domain enums, registries, factories,
hooks, or service lookup.

R1 owns its V1 golden-byte and mutation evidence and the V2 inert-shared-data
evidence for that package. Its lane verifier is a directly runnable test suite; it
does not activate or claim the integrated `stage0` profile.

The closed public export set for this slice is:

| Export | Frozen public shape |
| --- | --- |
| `TenantId` | frozen value object with `value: str` |
| `PrincipalId` | frozen value object with `value: str` |
| `PermissionScope` | frozen value object with `value: str` |
| `RecordId` | frozen value object with `tenant_id: TenantId`, `value: str` |
| `RecordTypeId` | frozen value object with `namespace: str`, `name: str` |
| `SchemaId` | frozen value object with `namespace: str`, `name: str`, `version: int` |
| `CodecVersion` | frozen value object with `value: int` |
| `CanonicalizationVersion` | frozen value object with `value: int` |
| `OwnerTag` | frozen value object with `value: str` |
| `Fingerprint` | frozen value object with `algorithm: str`, `digest: bytes` |
| `ProducingVersions` | frozen value object with `schema_id: SchemaId`, `codec: CodecVersion`, `canonicalization: CanonicalizationVersion` |
| `CanonicalBytes` | frozen value object with `payload: bytes`, `producing_versions: ProducingVersions` |
| `PreservedBytes` | frozen value object with `original: bytes`, `producing_versions: ProducingVersions` |

There are no other re-exports from `chiplog.domain_primitives`. Constructor
signatures are positional-or-keyword in the table's field order; annotations are
the exact built-in or exported names shown, with no defaults, variadics, aliases,
generic parameters, inherited members, or executable descriptors. The canonical
signature representation is UTF-8 JSON with sorted keys and compact separators:
`{"export":"<name>","fields":[["<field>","<annotation>"],...]}`. Its signature
digest is lowercase SHA-256 hex over those exact bytes. Both lanes derive these
digests independently from this table; neither copies generated output from the
other lane.

## R2-owned contract

R2 owns declarative boundary, export/signature/capability, record-owner, bridge,
provider, bootstrap, surface, and synchronous-routing manifest schemas and their
closed-world verifier. References to R1 are stable strings of the form
`chiplog.domain_primitives:<export>` plus a canonical signature digest; R2 does not
import R1 while generating or validating manifests.

Production universes that do not exist yet use explicitly named empty generations.
An empty generation is not evidence about a real graph. R2's negative fixtures must
independently contain at least two owners, a public and private export, a concrete
record owner, an executable root, a bridge/reverse edge, and a dynamic-resolution
escape so omission, addition, substitution, cycles, private leakage, generic append,
and bootstrap bypasses cannot pass vacuously.

R2 owns its V2 architecture and mutation evidence. Its lane verifier is a directly
runnable test suite; it does not activate or claim the integrated `stage0` profile.

## Integration-owned contract

The root integration owns shared verification registries and the profile runner.
After both lanes merge it runs a distinct R1/R2 convergence suite whose exact check
set is the union of R1 V1/V2, R2 V2, and an independently derived runtime-to-frozen
signature comparison. This convergence result does not activate or imply the
canonical `stage0` profile. That profile remains `HOLD` until the R0–R8 Stage-0
barrier in the roadmap is complete. Neither lane edits
`src/chiplog/verification/runner.py` or `registries.py`.

The canonical ignored inputs copied into each worktree are read-only snapshots of
`grill/project-architecture/document.md`, `grill/project-architecture/roadmap.md`,
and the authored transcript fixtures. Their SHA-256 digests are recorded in each
lane assignment; a mismatch stops the lane.
