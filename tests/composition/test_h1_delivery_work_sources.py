"""Delivery-work absence must fail closed for future record versions."""

import pytest

from chiplog.composition.h1_delivery_work_sources import (
    H1DeliveryWorkSourceHold,
    require_delivery_work_absent,
)
from chiplog.composition.h1_inventory_owner_cut import OwnerJournalSqlRecord


def test_unknown_delivery_work_version_blocks_genesis_absence() -> None:
    foreign = OwnerJournalSqlRecord(
        record_id="foreign-work",
        owner="another-owner",
        schema="chiplog.delivery-work.subject.v2",
        raw=b"foreign-work",
        commit_sequence=3,
    )
    with pytest.raises(H1DeliveryWorkSourceHold, match="state is present or unknown"):
        require_delivery_work_absent((foreign,))


def test_unrelated_post_terminal_work_does_not_block_delivery_genesis() -> None:
    original_obligation_work = OwnerJournalSqlRecord(
        record_id="recovery-work",
        owner="agent_loop",
        schema="chiplog.post-terminal.rollover.v1",
        raw=b"recovery-work",
        commit_sequence=3,
    )
    require_delivery_work_absent((original_obligation_work,))
