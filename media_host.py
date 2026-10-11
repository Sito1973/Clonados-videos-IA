"""Direct media URLs for the providers: signed links from this server (self) or public hosts. Configured fallback hosts are tried only for media hosting failures."""
from pathlib import Path
from urllib.parse import urlparse
import requests,re,html,time,warnings
from config import setting

def _upload_once(path,host):
    path=Path(path)
    with path.open('rb') as f:
        if host=='self':
            from auth import media_url
            url=media_url(path)
        elif host=='kie':
            # kie.ai's own temporary storage (files deleted after 24 hours), reachable by its model backends.
            import uuid
            from config import credential
            r=requests.post('https://kieai.redpandaai.co/api/file-stream-upload',headers={'Authorization':'Bearer '+credential('KIE_API_KEY')},files={'file':(path.name,f)},data={'uploadPath':'genjustsu/'+uuid.uuid4().hex,'fileName':path.name},timeout=300)
            r.raise_for_status();j=r.json();url=(j.get('data') or {}).get('downloadUrl') or ''
            if j.get('success') is False or not url:raise ValueError('Falló la subida a kie.ai: '+str(j.get('msg')))
        elif host=='tmpfiles':
            if path.stat().st_size>100_000_000:raise ValueError('Tmpfiles admite hasta 100 MB: elige otro servicio o reduce el archivo.')
            r=requests.post('https://tmpfiles.org/api/v1/upload',files={'file':(path.name,f)},data={'expire':21600},timeout=180)
            r.raise_for_status();url=r.json()['data']['url']
            parsed=urlparse(url)
            if parsed.hostname!='tmpfiles.org':raise ValueError('Respuesta inesperada del servicio temporal.')
            page=requests.get(url,timeout=60)
            page.raise_for_status()
            matches=re.findall(r'https://tmpfiles\.org/dl/[^\"\'<> ]+',page.text)
            if not matches:raise ValueError('La página de Tmpfiles no ofreció un enlace directo al archivo.')
            url=html.unescape(matches[0])
        elif host=='catbox':
            data={'reqtype':'fileupload'}
            if setting('CATBOX_USERHASH'):data['userhash']=setting('CATBOX_USERHASH')
            r=requests.post('https://catbox.moe/user/api.php',data=data,files={'fileToUpload':(path.name,f)},timeout=180)
            r.raise_for_status();url=r.text.strip()
        else:raise ValueError('MEDIA_HOST debe ser kie, self, tmpfiles o catbox.')
    if not url.startswith('https://'):raise ValueError('El servicio no devolvió una URL HTTPS.')
    if host=='self':requests.head(url,timeout=60).raise_for_status()
    with requests.get(url,stream=True,timeout=60) as check:
        check.raise_for_status()
        if 'text/html' in check.headers.get('Content-Type',''):raise ValueError('La URL devolvió una página, no un archivo multimedia.')
        first=next(check.iter_content(256),b'')
        if not first or first.lstrip().lower().startswith((b'<!doctype',b'<html')):raise ValueError('La URL devolvió una respuesta vacía o HTML.')
    return url


def upload(path):
    """Retry hosting only; never retries a paid model request."""
    primary=setting('MEDIA_HOST','tmpfiles')
    fallback=setting('MEDIA_HOST_FALLBACKS','catbox')
    hosts=list(dict.fromkeys([primary]+[h.strip() for h in fallback.split(',') if h.strip()]))
    if any(h not in ['kie','self','tmpfiles','catbox'] for h in hosts):raise ValueError('Servicios admitidos para MEDIA_HOST: kie, self, tmpfiles, catbox.')
    errors=[]
    for host in hosts:
        for attempt in range(2):
            try:return _upload_once(path,host)
            except (requests.RequestException,ValueError,KeyError,StopIteration) as exc:
                errors.append(host+': '+type(exc).__name__+(' '+str(exc)[:160] if str(exc) else ''))
                if attempt==0:time.sleep(1)
        warnings.warn('Falló la subida en '+host+'; se prueba el siguiente servicio configurado.')
    raise RuntimeError('No se pudieron subir los archivos ('+', '.join(errors)+'). No se envió ninguna generación. Revisa la conexión o cambia MEDIA_HOST.')
