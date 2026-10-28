"""Launch the local-only workspace without a persistent terminal window."""
import argparse
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from ff_agent.__main__ import load_environment
from ff_agent.build_identity import capture_deployment
from ff_agent.deploy_compat import decide_deployment
from ff_agent.launch_lock import launch_lock, LaunchBusy


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def open_health(url):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    return opener.open(urllib.request.Request(url,headers={'Cache-Control':'no-cache'}),timeout=.7)


def probe_health(url):
    try:
        with open_health(url+'/api/health') as response:
            raw=response.read(16385)
            if len(raw)>16384:
                return 'unknown',None
            return 'response',json.loads(raw)
    except urllib.error.URLError as error:
        cause=error.reason
        if isinstance(cause,OSError) and (cause.errno==errno.ECONNREFUSED or getattr(cause,'winerror',None)==10061):
            return 'absent',None
    except (OSError,ValueError):
        pass
    return 'unknown',None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    load_environment()
    port=int(os.getenv('FF_PORT','8765'))
    url=f'http://127.0.0.1:{port}'
    try:
        with launch_lock(ROOT/'.cache/launch.lock'):
            launch(url,args.no_browser)
    except (LaunchBusy,OSError) as error:
        raise SystemExit(str(error)) from None


def launch(url,no_browser):
    if not (ROOT/'frontend'/'dist'/'index.html').exists():
        raise SystemExit('Frontend is not built. Run scripts/setup.ps1 first.')
    expected=capture_deployment(ROOT)
    if expected['build_state']!='ready':
        raise SystemExit('Local build is missing, invalid, or stale. Rebuild the frontend before launch.')
    decision=decide_deployment(expected,*probe_health(url))
    if decision=='REJECT':
        raise SystemExit('Local service version is incompatible or unknown. No service was started or stopped.')
    if decision=='START_ALLOWED':
        (ROOT/'reports').mkdir(exist_ok=True)
        with (ROOT/'reports'/'server.log').open('a',encoding='utf-8') as log:
            flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
            process=subprocess.Popen([sys.executable,'-m','ff_agent'],cwd=ROOT/'backend',stdout=log,stderr=log,creationflags=flags)
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            decision=decide_deployment(expected,*probe_health(url))
            if decision=='REUSE':
                break
            if decision=='REJECT':
                raise SystemExit('Local service is incompatible or unknown. See reports/server.log.')
            if process.poll() is not None:
                raise SystemExit('Local service did not start. See reports/server.log.')
            time.sleep(.2)
        else:
            raise SystemExit('Local service is not ready. See reports/server.log.')
    if not no_browser:
        webbrowser.open(url)
    print('FF Agent is running at '+url)


if __name__=='__main__':
    main()
