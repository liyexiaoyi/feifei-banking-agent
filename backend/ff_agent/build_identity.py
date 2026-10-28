"""Deterministic deployment compatibility fingerprints, not authentication."""
import hashlib
import json
from pathlib import Path
import re

FRONT_CONFIG = ('package.json', 'package-lock.json', 'tsconfig.json', 'index.html',
                'vite.config.ts', 'build-identity.mjs')
IDS = ('backend_id', 'frontend_id', 'assets_id')


def _walk(folder, ignore_cache=False):
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Missing directory or symbolic link')
    for path in folder.iterdir():
        if path.is_symlink():
            raise ValueError('Symbolic links are not supported')
        if path.is_dir():
            if not ignore_cache or path.name != '__pycache__':
                yield from _walk(path, ignore_cache)
        elif path.is_file():
            yield path


def _digest(root, paths, text=True):
    digest = hashlib.sha256()
    entries = sorted(paths, key=lambda p: p.relative_to(root).as_posix().encode('utf-8'))
    if not entries:
        raise ValueError('Empty fingerprint input')
    for path in entries:
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent == root or root in parent.parents):
            raise ValueError('Symbolic path components are not supported')
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing file or symbolic link')
        content = path.read_bytes()
        if text:
            try:
                content = content.decode('utf-8').replace('\r\n', '\n').encode('utf-8')
            except UnicodeError as exc:
                raise ValueError('Source must be UTF8') from exc
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        digest.update(hashlib.sha256(content).digest() + b'\0')
    return digest.hexdigest()


def source_ids(root):
    root = Path(root)
    back = [p for p in _walk(root / 'backend/ff_agent', True)
            if p.suffix == '.py' or (p.suffix == '.txt' and 'prompts' in p.relative_to(root / 'backend/ff_agent').parts)]
    front = [p for p in _walk(root / 'frontend/src', True)
             if not re.search(r'\.(test|spec)\.', p.name) and p.name != 'component-test-loader.mjs']
    return {'backend_id': _digest(root, [*back, root / 'requirements.lock.txt']),
            'frontend_id': _digest(root, [*front, *(root / 'frontend' / n for n in FRONT_CONFIG)])}


def assets_id(dist):
    dist = Path(dist)
    return _digest(dist, [p for p in _walk(dist) if p.relative_to(dist).as_posix() != 'build-meta.json'], text=False)


def valid_meta(value):
    return (isinstance(value, dict) and type(value.get('schema')) is int and value['schema'] == 1
            and all(isinstance(value.get(k), str) and re.fullmatch('[a-f0-9]{64}', value[k]) for k in IDS))


def capture_deployment(root):
    """Factory-time disk snapshot. Does not attest imported bytecode or prevent edits."""
    result = {'schema': 1, 'backend_id': '', 'frontend_id': '', 'assets_id': '', 'build_state': 'invalid'}
    try:
        result.update(source_ids(root))
        dist = Path(root) / 'frontend/dist'
        if not (dist / 'build-meta.json').exists():
            return {**result, 'build_state': 'missing'}
        if (dist / 'build-meta.json').is_symlink():
            return result
        meta = json.loads((dist / 'build-meta.json').read_text(encoding='utf-8'))
        result['assets_id'] = assets_id(dist)
        if valid_meta(meta):
            result['build_state'] = 'ready' if all(meta[k] == result[k] for k in IDS) else 'stale'
    except (OSError, ValueError):
        pass
    return result
