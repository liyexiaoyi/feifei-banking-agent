import sqlite3

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from ff_agent.integrations import (
    InvalidIntegrationMode,
    RealModeRejected,
    get_snapshot,
)


EXPECTED_SNAPSHOT = {
    'schema_version': '1',
    'mode': 'simulated',
    'capabilities': [
        {
            'id': 'bank',
            'status': 'simulated',
            'real_mode': 'rejected',
            'note_code': 'mock_ledger',
        },
        {
            'id': 'identity',
            'status': 'simulated',
            'real_mode': 'rejected',
            'note_code': 'mock_verification',
        },
        {
            'id': 'human',
            'status': 'not_connected',
            'real_mode': 'rejected',
            'note_code': 'awaiting_official_docs',
        },
    ],
}


@pytest.fixture
def logged_client(tmp_path):
    db_path = tmp_path / 'api.db'
    app = create_app(db_path)
    with TestClient(app) as client:
        login = client.post('/api/session', json={'user': 'demo'})
        assert login.status_code == 200, login.text
        yield client, db_path


def _database_dump(path):
    connection = sqlite3.connect(str(path))
    try:
        connection.execute('BEGIN')
        schema = connection.execute(
            'SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name'
        ).fetchall()
        table_names = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        tables = []
        for (name,) in table_names:
            quoted_name = chr(34) + name.replace(chr(34), chr(34) * 2) + chr(34)
            rows = connection.execute(f'SELECT * FROM {quoted_name}').fetchall()
            tables.append((name, tuple(sorted(rows, key=repr))))
        return tuple(schema), tuple(tables)
    finally:
        connection.rollback()
        connection.close()


def test_get_snapshot_has_the_contract_and_defaults_to_simulated():
    assert get_snapshot() == EXPECTED_SNAPSHOT
    assert get_snapshot(mode='simulated') == EXPECTED_SNAPSHOT


def test_each_snapshot_is_deeply_independent():
    first = get_snapshot()
    first['capabilities'][0]['note_code'] = 'changed'
    first['capabilities'].append({'id': 'unexpected'})

    second = get_snapshot()
    assert second == EXPECTED_SNAPSHOT
    assert second is not first
    assert second['capabilities'] is not first['capabilities']
    assert second['capabilities'][0] is not first['capabilities'][0]


def test_real_mode_is_rejected():
    with pytest.raises(RealModeRejected):
        get_snapshot(mode='real')


@pytest.mark.parametrize('mode', [None, 7, True, [], {}, 'SIMULATED', 'unknown'])
def test_non_string_and_invalid_modes_are_rejected(mode):
    with pytest.raises(InvalidIntegrationMode):
        get_snapshot(mode=mode)


def test_authenticated_endpoint_returns_snapshot_for_default_and_explicit_mode(logged_client):
    client, _ = logged_client
    for path in ('/api/integrations', '/api/integrations?mode=simulated'):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == EXPECTED_SNAPSHOT


def test_real_mode_endpoint_returns_fixed_conflict_error(logged_client):
    client, _ = logged_client
    response = client.get('/api/integrations?mode=real')
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'REAL_MODE_REJECTED'


@pytest.mark.parametrize(
    ('path', 'code', 'markers'),
    [
        (
            '/api/integrations?privateQueryMarker=privateValueMarker',
            'INVALID_INTEGRATION_QUERY',
            ('privateQueryMarker', 'privateValueMarker'),
        ),
        (
            '/api/integrations?mode=firstModeMarker&mode=secondModeMarker',
            'INVALID_INTEGRATION_QUERY',
            ('firstModeMarker', 'secondModeMarker'),
        ),
        (
            '/api/integrations?mode=unknownModeMarker',
            'INVALID_INTEGRATION_QUERY',
            ('unknownModeMarker',),
        ),
    ],
)
def test_invalid_queries_return_fixed_errors_without_echoing_input(logged_client, path, code, markers):
    client, _ = logged_client
    response = client.get(path)
    assert response.status_code == 422
    assert response.json()['error']['code'] == code
    for marker in markers:
        assert marker not in response.text


def test_authentication_precedes_query_validation(tmp_path):
    with TestClient(create_app(tmp_path / 'unauthenticated.db')) as client:
        response = client.get(
            '/api/integrations?mode=real&mode=simulated&privateQueryMarker=valueMarker'
        )
    assert response.status_code == 401


def test_integration_get_requests_do_not_change_database(logged_client):
    client, db_path = logged_client
    before = _database_dump(db_path)

    assert client.get('/api/integrations').status_code == 200
    assert client.get('/api/integrations?mode=real').status_code == 409
    assert client.get('/api/integrations?mode=invalid').status_code == 422
    assert client.get('/api/integrations?unexpected=value').status_code == 422
    assert client.get('/api/integrations?mode=simulated&mode=real').status_code == 422

    after = _database_dump(db_path)
    assert after == before
