"""Development worker credentials never enter the runtime environment loader."""
import os
from ff_agent import __main__ as entry


def test_runtime_ignores_worker_credentials(tmp_path, monkeypatch):
    fields=('FF_WORKER_MODEL_API_KEY','FF_WORKER_MODEL_BASE_URL','FF_WORKER_MODEL_NAME')
    for field in fields:
        monkeypatch.delenv(field, raising=False)
    (tmp_path/'.env').write_text('\n'.join(field+'=developer-fixture' for field in fields), encoding='utf-8')
    monkeypatch.setattr(entry,'ROOT',tmp_path)
    entry.load_environment()
    assert all(field not in os.environ for field in fields)
