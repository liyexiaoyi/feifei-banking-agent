"""Annual insight evidence and isolation; synthetic entries only, no provider calls."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from ff_agent.bank import Bank, DomainError


def bank_with_rows(tmp_path):
    bank = Bank(tmp_path / 'annual.db', clock=lambda: datetime(2026, 10, 27, tzinfo=ZoneInfo('Asia/Shanghai')))
    with bank.transaction() as db:
        db.execute('DELETE FROM transactions')
        rows = [
            ('jan', 'demo', 'a_daily', 'EXPENSE', 10001, '餐饮', '商户甲', '2026-01-01T00:00:00+08:00'),
            ('dec', 'demo', 'a_daily', 'EXPENSE', 20002, '交通', '商户乙', '2026-12-31T23:59:59+08:00'),
            ('refund', 'demo', 'a_daily', 'REFUND', 40000, '餐饮', '退款', '2026-02-01T10:00:00+08:00'),
            ('income', 'demo', 'a_daily', 'INCOME', 90000, '工资', '单位', '2026-02-01T10:00:00+08:00'),
            ('internal', 'demo', 'a_daily', 'INTERNAL', 77777, '账户划转', '本人', '2026-03-01T10:00:00+08:00'),
            ('invest', 'demo', 'a_daily', 'INVESTMENT_PURCHASE', 88888, '理财', '演示产品甲', '2026-03-01T10:00:00+08:00'),
            ('redemption', 'demo', 'a_daily', 'INVESTMENT_REDEMPTION', 66666, '理财', '演示产品甲', '2026-03-01T10:00:00+08:00'),
            ('previous', 'demo', 'a_daily', 'EXPENSE', 321, '餐饮', '商户甲', '2025-12-31T23:59:59+08:00'),
            ('next', 'demo', 'a_daily', 'EXPENSE', 999, '餐饮', '商户甲', '2027-01-01T00:00:00+08:00'),
            ('guest-secret', 'guest', 'b_daily', 'EXPENSE', 999999, '其他', '秘密商户', '2026-01-01T00:00:00+08:00'),
        ]
        db.executemany('INSERT INTO transactions(id,user_id,account_id,kind,amount_cents,category,merchant,occurred_at) VALUES(?,?,?,?,?,?,?,?)', rows)
    return bank


def test_year_has_twelve_months_exact_cents_and_negative_net(tmp_path):
    bank = bank_with_rows(tmp_path)
    report = bank.analyze_year('demo', '2026')
    assert report['year'] == report['period'] == '2026'
    assert report['simulated'] is True
    assert report['expense_cents'] == 30003
    assert report['refund_cents'] == 40000
    assert report['income_cents'] == 90000
    assert report['net_expense_cents'] == -9997
    assert report['previous_net_expense_cents'] == 321
    assert [m['period'] for m in report['months']] == [f'2026-{m:02}' for m in range(1, 13)]
    assert [m['net_expense_cents'] for m in report['months']] == [10001, -40000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 20002]
    assert sum(m['expense_cents'] for m in report['months']) == report['expense_cents']
    assert sum(m['transaction_count'] for m in report['months']) == 7
    assert set(report['evidence_ids']) == {'jan', 'dec', 'refund', 'income', 'internal', 'invest', 'redemption'}
    assert sorted(report['categories'], key=lambda x: x['name']) == [{'name': '交通', 'amount_cents': 20002}, {'name': '餐饮', 'amount_cents': 10001}]
    assert {t['id'] for t in report['transactions']} == set(report['evidence_ids'])
    assert not any('user_id' in t or 'account_id' in t or 'note' in t for t in report['transactions'])
    assert '秘密商户' not in str(report)


def test_annual_read_does_not_expire_or_release_unknown_or_change_assets(tmp_path):
    bank = bank_with_rows(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 10000, 'before-year-read')
    with bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN',expires_at=0 WHERE id=?", (op['id'],))
        before = [tuple(r) for r in db.execute('SELECT * FROM operations')]
        assets = [tuple(r) for r in db.execute('SELECT * FROM accounts')]
        db.execute("UPDATE users SET locked_until=? WHERE id='demo'", (bank.now().timestamp() + 600,))
    bank.analyze_year('demo', '2026')
    with bank.transaction() as db:
        assert before == [tuple(r) for r in db.execute('SELECT * FROM operations')]
        assert assets == [tuple(r) for r in db.execute('SELECT * FROM accounts')]


def test_empty_year_is_zero_not_error_and_leap_day_is_in_february(tmp_path):
    bank = bank_with_rows(tmp_path)
    empty = bank.analyze_year('guest', '2024')
    assert empty['net_expense_cents'] == 0 and empty['evidence_ids'] == [] and empty['flags'] == []
    with bank.transaction() as db:
        db.execute("INSERT INTO transactions(id,user_id,account_id,kind,amount_cents,category,merchant,occurred_at) VALUES('leap','guest','b_daily','EXPENSE',1,'其他','闰日','2024-02-29T23:59:59+08:00')")
    assert bank.analyze_year('guest', '2024')['months'][1]['expense_cents'] == 1
    with pytest.raises(DomainError) as denied:
        bank.analyze_year('missing-user', '2026')
    assert denied.value.code == 'NOT_FOUND'


@pytest.mark.parametrize('year', ['2026-10', '2026x', '２０２６', '0000', '0001', '1899', '2101', '', True, 2026, None])
def test_invalid_annual_year_is_rejected(tmp_path, year):
    bank = bank_with_rows(tmp_path)
    with pytest.raises(DomainError) as denied:
        bank.analyze_year('demo', year)
    assert denied.value.code == 'INVALID_YEAR'


def test_anomaly_candidates_are_explained_evidence_not_scam_verdict(tmp_path):
    bank = bank_with_rows(tmp_path)
    with bank.transaction() as db:
        for tid, amount, at in [('large', 100000, '2026-06-01T10:00:00+08:00'), ('edge', 99999, '2026-06-02T10:00:00+08:00'), ('dup-a', 100, '2026-06-03T10:00:00+08:00'), ('dup-b', 100, '2026-06-03T10:02:00+08:00'), ('far', 100, '2026-06-03T10:04:01+08:00')]:
            db.execute('INSERT INTO transactions(id,user_id,account_id,kind,amount_cents,category,merchant,occurred_at) VALUES(?,?,?,?,?,?,?,?)', (tid, 'demo', 'a_daily', 'EXPENSE', amount, '其他', '测试商户', at))
    report = bank.analyze_year('demo', '2026')
    flags = report['flags']
    assert any(f['type'] == 'large_expense' and f['evidence_ids'] == ['large'] for f in flags)
    assert not any('edge' in f['evidence_ids'] for f in flags)
    duplicates = [f for f in flags if f['type'] == 'possible_duplicate']
    assert len(duplicates) == 1 and set(duplicates[0]['evidence_ids']) == {'dup-a', 'dup-b'}
    for flag in flags:
        assert '核查' in flag['message'] or '核对' in flag['message']
        assert set(flag['evidence_ids']) <= set(report['evidence_ids'])
        assert '确定诈骗' not in flag['message']
    assert not bank.snapshot('demo')['safety_hold']


def test_annual_api_is_session_scoped_read_only_and_strict_query(tmp_path):
    with TestClient(create_app(tmp_path / 'api-annual.db')) as client:
        assert client.get('/api/bill/annual?year=2026').status_code == 401
        assert client.post('/api/session', json={'user': 'guest'}).status_code == 200
        r = client.get('/api/bill/annual?year=2026')
        assert r.status_code == 200
        assert r.json()['evidence_ids'] == []
        for query in ['year=2026&user_id=demo', 'year=2026&year=2025', 'year=2026&unused=1', 'year=２０２６', 'year=1899']:
            assert client.get('/api/bill/annual?' + query).status_code == 422
        assert client.get('/api/bill?period=2026-10').status_code == 200


@pytest.mark.parametrize('at', ['2026-13-01T00:00:00+08:00','2026-02-30T12:00:00+08:00'])
def test_corrupt_annual_dates_fail_instead_of_returning_a_partial_report(tmp_path, at):
    bank = bank_with_rows(tmp_path)
    with bank.transaction() as db:
        db.execute("UPDATE transactions SET occurred_at=? WHERE id='jan'", (at,))
    with pytest.raises(DomainError) as failed:
        bank.analyze_year('demo','2026')
    assert failed.value.code == 'INVALID_BILL_DATA' and failed.value.status == 503


@pytest.mark.parametrize('at,year,month', [('2025-12-31T16:30:00Z','2026',0),('2026-01-31T20:00:00+00:00','2026',1),('2027-01-01T00:30:00+14:00','2026',11)])
def test_annual_calendar_uses_shanghai_time_without_changing_stored_evidence(tmp_path,at,year,month):
    bank = bank_with_rows(tmp_path)
    with bank.transaction() as db:
        db.execute('DELETE FROM transactions')
        db.execute('INSERT INTO transactions(id,user_id,account_id,kind,amount_cents,category,merchant,occurred_at) VALUES(?,?,?,?,?,?,?,?)', ('boundary','demo','a_daily','EXPENSE',500,'其他','时区测试',at))
    report=bank.analyze_year('demo',year)
    assert report['expense_cents']==500 and report['months'][month]['expense_cents']==500
    assert report['transactions'][0]['occurred_at'].endswith('+08:00')
    assert bank.analyze_year('demo','2025')['expense_cents']==0
    with bank.transaction() as db:
        assert db.execute("SELECT occurred_at FROM transactions WHERE id='boundary'").fetchone()[0]==at
