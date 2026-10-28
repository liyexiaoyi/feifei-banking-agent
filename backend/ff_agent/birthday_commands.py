"""生日流程命令辅助：本机模拟，不连接商户或银行。调用方必须已持有 Bank.transaction。"""
import hashlib
import json
import re
from datetime import date

from .birthday_store import DomainError, load_flow, load_nodes, view_flow, cas_flow, cas_node
from .birthday_provenance import observe_node
from .birthday_reselection import reselect_node

_KEY = re.compile(r'[A-Za-z0-9_.:-]{8,64}')
_GIFT_IDX = {'flowers': 2, 'cake': 3}


def _need_tx(db):
    if not getattr(db, 'in_transaction', False):
        raise DomainError('TX_REQUIRED', '写操作必须在事务内执行', 500)


def _key(key):
    if type(key) is not str or not key.isascii() or not _KEY.fullmatch(key):
        raise DomainError('INVALID_KEY', '请求键无效', 400)


def _hash(flow_id, action, expected_version, args):
    raw = json.dumps({'flow_id': flow_id, 'action': action, 'expected_version': expected_version,
                      'args': args}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def command_start(bank, db, user, flow_id, action, args, expected_version, key, allowed_statuses):
    _need_tx(db)
    if type(user) is not str or type(flow_id) is not str or not flow_id or type(action) is not str:
        raise DomainError('BAD_REQUEST', '请求参数无效', 400)
    _key(key)
    if type(expected_version) is not int or expected_version < 1:
        raise DomainError('INVALID_VERSION', '版本无效', 400)
    bank._user(db, user)
    flow = load_flow(db, user, flow_id)
    h = _hash(flow_id, action, expected_version, args)
    req = db.execute('SELECT action,args_hash,flow_id FROM birthday_requests WHERE user_id=? AND request_key=?',
                     (user, key)).fetchone()
    if req:
        if req['action'] != action or req['args_hash'] != h or req['flow_id'] != flow_id:
            raise DomainError('KEY_CONFLICT', '请求键冲突', 409)
        return {'flow': flow, 'action': action, 'args_hash': h, 'request_key': key,
                'expected_version': expected_version, 'replay': True}
    if flow['version'] != expected_version:
        raise DomainError('VERSION_CONFLICT', '版本冲突', 409)
    if flow['status'] not in tuple(allowed_statuses):
        raise DomainError('INVALID_STATE', '当前状态不允许该操作', 409)
    return {'flow': flow, 'action': action, 'args_hash': h, 'request_key': key,
            'expected_version': expected_version, 'replay': False}


def assert_current(bank, db, user, flow):
    _need_tx(db)
    bank._user(db, user)
    if flow['status'] not in ('ACTIVE', 'PAUSED'):
        raise DomainError('INVALID_STATE', '当前状态不允许该操作', 409)
    acct = db.execute('SELECT name FROM accounts WHERE id=? AND user_id=?',
                      (flow['account_id'], user)).fetchone()
    if not acct or str(acct['name']) != flow['account_name']:
        raise DomainError('ACCOUNT_CHANGED', '账户已变化，请重新建立流程', 409)
    if bank._safety_epoch(db, user) != flow['risk_epoch']:
        raise DomainError('REQUEST_SUPERSEDED', '该流程早于最近一次风险提示，已失效。请重新提出业务需求。', 409)
    if date.fromisoformat(flow['event_date']) < bank.now().date():
        raise DomainError('DATE_PASSED', '生日日期已过', 409)
    bank._unlocked(db, user)
    bank._assert_transfer_allowed(db, user)


def _sync(bank, db, user, flow):
    from .birthday_store import sync_nodes
    sync_nodes(bank, db, user, flow['id'])
    nodes = load_nodes(db, user, flow['id'])
    return nodes, {i: observe_node(bank, db, user, flow, n) for i, n in nodes.items()}


def command_finish(bank, db, user, ctx, changes=None, claimed=False):
    _need_tx(db)
    if ctx['replay']:
        raise DomainError('REPLAY_NO_WRITE', '重放请求不可写入', 500)
    fid, ev = ctx['flow']['id'], ctx['expected_version']
    if claimed is True:
        flow = load_flow(db, user, fid)
        if flow['version'] != ev + 1:
            raise DomainError('VERSION_CONFLICT', '版本冲突', 409)
    else:
        flow = cas_flow(db, user, fid, ev, changes if changes is not None else {'status': ctx['flow']['status']})
    db.execute('INSERT INTO birthday_requests (user_id,request_key,action,args_hash,flow_id,created_at)'
               ' VALUES (?,?,?,?,?,?)', (user, ctx['request_key'], ctx['action'], ctx['args_hash'], fid,
                                          bank.now().timestamp()))
    bank._event(db, user, None, 'BIRTHDAY_COMMAND', f"生日流程 {fid} 命令 {ctx['action']} / 本机模拟")
    return view_flow(bank, db, user, flow)


def lookup_request(bank, db, user, key):
    _key(key)
    bank._user(db, user)
    req = db.execute('SELECT action,flow_id FROM birthday_requests WHERE user_id=? AND request_key=?',
                     (user, key)).fetchone()
    if not req:
        raise DomainError('NOT_FOUND', '未找到可访问的数据', 404)
    return {'action': req['action'], 'flow': view_flow(bank, db, user, load_flow(db, user, req['flow_id'])),
            'simulated': True, 'merchant_connected': False}


def decide_locked(bank, db, user, ctx, cake):
    if type(cake) is not bool:
        raise DomainError('INVALID_CHOICE', '蛋糕选择无效', 400)
    flow = ctx['flow']
    assert_current(bank, db, user, flow)
    nodes, obs = _sync(bank, db, user, flow)
    if obs.get(0) != 'SUCCEEDED':
        raise DomainError('NOT_READY', '前置步骤尚未完成', 409)
    n1 = nodes.get(1)
    if not n1 or n1['state'] != 'PENDING':
        raise DomainError('CHOICE_LOCKED', '选择已完成不可更改', 409)
    cas_flow(db, user, flow['id'], ctx['expected_version'], {'cake_choice': int(cake)})
    cas_node(db, user, flow['id'], 1, n1['version'], {'state': 'SUCCEEDED'})
    return command_finish(bank, db, user, ctx, claimed=True)


def select_locked(bank, db, user, ctx, gift, item_id):
    if type(gift) is not str or gift not in _GIFT_IDX or type(item_id) is not str or not item_id:
        raise DomainError('BAD_REQUEST', '礼物选择无效', 400)
    flow = ctx['flow']
    assert_current(bank, db, user, flow)
    nodes, _ = _sync(bank, db, user, flow)
    idx = _GIFT_IDX[gift]
    if idx not in nodes:
        raise DomainError('BAD_GRAPH', '生日流程图无效', 500)
    reselect_node(bank, db, user, flow['id'], idx, ctx['expected_version'], nodes[idx]['version'], item_id)
    return command_finish(bank, db, user, ctx)
