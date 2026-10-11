import os,unittest,shutil,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch,Mock
from fastapi.testclient import TestClient
import auth,app as server,media_host

ENV={'APP_PASSWORD':'correct horse battery','APP_SECRET':'s'*64,'PUBLIC_BASE_URL':'https://video.example.com','KIE_API_KEY':'test','REPLICATE_API_TOKEN':'','SEEDANCE_PROVIDER':'','AUDIO_SEPARATION':''}

class HostedTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,ENV);self.env.start();auth.secret.cache_clear();auth._failures.clear()
  self.client=TestClient(server.app,base_url='https://video.example.com')
  self.job=auth.RUNS/('f'*32);(self.job/'kie').mkdir(parents=True,exist_ok=True)
 def tearDown(self):
  self.env.stop();auth.secret.cache_clear();auth._failures.clear();shutil.rmtree(self.job,ignore_errors=True)
 def login(self):
  self.assertEqual(self.client.post('/login',data={'password':ENV['APP_PASSWORD']},follow_redirects=False).status_code,303)

 def test_everything_but_health_requires_login(self):
  page=self.client.get('/',headers={'accept':'text/html'},follow_redirects=False)
  self.assertEqual((page.status_code,page.headers['location']),(303,'/login'))
  for path in ['/settings','/jobs/'+'f'*32,'/runs/'+'f'*32+'/kie','/docs','/openapi.json']:self.assertEqual(self.client.get(path).status_code,401,path)
  self.assertEqual(self.client.get('/health').status_code,200)

 def test_login_session_and_settings(self):
  self.assertEqual(self.client.post('/login',data={'password':'wrong'}).status_code,401)
  self.login();settings=self.client.get('/settings').json()
  self.assertEqual((settings['seedance_label'],settings['separation'],settings['depth_available'],settings['login']),('kie.ai','kie',False,True))
  self.assertEqual(self.client.get('/runs/.app-secret').status_code,404)

 def test_password_change_ends_sessions(self):
  token=auth.session_token();self.assertTrue(auth.valid_session(token))
  with patch.dict(os.environ,{'APP_PASSWORD':'new password'}):self.assertFalse(auth.valid_session(token))
  self.assertFalse(auth.valid_session(token.split('.')[0]+'.forged'))

 def test_login_attempts_are_limited(self):
  for _ in range(10):self.client.post('/login',data={'password':'wrong'})
  self.assertEqual(self.client.post('/login',data={'password':ENV['APP_PASSWORD']}).status_code,429)

 def test_signed_media_links(self):
  path=self.job/'kie'/'source.mp4';path.write_bytes(b'media')
  url=auth.media_url(path);self.assertTrue(url.startswith('https://video.example.com/media/'))
  route=url.removeprefix('https://video.example.com')
  self.assertEqual(self.client.get(route).content,b'media')
  expires,signature,relative=route.removeprefix('/media/').split('/',2)
  self.assertEqual(self.client.get(f'/media/{expires}/{"0"*64}/{relative}').status_code,404)
  self.assertEqual(self.client.get(auth.media_url(path,hours=-1).removeprefix('https://video.example.com')).status_code,404)
  escape=f'{"f"*32}/../../config.py'
  self.assertIsNone(auth.media_file(expires,auth.sign(f'media|{expires}|{escape}'),escape))
  with self.assertRaises(ValueError):auth.media_url(Path(server.__file__))

 def test_self_media_host_returns_checked_signed_link(self):
  path=self.job/'kie'/'source.mp4';path.write_bytes(b'\x00\x00\x00 ftypisom')
  media=Mock();media.headers={'Content-Type':'video/mp4'};media.iter_content.return_value=iter([b'\x00\x00\x00 ftypisom'])
  context=Mock();context.__enter__=Mock(return_value=media);context.__exit__=Mock(return_value=False)
  settings=lambda k,d='':{'MEDIA_HOST':'self','MEDIA_HOST_FALLBACKS':''}.get(k,d)
  with patch('media_host.setting',side_effect=settings),patch('media_host.requests.get',return_value=context) as get,patch('media_host.requests.post') as post:
   url=media_host.upload(path)
  self.assertTrue(url.startswith('https://video.example.com/media/'));self.assertEqual(get.call_args.args[0],url);post.assert_not_called()

 def test_callback_route_acknowledges_only_its_secret(self):
  self.assertEqual(self.client.post(auth.callback_path(),json={'taskId':'x'}).json(),{'received':True})
  self.assertEqual(self.client.post('/callback/'+'0'*32,json={}).status_code,404)

 def test_depth_modes_refused_without_replicate(self):
  self.login()
  reply=self.client.post('/jobs',files={'video':('clip.mp4',b'video','video/mp4')},data={'mode':'depth'})
  self.assertEqual(reply.status_code,400);self.assertIn('REPLICATE_API_TOKEN',reply.json()['detail'])

 def test_launch_refuses_public_bind_without_password(self):
  env={**os.environ,'APP_HOST':'0.0.0.0','APP_PASSWORD':''}
  result=subprocess.run([sys.executable,'launch.py'],cwd=Path(server.__file__).parent,env=env,capture_output=True,text=True,timeout=60)
  self.assertNotEqual(result.returncode,0);self.assertIn('APP_PASSWORD',result.stderr)

if __name__=='__main__':unittest.main()
