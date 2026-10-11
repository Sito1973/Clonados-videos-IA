# Media hosting

Seedance providers (Enhancor or kie.ai) must fetch direct HTTPS media. Replicate preprocessing uses Replicate's `/v1/files` upload API with your token. Provider references use the selected host below. URLs and request bodies stay in each local run; do not share run folders with the clean project.

## kie.ai storage (recommended with SEEDANCE_PROVIDER=kie)

`MEDIA_HOST=kie` with `KIE_API_KEY`. Each file is streamed to kie.ai's File Upload API (`https://kieai.redpandaai.co/api/file-stream-upload`) under a random `genjustsu/<id>` path, and the returned `data.downloadUrl` is passed to the model. kie.ai's backends fetch from their own storage, so the task does not depend on those backends reaching your server (a video-edit draft served from a VPS was observed to fail with `File processing failed, please check the URL`). kie.ai deletes the files after 24 hours, so request the 1080p within that window. The URL is checked once before any generation call.

## Self (signed links from your deployment)

`MEDIA_HOST=self` with `PUBLIC_BASE_URL=https://your-domain`. Nothing is uploaded elsewhere: the app serves the one requested run file at `/media/EXPIRY/SIGNATURE/JOB/FILE`, an HMAC-signed link valid for `MEDIA_LINK_HOURS` (default 168, matching how long a draft can be completed). Links outside `runs/`, expired or altered links return 404; everything else stays behind the login. The uploader fetches each link once to confirm the domain serves it. Set `MEDIA_HOST_FALLBACKS=` empty so a failure never falls back to a public host. The signing key is `APP_SECRET` or a generated `runs/.app-secret`; keep `runs/` on a persistent volume.

## Tmpfiles (default)

`MEDIA_HOST=tmpfiles`. No account key required. Adapter POSTs multipart `file` and `expire=21600` (six hours) to `https://tmpfiles.org/api/v1/upload`, reads the response download page and extracts its actual `/dl/…` link (including host-generated path components), then verifies that it returns nonempty non-HTML bytes. Current documented maximum is 100 MB. Reference: [Tmpfiles API](https://tmpfiles.org/api).

Temporary files can disappear before a long queue finishes. Re-upload only for a deliberate new submission, after checking whether the original request completed. Do not requeue blindly.

## Catbox

Set `MEDIA_HOST=catbox`. Optionally set your own `CATBOX_USERHASH` from your account. Multipart POST to `https://catbox.moe/user/api.php` with `reqtype=fileupload`, optional `userhash`, and `fileToUpload`. The response is a direct URL. Reference: [Catbox API](https://catbox.moe/tools.php).

Both hosts expose uploaded files via public URLs. Use them only for media you intend to share with these services. There is no silent host fallback. For private storage, replace `media_host.upload` with your own signed-URL uploader whose expiry covers the full generation queue. No storage credentials are included.


## Configured automatic fallback

`MEDIA_HOST` selects the primary (`tmpfiles` by default). `MEDIA_HOST_FALLBACKS=catbox` tries Catbox after two failed primary attempts. Set the fallback value to empty to prohibit switching. Both routes provide public URLs; Catbox is persistent. The uploader warns when switching hosts and checks the returned media URL. It never retries model generation. Failed or expired media must be freshly uploaded before a separately authorized generation retry.
