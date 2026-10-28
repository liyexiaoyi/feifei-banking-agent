"""Birthday goal loop: explicit slots, real catalog, observation-driven continuation.

Never confirms, verifies, or cancels bank operations. Only metadata writes and prepare steps.
"""
import hashlib
import re

from ff_agent.bank import DomainError
from ff_agent.birthday_service import BirthdayService
from ff_agent.birthday_goal_view import extract_slots, catalog_choices, render_goal
from ff_agent.birthday_budget import BirthdayBudgets
from ff_agent.birthday_chat_fields import is_birthday_intent
from ff_agent.conversation_subscriptions import SubscriptionGoal, BILL_WORDS, RENEW_WORDS, FAQ, KIND as _SUB_KIND

_ASK = (('recipient_label', '给谁过生日？'), ('event_date', '哪天过生日？'),
        ('budget_cents', '预算多少？'), ('account_id', '用哪个账户付款？'), ('cake', '需要蛋糕吗？'))
_ORD = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '1': 0, '2': 1, '3': 2, '4': 3, '5': 4}
_WAIT = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION')
_RELATED = re.compile(r'确认|进度|核对|生日|花|蛋糕')
_PICK = '选哪束花？'


class _Stop(Exception):
    pass


class GoalLoop:
    def __init__(self, bank, store, run_tool=None):
        self.bank, self.store, self.run_tool = bank, store, run_tool
        self.svc = BirthdayService(bank)
        self.renewals = SubscriptionGoal(bank, store, run_tool)

    # ---- pure observation ----
    def _run(self, user, group, cb):
        return self.run_tool(user, group, cb) if self.run_tool else cb()

    def _flow(self, user, goal):
        fid = (goal.get('observations') or {}).get('flow_id') or next(
            (st['result']['flow_id'] for st in reversed(goal.get('steps') or [])
             if st.get('action') == 'birthday_create' and st.get('state') == 'OBSERVED'
             and (st.get('result') or {}).get('flow_id')), None)
        if not fid:
            return None
        try:
            return self.svc.get(user, fid)
        except Exception:
            return {'status': 'UNKNOWN'}

    def _ops(self, user, flow):
        out = []
        for n in (flow or {}).get('nodes') or []:
            oid = n.get('operation_id') if isinstance(n, dict) else None
            if oid is None:
                continue
            try:
                r = self.bank.receipt(user, oid)
                out.append({**r['operation'], 'status': r['observed_status']})
            except Exception:
                out.append({'id': oid, 'type': '', 'status': 'UNKNOWN', 'unverified': True})
        return out

    def _done(self, g, action, **args):
        return any(st.get('action') == action and st.get('state') == 'OBSERVED'
                   and (st.get('result') or {}).get('flow_id')
                   and all((st.get('args') or {}).get(k) == v for k, v in args.items())
                   for st in g.get('steps') or [])

    def _cakes(self, rem):
        cat = self.svc.catalog('cake') or []
        items = [{**v, 'id': v.get('id', k)} for k, v in cat.items()] if isinstance(cat, dict) else [{**v, 'id': v['item_id']} for v in cat]
        return [{'id': i['id'], 'label': i.get('label') or i.get('name') or i['id'],
                 'price_cents': i['price_cents'], 'quote_version': i.get('quote_version')}
                for i in items if i.get('gift', 'cake') == 'cake'
                and type(i.get('price_cents')) is int and 0 < i['price_cents'] <= rem]

    def _resp(self, user, goal, message=None):
        flow = self._flow(user, goal)
        ops = self._ops(user, flow)
        if goal.get('phase') in _WAIT and flow is None:
            flow = {'status': 'UNKNOWN'}  # missing flow facts => UNKNOWN, never a new flow
        budget = None
        reserved = [o for o in ops if o.get('type') == 'birthday_budget_reserve'
                    and o.get('status') == 'SUCCEEDED' and not o.get('unverified')]
        if reserved:
            ref = None
            r = reserved[-1].get('result')
            if isinstance(r, dict):
                if isinstance(r.get('budget'), dict):
                    ref = r['budget'].get('id')
                ref = ref or r.get('budget_id') or (r.get('id') if 'amount_cents' in r else None)
            if not ref and isinstance(flow, dict):
                ref = flow.get('budget_id') or next((n.get('budget_id') for n in (flow.get('nodes') or [])
                                                     if isinstance(n, dict) and n.get('budget_id')), None)
            budget = False
            bank = getattr(self, 'bank', None) or getattr(self, '_bank', None)
            if isinstance(ref, str) and ref and bank is not None:
                try:
                    budget = BirthdayBudgets(bank).get(user, ref)  # read-only SELECT
                except (DomainError, LookupError, TypeError, ValueError):
                    budget = False
        pub = render_goal(goal, flow, ops, budget)
        paused = bool(goal.get('paused'))
        pub['paused'] = paused
        pub['pause_gen'] = int(goal.get('pause_gen') or 0)
        pub['reason'] = goal.get('reason')
        if paused and pub['status'] != 'UNKNOWN':  # UNKNOWN always wins over PAUSED
            pub['status'], pub['reason'] = 'PAUSED', 'USER_PAUSED'
            if message is None:
                message = '你已暂停目标，已有预留和待确认操作保持原状态；恢复不会自动确认或付款。'
        if message is None:
            st = pub['status']
            message = ('已在本地演示中完成。' if st == 'COMPLETED' else
                       '状态暂不确定，请稍后核对。' if st in ('UNKNOWN', 'BLOCKED', 'PAUSED') else
                       pub['question'] or '正在等待你在卡片中确认。')
        cards = [{'type': 'operation', 'operation': o} for o in ops if not o.get('unverified')]
        return {'message': message, 'cards': cards, 'tasks': [], 'goal': pub}

    def view(self, user, session, epoch, id=None):
        g = self._get(user, session, epoch, id)
        if g and g.get('kind') == 'spending_subscriptions':
            return self.renewals.view(user, session, epoch, id)
        return self._resp(user, g)['goal'] if g else None

    def _get(self, user, session, epoch, id):
        try:
            return self.store.get(user, session, epoch, id) if id else self.store.current(user, session, epoch)
        except DomainError:
            return None

    # ---- effects ----
    def _effect(self, user, s, ep, g, action, args, fn):
        g, tok = self.store.start_step(user, s, ep, g['id'], g['version'], action, args)
        key = g['steps'][-1]['effect_key']
        try:
            flow = self._run(user, 'transfer', lambda: fn(key))
        except DomainError as e:
            self.store.finish_step(user, s, ep, g['id'], tok, 'OBSERVED',
                                   {'refused': str(getattr(e, 'code', 'DOMAIN'))[:40]})
            raise _Stop()
        except Exception:
            self.store.finish_step(user, s, ep, g['id'], tok, 'UNKNOWN', {})
            raise _Stop()
        g = self.store.finish_step(user, s, ep, g['id'], tok, 'OBSERVED',
                                   {'flow_id': flow['id'], 'flow_version': int(flow['version'])})
        return g, flow

    def _upd(self, user, s, ep, g, **patch):
        return self.store.update(user, s, ep, g['id'], g['version'], patch)

    # ---- chat ----
    def handle(self, user, session, epoch, text, key):
        g = self._get(user, session, epoch, None)
        if g is not None and isinstance(text, str) and text.strip():
            wants = set()
            if is_birthday_intent(text):
                wants.add('birthday')
            if (not FAQ.search(text) and any(w in text for w in BILL_WORDS)
                    and any(w in text for w in RENEW_WORDS)):
                wants.add(_SUB_KIND)
            if wants:
                kind = g.get('kind')
                r = (self.renewals._resp((user, session, epoch), g) if kind == _SUB_KIND
                     else self._resp(user, g))
                st = (r.get('goal') or {}).get('status')
                paused = bool(g.get('paused')) or st == 'PAUSED'
                if kind not in wants or paused:
                    if st == 'UNKNOWN':
                        return r  # unknown wins: keep read-only check message
                    return dict(r, message='当前已有一个目标（进行中或已暂停），不会开始新目标。'
                                           '如需开始新目标，请先在目标卡片中点击“切换”，再重新发起。')
        if g is None or g.get('kind') == 'spending_subscriptions':
            renewal = self.renewals.handle(user, session, epoch, text, key)
            if renewal is not None or g is not None:
                return renewal
        if g is None:
            if not is_birthday_intent(text):
                return None
            slots = extract_slots(self.bank, user, text, {})
            rk = 'bg-' + hashlib.sha256(str(key).encode('utf-8')).hexdigest()[:40]
            g = self.store.create(user, session, epoch, 'birthday', slots, rk)
            return self._collect(user, session, epoch, g)
        if g.get('paused'):
            # Paused: read-only view for related text; never reinterpret new text as an old choice.
            return self._resp(user, g) if _RELATED.search(text or '') else None
        phase = g.get('phase')
        if phase == 'COLLECTING':
            slots = extract_slots(self.bank, user, text, g.get('slots'))
            if slots == (g.get('slots') or {}):
                return None
            g = self._upd(user, session, epoch, g, slots=slots)
            return self._collect(user, session, epoch, g)
        if phase == 'CHOOSING':
            return self._choose(user, session, epoch, g, text)
        if _RELATED.search(text or ''):
            return self._resp(user, g)  # chat confirmation / progress check: read only
        return None

    def _collect(self, user, s, ep, g):
        slots = dict(g.get('slots') or {})
        for k, q in _ASK:
            if slots.get(k) is None:
                slots['asked'] = 'cake' if k == 'cake' else k
                g = self._upd(user, s, ep, g, slots=slots, phase='COLLECTING', question=q)
                return self._resp(user, g, q)
        choices = catalog_choices(self.bank, slots['budget_cents'])
        obs = {**(g.get('observations') or {}), 'choices': choices}
        g = self._upd(user, s, ep, g, observations=obs, phase='CHOOSING', question=_PICK)
        return self._resp(user, g, _PICK if choices else '预算内暂无可选花束，换个预算？')

    def _pick(self, text, choices):
        t = text or ''
        for c in choices:
            if c['id'] in t or (c.get('label') and c['label'] in t):
                return c
        m = re.search(r'第\s*([一二三四五1-5])', t) or re.fullmatch(r'\s*([1-5])\s*', t)
        if m:
            i = _ORD[m.group(1)]
            return choices[i] if i < len(choices) else False
        return None

    def _choose(self, user, s, ep, g, text):
        obs = dict(g.get('observations') or {})
        t = text or ''
        cake = obs.get('choices_gift') == 'cake'
        ask = '选哪个蛋糕？' if cake else _PICK
        if re.search(r'不要|不是|别|不选|除了|换掉', t):
            return self._resp(user, g, '请直接说要选哪一个，例如“第二个”。' + ask)
        c = self._pick(t, obs.get('choices') or [])
        if c is None:
            if re.search(r'第|个|花|蛋糕', t):
                c = False
            else:
                return None
        if c is False:
            return self._resp(user, g, '没有这个选项，' + ask)
        pick = {k: c.get(k) for k in ('id', 'price_cents', 'quote_version')}
        if cake:
            g = self._upd(user, s, ep, g, observations={**obs, 'chosen_cake': pick}, phase='READY', question=None)
            return self._resp(user, g, '已记下蛋糕，继续后会准备订单卡片。')
        obs['chosen'] = pick
        flow = self._flow(user, g)
        if flow and flow.get('id'):
            obs['flow_id'] = flow['id']
        g = self._upd(user, s, ep, g, observations=obs)
        sl = g['slots']
        try:
            if not (flow and flow.get('id')):
                if flow is not None:
                    return self._resp(user, g)
                g, flow = self._effect(user, s, ep, g, 'birthday_create', {'item_id': c['id']},
                                       lambda k: self.svc.create(user, sl['recipient_label'], sl['event_date'],
                                                                 sl['account_id'], sl['budget_cents'], k))
                g = self._upd(user, s, ep, g, observations={**obs, 'flow_id': flow['id']})
            ops = self._ops(user, flow)
            if any(o.get('unverified') or o['status'] != 'SUCCEEDED' for o in ops):
                return self._resp(user, g)
            if any(o.get('type') == 'birthday_budget_reserve' for o in ops):
                g = self._upd(user, s, ep, g, phase='READY', question=None)
                return self._resp(user, g, '已更新选择，继续即可准备订单。')
            g, flow = self._effect(user, s, ep, g, 'birthday_advance', {'stage': 'budget'},
                                   lambda k: self.svc.advance(user, flow['id'], flow['version'], k))
            q = '请在卡片中确认预算预留。'
            g = self._upd(user, s, ep, g, phase='WAITING_CONFIRMATION', question=q)
        except _Stop:
            g = self._get(user, s, ep, g['id']) or g
        return self._resp(user, g)

    # ---- continuation ----
    def continue_goal(self, user, session, epoch, id, expected_version, key):
        g = self._get(user, session, epoch, id)
        if g is None:
            raise DomainError('GOAL_NOT_FOUND', '目标不存在', 404)
        if g.get('kind') == 'spending_subscriptions':
            return self.renewals.continue_goal(user, session, epoch, id, expected_version, key)
        if type(expected_version) is not int or g.get('version') != expected_version:
            raise DomainError('VERSION_CONFLICT', '目标已更新', 409)
        flow = self._flow(user, g)
        ops = self._ops(user, flow)
        pub = self._resp(user, g)['goal']  # same budget-aware public view; unverifiable budget => UNKNOWN
        if flow is None or pub['status'] in ('UNKNOWN', 'PAUSED', 'BLOCKED', 'COMPLETED') \
                or flow.get('status') != 'ACTIVE' or any(o['status'] in _WAIT for o in ops):
            return self._resp(user, g)
        st = {o.get('type'): o['status'] for o in ops}
        try:
            if st.get('birthday_order') == 'SUCCEEDED':
                g, flow = self._effect(user, session, epoch, g, 'birthday_advance', {'stage': 'final'},
                                       lambda k: self.svc.advance(user, flow['id'], flow['version'], k))
                g = self._upd(user, session, epoch, g, status='DONE', phase='DONE', question=None)
            elif st.get('birthday_budget_reserve') == 'SUCCEEDED' and 'birthday_order' not in st:
                obs = g.get('observations') or {}
                ch = obs.get('chosen') or {}
                sl = g['slots']
                cur = [c for c in catalog_choices(self.bank, sl['budget_cents'])
                       if c['id'] == ch.get('id') and c['price_cents'] == ch.get('price_cents')
                       and c['quote_version'] == ch.get('quote_version')]
                if not cur:
                    choices = catalog_choices(self.bank, g['slots']['budget_cents'])
                    # Flower requote: reset choice kind and drop the cake pick (new flower price changes remaining budget).
                    obs = {k: v for k, v in obs.items() if k not in ('chosen_cake', 'choices_gift')}
                    g = self._upd(user, session, epoch, g,
                                  observations={**obs, 'choices': choices, 'choices_gift': 'flowers'},
                                  phase='CHOOSING', question=_PICK)
                    return self._resp(user, g, '花束信息有变化，' + _PICK)
                cake = sl['cake'] is True
                if not (isinstance(flow.get('cake_choice'), bool) or self._done(g, 'birthday_choice')):
                    g, flow = self._effect(user, session, epoch, g, 'birthday_choice', {'cake': cake},
                                           lambda k: self.svc.decide_cake(user, flow['id'], cake, flow['version'], k))
                fq = {'item_id': ch['id'], 'quote_version': ch.get('quote_version'), 'price_cents': ch.get('price_cents')}
                if not self._done(g, 'birthday_select', **fq):
                    g, flow = self._effect(user, session, epoch, g, 'birthday_select', dict(fq),
                                           lambda k: self.svc.select_item(user, flow['id'], 'flowers', ch['id'],
                                                                          flow['version'], k))
                if cake:
                    cc = obs.get('chosen_cake')
                    cake_choices = self._cakes(sl['budget_cents'] - ch['price_cents'])
                    if cc and not any(c['id'] == cc['id'] and c['price_cents'] == cc.get('price_cents')
                                      and c['quote_version'] == cc.get('quote_version') for c in cake_choices):
                        refreshed = {k: v for k, v in obs.items() if k != 'chosen_cake'}
                        question = ('蛋糕信息有变化，选哪个蛋糕？' if cake_choices
                                    else '蛋糕信息有变化，当前预算内没有可选蛋糕。请先核对这次安排。')
                        g = self._upd(user, session, epoch, g, phase='CHOOSING', question=question,
                                      observations={**refreshed, 'choices': cake_choices, 'choices_gift': 'cake'})
                        return self._resp(user, g, question)
                    if not cc:
                        choices = self._cakes(sl['budget_cents'] - ch['price_cents'])
                        q = '选哪个蛋糕？' if choices else '剩余预算不够买蛋糕，要不要先只订花？'
                        g = self._upd(user, session, epoch, g, phase='CHOOSING', question=q,
                                      observations={**obs, 'choices': choices, 'choices_gift': 'cake'})
                        return self._resp(user, g, q)
                    cq = {'item_id': cc['id'], 'quote_version': cc.get('quote_version'), 'price_cents': cc.get('price_cents')}
                    if not self._done(g, 'birthday_select', **cq):
                        g, flow = self._effect(user, session, epoch, g, 'birthday_select', dict(cq),
                                               lambda k: self.svc.select_item(user, flow['id'], 'cake', cc['id'],
                                                                              flow['version'], k))
                g, flow = self._effect(user, session, epoch, g, 'birthday_advance', {'stage': 'order'},
                                       lambda k: self.svc.advance(user, flow['id'], flow['version'], k))
                g = self._upd(user, session, epoch, g, phase='WAITING_CONFIRMATION',
                              question='请在卡片中确认下单。')
        except _Stop:
            g = self._get(user, session, epoch, id) or g
        return self._resp(user, g)
