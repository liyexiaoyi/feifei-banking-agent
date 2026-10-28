# Continuous spending + renewal-cleanup goal for 菲菲. It prepares bank operations only;
# every approval stays a separate user gate on the operation card.
import re
from contextvars import ContextVar
from datetime import datetime

from .bank import DomainError
from .bill_period import chinese_month, parse_bill_period

KIND = 'spending_subscriptions'
TOOLS = frozenset({'analyze', 'prepare_subscription_query', 'subscriptions', 'prepare_cancel'})
CONSENT_SECONDS, MAX_CALLS = 1800, 3
BILL_WORDS = ('花', '消费', '支出', '开销', '账单')
RENEW_WORDS = ('续费', '订阅', '代扣', '自动扣')
OFF_TOPIC = ('余额', '转账', '汇款')
# Colloquial balance asks (no renewal word) are interruptions, never merchant answers.
BALANCE_ASK = re.compile(r'(?:还|还有|还剩|剩下?|剩余)(?:多少钱|多少|几块钱)|多少钱了?[？?]?$')
# Definition/difference questions are FAQ: answered elsewhere, never start a goal or prepare permission.
FAQ = re.compile(r'是什么意思|什么意思|啥意思|是什么区别|有什么区别|什么区别|区别是什么|有啥区别|什么是|什么叫|是指什么')
REJECT = re.compile(r'[不别没吗呢？?都全所退留和、,，及或]|还是')
ORDINAL = re.compile(r'(?:选|取消)?第([0-9]{1,2}|[一二三四五六七八九十]{1,3})个?')
NATURAL_PREFIX = r'(?:帮我|请|麻烦)?'
NATURAL_OBJECT = r'(?:的?(?:自动续费|自动扣费|续费|订阅|代扣))?'
NATURAL_STOP = r'(?:停掉|停了|关掉|关了|取消掉|取消|停止)'
LABELS = {'bill': '账单分析', 'subscription_query': '订阅查询授权', 'subscription_read': '读取订阅',
          'subscription_cancel_prepare': '准备取消续费'}
BAD = frozenset({'FAILED', 'EXPIRED', 'CANCELLED', 'REJECTED'})
UNSURE = frozenset({'UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'})
PUBLIC = {'DONE': ('COMPLETED', 'DONE', 'NONE'), 'UNKNOWN': ('UNKNOWN', 'CHECK', 'CHECK'),
          'BLOCKED': ('BLOCKED', 'BLOCKED', 'CHECK'), 'PAUSED': ('PAUSED', 'PAUSED', 'CHECK'),
          'WAIT': ('ACTIVE', 'WAITING_CONFIRMATION', 'WAIT'), 'READY': ('ACTIVE', 'READY', 'CONTINUE'),
          'CHOOSING': ('ACTIVE', 'CHOOSING', 'CHOOSE'), 'EXPIRED': ('ACTIVE', 'READY', 'CONTINUE')}
QUESTIONS = {
    'DONE': '已停止所选订阅的后续自动续费，本期权益保留至到期。这只是停止续费，不是退款。',
    'UNKNOWN': '有一步结果尚未确认，菲菲不会重复发起，请稍后用操作编号核对。',
    'BLOCKED': '这一步未能完成或已失效，请核对操作状态后再决定。',
    'PAUSED': '风险状态已变化，目标已暂停，请重新提出需求。',
    'WAIT': '请在卡片中完成确认；聊天里的“确认”不会被当作授权。',
    'READY': '已收到确认结果，可以继续下一步。',
    'CHOOSING': '这些是你名下仍在生效的订阅，请说出一个要停止续费的商户名称或序号。',
    'EXPIRED': '订阅查询授权已超过30分钟，需要重新授权后才能继续。'}


def _ids(step):
    res = (step or {}).get('result') or {}
    ids = res.get('operation_ids')
    return [i for i in ids if type(i) is str] if type(ids) is list else []


def _ts(value, tz):
    if type(value) in (int, float):
        return float(value)
    try:
        d = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=tz)).timestamp()


def _pick(text, rows):
    exact = [r for r in rows if text in (r['id'], r['merchant'])]
    if exact:
        return exact[0] if len(exact) == 1 else None
    if REJECT.search(text):
        return None
    m = ORDINAL.fullmatch(text)
    if not m:
        return _pick_natural(text, rows)
    n = int(m.group(1)) if m.group(1).isdigit() else chinese_month(m.group(1))
    return rows[n - 1] if n and 1 <= n <= len(rows) else None


