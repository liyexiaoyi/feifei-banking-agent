"""Pure DTOs for i64 calculation code generation. No I/O, no side effects."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

I64_MIN = -(2 ** 63)
I64_MAX = 2 ** 63 - 1
MAX_ARGS = 8
MAX_DESCRIPTION_CHARS = 300
MAX_WAT_BYTES = 32768
_NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,19}')
_OUTCOMES = ('FAILED', 'SENT_UNKNOWN')


class GenerationError(Exception):
    """Generation failure. str() never echoes raw data, exceptions or config."""

    def __init__(self, outcome, code, meta=None):
        if outcome not in _OUTCOMES:
            raise ValueError('invalid outcome')
        if type(code) is not str or not code:
            raise ValueError('invalid code')
        if meta is not None and type(meta) is not dict:
            raise ValueError('invalid meta')
        self.outcome = outcome
        self.code = code
        self.meta = dict(meta) if meta else {}
        super().__init__(self.__str__())

    def __str__(self):
        if self.outcome == 'SENT_UNKNOWN':
            return '生成请求状态未知，未重发。'
        return '计算程序生成失败。'


def _utf8(value, what):
    if type(value) is not str:
        raise ValueError(what + ' must be str')
    try:
        return value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError(what + ' must be valid UTF-8') from None


@dataclass(frozen=True)
class CalcIntent:
    description: str
    args: tuple
    kind: str = 'calc'

    def __post_init__(self):
        if type(self.kind) is not str or self.kind != 'calc':
            raise ValueError('kind must be calc')
        _utf8(self.description, 'description')
        if not self.description.strip() or len(self.description) > MAX_DESCRIPTION_CHARS:
            raise ValueError('invalid description')
        if type(self.args) is not tuple or len(self.args) > MAX_ARGS:
            raise ValueError('args must be tuple of 0..8')
        for a in self.args:
            if type(a) is not int or not I64_MIN <= a <= I64_MAX:
                raise ValueError('args must be signed i64 ints')


def parse_intent(data) -> CalcIntent:
    if type(data) is not dict or set(data) != {'kind', 'description', 'args'}:
        raise ValueError('intent must have exactly kind/description/args')
    if type(data['args']) is not list:
        raise ValueError('args must be list')
    return CalcIntent(data['description'], tuple(data['args']), data['kind'])


def intent_sha256(intent: CalcIntent) -> str:
    if type(intent) is not CalcIntent:
        raise ValueError('intent must be CalcIntent')
    doc = {'kind': intent.kind, 'description': intent.description, 'args': list(intent.args)}
    text = json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class GeneratedWat:
    wat: str
    arg_names: tuple
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        raw = _utf8(self.wat, 'wat')
        if not raw or len(raw) > MAX_WAT_BYTES:
            raise ValueError('wat must be 1..32768 bytes')
        if type(self.arg_names) is not tuple or len(self.arg_names) > MAX_ARGS:
            raise ValueError('arg_names must be tuple of 0..8')
        for n in self.arg_names:
            if type(n) is not str or not _NAME_RE.fullmatch(n):
                raise ValueError('invalid arg name')
        if len(set(self.arg_names)) != len(self.arg_names):
            raise ValueError('arg names must be unique')
        if type(self.meta) is not dict:
            raise ValueError('meta must be dict')


def parse_generated(data, arg_count, meta=None) -> GeneratedWat:
    if type(data) is not dict or set(data) != {'wat', 'arg_names'}:
        raise ValueError('generated must have exactly wat/arg_names')
    if type(arg_count) is not int or not 0 <= arg_count <= MAX_ARGS:
        raise ValueError('arg_count must be int 0..8')
    names = data['arg_names']
    if type(names) is not list or len(names) != arg_count:
        raise ValueError('arg_names must be list matching arg_count')
    if meta is not None and type(meta) is not dict:
        raise ValueError('meta must be dict')
    return GeneratedWat(data['wat'], tuple(names), dict(meta) if meta else {})
