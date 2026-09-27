"""Fail-closed canonical state for a future authenticated H1 recovery mount.

This module deliberately has no filesystem-opening API.  A path plus an
``AuthorityGate`` is not an authentication boundary: an untrusted caller could
provision a new journal and key.  The future mounted journal adapter must pass
an already authenticated prefix to this pure decoder and use its transition
records under one gate-held scan/CAS/readback operation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.composition.h1_launch_enrollment import EnrolledH1RecoveryMount

_SCHEMA = "chiplog.h1.postseal-recovery-record.v1"
_DOMAIN = b"chiplog.h1.postseal-recovery-root.v1\x00"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RUN_HEAD = re.compile(r"^loop:[0-9a-f]{64}$")
_STAGES = ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")
_MAX_SEMANTIC_BYTES = 256 * 1024


class H1PostSealRecoveryUnavailable(RuntimeError):
    """The authenticated runtime mount is not installed yet."""


class H1PostSealRecoveryRecordError(ValueError):
    """A recovery record/prefix cannot safely resume post-seal work."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_identity(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise H1PostSealRecoveryRecordError(f"{name} is invalid")
    return value


def _require_digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise H1PostSealRecoveryRecordError(f"{name} is invalid")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class H1PostSealRecoveryRootV1:
    """Every immutable value that identifies one recoverable sealed completion."""

    tenant_id: str
    database_id: str
    database_identity: tuple[str, int, int]
    journal_instance_id: str
    selected_seal_subject_id: str
    selected_seal_head: str
    selected_seal_fingerprint: str
    selected_decision_id: str
    selected_decision_digest: str
    selected_run_head: str
    original_command_id: str
    original_command_fingerprint: str
    source_commitment: str
    publication_command_id: str
    publication_command_fingerprint: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "database_id",
            "journal_instance_id",
            "selected_seal_subject_id",
            "selected_seal_head",
            "original_command_id",
            "publication_command_id",
        ):
            _require_identity(getattr(self, name), name)
        path, device, inode = self.database_identity
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or not isinstance(device, int)
            or isinstance(device, bool)
            or device < 0
            or not isinstance(inode, int)
            or isinstance(inode, bool)
            or inode < 0
        ):
            raise H1PostSealRecoveryRecordError("database identity is invalid")
        for name in (
            "selected_seal_fingerprint",
            "selected_decision_id",
            "selected_decision_digest",
            "original_command_fingerprint",
            "source_commitment",
            "publication_command_fingerprint",
        ):
            _require_digest(getattr(self, name), name)
        if not isinstance(self.selected_run_head, str) or _RUN_HEAD.fullmatch(
            self.selected_run_head
        ) is None:
            raise H1PostSealRecoveryRecordError("selected_run_head is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "database_id": self.database_id,
            "database_identity": list(self.database_identity),
            "journal_instance_id": self.journal_instance_id,
            "selected_seal_subject_id": self.selected_seal_subject_id,
            "selected_seal_head": self.selected_seal_head,
            "selected_seal_fingerprint": self.selected_seal_fingerprint,
            "selected_decision_id": self.selected_decision_id,
            "selected_decision_digest": self.selected_decision_digest,
            "selected_run_head": self.selected_run_head,
            "original_command_id": self.original_command_id,
            "original_command_fingerprint": self.original_command_fingerprint,
            "source_commitment": self.source_commitment,
            "publication_command_id": self.publication_command_id,
            "publication_command_fingerprint": self.publication_command_fingerprint,
        }

    def root_id(self) -> str:
        return _digest(_DOMAIN + _canonical(self.as_dict()))

    def model_copy(self, **changes: object) -> H1PostSealRecoveryRootV1:
        return replace(self, **cast(Any, changes))


