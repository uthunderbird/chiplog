"""Delivery-work absence must fail closed for future record versions."""

import pytest

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    delivery_work_record_id,
    delivery_work_subject_id,
)
from chiplog.composition.h1_delivery_work_sources import (
    H1DeliveryWorkSourceHold,
    require_delivery_work_absent,
)
from chiplog.composition.h1_inventory_owner_cut import OwnerJournalSqlRecord


def test_occupied_reserved_subject_id_blocks_regardless_of_owner_or_schema() -> None:
    subject = delivery_work_subject_id("tenant", "intent")
    foreign = OwnerJournalSqlRecord(
        record_id=delivery_work_record_id(subject, 0),
        owner="another-owner",
        schema="chiplog.delivery-work.subject.v2",
        raw=b"foreign-work",
        commit_sequence=3,
    )
    with pytest.raises(H1DeliveryWorkSourceHold, match="subject record is present or unknown"):
        require_delivery_work_absent(
            (foreign,),
            (delivery_work_record_id(subject, 0), delivery_work_record_id(subject, 1)),
        )


def test_unrelated_subject_and_post_terminal_work_do_not_block_delivery_genesis() -> None:
    original_obligation_work = OwnerJournalSqlRecord(
        record_id="recovery-work",
        owner="agent_loop",
        schema="chiplog.post-terminal.rollover.v1",
        raw=b"recovery-work",
        commit_sequence=3,
    )
    subject = delivery_work_subject_id("tenant", "intent")
    require_delivery_work_absent(
        (original_obligation_work,),
        (delivery_work_record_id(subject, 0), delivery_work_record_id(subject, 1)),
    )


def test_unknown_delivery_work_schema_holds_even_without_a_matching_subject_id() -> None:
    subject = delivery_work_subject_id("tenant", "intent")
    unknown = OwnerJournalSqlRecord(
        record_id="unattributable-work",
        owner="agent_loop",
        schema="chiplog.delivery-work.subject.v99",
        raw=b"unknown",
        commit_sequence=3,
    )
    with pytest.raises(H1DeliveryWorkSourceHold, match="present or unknown"):
        require_delivery_work_absent(
            (unknown,),
            (delivery_work_record_id(subject, 0), delivery_work_record_id(subject, 1)),
        )
