"""Offline checks; never prints credential values or starts paid calls."""
import argparse,importlib,shutil,sys
from config import setting,seedance_provider,separation_provider

def configured(key):
    value=setting(key).strip()
    return bool(value) and not any(x in value.lower() for x in ['your_key','your_api','replace_me','placeholder'])

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--require-keys',action='store_true');parser.add_argument('--mesh',action='store_true');args=parser.parse_args()
    failed=sys.version_info<(3,12)
    print('Python:',sys.version.split()[0], '(3.12+ required)')
    for name in ['av','numpy','PIL','requests','fastapi','uvicorn','multipart','gradio_client','scipy']:
        try:importlib.import_module(name);print(name+': OK')
        except Exception as exc:print(name+': FAILED ('+type(exc).__name__+')');failed=True
    binary=shutil.which('rubberband');print('rubberband:', 'OK' if binary else 'MISSING — see docs/SETUP-AGENT.md');failed=failed or not binary
    if args.mesh:
        from pathlib import Path
        import subprocess
        worker=Path(__file__).resolve().parent/'.venv-face/bin/python'
        model=Path(__file__).resolve().parent/'models/face_landmarker.task'
        # Creating the landmarker loads MediaPipe's native library (EGL/GLES), which a plain import does not.
        code='import mediapipe,av,PIL,numpy'+(';import sys,mediapipe as mp;mp.tasks.vision.FaceLandmarker.create_from_options(mp.tasks.vision.FaceLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_path=sys.argv[1]))).close()' if model.exists() else '')
        result=subprocess.run([str(worker),'-c',code]+([str(model)] if model.exists() else []),capture_output=True,text=True) if worker.exists() else None
        ready=bool(result) and result.returncode==0
        reason=(result.stderr.strip().splitlines() or ['unknown error'])[-1] if result and not ready else ''
        print('Face mesh worker: '+('OK' if ready else f'FAILED ({reason}); on Linux install libegl1 and libgles2' if result else 'MISSING — run bash setup-face-mesh.sh'))
        failed=failed or not ready
    service=seedance_provider();separation=separation_provider()
    print('Seedance provider:',service);print('Vocal separation:',separation)
    if service not in ['enhancor','kie']:print('SEEDANCE_PROVIDER must be enhancor or kie');failed=True
    if separation not in ['replicate','kie']:print('AUDIO_SEPARATION must be replicate or kie');failed=True
    missing=[]
    for key in dict.fromkeys(['KIE_API_KEY' if service=='kie' else 'ENHANCOR_API_KEY','KIE_API_KEY' if separation=='kie' else 'REPLICATE_API_TOKEN']):
        print(key+': '+('configured (not verified remotely)' if configured(key) else 'MISSING'))
        if not configured(key):missing.append(key)
    print('Depth + SAM modes: '+('available' if configured('REPLICATE_API_TOKEN') else 'unavailable until REPLICATE_API_TOKEN is set; Fast · Face mesh still works'))
    host=setting('MEDIA_HOST','tmpfiles')
    if host not in ['kie','self','tmpfiles','catbox']:
        print('MEDIA_HOST must be kie, self, tmpfiles or catbox');failed=True
    if host=='kie' and not configured('KIE_API_KEY'):
        print('MEDIA_HOST=kie needs KIE_API_KEY');failed=True
    if host=='self' and not setting('PUBLIC_BASE_URL').startswith('https://'):
        print('MEDIA_HOST=self needs PUBLIC_BASE_URL=https://your-domain');failed=True
    if setting('APP_HOST','127.0.0.1') not in ['127.0.0.1','localhost','::1'] and not setting('APP_PASSWORD'):
        print('APP_PASSWORD is required when APP_HOST exposes the app beyond localhost');failed=True
    print('Login: '+('enabled' if setting('APP_PASSWORD') else 'disabled (localhost use only)'))
    if service=='enhancor' and not setting('ENHANCOR_WEBHOOK_URL'):
        print('WEBHOOK: not running/configured — install cloudflared and launch the app, set PUBLIC_BASE_URL, or set your own HTTPS callback')
        missing.append('ENHANCOR_WEBHOOK_URL')
    if failed:print('DEPENDENCIES NOT READY');return 1
    if missing:
        print('LOCAL UI READY; GENERATION BLOCKED until keys and the required callback are configured.')
        return 2 if args.require_keys else 0
    print('LOCAL SETUP READY; provider access/balance has not been verified by this offline check.')
    return 0
if __name__=='__main__':sys.exit(main())
