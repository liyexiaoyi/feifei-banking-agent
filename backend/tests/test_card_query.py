from copy import deepcopy
import pytest

from ff_agent.card_query import normalize_card_text,resolve_card_query


def sample_cards():
    return [
        {'id':'card_a_daily','account_id':'a_daily','name':'日常账户演示卡','masked_number':'DEMO ···· 0826','status':'ACTIVE','version':1,'simulated':True},
        {'id':'card_a_saving','account_id':'a_saving','name':'储蓄账户演示卡','masked_number':'DEMO ···· 1018','status':'LOST','version':2,'simulated':True},
    ]


def test_fullwidth_and_invisible_format_chars_are_normalized_without_dropping_digits():
    assert normalize_card_text(' 尾号０８\u200b２６ ')=='尾号0826'
    assert normalize_card_text('０８ ２６')=='08 26'


@pytest.mark.parametrize('query',['0826','尾号0826','卡尾号０８２６'])
def test_explicit_four_digit_tail_resolves_original_owned_card(query):
    cards=sample_cards()
    found=resolve_card_query(cards,query)
    assert len(found)==1 and found[0] is cards[0]


@pytest.mark.parametrize('query',['08265','826','卡号6222000000000826','6222000000000826','card_a_daily'])
def test_inexact_tail_full_number_and_internal_id_do_not_resolve(query):
    assert resolve_card_query(sample_cards(),query)==[]


def test_aliases_and_exact_display_names_are_supported():
    cards=sample_cards()
    for query in ('日常','日常账户','日常卡','日常那张','日常账户演示卡'):
        assert resolve_card_query(cards,query)==[cards[0]]
    assert resolve_card_query(cards,'储蓄那张')==[cards[1]]


def test_ambiguous_tails_return_every_candidate_without_copying_or_mutating():
    cards=sample_cards()
    cards[1]['masked_number']='DEMO ···· 0826'
    before=deepcopy(cards)
    result=resolve_card_query(cards,'0826')
    assert len(result)==2
    assert result[0] is cards[0] and result[1] is cards[1]
    assert cards==before


@pytest.mark.parametrize('query',['未指定','全部','','银行卡','不认识的卡'])
def test_unspecified_or_unknown_target_never_defaults_to_the_only_card(query):
    assert resolve_card_query(sample_cards()[:1],query)==[]


def test_pronouns_require_an_explicit_trusted_pointer_to_an_owned_card():
    cards=sample_cards()
    for query in ('刚才那张','这张','那张','上次那张'):
        assert resolve_card_query(cards,query)==[]
        assert resolve_card_query(cards,query,recent_card_id='foreign')==[]
        found=resolve_card_query(cards,query,recent_card_id=cards[1]['id'])
        assert len(found)==1 and found[0] is cards[1]


def test_a_five_digit_visible_suffix_is_not_truncated_into_a_four_digit_card_tail():
    cards=sample_cards()
    cards[0]['masked_number']='**** 1234'
    cards[1]['masked_number']='**** 12345'
    assert resolve_card_query(cards,'1234')==[cards[0]]
    assert resolve_card_query(cards,'2345')==[]
