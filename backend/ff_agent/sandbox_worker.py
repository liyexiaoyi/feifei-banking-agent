"""Trusted one-shot Wasm worker: stdlib bootstrap, ready frame, one bounded input frame, one terminal frame."""
import json
import sys

MAX_FRAME = 198656
MAX_WAT = 32768
MAX_ARGS = 8
FUEL = 100000
LINEAR_MEMORY = 1048576
RUNTIME_VERSION = '50.0.1'
RANGES = {'i32': (-2**31, 2**31 - 1), 'i64': (-2**63, 2**63 - 1)}
DISABLED = (
    'wasm_component_model', 'wasm_component_model_implements', 'wasm_component_model_map',
    'wasm_threads', 'wasm_memory64', 'wasm_multi_memory', 'wasm_gc',
    'wasm_function_references', 'wasm_reference_types', 'wasm_tail_call', 'wasm_exceptions',
    'wasm_stack_switching', 'wasm_relaxed_simd', 'wasm_simd', 'wasm_multi_value',
)
MESSAGES = {
    'INVALID_INPUT': '输入无效',
    'INVALID_MODULE': '模块无效',
    'IMPORT_DENIED': '禁止导入',
    'INVALID_EXPORT': '导出函数无效',
    'FUEL_EXHAUSTED': '计算步数超限',
    'MEMORY_LIMIT': '内存超限',
    'GUEST_TRAP': '执行出错',
    'RUNTIME_UNAVAILABLE': '运行时不可用',
}


class Reject(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def emit(frame):
    data = json.dumps(frame, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    sys.stdout.buffer.write(data + b'\n')
    sys.stdout.buffer.flush()


def _pairs(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise Reject('INVALID_INPUT')
    return result


def _no_constant(_name):
    raise Reject('INVALID_INPUT')


def read_input():
    line = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not line.endswith(b'\n') or len(line) - 1 > MAX_FRAME:
        raise Reject('INVALID_INPUT')
    try:
        frame = json.loads(line[:-1].decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_no_constant)
    except (ValueError, RecursionError):
        raise Reject('INVALID_INPUT') from None
    if type(frame) is not dict or set(frame) != {'wat', 'args'}:
        raise Reject('INVALID_INPUT')
    wat, args = frame['wat'], frame['args']
    if type(wat) is not str or type(args) is not list or len(args) > MAX_ARGS:
        raise Reject('INVALID_INPUT')
    try:
        size = len(wat.encode('utf-8'))
    except UnicodeEncodeError:
        raise Reject('INVALID_INPUT') from None
    low, high = RANGES['i64']
    if size > MAX_WAT or any(type(a) is not int or not low <= a <= high for a in args):
        raise Reject('INVALID_INPUT')
    return wat, args


def classify(wasmtime, error):
    out_of_fuel = getattr(wasmtime.TrapCode, 'OUT_OF_FUEL', None)
    if isinstance(error, wasmtime.Trap) and out_of_fuel is not None and getattr(error, 'trap_code', None) == out_of_fuel:
        return 'FUEL_EXHAUSTED'
    text = str(error).lower()
    if 'fuel' in text:
        return 'FUEL_EXHAUSTED'
    if 'memory' in text and ('limit' in text or 'exceed' in text):
        return 'MEMORY_LIMIT'
    return 'GUEST_TRAP'


def execute(wat, args):
    try:
        import importlib.metadata
        if importlib.metadata.version('wasmtime') != RUNTIME_VERSION:
            raise Reject('RUNTIME_UNAVAILABLE')
        import wasmtime
        from wasmtime import _ffi
        config = wasmtime.Config()
        for name in ('consume_fuel', 'parallel_compilation') + DISABLED:
            if not isinstance(getattr(type(config), name, None), property):
                raise Reject('RUNTIME_UNAVAILABLE')
        config.consume_fuel = True
        config.parallel_compilation = False
        # 50.0.1 exposes these official C API setters through generated bindings,
        # but not Config properties. Leaving async enabled while references are
        # disabled aborts Engine construction instead of raising a Python error.
        for suffix in ('async_stackful', 'more_async_builtins', 'async', 'accessors', 'canonical_names'):
            setter = getattr(_ffi, 'wasmtime_config_wasm_component_model_' + suffix + '_set', None)
            if setter is None:
                raise Reject('RUNTIME_UNAVAILABLE')
            setter(config.ptr(), False)
        for name in DISABLED:
            setattr(config, name, False)
        engine = wasmtime.Engine(config)
    except Exception:
        raise Reject('RUNTIME_UNAVAILABLE') from None
    try:
        module = wasmtime.Module(engine, wat)
    except Exception:
        raise Reject('INVALID_MODULE') from None
    if len(module.imports) != 0:
        raise Reject('IMPORT_DENIED')
    exports = module.exports
    if len(exports) != 1 or exports[0].name != 'run' or not isinstance(exports[0].type, wasmtime.FuncType):
        raise Reject('INVALID_EXPORT')
    kinds = {'i32': wasmtime.ValType.i32(), 'i64': wasmtime.ValType.i64()}

    def kind(valtype):
        return next((name for name, value in kinds.items() if valtype == value), None)

    params = [kind(p) for p in exports[0].type.params]
    results = [kind(r) for r in exports[0].type.results]
    if len(params) > MAX_ARGS or None in params or len(results) != 1 or None in results:
        raise Reject('INVALID_EXPORT')
    if len(args) != len(params) or any(not RANGES[k][0] <= a <= RANGES[k][1] for k, a in zip(params, args)):
        raise Reject('INVALID_INPUT')
    try:
        store = wasmtime.Store(engine)
        store.set_fuel(FUEL)
        store.set_limits(memory_size=LINEAR_MEMORY, table_elements=0, instances=1, tables=0, memories=1)
    except Exception:
        raise Reject('RUNTIME_UNAVAILABLE') from None
    try:
        instance = wasmtime.Instance(store, module, [])
        result = instance.exports(store)['run'](store, *args)
        fuel_used = FUEL - store.get_fuel()
    except Exception as error:
        raise Reject(classify(wasmtime, error)) from None
    if type(result) is not int or type(fuel_used) is not int:
        raise Reject('GUEST_TRAP')
    return {'ok': True, 'result': result, 'fuel_used': fuel_used}


def main():
    emit({'ready': True, 'protocol': 1})
    try:
        frame = execute(*read_input())
    except Reject as reject:
        frame = {'ok': False, 'error': {'code': reject.code, 'message': MESSAGES[reject.code]}}
    except Exception:
        frame = {'ok': False, 'error': {'code': 'RUNTIME_UNAVAILABLE', 'message': MESSAGES['RUNTIME_UNAVAILABLE']}}
    emit(frame)


if __name__ == '__main__':
    main()
