"""Local explicit notes, related links and bounded working/recent context.

Inspired by structured agent memory; no vector model, automatic personal-fact
extraction, or authorization is delegated to a language model.
"""
import hashlib
import json
import re
import unicodedata
import uuid

from .bank import DomainError, PENDING
from .card_working import validate_card_slots
from .virtual_working import validate_virtual_pending
from .wealth_working import validate_wealth_pending
from .birthday_chat_slot import validate_birthday_pending
from .card_query import normalize_card_text, resolve_card_query
from .clarification import sanitize_legacy_reply
from .dialog_state import valid_bill_pending

REMEMBER = re.compile(r'^(?:请|帮我)?记住[\s，,:：]*(.*)$', re.S)
FORGET_HELP = re.compile(r'^(?:忘记|忘掉|移出记忆)[\s，,:：]*(?:了[\s，,:：]*)?(?:[。！？?!]*$|怎么|如何|怎样|密码|验证码)')
RECALL = re.compile(r'你(?:还)?记得|(?:查看|列出|查找).{0,5}记忆|回忆一下|(?:我的.*偏好|我常用收款人)(?:是什么|是谁|有哪些|呢)[？?]?$')
RECALL_ALL = re.compile(r'^(?:(?:请)?(?:查看|列出|查找)(?:我的|所有|全部|我的所有|我的全部)?记忆|你(?:还)?记得我(?:什么|哪些(?:事|事情|偏好)?)|回忆一下)[？?。！!]*$')
ASSISTANT_NAME = re.compile(r'给你.{0,10}(?:取|起).{0,4}名|(?:你|自己)(?:的(?:名字|昵称)|叫什么|叫啥)')
SENSITIVE = re.compile(r'密码|验证码|密钥|口令|私钥|token|secret|authorization|sk-[a-z0-9_-]+|(?<!\d)\d{11,19}(?!\d)', re.I)
AUTHORITY = re.compile(r'免确认|无需确认|跳过.{0,5}(?:确认|验证)|忽略.{0,5}(?:规则|安全)|已经.{0,6}(?:授权|验证)|已验证|确认过|已转账|转账成功|付款成功|自动转账')
TOPICS = {'账单':('账单','退款','收支','消费'), '订阅':('订阅','续费','会员','代扣'),
          '转账':('转账','收款人','妈妈','爸爸'), '交流':('简洁','详细','称呼','回复','说话')}


def clean_excerpt(text, limit=240):
    if SENSITIVE.search(text):
        return '[敏感内容未加入近期上下文]'
    return text[:limit]


def tokens(text):
    text=unicodedata.normalize('NFKC',text).lower()
    words=set(re.findall(r'[a-z0-9_]{2,}',text))
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        words.update(run[i:i+2] for i in range(len(run)-1))
    return words


def tags(text):
    return [name for name,terms in TOPICS.items() if any(term in text for term in terms)]


