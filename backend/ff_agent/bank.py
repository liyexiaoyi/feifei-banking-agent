"""Authoritative simulated bank: permissions never come from the language model."""
import hashlib
import hmac
import json
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import bootstrap, connect
from .plan_ownership import OwnershipConflict, bind_operation, owner_for

TZ = timezone(timedelta(hours=8))
PENDING = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY')


class DomainError(Exception):
    def __init__(self, code, message='操作未完成', status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class Bank:
    def __init__(self, path, clock=None, verification_provider=None):
        if verification_provider is None:
            from .verification_provider import LocalDemoProvider
            verification_provider = LocalDemoProvider()
        if (getattr(verification_provider, 'offline_only', None) is not True
                or getattr(verification_provider, 'simulated', None) is not True
                or not callable(getattr(verification_provider, 'verify', None))):
            raise ValueError('verification provider must be offline_only and simulated with callable verify')
        self.verification_provider = verification_provider
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now(TZ))
        bootstrap(self.path, self.now())
        from .cards import bootstrap_cards
        bootstrap_cards(self)
        from .virtual_cards import bootstrap_virtual_cards
        bootstrap_virtual_cards(self)
        from .card_controls import bootstrap_card_controls
        bootstrap_card_controls(self)
        from .wealth import bootstrap_wealth
        bootstrap_wealth(self)
        from .investment_profile import bootstrap_profiles
        bootstrap_profiles(self)
        from .wealth_suitability import bootstrap_suitability
        bootstrap_suitability(self)
        from .verification import bootstrap_verification
        bootstrap_verification(self)
        from .verification_challenge import bootstrap as bootstrap_verification_challenge
        bootstrap_verification_challenge(self)
        from .reminder_outbox import bootstrap_reminders
        bootstrap_reminders(self)
        from .aa_requests import bootstrap_aa
        bootstrap_aa(self)
        from .birthday_budget import bootstrap_birthday_budgets
        bootstrap_birthday_budgets(self)
        from .birthday_orders import bootstrap_birthday_orders
        bootstrap_birthday_orders(self)
        from .birthday_store import bootstrap_birthday_store
        bootstrap_birthday_store(self)
        from .card_credentials import bootstrap_card_credentials
        bootstrap_card_credentials(self)
        from .scheduled_rules import bootstrap_scheduled
        bootstrap_scheduled(self)

    def now(self):
        return self.clock().astimezone(TZ)

    @contextmanager
    def transaction(self):
        db = connect(self.path)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _user(self, db, user):
        row = db.execute('SELECT * FROM users WHERE id=?', (user,)).fetchone()
        if not row:
            raise DomainError('NOT_FOUND', '未找到可访问的数据', 404)
        return row

    def _unlocked(self, db, user):
        from .verification import check_unlocked
        check_unlocked(db,user,self.now().timestamp())

    def _event(self, db, user, opid, event, detail):
        db.execute('INSERT INTO audit(user_id,operation_id,event,detail,created_at) VALUES(?,?,?,?,?)',
                   (user, opid, event, detail, self.now().isoformat()))

    def _safety_epoch(self, db, user):
        row=db.execute('SELECT id,ended_at FROM safety_holds WHERE user_id=? ORDER BY rowid DESC LIMIT 1',(user,)).fetchone()
        return row['id']+(':active' if row['ended_at'] is None else ':closed') if row else ''

    def _safety_view(self, row):
        return {'id':row['id'], 'codes':json.loads(row['codes']), 'reasons':json.loads(row['reasons']),
                'advice':row['advice'], 'active':row['ended_at'] is None}

    def _safety_hold(self, db, user):
        row=db.execute('SELECT * FROM safety_holds WHERE user_id=? AND ended_at IS NULL',(user,)).fetchone()
        return self._safety_view(row) if row else None

    def _assert_transfer_allowed(self, db, user, expected_epoch=None):
        if self._safety_hold(db,user):
            raise DomainError('SAFETY_HOLD','发现风险线索，转账已暂停。请先结束这次可疑请求，再重新提出独立业务。',409)
        if expected_epoch is not None and expected_epoch!=self._safety_epoch(db,user):
            raise DomainError('REQUEST_SUPERSEDED','该请求早于最近一次风险提示，已失效。请重新提出业务需求。',409)

    def activate_safety_hold(self, user, finding, request_key):
        with self.transaction() as db:
            self._user(db,user)
            existing=db.execute('SELECT * FROM safety_holds WHERE user_id=? AND request_key=?',(user,request_key)).fetchone()
            if existing:
                return self._safety_view(existing)
            ts=self.now().timestamp()
            db.execute('UPDATE safety_holds SET ended_at=? WHERE user_id=? AND ended_at IS NULL',(ts,user))
            holdid='hold_'+uuid.uuid4().hex
            db.execute('INSERT INTO safety_holds VALUES(?,?,?,?,?,?,?,NULL)',
                       (holdid,user,request_key,json.dumps(finding['codes']),json.dumps(finding['reasons'],ensure_ascii=False),finding['advice'],ts))
            self._expire(db,user)
            pending=db.execute("SELECT id FROM operations WHERE user_id=? AND type IN ('transfer','scheduled_transfer_authorize') AND status IN ('WAITING_CONFIRMATION','WAITING_VERIFICATION','READY')",(user,)).fetchall()
            for op in pending:
                db.execute("UPDATE operations SET status='CANCELLED' WHERE id=?",(op['id'],))
                self._event(db,user,op['id'],'TRANSFER_SAFETY_CANCELLED','风险请求触发，取消未执行转账并释放预留')
            wealth=db.execute("SELECT id FROM operations WHERE user_id=? AND type IN ('wealth_purchase','wealth_redemption') AND status IN ('WAITING_CONFIRMATION','WAITING_VERIFICATION','READY')",(user,)).fetchall()
            for op in wealth:
                db.execute("UPDATE operations SET status='CANCELLED' WHERE id=?",(op['id'],))
                self._event(db,user,op['id'],'WEALTH_SAFETY_CANCELLED','风险请求触发，取消未执行模拟理财并释放预留')
            db.execute('DELETE FROM contexts WHERE user_id=?',(user,))
            self._event(db,user,None,'SAFETY_HOLD_ACTIVATED','转账暂停 / '+','.join(finding['codes']))
            return self._safety_view(db.execute('SELECT * FROM safety_holds WHERE id=?',(holdid,)).fetchone())

    def end_safety_hold(self, user, hold_id):
        with self.transaction() as db:
            self._user(db,user)
            row=db.execute('SELECT * FROM safety_holds WHERE id=? AND user_id=?',(hold_id,user)).fetchone()
            if not row:
                raise DomainError('NOT_FOUND','未找到本人可结束的风险请求。',404)
            current=self._safety_hold(db,user)
            if current and current['id']!=hold_id:
                raise DomainError('SAFETY_HOLD_CHANGED','已有新的风险提示，请刷新后核对。',409)
            if row['ended_at'] is None:
                db.execute('UPDATE safety_holds SET ended_at=? WHERE id=?',(self.now().timestamp(),hold_id))
                db.execute('DELETE FROM contexts WHERE user_id=?',(user,))
                self._event(db,user,None,'SAFETY_REQUEST_ENDED','已结束可疑请求；旧转账保持取消，新业务须重新提出')
            return {'ended':True,'hold_id':hold_id}

    def _expire(self, db, user):
        db.execute("UPDATE operations SET status='EXPIRED' WHERE user_id=? AND status IN ('WAITING_CONFIRMATION','WAITING_VERIFICATION','READY') AND expires_at<=?",
                   (user, self.now().timestamp()))
        from .card_credentials import purge_expired
        purge_expired(db,user,self.now().timestamp())

    def _load(self, db, user, opid):
        self._expire(db, user)
        row = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, user)).fetchone()
        if not row:
            raise DomainError('NOT_FOUND', '未找到可访问的操作', 404)
        return dict(row)

    def _view(self, row):
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        result['result'] = json.loads(result['result']) if result['result'] else None
        result.pop('idempotency_key', None)
        result.pop('user_id', None)
        result.pop('attempts', None)
        return result

    def _active_amount(self, db, user, account=None, exclude=None):
        total = 0
        for row in db.execute("SELECT * FROM operations WHERE user_id=? AND type='transfer' AND status IN ('WAITING_CONFIRMATION','WAITING_VERIFICATION','READY','UNKNOWN','EXECUTING','MANUAL_REVIEW') AND (expires_at>? OR status IN ('UNKNOWN','EXECUTING','MANUAL_REVIEW'))",
                              (user, self.now().timestamp())):
            payload = json.loads(row['payload'])
            if row['id'] != exclude and (account is None or payload['account_id'] == account):
                total += payload['amount_cents']
        return total

    def _reserved_amount(self,db,user,account_id,exclude=None):
        from .wealth_reservations import reserved_cash
        from .birthday_budget import reserved_cash as birthday_cash
        from .card_spend_rules import reserved_cash as card_cash
        return self._active_amount(db,user,account_id,exclude)+reserved_cash(db,user,self.now().timestamp(),account_id,exclude)+birthday_cash(db,user,self.now().timestamp(),account_id,exclude)+card_cash(db,user,self.now(),account_id,exclude)

    def _risk(self, db, user, amount, exclude=None):
        settled = 0
        for row in db.execute("SELECT payload FROM operations WHERE user_id=? AND type='transfer' AND status='SUCCEEDED' AND executed_day=?",
                              (user, self.now().date().isoformat())):
            settled += json.loads(row['payload'])['amount_cents']
        from .birthday_orders import daily_exposure
        orders = daily_exposure(db, user, self.now(), exclude)
        from .card_spend_rules import daily_exposure as card_exposure
        cards = card_exposure(db, user, self.now(), exclude)
        return 'RED' if settled + self._active_amount(db, user, exclude=exclude) + orders + cards + amount > 100000 else 'YELLOW'

    def snapshot(self, user):
        with self.transaction() as db:
            profile = self._user(db, user)
            self._expire(db, user)
            accounts = []
            for row in db.execute('SELECT * FROM accounts WHERE user_id=? ORDER BY id', (user,)):
                account = dict(row)
                account.pop('user_id')
                account['reserved_cents'] = self._reserved_amount(db, user, row['id'])
                account['available_cents'] = account['balance_cents'] - account['reserved_cents']
                accounts.append(account)
            operations = [self._view(r) for r in db.execute('SELECT * FROM operations WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 30', (user,))]
            return {'name': profile['name'], 'accounts': accounts, 'operations': operations,
                    'business_date': self.now().date().isoformat(), 'locked': profile['locked_until'] > self.now().timestamp(),
                    'safety_hold':self._safety_hold(db,user), 'safety_epoch':self._safety_epoch(db,user)}

    def analyze(self, user, period):
        if isinstance(period, str) and re.fullmatch(r'[0-9]{4}', period):
            return self.analyze_year(user, period)
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
            raise DomainError('INVALID_PERIOD', '请提供有效月份，例如 2026-10。')
        try:
            start = datetime.strptime(period, '%Y-%m').replace(tzinfo=TZ)
            previous = (start - timedelta(days=1)).strftime('%Y-%m')
        except (ValueError,OverflowError):
            raise DomainError('INVALID_PERIOD', '月份超出可查询范围，请提供有效年份。') from None
        with self.transaction() as db:
            self._user(db, user)
            rows = [dict(r) for r in db.execute('SELECT * FROM transactions WHERE user_id=? AND substr(occurred_at,1,7)=? ORDER BY occurred_at DESC,id', (user,period))]
            prev = db.execute("SELECT COALESCE(SUM(CASE WHEN kind='EXPENSE' THEN amount_cents WHEN kind='REFUND' THEN -amount_cents ELSE 0 END),0) FROM transactions WHERE user_id=? AND substr(occurred_at,1,7)=?", (user,previous)).fetchone()[0]
            categories = {}
            for row in rows:
                if row['kind'] == 'EXPENSE':
                    categories[row['category']] = categories.get(row['category'], 0) + row['amount_cents']
            expense = sum(categories.values())
            refund = sum(r['amount_cents'] for r in rows if r['kind'] == 'REFUND')
            income = sum(r['amount_cents'] for r in rows if r['kind'] == 'INCOME')
            public_rows = [{k: r[k] for k in ('id','kind','amount_cents','category','merchant','occurred_at')} for r in rows]
            self._event(db, user, None, 'BILL_ANALYZED', f'{period} 账单，{len(rows)} 条流水')
            return {'period':period, 'expense_cents':expense, 'refund_cents':refund, 'income_cents':income,
                    'net_expense_cents':expense-refund, 'previous_net_expense_cents':prev,
                    'categories':[{'name':name,'amount_cents':amount} for name,amount in sorted(categories.items(),key=lambda item:-item[1])],
                    'transactions':public_rows, 'evidence_ids':[r['id'] for r in rows],
                    'flags':[{'type':'recurring_candidate','message':'发现订阅类消费，可经授权查询正式代扣关系。'}] if '订阅' in categories else []}

    def analyze_year(self, user, year):
        from .bill_insights import annual_report
        return annual_report(self, user, year)

    def resolve_payee(self, user, query):
        with self.transaction() as db:
            self._user(db, user)
            rows = db.execute('SELECT * FROM payees WHERE user_id=? AND (name=? OR phone=? OR remark=?)', (user,query,query,query)).fetchall()
            return [{'id':r['id'],'name':r['name'],'phone':r['phone'][:3]+'****'+r['phone'][-4:],'remark':r['remark']} for r in rows]

    def _create(self, db, user, kind, payload, risk, key, owner=None, origin='ticket', source_id=None, birthday_binding=None):
        if not isinstance(key,str) or not 1 <= len(key) <= 120:
            raise DomainError('INVALID_KEY', '请求编号无效。')
        if key.startswith('task-') and owner is None:
            raise DomainError('PLAN_CONTEXT_REQUIRED','计划操作须来自已核验的原任务上下文。',409)
        if key.startswith('bday-') or birthday_binding is not None:
            if owner is not None or origin != 'ticket' or source_id is not None:
                raise DomainError('BIRTHDAY_BINDING_INVALID','生日流程的操作来源无法核对。',409)
            from .birthday_tickets import before_create
            before_create(self,db,user,kind,payload,key,birthday_binding)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        existing = db.execute('SELECT * FROM operations WHERE user_id=? AND idempotency_key=?', (user,key)).fetchone()
        if existing:
            if existing['payload'] != encoded or existing['type'] != kind:
                raise DomainError('IDEMPOTENCY_CONFLICT', '同一请求编号的操作内容已改变，请重新发起。', 409)
            if owner_for(db,user,existing['id']) != owner:
                raise DomainError('PLAN_OWNERSHIP_CONFLICT','原操作归属不匹配，请核对已有回执，不要重复提交。',409)
            return self._view(existing)
        opid = 'op_' + uuid.uuid4().hex
        ts = self.now().timestamp()
        db.execute("INSERT INTO operations(id,user_id,type,payload,risk,status,idempotency_key,created_at,expires_at) VALUES(?,?,?,?,?,'WAITING_CONFIRMATION',?,?,?)",
                   (opid,user,kind,encoded,risk,key,ts,ts+300))
        try:
            bind_operation(db,user,opid,owner,origin,source_id)
        except OwnershipConflict:
            raise DomainError('PLAN_OWNERSHIP_CONFLICT','操作归属无法核实，本次准备未保存。',409) from None
        self._event(db,user,opid,'OPERATION_PREPARED',f'{kind} / {risk} / 等待确认')
        if birthday_binding is not None:
            from .birthday_tickets import bind_created
            bind_created(self,db,user,birthday_binding,opid)
        return self._view(db.execute('SELECT * FROM operations WHERE id=?',(opid,)).fetchone())

    def _assert_plan_ticket(self, db, user, ticket, key=None):
        if ticket is None:
            return
        row=db.execute("SELECT p.status,p.risk_epoch,n.state,n.token,n.effect_key FROM plan_store_plans p JOIN plan_store_nodes n ON n.plan_id=p.id WHERE p.user=? AND p.id=? AND n.idx=?",(user,ticket.get('id'),ticket.get('idx'))).fetchone()
        if not row or row['status']!='RUNNING' or row['state']!='RUNNING' or not hmac.compare_digest(row['token'] or '',ticket.get('token','')):
            raise DomainError('PLAN_STOPPED','该计划已暂停或更新，本次操作未重新准备。',409)
        if row['risk_epoch']!=self._safety_epoch(db,user):
            raise DomainError('REQUEST_SUPERSEDED','计划早于最近风险提示，请重新提出业务需求。',409)
        if key != row['effect_key']:
            raise DomainError('PLAN_OWNERSHIP_CONFLICT','准备编号与原任务步骤不一致。',409)
        return (ticket['id'],ticket['idx'])

    def _assert_plan_selection(self, db, user, key, payee_id, amount_cents):
        if not key.startswith('select-'):
            return
        matches=db.execute("SELECT p.id,p.status,p.risk_epoch,n.idx,n.state,n.action,n.result FROM plan_store_plans p JOIN plan_store_nodes n ON n.plan_id=p.id WHERE p.user=? AND n.effect_key=?",(user,key[7:])).fetchall()
        if len(matches)>1:
            raise DomainError('PLAN_OWNERSHIP_CONFLICT','旧选择卡的来源存在歧义，请重新发起任务并核对原操作。',409)
        row=matches[0] if matches else None
        if not row or row['status'] not in ('RUNNING','DONE') or row['state']!='PREPARED':
            raise DomainError('PLAN_STOPPED','原收款人选择所属计划已停止，请重新提出业务需求。',409)
        if row['risk_epoch'] != self._safety_epoch(db,user):
            raise DomainError('REQUEST_SUPERSEDED','原收款人选择早于最近风险提示，请重新提出业务需求。',409)
        action=json.loads(row['action'])
        result=json.loads(row['result']) if row['result'] else {}
        choice=next((c for c in result.get('cards',[]) if c.get('type')=='payees' and c.get('selection_key')==key[7:]),None)
        if not choice or payee_id not in {p['id'] for p in choice.get('candidates',[])}:
            raise DomainError('PLAN_STOPPED','原任务没有这张可用的收款人选择卡。',409)
        query=action.get('payee','')
        payee=db.execute('SELECT id FROM payees WHERE user_id=? AND id=? AND (name=? OR phone=? OR remark=?)',(user,payee_id,query,query,query)).fetchone()
        if action.get('type')!='transfer' or action.get('amount_cents')!=amount_cents or not payee:
            raise DomainError('IDEMPOTENCY_CONFLICT','收款人选择与原计划不一致。',409)
        return (row['id'],row['idx'])

    def list_cards(self, user):
        from .cards import CardService
        return CardService(self).list_cards(user)

    def prepare_card_status(self, user, card_id, action, key, expected_version, plan_ticket=None):
        from .cards import CardService
        return CardService(self).prepare_status(user,card_id,action,key,expected_version,plan_ticket)

    def wealth(self,user):
        from .wealth import WealthService
        return WealthService(self).query(user)

    def prepare_wealth(self,user,account_id,product_id,action,amount_cents,key,expected_epoch=None,plan_ticket=None):
        from .wealth import WealthService
        return WealthService(self).prepare(user,account_id,product_id,action,amount_cents,key,expected_epoch,plan_ticket)

    def prepare_virtual_card(self,user,account_id,key,plan_ticket=None):
        from .virtual_cards import VirtualCardService
        return VirtualCardService(self).prepare(user,account_id,key,plan_ticket)

    def prepare_transfer(self, user, payee_id, amount_cents, key, replace_id=None, expected_epoch=None, plan_ticket=None):
        if isinstance(key,str) and key.startswith('sched-'):
            raise DomainError('INVALID_KEY','该请求编号仅用于已授权的定时付款。',409)
        if type(amount_cents) is not int or not 0 < amount_cents <= 100000000:
            raise DomainError('INVALID_AMOUNT', '金额须为大于零、最多两位小数且不超过 100 万元的数值。')
        with self.transaction() as db:
            owner=self._assert_plan_ticket(db,user,plan_ticket,key)
            self._assert_transfer_allowed(db,user,expected_epoch)
            selection_owner=self._assert_plan_selection(db,user,key,payee_id,amount_cents)
            if selection_owner is not None:
                if owner is not None and owner != selection_owner:
                    raise DomainError('PLAN_OWNERSHIP_CONFLICT','操作来源属于不同任务。',409)
                owner=selection_owner
            origin='select' if selection_owner is not None else 'ticket'
            self._unlocked(db,user)
            self._expire(db,user)
            payee = db.execute('SELECT * FROM payees WHERE user_id=? AND id=?',(user,payee_id)).fetchone()
            if not payee:
                raise DomainError('NOT_FOUND','未找到可访问的收款人',404)
            account = db.execute('SELECT * FROM accounts WHERE user_id=? ORDER BY id LIMIT 1',(user,)).fetchone()
            payload = {'account_id':account['id'],'account_name':account['name'], 'payee_id':payee_id,'payee_name':payee['name'],
                       'payee_phone':payee['phone'][:3]+'****'+payee['phone'][-4:],'amount_cents':amount_cents,'fee_cents':0}
            existing = db.execute('SELECT * FROM operations WHERE user_id=? AND idempotency_key=?',(user,key)).fetchone()
            if existing:
                return self._create(db,user,'transfer',payload,existing['risk'],key,owner,origin)
            if replace_id:
                old=self._load(db,user,replace_id)
                self._assert_pending(db,old)
                if old['type']!='transfer':
                    raise DomainError('INVALID_STATE','仅可修改尚未执行的转账。',409)
                db.execute("UPDATE operations SET status='CANCELLED' WHERE id=?",(replace_id,))
                self._event(db,user,replace_id,'OPERATION_SUPERSEDED','用户更正转账，新操作须重新确认')
            if account['balance_cents'] - self._reserved_amount(db,user,account['id']) < amount_cents:
                raise DomainError('INSUFFICIENT_FUNDS','可用余额不足，其他待确认转账可能已预留资金。')
            return self._create(db,user,'transfer',payload,self._risk(db,user,amount_cents),key,owner,origin)

    def _subscriptions(self, db, user):
        return [{k:r[k] for k in ('id','merchant','amount_cents','next_charge','status','direct_cancel','description')} for r in
                db.execute('SELECT * FROM subscriptions WHERE user_id=? ORDER BY rowid',(user,))]

    def _consent(self, db, user):
        if self._user(db,user)['subscription_consent'] <= self.now().timestamp():
            raise DomainError('SUBSCRIPTION_CONSENT_REQUIRED','请先确认查询订阅与代扣信息。',403)

    def subscriptions(self, user):
        with self.transaction() as db:
            self._consent(db,user)
            return self._subscriptions(db,user)

    def prepare_subscription_query(self, user, key, proposed_cancel=None, plan_ticket=None):
        with self.transaction() as db:
            owner=self._assert_plan_ticket(db,user,plan_ticket,key)
            self._unlocked(db,user)
            payload={'scope':'本人订阅与代扣','consent_minutes':30}
            if proposed_cancel:
                payload['proposed_cancel']=proposed_cancel
            return self._create(db,user,'subscription_query',payload,'YELLOW',key,owner)

    def _assert_plan_continuation(self, db, user, key):
        if not key.startswith('resume-'):
            return
        source=db.execute('SELECT * FROM operations WHERE user_id=? AND id=?',(user,key[7:])).fetchone()
        if not source or source['status']!='SUCCEEDED' or source['type']!='subscription_query':
            raise DomainError('PLAN_STOPPED','原始订阅查询回执尚不可用于继续处理。',409)
        # Legacy non-plan queries retain their explicit consent path; uncertain
        # legacy plan ownership is quarantined rather than inferred from a key.
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plan_store_plans'").fetchone()
        if not exists:return
        owner=owner_for(db,user,source['id'])
        if owner is None:
            possible=db.execute('SELECT 1 FROM plan_store_plans p JOIN plan_store_nodes n ON n.plan_id=p.id WHERE p.user=? AND n.effect_key=?',(user,source['idempotency_key'])).fetchone()
            if possible:
                raise DomainError('PLAN_OWNERSHIP_CONFLICT','旧查询的任务归属尚未核实，未继续准备取消操作。',409)
            return
        plan=db.execute('SELECT status,risk_epoch FROM plan_store_plans WHERE user=? AND id=?',(user,owner[0])).fetchone()
        if not plan or plan['status'] not in ('RUNNING','DONE') or plan['risk_epoch']!=self._safety_epoch(db,user):
            raise DomainError('PLAN_STOPPED','查询已完成；原任务已暂停或更新，未继续准备取消操作。',409)
        return owner

    def prepare_cancel(self,user,subscription_id,key,plan_ticket=None):
        with self.transaction() as db:
            owner=self._assert_plan_ticket(db,user,plan_ticket,key)
            continuation_owner=self._assert_plan_continuation(db,user,key)
            if continuation_owner is not None:
                if owner is not None and owner != continuation_owner:
                    raise DomainError('PLAN_OWNERSHIP_CONFLICT','续接来源属于不同任务。',409)
                owner=continuation_owner
            self._unlocked(db,user)
            self._consent(db,user)
            sub = db.execute('SELECT * FROM subscriptions WHERE user_id=? AND id=?',(user,subscription_id)).fetchone()
            if not sub:
                raise DomainError('NOT_FOUND','未找到可访问的订阅',404)
            if not sub['direct_cancel']:
                raise DomainError('MERCHANT_ACTION_REQUIRED','该订阅需到商户应用或门店取消，当前仍保持有效。')
            return self._create(db,user,'subscription_cancel',{'subscription_id':sub['id'],'merchant':sub['merchant'],
                                'amount_cents':sub['amount_cents'],'fee_cents':0,'effect':'停止后续自动续费，本期权益保留至到期'},'YELLOW',key,owner,'resume' if key.startswith('resume-') else 'ticket',key[7:] if key.startswith('resume-') else None)

    def _assert_pending(self, db, row):
        if row['status'] == 'EXPIRED':
            db.commit()
            raise DomainError('OPERATION_EXPIRED','操作已过期，预留资金已释放，请重新发起。',409)
        if row['status'] not in PENDING:
            raise DomainError('INVALID_STATE','此操作当前不能继续执行。',409)

    def confirm(self,user,opid):
        with self.transaction() as db:
            row = self._load(db,user,opid)
            if row['status'] == 'SUCCEEDED':
                return self._view(row)
            from .goal_control_refs import guard_goal_operation
            guard_goal_operation(self,db,user,row,'confirm')
            from .birthday_tickets import guard_effect
            guard_effect(self,db,user,row,'confirm')
            if row['type']=='scheduled_transfer_authorize':
                from .scheduled_rules import guard_authorization
                guard_authorization(self,db,user,row,'confirm')
            schedule_binding = None
            if row['type']=='transfer':
                from .scheduled_driver import guard_child
                schedule_binding = guard_child(self,db,user,row,'confirm')
            if row['type']=='transfer':
                self._assert_transfer_allowed(db,user)
            elif row['type'] in ('wealth_purchase','wealth_redemption'):
                from .wealth import WealthService
                WealthService(self).check_epoch(db,user,opid)
                if row['type']=='wealth_purchase':
                    from .wealth_suitability import SuitabilityGuard
                    SuitabilityGuard(self).check(db,user,row)
            self._unlocked(db,user)
            self._assert_pending(db,row)
            if row['status'] == 'WAITING_VERIFICATION':
                # Reconfirming cannot replace the verification already required.
                return self._view(row)
            payload = json.loads(row['payload'])
            risk = (self._risk(db,user,payload['amount_cents'],exclude=opid) if row['type'] in ('transfer','birthday_budget_reserve','birthday_order','card_spend')
                    else 'RED' if row['type'] in ('card_report_lost','card_restore','card_controls_update','card_credential_change','scheduled_transfer_authorize','wealth_purchase','wealth_redemption') else 'YELLOW')
            if schedule_binding and schedule_binding['mode']=='MANUAL':
                risk = 'RED'
            db.execute('UPDATE operations SET confirmed_at=?,risk=? WHERE id=?',(self.now().timestamp(),risk,opid))
            self._event(db,user,opid,'OPERATION_CONFIRMED',f'用户确认 / {risk}')
            if risk == 'RED':
                db.execute("UPDATE operations SET status='WAITING_VERIFICATION' WHERE id=?",(opid,))
                row = db.execute('SELECT * FROM operations WHERE id=?',(opid,)).fetchone()
                from .verification_challenge import issue
                issue(self,db,user,row)
                return self._view(row)
            return self._execute(db,user,row)

    def verify(self,user,opid,code):
        from .verification import record_failure, record_success

        with self.transaction() as db:
            row = self._load(db,user,opid)
            if row['status']=='SUCCEEDED':
                return self._view(row)
            from .goal_control_refs import guard_goal_operation
            guard_goal_operation(self,db,user,row,'verify')
            from .birthday_tickets import guard_effect
            guard_effect(self,db,user,row,'verify')
            if row['type']=='scheduled_transfer_authorize':
                from .scheduled_rules import guard_authorization
                guard_authorization(self,db,user,row,'verify')
            if row['type']=='transfer':
                from .scheduled_driver import guard_child
                guard_child(self,db,user,row,'verify')
            if row['type']=='transfer':
                self._assert_transfer_allowed(db,user)
            elif row['type'] in ('wealth_purchase','wealth_redemption'):
                from .wealth import WealthService
                WealthService(self).check_epoch(db,user,opid)
            self._unlocked(db,user)
            self._assert_pending(db,row)
            if row['status']!='WAITING_VERIFICATION' or row['confirmed_at'] is None:
                raise DomainError('CONFIRMATION_REQUIRED','请先核对并确认具体操作。',409)
            from .verification_challenge import authorize
            from .verification_provider import VerifyResult
            verdict = authorize(self,db,user,row,code)
            if verdict is VerifyResult.REJECTED:
                attempts = row['attempts']+1
                db.execute('UPDATE operations SET attempts=? WHERE id=?',(attempts,opid))
                streak = record_failure(db,user,self.now().timestamp())
                self._event(db,user,opid,'VERIFICATION_REJECTED',f'验证未通过 / 连续第 {streak} 次')
                db.commit()
                message=('模拟验证码不正确。敏感操作已暂时锁定10分钟，仍可查看账单和余额。' if streak>=3
                         else '模拟验证码不正确。连续3次错误将锁定敏感操作10分钟。')
                raise DomainError('INVALID_VERIFICATION',message,403)
            if verdict is not VerifyResult.VERIFIED:
                raise DomainError('VERIFICATION_UNKNOWN','验证结果未核实，本次未执行。',409)
            record_success(db,user)
            self._event(db,user,opid,'VERIFICATION_PASSED','模拟验证通过，绑定当前不可变操作')
            db.execute('SAVEPOINT verification_execute')
            try:
                result = self._execute(db,user,row)
            except DomainError as error:
                db.execute('ROLLBACK TO verification_execute')
                db.execute('RELEASE verification_execute')
                self._event(db,user,opid,'EXECUTION_REJECTED',
                            f'模拟验证已通过，但业务校验拒绝 / {error.code} / 本次未执行未扣款')
                db.commit()
                raise
            else:
                db.execute('RELEASE verification_execute')
                return result

    def _execute(self,db,user,row):
        from .birthday_tickets import guard_effect
        guard_effect(self,db,user,row,'execute')
        payload = json.loads(row['payload'])
        opid = row['id']
        if row['type']=='transfer':
            from .scheduled_driver import guard_child
            guard_child(self,db,user,row,'execute')
            self._assert_transfer_allowed(db,user)
            account = db.execute('SELECT * FROM accounts WHERE id=? AND user_id=?',(payload['account_id'],user)).fetchone()
            amount = payload['amount_cents']
            if account['balance_cents']-self._reserved_amount(db,user,account['id'],opid) < amount:
                raise DomainError('INSUFFICIENT_FUNDS','执行前重新检查发现可用余额不足。')
            tid = 'tx_'+uuid.uuid4().hex
            db.execute('UPDATE accounts SET balance_cents=balance_cents-? WHERE id=?',(amount,account['id']))
            db.execute('INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,user,account['id'],'EXPENSE',amount,'转账',payload['payee_name'],self.now().isoformat(),'模拟转账',opid))
            result = {'transaction_id':tid,'amount_cents':amount,'payee_name':payload['payee_name'],'balance_cents':account['balance_cents']-amount,'simulated':True}
            self._event(db,user,opid,'TRANSFER_SUCCEEDED',f'模拟转账 {amount} 分 / 流水 {tid}')
        elif row['type']=='scheduled_transfer_authorize':
            from .scheduled_transfers import ScheduledTransfers
            result = ScheduledTransfers(self).activate_locked(db,user,row)
        elif row['type']=='birthday_budget_reserve':
            from .birthday_budget import BirthdayBudgets
            result = BirthdayBudgets(self).execute(db,user,row)
        elif row['type']=='birthday_order':
            from .birthday_orders import BirthdayOrders
            result = BirthdayOrders(self).execute(db,user,row)
        elif row['type']=='subscription_query':
            db.execute('UPDATE users SET subscription_consent=? WHERE id=?',(self.now().timestamp()+1800,user))
            result = {'subscriptions':self._subscriptions(db,user),'consent_minutes':30}
            self._event(db,user,opid,'SUBSCRIPTION_QUERY_SUCCEEDED','经确认查询本人订阅')
        elif row['type'] in ('card_report_lost','card_restore'):
            from .cards import CardService
            result=CardService(self).execute(db,user,row)
        elif row['type']=='card_controls_update':
            from .card_controls import CardControls
            result=CardControls(self).execute(db,user,row)
        elif row['type']=='card_spend':
            from .card_spend import CardSpend
            result=CardSpend(self).execute(db,user,row)
        elif row['type']=='card_credential_change':
            from .card_credentials import CardCredentials
            result=CardCredentials(self).execute(db,user,row)
        elif row['type'] in ('wealth_purchase','wealth_redemption'):
            from .wealth import WealthService
            result=WealthService(self).execute(db,user,row)
        elif row['type']=='virtual_card_create':
            from .virtual_cards import VirtualCardService
            result=VirtualCardService(self).execute(db,user,row)
        elif row['type']=='subscription_cancel':
            self._consent(db,user)
            changed = db.execute("UPDATE subscriptions SET status='CANCELLED' WHERE id=? AND user_id=? AND direct_cancel=1",(payload['subscription_id'],user)).rowcount
            if not changed:
                raise DomainError('NOT_FOUND','未找到可取消的订阅',404)
            result = {'subscription_id':payload['subscription_id'],'merchant':payload['merchant'],'subscription_status':'CANCELLED','simulated':True}
            self._event(db,user,opid,'SUBSCRIPTION_CANCELLED',f"取消 {payload['merchant']} 后续续费")
        else:
            raise DomainError('INVALID_OPERATION','未支持的业务操作，未执行。',409)
        db.execute("UPDATE operations SET status='SUCCEEDED',result=?,executed_day=? WHERE id=?",(json.dumps(result,ensure_ascii=False),self.now().date().isoformat(),opid))
        return self._view(db.execute('SELECT * FROM operations WHERE id=?',(opid,)).fetchone())

    def operation(self,user,opid):
        with self.transaction() as db:
            return self._view(self._load(db,user,opid))

    def receipt(self,user,opid):
        # Query-only mode prevents even expiration bookkeeping on this recovery path.
        db=connect(self.path)
        try:
            db.execute('PRAGMA query_only=ON')
            self._user(db,user)
            row=db.execute('SELECT * FROM operations WHERE id=? AND user_id=?',(opid,user)).fetchone()
            if not row:
                raise DomainError('NOT_FOUND','未找到本人可核对的操作记录。',404)
            operation=self._view(row)
            observed='EXPIRED' if row['status'] in PENDING and row['expires_at']<=self.now().timestamp() else row['status']
            return {'operation':operation,'recorded_status':row['status'],'observed_status':observed,
                    'read_only':True,'simulated':True,'checked_at':self.now().isoformat()}
        finally:
            db.close()

    def cancel(self,user,opid):
        with self.transaction() as db:
            row = self._load(db,user,opid)
            if row['status']=='SUCCEEDED':
                raise DomainError('ALREADY_EXECUTED','此操作已完成。已完成交易需要独立的撤销业务。',409)
            if row['status'] in ('UNKNOWN','EXECUTING'):
                raise DomainError('RESULT_PENDING','操作结果待核实，请使用原操作编号查询。',409)
            if row['status'] in PENDING:
                db.execute("UPDATE operations SET status='CANCELLED' WHERE id=?",(opid,))
                if row['type']=='card_credential_change':
                    from .card_credentials import discard_candidate
                    discard_candidate(db,user,opid)
                self._event(db,user,opid,'OPERATION_CANCELLED','用户取消未执行操作，释放预留')
            return self._view(db.execute('SELECT * FROM operations WHERE id=?',(opid,)).fetchone())

    def audit(self,user):
        with self.transaction() as db:
            self._user(db,user)
            return [dict(r) for r in db.execute('SELECT id,operation_id,event,detail,created_at FROM audit WHERE user_id=? ORDER BY id DESC LIMIT 80',(user,))]