@dataclass(frozen=True, slots=True)
class H1PostSealRecoveryRecordV1:
    """One canonical root, input pin, or result commitment record."""

    kind: Literal["ROOT", "STAGE_INPUT", "STAGE_RESULT"]
    root_id: str
    predecessor_entry_id: str | None
    root: H1PostSealRecoveryRootV1 | None = None
    stage: str | None = None
    stage_ordinal: int | None = None
    semantic_input: bytes | None = None
    semantic_input_digest: str | None = None
    effects_command_id: str | None = None
    result_bytes: bytes | None = None
    result_digest: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.root_id, "root_id")
        if self.predecessor_entry_id is not None:
            _require_identity(self.predecessor_entry_id, "predecessor entry")
        if self.kind == "ROOT":
            if (
                self.root is None
                or self.root.root_id() != self.root_id
                or self.predecessor_entry_id is not None
            ):
                raise H1PostSealRecoveryRecordError("root record differs from recovery root")
            if any(
                value is not None
                for value in (
                    self.stage,
                    self.stage_ordinal,
                    self.semantic_input,
                    self.semantic_input_digest,
                    self.effects_command_id,
                    self.result_bytes,
                    self.result_digest,
                )
            ):
                raise H1PostSealRecoveryRecordError("root record has successor fields")
            return
        if (
            self.root is not None
            or self.stage not in _STAGES
            or self.stage_ordinal != _STAGES.index(self.stage)
        ):
            raise H1PostSealRecoveryRecordError("stage record is invalid")
        if self.predecessor_entry_id is None:
            raise H1PostSealRecoveryRecordError("stage record lacks predecessor")
        if self.kind == "STAGE_INPUT":
            if (
                self.semantic_input is None
                or self.result_bytes is not None
                or self.result_digest is not None
            ):
                raise H1PostSealRecoveryRecordError("stage input record differs")
            _validate_bytes(self.semantic_input, self.semantic_input_digest, "semantic input")
            if self.stage == "EFFECTS":
                _require_identity(self.effects_command_id, "effects command ID")
            elif self.effects_command_id is not None:
                raise H1PostSealRecoveryRecordError("only Effects may pin a command ID")
            return
        if self.kind == "STAGE_RESULT":
            if (
                self.result_bytes is None
                or self.semantic_input is not None
                or self.semantic_input_digest is not None
                or self.effects_command_id is not None
            ):
                raise H1PostSealRecoveryRecordError("stage result record differs")
            _validate_bytes(self.result_bytes, self.result_digest, "result")

    def model_copy(self, **changes: object) -> H1PostSealRecoveryRecordV1:
        return replace(self, **cast(Any, changes))

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_id": _SCHEMA,
            "kind": self.kind,
            "root_id": self.root_id,
            "predecessor_entry_id": self.predecessor_entry_id,
        }
        if self.kind == "ROOT":
            assert self.root is not None
            result["root"] = self.root.as_dict()
        elif self.kind == "STAGE_INPUT":
            assert self.semantic_input is not None and self.semantic_input_digest is not None
            result.update(
                {
                    "stage": self.stage,
                    "stage_ordinal": self.stage_ordinal,
                    "semantic_input_base64": base64.b64encode(self.semantic_input).decode("ascii"),
                    "semantic_input_digest": self.semantic_input_digest,
                    "effects_command_id": self.effects_command_id,
                }
            )
        else:
            assert self.result_bytes is not None and self.result_digest is not None
            result.update(
                {
                    "stage": self.stage,
                    "stage_ordinal": self.stage_ordinal,
                    "result_base64": base64.b64encode(self.result_bytes).decode("ascii"),
                    "result_digest": self.result_digest,
                }
            )
        return result

    def canonical_bytes(self) -> bytes:
        return _canonical(self.as_dict())


def _validate_bytes(value: object, commitment: object, name: str) -> None:
    if not isinstance(value, bytes) or len(value) > _MAX_SEMANTIC_BYTES:
        raise H1PostSealRecoveryRecordError(f"{name} bytes are invalid")
    if _digest(value) != _require_digest(commitment, f"{name} digest"):
        raise H1PostSealRecoveryRecordError(f"{name} commitment differs")


