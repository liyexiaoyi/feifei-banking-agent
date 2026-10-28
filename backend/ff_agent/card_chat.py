"""Card dialogue orchestration; only the Bank may execute a confirmed action."""
import hashlib
import json

from .bank import DomainError, PENDING
from .card_chat_auth import check_card_ticket
from .card_chat_controls import CardControls
from .card_intent import parse_card_intent
from .card_query import normalize_card_text, resolve_card_query
from .card_working import validate_card_slots
from .planner import validate_plan
from .plan_ownership import owner_for


class CardChat:
    def __init__(self, bank):
        self.bank = bank
        self.controls = CardControls(bank)
        with bank.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS card_chat_preparations (
                user_id TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL,
                card_id TEXT NOT NULL, expected_version INTEGER NOT NULL,
                PRIMARY KEY(user_id, key))''')

    def _preparation(self, user, key, request_hash, action, ticket, card=None):
        """Pin a dialogue request before preparing; never derive a retry from new context."""
        if not isinstance(key, str) or not 1 <= len(key) <= 120:
            raise DomainError('INVALID_KEY', '请求编号无效。')
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            check_card_ticket(self.bank, db, user, ticket, action, key)
            row = db.execute('SELECT * FROM card_chat_preparations WHERE user_id=? AND key=?',
                             (user, key)).fetchone()
            if row is not None:
                if row['request_hash'] != request_hash:
                    raise DomainError('IDEMPOTENCY_CONFLICT', '这项请求与原记录不一致，请先核对。', 409)
                return dict(row)
            if card is None:
                return None
            db.execute('INSERT INTO card_chat_preparations VALUES(?,?,?,?,?)',
                       (user, key, request_hash, card['id'], card['version']))
            return {'card_id': card['id'], 'expected_version': card['version']}

    @staticmethod
    def _answer(lines, cards=(), state='COMPLETED', working=None):
        return {'cards': list(cards), 'lines': list(lines), 'state': state,
                'working': {} if working is None else working}

    @staticmethod
    def _request_hash(action, text):
        value = json.dumps({'action': action, 'text': normalize_card_text(text)},
                           ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    def _card_list(self, user):
        return [{'type': 'bank_cards', 'cards': self.bank.list_cards(user), 'simulated': True}]

    def _ops(self, user):
        allowed = {c['id'] for c in self.bank.list_cards(user)}
        with self.bank.transaction() as db:
            self.bank._expire(db, user)
            rows = db.execute("SELECT * FROM operations WHERE user_id=? AND type IN "
                              "('card_report_lost','card_restore') ORDER BY created_at DESC,rowid DESC",
                              (user,)).fetchall()
            operations = [self.bank._view(row) for row in rows]
        return [op for op in operations if op['payload'].get('card_id') in allowed]

    def _existing(self, user, key):
        with self.bank.transaction() as db:
            self.bank._expire(db, user)
            row = db.execute('SELECT * FROM operations WHERE user_id=? AND idempotency_key=?',
                             (user, key)).fetchone()
            return self.bank._view(row) if row is not None else None

    def _recent(self, user, context):
        with self.bank.transaction() as db:
            slots = validate_card_slots(db, user, context, self.bank.now().timestamp())
        pointer = slots.get('recent_card')
        return self.bank.operation(user, pointer['operation_id']) if pointer else None

    def _resolve(self, user, query, context):
        recent = self._recent(user, context) if query == '刚才那张' else None
        return resolve_card_query(self.bank.list_cards(user), query,
                                  recent['payload']['card_id'] if recent else None)

    def _ask(self, user, mode, action):
        message = '要撤回哪张卡的申请？请说卡片名称或末四位。' if mode == 'stop' else '要办理哪张卡？请说卡片名称或末四位。'
        return self._answer([message], self._card_list(user), 'WAITING_USER',
                            {'pending_card': {'mode': mode, 'action': action,
                                              'started_at': self.bank.now().timestamp()}})

    def _prepared(self, op, old=None, lines=None):
        state = 'WAITING_USER' if op['status'] in PENDING else 'COMPLETED' if op['status'] == 'SUCCEEDED' else 'BLOCKED'
        cards = ([{'type': 'operation', 'operation': old}] if old else [])
        cards.append({'type': 'operation', 'operation': op})
        message = '申请已准备，尚未办理。请在卡片中核对、确认并完成模拟验证。' if op['status'] in PENDING else '这是已记录的申请，请按下方操作状态核对。'
        return self._answer(lines or [message], cards, state,
                            {'recent_card': {'operation_id': op['id'], 'started_at': op['created_at']}})

    def handle(self, user, action, text, key, context, plan_ticket=None):
        validate_plan({'actions': [action]})
        kind = action['type']
        if kind not in ('cards', 'card_status', 'card_stop', 'card_correct'):
            raise DomainError('INVALID_ACTION', '这不是可用的卡片请求。')
        if kind in ('card_stop', 'card_correct') and parse_card_intent(text, context) != [action]:
            return self._answer(['请明确说要撤回哪项申请，或要改成哪张卡。'], state='BLOCKED')
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            check_card_ticket(self.bank, db, user, plan_ticket, action, key)
        if kind in ('cards', 'card_status'):
            return self._cards_status(user, action, text, key, context, plan_ticket)
        if kind == 'card_stop':
            return self._stop(user, action, text, key, context, plan_ticket)
        return self._correct(user, action, text, key, context, plan_ticket)

    def _stop(self, user, action, text, key, context, plan_ticket=None):
        request_hash = self._request_hash(action, text)
        try:
            journal = self.controls.load(user, key, request_hash)
            if journal is None:
                operations = [op for op in self._ops(user)
                              if action['action'] in ('any', op['payload'].get('action'))
                              and op['status'] not in ('SUCCEEDED', 'CANCELLED', 'EXPIRED', 'FAILED')]
                query = action['card']
                if query not in ('全部', '未指定'):
                    matches = self._resolve(user, query, context)
                    if len(matches) != 1:
                        return self._ask(user, 'stop', action['action'])
                    operations = [op for op in operations if op['payload']['card_id'] == matches[0]['id']]
                if any(op['status'] not in PENDING for op in operations):
                    return self._answer(['有申请结果尚未核实，请先查看原记录；本次未撤回。'],
                                        [{'type': 'operation', 'operation': op} for op in operations], 'BLOCKED')
                if not operations:
                    return self._answer(['没有查到符合条件、可撤回的未执行卡片申请。'])
                if len(operations) > 1 and query != '全部':
                    if len({op['payload']['card_id'] for op in operations}) == 1:
                        return self._answer(['这张卡有多份待处理申请，请在操作卡中选择要撤回的申请。'],
                                            [{'type': 'operation', 'operation': op} for op in operations], 'WAITING_USER')
                    return self._ask(user, 'stop', action['action'])
                journal = self.controls.cancel(user, key, request_hash, action,
                                               [op['id'] for op in operations], plan_ticket=plan_ticket)
            if journal['action'] != action:
                raise DomainError('IDEMPOTENCY_CONFLICT', '这项请求与原记录不一致，请重新核对。', 409)
            operations = [self.bank.operation(user, opid) for opid in journal['source_ids']]
            cancelled = all(op['status'] == 'CANCELLED' for op in operations)
            return self._answer(['这些未办理的卡片申请已撤回。' if cancelled else '原申请状态仍需核实，请查看记录。'],
                                [{'type': 'operation', 'operation': op} for op in operations],
                                'COMPLETED' if cancelled else 'BLOCKED')
        except DomainError as error:
            return self._answer([error.message], state='BLOCKED')

    def _cards_status(self, user, action, text, key, context, plan_ticket=None):
        try:
            with self.bank.transaction() as db:
                self.bank._user(db, user)
                check_card_ticket(self.bank, db, user, plan_ticket, action, key)

            mode = action.get('type')
            if mode == 'cards':
                return self._answer(
                    ['当前卡片如下。'], cards=self._card_list(user), working={}
                )
            intent = action.get('action')
            if mode != 'card_status' or intent not in ('report_lost', 'restore'):
                return self._answer(
                    ['卡片状态操作无效。'], state='BLOCKED', working={}
                )

            existing = self._existing(user, key)
            request_hash = self._request_hash(action, text)
            binding = self._preparation(user, key, request_hash, action, plan_ticket)
            if binding is not None and existing is not None:
                payload = existing.get('payload')
                if (existing.get('type') != 'card_' + intent or not isinstance(payload, dict)
                        or payload.get('card_id') != binding['card_id']
                        or payload.get('action') != intent
                        or payload.get('expected_version') != binding['expected_version']):
                    raise DomainError('IDEMPOTENCY_CONFLICT', '原卡片请求绑定不一致，请先核对。', 409)
                with self.bank.transaction() as db:
                    check_card_ticket(self.bank, db, user, plan_ticket, action, key)
                    owner = None if plan_ticket is None else (plan_ticket['id'], plan_ticket['idx'])
                    if owner_for(db, user, existing['id']) != owner:
                        raise DomainError('PLAN_OWNERSHIP_CONFLICT', '原操作归属不匹配，请先核对。', 409)
                return self._prepared(existing)
            cards = ([{'id': binding['card_id'], 'version': binding['expected_version']}]
                     if binding is not None else self._resolve(user, action.get('card', '未指定'), context))
            if len(cards) != 1:
                return self._ask(user, 'prepare', intent)
            card = cards[0]
            expected_version = card['version']

            if existing is not None:
                payload = existing.get('payload')
                if (
                    existing.get('type') != 'card_' + intent
                    or not isinstance(payload, dict)
                    or payload.get('card_id') != card['id']
                    or payload.get('action', intent) != intent
                ):
                    return self._answer(
                        ['这项请求已关联其他操作，请先核对原记录。'],
                        state='BLOCKED', working={}
                    )
                replayable = tuple(PENDING) + ('SUCCEEDED', 'CANCELLED', 'EXPIRED', 'FAILED')
                if existing.get('status') not in replayable:
                    return self._prepared(
                        existing, lines=['当前申请状态无法重新准备，请查看申请回执。']
                    )
                expected_version = payload.get('expected_version')
                if type(expected_version) is not int:
                    return self._answer(
                        ['原申请缺少有效的卡片版本，无法验证重试请求。'],
                        cards=[{'type': 'operation', 'operation': existing}],
                        state='BLOCKED', working={}
                    )

            with self.bank.transaction() as db:
                self.bank._user(db, user)
                check_card_ticket(self.bank, db, user, plan_ticket, action, key)
            binding = self._preparation(user, key, request_hash, action, plan_ticket,
                                        {'id': card['id'], 'version': expected_version})
            operation = self.bank.prepare_card_status(
                user, binding['card_id'], intent, key, binding['expected_version'],
                plan_ticket=plan_ticket
            )
            return self._prepared(operation)
        except DomainError as error:
            return self._answer(
                [error.message],
                state='BLOCKED', working={}
            )

    def _correct(self, user, action, text, key, context, plan_ticket=None):
        if parse_card_intent(text, context) != [action]:
            return self._answer(['请明确说明要将哪笔申请改到哪张卡。'], state='BLOCKED')
        journal, old, op = None, None, None
        request_hash = self._request_hash(action, text)
        card_types = {'report_lost': 'card_report_lost', 'restore': 'card_restore'}
        try:
            with self.bank.transaction() as db:
                self.bank._user(db, user)
                check_card_ticket(self.bank, db, user, plan_ticket, action, key)
            journal = self.controls.load(user, key, request_hash)
            if journal is None:
                existing = self._existing(user, key)
                recent = self._recent(user, context)
                if existing is not None or recent is None or recent['status'] not in PENDING:
                    # A same-key caller may have committed after the first read.
                    # Its atomic cancellation and journal must be recovered together.
                    journal = self.controls.load(user, key, request_hash)
                    if journal is None:
                        return self._answer(['这项请求需核对原记录。' if existing else '没有可更正的有效待确认申请。'], state='BLOCKED')
                if journal is None:
                    source_action = recent['payload'].get('action')
                    if source_action not in card_types or recent['type'] != card_types[source_action]:
                        return self._answer(['原申请不支持更正。'], state='BLOCKED')
                    targets = self._resolve(user, action.get('card', '未指定'), context)
                    if len(targets) != 1:
                        return self._answer(['没有明确目标，原申请还没有撤回。要改成哪张卡？请重新说明更正需求和具体末四位。'],
                                            cards=self._card_list(user), state='WAITING_USER',
                                            working={'recent_card': {'operation_id': recent['id'], 'started_at': recent['created_at']}})
                    target = targets[0]
                    if target['id'] == recent['payload'].get('card_id'):
                        return self._answer(['请选择与原申请不同的卡片。'],
                                            cards=self._card_list(user), state='BLOCKED')
                    journal = self.controls.cancel(
                        user, key, request_hash, action, [recent['id']],
                        target={'id': target['id'], 'version': target['version']},
                        plan_ticket=plan_ticket)
            existing = self._existing(user, key)
            source_action = journal.get('source_action')
            if (journal.get('action') != action or source_action not in card_types
                    or len(journal.get('source_ids', [])) != 1
                    or journal.get('target_card_id') is None
                    or journal.get('target_version') is None):
                return self._answer(['更正记录不一致，无法继续。'], state='BLOCKED')
            source = self.bank.operation(user, journal['source_ids'][0])
            if source['status'] != 'CANCELLED':
                return self._answer(['原申请尚未确认撤回，无法准备新申请。'],
                                    cards=[{'type': 'operation', 'operation': source}], state='BLOCKED')
            old = source
            if existing is not None:
                payload = existing['payload']
                if (existing['type'] != card_types[source_action]
                        or payload.get('card_id') != journal['target_card_id']
                        or payload.get('action') != source_action
                        or payload.get('expected_version') != journal['target_version']
                        or journal.get('new_op_id') not in (None, existing['id'])):
                    return self._answer(['原申请已撤回，但新申请与更正记录不一致，请核对原记录。'],
                                        cards=[{'type': 'operation', 'operation': old}], state='BLOCKED')
                op = existing
                if op['status'] not in ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'):
                    self.controls.record_prepare(user, key, operation_id=op['id'])
                return self._prepared(op, old=old)
            if journal.get('new_op_id') is not None or journal.get('phase') not in ('cancelled', 'prepare_failed'):
                return self._answer(['原申请已撤回，新申请记录需要核实。'],
                                    cards=[{'type': 'operation', 'operation': old}], state='BLOCKED')
            with self.bank.transaction() as db:
                check_card_ticket(self.bank, db, user, plan_ticket, action, key)
            try:
                op = self.bank.prepare_card_status(
                    user, journal['target_card_id'], source_action, key,
                    expected_version=journal['target_version'], plan_ticket=plan_ticket)
            except DomainError as exc:
                lines = ['原申请已撤回，新申请未准备：' + exc.message]
                try:
                    self.controls.record_prepare(user, key, error=exc.code)
                except DomainError as record_error:
                    lines.append('未能记录准备失败状态：' + record_error.message)
                return self._answer(lines, cards=[{'type': 'operation', 'operation': old}], state='BLOCKED')
            self.controls.record_prepare(user, key, operation_id=op['id'])
            return self._prepared(op, old=old)
        except DomainError as exc:
            cards = [{'type': 'operation', 'operation': item} for item in (old, op) if item is not None]
            lines = (['原申请已撤回。'] if old is not None else []) + [exc.message]
            return self._answer(lines, cards=cards, state='BLOCKED')
