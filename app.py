from pathlib import Path
import threading,uuid,shutil,json,hmac
from fastapi import FastAPI,UploadFile,File,Form,HTTPException,Request
from fastapi.responses import FileResponse,HTMLResponse,JSONResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from pipeline import run
import auth
ROOT=Path(__file__).resolve().parent
RUNS=ROOT/'runs';RUNS.mkdir(exist_ok=True)
if auth.exposed() and not auth.password():raise RuntimeError('Configura APP_PASSWORD antes de exponer la app fuera de localhost.')
app=FastAPI();jobs={}

@app.middleware('http')
async def require_login(request:Request,call_next):
    path=request.url.path
    if '/.' in path:return JSONResponse({'detail':'Not Found'},404)
    if not auth.password() or path in ['/health','/login'] or path.startswith(('/callback/','/media/')) or auth.valid_session(request.cookies.get(auth.COOKIE,'')):return await call_next(request)
    if request.method=='GET' and 'text/html' in request.headers.get('accept',''):return RedirectResponse('/login',303)
    return JSONResponse({'detail':'Inicia sesión para continuar.'},401)

LOGIN='''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Iniciar sesión · Genjustsu</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#fafafb;color:#2b2f3a;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}form{width:min(360px,calc(100vw - 32px));padding:28px;background:#fff;border:1px solid #ececef;border-radius:16px;box-shadow:0 12px 32px -12px #16181f14}h1{font-size:22px;margin:0 0 18px}input{width:100%;box-sizing:border-box;font:inherit;padding:12px;border:1px solid #dfe2e8;border-radius:8px;margin-bottom:14px}button{width:100%;font:inherit;font-weight:650;padding:12px;border:0;border-radius:8px;background:#1f18c0;color:#fff;cursor:pointer}p{color:#b42318;font-size:13px;margin:0 0 12px}</style></head><body><form method="post" action="/login"><h1>Genjustsu</h1><!--error--><input type="password" name="password" placeholder="Contraseña" aria-label="Contraseña" autocomplete="current-password" required autofocus><button>Entrar</button></form></body></html>'''
def login_page(error='',code=200):return HTMLResponse(LOGIN.replace('<!--error-->',f'<p>{error}</p>' if error else ''),code)

@app.get('/login')
def show_login():return login_page()

@app.post('/login')
def login(request:Request,password:str=Form('')):
    if not auth.password():return RedirectResponse('/',303)
    client=request.client.host if request.client else 'unknown'
    if not auth.login_allowed(client):return login_page('Demasiados intentos. Vuelve a intentarlo en 15 minutos.',429)
    if not auth.check_password(password):
        auth.login_failed(client)
        return login_page('Contraseña incorrecta.',401)
    response=RedirectResponse('/',303)
    response.set_cookie(auth.COOKIE,auth.session_token(),max_age=14*86400,httponly=True,samesite='lax',secure=request.url.scheme=='https')
    return response

@app.get('/logout')
def logout():
    response=RedirectResponse('/login',303);response.delete_cookie(auth.COOKIE)
    return response

@app.post('/callback/{token}')
async def callback(token:str,request:Request):
    if not hmac.compare_digest(('/callback/'+token).encode(),auth.callback_path().encode()):raise HTTPException(404)
    body=await request.body()
    if not 0<len(body)<=65536:raise HTTPException(413)
    try:json.loads(body)
    except ValueError:raise HTTPException(400)
    # Polling remains authoritative; unverified callbacks cannot alter jobs.
    return {'received':True}

@app.api_route('/media/{expires}/{signature}/{relative:path}',methods=['GET','HEAD'])
def media(expires:str,signature:str,relative:str):
    path=auth.media_file(expires,signature,relative)
    if not path:raise HTTPException(404)
    return FileResponse(path)

@app.get('/settings')
def settings():
    from config import seedance_provider,separation_provider,setting
    service=seedance_provider()
    return {'seedance_provider':service,'seedance_label':{'kie':'kie.ai','enhancor':'Enhancor'}.get(service,service),'separation':separation_provider(),'depth_available':bool(setting('REPLICATE_API_TOKEN')),'login':bool(auth.password())}