def _decode_record(raw: bytes) -> H1PostSealRecoveryRecordV1:
    try:
        decoded = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(decoded, dict) or decoded.get("schema_id") != _SCHEMA:
            raise ValueError("schema differs")
        kind = decoded.get("kind")
        common = {"schema_id", "kind", "root_id", "predecessor_entry_id"}
        if kind == "ROOT":
            if set(decoded) != common | {"root"} or not isinstance(decoded["root"], dict):
                raise ValueError("root fields differ")
            root_data = dict(decoded["root"])
            identity = root_data.pop("database_identity", None)
            if not isinstance(identity, list) or len(identity) != 3:
                raise ValueError("database identity differs")
            root = H1PostSealRecoveryRootV1(database_identity=tuple(identity), **root_data)
            record = H1PostSealRecoveryRecordV1(
                "ROOT", decoded["root_id"], decoded["predecessor_entry_id"], root=root
            )
        elif kind == "STAGE_INPUT":
            required = common | {
                "stage",
                "stage_ordinal",
                "semantic_input_base64",
                "semantic_input_digest",
                "effects_command_id",
            }
            if set(decoded) != required:
                raise ValueError("input fields differ")
            semantic = _decode_base64(decoded["semantic_input_base64"])
            record = H1PostSealRecoveryRecordV1(
                "STAGE_INPUT",
                decoded["root_id"],
                decoded["predecessor_entry_id"],
                stage=decoded["stage"],
                stage_ordinal=decoded["stage_ordinal"],
                semantic_input=semantic,
                semantic_input_digest=decoded["semantic_input_digest"],
                effects_command_id=decoded["effects_command_id"],
            )
        elif kind == "STAGE_RESULT":
            required = common | {"stage", "stage_ordinal", "result_base64", "result_digest"}
            if set(decoded) != required:
                raise ValueError("result fields differ")
            result = _decode_base64(decoded["result_base64"])
            record = H1PostSealRecoveryRecordV1(
                "STAGE_RESULT",
                decoded["root_id"],
                decoded["predecessor_entry_id"],
                stage=decoded["stage"],
                stage_ordinal=decoded["stage_ordinal"],
                result_bytes=result,
                result_digest=decoded["result_digest"],
            )
        else:
            raise ValueError("record kind differs")
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeError) as error:
        raise H1PostSealRecoveryRecordError("recovery record is invalid") from error
    if record.canonical_bytes() != raw:
        raise H1PostSealRecoveryRecordError("recovery record is not canonical")
    return record


def _decode_base64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("base64 differs")
    decoded = base64.b64decode(value.encode("ascii"), validate=True)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("base64 is noncanonical")
    return decoded


@dataclass(frozen=True, slots=True)
class H1PostSealRecoveryState:
    root: H1PostSealRecoveryRootV1
    head: str | None
    inputs: tuple[tuple[str, bytes, str | None], ...] = ()
    results: tuple[tuple[str, bytes], ...] = ()

    @classmethod
    def empty(cls, root: H1PostSealRecoveryRootV1) -> H1PostSealRecoveryState:
        return cls(root, None)

    @property
    def next_stage(self) -> str:
        inputs = dict((stage, (value, command_id)) for stage, value, command_id in self.inputs)
        results = dict(self.results)
        for stage in _STAGES:
            if stage not in inputs or stage not in results:
                return stage
        return "COMPLETE"

    def stage_input(self, stage: str) -> tuple[bytes, str | None]:
        for found, value, command_id in self.inputs:
            if found == stage:
                return value, command_id
        raise H1PostSealRecoveryRecordError("stage input is absent")


@dataclass(frozen=True, slots=True)
class H1PostSealRecoveryScan:
    """Data-only authenticated prefix observation; it never recreates a B/P cut."""

    tip: str | None
    states_by_root: tuple[tuple[str, H1PostSealRecoveryState], ...]
    root_id_by_selected_seal: tuple[tuple[tuple[str, str, str, str, str], str], ...]

    def state_for_root(self, root_id: str) -> H1PostSealRecoveryState:
        for found, state in self.states_by_root:
            if found == root_id:
                return state
        raise H1PostSealRecoveryRecordError("recovery root is absent")


@dataclass(frozen=True, slots=True)
class H1PostSealRecoveryAppendReceipt:
    entry_id: str
    scan: H1PostSealRecoveryScan


