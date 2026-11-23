# ELLOOL AI — اللول

This repository currently contains the available standalone browser edition of ELLOOL AI.

## Current deployment scope

- `index.html`: single-page Arabic RTL application.
- `docs/ellool-master-prompt.md`: the supplied assistant identity and behavior prompt.

## Important limitations

This is **not yet the full backend-connected application**. The uploaded source archive `ellool-pro.zip` could not be read by the available file reader, so the original FastAPI backend, React/Vite source tree, database migrations, and server configuration have not been imported.

The current HTML edition:
- Stores its vault, settings, chats, and connector configuration in the current browser's local storage (encrypted vault).
- Offers local in-browser model inference through WebGPU when supported by the browser/device; model downloads and hardware requirements apply.
- Does not provide cross-device synchronization, server-side accounts, hosted AI inference, or Supabase persistence.
- Uses browser-side image/video workflows; realistic hosted image/video generation is not included by default.

Do not put service-role keys, private API keys, or other secrets in this repository or in browser code.

## Run

Open `index.html` in a modern browser. For best compatibility, serve it over HTTPS (Vercel provides HTTPS). WebGPU support depends on the browser, operating system, and device.

## Hosting

The repository is linked to the Vercel project `ellool-ai`. A push to the configured production branch should trigger a Vercel deployment. Verify the deployment build and public access before treating it as production-ready.

## Next required source

To finish the full cloud edition, import the original `backend/` and `frontend/` files from `ellool-pro.zip` (or provide the extracted project), then implement and test Supabase Auth/Database/Storage, backend API hosting, environment variables, and production security rules. Existing Supabase tables must remain untouched; ELLOOL data should use isolated schema/storage resources.