class Memory:
    def __init__(self, bank):
        self.bank=bank

    def _validated(self, content):
        content=content.strip()
        if not 1<=len(content)<=300:
            raise DomainError('INVALID_MEMORY','记忆内容需要 1 至 300 字。')
        if SENSITIVE.search(content) or AUTHORITY.search(content):
            raise DomainError('MEMORY_NOT_ALLOWED','支付凭据、授权声明或交易结果不能作为个人笔记保存。请只记录非敏感偏好。')
        return content,hashlib.sha256(content.encode()).hexdigest()

    def _row(self, db, user, note_id, removed=False):
        row=db.execute('SELECT * FROM memory_notes WHERE id=? AND user_id=?',(note_id,user)).fetchone()
        if not row or (row['removed'] and not removed):
            raise DomainError('MEMORY_NOT_FOUND','未找到本人可使用的记忆。',404)
        return row

    def _view(self, db, row):
        links=db.execute('SELECT left_id,right_id FROM memory_links WHERE user_id=? AND (left_id=? OR right_id=?)',(row['user_id'],row['id'],row['id'])).fetchall()
        return {**{k:row[k] for k in ('id','content','version','created_at','updated_at')},
                'source':'user_explicit','tags':json.loads(row['tags']),
                'related_ids':[r['right_id'] if r['left_id']==row['id'] else r['left_id'] for r in links]}

    def _link(self, db, user, note_id):
        db.execute('DELETE FROM memory_links WHERE user_id=? AND (left_id=? OR right_id=?)',(user,note_id,note_id))
        row=self._row(db,user,note_id)
        own_tokens=tokens(row['content'])
        own_tags=set(json.loads(row['tags']))
        for other in db.execute('SELECT * FROM memory_notes WHERE user_id=? AND removed=0 AND id!=?',(user,note_id)):
            if own_tags.intersection(json.loads(other['tags'])) or len(own_tokens.intersection(tokens(other['content'])))>=2:
                left,right=sorted((note_id,other['id']))
                db.execute('INSERT OR IGNORE INTO memory_links VALUES(?,?,?)',(user,left,right))

    def remember(self, user, content, request_key):
        content,digest=self._validated(content)
        with self.bank.transaction() as db:
            self.bank._user(db,user)
            previous=db.execute('SELECT * FROM memory_requests WHERE user_id=? AND request_key=?',(user,request_key)).fetchone()
            if previous:
                if previous['content_hash']!=digest:
                    raise DomainError('IDEMPOTENCY_CONFLICT','同一请求编号的记忆内容已改变。',409)
                return self._view(db,self._row(db,user,previous['note_id']))
            row=db.execute('SELECT * FROM memory_notes WHERE user_id=? AND content_hash=? AND removed=0',(user,digest)).fetchone()
            if row:
                note_id=row['id']
            else:
                note_id='mem_'+uuid.uuid4().hex
                ts=self.bank.now().isoformat()
                db.execute('INSERT INTO memory_notes VALUES(?,?,?,?,?,1,?,?,0)',(note_id,user,content,digest,json.dumps(tags(content),ensure_ascii=False),ts,ts))
                self._link(db,user,note_id)
                self.bank._event(db,user,None,'MEMORY_SAVED','保存一条用户明确提供的非敏感笔记')
            db.execute('INSERT INTO memory_requests VALUES(?,?,?,?)',(user,request_key,digest,note_id))
            return self._view(db,self._row(db,user,note_id))

    def get(self, user, note_id):
        with self.bank.transaction() as db:
            self.bank._user(db,user)
            return self._view(db,self._row(db,user,note_id))

    def count(self, user):
        with self.bank.transaction() as db:
            self.bank._user(db,user)
            return db.execute('SELECT COUNT(*) FROM memory_notes WHERE user_id=? AND removed=0',(user,)).fetchone()[0]

    def recall(self, user, query, limit=50):
        with self.bank.transaction() as db:
            self.bank._user(db,user)
            words=tokens(query)
            topics=set(tags(query))
            scored=[]
            for row in db.execute('SELECT * FROM memory_notes WHERE user_id=? AND removed=0 ORDER BY updated_at DESC,rowid DESC',(user,)):
                score=len(words.intersection(tokens(row['content'])))+3*len(topics.intersection(json.loads(row['tags'])))
                if not query.strip() or score:
                    scored.append((score,self._view(db,row)))
            scored.sort(key=lambda item:-item[0])
            return [note for _,note in scored[:max(0,min(limit,50))]]

    def update(self, user, note_id, content, expected_version):
        content,digest=self._validated(content)
        with self.bank.transaction() as db:
            self.bank._user(db,user)
            row=self._row(db,user,note_id)
            if row['version']!=expected_version:
                if row['version']==expected_version+1 and row['content_hash']==digest:
                    return self._view(db,row)
                raise DomainError('MEMORY_CONFLICT','记忆已有更新，请刷新后再修改。',409)
            duplicate=db.execute('SELECT id FROM memory_notes WHERE user_id=? AND content_hash=? AND removed=0 AND id!=?',(user,digest,note_id)).fetchone()
            if duplicate:
                raise DomainError('MEMORY_CONFLICT','相同内容已存在，请保留已有笔记。',409)
            db.execute('UPDATE memory_notes SET content=?,content_hash=?,tags=?,version=version+1,updated_at=? WHERE id=?',
                       (content,digest,json.dumps(tags(content),ensure_ascii=False),self.bank.now().isoformat(),note_id))
            self._link(db,user,note_id)
            self.bank._event(db,user,None,'MEMORY_UPDATED','更新本人笔记，旧内容不再参与检索')
            return self._view(db,self._row(db,user,note_id))

    def forget(self, user, note_id, expected_version):
        with self.bank.transaction() as db:
            self.bank._user(db,user)
            return self._forget_in(db,user,note_id,expected_version)

    def _forget_in(self, db, user, note_id, expected_version):
        # Runs inside the caller's Bank.transaction; never opens a nested one.
        row=self._row(db,user,note_id,removed=True)
        if not row['removed']:
            if row['version']!=expected_version:
                raise DomainError('MEMORY_CONFLICT','记忆已有更新，请刷新后再移出。',409)
            db.execute("UPDATE memory_notes SET content='',content_hash=?,tags='[]',removed=1,version=version+1,updated_at=? WHERE id=?",('removed:'+note_id,self.bank.now().isoformat(),note_id))
            db.execute('DELETE FROM memory_links WHERE user_id=? AND (left_id=? OR right_id=?)',(user,note_id,note_id))
            self.bank._event(db,user,None,'MEMORY_REMOVED','移出本人笔记，内容及关联不再参与检索')
        return {'forgotten':True,'id':note_id}

    def save_working(self, user, value, epoch, session_id=None, request_order=None):
        # Only business slots are persisted, never retrieved notes or raw history.
        value={k:v for k,v in value.items() if k in ('pending_transfer','selected_merchant','pending_card','recent_card','pending_virtual_card','pending_wealth','pending_bill','pending_birthday')}
        if valid_bill_pending(value.get('pending_bill'),self.bank.now().timestamp()):
            value['pending_bill']={k:value['pending_bill'].get(k) for k in ('kind','started_at','year')}
        else:
            value.pop('pending_bill',None)
        if value.get('pending_transfer'):
            value['pending_transfer']={**value['pending_transfer']}
            value['pending_transfer'].setdefault('started_at',self.bank.now().timestamp())
        value['working_updated_at']=self.bank.now().timestamp()
        value['safety_epoch']=epoch
        value['session_id']=session_id
        value['request_order']=request_order
        with self.bank.transaction() as db:
            if epoch==self.bank._safety_epoch(db,user):
                previous=db.execute('SELECT value FROM contexts WHERE user_id=?',(user,)).fetchone()
                previous=json.loads(previous['value']) if previous else {}
                if request_order is not None and (previous.get('request_order') or 0)>request_order:
                    return
                # These are server-owned references, never note/history fields.
                # Stamp a new clarification once; resaving must not extend its TTL.
                for slot in ('pending_card','pending_virtual_card','pending_wealth'):
                    pending=value.get(slot)
                    if isinstance(pending,dict):
                        pending={**pending}
                        pending.setdefault('started_at',self.bank.now().timestamp())
                        if 'operation_watermark' not in pending:
                            pending['operation_watermark']=db.execute(
                                'SELECT COALESCE(MAX(rowid),0) FROM operations WHERE user_id=?',(user,)).fetchone()[0]
                        value[slot]=pending
                validated=validate_card_slots(db,user,value,self.bank.now().timestamp())
                validated.update(validate_virtual_pending(db,user,value,self.bank.now().timestamp()))
                validated.update(validate_wealth_pending(db,user,value,self.bank.now().timestamp()))
                validated.update(validate_birthday_pending(db,user,value,self.bank.now().timestamp(),epoch,session_id))
                value.pop('pending_birthday',None)
                value.pop('pending_card',None)
                value.pop('recent_card',None)
                value.pop('pending_virtual_card',None)
                value.pop('pending_wealth',None)
                value.update(validated)
                db.execute('INSERT INTO contexts VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET value=excluded.value',(user,json.dumps(value,ensure_ascii=False)))

    def context(self, user, query, session_id=None):
        with self.bank.transaction() as db:
            self.bank._user(db,user)
            row=db.execute('SELECT value FROM contexts WHERE user_id=?',(user,)).fetchone()
            stored=json.loads(row['value']) if row else {}
            fresh=0<=self.bank.now().timestamp()-stored.get('working_updated_at',0)<=600
            valid_working=fresh and stored.get('safety_epoch')==self.bank._safety_epoch(db,user) and stored.get('session_id')==session_id
            context={k:stored[k] for k in ('pending_transfer','selected_merchant') if k in stored} if valid_working else {}
            if valid_working and valid_bill_pending(stored.get('pending_bill'),self.bank.now().timestamp()):
                context['pending_bill']={k:stored['pending_bill'].get(k) for k in ('kind','started_at','year')}
            if valid_working:
                context.update(validate_card_slots(db,user,stored,self.bank.now().timestamp()))
                context.update(validate_virtual_pending(db,user,stored,self.bank.now().timestamp()))
                context.update(validate_wealth_pending(db,user,stored,self.bank.now().timestamp()))
                context.update(validate_birthday_pending(db,user,stored,self.bank.now().timestamp(),stored.get('safety_epoch'),session_id))
            pending=context.get('pending_transfer',{})
            if pending and not 0<=self.bank.now().timestamp()-pending.get('started_at',0)<=600:
                context.pop('pending_transfer',None)
            if pending.get('operation_id'):
                op=db.execute('SELECT status,expires_at FROM operations WHERE id=? AND user_id=?',(pending['operation_id'],user)).fetchone()
                if not op or op['status'] not in PENDING or op['expires_at']<=self.bank.now().timestamp():
                    context.pop('pending_transfer',None)
            rows=db.execute('SELECT role,content,request_key FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 16',(user,)).fetchall()
            recent=[]
            for message in reversed(rows):
                if message['role']=='assistant':
                    content=json.loads(message['content'])
                    original=db.execute('SELECT input FROM chat_requests WHERE user_id=? AND request_key=?',(user,message['request_key'])).fetchone()
                    content=sanitize_legacy_reply(content,original['input'] if original else '',mode=content.get('planner_mode','model'))
                    if any(card.get('type')=='memory' for card in content.get('cards',[])):
                        continue
                    text=content.get('message','')
                else:
                    text=message['content']
                    if REMEMBER.match(text) or RECALL.search(text) or re.match(r'^(?:忘掉|忘记|移出记忆)',text):
                        continue
                recent.append({'role':message['role'],'content':clean_excerpt(text)})
        context['recent_turns']=recent[-6:]
        context['memories']=[{k:n[k] for k in ('id','content','source','tags','version')} for n in self.recall(user,query,limit=3)]
        return context

    def refresh_cards(self, db, user, result, request_text=''):
        result=sanitize_legacy_reply(result,request_text,mode=result.get('planner_mode','model'))
        # Old memory snapshots are not another recall path after update/removal.
        has_memory=False
        for card in result.get('cards',[]):
            if card.get('type')=='memory':
                has_memory=True
                refreshed=[]
                for old in card.get('notes',[]):
                    row=db.execute('SELECT * FROM memory_notes WHERE user_id=? AND id=? AND removed=0',(user,old['id'])).fetchone()
                    if row:
                        refreshed.append(self._view(db,row))
                card['notes']=refreshed
        if has_memory and not any(c.get('notes') for c in result['cards'] if c.get('type')=='memory'):
            result['message']='没有可使用的相关笔记，已移出的记忆不会重新保存。'
        # Birthday cards are inert owned references; never replay budget/approval fields.
        for card in result.get('cards',[]):
            if isinstance(card,dict) and card.get('type')=='birthday_flow':
                fid=card.get('flow_id')
                owned=type(fid) is str and re.fullmatch(r'[0-9a-f]{32}',fid) and db.execute(
                    'SELECT id FROM birthday_flows WHERE id=? AND user_id=?',(fid,user)).fetchone()
                card.clear()
                card.update({'type':'birthday_flow','flow_id':fid if owned else None,'simulated':True,'merchant_connected':False})
        return result

    def handle(self, user, text, request_key):
        match=REMEMBER.match(text.strip())
        if match:
            note=self.remember(user,match[1],request_key)
            return {'message':'已记下这条偏好。它是你提供的笔记，不会代替任何业务确认。',
                    'cards':[{'type':'memory','action':'saved','notes':[note]}],
                    'tasks':[{'type':'memory','state':'COMPLETED'}]}
        if FORGET_HELP.match(text.strip()):
            return {'message':'你是在求助怎么操作，还是想移出一条笔记？',
                    'cards':[], 'tasks':[{'type':'memory','state':'WAITING_USER'}]}
        forget=re.match(r'^(?:忘掉|忘记|移出记忆)[\s：:，]*(.+)$',text.strip())
        if forget:
            return self._resolve_forget(user,forget[1])
        explicit_notes=bool(re.search(r'记忆|笔记',text))
        wants_status=bool(re.search(r'刚才|上次|最近',text) and re.search(r'转|操作|取消|挂失|解挂|银行卡|虚拟卡',text) and re.search(r'成功|完成|结果|状态|多少钱|多少元|怎么样',text)) and not explicit_notes
        virtual_status=bool(re.search(r'虚拟(?:银行)?卡',text) and re.search(
            r'(?:成功|完成|办好|开通)(?:了)?(?:吗|没)|申请(?:的)?(?:进度|状态|结果)',text)
            and not re.search(r'挂失|解挂',text)) and not explicit_notes
        wealth_status=bool(re.search(r'理财|申购|赎回|演示产品',text) and re.search(
            r'(?:成功|完成|办好)(?:了)?(?:吗|没)|(?:交易|申购|赎回)(?:的)?(?:进度|状态|结果)',text)) and not explicit_notes
        wants_status=wants_status or virtual_status or wealth_status
        assistant_name=bool(ASSISTANT_NAME.search(text)) and not explicit_notes
        if RECALL.search(text) and not wants_status and not assistant_name:
            notes=self.recall(user,'' if RECALL_ALL.fullmatch(text.strip()) else text)
            return {'message':'这是你之前明确让我保存的笔记。' if notes else '没有找到相关记忆，我不会猜测你曾经说过什么。',
                    'cards':[{'type':'memory','action':'recalled','notes':notes}], 'tasks':[{'type':'memory','state':'COMPLETED'}]}
        if wants_status:
            if re.search(r'理财|申购|赎回|演示产品',text):
                return self._wealth_status(user,text)
            if re.search(r'虚拟(?:银行)?卡',text) and not re.search(r'挂失|解挂',text):
                return self._virtual_status(user,text)
            if re.search(r'挂失|解挂|银行卡',text):
                if '取消挂失' in text and '取消本次挂失' not in text:
                    return None  # Do not guess withdrawal versus restoring a card.
                return self._card_status(user,text)
            if '转' in text:
                from .receipt_query import transfer_status
                return transfer_status(self.bank,user,text)
            operations=self.bank.snapshot(user)['operations']
            names=[name for name in ('妈妈','爸爸','林晓') if name in text]
            if names:
                operations=[op for op in operations if op['payload'].get('payee_name') in names]
            if '转' in text:
                operations=[op for op in operations if op['type']=='transfer']
            elif re.search(r'取消|退订|续费',text) and re.search(r'订阅|会员|续费|代扣|退订',text):
                operations=[op for op in operations if op['type']=='subscription_cancel']
            elif re.search(r'订阅|代扣|会员',text):
                operations=[op for op in operations if op['type']=='subscription_query']
            if not operations:
                return {'message':'没有查到符合条件的本人操作记录，不能据此认定已经付款。','cards':[],
                        'tasks':[{'type':'operation_status','state':'COMPLETED'}]}
            op=operations[0]
            states={'SUCCEEDED':'已完成','CANCELLED':'已取消，未执行','EXPIRED':'已过期，未执行',
                    'WAITING_CONFIRMATION':'等待你的确认，尚未执行','WAITING_VERIFICATION':'等待验证，尚未执行'}
            return {'message':'按本人银行操作记录，最近一笔相关操作'+states.get(op['status'],'结果仍需核实')+'。',
                    'cards':[{'type':'operation','operation':op}], 'tasks':[{'type':'operation_status','state':'COMPLETED'}]}
        return None

    def _resolve_forget(self, user, query):
        query=query.strip()
        # Same reply for unknown, foreign and already-removed IDs; never echoes content or ID.
        missing={'message':'没有找到可移出的本人记忆，未移出任何内容。','cards':[{'type':'memory','action':'recalled','notes':[]}],
                 'tasks':[{'type':'memory','state':'COMPLETED'}]}
        try:
            # Uniqueness check and removal share one BEGIN IMMEDIATE transaction.
            with self.bank.transaction() as db:
                self.bank._user(db,user)
                if query.lower().startswith('mem_'):
                    # ID-shaped input: exact own active ID only, no content fallback.
                    rows=db.execute('SELECT * FROM memory_notes WHERE user_id=? AND id=? AND removed=0',(user,query)).fetchall()
                    if len(rows)!=1:
                        return missing
                else:
                    # Full-set substring match, independent of recall tokens and top-50 ranking.
                    core=''.join(char for char in query if char.isalnum())
                    # Generic demonstratives/pronouns never select a note by substring.
                    if not core or re.fullmatch(r'(?:刚才|刚刚|上面|之前|最近|上)?(?:这|那|此|该|它|他|她)?(?:一)?(?:个|条|句|些|项|则|们)?(?:记忆|笔记|偏好|内容)?(?:吧|了|啊|呀|吗|呢)?',core) and re.search(r'[这那此该它他她]|上一?条',core):
                        return {'message':'请在记忆与偏好中选择具体笔记，再移出。',
                                'cards':[{'type':'memory','action':'recalled','notes':[]}],
                                'tasks':[{'type':'memory','state':'WAITING_USER'}]}
                    rows=db.execute('SELECT * FROM memory_notes WHERE user_id=? AND removed=0 AND instr(content,?)>0 '
                                    'ORDER BY updated_at DESC,rowid DESC LIMIT 51',(user,query)).fetchall() if query else []
                    if len(rows)!=1:
                        return {'message':'请在记忆与偏好中选择具体笔记，避免移出其他内容。',
                                'cards':[{'type':'memory','action':'recalled','notes':[self._view(db,r) for r in rows[:50]]}],
                                'tasks':[{'type':'memory','state':'WAITING_USER'}]}
                self._forget_in(db,user,rows[0]['id'],rows[0]['version'])
        except DomainError as error:
            if getattr(error,'code',None) not in ('MEMORY_NOT_FOUND','MEMORY_CONFLICT'):
                raise
            if error.code=='MEMORY_CONFLICT':
                return {'message':'记忆有更新，请刷新笔记后再选择移出。本次未移出内容。',
                        'cards':[{'type':'memory','action':'recalled','notes':[]}],
                        'tasks':[{'type':'memory','state':'WAITING_USER'}]}
            return missing
        return {'message':'已移出这条记忆，后续不再检索它。聊天记录仍可保留。','cards':[{'type':'memory','action':'removed','notes':[]}],
                'tasks':[{'type':'memory','state':'COMPLETED'}]}

    def _wealth_status(self,user,text):
        with self.bank.transaction() as db:
            self.bank._user(db,user);self.bank._expire(db,user)
            rows=db.execute("SELECT * FROM operations WHERE user_id=? AND type IN ('wealth_purchase','wealth_redemption') ORDER BY created_at DESC,rowid DESC",(user,)).fetchall()
            operations=[self.bank._view(row) for row in rows]
        if ('申购' in text and '赎回' in text) or ('产品甲' in text and '产品乙' in text):
            return {'message':'你想核对哪一笔模拟理财申请？','cards':[],'tasks':[{'type':'operation_status','state':'WAITING_USER'}]}
        kind='wealth_purchase' if '申购' in text else 'wealth_redemption' if '赎回' in text else None
        product='演示产品甲' if '产品甲' in text else '演示产品乙' if '产品乙' in text else None
        op=next((op for op in operations if (kind is None or op['type']==kind) and (product is None or op['payload'].get('product_name')==product)),None)
        if op is None:
            return {'message':'没有查到符合条件的本人模拟理财申请，不能认定已成交。','cards':[],'tasks':[{'type':'operation_status','state':'COMPLETED'}]}
        states={'SUCCEEDED':'已完成','WAITING_CONFIRMATION':'等待你的确认，尚未成交','WAITING_VERIFICATION':'等待模拟验证，尚未成交','CANCELLED':'已取消，未成交','EXPIRED':'已过期，未成交'}
        return {'message':'按本人银行记录，最近一笔相关模拟理财申请'+states.get(op['status'],'结果仍需核实')+'。','cards':[{'type':'operation','operation':op}],'tasks':[{'type':'operation_status','state':'COMPLETED'}]}

    def _virtual_status(self,user,text):
        names=[name+'账户' for name in ('日常','储蓄') if name in text]
        if len(names)>1:
            return {'message':'要查询哪个关联账户的虚拟卡申请？','cards':[],
                    'tasks':[{'type':'operation_status','state':'WAITING_USER'}]}
        with self.bank.transaction() as db:
            self.bank._expire(db,user)
            rows=db.execute("SELECT * FROM operations WHERE user_id=? AND type='virtual_card_create' ORDER BY created_at DESC,rowid DESC",(user,)).fetchall()
            operations=[self.bank._view(row) for row in rows]
        op=next((op for op in operations if not names or op['payload'].get('account_name')==names[0]),None)
        if op is None:
            return {'message':'没有查到符合条件的本人虚拟卡申请，不能据此认定已经生成卡。','cards':[],
                    'tasks':[{'type':'operation_status','state':'COMPLETED'}]}
        states={'SUCCEEDED':'已完成','WAITING_CONFIRMATION':'等待你的确认，尚未生成卡',
                'CANCELLED':'已取消，未生成卡','EXPIRED':'已过期，未生成卡'}
        return {'message':'按本人银行记录，最近一份模拟虚拟卡申请'+states.get(op['status'],'结果仍需核实')+'。',
                'cards':[{'type':'operation','operation':op}],'tasks':[{'type':'operation_status','state':'COMPLETED'}]}

    def _card_status(self,user,text):
        text=normalize_card_text(text)
        cards=self.bank.list_cards(user)
        tails=re.findall(r'(?<!\d)\d{4}(?!\d)',text)
        aliases=[name+'账户' for name in ('日常','储蓄') if name in text]
        targets=tails+aliases
        if len(targets)>1 or re.search(r'\d{5,}',text):
            return {'message':'要查询哪张卡的处理结果？请只说一张卡的名称或末四位。',
                    'cards':[],'tasks':[{'type':'operation_status','state':'WAITING_USER'}]}
        allowed={c['id'] for c in (resolve_card_query(cards,targets[0]) if targets else cards)}
        kinds=('card_restore',) if re.search(r'解除挂失|解挂',text) else ('card_report_lost',) if '挂失' in text else ('card_report_lost','card_restore')
        with self.bank.transaction() as db:
            self.bank._expire(db,user)
            rows=db.execute("SELECT * FROM operations WHERE user_id=? AND type IN ('card_report_lost','card_restore') ORDER BY created_at DESC,rowid DESC",(user,)).fetchall()
            operations=[self.bank._view(row) for row in rows if row['type'] in kinds]
        op=next((o for o in operations if o['payload'].get('card_id') in allowed),None)
        if op is None:
            return {'message':'没有查到符合条件的本人卡片操作记录，不能据此认定已办理。','cards':[],
                    'tasks':[{'type':'operation_status','state':'COMPLETED'}]}
        states={'SUCCEEDED':'已完成','CANCELLED':'已取消，未执行','EXPIRED':'已过期，未执行',
                'WAITING_CONFIRMATION':'等待你的确认，尚未执行','WAITING_VERIFICATION':'等待验证，尚未执行'}
        label='模拟解挂申请' if op['type']=='card_restore' else '模拟挂失申请'
        return {'message':'按本人银行操作记录，最近一笔相关'+label+states.get(op['status'],'结果仍需核实')+'。',
                'cards':[{'type':'operation','operation':op}],'tasks':[{'type':'operation_status','state':'COMPLETED'}]}
