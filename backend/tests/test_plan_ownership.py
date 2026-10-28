"""Real SQLite ownership, rollback, and conservative legacy migration checks."""
import json

import pytest

from ff_agent.api import create_app
from ff_agent.bank import DomainError
from ff_agent.plan_ownership import OwnershipConflict, bind_operation, migrate_legacy, owner_for


def active(app, key='one'):
    plans = app.state.plans
    plan, _ = plans.reserve('demo', key, '给妈妈300', '')
    plan = plans.freeze('demo', plan['id'], plan['version'], [
        {'idx': 0, 'action': {'type': 'transfer', 'payee': '妈妈', 'amount_cents': 30000}, 'deps': []}])
    plan, token = plans.claim('demo', plan['id'], 0, plan['version'], '')
    return plan, {'id': plan['id'], 'idx': 0, 'token': token}


def test_same_owner_retry_reuses_operation_and_never_reassigns(tmp_path):
    app = create_app(tmp_path/'retry.db'); bank = app.state.bank
    plan, ticket = active(app); key = plan['nodes'][0]['effect_key']
    first = bank.prepare_transfer('demo', 'p_mom', 30000, key, plan_ticket=ticket)
    assert bank.prepare_transfer('demo', 'p_mom', 30000, key, plan_ticket=ticket)['id'] == first['id']
    other, _ = active(app, 'two')
    with bank.transaction() as db:
        assert owner_for(db, 'demo', first['id']) == (plan['id'], 0)
        with pytest.raises(OwnershipConflict):
            bind_operation(db, 'demo', first['id'], (other['id'], 0), 'ticket')
        assert db.execute('SELECT count(*) FROM operation_plan_links').fetchone()[0] == 1
        assert owner_for(db, 'demo', first['id']) == (plan['id'], 0)


def test_ownership_insert_failure_rolls_back_operation_and_audit(tmp_path, monkeypatch):
    app = create_app(tmp_path/'rollback.db'); bank = app.state.bank
    plan, ticket = active(app)
    with bank.transaction() as db: before = '\n'.join(db.iterdump())
    def reject(*args): raise OwnershipConflict('injected')
    monkeypatch.setattr('ff_agent.bank.bind_operation', reject)
    with pytest.raises(DomainError) as error:
        bank.prepare_transfer('demo', 'p_mom', 30000, plan['nodes'][0]['effect_key'], plan_ticket=ticket)
    assert error.value.code == 'PLAN_OWNERSHIP_CONFLICT'
    with bank.transaction() as db: assert '\n'.join(db.iterdump()) == before


def test_valid_ticket_cannot_use_another_nodes_key(tmp_path):
    app = create_app(tmp_path/'ticket-key.db'); bank = app.state.bank
    plan, ticket = active(app)
    with pytest.raises(DomainError) as error:
        bank.prepare_transfer('demo', 'p_mom', 30000, 'arbitrary', plan_ticket=ticket)
    assert error.value.code == 'PLAN_OWNERSHIP_CONFLICT'
    assert bank.snapshot('demo')['operations'] == []


def test_conflicting_legacy_card_evidence_is_not_first_writer_wins(tmp_path):
    app = create_app(tmp_path/'conflict.db'); bank = app.state.bank
    first, ticket = active(app, 'first'); second, _ = active(app, 'second')
    op = bank.prepare_transfer('demo', 'p_mom', 30000, first['nodes'][0]['effect_key'], plan_ticket=ticket)
    with bank.transaction() as db:
        db.execute('DELETE FROM operation_plan_links')
        db.execute("UPDATE operations SET idempotency_key='legacy-0' WHERE id=?", (op['id'],))
        for plan in (first, second):
            db.execute("UPDATE plan_store_nodes SET effect_key='legacy-0',result=? WHERE plan_id=?",
                (json.dumps({'cards': [{'type': 'operation', 'operation': op}]}), plan['id']))
        stats = migrate_legacy(db)
        assert stats['conflicts'] == 1 and stats['links_added'] == 0
        assert db.execute('SELECT count(*) FROM operation_plan_links').fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM plan_link_issues WHERE code='ownership_conflict'").fetchone()[0] == 2
        before = '\n'.join(db.iterdump())
        again = migrate_legacy(db)
        assert again['links_added'] == again['issues_added'] == 0
        assert '\n'.join(db.iterdump()) == before


def test_existing_unlinked_operation_cannot_be_claimed_by_a_plan(tmp_path):
    app = create_app(tmp_path/'unlinked.db'); bank = app.state.bank
    plan, ticket = active(app)
    op = bank.prepare_transfer('demo', 'p_mom', 30000, plan['nodes'][0]['effect_key'], plan_ticket=ticket)
    with bank.transaction() as db: db.execute('DELETE FROM operation_plan_links')
    with pytest.raises(DomainError) as error:
        bank.prepare_transfer('demo', 'p_mom', 30000, plan['nodes'][0]['effect_key'], plan_ticket=ticket)
    assert error.value.code == 'PLAN_OWNERSHIP_CONFLICT'
    with bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM operation_plan_links').fetchone()[0] == 0
        assert db.execute('SELECT id FROM operations').fetchone()[0] == op['id']


def test_ambiguous_legacy_selection_source_cannot_choose_first_owner(tmp_path):
    app = create_app(tmp_path/'ambiguous-source.db'); bank = app.state.bank
    first, _ = active(app, 'first'); second, _ = active(app, 'second')
    with bank.transaction() as db:
        for plan in (first, second):
            db.execute("UPDATE plan_store_plans SET status='DONE' WHERE id=?", (plan['id'],))
            db.execute("UPDATE plan_store_nodes SET state='PREPARED',effect_key='legacy-0',action=?,result=? WHERE plan_id=?",
                (json.dumps({'type': 'transfer', 'payee': '林晓', 'amount_cents': 30000}),
                 json.dumps({'cards': [{'type': 'payees', 'selection_key': 'legacy-0', 'candidates': [{'id': 'p_lin_1'}]}]}), plan['id']))
    with pytest.raises(DomainError) as error:
        bank.prepare_transfer('demo', 'p_lin_1', 30000, 'select-legacy-0')
    assert error.value.code == 'PLAN_OWNERSHIP_CONFLICT'
    assert bank.snapshot('demo')['operations'] == []
