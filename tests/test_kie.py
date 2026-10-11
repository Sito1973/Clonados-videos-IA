import unittest,tempfile,json
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch,Mock
import av,numpy as np
from PIL import Image
import seedance_bridge,kie_api,audio_workflow

def make_video(path,w,h,seconds,fps=24,audio=True):
 with av.open(str(path),'w') as out:
  v=out.add_stream('libx264',rate=fps);v.width=w;v.height=h;v.pix_fmt='yuv420p'
  a=out.add_stream('aac',rate=44100) if audio else None
  if a:a.layout='stereo'
  for i in range(int(seconds*fps)):
   f=av.VideoFrame.from_ndarray(np.full((h,w,3),i*7%255,dtype=np.uint8),format='rgb24');f.pts=i;f.time_base=Fraction(1,fps)
   for p in v.encode(f):out.mux(p)
  for p in v.encode():out.mux(p)
  if a:
   n=int(seconds*44100);samples=np.tile((np.sin(np.arange(n)*2*np.pi*440/44100)*.1).astype(np.float32),(2,1))
   for i in range(0,n,4096):
    f=av.AudioFrame.from_ndarray(np.ascontiguousarray(samples[:,i:i+4096]),format='fltp',layout='stereo');f.sample_rate=44100;f.pts=i
    for p in a.encode(f):out.mux(p)
   for p in a.encode():out.mux(p)

def mesh_job(p):
 (p/'workflow-mode.json').write_text('{"mode":"face_mesh"}');(p/'audio-manifest.json').write_text('{}')
 make_video(p/'seedance-input.mp4',960,540,4.3);Image.new('RGB',(400,600),'gray').save(p/'sirio-reference.png')

