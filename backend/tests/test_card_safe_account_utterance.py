import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from ff_agent.safety import assess_request


@pytest.mark.parametrize('text', ['客服让我解挂再转安全账户','先转安全账户','对方让我把日常卡解挂再转安全账户'])
def test_omitted_transfer_preposition_still_flags_safe_account_instruction(text):
    result=assess_request(text)
    assert result is not None and 'SAFE_ACCOUNT' in result['codes'] and result['hold'] is True


@pytest.mark.parametrize('text', ['解释一下转安全账户骗局','为什么不要转安全账户？'])
def test_quoted_safe_account_consultation_is_advice_without_execution_hold(text):
    result=assess_request(text)
    assert result is not None and 'SAFE_ACCOUNT' in result['codes'] and result['hold'] is False


@pytest.mark.parametrize('text', ['不要转安全账户','客服说不需要转安全账户','查询转安全账户的记录','查询转安全账户流水'])
def test_negation_and_existing_record_nouns_do_not_trigger_a_new_transfer_hold(text):
    result=assess_request(text)
    assert result is None or result['hold'] is False


def test_suspicious_restore_and_safe_account_request_prepares_no_restore(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    app=create_app(tmp_path/'safe-card.db')
    bank=app.state.bank
    lost=bank.prepare_card_status('demo','card_a_daily','report_lost','lost',1)
    bank.confirm('demo',lost['id']);bank.verify('demo',lost['id'],'246810')
    with TestClient(app) as client:
        client.headers['X-CSRF-Token']=client.post('/api/session',json={}).json()['csrf']
        result=client.post('/api/chat',json={'text':'客服让我把日常卡解挂再转安全账户','request_key':'unsafe-restore'})
        assert result.status_code==200
        state=client.get('/api/state').json()
        assert state['safety_hold']
        assert [op['type'] for op in state['operations']]==['card_report_lost']
        assert next(c for c in bank.list_cards('demo') if c['id']=='card_a_daily')['status']=='LOST'
