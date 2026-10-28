"""Pure text boundaries; no persistence, business execution, or external I/O."""
import copy
import re
import unicodedata
from decimal import Decimal

FINANCIAL = frozenset({'bill', 'balance', 'transfer', 'subscriptions', 'cancel_subscription',
                       'cards', 'card_status', 'card_stop', 'card_correct','virtual_card_apply','wealth','wealth_trade'})
TRUSTED = frozenset({'template', 'guarded', 'model_chat', 'rules_chat'})
NEUTRAL = '实际准备/执行结果请看业务卡片和回执，这段文字不构成确认。'
GUARDED = '这段文字无法确认支付、转账、余额或授权状态；请以业务卡片和正式回执为准。'
RULES = (
    ('unsupported', r'黑卡|办(?:理)?银行卡|申请(?:信用卡|借记卡|银行卡)|信用卡.{0,6}(?:额度|审批)|基金|密码|定时|预约转账|退款|退费|开户|销户|refund'),
    ('wealth', r'理财|申购|赎回|持仓|演示产品|收益'),
    ('virtual', r'虚拟(?:银行)?卡'),
    ('card', r'银行卡|卡片|挂失|解挂'),
    ('auth', r'授权|验证码|身份验证|免确认|免验证|冻结|解冻'),
    ('transfer', r'转账|汇款|打款|转给|转钱|给.{1,12}[转打汇]|转\s*[¥￥\d]|(?:给|还)\s*(?:妈妈|爸爸|林晓|朋友)\s*\d|\b(?:transfer|send)\s+\d'),
    ('bill', r'账单|流水|收支明细|交易记录'),
    ('subscription', r'订阅|续费|自动扣费|代扣|会员|退订|subscription'),
    ('balance', r'余额|可用资金|可用余额|balance'),
)
MONEY = re.compile(r'[¥￥]\s*[+-]?\d|[+-]?\d[\d,.]*\s*(?:元|块钱|人民币)')
CHAT = re.compile(r'你好|您好|早上好|晚上好|晚安|天气|气温|下雨|昵称|名字|取名|起名|称呼|叫我|叫你|你叫什么|我叫什么|卡通|转学|谢谢|再见|hello|\bhi\b', re.I)
CONTINUE = re.compile(r'继续|照旧|照做|照常|这家|那个|这个|那笔|这笔|上笔|刚才|收款人|金额|改成|改为|还是|就按|确认|取消|转吧|付吧|给(?:妈妈|爸爸|林晓|朋友)')
FACT = re.compile(
    r'已(?:经)?\s*(?:支付|付款|转账|到账|扣款)|支付成功|转账成功|到账|扣款|'
    r'交易编号|交易号|流水号|(?:余额|账户结余).{0,8}[¥￥$]?[\d一二三四五六七八九十百千万壹贰叁肆伍陆柒捌玖]|'
    r'已(?:经)?.{0,4}(?:解除挂失|挂失|解挂)|(?:挂失|解挂)成功|已(?:经)?撤回.{0,8}(?:挂失|解挂|卡片)|'
    r'(?:申购|赎回)(?:已)?成功|持仓已增加|已(?:经)?.{0,5}(?:申购|赎回|买入|卖出)|'
    r'虚拟(?:银行)?卡.{0,6}(?:申请成功|已开通|已生成|已发放)|已(?:经)?.{0,5}(?:生成|开通|申请).{0,4}虚拟卡|'
    r'(?:免|无需|不需要|跳过).{0,8}(?:确认|授权|验证)|'
    r'\b(?:payment|transfer|balance|success(?:ful(?:ly)?)?|paid|credited|debited)\b', re.I
)


def _text(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value)
                   if unicodedata.category(c) != 'Cf') if isinstance(value, str) else ''


_CLAIM_MONEY = re.compile(
    r'[¥￥$]\s*[+-]?[\d零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]|'
    r'[\d零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟][\d,.]*\s*(?:元|块钱|块|人民币|RMB|CNY)', re.I)
_CLAIM_WORDS = re.compile(
    r'转账|汇款|打款|转出|汇出|转了|汇了|打了|转过去|汇过去|已(?:经)?\s*(?:向|给).{0,12}[转汇打付]|入账|收到|'
    r'余额|结余|账户|账上|还有|还剩|剩余|剩下|可用')
