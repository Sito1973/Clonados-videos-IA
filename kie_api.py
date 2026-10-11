"""kie.ai market API (createTask / recordInfo) and copies of media conformed to its Seedance 2.5 reference limits."""
import json,math,time
from fractions import Fraction
from pathlib import Path
import av,numpy as np,requests
from PIL import Image
from config import credential

BASE='https://api.kie.ai/api/v1/jobs/'
MIN_PIXELS,MAX_PIXELS,TARGET_PIXELS=409_600,927_408,921_600
VIDEO_TYPES=['.mp4','.mov']

class Rejected(ValueError):
    def __init__(self,body):
        self.body=body
        super().__init__(f"kie.ai rejected the task ({body.get('code')}): {body.get('msg')}")

def _headers():return {'Authorization':'Bearer '+credential('KIE_API_KEY')}

def create(body):
    """One billed createTask call; never retried. kie.ai answers HTTP 200 with an error code too."""
    r=requests.post(BASE+'createTask',headers=_headers(),json=body,timeout=90)
    if not r.ok:raise Rejected({'code':r.status_code,'msg':r.text[:500]})
    j=r.json()
    if j.get('code')!=200 or not (j.get('data') or {}).get('taskId'):raise Rejected(j)
    return j

def record(task_id):
    r=requests.get(BASE+'recordInfo',headers=_headers(),params={'taskId':task_id},timeout=30);r.raise_for_status();j=r.json()
    if j.get('code')!=200 or not isinstance(j.get('data'),dict):raise RuntimeError(f"kie.ai status error ({j.get('code')}): {j.get('msg')}")
    return j['data']

def result_urls(data):
    raw=data.get('resultJson') or '{}'
    try:parsed=json.loads(raw) if isinstance(raw,str) else raw
    except ValueError:parsed={}
    return [u for u in (parsed or {}).get('resultUrls') or [] if isinstance(u,str) and u.startswith('https://')]

def wait(task_id,save=None,attempts=360,delay=10):
    """Poll until success or failure. Polling only reads; it never creates another billed task."""
    errors=0
    for _ in range(attempts):
        try:data=record(task_id);errors=0
        except requests.RequestException:
            errors+=1
            if errors>=5:raise
            time.sleep(delay);continue
        if save:save(data)
        if data.get('state')=='success':return data
        if data.get('state')=='fail':raise RuntimeError('kie.ai task failed: '+str(data.get('failMsg') or data.get('failCode') or 'no reason given'))
        time.sleep(delay)
    raise TimeoutError('kie.ai task is still pending. Task ID saved; resume collection, do not resubmit automatically.')

def video_info(path):
    """Display width and height (after any stored rotation), frame rate and seconds."""
    with av.open(str(path)) as c:
        s=c.streams.video[0]
        seconds=float(s.duration*s.time_base) if s.duration else float(c.duration/av.time_base)
        w,h=(s.height,s.width) if next(c.decode(video=0)).rotation%180 else (s.width,s.height)
        return w,h,float(s.average_rate or 0),seconds

def conform_video(src,dst):
    """src when it already meets the reference-video limits; otherwise an H.264/AAC copy scaled and retimed to meet them."""
    src=Path(src);w,h,fps,seconds=video_info(src)
    if not 2<=seconds<=30.05:raise ValueError(f'kie.ai reference videos must last 2–30 seconds ({src.name}: {seconds:.1f} s).')
    if not .4<=w/h<=2.5:raise ValueError(f'kie.ai reference videos need an aspect ratio between 0.4 and 2.5 ({src.name}).')
    if MIN_PIXELS<=w*h<=MAX_PIXELS and 24<=fps<=60 and src.suffix.lower() in VIDEO_TYPES and src.stat().st_size<200_000_000:return src
    if w*h<MIN_PIXELS:
        scale=math.sqrt(MIN_PIXELS/(w*h));width,height=math.ceil(w*scale/2)*2,math.ceil(h*scale/2)*2
    elif w*h>MAX_PIXELS:
        scale=math.sqrt(TARGET_PIXELS/(w*h));width,height=int(w*scale/2)*2,int(h*scale/2)*2
    else:width,height=w//2*2,h//2*2
    rate=Fraction(fps).limit_denominator(1001) if 24<=fps<=60 else Fraction(24)
    count=max(1,int(seconds*rate))
    from audio_workflow import read_audio
    from pipeline import upright
    with av.open(str(src)) as inp,av.open(str(dst),'w') as out:
        vs=out.add_stream('libx264',rate=rate);vs.width=width;vs.height=height;vs.pix_fmt='yuv420p';vs.options={'crf':'18'}
        audio=out.add_stream('aac',rate=44100) if inp.streams.audio else None
        if audio:audio.layout='stereo'
        frames=inp.decode(video=0);current=next(frames);start=float(current.time or 0);upcoming=next(frames,None)
        for i in range(count):
            t=start+i/rate
            while upcoming is not None and float(upcoming.time)<=t:current=upcoming;upcoming=next(frames,None)
            image=upright(current)
            if image.size!=(width,height):image=image.resize((width,height),Image.Resampling.LANCZOS)
            frame=av.VideoFrame.from_image(image);frame.pts=i;frame.time_base=1/rate
            for p in vs.encode(frame):out.mux(p)
        for p in vs.encode():out.mux(p)
        if audio:
            samples=read_audio(src)[:,:round(count/rate*44100)]
            for i in range(0,samples.shape[1],4096):
                f=av.AudioFrame.from_ndarray(np.ascontiguousarray(samples[:,i:i+4096]),format='fltp',layout='stereo');f.sample_rate=44100;f.pts=i
                for p in audio.encode(f):out.mux(p)
            for p in audio.encode():out.mux(p)
    return Path(dst)

def conform_image(src,dst):
    """src when it already meets the reference-image limits; otherwise a resized JPEG copy."""
    src=Path(src)
    with Image.open(src) as im:
        w,h=im.size
        if not .4<=w/h<=2.5:raise ValueError(f'kie.ai reference images need an aspect ratio between 0.4 and 2.5 ({src.name}).')
        scale=max(1,300/min(w,h))*min(1,6000/max(w,h))
        if scale==1 and src.stat().st_size<30_000_000 and src.suffix.lower() in ['.png','.jpg','.jpeg','.webp']:return src
        image=im.convert('RGBA');flat=Image.new('RGB',image.size,'white');flat.paste(image,mask=image.getchannel('A'))
        flat.resize((round(w*scale),round(h*scale)),Image.Resampling.LANCZOS).save(dst,'JPEG',quality=92)
    return Path(dst)

def conform_references(folder,files):
    """Provider copies live in the job's kie/ folder; the reviewed artifacts stay untouched."""
    out=Path(folder)/'kie';out.mkdir(exist_ok=True);result=[]
    for i,path in enumerate(map(Path,files)):
        name='source' if i==0 else f'reference-{i}'
        result.append(conform_video(path,out/(name+'.mp4')) if path.suffix.lower() in VIDEO_TYPES+['.webm'] else conform_image(path,out/(name+'.jpg')))
    total=sum(video_info(p)[3] for p in result if p.suffix.lower() in VIDEO_TYPES)
    if total>30.05:raise ValueError(f'kie.ai accepts at most 30 s of reference video in total; these add up to {total:.1f} s. Use a shorter identity video.')
    return result
