from pathlib import Path
import json,requests,re,time,concurrent.futures
from config import credential, setting, seedance_provider
from media_host import upload
from audio_workflow import finish_preview

def failed_request(folder):
    """True only when the provider reported a final failure, so a new request cannot duplicate a live or finished one."""
    try:saved=json.loads((Path(folder)/'provider-status.json').read_text())
    except (OSError,ValueError):return False
    return saved.get('state')=='fail' or saved.get('status') in ['FAILED','CANCELED']

def finished_request(folder):
    """True once the provider reported a final state (done or failed): nothing billed is still running."""
    try:saved=json.loads((Path(folder)/'provider-status.json').read_text())
    except (OSError,ValueError):return False
    return saved.get('state') in ['success','fail'] or saved.get('status') in ['COMPLETED','FAILED','CANCELED']

def generation_state(folder):
    """none, failed, pending (draft or 1080p still running) or done."""
    folder=Path(folder)
    if not (folder/'seedance-response.json').exists():return 'none'
    if failed_request(folder):return 'failed'
    if not finished_request(folder) or ((folder/'hd/response.json').exists() and not finished_request(folder/'hd')):return 'pending'
    return 'done'

def archive_generation(folder):
    """Move a finished draft, its 1080p and its references to versions/ so the same prepared job can generate again."""
    folder=Path(folder);target=folder/'versions'/time.strftime('%Y%m%d-%H%M%S');target.mkdir(parents=True,exist_ok=True)
    for name in ['seedance-request.json','seedance-response.json','provider-status.json','generation.json','submission-error.json','seedance-result.mp4','seedance-duration-aligned.mp4','duration-adjustment.json','final-preview.mp4','references.json','hd']:
        if (folder/name).exists():(folder/name).rename(target/name)
    import shutil
    for reference in [*folder.glob('sirio-reference*'),*folder.glob('tein-reference*')]:shutil.copy2(reference,target/reference.name)
    return target

def archive_failed(folder,names):
    """Keep a failed request's files for the record and clear the way for a new one."""
    target=Path(folder)/'failed-requests'/time.strftime('%Y%m%d-%H%M%S');target.mkdir(parents=True,exist_ok=True)
    for name in names:
        if (Path(folder)/name).exists():(Path(folder)/name).rename(target/name)

def reference_roles(primary,secondary):
    """Which @Image tags show which character, numbered in upload order: primary images first."""
    def tags(start,count):
        names=[f'@Image{start+i}' for i in range(count)]
        return names[0] if count==1 else ', '.join(names[:-1])+' and '+names[-1]
    roles=[]
    if primary:roles.append(tags(1,primary)+(' is the only replacement character reference' if primary==1 else ' show the only replacement character') if not secondary else tags(1,primary)+(' is the primary character reference' if primary==1 else ' show the primary character'))
    if secondary:roles.append(tags(primary+1,secondary)+(' is the secondary character reference' if secondary==1 else ' show the secondary character'))
    return '; '.join(roles)+'.' if roles else ''

