"""Launcher regression: all network/process/browser effects replaced with mocks."""
import errno
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError
from ff_agent.launch_lock import launch_lock

import pytest


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[2] / 'scripts/launch.py'
    spec = importlib.util.spec_from_file_location('launch_test', source)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    dist = tmp_path / 'frontend/dist'
    dist.mkdir(parents=True)
    (dist / 'index.html').write_text('test')
    meta = {'schema': 1, 'backend_id': 'a'*64, 'frontend_id': 'b'*64, 'assets_id': 'c'*64}
    (dist / 'build-meta.json').write_text(json.dumps(meta))
    monkeypatch.setattr(mod, 'ROOT', tmp_path)
    monkeypatch.setattr(mod, 'load_environment', lambda: None)
    monkeypatch.setattr(mod, 'capture_deployment', lambda root: {**meta, 'build_state': 'ready'}, raising=False)
    monkeypatch.setattr(mod.sys, 'argv', ['launch.py'])
    monkeypatch.setattr(mod.webbrowser, 'open', Mock())
    monkeypatch.setattr(mod.subprocess, 'Popen', Mock(side_effect=AssertionError('No real subprocess allowed')))
    # All probe I/O is replaced below, including redirect/proxy handling boundary.
    return mod, meta


@pytest.mark.parametrize('health', [
    {'service': 'ff-agent', 'version': 'old'},
    {'service': 'foreign', 'status': 'ok'}, [], None,
    {'service': 'ff-agent', 'status': 'ok', 'deployment': {'schema': 2}},
])
def test_reject_existing_old_or_unknown_service_without_start_or_browser(launcher, monkeypatch, health):
    mod, _ = launcher
    monkeypatch.setattr(mod, 'open_health', lambda *a, **kw: io.StringIO(json.dumps(health)))
    with pytest.raises(SystemExit):
        mod.main()
    mod.subprocess.Popen.assert_not_called()
    mod.webbrowser.open.assert_not_called()


def test_matching_service_can_be_reused(launcher, monkeypatch):
    mod, meta = launcher
    monkeypatch.setattr(mod, 'open_health', lambda *a, **kw: io.StringIO(json.dumps({
        'service': 'ff-agent', 'status': 'ok', 'deployment': {**meta, 'build_state': 'ready'}})))
    mod.main()
    mod.subprocess.Popen.assert_not_called()
    mod.webbrowser.open.assert_called_once()


@pytest.mark.parametrize('error', [TimeoutError(), URLError(TimeoutError()), ValueError('invalid JSON')])
def test_uncertain_probe_never_starts(launcher, monkeypatch, error):
    mod, _ = launcher
    monkeypatch.setattr(mod, 'open_health', Mock(side_effect=error))
    with pytest.raises(SystemExit):
        mod.main()
    mod.subprocess.Popen.assert_not_called()
    mod.webbrowser.open.assert_not_called()


def test_only_confirmed_refused_can_reach_mock_start_path(launcher, monkeypatch):
    mod, meta = launcher
    response = lambda: io.StringIO(json.dumps({'service': 'ff-agent', 'status': 'ok',
        'deployment': {**meta, 'build_state': 'ready'}}))
    monkeypatch.setattr(mod, 'open_health', Mock(side_effect=[
        URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused')), response()]))
    monkeypatch.setattr(mod.subprocess, 'Popen', Mock(return_value=Mock(poll=lambda: None)))
    mod.main()
    mod.subprocess.Popen.assert_called_once()
    mod.webbrowser.open.assert_called_once()


def test_locked_launcher_does_not_probe_or_spawn(launcher, monkeypatch):
    mod, _ = launcher
    probe = Mock(side_effect=AssertionError('Locked launcher must not probe'))
    monkeypatch.setattr(mod, 'open_health', probe)
    with launch_lock(mod.ROOT / '.cache/launch.lock'):
        with pytest.raises(SystemExit):
            mod.main()
    probe.assert_not_called()
    mod.subprocess.Popen.assert_not_called()
    mod.webbrowser.open.assert_not_called()
