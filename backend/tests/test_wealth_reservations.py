import json
import sqlite3

import pytest

from ff_agent.bank import DomainError
from ff_agent.wealth_reservations import reserved_cash, reserved_units


@pytest.fixture
def db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE operations (id TEXT PRIMARY KEY, user_id TEXT, '
                 'type TEXT, payload TEXT, status TEXT, expires_at INTEGER)')
    yield conn
    conn.close()


def payload(kind, value=7, account='a', product='p'):
    data = {'account_id': account}
    data.update({'amount_cents': value} if kind == 'wealth_purchase' else
                {'product_id': product, 'units_1e4': value})
    return data


def add(db, oid, kind, data, user='u', status='READY', expires=101):
    raw = data if isinstance(data, str) else json.dumps(data)
    db.execute('INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?)',
               (oid, user, kind, raw, status, expires))


def reserve(db, kind, exclude=None):
    if kind == 'wealth_purchase':
        return reserved_cash(db, 'u', 100, 'a', exclude=exclude)
    return reserved_units(db, 'u', 100, 'a', 'p', exclude=exclude)


KINDS = ['wealth_purchase', 'wealth_redemption']
TTL_STATUSES = ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY']
STICKY_STATUSES = ['UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW']


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('status', TTL_STATUSES + STICKY_STATUSES +
                         ['COMPLETED', 'DONE', 'CANCELLED', 'FAILED', 'EXPIRED', 'OTHER'])
@pytest.mark.parametrize('expires', [None, 99, 100, 101])
def test_status_and_strict_ttl(db, kind, status, expires):
    add(db, 'one', kind, payload(kind), status=status, expires=expires)
    active = status in STICKY_STATUSES or (
        status in TTL_STATUSES and expires is not None and expires > 100)
    result = reserve(db, kind)
    assert type(result) is int
    assert result == (7 if active else 0)


@pytest.mark.parametrize('kind', KINDS)
def test_scope_and_sum(db, kind):
    assert reserve(db, kind) == 0
    add(db, 'one', kind, payload(kind))
    add(db, 'two', kind, payload(kind, 11))
    add(db, 'account', kind, payload(kind, 29, account='other'))
    add(db, 'user', kind, '{', user='other')
    other_kind = KINDS[1] if kind == KINDS[0] else KINDS[0]
    add(db, 'type', other_kind, '{')
    add(db, 'expired', kind, '{', expires=100)
    add(db, 'terminal', kind, '{', status='COMPLETED')
    if kind == 'wealth_redemption':
        add(db, 'product', kind, payload(kind, 37, product='other'))
    assert reserve(db, kind) == 18
    assert reserve(db, kind, exclude='one') == 11
    assert reserve(db, kind, exclude='absent') == 18


@pytest.mark.parametrize('kind', KINDS)
def test_exclusion_precedes_json_parsing(db, kind):
    add(db, 'keep', kind, payload(kind))
    add(db, 'skip', kind, '{', status='EXECUTING', expires=0)
    assert reserve(db, kind, exclude='skip') == 7


BAD_COMMON = ['{', '', 'null', '[]', '"text"', '42', '{}']
BAD = [(kind, raw) for kind in KINDS for raw in BAD_COMMON]
BAD += [(kind, dict(payload(kind), account_id=value))
        for kind in KINDS for value in [None, True, 123, [], {}]]
BAD += [(kind, {k: v for k, v in payload(kind).items() if k != field})
        for kind in KINDS for field in payload(kind)]
BAD += [('wealth_purchase', payload('wealth_purchase', value))
        for value in [True, False, 0, -1, 100000001, 1.0, '1', None]]
BAD += [('wealth_redemption', payload('wealth_redemption', value))
        for value in [True, False, 0, -1, 1.0, '1', None]]
BAD += [('wealth_redemption', payload('wealth_redemption', product=value))
        for value in [None, True, 123, [], {}]]


@pytest.mark.parametrize('kind,data', BAD)
def test_invalid_payload_raises_domain_error(db, kind, data):
    add(db, 'bad', kind, data)
    before = db.total_changes
    with pytest.raises(DomainError) as caught:
        reserve(db, kind)
    assert caught.value.code == 'INVALID_OPERATION'
    assert caught.value.status == 409
    assert db.total_changes == before
    assert db.in_transaction


@pytest.mark.parametrize('kind,value', [('wealth_purchase', 1),
    ('wealth_purchase', 100000000), ('wealth_redemption', 1),
    ('wealth_redemption', 100000001)])
def test_boundaries_and_read_only_connection(db, kind, value):
    add(db, 'one', kind, payload(kind, value))
    assert isinstance(db.execute('SELECT * FROM operations').fetchone(), sqlite3.Row)
    before = db.total_changes
    assert db.in_transaction
    result = (reserved_cash(db, 'u', 100, 'a') if kind == KINDS[0]
              else reserved_units(db, 'u', 100, 'a', 'p'))
    assert type(result) is int and result == value
    assert db.total_changes == before
    assert db.in_transaction
    db.rollback()
    assert db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0
