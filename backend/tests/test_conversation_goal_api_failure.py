from datetime import datetime
import pytest
from ff_agent.bank import Bank,DomainError
from ff_agent.conversation_goal_api import GoalAccess


def ready(tmp_path):
 b=Bank(tmp_path/'goal-api-failure.db',clock=lambda:datetime.fromisoformat('2026-10-28T12:00:00+08:00'))
 op=b.prepare_transfer('demo',b.resolve_payee('demo','妈妈')[0]['id'],10000,'goal-api-proof-transfer')
 op=b.confirm('demo',op['id']);assert op['status']=='SUCCEEDED'
 return b,GoalAccess(b,object()),op


def test_receipt_read_failure_cannot_leave_a_cached_financial_success(tmp_path,monkeypatch):
 b,a,op=ready(tmp_path)
 def missing(*args):raise DomainError('NOT_FOUND','无法读取',404)
 monkeypatch.setattr(b,'receipt',missing)
 got=a.refresh_cards('demo',[{'type':'operation','operation':op}])
 assert got[0]['operation']['status']=='UNKNOWN'
 assert not got[0]['operation'].get('result')


def test_receipt_success_retains_only_the_authoritative_operation(tmp_path):
 b,a,op=ready(tmp_path);cached={**op,'status':'UNKNOWN','result':None}
 got=a.refresh_cards('demo',[{'type':'operation','operation':cached}])
 assert got[0]['operation']['status']=='SUCCEEDED'


def test_receipt_projection_marks_expired_without_writing_bank_tables(tmp_path):
 from datetime import timedelta
 b,a,_=ready(tmp_path);op=b.prepare_transfer('demo',b.resolve_payee('demo','妈妈')[0]['id'],10000,'goal-api-expired-transfer')
 now=b.now();b.clock=lambda:now+timedelta(minutes=10)
 with b.transaction() as db:before=list(db.iterdump())
 got=a.refresh_cards('demo',[{'type':'operation','operation':op}])
 assert got[0]['operation']['status']=='EXPIRED'
 with b.transaction() as db:assert list(db.iterdump())==before
