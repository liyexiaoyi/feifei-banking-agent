import json
import re

from .bank import DomainError
from .card_chat_auth import check_card_ticket
from .card_query import resolve_card_query


_PENDING = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY')
_TYPES = {'card_report_lost': 'report_lost', 'card_restore': 'restore'}


def _fail(code='CARD_CONTROL_UNAVAILABLE'):
    raise DomainError(code, '请求与原记录冲突' if code == 'IDEMPOTENCY_CONFLICT' else '卡片申请当前不可撤回或更正')


def _decode(row):
    if row is None:
        return None
    result = dict(row)
    result['action'] = json.loads(result.pop('action_json'))
    result['source_ids'] = json.loads(result['source_ids'])
    return result


class CardControls:
    def __init__(self, bank):
        self.bank = bank
        with bank.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS card_chat_controls (
                user_id TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL,
                action_json TEXT NOT NULL, source_ids TEXT NOT NULL,
                target_card_id TEXT, target_version INTEGER, source_action TEXT,
                phase TEXT NOT NULL, new_op_id TEXT, error TEXT, created_at REAL NOT NULL,
                PRIMARY KEY(user_id, key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS card_chat_claims (
                source_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, key TEXT NOT NULL)''')

    def _get(self, db, user, key):
        return _decode(db.execute(
            'SELECT * FROM card_chat_controls WHERE user_id=? AND key=?',
            (user, key)).fetchone())

    def _owned(self, db, user, card_id):
        rows = db.execute('''SELECT c.* FROM bank_all_cards c JOIN accounts a
            ON a.id=c.account_id AND a.user_id=c.user_id
            WHERE c.user_id=? AND c.id=?''', (user, card_id)).fetchall()
        return rows[0] if len(rows) == 1 else None

    def _selector(self, db, user, selector, card_id):
        if selector in ('全部', '刚才那张', '未指定'):
            return
        rows = db.execute('''SELECT c.* FROM bank_all_cards c JOIN accounts a
            ON a.id=c.account_id AND a.user_id=c.user_id
            WHERE c.user_id=?''', (user,)).fetchall()
        matches = resolve_card_query([dict(row) for row in rows], selector)
        if len(matches) != 1 or matches[0]['id'] != card_id:
            _fail()

    def load(self, user, key, request_hash):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            journal = self._get(db, user, key)
            if journal is not None and journal['request_hash'] != request_hash:
                _fail('IDEMPOTENCY_CONFLICT')
            return journal

    def cancel(self, user, key, request_hash, action, source_ids,
               target=None, plan_ticket=None):
        with self.bank.transaction() as db:
            check_card_ticket(self.bank, db, user, plan_ticket, action, key)
            self.bank._user(db, user)
            journal = self._get(db, user, key)
            if journal is not None:
                if journal['request_hash'] != request_hash or journal['action'] != action:
                    _fail('IDEMPOTENCY_CONFLICT')
                return journal
            kind = action.get('type') if isinstance(action, dict) else None
            if kind not in ('card_stop', 'card_correct'):
                _fail()
            correct = kind == 'card_correct'
            fields = {'type', 'card'} if correct else {'type', 'card', 'action'}
            if set(action) != fields:
                _fail()
            card = action['card']
            aliases = ('日常账户', '储蓄账户', '刚才那张', '未指定')
            if not isinstance(card, str) or not (
                card in aliases or re.fullmatch(r'[0-9]{4}', card)
                or (not correct and card == '全部')
            ):
                _fail()
            if not correct and action['action'] not in ('report_lost', 'restore', 'any'):
                _fail()
            if (not isinstance(source_ids, list) or not source_ids
                    or any(not isinstance(s, str) or not s for s in source_ids)
                    or len(set(source_ids)) != len(source_ids)):
                _fail()
            if correct:
                if (len(source_ids) != 1 or not isinstance(target, dict)
                        or set(target) != {'id', 'version'}
                        or type(target['version']) is not int):
                    _fail()
                owned = self._owned(db, user, target['id'])
                if owned is None or owned['version'] != target['version']:
                    _fail()
                self._selector(db, user, card, target['id'])
            elif target is not None:
                _fail()
            self.bank._expire(db, user)
            now = self.bank.now().timestamp()
            sources = []
            for source_id in source_ids:
                row = db.execute('''SELECT * FROM operations
                    WHERE user_id=? AND id=?''', (user, source_id)).fetchone()
                if row is None or row['type'] not in _TYPES or row['status'] not in _PENDING:
                    _fail()
                if not row['expires_at'] or row['expires_at'] <= now:
                    _fail()
                if db.execute('SELECT 1 FROM card_chat_claims WHERE source_id=?',
                              (source_id,)).fetchone() is not None:
                    _fail()
                try:
                    payload = json.loads(row['payload'])
                except (ValueError, TypeError):
                    _fail()
                if not isinstance(payload, dict) or not isinstance(payload.get('card_id'), str):
                    _fail()
                owned_source = self._owned(db, user, payload['card_id'])
                if owned_source is None:
                    _fail()
                if not correct:
                    self._selector(db, user, card, payload['card_id'])
                source_action = _TYPES[row['type']]
                if correct and payload['card_id'] == target['id']:
                    _fail()
                if not correct and action['action'] not in ('any', source_action):
                    _fail()
                sources.append(row)
            actions = {_TYPES[row['type']] for row in sources}
            source_action = next(iter(actions)) if len(actions) == 1 else None
            for row in sources:
                changed = db.execute('''UPDATE operations SET status='CANCELLED'
                    WHERE user_id=? AND id=? AND type=?
                    AND status IN (?,?,?) AND expires_at>?''',
                    (user, row['id'], row['type'], *_PENDING, now))
                if changed.rowcount != 1:
                    _fail()
                db.execute('INSERT INTO card_chat_claims(source_id,user_id,key) VALUES(?,?,?)',
                           (row['id'], user, key))
                self.bank._event(db, user, row['id'], 'OPERATION_CANCELLED',
                                 '用户明确撤回未执行卡片申请 / ' + kind)
            db.execute('''INSERT INTO card_chat_controls
                (user_id,key,request_hash,action_json,source_ids,target_card_id,
                 target_version,source_action,phase,new_op_id,error,created_at)
                VALUES(?,?,?,?,?,?,?,?,'cancelled',NULL,NULL,?)''',
                (user, key, request_hash, json.dumps(action, ensure_ascii=False, sort_keys=True),
                 json.dumps(source_ids), target['id'] if correct else None,
                 target['version'] if correct else None, source_action, now))
            return self._get(db, user, key)

    def record_prepare(self, user, key, operation_id=None, error=None):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            journal = self._get(db, user, key)
            if journal is None or journal['action']['type'] != 'card_correct':
                _fail()
            if operation_id is not None:
                row = db.execute('''SELECT * FROM operations
                    WHERE user_id=? AND id=? AND idempotency_key=?''',
                    (user, operation_id, key)).fetchone()
                if row is None or _TYPES.get(row['type']) != journal['source_action']:
                    _fail()
                payload = json.loads(row['payload'])
                if (not isinstance(payload, dict)
                        or payload.get('card_id') != journal['target_card_id']
                        or type(payload.get('expected_version')) is not int
                        or payload['expected_version'] != journal['target_version']):
                    _fail()
                if journal['new_op_id'] not in (None, operation_id):
                    _fail('IDEMPOTENCY_CONFLICT')
                phase, code = 'prepared', None
            else:
                if journal['new_op_id'] is not None:
                    return journal
                code = getattr(error, 'code', error)
                if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,199}', code):
                    code = 'PREPARE_FAILED'
                phase = 'prepare_failed'
            db.execute('''UPDATE card_chat_controls SET phase=?,new_op_id=?,error=?
                WHERE user_id=? AND key=?''', (phase, operation_id, code, user, key))
            return self._get(db, user, key)