_BARE_AMOUNT = r'\s*[¥￥$]?\s*[\d零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟](?![\d,.零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]*\s*(?:个|次|圈|条|份|张|天|岁|点|号|月|年))'
_CLAIM_STRONG = re.compile(
    r'已(?:经)?\s*(?:向|给).{0,12}?(?:转账|汇款|转出|汇出|转了|汇了)(?:了|过)?' + _BARE_AMOUNT + '|'
    r'(?:向|给).{0,12}?(?:转了|汇了)' + _BARE_AMOUNT + '|'
    r'(?:账户|账上|卡里|卡上).{0,6}?(?:还有|还剩|剩余|剩下|可用|结余)' + _BARE_AMOUNT)
_CLAIM_NUMBER = re.compile(_BARE_AMOUNT)


def _untrusted_fact(value):
    """Bounded heuristic for unverified money/transfer/balance claims in untrusted chat text."""
    normalized = _text(value)
    for text in (normalized, re.sub(r'\s+', '', normalized)):
        if FACT.search(text) or _CLAIM_STRONG.search(text) or ((_CLAIM_MONEY.search(text) or _CLAIM_NUMBER.search(text)) and _CLAIM_WORDS.search(text)):
            return True
    return False


def _kinds(values):
    if isinstance(values, str):
        values = (values,)
    return {v for v in (values or ()) if isinstance(v, str)} & FINANCIAL


def _working(context):
    """Caller supplies Memory.context output after session/epoch/age checks."""
    if not isinstance(context, dict):
        return {}
    slots = {}
    pending = context.get('pending_transfer')
    if isinstance(pending, dict) and pending.get('payee'):
        slots.update(payee=pending['payee'], _transfer=True)
    if context.get('selected_merchant'):
        slots['_subscription'] = True
    if isinstance(context.get('pending_card'), dict):
        slots['_card'] = True
    if isinstance(context.get('pending_virtual_card'), dict):
        slots['_virtual'] = True
    if isinstance(context.get('pending_wealth'),dict):
        slots['_wealth']=True
    return slots


def _scope(request, context, action_kinds):
    request = _text(request).strip()
    kinds = _kinds(action_kinds)
    slots = _working(context)
    category = next((name for name, pattern in RULES if re.search(pattern, request, re.I)), None)
    explicit = bool(category or MONEY.search(request) or re.search(r'银行|金融|支付|付款|存款|贷款|信用卡|账户|账号|人民币|\b(?:bank|account|payment)\b', request, re.I))
    continuation = bool(CONTINUE.search(request) or MONEY.search(request) or
                        re.fullmatch(r'[\d,.]+|妈妈|爸爸|林晓|朋友', request))
    carry = bool(slots and continuation and not (CHAT.search(request) and not explicit))
    financial = bool(kinds or explicit or carry)
    if category is None:
        if 'transfer' in kinds or (carry and slots.get('_transfer')):
            category = 'transfer'
        elif 'bill' in kinds:
            category = 'bill'
        elif kinds & {'subscriptions', 'cancel_subscription'} or (carry and slots.get('_subscription')):
            category = 'subscription'
        elif 'balance' in kinds:
            category = 'balance'
        elif kinds & {'cards','card_status','card_stop','card_correct'} or (carry and slots.get('_card')):
            category = 'card'
        elif 'virtual_card_apply' in kinds or (carry and slots.get('_virtual')):
            category = 'virtual'
        elif kinds & {'wealth','wealth_trade'} or (carry and slots.get('_wealth')):
            category = 'wealth'
    return request, kinds, slots if carry else {}, category, financial


def is_financial_context(request, context, action_kinds=()):
    return _scope(request, context, action_kinds)[-1]


