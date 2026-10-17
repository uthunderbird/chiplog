"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from chiplog.platform._sqlite import (
    PhysicalPublicationCommand,
    PublicationVerificationMode,
    VerifiedOwnerPublication,
)


class _Resolver:
    def __init__(self, command: PhysicalPublicationCommand) -> None:
        self.command = command
        self.modes: list[PublicationVerificationMode] = []

    def verify(
        self,
        command: PhysicalPublicationCommand,
        mode: PublicationVerificationMode,
        _connection: object,
    ) -> VerifiedOwnerPublication:
        self.modes.append(mode)
        return VerifiedOwnerPublication.from_command(
            command,
            binding_fingerprint="binding",
            selected_identity=None if mode == "PRESELECT" else "selected",
            selected_fingerprint=None if mode == "PRESELECT" else "selected-fingerprint",
            expected_resulting=None,
            expected_commit_sequence=1,
        )
