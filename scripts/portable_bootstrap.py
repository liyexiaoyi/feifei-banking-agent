"""Portable Windows entry point: preflight checks, then the existing launcher."""
import argparse
import importlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for _folder in (ROOT / 'scripts', ROOT / 'backend'):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))

REQUIRED_MODULES = ('fastapi', 'httpx', 'uvicorn', 'wasmtime', 'sqlite3')
DEFAULT_PORT_LINE = 'FF_PORT=8765'
PORTABLE_PORT_LINE = 'FF_PORT=18765'
REPACK_HINT = '请重新解压完整的便携压缩包。'


class SetupError(Exception):
    """Setup problem reported to the user without a traceback."""


def check_runtime():
    if sys.version_info[:2] != (3, 12):
        found = '.'.join(str(part) for part in sys.version_info[:3])
        raise SetupError(f'运行环境版本不受支持：需要 Python 3.12，当前为 {found}。')
    missing = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except (ImportError, OSError):
            missing.append(name)
    if missing:
        raise SetupError('运行环境缺少组件：' + ', '.join(missing) + '。' + REPACK_HINT)


def check_build():
    if not (ROOT / 'frontend' / 'dist' / 'index.html').is_file():
        raise SetupError('缺少前端文件 frontend/dist/index.html。' + REPACK_HINT)
    try:
        from ff_agent.build_identity import capture_deployment
    except ImportError:
        raise SetupError('缺少后端程序文件 backend/ff_agent。' + REPACK_HINT) from None
    if capture_deployment(ROOT)['build_state'] != 'ready':
        raise SetupError('程序文件缺失、损坏或版本不一致。' + REPACK_HINT)


def ensure_env():
    """Create .env from .env.example once; never touch an existing .env."""
    target = ROOT / '.env'
    if os.path.lexists(target):
        return False
    try:
        template = (ROOT / '.env.example').read_bytes().decode('utf-8')
    except FileNotFoundError:
        raise SetupError('缺少配置模板 .env.example。' + REPACK_HINT) from None
    except (OSError, UnicodeError):
        raise SetupError('无法读取配置模板 .env.example。' + REPACK_HINT) from None
    lines = template.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.rstrip('\r\n') == DEFAULT_PORT_LINE]
    if len(matches) != 1:
        raise SetupError('配置模板 .env.example 格式异常，未生成 .env。' + REPACK_HINT)
    old = lines[matches[0]]
    lines[matches[0]] = PORTABLE_PORT_LINE + old[len(DEFAULT_PORT_LINE):]
    data = ''.join(lines).encode('utf-8')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        return False
    except OSError:
        raise SetupError('无法创建配置文件 .env，请检查文件夹写入权限。') from None
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        try:
            target.unlink()
        except OSError:
            pass
        raise SetupError('写入配置文件 .env 失败，请检查磁盘空间和写入权限。') from None
    return True


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='portable_bootstrap', description='FF-Agent 便携版启动')
    parser.add_argument('--check', action='store_true', help='只检查运行环境，不启动服务')
    parser.add_argument('--no-browser', action='store_true', help='启动后不打开浏览器')
    return parser.parse_args(argv)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')
    args = parse_args(argv)
    try:
        check_runtime()
        check_build()
        created = ensure_env()
    except SetupError as error:
        print(f'启动失败：{error}', file=sys.stderr)
        return 1
    if created:
        print('已根据 .env.example 生成本地配置文件 .env。')
    if args.check:
        print(f'FF-Agent 便携版检查通过。Python {sys.version}')
        return 0
    import os
    for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'PYTHONUSERBASE'):
        os.environ.pop(name, None)
    os.environ['PYTHONNOUSERSITE'] = '1'
    try:
        import launch
    except ImportError:
        print('启动失败：无法加载启动程序 scripts/launch.py。' + REPACK_HINT, file=sys.stderr)
        return 1
    sys.argv = [str(ROOT / 'scripts' / 'launch.py')] + (['--no-browser'] if args.no_browser else [])
    try:
        launch.main()
    except SystemExit as exit_:
        code = exit_.code
        if code not in (None, 0):
            print(f'启动失败：启动程序返回码 {code}', file=sys.stderr)
            return code if isinstance(code, int) else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
