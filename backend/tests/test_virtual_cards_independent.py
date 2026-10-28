from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest

from ff_agent.bank import Bank, DomainError, TZ


def oid(op):
    return op["id"]


def virtuals(bank, user="demo"):
    return [c for c in bank.list_cards(user) if c["id"].startswith("vcard_")]


@pytest.fixture
def bank(tmp_path):
    clock = [datetime(2025, 1, 2, 12, 0, tzinfo=TZ)]
    return Bank(tmp_path/'independent-virtual.db', clock=lambda: clock[0]), clock


def test_yellow_confirm_creates_and_replay_reuses_id(bank):
    b, _ = bank
    assert virtuals(b) == []
    op = b.prepare_virtual_card("demo", "a_daily", "vc-1")
    assert virtuals(b) == []
    result = b.confirm("demo", oid(op))
    assert result["status"] == "SUCCEEDED"
    assert result["result"]["status"] == "ACTIVE"
    cards = virtuals(b)
    assert len(cards) == 1
    replay = b.prepare_virtual_card("demo", "a_daily", "vc-1")
    assert oid(replay) == oid(op)
    assert len(virtuals(b)) == 1


def test_cross_user_is_rejected_without_card(bank):
    b, _ = bank
    op = b.prepare_virtual_card("demo", "a_daily", "cross-user")
    with pytest.raises(DomainError):
        b.confirm("guest", oid(op))
    assert virtuals(b) == []
    assert virtuals(b,"guest") == []


def test_cancel_and_expire_do_not_create_cards(bank):
    b, clock = bank
    cancelled = b.prepare_virtual_card("demo", "a_daily", "cancelled")
    b.cancel("demo", oid(cancelled))
    clock[0] += timedelta(days=2)
    expired = b.prepare_virtual_card("demo", "a_saving", "expired")
    clock[0] += timedelta(days=2)
    assert b.operation("demo", oid(expired))["status"] == "EXPIRED"
    assert virtuals(b) == []


def test_same_key_on_another_account_conflicts(bank):
    b, _ = bank
    b.prepare_virtual_card("demo", "a_daily", "bound-key")
    with pytest.raises(DomainError):
        b.prepare_virtual_card("demo", "a_saving", "bound-key")
    assert virtuals(b) == []


def test_different_key_cannot_duplicate_pending_account_application(bank):
    b, _ = bank
    b.prepare_virtual_card("demo", "a_daily", "first-key")
    with pytest.raises(DomainError):
        b.prepare_virtual_card("demo", "a_daily", "second-key")
    assert virtuals(b) == []


def test_concurrent_prepare_has_one_winner(bank):
    b, _ = bank

    def prepare(index):
        try:
            return b.prepare_virtual_card("demo", "a_daily", f"race-key-{index}")
        except DomainError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, range(2)))
    assert sum(item is not None for item in results) == 1
    assert len(virtuals(b)) == 0


def test_money_and_ledger_are_unchanged_by_creation(bank):
    b, _ = bank
    before = b.snapshot("demo")
    before_transactions = b.analyze("demo", b.now().strftime("%Y-%m"))["transactions"]
    op = b.prepare_virtual_card("demo", "a_daily", "no-money")
    b.confirm("demo", oid(op))
    after = b.snapshot("demo")
    assert before["accounts"] == after["accounts"]
    assert before_transactions == b.analyze("demo", b.now().strftime("%Y-%m"))["transactions"]


def test_confirmed_operation_is_idempotent(bank):
    b, _ = bank
    op = b.prepare_virtual_card("demo", "a_daily", "idempotent")
    b.confirm("demo", oid(op))
    first = b.confirm("demo", oid(op))
    second = b.confirm("demo", oid(op))
    assert second == first
    assert len(virtuals(b)) == 1
