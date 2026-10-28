"""Explicit loopback proxy; HTTPS verification stays enabled."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

from .registry import load_env

_NAME='FF_RUNTIME_PROXY_URL'


def streaming_enabled(root: Path) -> bool:
    """Explicit project opt-in; never resubmits any stored task."""
    value = os.environ.get('FF_WORKROOM_STREAMING')
    if value is None:
        value = load_env(Path(root)/'.env').get('FF_WORKROOM_STREAMING','0')
    if value not in ('0','1'):
        raise ValueError('INVALID_WORKROOM_STREAM_MODE')
    return value == '1'


def client_options(root: Path) -> dict:
    """Return HTTPX options without inheriting ambient proxy or TLS settings.

    The private project configuration may explicitly select an existing local
    forwarding proxy. This does not start that proxy or change OS networking.
    An explicitly empty process setting disables the saved proxy.
    """
    value=os.environ.get(_NAME)
    if value is None:
        value=load_env(Path(root)/'.env').get(_NAME,'')
    result={'trust_env':False,'follow_redirects':False}
    if value=='':
        return result
    try:
        if not isinstance(value,str) or any(ord(c)<33 or ord(c)==127 for c in value):
            raise ValueError
        url=urlsplit(value)
        if (url.scheme not in ('http','https') or not url.hostname
            or url.username is not None or url.password is not None
            or url.path not in ('','/') or '?' in value or '#' in value
            or url.port is None or not 1<=url.port<=65535):
            raise ValueError
        if url.hostname.lower()!='localhost' and not ipaddress.ip_address(url.hostname).is_loopback:
            raise ValueError
    except (ValueError,TypeError):
        raise ValueError('INVALID_WORKROOM_PROXY') from None
    result['proxy']=value
    return result
