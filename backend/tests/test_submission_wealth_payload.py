import json
import pytest
from ff_agent.bank import Bank, DomainError
from ff_agent.wealth import WealthService


@pytest.mark.parametrize('invalid_action', [[], {}])
def test_malformed_stored_action_uses_domain_error_and_changes_no_assets(tmp_path, invalid_action):
    bank = Bank(tmp_path/'payload.db')
    _consent_test_profile(bank)
    op = bank.prepare_wealth('demo', 'a_daily', 'demo_cash', 'purchase', 10000, 'prepare')
    payload = dict(op['payload'], action=invalid_action)
    before = bank.snapshot('demo')['accounts']
    with pytest.raises(DomainError) as caught:
        WealthService._payload({'payload': json.dumps(payload)})
    assert caught.value.code == 'INVALID_OPERATION'
    assert bank.snapshot('demo')['accounts'] == before


# Explicit prerequisite for existing financial tests; never an application default.
from demo_assessment import consent_test_profile as _consent_test_profile
import pytest as _pytest_assessment
