"""Pure metadata helper: explicit transfer recipient named in the current request.

No bank effects, no persistence. Callers decide what to do with the result.
"""
import re
import unicodedata

ALIASES=('妈妈','爸爸','林晓','朋友')
MAX_CHARS=2000
_CLAUSE=re.compile(r'[，,；;。!！?？\n]')
_GIVE=re.compile(r'给\s*([^\s，,；;。]{1,20}?)\s*转')
_TO=re.compile(r'转(?:账)?给\s*(\d+|[^\s\d，,；;。零一二两三四五六七八九十百千万元块]{1,20})')
_PHONE=re.compile(r'[0-9]{11}')


def explicit_recipient(text):
    """Return (mentioned, pending).

    mentioned is True when the request names any transfer recipient.
    pending is {'payee': name} only for exactly one whitelisted alias or one
    whole 11-digit number; otherwise None (unknown, multiple or bad digits).
    """
    text=unicodedata.normalize('NFKC',str(text or '')[:MAX_CHARS])
    names=[]
    for clause in _CLAUSE.split(text):
        if '转' not in clause:
            continue
        for pattern in (_GIVE,_TO):
            for m in pattern.finditer(clause):
                name=m[1].strip()
                if name:
                    names.append(name)
    names=list(dict.fromkeys(names))
    if not names:
        return False,None
    if len(names)!=1:
        return True,None
    name=names[0]
    if name in ALIASES or _PHONE.fullmatch(name):
        return True,{'payee':name}
    return True,None
