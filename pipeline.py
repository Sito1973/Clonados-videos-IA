from pathlib import Path
from fractions import Fraction
import os, shutil, json, time
import av, numpy as np, requests
from PIL import Image
from gradio_client import Client, handle_file

from config import load_env
load_env()

DEPTH='depth-anything/Video-Depth-Anything'
SAM='linoyts/SAM3-Video'

def upright(frame):
    """RGB image in display orientation: phone videos store a rotation instead of rotated pixels."""
    image=frame.to_image()
    return image.rotate(frame.rotation,expand=True) if frame.rotation%360 else image

def frames(path):
    with av.open(str(path)) as c:
        for f in c.decode(video=0): yield f.to_ndarray(format='rgb24')

def writer(path,w,h):
    c=av.open(str(path),'w');s=c.add_stream('libx264',rate=24);s.width=w;s.height=h;s.pix_fmt='yuv420p';s.options={'crf':'18','preset':'fast'}
    return c,s

def put(c,s,a,i):
    f=av.VideoFrame.from_ndarray(a,format='rgb24');f.pts=i;f.time_base=Fraction(1,24)
    for p in s.encode(f):c.mux(p)

def finish(c,s):
    for p in s.encode():c.mux(p)
    c.close()

def check_clip(path,minimum=0):
    """Refuse a clip the configured provider cannot take, before any billed preparation."""
    try:
        with av.open(str(path)) as c:
            s=c.streams.video[0];seconds=float(c.duration/av.time_base) if c.duration else float(s.duration*s.time_base) if s.duration else 0
    except (av.FFmpegError,IndexError):raise ValueError('El archivo subido no es un video legible.') from None
    if seconds>30.05:raise ValueError(f'Usa un clip de 30 segundos o menos (ahora dura {seconds:.1f} s): recórtalo con Inicio y Fin.')
    if seconds<minimum:raise ValueError(f'Usa un clip de al menos {minimum} segundos: kie.ai edita videos de 4 a 30 segundos.')

VIDEO_SUFFIXES=['.mp4','.mov','.webm','.m4v','.mkv']
AUDIO_COPY=['aac','mp3','alac','ac3','eac3','opus','flac']

def trim(src,dst,start,end):
    """Cut [start,end] seconds: frame-accurate upright video re-encode, original audio packets copied untouched."""
    with av.open(str(src)) as probe:
        vin=probe.streams.video[0];rate=vin.average_rate or Fraction(24,1);width,height=upright(next(probe.decode(vin))).size
        codec=probe.streams.audio[0].codec_context.name if probe.streams.audio else None
    if codec and codec not in AUDIO_COPY:raise ValueError(f'El recorte conserva el audio original sin tocarlo y eso no es posible con audio {codec}. Sube un MP4 o MOV con audio AAC.')
    count=round((end-start)*float(rate))
    if count<1:raise ValueError('La parte seleccionada del video está vacía.')
    with av.open(str(src)) as vsrc,av.open(str(src)) as asrc,av.open(str(dst),'w') as out:
        vin=vsrc.streams.video[0];ain=asrc.streams.audio[0] if codec else None
        vs=out.add_stream('libx264',rate=rate);vs.width=width//2*2;vs.height=height//2*2;vs.pix_fmt='yuv420p';vs.options={'crf':'16','preset':'fast'}
        aout=out.add_stream_from_template(ain) if ain else None
        if start>0:vsrc.seek(int(start/vin.time_base),stream=vin,backward=True)
        decoded=vsrc.decode(vin);current=next(decoded);upcoming=next(decoded,None)
        for i in range(count):
            t=start+i/float(rate)
            while upcoming is not None and float(upcoming.time or 0)<=t+1e-6:current=upcoming;upcoming=next(decoded,None)
            frame=av.VideoFrame.from_image(upright(current).crop((0,0,vs.width,vs.height)));frame.pts=i;frame.time_base=1/rate
            for p in vs.encode(frame):out.mux(p)
        for p in vs.encode():out.mux(p)
        if ain:
            offset,limit=int(round(start/ain.time_base)),int(round(end/ain.time_base))
            if start>0:asrc.seek(offset,stream=ain,backward=True)
            for packet in asrc.demux(ain):
                if packet.pts is None or packet.dts is None or (start>0 and packet.pts<offset):continue
                if packet.pts>=limit:break
                packet.pts-=offset;packet.dts-=offset;packet.stream=aout;out.mux(packet)
    return {'frames':count,'fps':float(rate)}