def apply_authenticated_prefix(
    entries: tuple[tuple[str, str | None, bytes], ...], root: H1PostSealRecoveryRootV1
) -> H1PostSealRecoveryState:
    """Decode an already-authenticated journal prefix for exactly one recovery root."""
    state = H1PostSealRecoveryState.empty(root)
    expected_outer: str | None = None
    for entry_id, outer_predecessor, raw in entries:
        _require_identity(entry_id, "journal entry")
        if outer_predecessor != expected_outer:
            raise H1PostSealRecoveryRecordError("authenticated prefix has a gap")
        record = _decode_record(raw)
        if record.root_id != root.root_id():
            raise H1PostSealRecoveryRecordError("record recovery root differs")
        if record.kind == "ROOT":
            if state.head is not None:
                raise H1PostSealRecoveryRecordError("recovery root is repeated")
            if record.root != root:
                raise H1PostSealRecoveryRecordError("recovery root differs")
        else:
            if state.head is None or record.predecessor_entry_id != state.head:
                raise H1PostSealRecoveryRecordError("recovery successor predecessor differs")
            state = _apply_stage(state, record)
        state = replace(state, head=entry_id)
        expected_outer = entry_id
    return state


def _selected_seal_key(root: H1PostSealRecoveryRootV1) -> tuple[str, str, str, str, str]:
    return (
        root.tenant_id,
        root.database_id,
        root.selected_seal_subject_id,
        root.selected_seal_head,
        root.selected_seal_fingerprint,
    )


def scan_authenticated_prefix(
    entries: tuple[tuple[str, str | None, bytes], ...],
    *,
    tenant_id: str,
    journal_instance_id: str,
) -> H1PostSealRecoveryScan:
    """Strictly decode every global entry before exposing any selected root."""
    _require_identity(tenant_id, "enrolled tenant")
    _require_identity(journal_instance_id, "enrolled journal instance")
    expected_outer: str | None = None
    states: dict[str, H1PostSealRecoveryState] = {}
    seals: dict[tuple[str, str, str, str, str], str] = {}
    for entry_id, outer_predecessor, raw in entries:
        _require_identity(entry_id, "journal entry")
        if outer_predecessor != expected_outer:
            raise H1PostSealRecoveryRecordError("authenticated prefix has a gap")
        record = _decode_record(raw)
        if record.kind == "ROOT":
            assert record.root is not None
            root = record.root
            if root.tenant_id != tenant_id or root.journal_instance_id != journal_instance_id:
                raise H1PostSealRecoveryRecordError("recovery root differs from enrolled mount")
            seal_key = _selected_seal_key(root)
            if record.root_id in states:
                raise H1PostSealRecoveryRecordError("recovery root is repeated")
            if seal_key in seals:
                raise H1PostSealRecoveryRecordError("selected seal has competing recovery roots")
            states[record.root_id] = H1PostSealRecoveryState(root, entry_id)
            seals[seal_key] = record.root_id
        else:
            state = states.get(record.root_id)
            if state is None:
                raise H1PostSealRecoveryRecordError("recovery successor has an unknown root")
            if record.predecessor_entry_id != state.head:
                raise H1PostSealRecoveryRecordError("recovery successor predecessor differs")
            states[record.root_id] = replace(_apply_stage(state, record), head=entry_id)
        expected_outer = entry_id
    return H1PostSealRecoveryScan(
        expected_outer,
        tuple(sorted(states.items())),
        tuple(sorted(seals.items())),
    )


def _apply_stage(
    state: H1PostSealRecoveryState, record: H1PostSealRecoveryRecordV1
) -> H1PostSealRecoveryState:
    assert record.stage is not None
    if record.kind == "STAGE_INPUT":
        for stage, value, command_id in state.inputs:
            if stage == record.stage:
                if (value, command_id) != (record.semantic_input, record.effects_command_id):
                    raise H1PostSealRecoveryRecordError("rival stage input")
                raise H1PostSealRecoveryRecordError("duplicate stage input")
    else:
        for stage, value in state.results:
            if stage == record.stage:
                if value != record.result_bytes:
                    raise H1PostSealRecoveryRecordError("rival stage result")
                raise H1PostSealRecoveryRecordError("duplicate stage result")
    expected = state.next_stage
    if record.stage != expected:
        raise H1PostSealRecoveryRecordError("stage order differs")
    if record.kind == "STAGE_INPUT":
        return replace(
            state,
            inputs=(
                *state.inputs,
                (record.stage, record.semantic_input or b"", record.effects_command_id),
            ),
        )
    else:
        if not any(stage == record.stage for stage, _value, _command_id in state.inputs):
            raise H1PostSealRecoveryRecordError("result has no input pin")
        return replace(state, results=(*state.results, (record.stage, record.result_bytes or b"")))


