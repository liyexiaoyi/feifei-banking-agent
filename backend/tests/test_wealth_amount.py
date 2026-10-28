import pytest
from ff_agent.wealth_amount import wealth_amount

@pytest.mark.parametrize('text,cents',[('300元',30000),('三百元',30000),('一千二百元',120000),('一百零二元',10200),('两百元',20000),('３００．５０元',30050),('用日常账户买演示产品甲，300元',30000),('帮我来买演示产品甲300元',30000)])
def test_exact_amounts_and_ordinary_punctuation(text,cents):
    assert wealth_amount(text)==cents

@pytest.mark.parametrize('text',['三百块五','300元5角','300和200元','三百和二百元','一千二元','二三百元','1,000元','300多元','300元左右','100.123元','-300元','0元'])
def test_ambiguous_fractional_or_multiple_amounts_are_never_truncated(text):
    assert wealth_amount(text) is None

def test_bare_amount_keeps_unit_wording_and_no_full_request_default():
    assert wealth_amount('300块钱',bare=True)==30000
    assert wealth_amount('300',bare=True)==30000
    assert wealth_amount('300') is None