def _pick_natural(text, rows):
    # Selection only: anchored templates around one exact owned merchant name.
    hits = []
    for r in rows:
        name = r.get('merchant')
        if type(name) is not str or not name:
            continue
        m = re.escape(name)
        if (re.fullmatch(NATURAL_PREFIX + '取消' + m + NATURAL_OBJECT, text)
                or re.fullmatch(NATURAL_PREFIX + '把?' + m + NATURAL_OBJECT + NATURAL_STOP, text)):
            hits.append(r)
    return hits[0] if len(hits) == 1 else None


def _wait_or_unknown(st):
    if st in BAD:
        return 'BLOCKED'
    if st and (st.startswith('WAITING') or st.startswith('PENDING')):
        return 'WAIT'
    return 'UNKNOWN'


class SubscriptionGoal:
    def __init__(self, bank, store, run_tool=None):
        self.bank, self.store = bank, store
        self.run_tool = run_tool or None
        self._calls = ContextVar('subscription_goal_calls', default=0)

    def _tool(self, name, user, **kw):
        if name not in TOOLS:
            raise DomainError('ACTION_NOT_ALLOWED', '该操作不在目标允许范围内。', 403)
        calls = self._calls.get()
        if calls >= MAX_CALLS:
            raise DomainError('TOOL_LIMIT', '本轮操作次数已达上限，请稍后继续。', 409)
        self._calls.set(calls + 1)
        method = getattr(self.bank, name)

        def cb():
            return method(user, **kw)
        if self.run_tool is None:
            return cb()
        return self.run_tool(user, 'bill' if name == 'analyze' else 'subscriptions', cb)

    # ---- entry points ----
    def handle(self, user, session, epoch, text, key):
        self._calls.set(0)
        if type(text) is not str or not text.strip():
            return None
        ctx = (user, session, epoch)
        g = self.store.current(*ctx)
        if g is not None:
            if g['kind'] != KIND or ((any(w in text for w in OFF_TOPIC) or BALANCE_ASK.search(text))
                                     and not any(w in text for w in RENEW_WORDS)):
                return None
            return self._turn(ctx, g, text.strip())
        if FAQ.search(text):
            return None
        if not (any(w in text for w in BILL_WORDS) and any(w in text for w in RENEW_WORDS)):
            return None
        p = parse_bill_period(text, self.bank.now())
        if 'clarify' in p:
            return {'message': p['clarify'], 'cards': [], 'tasks': [], 'goal': None}
        g = self.store.create(user, session, epoch, KIND, {'period': p['period']}, key)
        if g['steps']:
            return self._resp(ctx, g)
        g, bill = self._effect(ctx, g, 'bill', {'period': p['period']}, lambda k: self._bill(user, p['period']))
        if bill is not None:
            g, _ = self._prepare_query(ctx, g)
        return self._resp(ctx, g, extra=[{'type': 'bill', 'bill': bill}] if bill else [])

    def continue_goal(self, user, session, epoch, id, version, key):
        self._calls.set(0)
        ctx = (user, session, epoch)
        g = self.store.get(*ctx, id)
        if g['kind'] != KIND:
            raise DomainError('NOT_FOUND', '未找到可访问的数据', 404)
        if type(version) is not int or version != g['version']:
            raise DomainError('VERSION_CONFLICT', '目标状态已变化，请刷新后重试。', 409)
        f = self._facts(user, g)
        if f['state'] == 'EXPIRED':
            g, _ = self._prepare_query(ctx, g)
        elif f['state'] == 'READY' and f['ready'] == 'cancel':
            g = self.store.update(*ctx, g['id'], g['version'], {'status': 'DONE', 'phase': 'DONE', 'question': None})
            merchant = (f['c_op'] or {}).get('payload', {}).get('merchant', '所选订阅')
            return self._resp(ctx, g, message='已停止「%s」的后续自动续费，本期权益保留至到期。这只是停止续费，不是退款。' % merchant)
        elif f['state'] == 'READY':
            qid = _ids(f['q'])[0]
            g, _ = self._effect(ctx, g, 'subscription_read', {'operation_id': qid}, lambda k: self._read(user, qid))
        else:
            return self._resp(ctx, g, f)
        return self._resp(ctx, g)

    def view(self, user, session, epoch, id=None):
        try:
            g = self.store.get(user, session, epoch, id) if id else self.store.current(user, session, epoch)
        except DomainError:
            return None
        if not g or g['kind'] != KIND:
            return None
        return self._resp((user, session, epoch), g)['goal']

    # ---- turn logic ----
    def _turn(self, ctx, g, text):
        if g.get('paused'):
            return self._resp(ctx, g)  # paused: read-only, no prepare/choose
        f = self._facts(ctx[0], g)
        if f['state'] == 'EXPIRED':
            g, _ = self._prepare_query(ctx, g)
            return self._resp(ctx, g, message='订阅查询授权已过期，已重新准备查询授权，请在卡片中确认。')
        if f['state'] != 'CHOOSING':
            return self._resp(ctx, g, f)
        pick = _pick(text, f['subs'])
        if pick is None:
            return self._resp(ctx, g, f, '没有识别出唯一的订阅，请说出一个商户名称或序号，例如“第一个”。菲菲不会据此取消或退款。')
        if not pick.get('direct_cancel'):
            return self._resp(ctx, g, f, '「%s」需要到商户应用或门店取消，菲菲无法在这里代办，该订阅当前仍保持有效。' % pick['merchant'])
        sid = pick['id']
        g, _ = self._effect(ctx, g, 'subscription_cancel_prepare', {'subscription_id': sid},
                            lambda k: self._op(self._tool('prepare_cancel', ctx[0], subscription_id=sid, key=k)))
        return self._resp(ctx, g)

    # ---- journaled effects ----
    def _effect(self, ctx, g, action, args, fn):
        g, token = self.store.start_step(*ctx, g['id'], g['version'], action, args)
        key = g['steps'][-1]['effect_key']
        try:
            out, refs = fn(key)
        except DomainError as e:
            code = getattr(e, 'code', None)
            out, refs = None, {'refused': code if type(code) is str else 'REFUSED'}
        except Exception:
            return self.store.finish_step(*ctx, g['id'], token, 'UNKNOWN', {'error': 'UNEXPECTED'}), None
        return self.store.finish_step(*ctx, g['id'], token, 'OBSERVED', refs), out

    def _prepare_query(self, ctx, g):
        n = 1 + sum(1 for s in g['steps'] if s['action'] == 'subscription_query')
        return self._effect(ctx, g, 'subscription_query', {'generation': n},
                            lambda k: self._op(self._tool('prepare_subscription_query', ctx[0], key=k)))

    def _op(self, op):
        return op, {'operation_ids': [op['id']]}

    def _bill(self, user, period):
        b = self._tool('analyze', user, period=period)
        return b, {'period': b['period'], 'evidence_ids': [str(i) for i in b.get('evidence_ids', [])][:100]}

    def _read(self, user, qid):
        rows = self._tool('subscriptions', user)
        active = [r['id'] for r in rows if r.get('status') == 'ACTIVE'][:100]
        return rows, {'operation_ids': [qid], 'subscription_ids': active}

    # ---- read-only facts ----
    def _receipt(self, user, opid):
        try:
            return self.bank.receipt(user, opid)
        except Exception:
            return None

    def _query_at(self, user, opid, receipt):
        tz = self.bank.now().tzinfo
        with self.bank.transaction() as db:
            row = db.execute("SELECT created_at FROM audit WHERE user_id=? AND operation_id=? "
                             "AND event='SUBSCRIPTION_QUERY_SUCCEEDED' ORDER BY created_at DESC LIMIT 1",
                             (user, opid)).fetchone()
        return _ts(row[0], tz) if row else None

    def _facts(self, user, g):
        steps = g['steps']

        def last(action, after):
            return next((s for s in reversed(steps) if s['action'] == action and s['seq'] > after), None)
        q = last('subscription_query', 0)
        r = last('subscription_read', q['seq']) if q else None
        c = last('subscription_cancel_prepare', r['seq']) if r else None
        ops = {}
        for s in steps:
            for oid in _ids(s):
                if oid not in ops:
                    rc = self._receipt(user, oid)
                    ok = (isinstance(rc, dict) and isinstance(rc.get('operation'), dict)
                          and type(rc.get('observed_status')) is str and rc['observed_status'])
                    ops[oid] = rc if ok else None
        f = {'q': q, 'c': c, 'ops': ops, 'subs': [], 'ready': None, 'c_op': None}
        if c and _ids(c) and ops.get(_ids(c)[0]):
            f['c_op'] = ops[_ids(c)[0]]['operation']
        if (g['status'] == 'UNKNOWN' or any(s['state'] != 'OBSERVED' for s in steps)
                or any(v is None or v['observed_status'] in UNSURE for v in ops.values())):
            f['state'] = 'UNKNOWN'
        elif g['status'] == 'DONE':
            rc = ops.get(_ids(c)[0]) if c and _ids(c) else None
            st = rc['observed_status'] if rc else None
            f['state'] = 'DONE' if st == 'SUCCEEDED' else 'BLOCKED' if st in BAD else 'UNKNOWN'
        elif g['status'] == 'PAUSED':
            f['state'] = 'PAUSED'
        elif g['status'] != 'ACTIVE':
            f['state'] = 'BLOCKED'
        else:
            f['state'] = self._stage(user, q, r, c, ops, f)
        return f

    def _stage(self, user, q, r, c, ops, f):
        def obs(step):
            rc = ops.get(_ids(step)[0])
            return rc['observed_status'] if rc else None
        if c and _ids(c):
            st = obs(c)
            if st == 'SUCCEEDED':
                f['ready'] = 'cancel'
                return 'READY'
            return _wait_or_unknown(st)
        if q is None or not _ids(q):
            return 'BLOCKED'
        st = obs(q)
        if st != 'SUCCEEDED':
            return _wait_or_unknown(st)
        at = self._query_at(user, _ids(q)[0], ops.get(_ids(q)[0]))
        if at is None or at + CONSENT_SECONDS <= self.bank.now().timestamp():
            return 'EXPIRED'
        if r is None:
            f['ready'] = 'query'
            return 'READY'
        if 'refused' in (r.get('result') or {}):
            return 'BLOCKED'
        try:
            rows = self.bank.subscriptions(user)
        except DomainError as e:
            return 'EXPIRED' if getattr(e, 'code', None) == 'SUBSCRIPTION_CONSENT_REQUIRED' else 'UNKNOWN'
        except Exception:
            return 'UNKNOWN'
        if type(rows) is not list:
            return 'UNKNOWN'
        keep = set(r['result'].get('subscription_ids') or [])
        f['subs'] = [x for x in rows if isinstance(x, dict) and x.get('id') in keep and x.get('status') == 'ACTIVE'
                     and type(x.get('amount_cents')) is int and x['amount_cents'] >= 0]
        return 'CHOOSING'

    # ---- public rendering ----
    def _resp(self, ctx, g, f=None, message=None, extra=()):
        f = f or self._facts(ctx[0], g)
        status, phase, nxt = PUBLIC[f['state']]
        ops = f['ops']
        cards = list(extra)
        for step in (f['q'], f['c']):
            rc = ops.get(_ids(step)[0]) if _ids(step) else None
            if rc:
                op = dict(rc['operation'])
                op['status'] = rc['observed_status']
                cards.append({'type': 'operation', 'operation': op})
        choices = [{'id': s['id'], 'label': s['merchant'], 'price_cents': s['amount_cents']}
                   for s in f['subs']] if f['state'] == 'CHOOSING' else []
        steps = []
        for s in g['steps']:
            rc = ops.get(_ids(s)[0]) if _ids(s) else None
            steps.append({'label': LABELS.get(s['action'], s['action']),
                          'state': rc['observed_status'] if rc else 'UNKNOWN' if _ids(s) else s['state']})
        bill = next((s for s in g['steps'] if s['action'] == 'bill'), None)
        summary = ['账单周期：%s' % g['slots'].get('period', '')]
        if bill and 'evidence_ids' in (bill.get('result') or {}):
            summary.append('账单依据：%d条流水' % len(bill['result']['evidence_ids']))
        question = QUESTIONS[f['state']]
        if f['state'] == 'CHOOSING' and not choices:
            question = '未发现仍在生效且可处理的订阅。'
        pub = {'id': g['id'], 'version': g['version'], 'kind': KIND, 'status': status, 'phase': phase,
               'title': '账单与续费整理', 'question': question, 'summary': summary, 'choices': choices,
               'steps': steps, 'next_action': nxt, 'operation_ids': list(ops)}
        paused = bool(g.get('paused'))
        pub['paused'] = paused
        pub['pause_gen'] = int(g.get('pause_gen') or 0)
        pub['reason'] = g.get('reason')
        if paused and pub['status'] != 'UNKNOWN':  # UNKNOWN always wins over PAUSED
            pub['status'], pub['reason'] = 'PAUSED', 'USER_PAUSED'
            if message is None:
                message = '你已暂停目标，已有预留和待确认操作保持原状态；恢复不会自动确认或付款。'
        return {'message': message or question, 'cards': cards, 'tasks': [], 'goal': pub}
