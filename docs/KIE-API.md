# kie.ai integration (Seedance 2.5 and vocal separation)

Request shapes used by this workflow, derived from the [kie.ai documentation](https://docs.kie.ai/llms.txt). Live kie.ai requests were not run while packaging this adapter; confirm account access and current limits before relying on it.

Base URL: `https://api.kie.ai/api/v1/jobs/`
Authentication: `Authorization: Bearer KIE_API_KEY` (server-side only).
kie.ai answers HTTP 200 even for errors: the adapter checks the body `code` (401 key, 402 credits, 422 input, 429 rate limit, 500 provider). A rejected request saves `submission-error.json` and is never retried automatically.

Enable it with `SEEDANCE_PROVIDER=kie` and/or `AUDIO_SEPARATION=kie`, plus `KIE_API_KEY`. Media reaches kie.ai as public HTTPS URLs; with `MEDIA_HOST=self` and `PUBLIC_BASE_URL` they are signed, expiring links served by this app (see [MEDIA-HOSTING.md](MEDIA-HOSTING.md)).

## Draft — POST createTask, model `bytedance/seedance-2-5`

```json
{
  "model": "bytedance/seedance-2-5",
  "callBackUrl": "https://YOUR_DOMAIN/callback/SECRET",
  "input": {
    "prompt": "…creative prompt and the adapter's reference/audio instructions…\nReference order: @Video1 is reference video 1. @Image1 is reference image 1.",
    "reference_video_urls": ["https://YOUR_DOMAIN/media/…/seedance-input.mp4"],
    "reference_image_urls": ["https://YOUR_DOMAIN/media/…/sirio-reference.png"],
    "resolution": "480p",
    "draft": true,
    "aspect_ratio": "adaptive",
    "duration": 8,
    "output_format": "mp4"
  }
}
```

kie.ai has no dedicated edit endpoint. The adapter sends the prepared input (with its embedded +3 vocals) as reference video 1. An identity video becomes reference video 2 (Omni routing), with images after it. `aspect_ratio` is `adaptive` for a single video and the nearest supported ratio for Omni. `duration` is the source length rounded up to whole seconds (4–30). `finish_preview` then drops the generated video past the source end (under 1.25 s, or up to the 4 s minimum for short clips). It never trims, loops or stretches the original audio, and it records `duration-adjustment.json`.

`kie_api.conform_references` enforces the documented reference limits before upload. Non-conforming media is copied into the job's `kie/` folder (scaled, retimed or converted); reviewed artifacts are never modified. Cases that cannot be fixed are refused before any paid call.

- Videos: mp4/mov, 2–30 s each, 30 s total, 24–60 fps, 409,600–927,408 pixels, aspect 0.4–2.5, under 200 MB.
- Images: jpeg/png/webp, 300–6000 px per side, aspect 0.4–2.5, under 30 MB.

The response is `{"code":200,"data":{"taskId":"…"}}`. It is saved as `seedance-response.json` (`{"requestId","provider":"kie","response"}`), so startup and `workflow.py resume` collect it without a new task. `generation.json` records `provider`, `api`, `mode`, `resolution` and `duration`.

## Poll — GET recordInfo?taskId=TASK_ID

`data.state` is `waiting`, `queuing`, `generating`, `success` or `fail`. On success, `data.resultJson` (a JSON string) holds `resultUrls`. The collector polls every 10 s for up to an hour, tolerates up to five consecutive network errors, and saves `provider-status.json`. A `fail` state surfaces `failMsg`. kie.ai keeps result files for 14 days.

## 1080p from the approved draft — model `bytedance/seedance-2-5-draft`

```json
{"model": "bytedance/seedance-2-5-draft", "input": {"draft_task_id": "DRAFT_TASK_ID", "resolution": "1080p", "output_format": "mp4"}}
```

This is a separate billed task that requires explicit approval. Request, response and status are saved in `hd/`. The original soundtrack is restored again.

## Vocal separation — model `ai-music-api/separate-vocals`

```json
{"model": "ai-music-api/separate-vocals", "input": {"audio_url": "https://YOUR_DOMAIN/media/…/original-audio.wav", "type": "separate_vocal"}}
```

The input must be 20 MB or less. Result files are identified by name: `…_Vocals…` becomes `vocals.wav` and `…_Instrumental…` becomes `music.wav`. Both are fitted to the source length. The task is billed per request and saved in `audio-prediction.json`.

## Faces and callbacks

BytePlus documents technical controls that restrict real human faces in Seedance 2.5 references. kie.ai's documentation does not describe its behavior, and there is no `pass_faces` equivalent. Rejections surface as the task's `failMsg`.

With `PUBLIC_BASE_URL`, `callBackUrl` points to `/callback/SECRET`. That route only acknowledges JSON; polling remains authoritative and callbacks never change jobs.
