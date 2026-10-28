# Guarded calculation generation: replay -> reserve -> generate once -> run once.
# No Bank writes and no financial authority: the generated program only runs in the
# sandbox, and only the original reservation nonce holder can advance each stage.
from __future__ import annotations

from .bank import DomainError
from .codegen_provider import RuntimeWatGenerator
from .codegen_store import CodegenStore
from .codegen_types import CalcIntent, GeneratedWat, GenerationError, parse_generated, parse_intent
from .tool_guard import ToolPaused

__all__ = ['CodegenService', 'GROUP', 'TIMEOUT_S']
GROUP = 'codegen'
TIMEOUT_S = 20
_UNKNOWN_META = {'delivery_status': 'unknown', 'billing_status': 'unknown'}


def _unavailable():
    return DomainError('CODEGEN_UNAVAILABLE', 'AI计算暂不可用。', 503)


class CodegenService:
    def __init__(self, bank, sandbox, generator=None, guard=None, allow_fixture=False):
        provenance = None
        if generator is not None:
            provenance = getattr(generator, 'provenance', None)
            model = isinstance(generator, RuntimeWatGenerator) and provenance == 'model'
            fixture = allow_fixture is True and provenance == 'fixture'
            if not (model or fixture):
                raise ValueError('untrusted generator')
        self.bank = bank
        self.sandbox = sandbox
        self.store = CodegenStore(bank, sandbox)
        self.generator = generator
        self.guard = guard
        self._provenance = provenance

    @property
    def available(self):
        if self.generator is None or self.guard is None:
            return False
        try:
            if getattr(self.generator, 'provenance', None) != self._provenance:
                return False
            return bool(self.generator.available)
        except Exception:
            return False

    def generate_and_run(self, user, intent, key):
        if type(intent) is not CalcIntent:
            intent = parse_intent(intent)
        replay = self.store.replay(user, intent, key)
        if replay is not None:
            return replay
        if not self.available:
            raise _unavailable()
        view, nonce = self.store.reserve(user, intent, key, self._provenance)
        if nonce is None:
            return view
        gid = view['generation']['id']
        try:
            ticket = self.guard.start(user, GROUP)
        except Exception as error:
            code = 'TOOL_PAUSED' if isinstance(error, ToolPaused) else 'TOOL_UNAVAILABLE'
            return self._fail(user, gid, nonce, 'FAILED', code)
        outcome = 'tech'
        try:
            view, outcome = self._pipeline(user, intent, gid, nonce)
            return view
        finally:
            try:
                self.guard.finish(ticket, outcome)
            except Exception:
                pass  # a guard bookkeeping error never rewrites a recorded calculation

    def _pipeline(self, user, intent, gid, nonce):
        try:
            sent = self.store.mark_sent(user, gid, nonce)
        except Exception:
            return self._fail(user, gid, nonce, 'FAILED', 'INTERNAL'), 'tech'
        if not sent:
            return self._read(user, gid), 'tech'
        try:
            raw = self.generator.generate(intent, timeout_s=TIMEOUT_S)
        except GenerationError as error:
            return self._fail(user, gid, nonce, error.outcome, error.code, error.meta), 'tech'
        except Exception:
            meta = dict(_UNKNOWN_META)
            return self._fail(user, gid, nonce, 'SENT_UNKNOWN', 'DELIVERY_UNKNOWN', meta), 'tech'
        try:
            generated = self._revalidate(raw, len(intent.args))
            saved = self.store.save_generated(user, gid, nonce, generated)
        except ValueError:
            return self._fail(user, gid, nonce, 'FAILED', 'INVALID_OUTPUT'), 'tech'
        except Exception:
            return self._fail(user, gid, nonce, 'FAILED', 'INTERNAL'), 'tech'
        if not saved:
            return self._read(user, gid), 'tech'
        try:
            cap = self.store.claim_run(user, gid, nonce, intent)
        except Exception:
            return self._fail(user, gid, nonce, 'FAILED', 'RUN_NOT_RESERVED'), 'tech'
        if cap is None:
            return self._read(user, gid), 'tech'
        outcome = None
        try:
            # Exactly one runner call; afterwards only read-side settlement is allowed.
            self.sandbox.execute_generated(
                user, run_id=cap['run_id'], token=cap['token'], wat=cap['wat'],
                args=cap['args'], generation_id=cap['generation_id'])
        except DomainError as error:
            status = getattr(error, 'status', 500)
            outcome = 'business' if type(status) is int and 400 <= status < 500 else 'tech'
        except Exception:
            outcome = 'tech'
        finally:
            cap = None
        view = self._settle(user, gid, nonce)
        status = view['generation']['status']
        if status == 'SUCCEEDED':
            outcome = 'ok'
        elif outcome is None:
            outcome = 'business' if status == 'FAILED' else 'tech'
        return view, outcome

    @staticmethod
    def _revalidate(raw, arg_count):
        if type(raw) is not GeneratedWat:
            raise ValueError('generated must be GeneratedWat')
        data = {'wat': raw.wat, 'arg_names': list(raw.arg_names)}
        return parse_generated(data, arg_count, raw.meta)

    def _settle(self, user, gid, nonce):
        try:
            return self.store.settle(user, gid, nonce)
        except Exception:
            return self._read(user, gid)

    def _fail(self, user, gid, nonce, outcome, code, meta=None):
        try:
            return self.store.fail(user, gid, nonce, outcome, code, meta)
        except Exception:
            return self._read(user, gid)

    def _read(self, user, gid):
        try:
            return self.store.get(user, gid)
        except Exception:
            raise _unavailable() from None
