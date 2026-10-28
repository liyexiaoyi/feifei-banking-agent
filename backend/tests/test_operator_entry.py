from fastapi.testclient import TestClient

from ff_agent import api as module
from test_build_identity import project, write_meta


def test_operator_entry_serves_pc_shell_without_opening_bank_or_operator_session(tmp_path, monkeypatch):
    root = project(tmp_path / 'pc-shell')
    write_meta(root)
    monkeypatch.setattr(module, 'ROOT', root)
    app = module.create_app(tmp_path / 'bank.db')
    with TestClient(app) as client:
        response = client.get('/operator')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/html')
        assert response.text == '<html>你好</html>'
        assert not response.headers.get('set-cookie')
        assert client.get('/api/state').status_code == 401
        assert client.get('/api/operator/cases').status_code == 401


def test_missing_operator_shell_is_reported_without_creating_a_session(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ROOT', tmp_path / 'not-built')
    with TestClient(module.create_app(tmp_path / 'bank.db')) as client:
        response = client.get('/operator')
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'FRONTEND_UNAVAILABLE'
        assert not response.headers.get('set-cookie')
