"""Explicit goal controls: pause, resume, switch. Orchestration metadata only.

One Bank transaction per command; never touches operations, verification challenges,
budgets or accounts, and never confirms/verifies/cancels/prepares anything. Resume only
re-authorizes orchestration; the user must freshly act on any pending card.
"""
import json

from .bank import DomainError
from .conversation_goal import KEY_RE, _bad, _canon, _conflict, _not_found, _sha
from .goal_control_refs import inspect_goal

CONTROL_ACTIONS = frozenset({'pause', 'resume', 'switch'})
LIST_LIMIT = 100


def _key(request_key):
    if type(request_key) is not str or not KEY_RE.fullmatch(request_key):
        raise _bad('INVALID_ARGUMENT', '请求标识无效。')
    return request_key


class GoalControls:
    def __init__(self, bank, store):
        self.bank = bank
        self.store = store

    def _focus(self, db, user, session_hash):
        f = db.execute('SELECT goal_id FROM conversation_goal_focus WHERE user_id=? AND session_hash=?',
                       (user, session_hash)).fetchone()
        return None if f is None else f['goal_id']

    def _set_focus(self, db, user, session_hash, goal_id):
        db.execute('INSERT OR REPLACE INTO conversation_goal_focus(user_id,session_hash,goal_id,updated_at) '
                   'VALUES(?,?,?,?)', (user, session_hash, goal_id, self.bank.now().isoformat()))

    def _stable(self, db, row):
        if row['status'] == 'UNKNOWN':
            raise _conflict('GOAL_UNKNOWN', '目标有结果未知的步骤，只能核对，不能暂停、恢复或切换。')
        if row['status'] != 'ACTIVE':
            raise _conflict('GOAL_LOCKED', '目标已结束，不能再暂停、恢复或切换。')
        if db.execute("SELECT 1 FROM conversation_goal_steps WHERE goal_id=? AND state IN ('STARTED','UNKNOWN')",
                      (row['id'],)).fetchone() is not None:
            raise _conflict('STEP_IN_FLIGHT', '上一步结果尚未确认，请先核对。')
        refs = inspect_goal(self.bank, db, row)
        if refs['unknown']:
            raise _conflict('GOAL_UNKNOWN', '相关业务结果未知，请先核对，不能暂停、恢复或切换。')
        return refs

    def command(self, user, session_id, epoch, goal_id, action, expected_version, request_key, confirmed):
        if type(action) is not str or action not in CONTROL_ACTIONS:
            raise _bad('INVALID_ARGUMENT', '不支持的目标操作。')
        if type(expected_version) is not int or expected_version < 1:
            raise _bad('INVALID_ARGUMENT', '目标版本无效。')
        _key(request_key)
        if confirmed is not True:
            raise _bad('CONFIRMATION_REQUIRED', '请明确确认后再操作。')
        if type(goal_id) is not str or not 1 <= len(goal_id) <= 64:
            raise _bad()
        args_hash = _sha(_canon({'goal_id': goal_id, 'action': action,
                                 'expected_version': expected_version, 'confirmed': True}))
        store = self.store
        with self.bank.transaction() as db:
            session_hash, live = store._scope(db, user, session_id, epoch)
            prior = db.execute('SELECT args_hash,result FROM conversation_goal_controls WHERE user_id=? '
                               'AND session_hash=? AND request_key=?',
                               (user, session_hash, request_key)).fetchone()
            if prior is not None:
                if prior['args_hash'] != args_hash:
                    raise _conflict('IDEMPOTENCY_CONFLICT', '请求标识已用于不同的目标操作。')
                return json.loads(prior['result'])  # reference only; never re-applied
            if epoch != live:
                raise _conflict('RISK_CHANGED', '风险状态已变化，请刷新后重试。')
            row = store._row(db, user, session_hash, goal_id)
            if expected_version != row['version']:
                raise _conflict('VERSION_CONFLICT')
            refs = self._stable(db, row)
            focus = self._focus(db, user, session_hash)
            if action in ('pause', 'switch') and focus != row['id']:
                raise _conflict('GOAL_NOT_FOCUSED', '只能暂停或切换当前目标。')
            if action == 'pause':
                if row['paused']:
                    raise _conflict('GOAL_PAUSED', '目标已暂停。')
                store._bump(db, row, {'paused': 1, 'pause_gen': row['pause_gen'] + 1})
            elif action == 'switch':
                cols = {'paused': 1}
                if not row['paused']:
                    cols['pause_gen'] = row['pause_gen'] + 1
                store._bump(db, row, cols)
                self._set_focus(db, user, session_hash, None)
            else:
                if row['epoch'] != live:
                    raise _conflict('RISK_CHANGED', '风险状态已变化，旧目标不能恢复，请重新提出需求。')
                if not row['paused']:
                    raise _conflict('GOAL_NOT_PAUSED', '目标未暂停，无需恢复。')
                if refs['terminal']:
                    raise _conflict('GOAL_TERMINAL', '相关业务已过期或结束，不能恢复。')
                held = focus is not None and focus != row['id'] and db.execute(
                    "SELECT 1 FROM conversation_goals WHERE id=? AND user_id=? AND session_hash=? "
                    "AND status IN ('ACTIVE','UNKNOWN')", (focus, user, session_hash)).fetchone()
                other = db.execute("SELECT 1 FROM conversation_goals WHERE user_id=? AND session_hash=? AND id<>? "
                                   "AND (status='UNKNOWN' OR (status='ACTIVE' AND paused=0))",
                                   (user, session_hash, row['id'])).fetchone()
                if held or other:
                    raise _conflict('RESUME_BLOCKED', '当前已有其他目标，请先明确切换后再恢复。')
                store._bump(db, row, {'paused': 0})
                self._set_focus(db, user, session_hash, row['id'])
            result = {'goal_id': row['id'], 'action': action, 'recorded': True}
            db.execute('INSERT INTO conversation_goal_controls(user_id,session_hash,request_key,goal_id,action,'
                       'args_hash,result,created_at) VALUES(?,?,?,?,?,?,?,?)',
                       (user, session_hash, request_key, row['id'], action, args_hash, _canon(result),
                        self.bank.now().isoformat()))
            return result

    def list_paused(self, user, session_id, epoch):
        store = self.store
        with self.bank.transaction() as db:
            session_hash, live = store._scope(db, user, session_id, epoch)
            rows = db.execute("SELECT * FROM conversation_goals WHERE user_id=? AND session_hash=? "
                              "AND status='ACTIVE' AND paused=1 ORDER BY updated_at DESC, id LIMIT ?",
                              (user, session_hash, LIST_LIMIT)).fetchall()
            return [store._view(db, r, epoch, live) for r in rows]

    def lookup(self, user, session_id, epoch, request_key):
        _key(request_key)
        with self.bank.transaction() as db:
            session_hash, _live = self.store._scope(db, user, session_id, epoch)
            row = db.execute('SELECT result FROM conversation_goal_controls WHERE user_id=? AND session_hash=? '
                             'AND request_key=?', (user, session_hash, request_key)).fetchone()
            if row is None:
                raise _not_found()  # absence is not proof the command did not run
            return json.loads(row['result'])
