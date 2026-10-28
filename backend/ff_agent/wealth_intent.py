import re
from .card_query import normalize_card_text
from .wealth_amount import wealth_amount

_PRODUCTS = {'演示产品甲': '演示产品甲', '产品甲': '演示产品甲', '演示产品乙': '演示产品乙', '产品乙': '演示产品乙'}
_ACCOUNTS = ('日常账户', '储蓄账户')
_AMOUNTISH = re.compile(r'^[约大约大概几多零〇一二两三四五六七八九十百千万亿点元块钱角分整人民币半负正0-9０-９.,，￥¥-]+(左右|上下|来)?$')


def _products(s):
    return {value for alias, value in _PRODUCTS.items() if alias in s}


def _accounts(s):
    return {name for name in _ACCOUNTS if name in s}


def _valid_pending(p):
    if not isinstance(p, dict):
        return False
    if p.get('action') not in ('purchase', 'redeem'):
        return False
    if p.get('product') not in ('演示产品甲', '演示产品乙', '未指定'):
        return False
    if p.get('account') not in ('日常账户', '储蓄账户', '未指定'):
        return False
    n = p.get('amount_cents')
    return n is None or (type(n) is int and 1 <= n <= 100_000_000)


def _bare(s):
    if s in ('演示产品甲', '产品甲'):
        return 'product', '演示产品甲'
    if s in ('演示产品乙', '产品乙'):
        return 'product', '演示产品乙'
    if s in _ACCOUNTS:
        return 'account', s
    if len(_products(s)) > 1 or len(_accounts(s)) > 1:
        return 'ambiguous', None
    if _AMOUNTISH.fullmatch(''.join(s.split())):
        return 'amount', None
    return None, None


def parse_wealth_intent(text, context=None) -> list[dict] | None:
    if not isinstance(text, str):
        return None
    s = normalize_card_text(text)
    if not isinstance(s, str) or not s.strip():
        return None
    s = s.strip()
    if isinstance(context,dict) and context.get('pending_wealth') and re.fullmatch(r'(?:算了|不用了)[。.!！\s]*',s):
        return [{'type':'clarify','message':'不会准备新的模拟理财交易；已有申请请在操作卡中取消。'}]
    kind, value = _bare(s)
    if kind:
        ctx = context if isinstance(context, dict) else {}
        pending = ctx.get('pending_wealth')
        if not _valid_pending(pending):
            return None
        if any(ctx.get(k) for k in ('pending_transfer', 'pending_card', 'pending_virtual_card')):
            return [{'type':'clarify','message':'你是在补充哪一项业务的信息？'}]
        if kind == 'ambiguous':
            return [{'type':'clarify','message':'你是想了解理财流程，还是准备一笔明确的模拟交易？'}]
        result = {k: pending.get(k) for k in ('action', 'product', 'account', 'amount_cents')}
        if kind == 'product':
            result['product'] = value
        elif kind == 'account':
            result['account'] = value
        else:
            amount = wealth_amount(s, bare=True)
            result['amount_cents'] = amount if type(amount) is int and 1 <= amount <= 100_000_000 else None
        return [{'type': 'wealth_trade', **result}]

    products, accounts = _products(s), _accounts(s)
    finance = bool(products) or any(x in s for x in ('理财', '持仓', '收益', '演示产品', '申购', '赎回'))
    if finance and ('退款' in s or '退钱' in s):
        return [{'type':'clarify','message':'你是想了解理财流程，还是准备一笔明确的模拟交易？'}]
    blockers = ('取消', '撤回', '不', '不要', '不用', '无需', '不想', '不买', '不申购', '不赎回', '不卖出', '不购买', '别', '先不', '要不要', '能不能', '怎么', '如何', '吗', '是否', '么', '能否', '可否', '可不可以', '行不行', '要不', '是不是', '需不需要', '会不会', '流程', '规则', '手续费', '区别', '是什么', '咨询', '了解')
    if finance and any(x in s for x in blockers):
        return [{'type':'clarify','message':'你是想了解理财流程，还是准备一笔明确的模拟交易？'}]

    buy = any(x in s for x in ('申购', '购买', '买'))
    redeem = any(x in s for x in ('赎回', '卖出'))
    if buy or redeem:
        if not finance:
            return None
        if buy and redeem or len(products) > 1 or len(accounts) > 1:
            return [{'type':'clarify','message':'你是想了解理财流程，还是准备一笔明确的模拟交易？'}]
        amount = wealth_amount(s)
        amount = amount if type(amount) is int and 1 <= amount <= 100_000_000 else None
        return [{'type': 'wealth_trade', 'action': 'purchase' if buy else 'redeem',
                 'product': next(iter(products), '未指定'),
                 'account': next(iter(accounts), '未指定'), 'amount_cents': amount}]

    query = ('查询', '查看', '查', '看看', '看下', '有哪些', '什么', '多少', '显示', '列出')
    if finance and any(x in s for x in query):
        return [{'type': 'wealth'}]
    return None