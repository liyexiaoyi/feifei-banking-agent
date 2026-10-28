import os

import ff_agent.__main__ as entry


def test_auxiliary_model_config_is_loaded_separately_and_unknown_keys_are_ignored(tmp_path,monkeypatch):
    monkeypatch.setattr(entry,'ROOT',tmp_path)
    for key in ('FF_AUX_MODEL_BASE_URL','FF_AUX_MODEL_NAME','FF_AUX_MODEL_API_KEY','UNKNOWN_SECRET'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('FF_MODEL_API_KEY','primary-test-secret')
    (tmp_path/'.env').write_text('FF_MODEL_API_KEY=replacement-test-secret\nFF_AUX_MODEL_BASE_URL=https://aux.example/v1\nFF_AUX_MODEL_NAME=aux-model\nFF_AUX_MODEL_API_KEY=aux-test-secret\nUNKNOWN_SECRET=ignored\n',encoding='utf-8')
    entry.load_environment()
    assert os.environ['FF_MODEL_API_KEY']=='primary-test-secret'
    assert os.environ.get('FF_AUX_MODEL_NAME')=='aux-model'
    assert os.environ.get('FF_AUX_MODEL_API_KEY')=='aux-test-secret'
    assert os.environ.get('FF_AUX_MODEL_BASE_URL')=='https://aux.example/v1'
    assert 'UNKNOWN_SECRET' not in os.environ


def test_coordinator_and_reasoning_settings_are_loaded_without_replacing_runtime_model(tmp_path,monkeypatch):
    monkeypatch.setattr(entry,'ROOT',tmp_path)
    values={'FF_COORD_MODEL_BASE_URL':'https://coordinator.example/v1','FF_COORD_MODEL_NAME':'test-coordinator',
            'FF_COORD_MODEL_API_KEY':'coordinator-test-only','FF_COORD_MODEL_REASONING_EFFORT':'high',
            'FF_MODEL_REASONING_EFFORT':'medium','FF_AUX_MODEL_REASONING_EFFORT':'low'}
    for key in values: monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('FF_MODEL_NAME','runtime-test-model')
    (tmp_path/'.env').write_text('\n'.join(k+'='+v for k,v in values.items()),encoding='utf-8')
    entry.load_environment()
    assert all(os.environ.get(key)==value for key,value in values.items())
    assert os.environ['FF_MODEL_NAME']=='runtime-test-model'
