import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ff_agent.build_identity import source_ids, assets_id, capture_deployment


def project(root):
    files = {
        'backend/ff_agent/a.py': 'value = "中文"\n',
        'backend/ff_agent/prompts/a.txt': '提示\n',
        'requirements.lock.txt': 'fastapi==0.1\n',
        'frontend/src/App.tsx': 'export default 1\n',
        **{'frontend/' + name: '{}\n' for name in (
            'package.json', 'package-lock.json', 'tsconfig.json', 'index.html',
            'vite.config.ts', 'build-identity.mjs')},
        'frontend/dist/index.html': '<html>你好</html>',
        'frontend/dist/assets/a.js': 'console.log(1)',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())
    return root


def write_meta(root):
    meta = {'schema': 1, **source_ids(root), 'assets_id': assets_id(root / 'frontend/dist')}
    (root / 'frontend/dist/build-meta.json').write_text(json.dumps(meta), encoding='utf-8')
    return meta


def test_source_ids_normalize_crlf_and_ignore_private_and_test_files(tmp_path):
    root = project(tmp_path)
    expected = source_ids(root)
    (root / 'backend/ff_agent/a.py').write_bytes('value = "中文"\r\n'.encode())
    for name in ('.env', 'data/db', '.git/HEAD', 'frontend/src/App.test.ts',
                 'frontend/src/component-test-loader.mjs', 'backend/tests/test_x.py',
                 'backend/ff_agent/__pycache__/a.pyc'):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('ignored', encoding='utf-8')
    assert source_ids(root) == expected
    (root / 'backend/ff_agent/prompts/a.txt').write_text('新提示', encoding='utf-8')
    assert source_ids(root)['backend_id'] != expected['backend_id']
    (root / 'frontend/src/App.tsx').write_text('new source', encoding='utf-8')
    assert source_ids(root)['frontend_id'] != expected['frontend_id']


def test_reject_invalid_utf8_and_missing_required_file(tmp_path):
    root = project(tmp_path)
    (root / 'backend/ff_agent/a.py').write_bytes(b'\xff')
    with pytest.raises(ValueError):
        source_ids(root)
    (root / 'backend/ff_agent/a.py').write_text('ok', encoding='utf-8')
    (root / 'requirements.lock.txt').unlink()
    with pytest.raises(ValueError):
        source_ids(root)


def test_reject_symlink(tmp_path):
    root = project(tmp_path)
    link = root / 'backend/ff_agent/link.py'
    try:
        link.symlink_to(root / 'backend/ff_agent/a.py')
    except OSError:
        pytest.skip('host does not permit symlink creation')
    with pytest.raises(ValueError):
        source_ids(root)


def test_symbolic_parent_and_utf8_sorting(tmp_path, monkeypatch):
    root = project(tmp_path)
    (root / 'backend/ff_agent/😀.py').write_bytes(b'\xef\xbb\xbf# UTF8 BOM\r\n')
    (root / 'frontend/dist/__pycache__').mkdir()
    cached_asset = root / 'frontend/dist/__pycache__/asset.bin'
    cached_asset.write_bytes(b'one')
    old = assets_id(root / 'frontend/dist')
    cached_asset.write_bytes(b'two')
    assert assets_id(root / 'frontend/dist') != old, 'all asset bytes participate, including unusual directory names'
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p == root / 'frontend' or original(p))
    with pytest.raises(ValueError):
        source_ids(root)


def test_metadata_missing_invalid_stale_ready_and_factory_snapshot(tmp_path, monkeypatch):
    root = project(tmp_path)
    assert capture_deployment(root)['build_state'] == 'missing'
    meta = write_meta(root)
    assert capture_deployment(root) == {**meta, 'build_state': 'ready'}
    from ff_agent import api
    monkeypatch.setattr(api, 'ROOT', root)
    app = api.create_app(db_path=root / 'test.db')
    with TestClient(app) as client:
        before = client.get('/api/health').json()['deployment']
        assert before == {**meta, 'build_state': 'ready'}
        # Tamper after factory creation; health must retain its startup identity.
        (root / 'frontend/dist/assets/a.js').write_text('different')
        assert client.get('/api/health').json()['deployment'] == before
        assert capture_deployment(root)['build_state'] == 'stale'
        before_db = app.state.bank.path.read_bytes()
        blocked = client.post('/api/session', json={'user': 'demo'}, headers={'X-Build-Identity': 'wrong'})
        assert blocked.status_code == 409
        assert blocked.json()['error']['code'] == 'DEPLOYMENT_MISMATCH'
        assert app.state.bank.path.read_bytes() == before_db
        accepted = client.post('/api/session', json={'user': 'demo'}, headers={
            'X-Build-Identity': meta['backend_id'] + ':' + meta['frontend_id']})
        assert accepted.status_code == 200
        assert client.post('/api/session', json={'user': 'demo'}).status_code == 200
    (root / 'frontend/dist/build-meta.json').write_text('{bad')
    assert capture_deployment(root)['build_state'] == 'invalid'


def test_node_python_hashes_identical_without_git(tmp_path):
    root = project(tmp_path)
    (root / 'backend/ff_agent/😀.py').write_bytes(b'\xef\xbb\xbf# BOM\r\n')
    (root / 'frontend/src/中文.ts').write_text('跨语言\n', encoding='utf-8')
    (root / 'frontend/src/custom-component-test-loader.mjs').write_text('included source', encoding='utf-8')
    (root / 'frontend/dist/二进制.bin').write_bytes(bytes(range(256)))
    node = shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
    module = Path(__file__).resolve().parents[2] / 'frontend/build-identity.mjs'
    result = subprocess.run([node, str(module), str(root)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {**source_ids(root), 'assets_id': assets_id(root / 'frontend/dist')}


def test_mismatch_rejected_before_every_write_route_and_body_validation(tmp_path, monkeypatch):
    root = project(tmp_path)
    write_meta(root)
    from ff_agent import api
    monkeypatch.setattr(api, 'ROOT', root)
    app = api.create_app(db_path=root / 'test.db')
    with TestClient(app) as client:
        before = app.state.bank.path.read_bytes()
        paths = [route.path for route in app.routes if 'POST' in getattr(route, 'methods', ())]
        assert len(paths) >= 10
        for path in paths:
            url = __import__('re').sub(r'\{[^}]+\}', 'test-id', path)
            response = client.post(url, json={}, headers={'X-Build-Identity': 'invalid'})
            assert response.status_code == 409, path
        assert app.state.bank.path.read_bytes() == before
