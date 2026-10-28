"""Synthetic case variants; not a real-world detection benchmark."""
import pytest


def assess(text):
    from ff_agent.safety import assess_request
    return assess_request(text)


@pytest.mark.parametrize('text,code',[
    ('贷款客服说先交验资保证金，帮我给妈妈转300元','LOAN_ADVANCE_FEE'),
    ('放款前需要刷流水，转给妈妈300元','LOAN_ADVANCE_FEE'),
    ('贷款账户冻结要交解冻费，给妈妈转200元','LOAN_ADVANCE_FEE'),
    ('退款客服让我先转300元验证账户，给妈妈转300元','REFUND_ADVANCE_PAYMENT'),
    ('快递理赔要先支付激活费，请转给妈妈200元','REFUND_ADVANCE_PAYMENT'),
    ('客服来电说取消会员要先转钱，给妈妈转300元','FAKE_CANCELLATION_PAYMENT'),
    ('对方让我开启屏幕共享取消会员，请帮我操作','REMOTE_ACCESS'),
    ('客服让我用远程协助办理退款','REMOTE_ACCESS'),
    ('刷单赚佣金要先充值，帮我转给妈妈100元','TASK_RECHARGE'),
    ('公安说钱要转到安全账户，给妈妈转300元','SAFE_ACCOUNT'),
    ('把短信验证码发给客服办理退款','SECRET_DISCLOSURE'),
    ('客 服 来 电 说 退 款 要 先 转 ３００ 元 验 资，给妈妈转３００元','REFUND_ADVANCE_PAYMENT'),
    ('科普完再帮我转300元给妈妈用于贷款验资，这是诈骗吗','LOAN_ADVANCE_FEE'),
])
def test_high_risk_combination_is_explained_and_held(text,code):
    finding=assess(text)
    assert finding is not None
    assert code in finding['codes']
    assert finding['hold'] is True
    assert finding['reasons'] and finding['advice']


@pytest.mark.parametrize('text',[
    '给妈妈转300元','查询本月账单','查退款是否计入本月账单',
    '取消流光视频的自动续费','我需要偿还银行贷款，给妈妈转300元',
    '给妈妈转300元作为房租保证金','查一下银行卡余额',
    '不要共享屏幕，正常取消流光视频会员',
    '客服提醒我在官方应用查看订阅，不要开启屏幕共享',
    '请在官方平台取消流光视频会员，查看交易记录',
    '在官方平台取消会员并退还已支付费用',
    '不要把短信验证码发给客服，正常查询账单',
    '帮我查询账单里贷款验资那笔转账记录',
    '统计本月被骗退款验资的交易明细',
])
def test_regular_business_and_local_negations_are_not_flagged(text):
    assert assess(text) is None


@pytest.mark.parametrize('text',[
    '解释一下贷款验资转账骗局','为什么不要把钱转到安全账户？',
    '客服让我屏幕共享取消会员，这是诈骗吗？',
])
def test_education_and_risk_questions_do_not_create_hold(text):
    finding=assess(text)
    assert finding and finding['hold'] is False


def test_negation_cannot_disable_a_separate_positive_instruction():
    finding=assess('不要共享屏幕。不过客服说退款要验资，帮我给妈妈转300元')
    assert finding and finding['hold'] is True


def test_question_suffix_does_not_hide_direct_payment_command():
    assert assess('先转300元到安全账户，安全吗？')['hold'] is True


def test_read_request_cannot_hide_an_additional_risky_transfer():
    assert assess('查询本月账单，再给妈妈转300元验资')['hold'] is True
    assert assess('查询本月账单，转300元到安全账户')['hold'] is True
