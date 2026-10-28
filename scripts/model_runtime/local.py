"""Local source checks and a non-listening foreground runner lock."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import subprocess


@contextmanager
def single_runner(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell()==0:handle.write(b'\0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('WORKROOM_BUSY') from None
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def source_fingerprint(root,exclude_paths=()):
    root=Path(root).resolve()
    exclusions=tuple(Path(os.path.abspath(path)) for path in exclude_paths)
    if any(root.is_relative_to(excluded) for excluded in exclusions):
        raise ValueError('SOURCE_EXCLUDES_ROOT')
    def git(*args):
        result=subprocess.run(['git','-C',str(root),*args],capture_output=True,check=False)
        if result.returncode:raise ValueError('SOURCE_UNAVAILABLE')
        return result.stdout
    top=Path(git('rev-parse','--show-toplevel').decode().strip()).resolve()
    if top!=root:raise ValueError('SOURCE_ROOT_MISMATCH')
    result=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True)
    base=result.stdout.decode().strip() if result.returncode==0 else 'unborn'
    names=git('ls-files','--cached','--others','--exclude-standard','-z').decode('utf-8').split('\0')
    digest=hashlib.sha256()
    for name in sorted(set(filter(None,names)),key=lambda name:name.encode('utf-8')):
        path=root/name
        if any(Path(os.path.abspath(path)).is_relative_to(excluded) for excluded in exclusions):continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('SOURCE_LINK_REJECTED')
        content=path.read_bytes() if path.is_file() else b'WORKROOM_DELETED'
        digest.update(name.encode()+b'\0'+hashlib.sha256(content).digest())
    return base+':'+digest.hexdigest()
