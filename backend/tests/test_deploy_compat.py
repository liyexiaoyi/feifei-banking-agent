import pytest
from ff_agent.deploy_compat import decide_deployment, validate_meta

META = {'schema': 1, 'backend_id': 'a'*64, 'frontend_id': 'b'*64, 'assets_id': 'c'*64}


@pytest.mark.parametrize('expected,state,health,wanted', [
    (META, 'absent', None, 'START_ALLOWED'),
    (META, 'unknown', None, 'REJECT'),
    ({**META, 'schema': True}, 'absent', None, 'REJECT'),
    (META, 'response', {'service': 'ff-agent'}, 'REJECT'),
    (META, 'response', {'service': 'ff-agent', 'status': 'ok', 'deployment': {**META, 'build_state': 'ready'}}, 'REUSE'),
    (META, 'response', {'service': 'ff-agent', 'status': 'ok', 'deployment': {**META, 'build_state': 'stale'}}, 'REJECT'),
    (META, 'response', {'service': 'ff-agent', 'status': 'ok', 'deployment': {**META, 'frontend_id': 'd'*64, 'build_state': 'ready'}}, 'REJECT'),
    (META, 'response', [], 'REJECT'),
    ({**META, 'assets_id': 'A'*64}, 'absent', None, 'REJECT'),
])
def test_decision(expected, state, health, wanted):
    assert decide_deployment(expected, state, health) == wanted


def test_static_meta_does_not_require_runtime_state():
    assert validate_meta(META)
    assert not validate_meta({**META, 'backend_id': 'a'*64+'\n'})