def submit(folder,prompt,sirio,tein=None,status=print):
    """sirio: one identity video or one or more images; tein: optional images of a second character."""
    folder=Path(folder)
    primary=[Path(x) for x in (sirio if isinstance(sirio,(list,tuple)) else [sirio])]
    secondary=[Path(x) for x in (tein if isinstance(tein,(list,tuple)) else [tein] if tein else [])]
    video_identity=len(primary)==1 and primary[0].suffix.lower() in ['.mp4','.mov','.webm']
    from mask_review import require_approved, is_mesh
    require_approved(folder)
    if (folder/'seedance-response.json').exists():
        if not failed_request(folder):raise ValueError('Este trabajo ya tiene una solicitud en curso o terminada. Para una variación, prepara un trabajo nuevo.')
        archive_failed(folder,['seedance-request.json','seedance-response.json','provider-status.json','generation.json','submission-error.json'])
    service=seedance_provider()
    if service not in ['enhancor','kie']:raise ValueError('SEEDANCE_PROVIDER debe ser enhancor o kie.')
    key=credential('KIE_API_KEY' if service=='kie' else 'ENHANCOR_API_KEY')
    if service=='enhancor' and not setting('ENHANCOR_WEBHOOK_URL'):raise ValueError('Enhancor necesita un webhook: configura PUBLIC_BASE_URL o ENHANCOR_WEBHOOK_URL, o instala cloudflared y reinicia.')
    if not (folder/'audio-manifest.json').exists():raise ValueError('La separación de voz debe terminar antes de enviar.')
    from audio_workflow import prepare_seedance_video
    status('Subiendo la voz aislada +3 semitonos e incrustándola en el video')
    prepared=folder/'seedance-input.mp4' if is_mesh(folder) else prepare_seedance_video(folder)
    files=[prepared]+primary+secondary
    if service=='kie':
        from kie_api import conform_references
        files=conform_references(folder,files)
    status('Subiendo el video y las referencias de los personajes')
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:urls=list(pool.map(upload,files))
    p={'resolution':'1080p','output_format':'mp4','pass_faces':True,'webhook_url':setting('ENHANCOR_WEBHOOK_URL','')}
    p.update(mode='edit',is_draft=True,aspect_ratio='adaptive',videos=[urls[0]],audios=[],images=urls[1:],prompt=prompt.replace('@Audio1', 'the audio embedded in @Video1')+'\n@Video1 contains the colored-depth composite and embedded isolated vocals pitched up three semitones, without the music bed; use its embedded audio for performance and lip synchronization. '+reference_roles(len(primary),len(secondary)));p.pop('duration',None)
    if video_identity:
        import av
        with av.open(str(prepared)) as source:
            seconds=float(source.duration/av.time_base)
            stream=source.streams.video[0]
            ratio=stream.width/stream.height
        ratios={'16:9':16/9,'9:16':9/16,'1:1':1,'4:3':4/3,'3:4':3/4,'21:9':21/9}
        p.update(mode='multi_reference',duration=str(max(4,min(30,round(seconds)))),aspect_ratio=min(ratios,key=lambda r:abs(ratios[r]-ratio)))
        p['videos']=[urls[0],urls[1]]
        p['images']=urls[2:]
        p['prompt']=prompt+'\n@Video1 is the source edit and contains isolated vocals pitched +3 semitones. @Video2 is only the primary character visual identity reference. Use only @Video1 audio for speech timing.'+(' '+reference_roles(0,len(secondary)) if secondary else '')
    if is_mesh(folder):
        mesh_mode=json.loads((folder/'workflow-mode.json').read_text()).get('mode')
        description='depth composite with face mesh overlay' if mesh_mode=='depth_mesh' else 'original video with face mesh overlay'
        p['prompt']=p['prompt'].replace('colored-depth composite',description)+' Remove all face mesh lines and any depth colors from the output; use them only for expression and lip motion guidance. Replace the entire original identity including hair. Preserve the exact embedded dialogue and timing.'
    p['creative_prompt']=prompt
    if service=='kie':return _queue_kie(folder,p,prepared,status)
    creative=p.pop('creative_prompt');(folder/'seedance-request.json').write_text(json.dumps(p,indent=2));h={'x-api-key':key};base=setting('ENHANCOR_BASE_URL','https://apireq.enhancor.ai/api/seedance2.5/v1').rstrip('/')+'/'
    r=requests.post(base+'queue',headers=h,json=p,timeout=90);
    if not r.ok:
        (folder/'submission-error.json').write_text(r.text)
        raise ValueError(f'Enhancor rechazó el envío ({r.status_code}): {r.text[:500]}')
    j=r.json();(folder/'seedance-response.json').write_text(json.dumps(j))
    ident=j.get('requestId')
    if not ident:raise ValueError('Seedance no devolvió un ID de solicitud.')
    (folder/'generation.json').write_text(json.dumps({'request_id':ident,'prompt':p['prompt'],'creative_prompt':creative,'video':'seedance-result.mp4','mode':p['mode']}))
    status('Borrador enviado a Seedance: '+ident)
    return ident

def _queue_kie(folder,p,prepared,status):
    """kie.ai has no edit endpoint: multimodal reference-to-video as a 480p draft, with the same references and prompt."""
    from kie_api import create,Rejected
    order=' '.join([f'@Video{i+1} is reference video {i+1}.' for i in range(len(p['videos']))]+[f'@Image{i+1} is reference image {i+1}.' for i in range(len(p['images']))])
    # Replacing a subject is classified as video editing, which requires duration -1: the output follows the source length.
    inp={'prompt':p['prompt']+'\nReference order: '+order,'reference_video_urls':p['videos'],'resolution':'480p','draft':True,'aspect_ratio':p['aspect_ratio'],'duration':-1,'output_format':'mp4'}
    if p['images']:inp['reference_image_urls']=p['images']
    body={'model':'bytedance/seedance-2-5','input':inp}
    if p['webhook_url'].startswith('https://'):body['callBackUrl']=p['webhook_url']
    (folder/'seedance-request.json').write_text(json.dumps(body,indent=2))
    try:j=create(body)
    except Rejected as e:
        (folder/'submission-error.json').write_text(json.dumps(e.body))
        raise
    ident=j['data']['taskId']
    (folder/'seedance-response.json').write_text(json.dumps({'requestId':ident,'provider':'kie','response':j}))
    (folder/'generation.json').write_text(json.dumps({'request_id':ident,'prompt':inp['prompt'],'creative_prompt':p['creative_prompt'],'video':'seedance-result.mp4','mode':p['mode'],'provider':'kie','api':'kie.ai bytedance/seedance-2-5 multimodal reference-to-video','resolution':'480p','duration':inp['duration']}))
    status('Borrador enviado a kie.ai: '+ident)
    return ident

