"""Optional local face-mesh preparation; no depth or segmentation calls."""
from pathlib import Path
import json,subprocess,sys,shutil
ROOT=Path(__file__).resolve().parent

def _track(folder,*flags):
    """Run the isolated MediaPipe worker and report its own error message, not the command line."""
    result=subprocess.run([str(ROOT/'.venv-face/bin/python'),str(ROOT/'mesh_worker.py'),str(folder),*flags],capture_output=True,text=True)
    if result.returncode:raise RuntimeError((result.stderr.strip().splitlines() or ['Falló el seguimiento facial.'])[-1].split(': ',1)[-1])

def run(src,folder,status=print):
    from pipeline import normalize
    from audio_workflow import separate,prepare_seedance_video
    folder=Path(folder)
    python=ROOT/'.venv-face/bin/python'
    if not python.exists():raise ValueError('La malla facial no está instalada: ejecuta bash setup-face-mesh.sh (Python 3.12).')
    folder.mkdir(parents=True,exist_ok=True)
    status('Normalizando el video');normalize(src,folder/'original.mp4')
    status('Siguiendo la malla facial · sin profundidad ni SAM')
    _track(folder)
    (folder/'workflow-mode.json').write_text(json.dumps({'mode':'face_mesh'}))
    separate(src,folder,status)
    prepare_seedance_video(folder)
    status('Malla facial lista · revisa el seguimiento y la voz')


def run_depth(src,folder,prompt='person',provider='replicate',status=print):
    from pipeline import run as run_advanced
    from audio_workflow import prepare_seedance_video
    folder=Path(folder)
    python=ROOT/'.venv-face/bin/python'
    if not python.exists():raise ValueError('Instala primero la malla facial: bash setup-face-mesh.sh')
    folder.mkdir(parents=True,exist_ok=True)
    run_advanced(src,folder,prompt,provider,status)
    shutil.copy2(folder/'composite-silent.mp4',folder/'depth-composite-silent.mp4')
    status('Añadiendo la malla facial sobre el video de profundidad')
    _track(folder,'--depth')
    (folder/'workflow-mode.json').write_text(json.dumps({'mode':'depth_mesh'}))
    prepare_seedance_video(folder)
    status('Profundidad + malla facial listas · revisa la máscara y el seguimiento facial')
