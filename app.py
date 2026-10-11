from pathlib import Path
import threading,uuid,shutil,json,hmac
from fastapi import FastAPI,UploadFile,File,Form,HTTPException,Request
from fastapi.responses import FileResponse,HTMLResponse,JSONResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from pipeline import run
import auth
ROOT=Path(__file__).resolve().parent
RUNS=ROOT/'runs';RUNS.mkdir(exist_ok=True)
if auth.exposed() and not auth.password():raise RuntimeError('Set APP_PASSWORD before serving beyond localhost.')
app=FastAPI();jobs={}

@app.middleware('http')
async def require_login(request:Request,call_next):
    path=request.url.path
    if '/.' in path:return JSONResponse({'detail':'Not Found'},404)
    if not auth.password() or path in ['/health','/login'] or path.startswith(('/callback/','/media/')) or auth.valid_session(request.cookies.get(auth.COOKIE,'')):return await call_next(request)
    if request.method=='GET' and 'text/html' in request.headers.get('accept',''):return RedirectResponse('/login',303)
    return JSONResponse({'detail':'Sign in required.'},401)

LOGIN='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · Genjustsu</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#fafafb;color:#2b2f3a;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}form{width:min(360px,calc(100vw - 32px));padding:28px;background:#fff;border:1px solid #ececef;border-radius:16px;box-shadow:0 12px 32px -12px #16181f14}h1{font-size:22px;margin:0 0 18px}input{width:100%;box-sizing:border-box;font:inherit;padding:12px;border:1px solid #dfe2e8;border-radius:8px;margin-bottom:14px}button{width:100%;font:inherit;font-weight:650;padding:12px;border:0;border-radius:8px;background:#1f18c0;color:#fff;cursor:pointer}p{color:#b42318;font-size:13px;margin:0 0 12px}</style></head><body><form method="post" action="/login"><h1>Genjustsu</h1><!--error--><input type="password" name="password" placeholder="Password" aria-label="Password" autocomplete="current-password" required autofocus><button>Sign in</button></form></body></html>'''
def login_page(error='',code=200):return HTMLResponse(LOGIN.replace('<!--error-->',f'<p>{error}</p>' if error else ''),code)

@app.get('/login')
def show_login():return login_page()

@app.post('/login')
def login(request:Request,password:str=Form('')):
    if not auth.password():return RedirectResponse('/',303)
    client=request.client.host if request.client else 'unknown'
    if not auth.login_allowed(client):return login_page('Too many attempts. Try again in 15 minutes.',429)
    if not auth.check_password(password):
        auth.login_failed(client)
        return login_page('Incorrect password.',401)
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

