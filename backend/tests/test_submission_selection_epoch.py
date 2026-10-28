import pytest

from ff_agent.api import create_app
from ff_agent.bank import DomainError


def selection(app, epoch):
    plans = app.state.plans
    plan, _ = plans.reserve('demo', 'selection', '给林晓转300元', epoch)
    plan = plans.freeze('demo', plan['id'], plan['version'], [{
        'idx': 0, 'action': {'type': 'transfer', 'payee': '林晓', 'amount_cents': 30000}, 'deps': []}])
    plan, token = plans.claim('demo', plan['id'], 0, plan['version'], epoch)
    key = plan['nodes'][0]['effect_key']
    plans.settle('demo', plan['id'], 0, token, 'PREPARED', {'cards': [{
        'type': 'payees', 'selection_key': key, 'candidates': [{'id': 'p_lin_1'}]}]})
    return 'select-' + key


@pytest.mark.parametrize('supply_current_epoch', [False, True])
def test_old_selection_cannot_prepare_after_risk_even_with_current_caller_epoch(tmp_path, supply_current_epoch):
    app = create_app(tmp_path/'stale-selection.db')
    bank = app.state.bank
    key = selection(app, '')
    hold = bank.activate_safety_hold('demo', {
        'codes': ['SAFE_ACCOUNT'], 'reasons': ['合成风险反例'], 'advice': '自行核实'}, 'risk')
    bank.end_safety_hold('demo', hold['id'])
    epoch = bank.snapshot('demo')['safety_epoch'] if supply_current_epoch else None
    with pytest.raises(DomainError) as caught:
        bank.prepare_transfer('demo', 'p_lin_1', 30000, key, expected_epoch=epoch)
    assert caught.value.code == 'REQUEST_SUPERSEDED'
    assert bank.snapshot('demo')['operations'] == []


def test_current_selection_still_prepares_one_owned_unexecuted_operation(tmp_path):
    app = create_app(tmp_path/'current-selection.db')
    key = selection(app, '')
    op = app.state.bank.prepare_transfer('demo', 'p_lin_1', 30000, key)
    assert op['status'] == 'WAITING_CONFIRMATION'
    assert app.state.bank.prepare_transfer('demo', 'p_lin_1', 30000, key)['id'] == op['id']
