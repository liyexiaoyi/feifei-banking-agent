"""Conservative, local combinations of case-derived clues; not a fraud classifier."""
import re
import unicodedata


ADVICE = '请停止按对方指引付款或共享屏幕，自行打开银行或商户官方应用核实。不要向他人提供密码、短信验证码。'
MAX_TEXT_LENGTH = 2000


def assess_request(text):
    # Bound both normalization input and every subsequent regular expression.
    text = unicodedata.normalize('NFKC', text[:MAX_TEXT_LENGTH])
    text = re.sub(r'\s+', '', text[:MAX_TEXT_LENGTH])
    # Record nouns describe existing transactions, not a new payment action.
    evidence = re.sub(
        r'转账记录|转账流水|转账明细|交易记录|交易明细'
        r'|转(?:到|入)?安全账户(?:的)?(?:记录|流水|明细)', '', text
    )
    payment_pattern = (
        r'转(?:账|钱|款|到|入|给|过去|多少|安全账户|[0-9一二三四五六七八九十百千万])'
        r'|汇(?:款|钱|到|入|给)|付款|付费|支付|缴(?:纳|费|款)'
        r'|交(?:钱|费|款|保证金|验资|解冻费)|充值|垫付|打钱'
        r'|(?:把|将)(?:钱|资金|存款).{0,8}打到'
    )
    remote_pattern = r'屏幕共享|共享屏幕|远程控制|远程协助'
    secret_pattern = r'短信验证码|验证码|银行卡密码|支付密码|网银密码|登录密码|密码'

    # Keep a later affirmative instruction independent of a preceding negation.
    separator = (
        r'[，,。；;！？!?：:]+'
        r'|但是|不过|然而|然后|随后|接着|并且|同时|但'
        r'|并(?=(?:请|帮我|替我|把|给|向|转|汇|付款|支付|充值|交|缴|开启|共享|发|报|填))'
    )
    affirmative_prefix = (
        r'(?:帮我|替我|请(?:你)?(?:帮我|替我)?|我要|我想|我打算|我决定'
        r'|我现在要|现在|马上|立刻|直接)'
    )
    positive_boundary = (
        '(?:' + affirmative_prefix
        + r'|再(?:帮我|替我|给|向|把|转|汇|付款|支付|充值))'
    )
    negative_prefix = (
        r'(?:不需要|不应该|不可以|不打算|不能|不必|不要|不用|无需|拒绝'
        r'|不会|不想|没有|从未|从不|并未|未曾|别|没'
        r'|不(?=(?:再|向|给|把|转|汇|付|交|开|共|提|填))'
        r'|(?:没人|没有人|无人)(?:要求|让|叫))'
    )
    negative_action = (
        negative_prefix
        + r'(?:我|我们)?'
        # These nonempty modifier alternatives have distinct prefixes.
        + r'(?:再|去|继续|先|马上|立刻|曾经|已经|直接|要求|让|叫|进行)*'
        + r'(?:(?:把|将|向|给).{0,20}?)?'
        + r'(?:开启|打开|共享|屏幕共享|远程控制|远程协助|转|汇'
        r'|付款|付费|支付|充值|交|缴|垫付|打钱|发给|发送|告诉'
        r'|提供|报给|念给|告知|填写|填|输入|点击|点|下载)'
    )
    negated_instruction = re.compile(negative_action)
    affirmative_boundary = re.compile(positive_boundary)

    def strip_negated_instruction(unit):
        # Scan forward instead of matching an unrestricted trailing expression.
        parts = []
        offset = 0
        while offset < len(unit):
            negative = negated_instruction.search(unit, offset)
            if negative is None:
                parts.append(unit[offset:])
                break
            parts.append(unit[offset:negative.start()])
            affirmative = affirmative_boundary.search(unit, negative.end())
            if affirmative is None:
                break
            offset = affirmative.start()
        return ''.join(parts)

    raw_units = [unit for unit in re.split(separator, evidence) if unit]
    active_units = [strip_negated_instruction(unit) for unit in raw_units]
    # A quoted conditional threat (不…就…) is the other party's coercion,
    # not the user's refusal; keep it aside as context only.
    threat_units = [
        unit for unit in raw_units
        if re.search(r'不[^，,。；;！？!?]{1,24}?就', unit)
        and not re.match(r'(?:我们|我)不', unit)
    ]
    active = '，'.join(unit for unit in active_units if unit)

    def collect(value):
        codes, reasons = [], []

        def has(pattern):
            return bool(re.search(pattern, value))

        def add(condition, code, reason):
            if condition:
                codes.append(code)
                reasons.append(reason)

        payment = has(payment_pattern)
        add(
            payment and (
                (has(r'贷款|借款|放款|信贷') and has(r'验资|保证金|解冻费|刷流水|激活'))
                or has(r'验资|刷流水|解冻费')
            ),
            'LOAN_ADVANCE_FEE',
            '出现验资、刷流水或借款前先付费的线索。',
        )
        add(
            payment and has(r'退款|退费|理赔') and has(r'验资|验证|激活|保证金|解冻'),
            'REFUND_ADVANCE_PAYMENT',
            '退款或理赔被附加了先付款、验资的要求。',
        )
        pay_instruction = has(
            r'(?:先|必须|需要|要).{0,6}(?:转|付款|付费|支付|交钱|缴费)'
            r'|转钱|转账|转给|给.{1,12}转|向.{1,12}(?:转|付款)|汇款|充值'
        )
        add(
            pay_instruction and has(r'客服|来电|电话|对方|平台')
            and has(r'取消|注销|关闭') and has(r'会员|订阅|续费|代扣'),
            'FAKE_CANCELLATION_PAYMENT',
            '取消会员或代扣被要求向他人转钱。',
        )
        add(
            has(remote_pattern) and has(r'客服|对方|会员|退款|理赔|订阅'),
            'REMOTE_ACCESS',
            '办理会员、退款等业务时被要求共享屏幕或接受远程操控。',
        )
        add(
            payment and has(r'刷单|做任务赚|返利任务'),
            'TASK_RECHARGE',
            '刷单或返利任务要求先充值、垫付。',
        )
        add(
            payment and has(r'安全账户'),
            'SAFE_ACCOUNT',
            '对方要求把资金转入所谓的安全账户。',
        )

        # A credential alone is insufficient: require disclosure to another
        # person, or entry into a link supplied by a third party.
        disclosure = r'(?:发给|发送给|告诉|提供给|交给|报给|告知|念给)'
        recipient = r'(?:客服|对方|陌生人|群|他|她|专员)'
        direct_secret = any(
            re.search(
                '(?:' + secret_pattern + r')[^，,。；;！？!?]{0,16}'
                + disclosure + r'[^，,。；;！？!?]{0,10}' + recipient,
                unit,
            )
            or re.search(
                disclosure + recipient + r'[^，,。；;！？!?]{0,16}(?:'
                + secret_pattern + ')',
                unit,
            )
            for unit in re.split(separator, value)
        )
        linked_secret = (
            has(r'客服|对方|陌生人|短信|他发来|她发来')
            and has(r'链接|网址|二维码')
            and has(r'(?:填写|填入|输入|填)[^，,。；;！？!?]{0,20}(?:' + secret_pattern + ')')
        )
        add(
            direct_secret or linked_secret,
            'SECRET_DISCLOSURE',
            '要求向他人提供验证码或密码，或在对方提供的链接中填写密码。',
        )
        add(
            payment and has(r'征信|信用记录')
            and has(r'洗白|内部通道|删除不良记录|消除不良记录|修复不良记录'),
            'CREDIT_REPAIR_PAYMENT',
            '对方以征信洗白、内部通道或消除不良记录为由要求付款。',
        )
        add(
            payment and has(r'投资|理财') and has(r'导师|老师|带单|荐股|投顾')
            and has(r'内部名额|内部资格|专属名额')
            and has(r'资格验证|验证资格|验证资金|验资')
            and has(r'指定账户|私人账户|个人账户|对方账户'),
            'INVESTMENT_QUALIFICATION_PAYMENT',
            '投资指导者以内部门槛或名额为由，要求向指定账户付款验证资格。',
        )
        add(
            payment and has(r'监管账户')
            and has(r'客服|对方|来电|电话|公安|警察|检察院|法院')
            and has(r'风险标记|解除风控|解冻|涉嫌洗钱|资金审查|资金监管|验证完.{0,6}退回'),
            'REGULATORY_ACCOUNT_TRANSFER',
            '对方以风险处置、资金审查或验证后退款为由，要求转入监管账户。',
        )
        return codes, reasons

    explanatory = bool(re.search(
        r'解释|科普|讲讲|什么是|为什么|是诈骗吗|是不是诈骗|安全吗|靠谱吗'
        r'|靠不靠谱|怎么|如何|是否|会不会|能不能|可不可以|是真是假'
        r'|是真的吗|什么|哪些|哪里|在哪|多少|只是想知道|想了解|想咨询'
        r'|咨询|想问|请问|核实途径|核实渠道|[？?]',
        text,
    ))
    active_codes, active_reasons = collect(active)
    # Explain a quoted or negated risk in a consultation, while basing any
    # execution hold on affirmative evidence only.
    codes, reasons = collect(evidence) if explanatory else (active_codes, active_reasons)

    executable = (
        '(?:' + payment_pattern + '|' + remote_pattern
        + r'|开启|打开|提供|发给|发送给|告诉|交给|报给|告知|念给|填写|填入|输入)'
    )
    command = re.compile(
        '(?:' + affirmative_prefix + r'|再)'
        r'(?:先|再|现在|马上|立刻|直接|继续|就)?'
        r'(?:(?:把|将|给|向).{1,16}?)?' + executable
    )
    initial_command = re.compile(
        r'^(?:先|现在|马上|立刻|直接|再)?'
        r'(?:(?:把|将|给|向).{1,16}?)?' + executable
    )

    # Current activity is separate from a request for advice. Anchor it to a
    # short clause so hypothetical questions and negated activity do not match
    # merely because they contain an action word later in the clause.
    current_prefix = r'^(?:我们|我)?(?:现在|目前|此刻|这会儿)?'
    guidance = (
        r'(?:(?:按照|按|照着|跟着)[^，,。；;！？!?]{1,24}?'
        r'(?:说的|给的|发的|要求|指引|指导|提示|步骤)'
        r'(?:账号|账户|户名|收款信息)?)?'
    )
    transaction_page = r'(?:转账|汇款|付款|支付|充值|确认)(?:页面|页|界面)'
    current_operation = (
        '(?:' + payment_pattern + '|' + remote_pattern
        + r'|(?:开启|打开)(?:了)?(?:' + remote_pattern + '|' + transaction_page + ')'
        + r'|提供给|发给|发送给|告诉|交给|报给|告知|念给'
        r'|填写|填入|输入|填单)'
    )
    ongoing = (
        r'(?:正在|正|还在|仍在|在)' + guidance
        + r'(?:先|继续|准备)?'
        + r'(?:(?:把|将|给|向)[^，,。；;！？!?]{1,16}?)?'
        + current_operation
    )
    at_page = (
        r'(?:已经|已|刚刚|刚)?'
        r'(?:到了|到|进入了|进入|打开了|打开|还在|仍在|停在|留在|处于|在)'
        + transaction_page
    )
    repeated_submission = (
        r'(?:正在|正|还在|仍在)?(?:连续|反复|重复)'
        r'(?:(?:点击|点|按)(?:了)?(?:[0-9一二三四五六七八九十两几]{1,8}次)?'
        r'(?:提交|确认)|提交|确认)'
    )
    current_action = re.compile(
        current_prefix + '(?:' + ongoing + '|' + at_page + '|'
        + repeated_submission + ')'
    )
    wants_action = any(
        command.search(unit)
        or initial_command.search(unit)
        or current_action.search(unit)
        for unit in active_units if unit
    )
    # Link a quoted threat only to the user's own affirmative payment command.
    if threat_units and any(
        command.search(unit) or initial_command.search(unit)
        for unit in active_units if unit
    ):
        active_codes, active_reasons = collect(active + '，' + '，'.join(threat_units))
        if not explanatory:
            codes, reasons = active_codes, active_reasons
    if not codes:
        return None
    return {
        'codes': codes,
        'reasons': reasons,
        'advice': ADVICE,
        'hold': bool(active_codes and (not explanatory or wants_action)),
    }
