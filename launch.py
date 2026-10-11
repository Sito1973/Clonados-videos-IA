"""Start the app plus optional callback-only tunnel, never expose the app itself."""
import os,subprocess,sys,time,shutil
from config import ROOT,setting,seedance_provider
process=None
host=setting('APP_HOST','127.0.0.1');local=host in ['127.0.0.1','localhost','::1']
if not local and not setting('APP_PASSWORD'):raise SystemExit(f'Configura APP_PASSWORD antes de exponer la app fuera de localhost (APP_HOST={host}).')
try:
    # Only Enhancor requires a callback; with PUBLIC_BASE_URL the app receives it itself.
    needs_webhook=seedance_provider()=='enhancor'
    if needs_webhook and not setting('ENHANCOR_WEBHOOK_URL') and shutil.which('cloudflared'):
        (ROOT/'.runtime-webhook.json').unlink(missing_ok=True)
        process=subprocess.Popen([sys.executable,str(ROOT/'webhook.py')])
        for _ in range(60):
            if setting('ENHANCOR_WEBHOOK_URL'):break
            if process.poll() is not None:break
            time.sleep(1)
    if needs_webhook and not setting('ENHANCOR_WEBHOOK_URL'):
        print('La interfaz funciona, pero Enhancor necesita un webhook para generar. Instala cloudflared y reinicia, o configura ENHANCOR_WEBHOOK_URL.',flush=True)
    import uvicorn
    # Only the local reverse proxy (EasyPanel's Traefik on a private Docker network) may set client IP and scheme.
    proxies=os.getenv('FORWARDED_ALLOW_IPS') or ('127.0.0.1' if local else '127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fd00::/8')
    uvicorn.run('app:app',host=host,port=int(os.getenv('PORT','8770')),proxy_headers=True,forwarded_allow_ips=proxies)
finally:
    if process:
        import signal
        process.send_signal(signal.SIGINT)
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.terminate()
        (ROOT/'.runtime-webhook.json').unlink(missing_ok=True)