class H1PostSealRecoveryTransition:
    """Pure next-record construction; the mount owns durable CAS and readback."""

    @staticmethod
    def begin(state: H1PostSealRecoveryState) -> H1PostSealRecoveryRecordV1:
        if state.head is not None:
            raise H1PostSealRecoveryRecordError("recovery root is already durable")
        return H1PostSealRecoveryRecordV1("ROOT", state.root.root_id(), None, root=state.root)

    @staticmethod
    def pin_input(
        state: H1PostSealRecoveryState,
        *,
        stage: str,
        semantic_input: bytes,
        effects_command_id: str | None = None,
    ) -> H1PostSealRecoveryRecordV1:
        if state.head is None:
            raise H1PostSealRecoveryRecordError("recovery root is not durable")
        if stage != state.next_stage:
            raise H1PostSealRecoveryRecordError("first stage or pending result differs")
        return H1PostSealRecoveryRecordV1(
            "STAGE_INPUT",
            state.root.root_id(),
            state.head,
            stage=stage,
            stage_ordinal=_STAGES.index(stage) if stage in _STAGES else None,
            semantic_input=semantic_input,
            semantic_input_digest=_digest(semantic_input),
            effects_command_id=effects_command_id,
        )

    @staticmethod
    def commit_result(
        state: H1PostSealRecoveryState, *, stage: str, result_bytes: bytes
    ) -> H1PostSealRecoveryRecordV1:
        if state.head is None or stage != state.next_stage:
            raise H1PostSealRecoveryRecordError("stage result differs")
        try:
            state.stage_input(stage)
        except H1PostSealRecoveryRecordError as error:
            raise H1PostSealRecoveryRecordError("stage result has no durable input") from error
        return H1PostSealRecoveryRecordV1(
            "STAGE_RESULT",
            state.root.root_id(),
            state.head,
            stage=stage,
            stage_ordinal=_STAGES.index(stage),
            result_bytes=result_bytes,
            result_digest=_digest(result_bytes),
        )


