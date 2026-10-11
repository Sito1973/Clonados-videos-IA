"""Mandatory human/agent visual review, bound to the exact prepared assets."""
import hashlib,json
from pathlib import Path
from datetime import datetime,timezone

FILES=('original.mp4','depth.mp4','mask.mp4','composite-silent.mp4')
def is_mesh(folder):
    p=Path(folder)/'workflow-mode.json'
    return p.exists() and json.loads(p.read_text()).get('mode') in ['face_mesh','depth_mesh']

def fingerprint(folder):
    digest=hashlib.sha256()
    names=list(('workflow-mode.json','original.mp4','face-landmarks.json','composite-silent.mp4','seedance-input.mp4') if is_mesh(folder) else FILES)
    if is_mesh(folder) and json.loads((Path(folder)/'workflow-mode.json').read_text()).get('mode')=='depth_mesh':names+=['depth.mp4','mask.mp4','depth-composite-silent.mp4']
    for name in names:
        path=Path(folder)/name
        if not path.exists():raise ValueError('Prepara el video antes de revisarlo.')
        digest.update(name.encode())
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def state(folder):
    path=Path(folder)/'mask-review.json'
    saved=json.loads(path.read_text()) if path.exists() else {}
    try:current=fingerprint(folder)
    except ValueError:return {'approved':False,'status':'unavailable','note':'Primero prepara el video.'}
    if saved.get('fingerprint')!=current:return {'approved':False,'status':'pending','note':'Revisa el video preparado completo: la máscara en los modos con profundidad y el seguimiento de la malla facial en el modo rápido.','fingerprint':current}
    return saved

def review(folder,approved,note,expected):
    current=fingerprint(folder)
    if current!=expected:raise ValueError('El video preparado cambió. Recarga la página y revisa la versión más reciente.')
    data={'approved':approved,'status':'approved' if approved else 'rejected','note':note,'fingerprint':current,'reviewed_at':datetime.now(timezone.utc).isoformat()}
    (Path(folder)/'mask-review.json').write_text(json.dumps(data,indent=2))
    return data

def require_approved(folder):
    if not state(folder).get('approved'):raise ValueError('Falta la revisión: revisa el video preparado completo y apruébalo antes de enviarlo.')
