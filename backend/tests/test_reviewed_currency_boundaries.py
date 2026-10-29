import pytest

from ff_agent.planner import Planner

PENDING = {'pending_transfer': {'payee': '妈妈', 'amount_cents': 20000, 'account_id': 'daily'}}
BAD = ['负300元', '- 300元', '負三百块', '300澳元', '300加元', '300刀',
       '300新币', '300台币', '300泰铢', '300卢布']
PLAIN = ['给妈妈转300元', '给妈妈转三百块钱']
FOLLOW = ['300块钱', '改到300元', '再转三百块']


def _types(plan):
    return [a.get('type') for a in plan]


def _transfers(plan):
    return [a for a in plan if a.get('type') == 'transfer']


def _no_model(*a, **k):
    raise AssertionError('model must not be called')


@pytest.mark.parametrize('amount', BAD)
def test_bad_amounts_clarify_without_transfer(amount):
    plan = Planner().plan('给妈妈转' + amount, {})
    assert not _transfers(plan)
    assert 'clarify' in _types(plan)


@pytest.mark.parametrize('amount', BAD)
def test_bad_amounts_in_pending_followup_clarify(amount):
    plan = Planner().plan(amount, PENDING)
    assert not _transfers(plan)
    assert 'clarify' in _types(plan)


@pytest.mark.parametrize('text', PLAIN)
def test_plain_cny_transfer(text):
    t = _transfers(Planner().plan(text, {}))
    assert len(t) == 1
    assert t[0]['payee'] == '妈妈' and t[0]['amount_cents'] == 30000


@pytest.mark.parametrize('text', FOLLOW)
def test_pending_followup_uses_context(text):
    t = _transfers(Planner().plan(text, PENDING))
    assert len(t) == 1
    assert t[0]['payee'] == '妈妈' and t[0]['amount_cents'] == 30000


@pytest.mark.parametrize('text', FOLLOW)
def test_model_mode_followup_stays_on_rules(text, monkeypatch):
    planner = Planner()
    planner.mode = 'model'
    monkeypatch.setattr(planner, '_model', _no_model)
    t = _transfers(planner.plan(text, PENDING))
    assert len(t) == 1
    assert t[0]['payee'] == '妈妈' and t[0]['amount_cents'] == 30000


@pytest.mark.parametrize('amount', BAD)
def test_model_mode_bad_amounts_clarify_before_model(amount, monkeypatch):
    planner = Planner()
    planner.mode = 'model'
    monkeypatch.setattr(planner, '_model', _no_model)
    for text, ctx in (('给妈妈转' + amount, {}), (amount, PENDING)):
        plan = planner.plan(text, ctx)
        assert not _transfers(plan)
        assert 'clarify' in _types(plan)