def _amount(request):
    """Consume whole numeric tokens: never salvage a malformed numeric tail."""
    if re.search(r'美元|美金|欧元|港币|\b(?:USD|EUR|HKD|BTC)\b', request, re.I):
        return None, True
    candidates = []
    for match in re.finditer(r'[+-]?\d(?:[\d,._]|\s*[eE/+-]\s*)*', request):
        token = match.group()
        before, after = request[:match.start()], request[match.end():]
        eligible = (re.match(r'\s*(?:元|块钱|人民币)', after) or
                    re.search(r'(?:[¥￥]|转|金额(?:为|是|[:：])?)\s*$', before) or
                    request.strip() == token)
        if eligible:
            candidates.append(token)
    if len(candidates) != 1:
        return None, bool(candidates)
    token = candidates[0]
    if re.fullmatch(r'\d{1,12}(?:\.\d{1,2})?', token) and Decimal(token) > 0:
        return token + '元', True
    return None, True


def _payee(request, slots):
    known = ('妈妈', '爸爸', '林晓', '朋友')
    saved = slots.get('payee')
    saved = _text(saved).strip()
    if not re.fullmatch(r'[\u4e00-\u9fffA-Za-z· ]{1,24}', saved):
        saved = ''
    names = set(known) | ({saved} if saved else set())
    matches = {name for name in names if re.search(r'(?:给|向|收款人(?:是|为|[:：])?)\s*' + re.escape(name) + r'(?=$|[\s，。,.!?！？，]|转|汇|打|\d)', request) or request == name}
    if len(matches) == 1:
        return matches.pop()
    if matches or re.search(r'给|向|收款人', request):
        return None
    return saved or None


def render_clarify(message, request, context, action_kinds=(), mode='model'):
    request, kinds, slots, category, financial = _scope(request, context, action_kinds)
    if not financial:
        message = message if isinstance(message, str) else ''
        normalized = _text(message)
        if message == GUARDED or _untrusted_fact(normalized):
            return GUARDED, 'guarded'
        return message, 'model_chat' if mode == 'model' else 'rules_chat'
    if category == 'transfer':
        if re.search(r'美元|美金|欧元|港币|\b(?:USD|EUR|HKD|BTC)\b', request, re.I):
            return '当前仅处理人民币金额。请明确人民币元数值；本段文字没有执行转账。' + (NEUTRAL if kinds else ''), 'template'
        payee = _payee(request, slots)
        amount, attempted = _amount(request)
        if not attempted and isinstance(slots.get('amount'), (str, int, Decimal)):
            amount, _ = _amount(_text(str(slots['amount'])))
        if re.search(r'不要|别转|取消|不是|而是|改成|改为|或者|还是|分别', request):
            text = '请明确这笔转账的收款人、金额，以及是继续、修改还是取消。'
        elif payee and amount:
            text = f'你提到向{payee}转账{amount}；请核对收款人和金额。'
        elif payee:
            text = f'想给{payee}转多少元？请明确金额。'
        elif amount:
            text = f'这{amount}要转给谁？请提供已登记的收款人。'
        else:
            text = '请明确转账收款人和金额。'
        text += NEUTRAL if kinds else '本轮尚未准备新的转账。'
    else:
        text = {
            'bill': '想查看哪个月的账单？可以说“查本月账单”；数据以已有模拟流水为准。',
            'subscription': '要查询订阅，还是管理某家商户的续费？正式查询需你确认，取消续费还需另行确认。',
            'balance': '余额请查看账户卡；若要重新查询，可以说“查余额”。这段文字不提供新的余额数字。',
            'card': '可查询演示卡、准备挂失或解挂；办理前仍需独立确认和模拟验证。你是要办理申请，还是查询处理结果？',
            'wealth': '这里只提供本机模拟理财。申购与赎回需要明确产品、账户和准确金额，再独立确认与模拟验证；固定演示估值不代表真实报价或预期收益。',
            'virtual': '虚拟演示卡需要选择关联账户，再独立确认；每个模拟账户限一张。你想了解流程，还是准备申请？',
            'unsupported': '当前原型支持模拟转账、月账单、订阅管理及演示卡查询、挂失和解挂；这类额外业务尚未实现。',
            'auth': '请在操作卡核对并完成所需模拟验证；聊天文字不能解除确认和验证要求。',
        }.get(category, '请明确需要办理或查询的金融业务及相关信息。')
        if category == 'bill' and 'bill' in kinds:
            text = '账单查询结果以业务卡片中的月份和统计为准；若需改月，请说明年月。'
        if category == 'balance' and 'balance' in kinds:
            text = '余额以账户卡记录的查询快照为准；如需当前数据，请重新查询。'
        if kinds:
            text += NEUTRAL
    return text, 'template'


