"""Project-local credentials only; environment values take precedence."""
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def load_env():
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):continue
            key,sep,value=line.partition('=')
            if sep:os.environ.setdefault(key.strip(),value.strip().strip('\"').strip("'"))

def setting(name,default=''):
    load_env()
    value=os.getenv(name,default)
    if name=='ENHANCOR_WEBHOOK_URL' and not value:
        # A public deployment receives callbacks itself; locally launch.py writes a tunnel URL.
        if setting('PUBLIC_BASE_URL').startswith('https://'):
            from auth import callback_url
            return callback_url()
        import json
        try:value=json.loads((ROOT/'.runtime-webhook.json').read_text())['url']
        except (OSError,ValueError,KeyError):pass
    return value

def credential(name):
    value=setting(name)
    if not value:raise ValueError(f'Falta {name}: configúralo en .env o en las variables de entorno (Environment en EasyPanel).')
    return value

def seedance_provider():
    """enhancor (original integration) or kie; kie is implied when only a kie.ai key exists."""
    value=setting('SEEDANCE_PROVIDER').strip().lower()
    if value:return value
    return 'kie' if setting('KIE_API_KEY') and not setting('ENHANCOR_API_KEY') else 'enhancor'

def separation_provider():
    """replicate (Demucs) or kie (separate-vocals); kie is implied when only a kie.ai key exists."""
    value=setting('AUDIO_SEPARATION').strip().lower()
    if value:return value
    return 'kie' if setting('KIE_API_KEY') and not setting('REPLICATE_API_TOKEN') else 'replicate'

def require_mode(mode):
    """Refuse a preparation that would stop midway after billed calls."""
    if mode!='face_mesh' and not setting('REPLICATE_API_TOKEN'):
        raise ValueError('Los modos con profundidad necesitan REPLICATE_API_TOKEN: la profundidad y las máscaras SAM 3 se generan en Replicate. Usa Rápido · Malla facial o añade el token de Replicate.')
    separation=separation_provider()
    if separation not in ['replicate','kie']:raise ValueError('AUDIO_SEPARATION debe ser replicate o kie.')
    credential('KIE_API_KEY' if separation=='kie' else 'REPLICATE_API_TOKEN')