class KieSeedanceTests(unittest.TestCase):
 def test_draft_is_480p_reference_to_video_with_saved_ids(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);mesh_job(p)
   created=Mock(return_value={'code':200,'msg':'success','data':{'taskId':'task-1'}})
   with patch('mask_review.require_approved'),patch('seedance_bridge.seedance_provider',return_value='kie'),patch('seedance_bridge.credential',return_value='test'),patch('seedance_bridge.setting',return_value='https://video.example.com/callback/x'),patch('seedance_bridge.upload',side_effect=lambda path:'https://video.example.com/media/'+Path(path).name),patch('kie_api.create',created):
    self.assertEqual(seedance_bridge.submit(p,'Replace the subject',p/'sirio-reference.png'),'task-1')
   body=created.call_args.args[0];inp=body['input']
   self.assertEqual(body['model'],'bytedance/seedance-2-5');self.assertEqual(body['callBackUrl'],'https://video.example.com/callback/x')
   self.assertTrue(inp['draft']);self.assertEqual(inp['resolution'],'480p');self.assertEqual(inp['duration'],-1);self.assertEqual(inp['aspect_ratio'],'adaptive')
   self.assertEqual(inp['reference_video_urls'],['https://video.example.com/media/seedance-input.mp4'])
   self.assertEqual(inp['reference_image_urls'],['https://video.example.com/media/sirio-reference.png'])
   self.assertIn('Reference order: @Video1 is reference video 1. @Image1 is reference image 1.',inp['prompt']);self.assertIn('Remove all face mesh lines',inp['prompt'])
   self.assertEqual(json.loads((p/'seedance-response.json').read_text())['requestId'],'task-1')
   self.assertEqual(json.loads((p/'generation.json').read_text())['provider'],'kie')

 def test_rejection_is_saved_and_not_treated_as_submitted(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);mesh_job(p)
   reply=Mock(ok=True);reply.json.return_value={'code':402,'msg':'Insufficient credits'}
   with patch('mask_review.require_approved'),patch('seedance_bridge.seedance_provider',return_value='kie'),patch('seedance_bridge.credential',return_value='test'),patch('kie_api.credential',return_value='test'),patch('seedance_bridge.setting',return_value=''),patch('seedance_bridge.upload',return_value='https://video.example.com/media/x'),patch('kie_api.requests.post',return_value=reply) as post:
    with self.assertRaisesRegex(ValueError,'402'):seedance_bridge.submit(p,'Replace',p/'sirio-reference.png')
   self.assertEqual(post.call_count,1);self.assertFalse((p/'seedance-response.json').exists())
   self.assertEqual(json.loads((p/'submission-error.json').read_text())['code'],402)

 def test_hd_completes_the_same_draft(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   for name in ['upload.mp4','final-preview.mp4']:(p/name).write_bytes(b'test')
   (p/'generation.json').write_text(json.dumps({'request_id':'task-1','provider':'kie'}))
   (p/'seedance-request.json').write_text(json.dumps({'model':'bytedance/seedance-2-5','callBackUrl':'https://video.example.com/cb','input':{'draft':True}}))
   with patch('kie_api.create',return_value={'code':200,'data':{'taskId':'hd-1'}}) as created:
    self.assertEqual(seedance_bridge.submit_hd(p),'hd-1')
    self.assertEqual(created.call_args.args[0],{'model':'bytedance/seedance-2-5-draft','input':{'draft_task_id':'task-1','resolution':'1080p','output_format':'mp4'},'callBackUrl':'https://video.example.com/cb'})
    with self.assertRaises(ValueError):seedance_bridge.submit_hd(p)
   self.assertEqual(json.loads((p/'hd/response.json').read_text())['provider'],'kie')

 def test_collection_polls_kie_and_restores(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'seedance-response.json').write_text(json.dumps({'requestId':'task-1','provider':'kie'}))
   states=[{'state':'generating'},{'state':'success','resultJson':json.dumps({'resultUrls':['https://cdn.example.com/out.mp4']})}]
   media=Mock();media.content=b'video'
   with patch('kie_api.record',side_effect=states),patch('kie_api.time.sleep'),patch('seedance_bridge.requests.get',return_value=media) as get,patch('seedance_bridge.finish_preview') as restore:
    seedance_bridge.wait_and_finish(p,'task-1')
   self.assertEqual(get.call_args.args[0],'https://cdn.example.com/out.mp4');self.assertEqual((p/'seedance-result.mp4').read_bytes(),b'video');restore.assert_called_once()

 def test_failed_task_reports_reason(self):
  with patch('kie_api.record',return_value={'state':'fail','failMsg':'reference rejected'}):
   with self.assertRaisesRegex(RuntimeError,'reference rejected'):kie_api.wait('task-1')

class ReferenceTests(unittest.TestCase):
 def test_images_are_numbered_per_character(self):
  self.assertEqual(seedance_bridge.reference_roles(1,0),'@Image1 is the only replacement character reference.')
  self.assertEqual(seedance_bridge.reference_roles(2,2),'@Image1 and @Image2 show the primary character; @Image3 and @Image4 show the secondary character.')
  self.assertEqual(seedance_bridge.reference_roles(0,2),'@Image1 and @Image2 show the secondary character.')

 def test_kie_draft_sends_every_reference_image(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);mesh_job(p)
   for name in ['sirio-2.png','tein-1.png']:Image.new('RGB',(400,600),'gray').save(p/name)
   created=Mock(return_value={'code':200,'data':{'taskId':'t'}})
   with patch('mask_review.require_approved'),patch('seedance_bridge.seedance_provider',return_value='kie'),patch('seedance_bridge.credential',return_value='test'),patch('seedance_bridge.setting',return_value=''),patch('seedance_bridge.upload',side_effect=lambda path:'https://cdn.example.com/'+Path(path).name),patch('kie_api.create',created):
    seedance_bridge.submit(p,'Replace',[p/'sirio-reference.png',p/'sirio-2.png'],[p/'tein-1.png'])
   inp=created.call_args.args[0]['input']
   self.assertEqual(inp['reference_image_urls'],['https://cdn.example.com/sirio-reference.png','https://cdn.example.com/sirio-2.png','https://cdn.example.com/tein-1.png'])
   self.assertIn('@Image1 and @Image2 show the primary character; @Image3 is the secondary character reference.',inp['prompt'])

class RetryTests(unittest.TestCase):
 def test_failed_draft_can_be_sent_again_but_pending_cannot(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);mesh_job(p);(p/'seedance-response.json').write_text(json.dumps({'requestId':'old','provider':'kie'}))
   patches=[patch('mask_review.require_approved'),patch('seedance_bridge.seedance_provider',return_value='kie'),patch('seedance_bridge.credential',return_value='test'),patch('seedance_bridge.setting',return_value=''),patch('seedance_bridge.upload',return_value='https://video.example.com/media/x'),patch('kie_api.create',return_value={'code':200,'data':{'taskId':'new'}})]
   for x in patches:x.start()
   try:
    (p/'provider-status.json').write_text(json.dumps({'state':'generating'}))
    with self.assertRaisesRegex(ValueError,'ya tiene una solicitud'):seedance_bridge.submit(p,'Replace',p/'sirio-reference.png')
    (p/'provider-status.json').write_text(json.dumps({'state':'fail','failMsg':'File processing failed'}))
    self.assertEqual(seedance_bridge.submit(p,'Replace',p/'sirio-reference.png'),'new')
   finally:
    for x in patches:x.stop()
   archived=list((p/'failed-requests').glob('*/seedance-response.json'))
   self.assertEqual(len(archived),1);self.assertEqual(json.loads(archived[0].read_text())['requestId'],'old')
   self.assertFalse((p/'provider-status.json').exists())

class ConformTests(unittest.TestCase):
 def test_valid_source_is_sent_untouched(self):
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'in.mp4';make_video(src,960,540,4)
   self.assertEqual(kie_api.conform_video(src,Path(d)/'out.mp4'),src)
   short=Path(d)/'short.mp4';make_video(short,960,540,3)
   with self.assertRaisesRegex(ValueError,'4 a 30 segundos'):kie_api.conform_video(short,Path(d)/'x.mp4')

 def test_small_low_fps_video_is_scaled_and_retimed(self):
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'in.mov';make_video(src,640,360,4.5,fps=20)
   out=kie_api.conform_video(src,Path(d)/'out.mp4');w,h,fps,seconds=kie_api.video_info(out)
   self.assertNotEqual(out,src);self.assertTrue(kie_api.MIN_PIXELS<=w*h<=kie_api.MAX_PIXELS);self.assertEqual((w%2,h%2),(0,0))
   self.assertEqual(fps,24);self.assertAlmostEqual(seconds,4.5,delta=.1)
   with av.open(str(out)) as c:self.assertTrue(c.streams.audio)

 def test_total_reference_video_limit(self):
  with patch('kie_api.conform_video',side_effect=lambda src,dst:Path(src)),patch('kie_api.video_info',return_value=(960,540,24,20)):
   with tempfile.TemporaryDirectory() as d:
    with self.assertRaisesRegex(ValueError,'30 s'):kie_api.conform_references(d,['source.mp4','identity.mp4'])

 def test_images_are_resized_or_refused(self):
  with tempfile.TemporaryDirectory() as d:
   small=Path(d)/'small.png';Image.new('RGBA',(200,300),(255,0,0,0)).save(small)
   out=kie_api.conform_image(small,Path(d)/'out.jpg')
   with Image.open(out) as im:self.assertGreaterEqual(min(im.size),300);self.assertEqual(im.format,'JPEG')
   wide=Path(d)/'wide.png';Image.new('RGB',(1200,400)).save(wide)
   with self.assertRaisesRegex(ValueError,'proporción'):kie_api.conform_image(wide,Path(d)/'x.jpg')

class KieStorageTests(unittest.TestCase):
 def test_kie_media_host_uploads_to_kie_storage(self):
  import media_host
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'seedance-input.mp4';path.write_bytes(b'\x00\x00\x00 ftypisom')
   uploaded=Mock();uploaded.json.return_value={'success':True,'code':200,'data':{'downloadUrl':'https://tempfile.redpandaai.co/kieai/1/seedance-input.mp4'}}
   media=Mock();media.headers={'Content-Type':'video/mp4'};media.iter_content.return_value=iter([b'\x00\x00\x00 ftypisom'])
   context=Mock();context.__enter__=Mock(return_value=media);context.__exit__=Mock(return_value=False)
   settings=lambda k,d='':{'MEDIA_HOST':'kie','MEDIA_HOST_FALLBACKS':''}.get(k,d)
   with patch('media_host.setting',side_effect=settings),patch('config.credential',return_value='key'),patch('media_host.requests.post',return_value=uploaded) as post,patch('media_host.requests.get',return_value=context):
    self.assertEqual(media_host.upload(path),'https://tempfile.redpandaai.co/kieai/1/seedance-input.mp4')
   self.assertEqual(post.call_args.args[0],'https://kieai.redpandaai.co/api/file-stream-upload')
   self.assertEqual(post.call_args.kwargs['headers'],{'Authorization':'Bearer key'})

 def test_source_just_over_30_seconds_is_cut_for_the_provider(self):
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'in.mp4';make_video(src,320,240,30.05)
   out=kie_api.conform_video(src,Path(d)/'out.mp4')
   self.assertNotEqual(out,src);self.assertLessEqual(kie_api.video_info(out)[3],30.0)

class RotationTests(unittest.TestCase):
 def test_phone_rotation_is_applied(self):
  import shutil,subprocess
  from pipeline import normalize
  if not shutil.which('ffmpeg'):self.skipTest('ffmpeg CLI is needed to write rotation metadata')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);make_video(p/'plain.mp4',320,180,4.5)
   subprocess.run(['ffmpeg','-v','error','-display_rotation:v:0','90','-i',str(p/'plain.mp4'),'-c','copy',str(p/'phone.mp4')],check=True)
   self.assertEqual(kie_api.video_info(p/'phone.mp4')[:2],(180,320))
   meta=normalize(p/'phone.mp4',p/'original.mp4');self.assertEqual((meta['width'],meta['height']),(180,320))
   out=kie_api.conform_video(p/'phone.mp4',p/'out.mp4');w,h,fps,seconds=kie_api.video_info(out)
   self.assertLess(w,h);self.assertTrue(kie_api.MIN_PIXELS<=w*h<=kie_api.MAX_PIXELS)

