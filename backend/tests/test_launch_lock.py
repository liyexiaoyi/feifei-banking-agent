import pytest
import subprocess
import sys
from pathlib import Path
from ff_agent.launch_lock import launch_lock, LaunchBusy


def test_two_handles_cannot_start_same_checkout_concurrently(tmp_path):
    path = tmp_path / '.cache/launch.lock'
    with launch_lock(path):
        with pytest.raises(LaunchBusy):
            with launch_lock(path):
                pytest.fail('Second launcher must not enter start path')
    with launch_lock(path):
        pass  # Release must permit a later explicit launch.


def test_lock_is_exclusive_across_real_non_listening_processes(tmp_path):
    path = tmp_path / '.cache/launch.lock'
    backend = Path(__file__).resolve().parents[1]
    code = f"""import sys
from pathlib import Path
sys.path.insert(0, {str(backend)!r})
from ff_agent.launch_lock import launch_lock, LaunchBusy
try:
    with launch_lock(Path(sys.argv[1])):
        pass
except LaunchBusy:
    sys.exit(23)
"""
    with launch_lock(path):
        child = subprocess.run([sys.executable, '-c', code, str(path)], timeout=5, capture_output=True)
        assert child.returncode == 23, child.stderr
    child = subprocess.run([sys.executable, '-c', code, str(path)], timeout=5, capture_output=True)
    assert child.returncode == 0, child.stderr
