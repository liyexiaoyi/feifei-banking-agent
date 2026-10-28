import json
from sqlite3 import Row

from .bank import DomainError


_SQL = """
SELECT payload
FROM operations
WHERE user_id = ?
  AND type = ?
  AND (? IS NULL OR id <> ?)
  AND (
    (status IN ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY')
     AND expires_at > ?)
    OR status IN ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW')
  )
"""


def _invalid():
    return DomainError(
        'INVALID_OPERATION', '原理财操作数据无法核对，请先核实已有记录。', status=409
    )


def _payloads(db, user, now, operation_type, exclude):
    cursor = db.cursor()
    cursor.row_factory = Row
    cursor.execute(_SQL, (user, operation_type, exclude, exclude, now))
    for row in cursor:
        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError, RecursionError) as exc:
            raise _invalid() from exc
        if not isinstance(payload, dict):
            raise _invalid()
        if not isinstance(payload.get('account_id'), str):
            raise _invalid()
        yield payload


def reserved_cash(db, user, now, account_id, exclude=None) -> int:
    """Sum active purchase reservations; user is the stored user_id."""
    total = 0
    for payload in _payloads(db, user, now, 'wealth_purchase', exclude):
        amount = payload.get('amount_cents')
        if type(amount) is not int or not 1 <= amount <= 100000000:
            raise _invalid()
        if payload['account_id'] == account_id:
            total += amount
    return total


def reserved_units(db, user, now, account_id, product_id, exclude=None) -> int:
    """Sum active redemption reservations; user is the stored user_id."""
    total = 0
    for payload in _payloads(db, user, now, 'wealth_redemption', exclude):
        product = payload.get('product_id')
        units = payload.get('units_1e4')
        if not isinstance(product, str):
            raise _invalid()
        if type(units) is not int or units <= 0:
            raise _invalid()
        if payload['account_id'] == account_id and product == product_id:
            total += units
    return total
