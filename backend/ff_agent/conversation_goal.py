"""Conversation goal metadata and step journal. It never executes or authorizes bank effects.

Orchestration lives in the next module; this store only records intent, observations
and single-flight step claims so an uncertain step is never dispatched twice.
"""
import hashlib
import json
import re
import secrets
import sqlite3
import uuid

from .bank import DomainError
from .goal_control_schema import bootstrap_controls

BOOT = uuid.uuid4().hex  # per process: a STARTED step from an earlier process is interrupted
KINDS = frozenset({'birthday', 'spending_subscriptions'})
ACTIONS = frozenset({
    'birthday_create', 'birthday_catalog', 'birthday_choice', 'birthday_select',
    'birthday_advance', 'birthday_read', 'bill', 'subscription_query',
    'subscription_read', 'subscription_cancel_prepare', 'operation_read'})
PATCH_FIELDS = frozenset({'phase', 'question', 'slots', 'observations', 'status'})
PATCH_STATUS = frozenset({'ACTIVE', 'DONE', 'CANCELLED'})
SETTLE_STATES = frozenset({'OBSERVED', 'UNKNOWN'})
MAX_JSON, MAX_DEPTH, MAX_ITEMS, MAX_TEXT, MAX_STEPS = 16384, 8, 200, 2000, 64
MIN_CENTS, MAX_CENTS = 1, 100_000_000
SECRET_PARTS = ('password', 'passwd', 'passcode', 'verification_code', 'verify_code', 'csrf',
                'api_key', 'apikey', 'secret', 'token', 'cookie', 'authorization', 'cvv',
                'private_key')