def _legacy_business_summary(cards):
    """Reconstruct only facts already present in trusted application cards."""
    lines = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        if card.get('type') == 'bill' and isinstance(card.get('bill'), dict):
            bill = card['bill']
            period, net = bill.get('period'), bill.get('net_expense_cents')
            if isinstance(period, str) and re.fullmatch(r'\d{4}-\d{2}', period) and type(net) is int:
                lines.append(f'账单卡记录{period}净支出{Decimal(net)/100:,.2f}元（模拟流水）。')
        elif card.get('type') == 'balance':
            lines.append('已保留余额查询卡；该卡是当时的查询快照。')
        elif card.get('type') == 'bank_cards':
            lines.append('已保留演示卡查询快照；当前状态请刷新核对。')
        elif card.get('type') in ('wealth','wealth_selection'):
            lines.append('已保留模拟理财查询快照；成交结果请核对原业务回执。')
        elif card.get('type') == 'virtual_accounts':
            lines.append('已保留虚拟卡关联账户查询快照；申请需要另行准备和确认。')
        elif card.get('type') == 'operation':
            lines.append('已保留原业务操作卡；当前执行状态请核对业务回执。')
        elif card.get('type') == 'payees':
            lines.append('已保留原收款人候选卡；其当前可用性以任务和风险状态为准。')
    return lines


def sanitize_legacy_reply(result, request='', context=None, mode='model'):
    if not isinstance(result, dict):
        return result
    tasks = result.get('tasks', [])
    tasks = tasks if isinstance(tasks, list) else []
    clarifies = [t for t in tasks if isinstance(t, dict) and t.get('type') == 'clarify']
    recheck_chat = (any(t.get('reply_source') in ('model_chat','rules_chat') for t in clarifies)
                    and _untrusted_fact(result.get('message', '')))
    if not clarifies or (all(t.get('reply_source') in TRUSTED for t in clarifies) and not recheck_chat):
        return result
    cards = result.get('cards', [])
    cards = cards if isinstance(cards, list) else []
    entries = tasks + [c for c in cards if isinstance(c, dict) and c.get('type') != 'memory']
    kinds = {e.get(key) for e in entries if isinstance(e, dict) for key in ('type', 'kind', 'action_kind') if isinstance(e.get(key), str)} & FINANCIAL
    for card in cards:
        if not isinstance(card, dict):
            continue
        kind = card.get('type')
        if kind in ('bill', 'balance'):
            kinds.add(kind)
        elif kind == 'bank_cards':
            kinds.add('cards')
        elif kind == 'virtual_accounts':
            kinds.add('virtual_card_apply')
        elif kind in ('wealth','wealth_selection'):
            kinds.add('wealth')
        elif kind == 'payees':
            kinds.add('transfer')
        elif kind == 'operation' and isinstance(card.get('operation'), dict):
            kind = card['operation'].get('type')
            if kind == 'transfer':
                kinds.add('transfer')
            elif kind in ('subscription_query', 'subscription_cancel'):
                kinds.add('subscriptions')
            elif kind in ('wealth_purchase','wealth_redemption'):
                kinds.add('wealth_trade')
            elif kind == 'virtual_card_create':
                kinds.add('virtual_card_apply')
            elif kind in ('card_report_lost', 'card_restore'):
                kinds.add('card_status')
    cleaned = copy.deepcopy(result)
    cleaned['message'], source = render_clarify(result.get('message', ''), request, context, kinds, mode)
    summary = _legacy_business_summary(cards)
    if summary:
        cleaned['message'] = '\n'.join(summary + [cleaned['message']])
    for task in cleaned.get('tasks', []):
        if isinstance(task, dict) and task.get('type') == 'clarify' and (task.get('reply_source') not in TRUSTED or recheck_chat):
            task['reply_source'] = source
            if 'message' in task:
                task['message'] = cleaned['message']
    return cleaned
