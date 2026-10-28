"""Resolve owned current names and bounded clarification; never confirm."""
import json
from .bank import DomainError,PENDING

FIELDS={'type','action','product','account','amount_cents'}

class WealthDialogue:
    def __init__(self,bank):self.bank=bank

    def handle(self,user,action,key,context,expected_epoch=None,plan_ticket=None):
        try:
            if not isinstance(action,dict) or set(action)!=FIELDS or action['type']!='wealth_trade' or action['action'] not in ('purchase','redeem'):
                raise DomainError('INVALID_OPERATION','这不是可核对的模拟理财需求。',409)
            if any(not isinstance(action[k],str) or not 1<=len(action[k])<=300 for k in ('product','account')):
                raise DomainError('INVALID_OPERATION','产品或账户信息无效。',409)
            if action['amount_cents'] is not None and (type(action['amount_cents']) is not int or not 1<=action['amount_cents']<=100000000):
                raise DomainError('INVALID_AMOUNT','请提供准确的数字金额（元）。')
            with self.bank.transaction() as db:
                self.bank._user(db,user)
                self.bank._assert_plan_ticket(db,user,plan_ticket,key)
                if plan_ticket is not None:
                    row=db.execute('SELECT action FROM plan_store_nodes WHERE plan_id=? AND idx=?',(plan_ticket['id'],plan_ticket['idx'])).fetchone()
                    if row is None or json.loads(row['action'])!=action:
                        raise DomainError('PLAN_OWNERSHIP_CONFLICT','理财需求与当前任务步骤不一致。',409)
                watermark=db.execute('SELECT COALESCE(MAX(rowid),0) FROM operations WHERE user_id=?',(user,)).fetchone()[0]
            accounts=self.bank.snapshot(user)['accounts']
            products=self.bank.wealth(user)['products']
            names={'产品甲':'演示产品甲','产品乙':'演示产品乙'}
            product=[p for p in products if p['name']==names.get(action['product'],action['product'])]
            account=[a for a in accounts if a['name']==action['account']]
            question=('你想选择哪个演示产品？' if len(product)!=1 else
                      '你想使用哪个关联账户？' if len(account)!=1 else
                      '请提供准确的数字金额（元）？' if action['amount_cents'] is None else None)
            if question:
                old=context.get('pending_wealth') or {}
                pending={k:action[k] for k in ('action','product','account','amount_cents')}
                pending.update(started_at=old.get('started_at',self.bank.now().timestamp()),operation_watermark=old.get('operation_watermark',watermark))
                return {'cards':[{'type':'wealth_selection','products':products,'accounts':accounts,'simulated':True}],
                        'lines':[question],'state':'WAITING_USER','working':{'pending_wealth':pending}}
            op=self.bank.prepare_wealth(user,account[0]['id'],product[0]['id'],action['action'],action['amount_cents'],key,expected_epoch,plan_ticket)
            success=op['status']=='SUCCEEDED'
            line=(op['result'] or {}).get('message','请核对原模拟理财回执。') if success else '模拟理财申请已准备，尚未成交。请核对产品、账户和金额；确认后还需模拟验证。'
            return {'cards':[{'type':'operation','operation':op}],'lines':[line],
                    'state':'COMPLETED' if success else 'WAITING_USER' if op['status'] in PENDING else 'BLOCKED','working':{}}
        except DomainError as error:
            return {'cards':[],'lines':[error.message],'state':'BLOCKED','working':{}}