def trim_source(original,dst,start=0,end=0):
    """Make the job source from the upload: all of it, or only [start,end] seconds. A trimmed upload is kept as is."""
    original,dst=Path(original),Path(dst)
    try:
        with av.open(str(original)) as c:seconds=float(c.duration/av.time_base) if c.duration else 0
    except av.FFmpegError:raise ValueError('El archivo subido no es un video legible.') from None
    if end<=0 or (start<=0.01 and end>=seconds-0.05):
        original.replace(dst);return None
    if not 0<=start<end:raise ValueError('Elige un fin posterior al inicio.')
    end=min(end,seconds);meta=trim(original,dst,start,end)
    (dst.parent/'trim.json').write_text(json.dumps({'source':original.name,'start':round(start,3),'end':round(end,3),'note':'upload.mp4 is the selected part; its audio packets are copied from the source without re-encoding',**meta},indent=2))
    return meta

def normalize(src,dst):
    with av.open(str(src)) as inp:
        vs=inp.streams.video[0];rate=float(vs.average_rate or 24)
        dur=float(vs.duration*vs.time_base) if vs.duration else float(inp.duration/av.time_base)
        if dur>30:raise ValueError('Usa un clip de 30 segundos o menos.')
        it=iter(inp.decode(video=0));cur=next(it);start=float(cur.time or 0);nxt=next(it,None);width,height=upright(cur).size
        scale=min(1,960/max(width,height));w=max(2,int(width*scale)//2*2);h=max(2,int(height*scale)//2*2)
        out,stream=writer(dst,w,h);count=round(dur*24)
        for i in range(count):
            t=i/24+start
            while nxt is not None and float(nxt.time)<=t:cur=nxt;nxt=next(it,None)
            a=np.asarray(upright(cur).resize((w,h),Image.Resampling.LANCZOS));put(out,stream,a,i)
        finish(out,stream)
    return {'frames':count,'fps':24,'width':w,'height':h,'duration':count/24}

def file_path(x):
    if isinstance(x,str):return x
    if isinstance(x,dict):return file_path(x.get('video') or x.get('path'))
    raise ValueError('El proveedor no devolvió un video descargable.')

def depth_hf(src,dst):
    c=Client(DEPTH,token=os.getenv('HF_TOKEN') or None,verbose=False)
    result=c.predict({'video':handle_file(str(src))},max_len=-1,target_fps=24,max_res=960,grayscale=False,api_name='/infer_video_depth')
    shutil.copy(file_path(result[1]),dst)

def depth_replicate(src,dst,model_name="lucataco/depth-anything-video"):
    token=os.getenv('REPLICATE_API_TOKEN')
    if not token:raise ValueError('La profundidad en Replicate necesita REPLICATE_API_TOKEN en el servidor.')
    # Resolve live model schema instead of assuming an input field.
    headers={'Authorization':f'Bearer {token}'}
    r=requests.get('https://api.replicate.com/v1/models/'+model_name,headers=headers,timeout=30);r.raise_for_status();model=r.json();v=model['latest_version'];props=v['openapi_schema']['components']['schemas']['Input']['properties']
    key=next((k for k in props if 'video' in k and props[k].get('format')=='uri'),None)
    if not key:raise ValueError('No se encontró la entrada de video en el esquema del modelo de Replicate.')
    with open(src,'rb') as f:r=requests.post('https://api.replicate.com/v1/files',headers=headers,files={'content':f},timeout=120)
    r.raise_for_status();url=r.json()['urls']['get']
    r=requests.post('https://api.replicate.com/v1/predictions',headers=headers,json={'version':v['id'],'input':{key:url}},timeout=60);r.raise_for_status();j=r.json()
    for _ in range(180):
        if j['status'] in ['succeeded','failed','canceled']:break
        time.sleep(5);r=requests.get(j['urls']['get'],headers=headers,timeout=30);r.raise_for_status();j=r.json()
    if j['status']!='succeeded':raise RuntimeError(str(j.get('error') or j['status']))
    output=j['output'];url=output if isinstance(output,str) else output[0] if isinstance(output,list) else next(v for k,v in output.items() if 'depth' in k)
    r=requests.get(url,timeout=120);r.raise_for_status();Path(dst).write_bytes(r.content)

def grayscale(src,dst,inferno=False):
    out=s=None
    for i,a in enumerate(frames(src)):
        if inferno:
            import cv2
            lut=cv2.applyColorMap(np.arange(256,dtype=np.uint8)[:,None],cv2.COLORMAP_INFERNO)[:,0,::-1].astype(np.float32)
            pixels=a.reshape(-1,3).astype(np.float32);indices=[]
            for chunk in np.array_split(pixels,max(1,len(pixels)//4096)):
                dist=((chunk[:,None,:]-lut[None,:,:])**2).sum(axis=2)
                indices.append(dist.argmin(axis=1).astype(np.uint8))
            gray=np.concatenate(indices).reshape(a.shape[:2]);g=np.repeat(gray[:,:,None],3,axis=2)
        else:g=np.asarray(Image.fromarray(a).convert('L').convert('RGB'))
        if out is None:out,s=writer(dst,g.shape[1],g.shape[0])
        put(out,s,g,i)
    if out is None:raise ValueError('El video de profundidad está vacío.')
    finish(out,s)

def composite(original,depth,seg,directory,audio_source=None):
    raw=Path(seg).is_dir()
    mask_files=sorted(Path(seg).glob('mask_*.png')) if raw else []
    counts=[sum(1 for _ in frames(p)) for p in [original,depth]]+[len(mask_files) if raw else sum(1 for _ in frames(seg))]
    if len(set(counts))!=1:raise ValueError(f'Los fotogramas no coinciden (original/profundidad/SAM): {counts}. No se generó una exportación desalineada.')
    if raw and [int(p.stem.split('_')[-1]) for p in mask_files]!=list(range(counts[0])):raise ValueError('Faltan fotogramas o están desplazados en las máscaras de SAM.')
    outs={};area=0
    for i,(rgb,d,sam) in enumerate(zip(frames(original),frames(depth),(np.asarray(Image.open(p).convert('RGB')) for p in mask_files) if raw else frames(seg))):
        h,w=rgb.shape[:2]
        if raw:
            mask=(sam[:,:,0]>127).astype(np.uint8)*255
        else:
            # Recover SAM's known 50% green overlay against the untouched depth frame.
            base=np.asarray(Image.fromarray(d).resize((sam.shape[1],sam.shape[0]),Image.Resampling.BILINEAR)).astype(np.float32)
            marked=base*0.5+np.array([0,127.5,0],dtype=np.float32)
            observed=sam.astype(np.float32)
            unchanged_error=((observed-base)**2).sum(axis=2)
            marked_error=((observed-marked)**2).sum(axis=2)
            mask=((marked_error+100<unchanged_error)&(observed[:,:,1]-base[:,:,1]>12)).astype(np.uint8)*255
        mask=np.asarray(Image.fromarray(mask).resize((w,h),Image.Resampling.NEAREST));area+=np.count_nonzero(mask)
        d=np.asarray(Image.fromarray(d).resize((w,h),Image.Resampling.BILINEAR));alpha=(mask>127)[:,:,None]
        result=np.where(alpha,d,rgb).astype(np.uint8)
        for name,a in [('mask',np.repeat(mask[:,:,None],3,axis=2)),('composite-silent',result)]:
            if name not in outs:outs[name]=writer(directory/(name+'.mp4'),w,h)
            put(*outs[name],a,i)
    for c,s in outs.values():finish(c,s)
    if area==0:raise ValueError('SAM 3 no encontró al sujeto en el video de profundidad. Prueba con un sujeto simple como "person".')
    final=directory/'composite.mp4'
    with av.open(str(directory/'composite-silent.mp4')) as video, av.open(str(audio_source or original)) as aud, av.open(str(final),'w') as output:
        vs=output.add_stream_from_template(video.streams.video[0])
        astream=output.add_stream_from_template(aud.streams.audio[0]) if aud.streams.audio else None
        for packet in video.demux(video=0):
            if packet.dts is not None:packet.stream=vs;output.mux(packet)
        if aud.streams.audio:
            for packet in aud.demux(audio=0):
                if packet.dts is not None:packet.stream=astream;output.mux(packet)
    return counts[0]

def run(src,directory,prompt='person',provider='replicate',status=print):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    from audio_workflow import separate
    separate(src,directory,status)
    status('Preparando el video · 24 fps');meta=normalize(src,directory/'original.mp4')
    status('Video Depth Anything · generando la profundidad')
    if provider=='huggingface':depth_hf(directory/'original.mp4',directory/'depth-raw.mp4')
    else:depth_replicate(directory/'original.mp4',directory/'depth-raw.mp4', 'chenxwh/depth-any-video' if provider=='replicate-chenxwh' else 'lucataco/depth-anything-video')
    shutil.copyfile(directory/'depth-raw.mp4',directory/'depth.mp4')
    status('SAM 3 · recortando al sujeto')
    from raw_masks import segment_raw,clean_border_fragments
    raw_dir=clean_border_fragments(segment_raw(directory/'depth.mp4',directory,prompt))
    status('Componiendo · sujeto en profundidad sobre el fondo original')
    composite(directory/'original.mp4',directory/'depth.mp4',raw_dir,directory,src)
    from audio_workflow import prepare_seedance_video
    status('Incrustando la voz aislada · +3 semitonos')
    prepare_seedance_video(directory)
    meta.update(prompt=prompt,depth_provider=provider,sam_space=SAM,mask_method='SAM 3 raw PNG masks')
    (directory/'manifest.json').write_text(json.dumps(meta,indent=2));status('Completo')