def _saved_provider(folder):
    for name in ['seedance-response.json','response.json']:
        try:return json.loads((Path(folder)/name).read_text()).get('provider','enhancor')
        except (OSError,ValueError):pass
    return 'enhancor'

def _download_and_restore(folder,url,status):
    r=requests.get(url,timeout=180);r.raise_for_status();(folder/'seedance-result.mp4').write_bytes(r.content)
    status('Restaurando el audio original completo · se descarta el audio generado')
    finish_preview(folder/'seedance-result.mp4',folder)

def wait_and_finish(folder,request_id,status=print):
    folder=Path(folder)
    if _saved_provider(folder)=='kie':
        from kie_api import wait,result_urls
        urls=result_urls(wait(request_id,save=lambda data:(folder/'provider-status.json').write_text(json.dumps(data,indent=2))))
        if not urls:raise ValueError('Falta la URL del resultado de Seedance.')
        return _download_and_restore(folder,urls[0],status)
    key=credential('ENHANCOR_API_KEY')
    for _ in range(360):
        r=requests.post(setting('ENHANCOR_BASE_URL','https://apireq.enhancor.ai/api/seedance2.5/v1').rstrip('/')+'/status',headers={'x-api-key':key},json={'request_id':request_id},timeout=30);r.raise_for_status();j=r.json()
        (folder/'provider-status.json').write_text(json.dumps(j,indent=2))
        if j.get('status')=='COMPLETED':
            url=j.get('result')
            if not isinstance(url,str) or not url.startswith('https://'):raise ValueError('Falta la URL del resultado de Seedance.')
            return _download_and_restore(folder,url,status)
        if j.get('status') in ['FAILED','CANCELED']:raise RuntimeError('Seedance falló: '+str(j.get('error') or j.get('status')))
        time.sleep(10)
    raise TimeoutError('Seedance sigue en proceso. El ID quedó guardado: retoma la descarga, no lo reenvíes.')


def submit_hd(folder):
    """Complete the accepted draft; the provider reuses its original parameters."""
    folder=Path(folder)
    if (folder/'hd/response.json').exists():
        if not failed_request(folder/'hd'):raise ValueError('El 1080p ya se envió. Retoma su solicitud guardada.')
        archive_failed(folder/'hd',['request.json','response.json','provider-status.json','submission-error.json'])
    generation=json.loads((folder/'generation.json').read_text())
    original=json.loads((folder/'seedance-request.json').read_text())
    kie=generation.get('provider')=='kie'
    if not (original.get('input',{}).get('draft') if kie else original.get('is_draft')) or not (folder/'final-preview.mp4').exists():
        raise ValueError('Necesitas un borrador terminado antes de generar el 1080p.')
    if kie:
        payload={'model':'bytedance/seedance-2-5-draft','input':{'draft_task_id':generation['request_id'],'resolution':'1080p','output_format':'mp4'}}
        if original.get('callBackUrl'):payload['callBackUrl']=original['callBackUrl']
    else:
        payload={'draft_id':generation['request_id'],'webhook_url':original['webhook_url']}
        key=credential('ENHANCOR_API_KEY')
    hd=folder/'hd';hd.mkdir(exist_ok=True)
    # Keep the draft intact and use the exact source audio for the HD export.
    source=hd/'upload.mp4'
    if not source.exists():__import__('shutil').copy2(folder/'upload.mp4',source)
    (hd/'request.json').write_text(json.dumps(payload,indent=2))
    if kie:
        from kie_api import create,Rejected
        try:data=create(payload)
        except Rejected as e:
            (hd/'submission-error.json').write_text(json.dumps(e.body))
            raise
        ident=data['data']['taskId']
        (hd/'response.json').write_text(json.dumps({'requestId':ident,'provider':'kie','response':data}))
        return ident
    response=requests.post(setting('ENHANCOR_BASE_URL','https://apireq.enhancor.ai/api/seedance2.5/v1').rstrip('/')+'/queue',headers={'x-api-key':key},json=payload,timeout=90)
    response.raise_for_status();data=response.json()
    (hd/'response.json').write_text(json.dumps(data))
    ident=data.get('requestId')
    if not ident:raise ValueError('Enhancor no devolvió un ID de solicitud.')
    return ident
