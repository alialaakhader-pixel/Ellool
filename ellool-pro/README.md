# ELLOOL AI — local-first AI studio

The frontend defaults to an open-weight model through the ELLOOL Cloudflare Workers AI gateway deployed in the owner's Cloudflare account. The app also retains an on-device WebLLM/WebGPU option and an optional owner-operated Ollama server. No Gemini/OpenAI hosted chat API fallback is used.

## Live frontend

- https://ellool-ai-studio.vercel.app
- Vercel root directory: `ellool-pro/frontend`
- Build: `npm run build`
- Output: `dist`

Vercel hosts only the static web application. It does not host the model weights or run GPU inference.

## Default cloud brain (Cloudflare Workers AI)

- Worker: `ellool-ai-brain`
- Endpoint: `https://ellool-ai-brain.ellool-ai.workers.dev/v1`
- Model: `@cf/meta/llama-3.3-70b-instruct-fp8-fast`
- Source: `cloudflare/worker.js`
- The Worker is deployed in the connected Cloudflare account and uses the Workers AI binding; it does not store conversation text.
- The Cloudflare Workers AI Free allocation is 10,000 Neurons per day. Requests stop when the free daily allowance or service limits are reached; no paid upgrade is configured.
- The Worker restricts browser origins to the ELLOOL production domains, applies a best-effort per-IP rate limit, caps request size and output tokens, and returns OpenAI-compatible SSE chunks.
- Cloudflare's Free allocation is a quota, not unlimited hosting. The local WebGPU model remains available as an alternative, and a self-hosted Ollama backend remains optional.

## Local inference

On first use, the browser downloads the selected model weights and initializes WebGPU. The default model is `Qwen2.5-1.5B-Instruct-q4f16_1-MLC`. This needs a supported browser, WebGPU, enough RAM/GPU memory, and a one-time model download. Performance depends on the device. If WebGPU is unavailable, the app reports that limitation rather than silently switching to a third-party AI.

## Optional: self-host the Ollama brain

A Docker deployment is provided in `backend/`:

- `Dockerfile`
- `compose.yaml`
- `.env.selfhost.example`
- OpenAI-compatible endpoint: `/v1/chat/completions`
- Default model: `qwen2.5:3b`
- Inference is restricted to the Ollama instance inside the deployment. No Gemini/OpenAI provider fallback exists.

To run it on a server you control:

1. Install Docker Engine and the Docker Compose plugin. For useful speed, use a server with an NVIDIA GPU. If it has NVIDIA Container Toolkit, run `docker compose -f compose.yaml -f compose.nvidia.yaml up -d --build`; otherwise run `docker compose up -d --build` (CPU inference can be slow).
2. In `ellool-pro/backend`, copy `.env.selfhost.example` to `.env`.
3. Generate unique random values for `APP_SECRET` and `ELLOOL_API_KEY`; set a unique `BOOTSTRAP_ADMIN_PASSWORD`. Do not commit or share the real `.env`.
4. Set `CORS_ORIGINS=https://ellool-ai-studio.vercel.app`.
5. Run `docker compose up -d --build`. The first start downloads the Qwen model and can take time and disk space.
6. Put the API behind HTTPS using a reverse proxy. The Compose file binds port 8000 to localhost only; do not expose Ollama port 11434 publicly.
7. In the app, select **خادم Ollama خاص أملكه**, set the URL to `https://YOUR-DOMAIN/v1`, model to `qwen2.5:3b`, and enter the same `ELLOOL_API_KEY` in the encrypted app vault.

This self-hosted API requires an always-on machine/server and persistent storage. A Vercel serverless function is not suitable for hosting Ollama model weights and GPU inference. The repository changes prepare the service, but the owner must supply and operate the actual compute server; no server or GPU was provisioned by this code change.

## Other capabilities and honest limits

- Encrypted local vault with PBKDF2 + AES-GCM.
- Optional Supabase Auth and encrypted vault backup/restore.
- Local SVG illustration generation for logos, diagrams, and vector artwork.
- Browser-side video processing and storyboard animation; not generative photorealistic video.
- Stable Diffusion integration is optional and requires a separately self-hosted image model server.
- Image/video model hosting and high-quality generation need their own compute resources; no external AI image/video service is silently used.

## Run the frontend locally

```bash
cd ellool-pro/frontend
npm install
npm run dev
```

## Security

- Never commit `.env`, API keys, service-role keys, or passwords.
- Keep Ollama bound to the private Docker network.
- Put the API behind HTTPS and restrict CORS to the deployed frontend origin.
- The self-hosted chat endpoint requires a bearer key of at least 24 characters.
- Keep backups of the encrypted vault and its passphrase separately.
