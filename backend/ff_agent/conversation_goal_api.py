"""Goal-mode API boundary: chat mode bindings, target checks and at-most-once continuation."""
import hashlib
import json
import logging
import time
from fastapi import Depends, Path
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from .bank import DomainError

logger = logging.getLogger(__name__)
GOAL_ID = dict(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.:-]{1,64}$')


class ContinueBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: StrictInt = Field(ge=1)
    request_key: StrictStr = Field(min_length=1, max_length=100)


def _invalid(message):
    return DomainError('VALIDATION_ERROR', message, 422)


def _not_found():
    return DomainError('NOT_FOUND', '未找到可访问的目标。', 404)


def _stale():
    return DomainError('GOAL_STALE', '目标已更新，请刷新后再操作。', 409)


def _field(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _public(raw):
    if not isinstance(raw, dict):
        raise DomainError('GOAL_RESULT_INVALID', '目标处理结果无法确认，请核对原请求。', 500)
    return {'message': raw.get('message') or '', 'cards': list(raw.get('cards') or []),
            'tasks': list(raw.get('tasks') or []), 'goal': raw.get('goal')}


class GoalAccess:
    def __init__(self, bank, loop):
        self.bank = bank
        self.loop = loop
        with bank.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS goal_mode_bindings(user_id TEXT NOT NULL, request_key TEXT NOT NULL, '
                       'goal_mode INTEGER NOT NULL, goal_id TEXT, goal_version INTEGER, created_at REAL NOT NULL, '
                       'PRIMARY KEY(user_id, request_key))')
            db.execute('CREATE TABLE IF NOT EXISTS goal_continuations(owner TEXT NOT NULL, session_hash TEXT NOT NULL, '
                       'request_key TEXT NOT NULL, goal_id TEXT NOT NULL, expected_version INTEGER NOT NULL, '
                       'status TEXT NOT NULL, response TEXT, created_at REAL NOT NULL, '
                       'PRIMARY KEY(owner, session_hash, request_key))')

    @staticmethod
    def session_hash(token_hash):
        return hashlib.sha256(str(token_hash).encode('utf-8')).hexdigest()

    def epoch(self, user):
        with self.bank.transaction() as db:
            return self.bank._safety_epoch(db, user)

    @staticmethod
    def _validate(mode, goal_id, goal_version):
        if type(mode) is not bool:
            raise _invalid('goal_mode 必须是布尔值。')
        if goal_id is not None and (not isinstance(goal_id, str) or not 1 <= len(goal_id) <= 64):
            raise _invalid('goal_id 格式无效。')
        if goal_version is not None and (type(goal_version) is not int or goal_version < 1):
            raise _invalid('goal_version 必须是正整数。')
        if (goal_id is None) != (goal_version is None):
            raise _invalid('goal_id 与 goal_version 必须同时提供。')
        if not mode and goal_id is not None:
            raise _invalid('普通对话不能携带目标字段。')

    def bind_chat(self, db, user, key, text, mode, goal_id=None, goal_version=None):
        """Runs inside the caller's chat transaction; text equality is checked by the legacy chat path."""
        self._validate(mode, goal_id, goal_version)
        want = (1 if mode else 0, goal_id, goal_version)
        row = db.execute('SELECT goal_mode, goal_id, goal_version FROM goal_mode_bindings WHERE user_id=? AND request_key=?',
                         (user, key)).fetchone()
        if row is not None:
            have = (row[0], row[1], row[2])
        elif db.execute('SELECT 1 FROM chat_requests WHERE user_id=? AND request_key=?', (user, key)).fetchone():
            have = (0, None, None)
        else:
            db.execute('INSERT INTO goal_mode_bindings VALUES(?,?,?,?,?,?)', (user, key, *want, time.time()))
            return
        if have != want:
            raise DomainError('IDEMPOTENCY_CONFLICT', '同一请求编号的对话模式已改变。', 409)

    def check_target(self, user, session, epoch, mode, goal_id=None, goal_version=None):
        self._validate(mode, goal_id, goal_version)
        if not mode or goal_id is None:
            return None
        goal = self.loop.view(user, session, epoch, goal_id)
        if not goal:
            raise _not_found()
        latest = self.loop.view(user, session, epoch)
        if (_field(goal, 'version') != goal_version or not latest or _field(latest, 'id') != goal_id
                or _field(latest, 'version') != goal_version):
            raise _stale()
        return goal

    def _operation_cards(self, cards):
        for index, card in enumerate(cards or []):
            op = card.get('operation') if isinstance(card, dict) and card.get('type') == 'operation' else None
            opid = _field(op, 'id')
            if isinstance(opid, str) and opid:
                yield index, card, opid

    def refresh_cards(self, user, cards):
        cards = list(cards or [])
        # Fields that could assert a financial outcome; never keep them without an authoritative receipt.
        unsafe = ('result', 'next_operation', 'notices')
        for index, card, opid in self._operation_cards(cards):
            try:
                receipt = self.bank.receipt(user, opid)  # query-only read path
                op = _field(receipt, 'operation')
                observed = _field(receipt, 'observed_status')
                if (not isinstance(op, dict) or op.get('id') != opid
                        or not isinstance(observed, str) or not observed):
                    raise ValueError('incomplete receipt')
                # Overlay the observed status (e.g. EXPIRED) without writing bookkeeping.
                cards[index] = {**card, 'operation': {**op, 'status': observed}}
            except Exception as exc:
                logger.warning('goal_card_refresh_failed type=%s', type(exc).__name__)
                cached = card.get('operation')
                kept = {k: v for k, v in cached.items() if k not in unsafe} if isinstance(cached, dict) else {'id': opid}
                kept['status'] = 'UNKNOWN'
                cards[index] = {**{k: v for k, v in card.items() if k not in unsafe}, 'operation': kept}
        return cards

    def record_new(self, user, cards, record_operation):
        seen = set()
        for _, _, opid in self._operation_cards(cards):
            if opid in seen:
                continue
            seen.add(opid)
            try:
                receipt = self.bank.receipt(user, opid)
                if _field(receipt, 'observed_status') == 'WAITING_CONFIRMATION':
                    record_operation(user, _field(receipt, 'operation'))
            except Exception as exc:
                logger.warning('goal_card_record_failed type=%s', type(exc).__name__)


def register_goal_api(app, bank, loop, current_user, record_operation):
    access = GoalAccess(bank, loop)
    app.state.goal_access = access

    @app.get('/api/goals/current')
    def goal_current_route(session=Depends(current_user)):
        user = session['user_id']
        return {'goal': loop.view(user, session['token_hash'], access.epoch(user))}

    @app.get('/api/goals/{goal_id}')
    def goal_view_route(goal_id: str = Path(**GOAL_ID), session=Depends(current_user)):
        user = session['user_id']
        goal = loop.view(user, session['token_hash'], access.epoch(user), goal_id)
        if not goal:
            raise _not_found()
        return {'goal': goal}

    @app.post('/api/goals/{goal_id}/continue')
    def goal_continue_route(body: ContinueBody, goal_id: str = Path(**GOAL_ID), session=Depends(current_user)):
        user, token = session['user_id'], session['token_hash']
        owner_session, key = access.session_hash(token), body.request_key
        scope = (user, owner_session, key)
        epoch = access.epoch(user)
        with bank.transaction() as db:
            row = db.execute('SELECT goal_id, expected_version, status, response FROM goal_continuations '
                             'WHERE owner=? AND session_hash=? AND request_key=?', scope).fetchone()
        if row:
            if row['goal_id'] != goal_id or row['expected_version'] != body.expected_version:
                raise DomainError('IDEMPOTENCY_CONFLICT', '同一请求编号的内容已改变。', 409)
            if row['status'] == 'DONE':
                result = json.loads(row['response'])
                result['cards'] = access.refresh_cards(user, result.get('cards'))
                result['goal'] = loop.view(user, token, epoch, goal_id)
                fresh_status = _field(result['goal'], 'status') if result['goal'] else None
                if fresh_status is None or fresh_status in ('UNKNOWN', 'PAUSED', 'BLOCKED', 'FAILED', 'EXPIRED'):
                    # never replay a cached success claim over an unconfirmed current state
                    result['message'] = '请先核对原请求，当前状态暂未确认。'
                return result
            raise DomainError('REQUEST_UNKNOWN', '该请求结果未确定，请核对原请求和已有操作，不要重复提交。', 409)
        goal = loop.view(user, token, epoch, goal_id)
        if not goal:
            raise _not_found()
        if _field(goal, 'version') != body.expected_version:
            raise _stale()
        with bank.transaction() as db:
            claimed = db.execute("INSERT OR IGNORE INTO goal_continuations VALUES(?,?,?,?,?,'PROCESSING',NULL,?)",
                                 (*scope, goal_id, body.expected_version, time.time())).rowcount == 1
        if not claimed:
            raise DomainError('REQUEST_PENDING', '该请求正在处理，请核对原请求。', 409)
        try:
            result = _public(loop.continue_goal(user, token, epoch, goal_id, body.expected_version, key))
        except Exception:
            try:
                with bank.transaction() as db:
                    db.execute("UPDATE goal_continuations SET status='UNKNOWN' WHERE owner=? AND session_hash=? AND request_key=?", scope)
            except Exception as exc:
                logger.warning('goal_continue_mark_unknown_failed type=%s', type(exc).__name__)
            raise
        with bank.transaction() as db:
            db.execute("UPDATE goal_continuations SET status='DONE', response=? WHERE owner=? AND session_hash=? AND request_key=?",
                       (json.dumps(result, ensure_ascii=False), *scope))
        access.record_new(user, result['cards'], record_operation)
        return result

    return access
