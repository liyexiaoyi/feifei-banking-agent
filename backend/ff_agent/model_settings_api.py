"""Owner-only DeepSeek model settings. Saves root/.env only; takes effect after manual restart."""
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from .bank import DomainError

BASE_URL='https://api.deepseek.com'
DEFAULT_MODEL='deepseek-flash'
MAX_BODY=4096
FIELDS=('model','mode','api_key','consent')
MODEL_RE=re.compile(r'[A-Za-z0-9_.:-]{1,100}')
# Visible ASCII without quote characters, which the .env loader would strip.
KEY_RE=re.compile(r'[\x21\x23-\x26\x28-\x7e]{12,512}')
MANAGED=('FF_MODEL_BASE_URL','FF_MODEL_NAME','FF_MODEL_API_KEY','FF_PLANNER_MODE','FF_MODEL_REASONING_EFFORT')


def _invalid():
    return DomainError('MODEL_SETTINGS_INVALID','模型设置无效，请检查后重试。',400)


def _unavailable():
    return DomainError('MODEL_SETTINGS_UNAVAILABLE','模型设置暂不可用，未做任何更改。',503)


def _env_path(root):
    path=Path(root).resolve()/'.env'
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise _unavailable()
    return path


def _read(path):
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    except FileNotFoundError:
        return ''
    except OSError:
        raise _unavailable() from None
    try:
        with os.fdopen(fd,'rb') as handle:
            return handle.read().decode('utf-8-sig')
    except (OSError,UnicodeError):
        raise _unavailable() from None


def _values(text):
    # First occurrence wins, matching the setdefault loader in __main__.
    found={}
    for line in text.splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1)
            key=key.strip()
            if key in MANAGED and key not in found:
                found[key]=value.strip().strip('"').strip("'")
    return found


def _official_key(values):
    key=values.get('FF_MODEL_API_KEY','')
    return key if key and values.get('FF_MODEL_BASE_URL')==BASE_URL else ''


def _snapshot(values,pending):
    model=values.get('FF_MODEL_NAME','')
    return {'provider':'deepseek','base_url':BASE_URL,
            'model':model if MODEL_RE.fullmatch(model) else DEFAULT_MODEL,
            'mode':'model' if values.get('FF_PLANNER_MODE')=='model' else 'rules',
            'has_key':bool(_official_key(values)),'editable':True,'restart_required':pending}


def _render(text,updates):
    out,done=[],set()
    for line in text.splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key=line.split('=',1)[0].strip()
            if key in updates:
                if key not in done:
                    out.append(f'{key}={updates[key]}')
                    done.add(key)
                continue
        out.append(line)
    out+=[f'{key}={value}' for key,value in updates.items() if key not in done]
    return '\n'.join(out)+'\n'


def _write(path,content):
    tmp=None
    try:
        fd,tmp=tempfile.mkstemp(prefix='.env.',suffix='.tmp',dir=path.parent)
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise OSError('symlink')
        os.replace(tmp,path)
        tmp=None
    except OSError:
        raise _unavailable() from None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


async def _read_body(request):
    length=request.headers.get('content-length')
    if length is not None and (not length.isdigit() or int(length)>MAX_BODY):
        raise _invalid()
    raw=b''
    async for chunk in request.stream():
        raw+=chunk
        if len(raw)>MAX_BODY:
            raise _invalid()
    return raw


def _parse(raw):
    try:
        body=json.loads(raw.decode('utf-8'))
    except (UnicodeError,ValueError,RecursionError):
        raise _invalid() from None
    if not isinstance(body,dict) or set(body)!=set(FIELDS):
        raise _invalid()
    model,mode,key,consent=(body[name] for name in FIELDS)
    if not (isinstance(model,str) and MODEL_RE.fullmatch(model) and mode in ('model','rules')
            and isinstance(key,str) and (key=='' or KEY_RE.fullmatch(key)) and isinstance(consent,bool)):
        raise _invalid()
    if mode=='model' and consent is not True:
        raise _invalid()
    return model,mode,key


def register_model_settings_api(app,current_user,root):
    root_of=root if callable(root) else (lambda:root)
    lock=threading.Lock()
    state={'pending':False}

    def require_owner(session):
        if session.get('user_id')!='demo':
            raise DomainError('MODEL_SETTINGS_FORBIDDEN','仅演示所有者可以修改模型设置。',403)
        if os.environ.get('FF_ALLOW_MODEL_SETTINGS')!='1':
            raise DomainError('MODEL_SETTINGS_DISABLED','模型设置未在服务端启用。',403)

    def save(model,mode,key):
        with lock:
            path=_env_path(root_of())
            text=_read(path)
            key=key or _official_key(_values(text))
            if mode=='model' and not key:
                raise _invalid()
            updates={'FF_MODEL_BASE_URL':BASE_URL,'FF_MODEL_NAME':model,'FF_MODEL_API_KEY':key,
                     'FF_PLANNER_MODE':mode,'FF_MODEL_REASONING_EFFORT':''}
            _write(path,_render(text,updates))
            state['pending']=True
            return {**_snapshot(updates,True),'saved':True,'restart_required':True}

    @app.get('/api/model-settings')
    def get_model_settings(session=Depends(current_user)):
        require_owner(session)
        with lock:
            values=_values(_read(_env_path(root_of())))
        return _snapshot(values,state['pending'])

    @app.post('/api/model-settings')
    async def save_model_settings(request:Request,session=Depends(current_user)):
        require_owner(session)
        model,mode,key=_parse(await _read_body(request))
        return await run_in_threadpool(save,model,mode,key)
