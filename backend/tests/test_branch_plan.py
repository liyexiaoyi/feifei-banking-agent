"""Conditional birthday DAG is data only; unresolved nodes never auto execute."""
import importlib
import importlib.util

import pytest


def module():
    assert importlib.util.find_spec('ff_agent.branch_plan') is not None,'birthday conditional DAG not implemented'
    return importlib.import_module('ff_agent.branch_plan')


def env():
    m=module();g=m.birthday_graph()
    return m,g,{i:'PENDING' for i in range(7)}


def test_template_is_a_real_fork_and_join_and_validation_is_deep_copy():
    m,g,s=env();v=m.validate_graph(g)
    assert v[2]['deps']==[1] and v[3]['deps']==[1] and v[6]['deps']==[4,5]
    assert m.evaluate_graph(g,s,{})=={'ready':[0],'skipped':[],'blocked':[]}
    g[2]['action']['gift']='BAD';assert v[2]['action']['gift']=='flowers'


def test_true_branch_runs_both_catalogs_then_both_orders_then_summary():
    m,g,s=env();s.update({0:'SUCCEEDED',1:'SUCCEEDED'})
    assert m.evaluate_graph(g,s,{1:True})['ready']==[2,3]
    s.update({2:'SUCCEEDED',3:'SUCCEEDED'})
    assert m.evaluate_graph(g,s,{1:True})['ready']==[4,5]
    s.update({4:'SUCCEEDED',5:'SUCCEEDED'})
    assert m.evaluate_graph(g,s,{1:True})['ready']==[6]


def test_false_choice_skips_only_cake_and_join_waits_for_flower():
    m,g,s=env();s.update({0:'SUCCEEDED',1:'SUCCEEDED'})
    v=m.evaluate_graph(g,s,{1:False});assert v['ready']==[2] and v['skipped']==[3,5]
    s.update({2:'SUCCEEDED',4:'SUCCEEDED'})
    assert m.evaluate_graph(g,s,{1:False})['ready']==[6]


@pytest.mark.parametrize('state',['UNKNOWN','RUNNING','WAITING_CONFIRMATION','WAITING_VERIFICATION'])
def test_unresolved_orders_never_reexecute_or_allow_summary(state):
    m,g,s=env();s.update({0:'SUCCEEDED',1:'SUCCEEDED',2:'SUCCEEDED',3:'SUCCEEDED',4:state,5:'SUCCEEDED'})
    v=m.evaluate_graph(g,s,{1:True});assert 4 not in v['ready'] and 6 not in v['ready'] and 4 not in v['skipped']


def test_failed_order_blocks_join_without_turning_success():
    m,g,s=env();s.update({0:'SUCCEEDED',1:'SUCCEEDED',2:'SUCCEEDED',3:'SUCCEEDED',4:'FAILED',5:'SUCCEEDED'})
    v=m.evaluate_graph(g,s,{1:True});assert v['ready']==[] and 6 in v['blocked']


@pytest.mark.parametrize('change',['cycle','duplicate','foreign_condition','unsafe_action','bool_index','bad_gift'])
def test_graph_rejects_cycles_ambiguity_and_unregistered_financial_actions(change):
    m,g,s=env()
    if change=='cycle':g[0]['deps']=[6]
    if change=='duplicate':g[1]['idx']=0
    if change=='foreign_condition':g[3]['when']={'node':4,'equals':True}
    if change=='unsafe_action':g[2]['action']={'type':'execute_transfer'}
    if change=='bool_index':g[0]['idx']=False
    if change=='bad_gift':g[2]['action']['gift']='external-bank'
    with pytest.raises(m.GraphError):m.validate_graph(g)


def test_decision_is_explicit_boolean_and_unknown_choice_keeps_branches_waiting():
    m,g,s=env();s.update({0:'SUCCEEDED',1:'UNKNOWN'})
    assert m.evaluate_graph(g,s,{})['ready']==[]
    with pytest.raises(m.GraphError):m.evaluate_graph(g,s,{1:1})


def test_failed_unresolved_ancestor_cannot_be_skipped_by_new_false_choice():
    m,g,s=env();s.update({0:'SUCCEEDED',1:'SUCCEEDED',2:'SUCCEEDED',3:'UNKNOWN',4:'SUCCEEDED'})
    v=m.evaluate_graph(g,s,{1:False});assert 3 not in v['skipped'] and 5 not in v['skipped'] and 6 not in v['ready']


@pytest.mark.parametrize('case',['skipped_budget','skipped_flower','skipped_true_cake','pending_budget_with_successors','successful_false_cake'])
def test_persisted_completion_must_be_justified_by_dependencies_and_decision(case):
    m,g,s=env();s.update({0:'SUCCEEDED',1:'SUCCEEDED',2:'SUCCEEDED',3:'SUCCEEDED',4:'PENDING',5:'PENDING'})
    decision=True
    if case=='skipped_budget':s[0]='SKIPPED'
    if case=='skipped_flower':s[2]='SKIPPED'
    if case=='skipped_true_cake':s[3]='SKIPPED'
    if case=='pending_budget_with_successors':s[0]='PENDING'
    if case=='successful_false_cake':decision=False
    with pytest.raises(m.GraphError):m.evaluate_graph(g,s,{1:decision})


def test_justified_persisted_skips_and_mixed_join_are_accepted_without_mutating_inputs():
    from copy import deepcopy
    m,g,s=env();s.update({0:'SUCCEEDED',1:'SUCCEEDED',2:'SUCCEEDED',3:'SKIPPED',4:'SUCCEEDED',5:'SKIPPED'})
    decisions={1:False};before=deepcopy((g,s,decisions))
    assert m.evaluate_graph(g,s,decisions)=={'ready':[6],'skipped':[],'blocked':[]}
    assert (g,s,decisions)==before
