# Mechanism Audit

Load this file when the target claims or strongly implies that a process, schema, model, protocol, or design guarantees something important.

## Purpose

Test whether the target's stated mechanism actually delivers the claimed or strongly implied guarantee.

## Claim-Boundary Attack Inventory

Before reaching a verdict, name the exact claim boundary and close the applicable attack
inventory below. For each family, provide either:

- the observable failure and a concrete verifier that discriminates it, or
- a target-specific reason the family is not applicable.

The minimum families are:

1. omission or incomplete enumeration;
2. addition, unknown member, or extension;
3. duplication, reordering, or conflicting identity;
4. substitution, aliasing, or cross-owner input;
5. stale state, race, or publication collision;
6. implicit default, fallback, or permissive error path;
7. lifecycle composition: startup, runtime call path, restart, and teardown.

Do not accept a helper-level test as evidence for a runtime guarantee until the actual call
path is attacked. When mutable registries, configuration, callbacks, or dependency injection
participate, test substitution after initialization as well as direct validator invocation.

The inventory is a reasoning constraint, not a table-format requirement. A ritual list with
no observable, verifier, or substantive non-applicability reason does not close the audit.

## Required Questions

1. What does the target explicitly promise?
2. What does the mechanism actually guarantee?
3. Where does the stronger reading fail?
4. What is the minimal fix set?

## Minimal Fix Set

- `P0`: required fixes without which the guarantee claim should not stand.
- `P1`: strengthening fixes that improve robustness but are not strictly required for the weaker claim.

## Quality Bar

- Separate promise from guarantee.
- Point to the exact missing constraints, steps, fields, assumptions, or enforcement layer.
- Keep the audit domain-general; avoid domain-specific ritual unless the target itself requires it.