@app.get('/')
def index():return FileResponse(ROOT/'static/index.html')
@app.post('/jobs')
def create(video:UploadFile=File(...),prompt:str=Form('person'),provider:str=Form('replicate'),mode:str=Form('depth'),trim_start:float=Form(0),trim_end:float=Form(0)):
    if mode not in ['depth','face_mesh','depth_mesh']:raise HTTPException(400,'Modo de preparación desconocido.')
    if provider not in ['huggingface','replicate','replicate-chenxwh']:raise HTTPException(400,'Proveedor de profundidad desconocido.')
    from config import require_mode
    try:require_mode(mode)
    except ValueError as e:raise HTTPException(400,str(e))
    ident=uuid.uuid4().hex;folder=RUNS/ident;folder.mkdir();src=folder/'upload.mp4'
    from pipeline import check_clip,trim_source,VIDEO_SUFFIXES
    from config import seedance_provider
    suffix=Path(video.filename or '').suffix.lower();original=folder/('upload-original'+(suffix if suffix in VIDEO_SUFFIXES else '.mp4'))
    with original.open('wb') as out:shutil.copyfileobj(video.file,out)
    try:
        trim_source(original,src,trim_start,trim_end)
        check_clip(src,4 if seedance_provider()=='kie' else 0)
    except ValueError as e:
        shutil.rmtree(folder,ignore_errors=True)
        raise HTTPException(400,str(e))
    jobs[ident]={'state':'running','status':'Iniciando','id':ident}
    def work():
        try:
            if mode=='face_mesh':
                from face_mesh import run as run_mesh
                run_mesh(src,folder,lambda s:jobs[ident].update(status=s))
            elif mode=='depth_mesh':
                from face_mesh import run_depth
                run_depth(src,folder,prompt,provider,lambda s:jobs[ident].update(status=s))
            else:run(src,folder,prompt,provider,lambda s:jobs[ident].update(status=s))
            jobs[ident].update(state='done',status='Preparación completa · revisa el video preparado')
            (folder/'status.json').write_text(json.dumps(jobs[ident]))
        except Exception as e:
            jobs[ident].update(state='error',status=str(e))
            (folder/'status.json').write_text(json.dumps(jobs[ident]))
    threading.Thread(target=work,daemon=True).start();return jobs[ident]
@app.get('/jobs/{ident}')
def get(ident:str):
    if ident not in jobs:
        if len(ident)==32 and all(c in '0123456789abcdef' for c in ident) and (RUNS/ident/'status.json').exists():return json.loads((RUNS/ident/'status.json').read_text())
        raise HTTPException(404)
    return jobs[ident]
@app.post('/jobs/{ident}/seedance')
def seedance(ident:str,prompt:str=Form(...),sirio:list[UploadFile]=File(...),tein:list[UploadFile]|None=File(None)):
    if len(ident)!=32 or not all(c in '0123456789abcdef' for c in ident):raise HTTPException(404)
    folder=RUNS/ident
    if not (folder/'audio-manifest.json').exists():raise HTTPException(400,'Primero debe terminar la separación de voz.')
    from mask_review import require_approved
    try:require_approved(folder)
    except ValueError as e:raise HTTPException(409,str(e))
    images,videos=['.png','.jpg','.jpeg','.webp'],['.mp4','.mov','.webm']
    groups={'sirio':[u for u in sirio if u.filename],'tein':[u for u in tein or [] if u.filename]}
    kinds={name:[Path(u.filename).suffix.lower() for u in uploads] for name,uploads in groups.items()}
    if not groups['sirio']:raise HTTPException(400,'Primero elige la referencia del Personaje 1.')
    if any(s not in images+videos for s in kinds['sirio']) or any(s not in images for s in kinds['tein']):raise HTTPException(400,'Usa imágenes PNG, JPEG o WebP, o un solo video para el Personaje 1.')
    if any(s in videos for s in kinds['sirio']) and len(kinds['sirio'])>1:raise HTTPException(400,'El Personaje 1 lleva un video o una o varias imágenes, no ambas cosas.')
    from config import seedance_provider
    limit=30 if seedance_provider()=='kie' else 9
    if sum(s in images for k in kinds.values() for s in k)>limit:raise HTTPException(400,f'Usa como máximo {limit} imágenes de referencia.')
    for old in [*folder.glob('sirio-reference*'),*folder.glob('tein-reference*')]:old.unlink()
    saved={}
    for name,uploads in groups.items():
        saved[name]=[]
        for i,upload in enumerate(uploads,1):
            path=folder/f'{name}-reference-{i}{Path(upload.filename).suffix.lower()}'
            with path.open('wb') as f:shutil.copyfileobj(upload.file,f)
            saved[name].append(path.name)
    (folder/'references.json').write_text(json.dumps(saved))
    from seedance_bridge import submit
    try:request_id=submit(folder,prompt,[folder/n for n in saved['sirio']],[folder/n for n in saved['tein']])
    except Exception as e:
        # A 4xx keeps the provider's message: reverse proxies (EasyPanel/Traefik) replace 502 bodies with their own page.
        print(f'Seedance submission failed for job {ident}: {e}',flush=True)
        raise HTTPException(422,str(e))
    jobs[ident]={'id':ident,'state':'running','status':'Generando en Seedance: '+request_id}
    def collect():
        from seedance_bridge import wait_and_finish
        try:
            wait_and_finish(folder,request_id,lambda s:jobs[ident].update(status=s))
            jobs[ident].update(state='done',status='Vista final lista · audio original completo',final_url='/runs/'+ident+'/final-preview.mp4')
        except Exception as e:jobs[ident].update(state='error',status=str(e))
        (folder/'status.json').write_text(json.dumps(jobs[ident]))
    threading.Thread(target=collect,daemon=True).start()
    return {'request_id':request_id,'id':ident,'state':'running','status':jobs[ident]['status']}

