"""Bounded intent planning. Plans cannot confirm, verify, or execute bank operations."""
import json
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .bank import TZ
from .bill_period import parse_bill_period
from .dialog_state import bill_answer
from .card_intent import parse_card_intent
from .virtual_intent import parse_virtual_intent
from .wealth_intent import parse_wealth_intent
from .language_guard import inspect_language
from .polite_tail import strip_polite_tail
from .memory import clean_excerpt

PROMPT_PATH=Path(__file__).resolve().parent/'prompts'/'planner.zh-CN.txt'


class PlannerError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


ACTION_FIELDS = {
    'bill': {'type','period'}, 'balance': {'type'}, 'subscriptions': {'type'},
    'transfer': {'type','payee','amount_cents'},
    'cancel_subscription': {'type','merchant'}, 'clarify': {'type','message'},
    'cards': {'type'}, 'card_status': {'type','card','action'},
    'card_stop': {'type','card','action'}, 'card_correct': {'type','card'},
    'virtual_card_apply': {'type','account'},
    'wealth': {'type'}, 'wealth_trade': {'type','action','product','account','amount_cents'},
}


def validate_plan(data):
    if not isinstance(data,dict) or set(data)!={'actions'} or not isinstance(data['actions'],list) or not 1<=len(data['actions'])<=6:
        raise PlannerError('INVALID_MODEL_PLAN','模型返回的任务格式无效，请换一种说法。')
    for action in data['actions']:
        if not isinstance(action,dict) or action.get('type') not in ACTION_FIELDS or set(action)!=ACTION_FIELDS[action['type']]:
            raise PlannerError('INVALID_MODEL_PLAN','模型提出了不支持的操作。')
        kind=action['type']
        for field in set(action)-{'type','amount_cents'}:
            if not isinstance(action[field],str) or not 1<=len(action[field])<=300:
                raise PlannerError('INVALID_MODEL_PLAN','任务参数无效。')
        if kind=='transfer' and (type(action['amount_cents']) is not int or not 0<action['amount_cents']<=100000000):
            raise PlannerError('INVALID_MODEL_PLAN','转账金额无效。')
        if kind=='wealth_trade' and (action['action'] not in ('purchase','redeem') or (action['amount_cents'] is not None and (type(action['amount_cents']) is not int or not 0<action['amount_cents']<=100000000))):
            raise PlannerError('INVALID_MODEL_PLAN','模拟理财动作或金额无效。')
        if kind=='bill' and not (re.fullmatch(r'[0-9]{4}(?:-(0[1-9]|1[0-2]))?',action['period'])
                and 1900<=int(action['period'][:4])<=2100):
            raise PlannerError('INVALID_MODEL_PLAN','账单月份或年份无效。')
        if kind in ('card_status','card_stop') and action['action'] not in (
                ('report_lost','restore','any') if kind=='card_stop' else ('report_lost','restore')):
            raise PlannerError('INVALID_MODEL_PLAN','卡片操作无效。')
    return data['actions']


def normalize_numeric_width(text):
    return text.translate(str.maketrans('０１２３４５６７８９．＋－','0123456789.+-'))


