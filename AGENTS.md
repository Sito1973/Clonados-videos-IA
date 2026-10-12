# Independent video workflow

For installation/onboarding requests, follow `docs/SETUP-AGENT.md` first. Do not run paid inference during setup. Distinguish a running UI from a generation-ready configuration.

Carry authorized jobs through preparation, quality review, generation and delivery without stopping after each processing stage. Ask only for missing creative choices or submission approval not already given. Never automatically purchase an HD upgrade: the user approves that step.

1. Preserve the source file and complete original audio. When the user trims the upload, `upload-original.*` stays untouched and `upload.mp4` is the selected part with its audio packets copied (not re-encoded); the final export restores that part's complete original audio.
2. Isolate vocals with the configured separator (Demucs on Replicate by default; kie.ai separate-vocals when AUDIO_SEPARATION=kie), pitch +3 semitones with timing preserved, and embed them in the prepared composite. Do not attach separate audio to Seedance.
3. Generate depth with lucataco/depth-anything-video and retain its exact colors.
4. Obtain raw SAM 3 masks. If depth segmentation misses the subject or includes background, retry using original RGB detail and composite the original colored depth through those masks. Record this fallback.
5. Mandatory visual mask QA before EVERY generation: review the full clip, including all cuts, subject extremities, hair, hands, clothing, background spill, holes, alignment and flicker. Automated checks and sampled contact sheets alone do not establish full visual approval. Repair failures independently within the authorized task, then review again. Never approve a known-bad mask. Use mask_review to bind approval to current artifacts. User approval of the current mask may be recorded as such, without falsely claiming agent full-clip review.
6. Match the references and prompt to the actual subjects. A single Sirio replacement uses only Sirio's reference; never carry over the previous two-person mapping. Each character may have several images (e.g. face and full body; the UI offers up to three per character): primary images are @Image1 onward, then the secondary character's; a primary identity video is @Video2. Use those tags in the prompt.
7. Show the exact creative prompt before submission. Existing explicit authorization to submit is sufficient; do not ask again unnecessarily. Submit as a draft through the configured provider: Enhancor Seedance Edit by default, or kie.ai Seedance 2.5 reference-to-video (480p draft) when SEEDANCE_PROVIDER=kie.
8. Retrieve results and restore the entire untouched original source audio, discarding generated audio. Preserve draft artifacts. A new version with other references or prompt may reuse the same prepared, approved job once its draft and any 1080p have finished: on explicit confirmation the previous generation (draft, 1080p, references, request IDs) moves to `versions/<timestamp>/`. Never while a draft or 1080p is still generating. Report failures honestly; never claim submission or completion without an actual result.
9. On explicit approval, complete the draft at 1080p (Enhancor: draft_id and webhook_url; kie.ai: bytedance/seedance-2-5-draft with draft_task_id), then restore original audio again.
10. Show the final preview and download. Keep status persisted and UI preview selection stable. Never expose credentials.

## Reference-mode routing (required)
Enhancor Edit accepts exactly one video: the prepared source. If the replacement identity reference is a video, use multi_reference (Omni), with prepared source @Video1 and identity reference @Video2. Use the identity clip for appearance only unless the user explicitly asks to use its voice or performance. Do not convert that reference to pictures without permission. For image references, default to Edit. Show the actual mode in the UI and saved generation metadata. Omni requires duration and aspect ratio; use the source framing and nearest supported duration. Preserve the full source audio in the final export and disclose any necessary short last-frame hold or generated-tail trim.

With SEEDANCE_PROVIDER=kie there is no Edit endpoint: the prepared source is reference video 1 (@Video1), a video identity reference is @Video2, and images follow as @Image1 onward. kie.ai classifies subject replacement as video editing: it requires duration -1 (the output follows the source length) and reference videos of 4–30 s, at most 30 s in total; the adapter conforms references to its limits in the job's kie/ folder without altering reviewed artifacts. Do not claim face pass-through on kie.ai; report face-policy rejections as returned.

## Hosted deployment
For VPS/EasyPanel installs follow `docs/DEPLOY-EASYPANEL.md`. Keys live only in the host's environment settings, never in the repository or chat. APP_PASSWORD is mandatory whenever the app binds beyond localhost; with SEEDANCE_PROVIDER=kie use MEDIA_HOST=kie (kie.ai's temporary storage, deleted after 24 h, which its model backends can always reach); otherwise prefer MEDIA_HOST=self with PUBLIC_BASE_URL so media is served through signed, expiring links.


## Fast alternative: face mesh

Choose **Fast · Face mesh** in the UI, or use `workflow.py prepare VIDEO --mode face_mesh`. Install the optional worker once with `bash setup-face-mesh.sh` (Python 3.12 required; isolated `.venv-face`). The model downloads from Google's official storage on first use.

This path keeps the original video, tracks and saves facial landmarks, draws the mesh, isolates vocals and embeds the +3 pitched vocals. It makes **no depth or SAM calls**. It is currently designed for a single centered speaker; inspect all frames and stop on tracking gaps or incorrect face alignment. Do not claim mask review in this mode: review facial tracking and audio instead. Saved artifact hashes gate submission.

Use the same character references, draft-first submission, collection, original-audio restoration and explicitly approved HD upgrade. In the prompt, explain that mesh lines are motion guidance and must disappear, replace the complete original identity including hair, and preserve all dialogue and timing. Image reference uses Edit; video identity uses Omni. Keep advanced depth mode available. Do not submit automatically during setup.


### Combined depth + face mesh

Select **Advanced + Face mesh** in the UI or use `workflow.py prepare VIDEO --mode depth_mesh`. This runs the original depth/SAM pipeline, preserves its depth composite, then tracks the face from the original frames and overlays the mesh onto that composite. Landmarks are saved in `face-landmarks.json`. Review BOTH mask coverage and face tracking before approval. All artifacts, including depth, mask, mesh and embedded audio, are bound to review approval. Follow the same draft → approved HD → original-audio restoration flow.

For setup with all three preparation modes, also install Python 3.12 and run `bash setup-face-mesh.sh`. This creates an isolated mesh environment; the main app can retain its existing Python version. A Google Face Landmarker model is downloaded at first use. No SAM/depth inference is used by fast mode. Mesh modes are experimental and currently optimized for one centered speaker.
