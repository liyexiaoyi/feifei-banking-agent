import pytest

from ff_agent.bank import DomainError
from test_card_chat_controls import fixture, source, STOP, CORRECT


@pytest.mark.parametrize('selector', ['日常账户', 'tail'])
def test_stop_explicit_selector_cannot_cancel_a_different_card(tmp_path, selector):
    bank, controls, _ = fixture(tmp_path)
    # Obtain the seeded daily tail rather than assuming demonstration numbers.
    if selector == 'tail':
        selector = next(c['masked_number'][-4:] for c in bank.list_cards('demo')
                        if c['id'] == 'card_a_daily')
    op = source(bank, card='card_a_saving')
    with pytest.raises(DomainError):
        controls.cancel('demo', 'mismatch', 'hash', {**STOP, 'card': selector}, [op['id']])
    assert bank.operation('demo', op['id'])['status'] == 'WAITING_CONFIRMATION'
    with bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM card_chat_claims').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM card_chat_controls').fetchone()[0] == 0


def test_correction_selector_identifies_target_and_not_the_source(tmp_path):
    bank, controls, _ = fixture(tmp_path)
    op = source(bank)
    result = controls.cancel('demo', 'correct-ok', 'hash', CORRECT, [op['id']],
                             {'id': 'card_a_saving', 'version': 1})
    assert result['target_card_id'] == 'card_a_saving'
    assert bank.operation('demo', op['id'])['status'] == 'CANCELLED'


def test_correction_target_must_match_the_explicit_selector(tmp_path):
    bank, controls, _ = fixture(tmp_path)
    op = source(bank, card='card_a_saving')
    with pytest.raises(DomainError):
        controls.cancel('demo', 'wrong-target', 'hash', CORRECT, [op['id']],
                        {'id': 'card_a_daily', 'version': 1})
    assert bank.operation('demo', op['id'])['status'] == 'WAITING_CONFIRMATION'


@pytest.mark.parametrize('kind', ['card_stop', 'card_correct'])
def test_duplicate_owned_tail_is_ambiguous_even_with_a_selected_source_or_target(tmp_path, kind):
    bank, controls, _ = fixture(tmp_path)
    with bank.transaction() as db:
        db.execute("UPDATE bank_cards SET masked_number='DEMO ···· 8888' WHERE user_id='demo'")
    op = source(bank)
    action = {'type': kind, 'card': '8888'}
    target = {'id': 'card_a_saving', 'version': 1} if kind == 'card_correct' else None
    if kind == 'card_stop':
        action['action'] = 'report_lost'
    with pytest.raises(DomainError):
        controls.cancel('demo', 'collision', 'hash', action, [op['id']], target)
    assert bank.operation('demo', op['id'])['status'] == 'WAITING_CONFIRMATION'
    with bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM card_chat_claims').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM card_chat_controls').fetchone()[0] == 0