SECRET_EXACT = frozenset({'pin', 'otp', 'cvc', 'sms_code'})
PHASE_RE = re.compile(r'[A-Z][A-Z0-9_]{0,39}\Z')
KEY_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS conversation_goals(id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    session_hash TEXT NOT NULL, epoch TEXT NOT NULL, request_key TEXT NOT NULL,
    intent_hash TEXT NOT NULL, kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','UNKNOWN','DONE','CANCELLED')),
    phase TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=1), slots TEXT NOT NULL,
    observations TEXT NOT NULL, question TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(user_id,session_hash,request_key))""",
    """CREATE TABLE IF NOT EXISTS conversation_goal_steps(
    goal_id TEXT NOT NULL REFERENCES conversation_goals(id), seq INTEGER NOT NULL,
    action TEXT NOT NULL, args TEXT NOT NULL,
    effect_key TEXT NOT NULL UNIQUE CHECK(length(effect_key) BETWEEN 8 AND 64),
    token_hash TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK(state IN ('STARTED','OBSERVED','UNKNOWN')),
    result TEXT, boot TEXT NOT NULL, created_at TEXT NOT NULL, settled_at TEXT,
    PRIMARY KEY(goal_id,seq))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS conversation_goal_one_started
    ON conversation_goal_steps(goal_id) WHERE state='STARTED'""",
)


def _bad(code='INVALID_ARGUMENT', message='目标数据无效。'):
    return DomainError(code, message, 400)


def _conflict(code='GOAL_CONFLICT', message='目标状态已变化，请刷新后重试。'):
    return DomainError(code, message, 409)


def _not_found():
    return DomainError('NOT_FOUND', '未找到可访问的数据', 404)


def _text(value, low=1, high=128):
    if type(value) is not str or not low <= len(value) <= high:
        raise _bad()
    return value


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _check(value, depth=0, key=None):
    if depth > MAX_DEPTH:
        raise _bad('INVALID_ARGUMENT', '目标数据层级过深。')
    if key is not None and key.endswith('_cents'):
        if type(value) is not int or not MIN_CENTS <= value <= MAX_CENTS:
            raise _bad('INVALID_AMOUNT', '金额须为1到100000000之间的整数分。')
        return
    kind = type(value)
    if kind is dict:
        if len(value) > MAX_ITEMS:
            raise _bad()
        for k, v in value.items():
            if type(k) is not str or not 1 <= len(k) <= 64:
                raise _bad()
            norm = re.sub(r'[\s\-]+', '_', k.strip().lower())
            if norm in SECRET_EXACT or any(p in norm for p in SECRET_PARTS):
                raise _bad('SECRET_REJECTED', '目标不保存密码、验证码或密钥。')
            _check(v, depth + 1, norm)
    elif kind is list:
        if len(value) > MAX_ITEMS:
            raise _bad()
        for v in value:
            _check(v, depth + 1)
    elif kind is str:
        if len(value) > MAX_TEXT:
            raise _bad()
    elif value is None or kind is bool:
        return
    elif kind is int:
        if abs(value) > 10 ** 15:
            raise _bad()
    else:
        raise _bad()


def _json(value):
    if type(value) is not dict:
        raise _bad()
    _check(value)
    encoded = _canon(value)
    if len(encoded.encode('utf-8')) > MAX_JSON:
        raise _bad('INVALID_ARGUMENT', '目标数据过大。')
    return encoded


class GoalStore:
    def __init__(self, bank):
        self.bank = bank
        with bank.transaction() as db:
            for stmt in SCHEMA:
                db.execute(stmt)
            bootstrap_controls(bank, db)

    # scoped helpers; all take the caller's open transaction
    def _scope(self, db, user, session_id, epoch):
        _text(user, 1, 64)
        _text(session_id, 1, 256)
        _text(epoch, 0, 128)
        self.bank._user(db, user)
        return _sha('ff-goal-session:' + session_id), self.bank._safety_epoch(db, user)

    def _row(self, db, user, session_hash, goal_id):
        _text(goal_id, 1, 64)
        row = db.execute('SELECT * FROM conversation_goals WHERE id=? AND user_id=? AND session_hash=?',
                         (goal_id, user, session_hash)).fetchone()
        if row is None:
            raise _not_found()
        return row

    def _reload(self, db, row):
        return db.execute('SELECT * FROM conversation_goals WHERE id=?', (row['id'],)).fetchone()

    def _view(self, db, row, epoch, live):
        steps, interrupted = [], False
        for s in db.execute('SELECT * FROM conversation_goal_steps WHERE goal_id=? ORDER BY seq', (row['id'],)):
            state = s['state']
            if state == 'STARTED' and s['boot'] != BOOT:
                state, interrupted = 'UNKNOWN', True
            steps.append({'seq': s['seq'], 'action': s['action'], 'args': json.loads(s['args']),
                          'effect_key': s['effect_key'], 'state': state,
                          'result': None if s['result'] is None else json.loads(s['result'])})
        view = {'id': row['id'], 'kind': row['kind'], 'status': row['status'], 'phase': row['phase'],
                'version': row['version'], 'slots': json.loads(row['slots']),
                'observations': json.loads(row['observations']), 'question': row['question'],
                'steps': steps, 'paused': bool(row['paused']), 'pause_gen': int(row['pause_gen'])}
        if view['status'] == 'ACTIVE' and interrupted:
            view['status'], view['reason'] = 'UNKNOWN', 'STEP_INTERRUPTED'
        if (epoch != row['epoch'] or epoch != live) and view['status'] == 'ACTIVE':
            view['status'], view['reason'] = 'PAUSED', 'RISK_CHANGED'
        if view['paused'] and view['status'] == 'ACTIVE':
            view['status'], view['reason'] = 'PAUSED', 'USER_PAUSED'
        return view

    def _writable(self, row, epoch, live):
        if epoch != row['epoch'] or epoch != live:
            raise _conflict('RISK_CHANGED', '风险状态已变化，目标已暂停，请重新提出需求。')

    def _inflight(self, db, row):
        return db.execute("SELECT 1 FROM conversation_goal_steps WHERE goal_id=? AND state='STARTED'",
                          (row['id'],)).fetchone() is not None

    def _ready(self, db, row, expected_version):
        if row['status'] != 'ACTIVE':
            raise _conflict('GOAL_LOCKED', '目标当前不可继续修改或推进。')
        if row['paused']:
            raise _conflict('GOAL_PAUSED', '目标已暂停，请先恢复后再继续。')
        if type(expected_version) is not int or expected_version != row['version']:
            raise _conflict('VERSION_CONFLICT')
        if self._inflight(db, row):
            raise _conflict('STEP_IN_FLIGHT', '上一步结果尚未确认，不能继续。')

    def _bump(self, db, row, cols):
        names = list(cols)
        sets = ''.join(name + '=?, ' for name in names)
        cur = db.execute('UPDATE conversation_goals SET ' + sets + 'version=version+1, updated_at=? '
                         'WHERE id=? AND version=?',
                         (*[cols[n] for n in names], self.bank.now().isoformat(), row['id'], row['version']))
        if cur.rowcount != 1:
            raise _conflict('VERSION_CONFLICT')

    # public API
    def create(self, user, session_id, epoch, kind, slots, request_key):
        if type(request_key) is not str or not KEY_RE.match(request_key):
            raise _bad()
        if type(kind) is not str or kind not in KINDS:
            raise _bad('INVALID_KIND', '不支持的目标类型。')
        encoded = _json(slots)
        intent = _sha(_canon({'kind': kind, 'slots': slots}))
        try:
            with self.bank.transaction() as db:
                session_hash, live = self._scope(db, user, session_id, epoch)
                row = db.execute('SELECT * FROM conversation_goals WHERE user_id=? AND session_hash=? '
                                 'AND request_key=?', (user, session_hash, request_key)).fetchone()
                if row is not None:
                    if row['intent_hash'] != intent:
                        raise _conflict('IDEMPOTENCY_CONFLICT', '请求标识已用于不同的目标。')
                    return self._view(db, row, epoch, live)
                if epoch != live:
                    raise _conflict('RISK_CHANGED', '风险状态已变化，请重新提出需求。')
                focus = db.execute('SELECT goal_id FROM conversation_goal_focus WHERE user_id=? AND session_hash=?',
                                   (user, session_hash)).fetchone()
                held = focus is not None and focus['goal_id'] is not None and db.execute(
                    "SELECT 1 FROM conversation_goals WHERE id=? AND user_id=? AND session_hash=? "
                    "AND status IN ('ACTIVE','UNKNOWN')", (focus['goal_id'], user, session_hash)).fetchone()
                if held or db.execute("SELECT 1 FROM conversation_goals WHERE user_id=? AND session_hash=? "
                                      "AND (status='UNKNOWN' OR (status='ACTIVE' AND paused=0))",
                                      (user, session_hash)).fetchone():
                    raise _conflict('GOAL_ACTIVE', '当前会话已有进行中的目标，请先完成或明确切换。')
                goal_id, now = uuid.uuid4().hex, self.bank.now().isoformat()
                db.execute('INSERT INTO conversation_goals(id,user_id,session_hash,epoch,request_key,'
                           'intent_hash,kind,status,phase,version,slots,observations,question,'
                           'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,1,?,?,NULL,?,?)',
                           (goal_id, user, session_hash, epoch, request_key, intent, kind, 'ACTIVE',
                            'COLLECTING', encoded, '{}', now, now))
                db.execute('INSERT OR REPLACE INTO conversation_goal_focus(user_id,session_hash,goal_id,updated_at) '
                           'VALUES(?,?,?,?)', (user, session_hash, goal_id, now))
                return self._view(db, self._row(db, user, session_hash, goal_id), epoch, live)
        except sqlite3.IntegrityError:
            raise _conflict('GOAL_ACTIVE', '当前会话已有进行中的目标，请先完成或明确切换。') from None

    def get(self, user, session_id, epoch, id):
        with self.bank.transaction() as db:
            session_hash, live = self._scope(db, user, session_id, epoch)
            return self._view(db, self._row(db, user, session_hash, id), epoch, live)

    def current(self, user, session_id, epoch):
        with self.bank.transaction() as db:
            session_hash, live = self._scope(db, user, session_id, epoch)
            focus = db.execute('SELECT goal_id FROM conversation_goal_focus WHERE user_id=? AND session_hash=?',
                               (user, session_hash)).fetchone()
            if focus is None or focus['goal_id'] is None:
                return None
            row = db.execute('SELECT * FROM conversation_goals WHERE id=? AND user_id=? AND session_hash=?',
                             (focus['goal_id'], user, session_hash)).fetchone()
            if row is None or row['status'] not in ('ACTIVE', 'UNKNOWN'):
                return None
            return self._view(db, row, epoch, live)

    def update(self, user, session_id, epoch, id, expected_version, patch):
        if type(patch) is not dict or not patch or not set(patch) <= PATCH_FIELDS:
            raise _bad('INVALID_PATCH', '不支持的目标字段。')
        cols = {}
        if 'phase' in patch:
            if type(patch['phase']) is not str or not PHASE_RE.match(patch['phase']):
                raise _bad()
            cols['phase'] = patch['phase']
        if 'question' in patch:
            q = patch['question']
            if q is not None:
                _text(q, 1, 500)
            cols['question'] = q
        if 'slots' in patch:
            cols['slots'] = _json(patch['slots'])
        if 'observations' in patch:
            cols['observations'] = _json(patch['observations'])
        if 'status' in patch:
            if type(patch['status']) is not str or patch['status'] not in PATCH_STATUS:
                raise _bad('INVALID_PATCH', '不支持的目标状态。')
            cols['status'] = patch['status']
        with self.bank.transaction() as db:
            session_hash, live = self._scope(db, user, session_id, epoch)
            row = self._row(db, user, session_hash, id)
            self._writable(row, epoch, live)
            self._ready(db, row, expected_version)
            self._bump(db, row, cols)
            return self._view(db, self._reload(db, row), epoch, live)

    def start_step(self, user, session_id, epoch, id, expected_version, action, args):
        if type(action) is not str or action not in ACTIONS:
            raise DomainError('ACTION_NOT_ALLOWED', '该步骤不在目标允许范围内。', 403)
        encoded = _json(args)
        token = secrets.token_urlsafe(32)
        try:
            with self.bank.transaction() as db:
                session_hash, live = self._scope(db, user, session_id, epoch)
                row = self._row(db, user, session_hash, id)
                self._writable(row, epoch, live)
                self._ready(db, row, expected_version)
                seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM conversation_goal_steps WHERE goal_id=?',
                                 (row['id'],)).fetchone()[0]
                if seq > MAX_STEPS:
                    raise _conflict('STEP_LIMIT', '目标步骤过多，请重新提出需求。')
                effect_key = 'ffg_' + _sha('%s:%d' % (row['id'], seq))[:40]
                db.execute('INSERT INTO conversation_goal_steps(goal_id,seq,action,args,effect_key,token_hash,'
                           'state,result,boot,created_at,settled_at) VALUES(?,?,?,?,?,?,?,NULL,?,?,NULL)',
                           (row['id'], seq, action, encoded, effect_key, _sha(token), 'STARTED', BOOT,
                            self.bank.now().isoformat()))
                self._bump(db, row, {})
                view = self._view(db, self._reload(db, row), epoch, live)
        except sqlite3.IntegrityError:
            raise _conflict('STEP_IN_FLIGHT', '上一步结果尚未确认，不能继续。') from None
        return view, token

    def finish_step(self, user, session_id, epoch, id, claimtoken, state, result):
        _text(claimtoken, 16, 128)
        if type(state) is not str or state not in SETTLE_STATES:
            raise _bad('INVALID_STATE', '步骤只能记录为已观察或结果未知。')
        with self.bank.transaction() as db:
            session_hash, live = self._scope(db, user, session_id, epoch)
            row = self._row(db, user, session_hash, id)
            step = db.execute('SELECT * FROM conversation_goal_steps WHERE goal_id=? AND token_hash=?',
                              (row['id'], _sha(claimtoken))).fetchone()
            if step is None:
                raise _not_found()
            if step['state'] != 'STARTED':
                return self._view(db, row, epoch, live)  # settled once; never adopt a new result
            # Recording an outcome for an already-dispatched claim is allowed even if risk changed;
            # new dispatch stays blocked in start_step/update and _view reports PAUSED.
            encoded = _json(result)
            cur = db.execute("UPDATE conversation_goal_steps SET state=?, result=?, settled_at=? "
                             "WHERE goal_id=? AND seq=? AND state='STARTED'",
                             (state, encoded, self.bank.now().isoformat(), row['id'], step['seq']))
            if cur.rowcount != 1:
                raise _conflict()
            self._bump(db, row, {'status': 'UNKNOWN'} if state == 'UNKNOWN' else {})
            return self._view(db, self._reload(db, row), epoch, live)