@app.get('/media/{expires}/{signature}/{relative:path}')
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
def create(video:UploadFile=File(...),prompt:str=Form('person'),provider:str=Form('replicate'),mode:str=Form('depth')):
    if mode not in ['depth','face_mesh','depth_mesh']:raise HTTPException(400,'Unknown workflow mode')
    if provider not in ['huggingface','replicate','replicate-chenxwh']:raise HTTPException(400,'Unknown provider')
    from config import require_mode
    try:require_mode(mode)
    except ValueError as e:raise HTTPException(400,str(e))
    ident=uuid.uuid4().hex;folder=RUNS/ident;folder.mkdir();src=folder/'upload.mp4'
    with src.open('wb') as out:shutil.copyfileobj(video.file,out)
    jobs[ident]={'state':'running','status':'Starting','id':ident}
    def work():
        try:
            if mode=='face_mesh':
                from face_mesh import run as run_mesh
                run_mesh(src,folder,lambda s:jobs[ident].update(status=s))
            elif mode=='depth_mesh':
                from face_mesh import run_depth
                run_depth(src,folder,prompt,provider,lambda s:jobs[ident].update(status=s))
            else:run(src,folder,prompt,provider,lambda s:jobs[ident].update(status=s))
            jobs[ident].update(state='done',status='Preparation complete · input review required')
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
def seedance(ident:str,prompt:str=Form(...),sirio:UploadFile=File(...),tein:UploadFile|None=File(None)):
    if len(ident)!=32 or not all(c in '0123456789abcdef' for c in ident):raise HTTPException(404)
    folder=RUNS/ident
    if not (folder/'audio-manifest.json').exists():raise HTTPException(400,'Audio separation must complete first.')
    from mask_review import require_approved
    try:require_approved(folder)
    except ValueError as e:raise HTTPException(409,str(e))
    paths=[]
    for name,upload in [('sirio-reference',sirio),('tein-reference',tein)]:
        if upload is None:
            paths.append(None)
            continue
        suffix=Path(upload.filename or '').suffix.lower()
        if suffix not in (['.png','.jpg','.jpeg','.webp','.mp4','.mov','.webm'] if name=='sirio-reference' else ['.png','.jpg','.jpeg','.webp']):raise HTTPException(400,'Use an image or a Sirio reference video.')
        path=folder/(name+suffix)
        with path.open('wb') as f:shutil.copyfileobj(upload.file,f)
        paths.append(path)
    from seedance_bridge import submit
    try:request_id=submit(folder,prompt,*paths)
    except Exception as e:raise HTTPException(502,str(e))
    jobs[ident]={'id':ident,'state':'running','status':'Seedance rendering: '+request_id}
    def collect():
        from seedance_bridge import wait_and_finish
        try:
            wait_and_finish(folder,request_id,lambda s:jobs[ident].update(status=s))
            jobs[ident].update(state='done',status='Final preview ready · entire original source audio',final_url='/runs/'+ident+'/final-preview.mp4')
        except Exception as e:jobs[ident].update(state='error',status=str(e))
        (folder/'status.json').write_text(json.dumps(jobs[ident]))
    threading.Thread(target=collect,daemon=True).start()
    return {'request_id':request_id,'id':ident,'state':'running','status':jobs[ident]['status']}

@app.post('/jobs/{ident}/finish')
def finish(ident:str,video:UploadFile=File(...)):
    if len(ident)!=32 or not all(c in '0123456789abcdef' for c in ident):raise HTTPException(404)
    folder=RUNS/ident
    if not (folder/'upload.mp4').exists():raise HTTPException(400,'Original source is required.')
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
    return {'state':'idle','status':'Approve your draft to generate 1080p.'}

@app.post('/jobs/{ident}/hd')
def create_hd(ident:str):
    folder=hd_folder(ident)
    with hd_lock:
        previous=get_hd(ident)
        if previous['state'] in ['running','done']:return previous
        if not (folder/'final-preview.mp4').exists():raise HTTPException(400,'Complete and review a draft first.')
        hd=folder/'hd';hd.mkdir(exist_ok=True)
        hd_jobs[ident]={'state':'running','status':'Submitting approved draft for 1080p','id':ident}
        def save(): (hd/'status.json').write_text(json.dumps(hd_jobs[ident]))
        save()
    def work_hd():
        from seedance_bridge import submit_hd,wait_and_finish
        try:
            request_id=submit_hd(folder)
            hd_jobs[ident].update(request_id=request_id,status='Generating 1080p through Enhancor');save()
            wait_and_finish(hd,request_id,lambda s:hd_jobs[ident].update(status=s))
            hd_jobs[ident].update(state='done',status='1080p ready · original source audio restored')
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
            registry[root.name]={'id':root.name,'state':'running','status':'Resuming saved request '+request_id}
            def collect_saved(folder=folder,ident=root.name,request_id=request_id,registry=registry):
                try:
                    wait_and_finish(folder,request_id,lambda message:registry[ident].update(status=message))
                    registry[ident].update(state='done',status='Final preview ready · original source audio')
                except Exception as e:registry[ident].update(state='error',status=str(e))
                (folder/'status.json').write_text(json.dumps(registry[ident]))
            threading.Thread(target=collect_saved,daemon=True).start()

@app.get('/health')
def health():
    return {'status':'ok','service':'TEIN Video Workflow'}
