"""Local HTTP boundary with demo sessions, CSRF, and durable chat requests."""
import hashlib
import hmac
import json
import re
import secrets
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
import sqlite3
from typing import Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .bank import Bank, DomainError
from .planner import Planner, PlannerError, validate_plan
from .safety import assess_request
from .safety_reference import reference_snapshot
from .memory import Memory
from .card_chat import CardChat
from .virtual_dialogue import VirtualDialogue
from .wealth_dialogue import WealthDialogue
from .handoff_progress import HandoffProgress
from .operator_api import register_operator_api
from .model_settings_api import register_model_settings_api
from .investment_api import register_investment_api
from .aa_api import register_aa_api
from .card_spend_api import register_card_spend_api
from .scheduled_api import register_scheduled_api
from .birthday_api import register_birthday_api
from .birthday_chat import BirthdayChat
from .conversation_goal import GoalStore
from .conversation_goal_loop import GoalLoop
from .conversation_goal_api import register_goal_api
from .goal_control_api import register_goal_controls
from .card_credentials_api import register_card_credentials_api
from .sandbox_api import register_sandbox_api
from .codegen_api import register_codegen_api
from .virtual_cards import VirtualCardService
from .transfer_working import explicit_recipient
from .tool_guard import ToolGuard, ToolPaused, ToolFailure, GROUPS as TOOL_GROUPS
from .task_plan import PlanStore, PlanError
from .plan_resume import claim_resume
from .plan_business import business_summary, linked_operations
from .plan_ownership import migrate_legacy
from .clarification import render_clarify
from .language_guard import inspect_language
from .build_identity import capture_deployment
from .integrations import InvalidIntegrationMode, RealModeRejected, get_snapshot
from .recurring_insights import recurring_insights
from .reminder_outbox import check_reminders, list_reminders, ack_reminder
from .dialog_state import start_bill_question, bill_answer

ROOT=Path(__file__).resolve().parents[2]


class StrictBody(BaseModel):
    model_config=ConfigDict(extra='forbid')


class SessionBody(StrictBody):
    user:str=Field(default='demo',pattern='^(demo|guest)$')


class ChatBody(StrictBody):
    text:str=Field(min_length=1,max_length=2000)
    request_key:str=Field(min_length=1,max_length=100)
    goal_mode:StrictBool=False
    goal_id:str|None=Field(default=None,min_length=1,max_length=64)
    goal_version:StrictInt|None=Field(default=None,ge=1)


class PlanControlBody(StrictBody):
    expected_version:StrictInt=Field(ge=1)


class ReminderAckBody(StrictBody):
    expected_version:StrictInt=Field(ge=1)


class HandoffProgressBody(StrictBody):
    expected_version:StrictInt=Field(ge=0)
    status:Literal['IN_REVIEW','CLOSED']
    request_key:str=Field(pattern=r'^[A-Za-z0-9_-]{8,64}$',min_length=8,max_length=64)


class VerifyBody(StrictBody):
    code:str=Field(pattern=r'^\d{6}$')


class TransferBody(StrictBody):
    payee_id:str=Field(min_length=1,max_length=100)
    amount_cents:StrictInt=Field(gt=0,le=100000000)
    request_key:str=Field(min_length=1,max_length=100)
    safety_epoch:str=Field(default='',max_length=100)


class WealthBody(StrictBody):
    account_id:str=Field(min_length=1,max_length=120)
    product_id:str=Field(min_length=1,max_length=120)
    action:Literal['purchase','redeem']
    amount_cents:StrictInt=Field(gt=0,le=100000000)
    request_key:str=Field(min_length=1,max_length=120)
    safety_epoch:str=Field(default='',max_length=100)


class EndSafetyBody(StrictBody):
    hold_id:str=Field(min_length=1,max_length=100)


class RememberBody(StrictBody):
    content:str=Field(min_length=1,max_length=300)
    request_key:str=Field(min_length=1,max_length=100)


class MemoryVersionBody(StrictBody):
    expected_version:StrictInt=Field(ge=1)


class UpdateMemoryBody(MemoryVersionBody):
    content:str=Field(min_length=1,max_length=300)


class CancelBody(StrictBody):
    subscription_id:str=Field(min_length=1,max_length=100)
    request_key:str=Field(min_length=1,max_length=100)


class CardStatusBody(StrictBody):
    card_id:str=Field(min_length=1,max_length=100)
    action:Literal['report_lost','restore']
    expected_version:StrictInt=Field(ge=1)
    request_key:str=Field(min_length=1,max_length=100)


class VirtualCardBody(StrictBody):
    account_id:str=Field(min_length=1,max_length=120)
    request_key:str=Field(min_length=1,max_length=120)


from .channel_api import register_channel_api