class KieSeparationTests(unittest.TestCase):
 def test_stems_are_named_and_fitted_to_source(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);make_video(p/'upload.mp4',320,240,2)
   stems={}
   for name,length in [('Vocals',70000),('Instrumental',100000)]:
    audio_workflow.write_audio(p/f'{name}.wav',np.zeros((2,length),dtype=np.float32));stems[name]=(p/f'{name}.wav').read_bytes()
   download=lambda url,timeout:Mock(content=stems['Vocals' if 'Vocals' in url else 'Instrumental'],raise_for_status=lambda:None)
   result={'state':'success','resultJson':json.dumps({'resultUrls':['https://cdn.example.com/a_Instrumental.wav','https://cdn.example.com/a_Vocals.wav']})}
   with patch('config.separation_provider',return_value='kie'),patch('media_host.upload',return_value='https://video.example.com/media/original-audio.wav'),patch('kie_api.create',return_value={'code':200,'data':{'taskId':'sep-1'}}) as created,patch('kie_api.wait',return_value=result),patch('audio_workflow.requests.get',side_effect=download):
    audio_workflow.separate(p/'upload.mp4',p,lambda s:None)
   self.assertEqual(created.call_args.args[0]['input'],{'audio_url':'https://video.example.com/media/original-audio.wav','type':'separate_vocal'})
   original=audio_workflow.read_audio(p/'original-audio.wav').shape[1]
   for name in ['vocals.wav','music.wav']:self.assertEqual(audio_workflow.read_audio(p/name).shape[1],original)
   self.assertEqual(json.loads((p/'audio-manifest.json').read_text())['provider'],'kie')

class DurationTests(unittest.TestCase):
 def check(self,generated_seconds,method):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);make_video(p/'upload.mp4',64,64,1);make_video(p/'generated.mp4',64,64,generated_seconds,audio=False)
   result=audio_workflow.finish_preview(p/'generated.mp4',p)
   def packets(path):
    with av.open(str(path)) as c:return [bytes(x) for x in c.demux(audio=0) if x.dts is not None]
   self.assertEqual(packets(p/'upload.mp4'),packets(result))
   with av.open(str(p/'upload.mp4')) as c:expected=round(c.duration/av.time_base*24)
   with av.open(str(result)) as c:self.assertEqual(sum(1 for _ in c.decode(video=0)),expected)
   self.assertEqual(json.loads((p/'duration-adjustment.json').read_text())['method'],method)
 def test_long_generation_tail_is_trimmed(self):self.check(2,'trim generated video tail')
 def test_short_generation_holds_last_frame(self):self.check(16/24,'hold final frame')
 def test_up_to_one_second_short_is_held(self):self.check(6/24,'hold final frame')

if __name__=='__main__':unittest.main()