class H1PostSealRecoveryJournal:
    """One enrolled recovery role's existing-only authenticated record journal.

    The runtime mount, rather than this wrapper, supplies the trust anchor.  A
    wrapper is permanently poisoned after an append might have reached durable
    storage but its exact readback cannot be established.
    """

    def __init__(
        self,
        *,
        mount: EnrolledH1RecoveryMount,
        journal: IndependentTenantDecisionJournal,
    ) -> None:
        self._mount = mount
        self._journal = journal
        self._gate = mount.authority_gate
        self._closed = False
        self._poisoned = False

    @classmethod
    def open_enrolled(cls, mount: EnrolledH1RecoveryMount) -> H1PostSealRecoveryJournal:
        """Open only an issuer-created existing recovery mount, never a path."""
        if type(mount) is not EnrolledH1RecoveryMount:
            raise H1PostSealRecoveryRecordError("recovery journal requires an enrolled mount")
        mount.assert_current()
        journal = mount._open_existing_recovery_journal()
        if type(journal) is not IndependentTenantDecisionJournal:
            if isinstance(journal, IndependentTenantDecisionJournal):
                journal.close()
            raise H1PostSealRecoveryRecordError("recovery mount supplied an invalid journal")
        wrapper = cls(mount=mount, journal=journal)
        try:
            wrapper.scan()
            return wrapper
        except BaseException:
            wrapper.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._journal.close()

    def scan(self) -> H1PostSealRecoveryScan:
        self._require_open()
        with self._gate.hold():
            self._mount.assert_current()
            scan = self._scan_held()
            self._mount.assert_current()
            return scan

    def append_transition(
        self,
        record: H1PostSealRecoveryRecordV1,
        *,
        expected_global_tip: str | None,
    ) -> H1PostSealRecoveryAppendReceipt:
        """Scan, compare, CAS append and exact readback under one authority gate."""
        self._require_open()
        if type(record) is not H1PostSealRecoveryRecordV1:
            raise TypeError("recovery transition requires an exact canonical record")
        with self._gate.hold():
            self._mount.assert_current()
            before = self._scan_held()
            if before.tip != expected_global_tip:
                raise H1PostSealRecoveryRecordError("recovery journal predecessor is stale")
            self._validate_next(before, record)
            try:
                entry_id = self._journal.append(record.canonical_bytes(), expected_global_tip)
                after = self._scan_held()
                self._mount.assert_current()
            except BaseException:
                # Primitive append can have committed body/head before a later
                # failure.  The caller must reopen and reconcile, never retry
                # this object or report the action as not-written.
                self._poisoned = True
                raise
            if after.tip != entry_id:
                self._poisoned = True
                raise H1PostSealRecoveryRecordError("recovery append readback differs")
            return H1PostSealRecoveryAppendReceipt(entry_id, after)

    def _require_open(self) -> None:
        if self._closed:
            raise H1PostSealRecoveryUnavailable("recovery journal is closed")
        if self._poisoned:
            raise H1PostSealRecoveryUnavailable("recovery journal outcome is unknown")

    def _scan_held(self) -> H1PostSealRecoveryScan:
        if self._gate is not self._mount.authority_gate:
            raise H1PostSealRecoveryRecordError("recovery mount gate identity differs")
        self._gate.require_held()
        entries = self._journal.entries()
        return scan_authenticated_prefix(
            entries,
            tenant_id=self._mount.tenant_id,
            journal_instance_id=self._mount.journal_instance_id,
        )

    def _validate_next(
        self, before: H1PostSealRecoveryScan, record: H1PostSealRecoveryRecordV1
    ) -> None:
        if record.kind == "ROOT":
            if record.root_id in dict(before.states_by_root):
                raise H1PostSealRecoveryRecordError("recovery root is already durable")
            assert record.root is not None
            if _selected_seal_key(record.root) in dict(before.root_id_by_selected_seal):
                raise H1PostSealRecoveryRecordError("selected seal has a recovery root")
            if record.kind != "ROOT" or record.predecessor_entry_id is not None:
                raise H1PostSealRecoveryRecordError("first recovery transition must be root")
            if (
                record.root is None
                or record.root.tenant_id != self._mount.tenant_id
                or record.root.journal_instance_id != self._mount.journal_instance_id
            ):
                raise H1PostSealRecoveryRecordError("recovery root differs from enrolled mount")
            return
        try:
            state = before.state_for_root(record.root_id)
        except H1PostSealRecoveryRecordError as error:
            raise H1PostSealRecoveryRecordError(
                "recovery transition has an unknown root"
            ) from error
        if record.root_id != state.root.root_id() or record.predecessor_entry_id != state.head:
            raise H1PostSealRecoveryRecordError("recovery transition root or predecessor differs")
        # Reuse the pure prefix decoder to make the transition checks exactly
        # match restart scan behavior before any durable write.
        _apply_stage(state, record)


def denied_production_recovery_journal(path: object, mount: object) -> None:
    """Explicitly reject un-enrolled storage until the runtime mount is supplied."""
    del path, mount
    raise H1PostSealRecoveryRecordError(
        "production recovery journal requires an enrolled authenticated mount"
    )


__all__ = [
    "H1PostSealRecoveryAppendReceipt",
    "H1PostSealRecoveryJournal",
    "H1PostSealRecoveryRecordError",
    "H1PostSealRecoveryRecordV1",
    "H1PostSealRecoveryRootV1",
    "H1PostSealRecoveryScan",
    "H1PostSealRecoveryState",
    "H1PostSealRecoveryTransition",
    "H1PostSealRecoveryUnavailable",
    "apply_authenticated_prefix",
    "denied_production_recovery_journal",
    "scan_authenticated_prefix",
]