def create_app(db_path=None,planner=None,channel_config=None,codegen_generator=None,allow_codegen_fixture=False):
    deployment=capture_deployment(ROOT)
    bank=Bank(db_path or ROOT/'data'/'ff-agent.db')
    memory=Memory(bank)
    card_chat=CardChat(bank)
    virtual_dialogue=VirtualDialogue(bank)
    wealth_dialogue=WealthDialogue(bank)
    planner=planner or Planner(clock=bank.now)
    guard=ToolGuard(bank.path,clock=lambda:bank.now().timestamp(),
                    audit=lambda db,user,event,detail:bank._event(db,user,None,event,detail))
    plans=PlanStore(bank.path,clock=lambda:bank.now().timestamp(),
                    audit=lambda db,user,event,detail:bank._event(db,user,None,event,json.dumps(detail,ensure_ascii=False)))
    with bank.transaction() as db:
        link_migration=migrate_legacy(db)
    plans.mark_orphans()
    guard.mark_orphans()
    with bank.transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS local_handoffs(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,user_id TEXT NOT NULL,summary TEXT NOT NULL,created_at REAL NOT NULL,UNIQUE(user_id,plan_id))")
    handoff_progress=HandoffProgress(bank)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            guard.close()
            plans.close()
    app=FastAPI(title='FF Agent 模拟银行',version='0.1.0',docs_url='/api/docs',openapi_url='/api/openapi.json',lifespan=lifespan)
    app.state.bank=bank
    app.state.handoff_progress=handoff_progress
    app.state.plans=plans
    app.state.link_migration=link_migration
    app.state.tool_guard=guard
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])

    @app.middleware('http')
    async def secure_headers(request,call_next):
        identity=request.headers.get('x-build-identity')
        if request.method not in ('GET','HEAD','OPTIONS') and identity is not None and (
                deployment['build_state']!='ready' or
                identity!=deployment['backend_id']+':'+deployment['frontend_id']):
            response=JSONResponse(status_code=409,content={'error':{'code':'DEPLOYMENT_MISMATCH','message':'页面与服务版本不匹配，请先核对本机部署；本次请求未执行。'}})
        else:
            response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        owner=getattr(request.state,'ff_session_owner',None)
        if isinstance(owner,str) and owner in ('demo','guest'):
            response.headers['X-FF-Session-Owner']=owner
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request,error):
        return JSONResponse(status_code=error.status,content={'error':{'code':error.code,'message':error.message}})

    @app.exception_handler(PlanError)
    async def plan_error(request,error):
        return JSONResponse(status_code=error.status,content={'error':{'code':error.code,'message':error.message}})

    @app.exception_handler(PlannerError)
    async def planner_error(request,error):
        return JSONResponse(status_code=503,content={'error':{'code':error.code,'message':error.message}})

    @app.exception_handler(ToolPaused)
    async def tool_paused(request,error):
        return JSONResponse(status_code=503,content={'error':{'code':'TOOL_PAUSED','message':'该功能因连续技术故障暂停，请先核对已有回执，再按页面提示恢复。',
            'group':error.group,'retry_after_seconds':error.retry_after_seconds,'needs_handoff':error.needs_handoff}})

    @app.exception_handler(ToolFailure)
    async def tool_failure(request,error):
        return JSONResponse(status_code=503,content={'error':{'code':'TOOL_UNAVAILABLE','message':'本次工具调用出现技术故障。请核对已有操作，不要另建重复请求。','group':error.group}})

    def run_tool(user,group,fn):
        return guard.run(user,group,fn,business_error_predicate=lambda e:isinstance(e,DomainError) and e.status<500)

    @contextmanager
    def guarded(user,group):
        if group is None:
            yield
            return
        ticket=guard.start(user,group)
        try:
            yield
        except DomainError as error:
            guard.finish(ticket,'business' if error.status<500 else 'tech')
            if error.status<500:
                raise
            raise ToolFailure(group) from None
        except Exception:
            guard.finish(ticket,'tech')
            raise ToolFailure(group) from None
        else:
            guard.finish(ticket,'ok')

    def check_origin(request):
        origin=request.headers.get('origin')
        expected=f'{request.url.scheme}://{request.headers.get("host","")}'
        if origin is not None and origin!=expected:
            raise DomainError('ORIGIN_REJECTED','此请求来源不受信任。',403)

    def current_user(request:Request):
        token=request.cookies.get('ff_session','')
        hashed=hashlib.sha256(token.encode()).hexdigest()
        with bank.transaction() as db:
            session=db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires_at>?',(hashed,time.time())).fetchone()
        if not session:
            raise DomainError('SESSION_REQUIRED','请先进入模拟体验。',401)
        request.state.ff_session_owner=session['user_id']
        if request.method not in ('GET','HEAD','OPTIONS'):
            check_origin(request)
            if not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):
                raise DomainError('CSRF_REJECTED','操作凭据已失效，请刷新页面后重试。',403)
        return dict(session)

    register_operator_api(app,bank,current_user,check_origin)
    register_model_settings_api(app,current_user,lambda:ROOT)
    register_investment_api(app,bank,current_user)
    register_aa_api(app,bank,current_user)
    sandbox_service=register_sandbox_api(app,bank,current_user)
    register_codegen_api(app,bank,current_user,sandbox_service,guard,codegen_generator,allow_codegen_fixture)
    register_channel_api(app,bank,current_user,check_origin,channel_config)

    def get_context(user,text,session_id):
        return memory.context(user,text,session_id=session_id)

    def save_context(user,value,epoch,session_id,request_order):
        memory.save_working(user,value,epoch,session_id=session_id,request_order=request_order)

    def record_operation(user,operation):
        key='direct-'+operation['id']
        with bank.transaction() as db:
            if not db.execute('SELECT 1 FROM messages WHERE user_id=? AND request_key=?',(user,key)).fetchone():
                content={'message':'操作已准备。请核对详情，完成后可在此查看回执。','cards':[{'type':'operation','operation':operation}],'tasks':[],'planner_mode':planner.mode}
                db.execute('INSERT INTO messages(user_id,role,content,request_key,created_at) VALUES(?,?,?,?,?)',(user,'assistant',json.dumps(content,ensure_ascii=False),key,bank.now().isoformat()))

    register_card_spend_api(app,bank,current_user,run_tool,record_operation)
    register_scheduled_api(app,bank,current_user,run_tool,record_operation)
    register_birthday_api(app,bank,current_user,run_tool,record_operation)
    birthday_chat=BirthdayChat(bank,app.state.birthday_service.store,run_tool=run_tool)
    app.state.birthday_chat=birthday_chat
    register_card_credentials_api(app,bank,current_user,run_tool,record_operation)

    @app.get('/api/health')
    def health():
        return {'status':'ok','service':'ff-agent','version':'0.1.0','simulated':True,'planner_mode':planner.mode,'deployment':dict(deployment)}

    @app.post('/api/session')
    def open_session(body:SessionBody,request:Request,response:Response):
        check_origin(request)
        token=secrets.token_urlsafe(32)
        csrf=secrets.token_urlsafe(24)
        with bank.transaction() as db:
            bank._user(db,body.user)
            db.execute('DELETE FROM sessions WHERE expires_at<?',(time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),body.user,csrf,time.time()+43200))
        request.state.ff_session_owner=body.user
        response.set_cookie('ff_session',token,httponly=True,samesite='strict',max_age=43200,path='/')
        return {'csrf':csrf,'user':body.user,'simulated':True}

    @app.get('/api/session')
    def session_info(session=Depends(current_user)):
        return {'csrf':session['csrf'],'user':session['user_id'],'simulated':True}

    @app.get('/api/integrations')
    def integrations_snapshot(request: Request, _user=Depends(current_user)):
        pairs = request.query_params.multi_items()
        mode_values = [value for key, value in pairs if key == 'mode']
        if any(key != 'mode' for key, _ in pairs) or len(mode_values) > 1:
            raise DomainError(
                'INVALID_INTEGRATION_QUERY',
                '接入状态查询参数不支持。',
                422,
            )

        mode = mode_values[0] if mode_values else 'simulated'
        try:
            return get_snapshot(mode=mode)
        except RealModeRejected:
            raise DomainError(
                'REAL_MODE_REJECTED',
                '真实服务尚未接入，本次只查询状态，不切换业务模式。',
                409,
            ) from None
        except InvalidIntegrationMode:
            raise DomainError(
                'INVALID_INTEGRATION_QUERY',
                '接入状态查询参数不支持。',
                422,
            ) from None

    @app.get("/api/safety/references")
    def get_safety_references(request: Request, _user=Depends(current_user)):
        # Authentication is enforced by current_user before this handler runs.
        if request.query_params:
            raise DomainError(
                code="INVALID_REFERENCE_QUERY",
                message="查询参数不受支持",
                status=422,
            )
        return reference_snapshot()

    @app.get('/api/state')
    def state(session=Depends(current_user)):
        return {**bank.snapshot(session['user_id']),'simulated':True,'planner_mode':planner.mode,'memory_count':memory.count(session['user_id']),'tools':guard.snapshot(session['user_id']),'plans':[public_plan(p,session['user_id']) for p in plans.list(session['user_id'])],'handoffs':handoffs(session['user_id'])}

    @app.post('/api/tools/{group}/recover')
    def recover_tool(group:str,body:StrictBody,session=Depends(current_user)):
        if group not in TOOL_GROUPS:
            raise DomainError('NOT_FOUND','没有这个可恢复的功能。',404)
        return {'tool':guard.recover(session['user_id'],group)}

    def public_plan(plan,user):
        with bank.transaction() as db:
            plan=plans.snapshot(db,user,plan['id'])
            business=business_summary(db,user,plan,bank.now().timestamp(),bank._safety_epoch(db,user))
        return {**{k:plan[k] for k in ('id','request_key','risk_epoch','version','status','reason')},
                'business':business,
                'nodes':[{k:n[k] for k in ('idx','kind','deps','state','error_code')} for n in plan['nodes']]}

    def handoffs(user):
        with bank.transaction() as db:
            return [json.loads(row['summary']) for row in db.execute('SELECT summary FROM local_handoffs WHERE user_id=? ORDER BY created_at DESC LIMIT 10',(user,))]

    def stop_plan(db,plan,user,action,reason=None):
        operations=[]
        rows=linked_operations(db,user,plan)
        for op in rows:
            if op['status'] in ('WAITING_CONFIRMATION','WAITING_VERIFICATION','READY'):
                db.execute("UPDATE operations SET status='CANCELLED' WHERE id=? AND user_id=? AND status IN ('WAITING_CONFIRMATION','WAITING_VERIFICATION','READY')",(op['id'],user))
                bank._event(db,user,op['id'],'PLAN_STOP_CANCELLED','计划停止，取消关联未执行操作并释放预留')
            operations.append({'id':op['id'],'type':op['type'],'status':db.execute('SELECT status FROM operations WHERE id=? AND user_id=?',(op['id'],user)).fetchone()[0]})
        if action=='handoff':
            summary={'id':'local_'+secrets.token_hex(12),'plan_id':plan['id'],'created_at':bank.now().timestamp(),
                     'reason':reason or 'USER_REQUESTED_REVIEW','steps':[{k:n[k] for k in ('idx','kind','state')} for n in plan['nodes']],
                     'operations':operations,'checklist':['自行打开银行或商户官方渠道核实，不使用对方发来的链接。','先核对已存在的操作编号和业务回执，不重复付款。','中断调用尚未证实终止时，不继续探测或恢复交易。'],
                     'simulated':True,'contacted_human':False}
            db.execute('INSERT OR IGNORE INTO local_handoffs VALUES(?,?,?,?,?)',(summary['id'],plan['id'],user,json.dumps(summary,ensure_ascii=False),bank.now().timestamp()))

    @app.get('/api/plans')
    def get_plans(session=Depends(current_user)):
        return {'plans':[public_plan(p,session['user_id']) for p in plans.list(session['user_id'])]}

    @app.get('/api/plans/{plan_id}')
    def get_plan(plan_id:str,session=Depends(current_user)):
        return public_plan(plans.get(session['user_id'],plan_id),session['user_id'])

    @app.get('/api/plans/{plan_id}/handoff')
    def get_handoff(plan_id:str,session=Depends(current_user)):
        user=session['user_id'];plans.get(user,plan_id)
        with bank.transaction() as db:
            row=db.execute('SELECT summary FROM local_handoffs WHERE user_id=? AND plan_id=?',(user,plan_id)).fetchone()
            if not row:
                raise DomainError('NOT_FOUND','尚无这份本地核查记录。',404)
            return json.loads(row['summary'])

    @app.get('/api/plans/{plan_id}/handoff/progress')
    def get_handoff_progress(plan_id:str,session=Depends(current_user)):
        return handoff_progress.get(session['user_id'],plan_id)

    @app.post('/api/plans/{plan_id}/handoff/progress')
    def update_handoff_progress(plan_id:str,body:HandoffProgressBody,session=Depends(current_user)):
        return handoff_progress.update(session['user_id'],plan_id,body.expected_version,body.status,body.request_key)

    @app.post('/api/plans/{plan_id}/{action}')
    def control_plan(plan_id:str,action:str,body:PlanControlBody,session=Depends(current_user)):
        user=session['user_id'];plan=plans.get(user,plan_id)
        if action not in ('pause','resume','handoff','cancel'):
            raise DomainError('NOT_FOUND','没有这个任务控制操作。',404)
        resume_request={}
        result=plans.control(user,plan_id,body.expected_version,action,plan['risk_epoch'],
                            on_stop=lambda db,p:stop_plan(db,p,user,action,plan['reason']),
                            on_resume=lambda db,p:resume_request.update(claim_resume(db,user,p,bank._safety_epoch(db,user))),
                            stoppable=lambda db,p:business_summary(db,user,p,bank.now().timestamp(),bank._safety_epoch(db,user))['actionable'])
        response={'plan':public_plan(result,user)}
        if action=='handoff':
            response['handoff']=get_handoff(plan_id,session)
        if action=='resume':
            response['result']=chat(ChatBody(**resume_request),session)
            response['plan']=public_plan(plans.get(user,plan_id),user)
        return response

    @app.get('/api/memories')
    def memories(query:str='',session=Depends(current_user)):
        return {'notes':memory.recall(session['user_id'],query[:300])}

    @app.post('/api/memories')
    def remember(body:RememberBody,session=Depends(current_user)):
        return memory.remember(session['user_id'],body.content,body.request_key)

    @app.post('/api/memories/{note_id}/update')
    def update_memory(note_id:str,body:UpdateMemoryBody,session=Depends(current_user)):
        return memory.update(session['user_id'],note_id,body.content,body.expected_version)

    @app.post('/api/memories/{note_id}/forget')
    def forget_memory(note_id:str,body:MemoryVersionBody,session=Depends(current_user)):
        return memory.forget(session['user_id'],note_id,body.expected_version)

    @app.get('/api/bill')
    def bill(period:str,session=Depends(current_user)):
        return run_tool(session['user_id'],'bill',lambda:bank.analyze(session['user_id'],period))

    @app.get('/api/bill/annual')
    def annual_bill(request:Request,year:str,session=Depends(current_user)):
        if list(request.query_params.multi_items()) != [('year',year)]:
            raise DomainError('INVALID_ANNUAL_QUERY','请只提供一个要查询的年份。',422)
        return run_tool(session['user_id'],'bill',lambda:bank.analyze_year(session['user_id'],year))

    @app.get('/api/bill/recurring')
    def recurring_bill(request:Request,session=Depends(current_user)):
        if request.query_params:
            raise DomainError('INVALID_RECURRING_QUERY','此查询不接受额外参数。',422)
        return run_tool(session['user_id'],'bill',lambda:recurring_insights(bank,session['user_id']))

    def no_reminder_query(request):
        if request.query_params:
            raise DomainError('INVALID_REMINDER_QUERY','此查询不接受额外参数。',422)

    @app.get('/api/subscriptions/reminders')
    def reminders(request:Request,session=Depends(current_user)):
        no_reminder_query(request)
        return run_tool(session['user_id'],'subscriptions',lambda:list_reminders(bank,session['user_id']))

    @app.post('/api/subscriptions/reminders/check')
    def reminders_check(request:Request,body:StrictBody,session=Depends(current_user)):
        no_reminder_query(request)
        return run_tool(session['user_id'],'subscriptions',lambda:check_reminders(bank,session['user_id']))

    @app.post('/api/subscriptions/reminders/{reminder_id}/ack')
    def reminder_ack(reminder_id:str,request:Request,body:ReminderAckBody,session=Depends(current_user)):
        no_reminder_query(request)
        return run_tool(session['user_id'],'subscriptions',lambda:ack_reminder(bank,session['user_id'],reminder_id,body.expected_version))

    @app.get('/api/audit')
    def audit(session=Depends(current_user)):
        return {'events':bank.audit(session['user_id'])}

    @app.get('/api/wealth')
    def wealth_query(session=Depends(current_user)):
        user=session['user_id']
        return run_tool(user,'wealth',lambda:bank.wealth(user))

    @app.post('/api/wealth/prepare')
    def wealth_prepare(body:WealthBody,session=Depends(current_user)):
        user=session['user_id']
        operation=run_tool(user,'wealth',lambda:bank.prepare_wealth(
            user,body.account_id,body.product_id,body.action,body.amount_cents,
            body.request_key,expected_epoch=body.safety_epoch))
        record_operation(user,operation)
        return operation

    @app.get('/api/cards')
    def cards(session=Depends(current_user)):
        user=session['user_id']
        return run_tool(user,'cards',lambda:{'cards':bank.list_cards(user),
                        'virtual_applications':VirtualCardService(bank).applications(user),'simulated':True})

    @app.post('/api/cards/virtual/prepare')
    def virtual_card_prepare(body:VirtualCardBody,session=Depends(current_user)):
        user=session['user_id']
        operation=run_tool(user,'cards',lambda:bank.prepare_virtual_card(user,body.account_id,body.request_key))
        record_operation(user,operation)
        return operation

    @app.post('/api/cards/prepare')
    def card_prepare(body:CardStatusBody,session=Depends(current_user)):
        user=session['user_id']
        operation=run_tool(user,'cards',lambda:bank.prepare_card_status(
            user,body.card_id,body.action,body.request_key,body.expected_version))
        record_operation(user,operation)
        return operation

    @app.post('/api/safety/end')
    def end_safety(body:EndSafetyBody,session=Depends(current_user)):
        return bank.end_safety_hold(session['user_id'],body.hold_id)

    @app.get('/api/subscriptions')
    def subscriptions(session=Depends(current_user)):
        return {'subscriptions':run_tool(session['user_id'],'subscriptions',lambda:bank.subscriptions(session['user_id']))}

    @app.get('/api/history')
    def history(session=Depends(current_user)):
        with bank.transaction() as db:
            rows=db.execute('SELECT id,role,content,created_at,request_key FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 40',(session['user_id'],)).fetchall()
            messages=[]
            for row in reversed(rows):
                content=row['content']
                if row['role']=='assistant':
                    original=db.execute('SELECT input FROM chat_requests WHERE user_id=? AND request_key=?',(session['user_id'],row['request_key'])).fetchone()
                    content=memory.refresh_cards(db,session['user_id'],json.loads(content),original['input'] if original else '')
                messages.append({'id':row['id'],'role':row['role'],'content':content,'created_at':row['created_at']})
            return {'messages':messages}

    @app.get('/api/operations/{opid}')
    def operation(opid:str,session=Depends(current_user)):
        return bank.operation(session['user_id'],opid)

    @app.get('/api/operations/{opid}/receipt')
    def receipt(opid:str,session=Depends(current_user)):
        return bank.receipt(session['user_id'],opid)

    def owned_child_operation(user,key):
        # Read-only lookup of a continuation created by an earlier confirm; never prepares, records or retries.
        ro=sqlite3.connect(Path(bank.path).resolve().as_uri()+'?mode=ro',uri=True)
        try:
            ro.row_factory=sqlite3.Row
            ro.execute('PRAGMA query_only=ON')
            # Existing SELECT-only guard: stopped/updated owner plans or uncertain ownership expose no child.
            try:
                bank._assert_plan_continuation(ro,user,key)
            except DomainError:
                return None
            rows=ro.execute("SELECT id FROM operations WHERE user_id=? AND idempotency_key=? AND type='subscription_cancel' LIMIT 2",(user,key)).fetchall()
        finally:
            ro.close()
        if len(rows)!=1:
            return None
        child=bank.receipt(user,rows[0][0])
        return {**child['operation'],'status':child['observed_status']}

    @app.post('/api/operations/{opid}/confirm')
    def confirm(opid:str,body:StrictBody,session=Depends(current_user)):
        user=session['user_id']
        recorded=bank.receipt(user,opid)['operation']
        group='wealth' if recorded['type'] in ('wealth_purchase','wealth_redemption') else 'cards' if recorded['type'] in ('card_report_lost','card_restore','virtual_card_create','card_controls_update','card_spend','card_credential_change') else 'transfer' if recorded['type'] in ('transfer','scheduled_transfer_authorize','birthday_budget_reserve','birthday_order') else 'subscriptions'
        if recorded['status']=='SUCCEEDED':
            if recorded['type']=='subscription_query' and recorded['payload'].get('proposed_cancel'):
                child=owned_child_operation(user,'resume-'+opid)
                if child is not None:
                    recorded['next_operation']=child
                else:
                    recorded['notice']='查询已完成，这次没有执行新的取消操作。请核对原来的办理记录；仍需取消时，请重新说明要取消哪一项。'
            return recorded
        result=run_tool(user,group,lambda:bank.confirm(user,opid))
        merchant=result['payload'].get('proposed_cancel')
        if result['type']=='subscription_query' and result['status']=='SUCCEEDED' and merchant:
            try:
                subs=run_tool(user,'subscriptions',lambda:bank.subscriptions(user))
            except (DomainError,ToolPaused,ToolFailure):
                result['notice']='查询已完成，后续取消步骤暂不可继续。请核对已有操作和授权，不要重复提交。'
                return result
            matches=[s for s in subs if s['merchant']==merchant or merchant in s['merchant']]
            if len(matches)==1:
                try:
                    result['next_operation']=run_tool(user,'subscriptions',lambda:bank.prepare_cancel(user,matches[0]['id'],'resume-'+opid))
                    record_operation(user,result['next_operation'])
                except (DomainError,ToolPaused,ToolFailure):
                    result['notice']='查询已完成；取消准备的结果需要核对，请查看已有操作，不会自动确认或重发。'
            else:
                result['notice']='请在订阅列表中选择需要取消的具体商户。'
        return result

    @app.post('/api/operations/{opid}/verify')
    def verify(opid:str,body:VerifyBody,session=Depends(current_user)):
        user=session['user_id']
        recorded=bank.receipt(user,opid)['operation']
        group='wealth' if recorded['type'] in ('wealth_purchase','wealth_redemption') else 'cards' if recorded['type'] in ('card_report_lost','card_restore','virtual_card_create','card_controls_update','card_spend','card_credential_change') else 'transfer' if recorded['type'] in ('transfer','scheduled_transfer_authorize','birthday_budget_reserve','birthday_order') else 'subscriptions'
        return recorded if recorded['status']=='SUCCEEDED' else run_tool(user,group,lambda:bank.verify(user,opid,body.code))

    @app.post('/api/operations/{opid}/cancel')
    def cancel(opid:str,body:StrictBody,session=Depends(current_user)):
        return bank.cancel(session['user_id'],opid)

    @app.post('/api/transfers/prepare')
    def transfer_prepare(body:TransferBody,session=Depends(current_user)):
        operation=run_tool(session['user_id'],'transfer',lambda:bank.prepare_transfer(session['user_id'],body.payee_id,body.amount_cents,body.request_key,expected_epoch=body.safety_epoch))
        record_operation(session['user_id'],operation)
        return operation

    @app.post('/api/subscriptions/cancel/prepare')
    def subscription_cancel(body:CancelBody,session=Depends(current_user)):
        operation=run_tool(session['user_id'],'subscriptions',lambda:bank.prepare_cancel(session['user_id'],body.subscription_id,body.request_key))
        record_operation(session['user_id'],operation)
        return operation

    goal_loop=GoalLoop(bank,GoalStore(bank),run_tool=run_tool)
    app.state.goal_loop=goal_loop
    goal_access=register_goal_api(app,bank,goal_loop,current_user,record_operation)
    register_goal_controls(app,bank,goal_loop,current_user)

    @app.post('/api/chat')
    def chat(body:ChatBody,session=Depends(current_user)):
        user=session['user_id']
        with bank.transaction() as db:
            goal_access.bind_chat(db,user,body.request_key,body.text,body.goal_mode,body.goal_id,body.goal_version)
            existing=db.execute('SELECT * FROM chat_requests WHERE user_id=? AND request_key=?',(user,body.request_key)).fetchone()
            if existing:
                if existing['input']!=body.text:
                    raise DomainError('IDEMPOTENCY_CONFLICT','同一请求编号的内容已改变。',409)
                if existing['status']=='DONE':
                    return memory.refresh_cards(db,user,json.loads(existing['response']),body.text)
                if existing['status']=='PROCESSING':
                    raise DomainError('REQUEST_PENDING','该请求正在处理，请稍后核对已有操作。',409)
                db.execute("UPDATE chat_requests SET status='PROCESSING',created_at=? WHERE user_id=? AND request_key=?",(time.time(),user,body.request_key))
            else:
                db.execute("INSERT INTO chat_requests VALUES(?,?,?,NULL,'PROCESSING',?)",(user,body.request_key,body.text,time.time()))
                db.execute('INSERT INTO chat_request_epochs VALUES(?,?,?)',(user,body.request_key,bank._safety_epoch(db,user)))
            epoch_row=db.execute('SELECT epoch FROM chat_request_epochs WHERE user_id=? AND request_key=?',(user,body.request_key)).fetchone()
            epoch=epoch_row['epoch'] if epoch_row else ''
            request_order=db.execute('SELECT rowid FROM chat_requests WHERE user_id=? AND request_key=?',(user,body.request_key)).fetchone()[0]
        try:
            return run_chat(body,user,epoch,session['token_hash'],request_order)
        except Exception:
            with bank.transaction() as db:
                db.execute("UPDATE chat_requests SET status='FAILED' WHERE user_id=? AND request_key=?",(user,body.request_key))
            raise

    def run_chat(body,user,epoch,session_id,request_order):
        context=get_context(user,body.text,session_id)
        finding=assess_request(body.text)
        local=None if finding else memory.handle(user,body.text,body.request_key)
        if body.goal_mode and not finding and not local:
            goal_access.check_target(user,session_id,epoch,True,body.goal_id,body.goal_version)
            answer=goal_loop.handle(user,session_id,epoch,body.text,body.request_key)
            if answer is not None:
                return finish_chat(body,user,{**answer,'planner_mode':planner.mode})
        if (finding or local) and context.pop('pending_birthday',None) is not None:
            save_context(user,context,epoch,session_id,request_order)
        if not finding and not local:
            birthday=birthday_chat.handle(user,body.text,body.request_key,epoch,session_id,request_order,context)
            if birthday is not None:
                birthday=dict(birthday)
                if not birthday.pop('_birthday_committed',False):
                    save_context(user,context,epoch,session_id,request_order)
                birthday['planner_mode']=planner.mode
                return finish_chat(body,user,birthday)
        bound_transfer=context.get('pending_transfer')
        bound_operation_id=bound_transfer.get('operation_id') if isinstance(bound_transfer,dict) else None
        recipient=explicit_recipient(body.text) if not finding and not local else (False,None)
        if recipient[0]:
            context.pop('pending_transfer',None)
        language=inspect_language(body.text,context) if not finding and not local else None
        if language is not None and language.clear_pending:
            context.pop('pending_transfer',None)
            bound_operation_id=None
        if not finding and not local:
            bill_pending=start_bill_question(body.text,bank.now())
            if bill_pending is not None:
                for slot in ('pending_transfer','pending_card','recent_card','pending_virtual_card','pending_wealth'):
                    context.pop(slot,None)
                context['pending_bill']=bill_pending
            elif bill_answer(body.text,context.get('pending_bill'),bank.now()) is None:
                context.pop('pending_bill',None)
        plan=None
        if not finding and not local:
            plan,created=plans.reserve(user,body.request_key,body.text,epoch)
            with bank.transaction() as db:
                current_epoch=bank._safety_epoch(db,user)
            if current_epoch!=epoch:
                if plan['status'] in ('RUNNING','PLANNING'):
                    plan=plans.control(user,plan['id'],plan['version'],'pause',epoch,
                                       on_stop=lambda db,p:stop_plan(db,p,user,'pause'))
                return finish_chat(body,user,{'message':'该请求早于最近风险提示，已失效。请重新提出业务需求。','cards':[],
                    'tasks':[{'type':'plan','state':'BLOCKED','code':'REQUEST_SUPERSEDED'}],'planner_mode':planner.mode})
            if plan['status']=='PAUSED' and (plan['reason'] in ('TOOL_FAILURE','PLANNING_FAILED')):
                plan=plans.control(user,plan['id'],plan['version'],'resume',epoch)
                created=not plan['nodes']
            if plan['status']=='PLANNING':
                if not created and plan['reason']!='PLANNING_FAILED':
                    raise PlanError('PLAN_PENDING','原规划状态尚未核对，不能重新生成计划。')
                try:
                    if language is not None:
                        actions=validate_plan({'actions':[{'type':'clarify','message':language.message}]})
                    else:
                        actions=validate_plan({'actions':run_tool(user,'planner',lambda:planner.plan(body.text,context))})
                except Exception:
                    try:plans.planning_failed(user,plan['id'],plan['version'])
                    except PlanError:pass
                    raise
                kinds=tuple(a['type'] for a in actions)
                actions=[{**a,'message':language.message if language is not None else
                          render_clarify(a['message'],body.text,context,kinds,planner.mode)[0]}
                         if a['type']=='clarify' else a for a in actions]
                plan=plans.freeze(user,plan['id'],plan['version'],[{'idx':i,'action':a,'deps':[i-1] if i else []} for i,a in enumerate(actions)])
            elif plan['status'] not in ('RUNNING','DONE'):
                raise PlanError('PLAN_STOPPED','原计划已暂停或转为本地核查，请核对任务进度。')
        actions=[n['action'] for n in plan['nodes']] if plan else []
        action_kinds=tuple(a['type'] for a in actions)
        cards=[]
        lines=[]
        tasks=[]
        if local:
            cards=local['cards']
            lines=[local['message']]
            tasks=local['tasks']
        if finding:
            hold=bank.activate_safety_hold(user,finding,body.request_key) if finding['hold'] else None
            cards.append({'type':'safety','finding':finding,'hold_id':hold['id'] if hold else None})
            lines.append('发现风险线索，本次请求未执行业务，转账已暂停。' if hold else '这类话术存在风险，可以先核实，不必按对方指引操作。')
            tasks.append({'type':'safety','state':'BLOCKED' if hold else 'COMPLETED','code':'SCAM_RISK' if hold else 'SAFETY_ADVICE'})
        for index,action in enumerate(actions):
            if action['type']!='clarify':
                context.pop('pending_bill',None)
            plan=plans.get(user,plan['id'])
            node=plan['nodes'][index]
            if node['state'] in ('DONE','PREPARED','BLOCKED'):
                saved=node['result'] or {}
                cards.extend(saved.get('cards',[]))
                if action['type']=='clarify' and saved:
                    if language is not None:
                        text,reply_source=language.message,'template'
                    else:
                        text,reply_source=render_clarify('\n'.join(saved.get('lines',[])),body.text,context,action_kinds,planner.mode)
                    lines.append(text)
                    tasks.extend([{**t,'reply_source':reply_source} for t in saved.get('tasks',[])] or
                                 [{'type':'clarify','state':'WAITING_USER','reply_source':reply_source}])
                else:
                    lines.extend(saved.get('lines',[]));tasks.extend(saved.get('tasks',[]))
                if not saved and node['state']=='BLOCKED':tasks.append({'type':node['kind'],'state':'BLOCKED','code':node['error_code']})
                continue
            if plan['status']!='RUNNING':
                break
            with bank.transaction() as db:
                current_epoch=bank._safety_epoch(db,user)
            if current_epoch!=epoch:
                plans.control(user,plan['id'],plan['version'],'pause',epoch,on_stop=lambda db,p:stop_plan(db,p,user,'pause'))
                lines.append('原计划已因风险状态更新而停止，请重新提出独立业务。')
                tasks.append({'type':node['kind'],'state':'BLOCKED','code':'REQUEST_SUPERSEDED'})
                break
            plan,token=plans.claim(user,plan['id'],index,plan['version'],epoch)
            plan_ticket={'id':plan['id'],'idx':index,'token':token}
            start_cards,start_lines,start_tasks=len(cards),len(lines),len(tasks)
            key=node['effect_key']
            kind=action['type']
            if kind in ('bill','subscriptions','cancel_subscription'):
                context.pop('pending_transfer',None)
            if kind in ('bill','balance','subscriptions','cancel_subscription','transfer'):
                context.pop('pending_card',None)
                context.pop('recent_card',None)
                context.pop('pending_virtual_card',None)
            if kind not in ('wealth_trade','clarify'):
                context.pop('pending_wealth',None)
            task_state='COMPLETED' if kind in ('bill','balance','wealth') else 'WAITING_USER'
            try:
                with guarded(user,{'bill':'bill','transfer':'transfer','subscriptions':'subscriptions','cancel_subscription':'subscriptions',
                                   'cards':'cards','card_status':'cards','card_stop':'cards','card_correct':'cards','virtual_card_apply':'cards','wealth':'wealth','wealth_trade':'wealth'}.get(kind)):
                    if kind=='wealth':
                        data=bank.wealth(user)
                        cards.append({'type':'wealth','wealth':data})
                        lines.append('已读取本人模拟持仓和合成产品；固定演示估值不代表真实报价或预期收益。')
                    elif kind=='wealth_trade':
                        for slot in ('pending_transfer','pending_card','recent_card','pending_virtual_card'):
                            context.pop(slot,None)
                        if re.search(r'申购|赎回|购买|买|卖出',body.text):
                            context.pop('pending_wealth',None)
                        answer=wealth_dialogue.handle(user,action,key,context,epoch,plan_ticket)
                        if 'pending_wealth' in answer['working']:
                            answer['working']['pending_wealth'].update(source_plan_id=plan_ticket['id'],source_plan_idx=plan_ticket['idx'])
                        context.pop('pending_wealth',None)
                        context.update(answer['working'])
                        cards.extend(answer['cards']);lines.extend(answer['lines']);task_state=answer['state']
                    elif kind=='virtual_card_apply':
                        context.pop('pending_transfer',None)
                        context.pop('pending_card',None)
                        context.pop('recent_card',None)
                        if (re.search(r'理财|申购|赎回|演示产品',body.text)
                                or re.fullmatch(r'(?:算了|不用(?:买)?了|(?:先)?不(?:买|申购|赎回)(?:了)?)[。.!！\s]*',body.text.strip())):
                            context.pop('pending_wealth',None)
                        if re.search(r'虚拟(?:银行)?卡',body.text):
                            context.pop('pending_virtual_card',None)
                        answer=virtual_dialogue.handle(user,action['account'],key,context,plan_ticket)
                        if 'pending_virtual_card' in answer['working']:
                            answer['working']['pending_virtual_card'].update(source_plan_id=plan_ticket['id'],source_plan_idx=plan_ticket['idx'])
                        context.pop('pending_virtual_card',None)
                        context.update(answer['working'])
                        cards.extend(answer['cards']);lines.extend(answer['lines']);task_state=answer['state']
                    elif kind in ('cards','card_status','card_stop','card_correct'):
                        context.pop('pending_virtual_card',None)
                        context.pop('pending_transfer',None)
                        answer=card_chat.handle(user,action,body.text,key,context,plan_ticket)
                        if 'pending_card' in answer['working']:
                            answer['working']['pending_card'].update(source_plan_id=plan_ticket['id'],source_plan_idx=plan_ticket['idx'])
                        context.pop('pending_card',None)
                        context.pop('recent_card',None)
                        context.update(answer['working'])
                        cards.extend(answer['cards'])
                        lines.extend(answer['lines'])
                        task_state=answer['state']
                    elif kind=='bill':
                        data=bank.analyze(user,action['period'])
                        cards.append({'type':'bill','bill':data})
                        lines.append(f"{data['period']} 净支出 {data['net_expense_cents']/100:,.2f} 元，统计基于 {len(data['evidence_ids'])} 条模拟流水。")
                    elif kind=='balance':
                        cards.append({'type':'balance','accounts':bank.snapshot(user)['accounts']})
                        lines.append('已读取你的模拟账户余额。')
                    elif kind=='subscriptions':
                        op=bank.prepare_subscription_query(user,key,plan_ticket=plan_ticket)
                        cards.append({'type':'operation','operation':op})
                        lines.append('查询订阅和代扣需要你的确认，请核对下方卡片。')
                    elif kind=='transfer':
                        candidates=bank.resolve_payee(user,action['payee'])
                        if len(candidates)==1:
                            replace_id=None
                            if re.search(r'还是|改成|改为|换成',body.text):
                                bound_id=bound_operation_id
                                if isinstance(bound_id,str) and bound_id:
                                    try:
                                        previous=bank.operation(user,bound_id)
                                    except DomainError as error:
                                        if error.code!='NOT_FOUND':
                                            raise
                                        previous=None
                                    if (isinstance(previous,dict) and previous.get('type')=='transfer'
                                            and previous.get('status') in ('WAITING_CONFIRMATION','WAITING_VERIFICATION')):
                                        replace_id=bound_id
                            op=bank.prepare_transfer(user,candidates[0]['id'],action['amount_cents'],key,replace_id=replace_id,expected_epoch=epoch,plan_ticket=plan_ticket)
                            cards.append({'type':'operation','operation':op})
                            lines.append('转账已准备，尚未扣款。请核对收款人与金额。')
                            context['pending_transfer']={'payee':action['payee'],'operation_id':op['id']}
                        elif len(candidates)>1:
                            cards.append({'type':'payees','candidates':candidates,'amount_cents':action['amount_cents'],'selection_key':key,'safety_epoch':epoch})
                            lines.append('有同名收款人，请选择具体对象后再准备转账。')
                        else:
                            raise DomainError('PAYEE_NOT_FOUND','没有找到该收款人，请提供已登记的姓名或手机号。')
                    elif kind=='cancel_subscription':
                        try:
                            subs=bank.subscriptions(user)
                        except DomainError as error:
                            if error.code!='SUBSCRIPTION_CONSENT_REQUIRED':
                                raise
                            op=bank.prepare_subscription_query(user,key,proposed_cancel=action['merchant'],plan_ticket=plan_ticket)
                            cards.append({'type':'operation','operation':op})
                            lines.append('请先确认查询订阅；查询后会准备该商户的取消确认，确认查询不会直接取消订阅。')
                            tasks.append({'type':kind,'state':'WAITING_USER'})
                            plans.settle(user,plan['id'],index,token,'PREPARED',{'cards':cards[start_cards:],'lines':lines[start_lines:],'tasks':tasks[start_tasks:]})
                            continue
                        matches=[s for s in subs if action['merchant']==s['merchant'] or action['merchant'] in s['merchant']]
                        if len(matches)!=1:
                            lines.append('请从订阅列表中选择具体商户。')
                        else:
                            op=bank.prepare_cancel(user,matches[0]['id'],key,plan_ticket=plan_ticket)
                            cards.append({'type':'operation','operation':op})
                            lines.append('已准备取消续费，请核对影响后确认。')
                    else:
                        if language is not None:
                            text,reply_source=language.message,'template'
                        else:
                            text,reply_source=render_clarify(action['message'],body.text,context,action_kinds,planner.mode)
                        lines.append(text)
                        if (re.search(r'理财|申购|赎回|演示产品',body.text)
                                or re.fullmatch(r'(?:算了|不用(?:买)?了|(?:先)?不(?:买|申购|赎回)(?:了)?)[。.!！\s]*',body.text.strip())):
                            context.pop('pending_wealth',None)
                        if re.search(r'虚拟(?:银行)?卡',body.text):
                            context.pop('pending_virtual_card',None)
                        if (language is None or language.kind=='ambiguous_amount') and recipient[1] is not None:
                            context.pop('pending_card',None)
                            context.pop('recent_card',None)
                            context.pop('pending_virtual_card',None)
                            context['pending_transfer']=dict(recipient[1])
                    task={'type':kind,'state':task_state}
                    if kind=='clarify':task['reply_source']=reply_source
                    tasks.append(task)
                settlement='PREPARED' if task_state=='WAITING_USER' else 'BLOCKED' if task_state=='BLOCKED' else 'DONE'
                plans.settle(user,plan['id'],index,token,settlement,{'cards':cards[start_cards:],'lines':lines[start_lines:],'tasks':tasks[start_tasks:]},
                             'CARD_REVIEW_REQUIRED' if task_state=='BLOCKED' else None)
            except (ToolPaused,ToolFailure) as error:
                plans.settle(user,plan['id'],index,token,'FAILED',error_code=error.code)
                raise
            except DomainError as error:
                lines.append(error.message)
                tasks.append({'type':kind,'state':'BLOCKED','code':error.code})
                plans.settle(user,plan['id'],index,token,'BLOCKED',{'cards':cards[start_cards:],'lines':lines[start_lines:],'tasks':tasks[start_tasks:]},error.code)
        if not finding and not local:
            save_context(user,context,epoch,session_id,request_order)
        result={'message':'\n'.join(lines),'cards':cards,'tasks':tasks,'planner_mode':planner.mode}
        return finish_chat(body,user,result)

    def finish_chat(body,user,result):
        with bank.transaction() as db:
            ts=bank.now().isoformat()
            db.execute('INSERT INTO messages(user_id,role,content,request_key,created_at) VALUES(?,?,?,?,?)',(user,'user',body.text,body.request_key,ts))
            db.execute('INSERT INTO messages(user_id,role,content,request_key,created_at) VALUES(?,?,?,?,?)',(user,'assistant',json.dumps(result,ensure_ascii=False),body.request_key,ts))
            db.execute("UPDATE chat_requests SET status='DONE',response=? WHERE user_id=? AND request_key=?",(json.dumps(result,ensure_ascii=False),user,body.request_key))
        return result

    frontend=ROOT/'frontend'/'dist'
    @app.get('/operator',include_in_schema=False)
    def operator_entry():
        index=frontend/'index.html'
        if not index.is_file():
            raise DomainError('FRONTEND_UNAVAILABLE','PC界面尚未构建，请先核对本机部署。',503)
        return FileResponse(index)

    if frontend.exists():
        app.mount('/',StaticFiles(directory=frontend,html=True),name='frontend')
    return app