@app.post('/jobs/{ident}/finish')
def finish(ident:str,video:UploadFile=File(...)):
    if len(ident)!=32 or not all(c in '0123456789abcdef' for c in ident):raise HTTPException(404)
    folder=RUNS/ident
    if not (folder/'upload.mp4').exists():raise HTTPException(400,'Falta el video original.')
    target=folder/'seedance-result.mp4'
    with target.open('wb') as f:shutil.copyfileobj(video.file,f)
    from audio_workflow import finish_preview
    try:finish_preview(target,folder)
    except Exception as e:raise HTTPException(400,str(e))
    return {'url':'/runs/'+ident+'/final-preview.mp4'}

app.mount('/runs' ,StaticFiles(directory=RUNS),name='runs')

hd_jobs={}
hd_lock=threading.Lock()

def hd_folder(ident):
    if len(ident)!=32 or not all(c in '0123456789abcdef' for c in ident):raise HTTPException(404)
    folder=RUNS/ident
    if not folder.exists():raise HTTPException(404)
    return folder

@app.get('/jobs/{ident}/hd')
def get_hd(ident:str):
    folder=hd_folder(ident)
    if ident in hd_jobs:return hd_jobs[ident]
    saved=folder/'hd/status.json'
    if saved.exists():return json.loads(saved.read_text())
    return {'state':'idle','status':'Aprueba tu borrador para generar el 1080p.'}

@app.post('/jobs/{ident}/hd')
def create_hd(ident:str):
    folder=hd_folder(ident)
    with hd_lock:
        previous=get_hd(ident)
        if previous['state'] in ['running','done']:return previous
        if not (folder/'final-preview.mp4').exists():raise HTTPException(400,'Primero completa y revisa un borrador.')
        hd=folder/'hd';hd.mkdir(exist_ok=True)
        hd_jobs[ident]={'state':'running','status':'Enviando el borrador aprobado para el 1080p','id':ident}
        def save(): (hd/'status.json').write_text(json.dumps(hd_jobs[ident]))
        save()
    def work_hd():
        from seedance_bridge import submit_hd,wait_and_finish
        try:
            request_id=submit_hd(folder)
            hd_jobs[ident].update(request_id=request_id,status='Generando el 1080p');save()
            wait_and_finish(hd,request_id,lambda s:hd_jobs[ident].update(status=s))
            hd_jobs[ident].update(state='done',status='1080p listo · audio original restaurado')
        except Exception as e:hd_jobs[ident].update(state='error',status=str(e))
        save()
    threading.Thread(target=work_hd,daemon=True).start()
    return hd_jobs[ident]

@app.get('/jobs/{ident}/mask-review')
def get_mask_review(ident:str):
    from mask_review import state
    return state(hd_folder(ident))

@app.post('/jobs/{ident}/mask-review')
def save_mask_review(ident:str,approved:bool=Form(...),fingerprint:str=Form(...),note:str=Form('')):
    from mask_review import review
    try:return review(hd_folder(ident),approved,note,fingerprint)
    except ValueError as e:raise HTTPException(409,str(e))

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

@app.on_event('startup')
def resume_saved_generations():
    """Resume collection only: never requeue a paid prediction on restart."""
    from seedance_bridge import wait_and_finish
    for root in RUNS.iterdir():
        if not root.is_dir() or len(root.name)!=32:continue
        for folder,response_file,registry in [(root,'seedance-response.json',jobs),(root/'hd','response.json',hd_jobs)]:
            response=folder/response_file
            if not response.exists() or (folder/'final-preview.mp4').exists():continue
            try:request_id=json.loads(response.read_text()).get('requestId')
            except (ValueError,OSError):continue
            if not request_id:continue
            registry[root.name]={'id':root.name,'state':'running','status':'Retomando la solicitud guardada '+request_id}
            def collect_saved(folder=folder,ident=root.name,request_id=request_id,registry=registry):
                try:
                    wait_and_finish(folder,request_id,lambda message:registry[ident].update(status=message))
                    registry[ident].update(state='done',status='Vista final lista · audio original')
                except Exception as e:registry[ident].update(state='error',status=str(e))
                (folder/'status.json').write_text(json.dumps(registry[ident]))
            threading.Thread(target=collect_saved,daemon=True).start()

@app.get('/health')
def health():
    return {'status':'ok','service':'TEIN Video Workflow'}