def chinese_integer(value):
    digits={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    units={'十':10,'百':100,'千':1000,'万':10000}
    total=section=number=0
    for char in value:
        if char in digits:
            number=digits[char]
        elif char in units:
            unit=units[char]
            if unit==10000:
                total+=(section+number)*unit
                section=0
            else:
                section+=(number or 1)*unit
            number=0
        else:
            raise ValueError('invalid Chinese amount')
    return total+section+number


class Planner:
    def __init__(self,clock=None,client=None):
        self.clock=clock or (lambda:datetime.now(TZ))
        self.client=client
        self.mode=os.getenv('FF_PLANNER_MODE','rules')

    def plan(self,text,context):
        text=normalize_numeric_width(text)
        bill=bill_answer(text,context.get('pending_bill'),self.clock())
        if bill is not None:
            return ([{'type':'clarify','message':bill['clarify']}] if 'clarify' in bill
                    else [{'type':'bill','period':bill['period']}])
        check=inspect_language(text,context)
        if check is not None:
            return [{'type':'clarify','message':check.message}]
        if re.search(r'忽略.{0,10}(安全|规则)|免确认|跳过.{0,5}(验证|确认)|绕过',text):
            return [{'type':'clarify','message':'涉及转账或订阅的操作需要按规则确认，无法跳过。可以正常提出业务需求。'}]
        if (context.get('pending_card') and context.get('pending_transfer')
                and re.fullmatch(r'\s*\d{4}\s*',text)):
            return [{'type':'clarify','message':'这四位是银行卡尾号，还是转账金额？'}]
        if (parse_wealth_intent(text,context) is not None or parse_virtual_intent(text,context) is not None or parse_card_intent(text,context) is not None):
            return self._rules(text,context)
        if re.fullmatch(r'\s*(?:日常|储蓄)(?:账户)?\s*',text):
            return self._rules(text,context)
        # This bank uses CNY yuan. Fill a precisely supplied numeric slot locally;
        # this is independent of model availability, not an outage fallback.
        if re.fullmatch(r'\s*-?\d+(?:\.\d+)?\s*[元块]?\s*',text):
            return self._rules(text,context)
        if (text.strip() in ('确认','同意','继续','好的')
            or re.search(r'(?:给|转给)(?:他|她|那个(?:人|收款人)?|刚才那个人)',text)
            or re.fullmatch(r'\s*(?:还是|改成|改为|改到|再转)\s*\d+(?:\.\d+)?\s*[元块]?\s*',text)):
            return self._rules(text,context)
        if self.mode=='model':
            return self._model(text,context)
        return self._rules(text,context)

    def _rules(self,text,context):
        text=strip_polite_tail(normalize_numeric_width(text))
        check=inspect_language(text,context)
        if check is not None:
            return [{'type':'clarify','message':check.message}]
        card_actions=parse_card_intent(text,context)
        virtual_actions=parse_virtual_intent(text,context)
        if virtual_actions and card_actions:
            card_actions=card_actions if (virtual_actions[0]['type']=='virtual_card_apply'
                                         and all(a['type']=='cards' for a in card_actions)) else None
        wealth_actions=parse_wealth_intent(text,context)
        if (wealth_actions and wealth_actions[0]['type']=='clarify' and context.get('pending_wealth')
                and any(context.get(k) for k in ('pending_transfer','pending_card','pending_virtual_card'))):
            return wealth_actions
        actions=list(card_actions or [])+list(virtual_actions or [])+list(wealth_actions or [])
        if card_actions or virtual_actions or wealth_actions:
            context={k:v for k,v in context.items() if k!='pending_transfer'}
        now=self.clock().astimezone(TZ)
        if re.search(r'账单|花销|消费|支出|花在哪|花了|开销|查账',text):
            bill=parse_bill_period(text,now)
            if 'clarify' in bill:
                return [{'type':'clarify','message':bill['clarify']}]
            actions.append({'type':'bill','period':bill['period']})
        if '余额' in text or '账户还有' in text:
            actions.append({'type':'balance'})
        is_cancel='取消' in text and re.search(r'订阅|会员|续费|视频|音乐|云盘|健身|工具',text)
        if re.search(r'订阅|会员|代扣|续费',text) and (not is_cancel or re.search(r'查看|查询|有哪些|看看',text)):
            actions.append({'type':'subscriptions'})
        if is_cancel:
            match=re.search(r'取消\s*(.+?)(?:的|自动续费|订阅|会员|[，。]|$)',text)
            merchant=match[1].strip() if match else ''
            if not merchant or merchant in ('这个','那个','它'):
                merchant=context.get('selected_merchant','')
            actions.append({'type':'cancel_subscription','merchant':merchant} if merchant else
                           {'type':'clarify','message':'你想取消哪一项订阅？可以在订阅列表中选择具体商户。'})
        pending=context.get('pending_transfer') or {}
        followup=bool(pending) and bool(re.fullmatch(r'\s*-?\d+(?:\.\d+)?\s*[元块]?\s*',text))
        if re.search(r'转账|转给|给.{1,20}转',text) or followup or (pending and re.search(r'还是|改成|改为',text)):
            clauses=re.split(r'[，,；;。]',text)
            starts=[i for i,c in enumerate(clauses) if re.search(r'转账|转给|给.{1,20}转',c)]
            segment='，'.join(clauses[starts[-1] if starts else len(clauses)-1:])
            fragment=clauses[-1]
            payees=list(dict.fromkeys(m[1] for m in re.finditer(r'(?:转给|给)\s*(妈妈|爸爸|林晓|朋友|(?<!\d)\d{11}(?!\d))',segment)))
            long_digits=bool(re.search(r'\d{12,}',segment))
            # Explicit recipient mentions (whitelisted or not) across every transfer clause in this request
            mentions=[]
            for i in starts:
                m=re.search(r'给\s*([^\s，,；;。]{1,20}?)\s*转',clauses[i]) or re.search(r'转给\s*([^\s，,；;。\d零一二两三四五六七八九十百千万]{1,20})',clauses[i])
                if m:
                    mentions.append(m[1].strip())
            mentions=list(dict.fromkeys(x for x in mentions if x))
            if len(mentions)>1 and len(payees)<=1:
                payees=mentions
            if mentions:
                # Never inherit the old pending payee when the user named someone; unknown names must clarify
                payee=payees[0] if len(payees)==1 and mentions==[payees[0]] else ''
            else:
                payee=payees[0] if len(payees)==1 else ('' if payees else pending.get('payee',''))
            cents=None
            scale_hits=list(re.finditer(r'(?<![\d.])(-?)(\d+(?:\.\d+)?)\s*([十百千万])',fragment))
            if scale_hits:
                # Arabic number + Chinese scale: exact Decimal or clarify; never fall back to a prefix/suffix.
                amount_match=chinese=None
                hit=scale_hits[0]
                rest=fragment[hit.end():]
                tail=re.match(r'\s*[元块]?(?!\s*[\d.零一二两三四五六七八九十百千万元块])',rest)
                outside=re.sub(r'(?<!\d)\d{11}(?!\d)','',fragment[:hit.start()]+' '+(rest[tail.end():] if tail else rest))
                if (len(scale_hits)==1 and not hit[1] and tail
                        and not re.search(r'\d|[零一二两三四五六七八九十百千万]+\s*[元块]',outside)):
                    scaled=Decimal(hit[2])*{'十':10,'百':100,'千':1000,'万':10000}[hit[3]]*100
                    if scaled==scaled.to_integral_value() and 0<scaled<=100000000:
                        cents=int(scaled)
            else:
                amount_match=re.search(r'(-?\d+(?:\.\d+)?)\s*(?:元|块)',fragment)
                if not amount_match:
                    amount_match=re.search(r'转\s*(-?\d+(?:\.\d+)?)',fragment)
                if not amount_match and followup:
                    amount_match=re.search(r'(-?\d+(?:\.\d+)?)',fragment)
                chinese=re.search(r'([零一二两三四五六七八九十百千万]+)元',fragment)
            try:
                if amount_match:
                    value=Decimal(amount_match[1])
                    if value>0 and value.as_tuple().exponent>=-2:
                        cents=int(value*100)
                elif chinese:
                    cents=chinese_integer(chinese[1])*100
            except (InvalidOperation,ValueError):
                pass
            if long_digits:
                actions.append({'type':'clarify','message':'收款号码位数不正确，请核对完整的 11 位手机号或在收款人列表中选择。'})
            elif len(payees)>1:
                actions.append({'type':'clarify','message':'检测到多个收款人，请一次只给一位收款人转账。'})
            elif not payee or cents is None or cents<=0:
                actions.append({'type':'clarify','message':'请明确收款人和金额，例如“给妈妈转 300 元”。金额需要大于零且最多两位小数。',})
            else:
                actions.append({'type':'transfer','payee':payee,'amount_cents':cents})
        if not actions:
            actions=[{'type':'clarify','message':'我可以分析账单、查询订阅或准备转账。敏感操作请在对应卡片中核对并确认。'}]
        return validate_plan({'actions':actions})

    def _model(self,text,context):
        key=os.getenv('FF_MODEL_API_KEY','')
        base=os.getenv('FF_MODEL_BASE_URL','').rstrip('/')
        model=os.getenv('FF_MODEL_NAME','')
        if not key or not base or not model:
            raise PlannerError('MODEL_NOT_CONFIGURED','真实模型尚未配置，请在项目 .env 中设置模型地址、名称和密钥。')
        parsed=urlparse(base)
        if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1')):
            raise PlannerError('INVALID_MODEL_URL','模型地址须使用 HTTPS，本机模型可使用 HTTP。')
        safe_context={k:context[k] for k in ('pending_transfer','selected_merchant') if k in context}
        virtual_pending=context.get('pending_virtual_card')
        if isinstance(virtual_pending,dict) and isinstance(virtual_pending.get('account'),str):
            safe_context['pending_virtual_card']={'account':clean_excerpt(virtual_pending['account'],300)}
        pending_card=context.get('pending_card')
        if (isinstance(pending_card,dict) and pending_card.get('mode') in ('prepare','stop')
                and pending_card.get('action') in (('report_lost','restore','any')
                    if pending_card['mode']=='stop' else ('report_lost','restore'))):
            safe_context['pending_card']={k:pending_card[k] for k in ('mode','action')}
        wealth_pending=context.get('pending_wealth')
        if isinstance(wealth_pending,dict):
            fields=('action','product','account','amount_cents')
            if all(k in wealth_pending for k in fields):
                try:
                    validated=validate_plan({'actions':[{'type':'wealth_trade',**{k:wealth_pending[k] for k in fields}}]})[0]
                    safe_context['pending_wealth']={k:validated[k] for k in fields}
                except PlannerError:
                    pass
        safe_context['recent_turns']=[{'role':turn['role'],'content':clean_excerpt(turn['content'])} for turn in context.get('recent_turns',[])[-6:]
                                    if isinstance(turn,dict) and turn.get('role') in ('user','assistant') and isinstance(turn.get('content'),str)]
        safe_context['memories']=[{'content':clean_excerpt(note['content'],300),'source':'user_explicit'} for note in context.get('memories',[])[:3]
                                 if isinstance(note,dict) and isinstance(note.get('content'),str)]
        try:
            template=PROMPT_PATH.read_text(encoding='utf-8')
        except (OSError,UnicodeError):
            raise PlannerError('MODEL_PROMPT_UNAVAILABLE','助手提示词文件不可用，未发送新的模型请求。请检查项目文件。') from None
        today=self.clock().astimezone(TZ).date().isoformat()
        system=template.replace('{{business_date}}',today).replace('{{business_month}}',today[:7])
        payload={'model':model,'messages':[{'role':'system','content':system},
                  {'role':'user','content':json.dumps({'request':text,'context':safe_context},ensure_ascii=False)}],
                 'response_format':{'type':'json_object'}}
        effort=os.getenv('FF_MODEL_REASONING_EFFORT','').strip()
        if effort:
            if effort not in ('low','medium','high'):
                raise PlannerError('INVALID_REASONING_EFFORT','模型思考程度配置无效，请使用 low、medium 或 high。')
            payload['reasoning_effort']=effort
        try:
            if self.client:
                response=self.client.post(base+'/chat/completions',headers={'Authorization':'Bearer '+key},json=payload,timeout=25)
            else:
                with httpx.Client(timeout=25,follow_redirects=False) as client:
                    response=client.post(base+'/chat/completions',headers={'Authorization':'Bearer '+key},json=payload)
            response.raise_for_status()
            raw=response.json()['choices'][0]['message']['content']
            if not isinstance(raw,str) or len(raw)>20000:
                raise ValueError('invalid response size')
            return validate_plan(json.loads(raw))
        except PlannerError:
            raise
        except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError):
            raise PlannerError('MODEL_UNAVAILABLE','模型服务暂不可用或返回格式无效，未执行新的业务操作。请稍后重试。') from None
