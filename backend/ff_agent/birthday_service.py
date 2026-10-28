"""BirthdayService: thin facade over the birthday store, commands, and handlers.

Local simulation only. No merchants or bank connection. One Bank.transaction per command.
"""
from .bank import DomainError
from .birthday_store import BirthdayStore
from .birthday_orders import BirthdayOrders
from .birthday_commands import (
    command_start,
    view_flow,
    decide_locked,
    select_locked,
    lookup_request as _lookup_request,
)
from .birthday_advancement import advance_locked
from .birthday_lifecycle import pause_locked, resume_locked, cancel_locked

_ACTIVE = ('ACTIVE',)
_PAUSABLE = ('ACTIVE', 'PAUSED')
_RESUMABLE = ('PAUSED',)
_CANCELLABLE = ('ACTIVE', 'PAUSED', 'BLOCKED', 'UNKNOWN', 'CANCELLED')
_GIFTS = ('flowers', 'cake')


def _require_confirmed(confirmed):
    if confirmed is not True:
        raise DomainError('CONFIRMATION_REQUIRED', '需要明确确认', 400)


class BirthdayService:
    def __init__(self, bank):
        self.bank = bank
        self.store = BirthdayStore(bank)

    def create(self, user, recipient_label, event_date, account_id, budget_cents, key):
        return self.store.create(user, recipient_label, event_date, account_id, budget_cents, key)

    def get(self, user, id):
        return self.store.get(user, id)

    def list(self, user):
        return self.store.list(user)

    def catalog(self, gift=None):
        return BirthdayOrders(self.bank).catalog(gift)

    def lookup_request(self, user, key):
        with self.bank.transaction() as db:
            return _lookup_request(self.bank, db, user, key)

    def _command(self, user, id, action, args, expected_version, key, allowed_statuses, callback):
        with self.bank.transaction() as db:
            ctx = command_start(self.bank, db, user, id, action, args,
                                expected_version, key, allowed_statuses)
            if ctx['replay']:
                return view_flow(self.bank, db, user, ctx['flow'])
            return callback(db, ctx)

    def decide_cake(self, user, id, cake, expected_version, key):
        if type(cake) is not bool:
            raise DomainError('INVALID_CAKE', 'cake 必须为布尔值', 400)
        return self._command(user, id, 'decide_cake', {'cake': cake}, expected_version, key, _ACTIVE,
                             lambda db, ctx: decide_locked(self.bank, db, user, ctx, cake))

    def select_item(self, user, id, gift, item_id, expected_version, key):
        if type(gift) is not str or gift not in _GIFTS:
            raise DomainError('INVALID_GIFT', 'gift 必须为 flowers 或 cake', 400)
        if type(item_id) is not str or not item_id:
            raise DomainError('INVALID_ITEM', 'item_id 必须为字符串', 400)
        return self._command(user, id, 'select_item', {'gift': gift, 'item_id': item_id},
                             expected_version, key, _ACTIVE,
                             lambda db, ctx: select_locked(self.bank, db, user, ctx, gift, item_id))

    def advance(self, user, id, expected_version, key):
        return self._command(user, id, 'advance', {}, expected_version, key, _ACTIVE,
                             lambda db, ctx: advance_locked(self.bank, db, user, ctx))

    def pause(self, user, id, expected_version, key):
        return self._command(user, id, 'pause', {}, expected_version, key, _PAUSABLE,
                             lambda db, ctx: pause_locked(self.bank, db, user, ctx))

    def resume(self, user, id, expected_version, key, confirmed):
        _require_confirmed(confirmed)
        return self._command(user, id, 'resume', {'confirmed': True}, expected_version, key, _RESUMABLE,
                             lambda db, ctx: resume_locked(self.bank, db, user, ctx, confirmed))

    def cancel(self, user, id, expected_version, key, confirmed):
        _require_confirmed(confirmed)
        return self._command(user, id, 'cancel', {'confirmed': True}, expected_version, key, _CANCELLABLE,
                             lambda db, ctx: cancel_locked(self.bank, db, user, ctx, confirmed))
