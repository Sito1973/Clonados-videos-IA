"""Hosted use: password login, signed public media links and the provider callback path. Disabled when APP_PASSWORD is empty."""
import hashlib,hmac,os,secrets,threading,time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from config import ROOT,setting
RUNS=ROOT/'runs'
COOKIE='genjustsu_session'
LOCAL_HOSTS=('127.0.0.1','localhost','::1')

@lru_cache(maxsize=1)
def secret():
    """APP_SECRET, or a random key persisted beside the jobs (keep runs/ on a volume)."""
    if setting('APP_SECRET'):return setting('APP_SECRET').encode()
    path=RUNS/'.app-secret';RUNS.mkdir(exist_ok=True)
    if not path.exists():
        temporary=path.with_name(f'.app-secret.{os.getpid()}')
        with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as f:f.write(secrets.token_hex(32))
        try:os.link(temporary,path)
        except FileExistsError:pass
        finally:temporary.unlink(missing_ok=True)
    value=path.read_text().strip()
    if len(value)<32:raise RuntimeError('runs/.app-secret no es válido. Bórralo para crear uno nuevo.')
    return value.encode()

def sign(message):return hmac.new(secret(),message.encode(),hashlib.sha256).hexdigest()
def password():return setting('APP_PASSWORD')
def exposed():return setting('APP_HOST','127.0.0.1') not in LOCAL_HOSTS

def check_password(candidate):
    digest=lambda value:hashlib.sha256(value.encode()).digest()
    return bool(password()) and hmac.compare_digest(digest(candidate),digest(password()))

def _session_message(expires):
    # Changing APP_PASSWORD invalidates every existing session.
    return f'session|{expires}|'+hashlib.sha256(password().encode()).hexdigest()

def session_token(days=14):
    expires=int(time.time())+days*86400
    return f'{expires}.{sign(_session_message(expires))}'

def valid_session(token):
    try:expires,signature=token.split('.',1);expires=int(expires)
    except (AttributeError,ValueError):return False
    return expires>time.time() and hmac.compare_digest(signature.encode(),sign(_session_message(expires)).encode())

_failures={};_lock=threading.Lock()
def login_allowed(client):
    now=time.time()
    with _lock:
        _failures[client]=[t for t in _failures.get(client,[]) if now-t<900]
        return len(_failures[client])<10
def login_failed(client):
    with _lock:_failures.setdefault(client,[]).append(time.time())

def public_base():
    base=setting('PUBLIC_BASE_URL').rstrip('/')
    return base if base.startswith('https://') else ''

def callback_path():return '/callback/'+sign('callback')[:32]
def callback_url():return public_base()+callback_path() if public_base() else ''

def media_url(path,hours=None):
    """Unguessable, expiring link so a provider can fetch one run file. Never exposes other files."""
    if not public_base():raise ValueError('MEDIA_HOST=self necesita PUBLIC_BASE_URL=https://tu-dominio.')
    try:relative=Path(path).resolve().relative_to(RUNS.resolve()).as_posix()
    except ValueError:raise ValueError('Solo se pueden compartir con un proveedor archivos de runs/.') from None
    expires=int(time.time()+3600*float(hours or setting('MEDIA_LINK_HOURS','168')))
    return f'{public_base()}/media/{expires}/{sign(f"media|{expires}|{relative}")}/{quote(relative)}'

def media_file(expires,signature,relative):
    try:expires=int(expires)
    except ValueError:return None
    if expires<time.time() or not hmac.compare_digest(signature.encode(),sign(f'media|{expires}|{relative}').encode()):return None
    path=(RUNS/relative).resolve()
    return path if path.is_relative_to(RUNS.resolve()) and path.is_file() else None
