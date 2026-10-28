"""Bridge natural chat turns to the birthday parser and atomic commit.

Read-only except confirm_birthday (one Bank transaction). Never moves money,
never advances/confirms/verifies flows, never calls the planner.
"""
import hashlib

from .bank import DomainError
from .birthday_chat_commit import bootstrap_birthday_chat, confirm_birthday
from .birthday_chat_parse import AccountOption, parse_birthday_turn
from .birthday_chat_slot import derive_chat_key
from .birthday_store_schema import load_flow, view_flow

__all__ = ('BirthdayChat',)

OTHER_SLOTS = ('pending_transfer', 'selected_merchant', 'pending_card', 'recent_card',
               'pending_virtual_card', 'pending_wealth', 'pending_bill')
SLOT_FIELDS = ('stage', 'asked', 'recipient_label', 'event_date', 'budget_cents',
               'account_id', 'session_id', 'epoch', 'started_at', 'origin_order',
               'summary_order', 'summary_hash', 'canonical_intent')
COMMITTED_MESSAGE = '生日安排已保存。请打开安排核对当前步骤及付款回执。'


def _task():
    return [{'type': 'birthday', 'state': 'WAITING_USER', 'reply_source': 'birthday_rules'}]


def _mask(number):
    s = str(number or '')
    return ('****' + s[-4:]) if len(s) >= 4 else '****'


class BirthdayChat:
    def __init__(self, bank, store, run_tool=None):
        self.bank = bank
        self.store = store
        self.run_tool = run_tool
        bootstrap_birthday_chat(bank)

    def recover(self, user, request_key):
        """Return the chat-originated flow view, {'id': None} if the link is corrupt, None if no link."""
        if type(user) is not str or type(request_key) is not str:
            return None
        key = derive_chat_key(request_key)
        with self.bank.transaction() as db:
            row = db.execute(
                'SELECT l.flow_id, l.input_hash, c.input, r.action, r.flow_id'
                ' FROM birthday_chat_links l'
                ' LEFT JOIN chat_requests c ON c.user_id=l.user_id AND c.request_key=l.request_key'
                ' LEFT JOIN birthday_requests r ON r.user_id=l.user_id AND r.request_key=?'
                ' WHERE l.user_id=? AND l.request_key=?', (key, user, request_key)).fetchone()
            if row is None:
                return None
            flow_id, input_hash, text, action, req_flow = row
            if (type(text) is not str
                    or hashlib.sha256(text.encode('utf-8')).hexdigest() != input_hash
                    or action != 'create' or req_flow != flow_id):
                return {'id': None}
            try:
                return view_flow(self.bank, db, user, load_flow(db, user, flow_id))
            except DomainError:
                return {'id': None}

    @staticmethod
    def _reference(flow_id):
        card = {'type': 'birthday_flow', 'flow_id': flow_id,
                'simulated': True, 'merchant_connected': False}
        return {'message': COMMITTED_MESSAGE, 'cards': [card], 'tasks': _task(),
                '_birthday_committed': True}

    def _owned(self, user):
        with self.bank.transaction() as db:
            rows = db.execute('SELECT id, name, masked_number FROM accounts WHERE user_id=? ORDER BY id',
                              (user,)).fetchall()
        return [AccountOption(r[0], r[1], _mask(r[2])) for r in rows]

    def handle(self, user, text, request_key, epoch, session_id, request_order, context):
        rec = self.recover(user, request_key)
        if rec is not None:
            if not isinstance(rec.get('id'), str) or not rec.get('id'):
                # Corrupt/unverifiable link: do not claim saved, do not create.
                return {'message': '无法核实本次请求关联的生日安排，不能确认已保存，也未新建安排。请在生日安排列表中核对。',
                        'cards': [], 'tasks': _task()}
            context.pop('pending_birthday', None)
            return self._reference(rec['id'])
        pending = context.get('pending_birthday') or None
        turn = parse_birthday_turn(text, pending, self._owned(user), self.bank.now())
        if turn.kind == 'NONE':
            return None
        if turn.kind == 'ABANDON':
            # Hand back to the normal planner; never stop flows or refund here.
            context.pop('pending_birthday', None)
            context.pop('recent_turns', None)
            return None
        if turn.kind == 'CONFIRM':
            expected = dict(pending)

            def commit():
                return confirm_birthday(self.bank, self.store, user, text, request_key, epoch,
                                        session_id, request_order, expected)

            flow = self.run_tool(user, 'transfer', commit) if self.run_tool else commit()
            context.pop('pending_birthday', None)
            return self._reference(flow['id'])
        # CLARIFY / SUMMARY
        p = dict(turn.pending)
        if not pending:
            for slot in OTHER_SLOTS:
                context.pop(slot, None)
            p['started_at'] = p.get('started_at')
        else:
            p['started_at'] = pending.get('started_at')
        p['session_id'], p['epoch'] = session_id, epoch
        if p.get('origin_order') is None:
            p['origin_order'] = request_order
        p['summary_order'] = request_order if turn.kind == 'SUMMARY' else None
        context['pending_birthday'] = {f: p.get(f) for f in SLOT_FIELDS}
        return {'message': turn.message, 'cards': [], 'tasks': _task()}
