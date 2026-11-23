"""
ELLOOL Local Image Engine - توليد صور محليًا، وفيديو حقيقي فقط عند توافر GPU مناسب.

التشغيل على كمبيوتر فيه Python 3.10+:
    python ai_studio.py

النموذج والأوزان تُنزّل أول مرة من مستودعاتها العامة، وبعدها يتم الاستدلال محليًا على الكمبيوتر.
لا يستخدم هذا المحرك API لتوليد الصور أو يرسل الصور إلى خدمة inference خارجية.

الأجهزة:
  - NVIDIA GPU بذاكرة 16GB+ : FLUX.1-schnell للصور، وتجربة Wan2.1 للفيديو الحقيقي.
  - GPU أضعف أو Mac أو CPU: Stable Diffusion 1.5 للصور؛ لا يوجد فيديو حقيقي.

متغيرات اختيارية: HW=low  API_KEY=...  PORT=8000
"""
import importlib
import importlib.util
import os
import secrets
import hmac
import subprocess
import sys


_DEPS = {
    "torch": "torch", "diffusers": "diffusers", "transformers": "transformers",
    "accelerate": "accelerate", "sentencepiece": "sentencepiece",
    "google.protobuf": "protobuf", "fastapi": "fastapi", "uvicorn": "uvicorn",
    "PIL": "pillow", "imageio": "imageio", "imageio_ffmpeg": "imageio-ffmpeg",
    "peft": "peft",
}


def _has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"):
    _DEPS["supabase"] = "supabase"

_missing = [pkg for mod, pkg in _DEPS.items() if not _has(mod)]
if _missing:
    print("بركّب المكتبات الناقصة:", ", ".join(_missing))
    subprocess.check_call([sys.executable, "-m", "pip", "install", *_missing])

import gc
import threading
import uuid
import webbrowser
from pathlib import Path

import torch
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
OUT = BASE_DIR / "outputs"
OUT.mkdir(exist_ok=True)
PORT = int(os.getenv("PORT", "8000"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "media").strip() or "media"
_sb = None


def load_api_key():
    configured = os.getenv("API_KEY", "").strip()
    if configured:
        return configured
    key_file = BASE_DIR / ".ellool_engine_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(32)
    key_file.write_text(key, encoding="utf-8")
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
    return key


API_KEY = load_api_key()
print("مفتاح ربط ELLOOL المحلي (احتفظ به ولا تشاركه):", API_KEY)


def detect():
    if torch.cuda.is_available():
        gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        return "cuda", ("high" if gb >= 16 else "low"), gb
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps", "low", 0.0
    return "cpu", "low", 0.0


DEVICE, TIER, VRAM = detect()
if os.getenv("HW", "").lower() == "low":
    TIER = "low"
print(f"الجهاز: {DEVICE} | VRAM: {VRAM:.1f}GB | وضع الصور: {TIER}")

FLUX_ID = "black-forest-labs/FLUX.1-schnell"
WAN_ID = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
SD15_ID = "stable-diffusion-v1-5/stable-diffusion-v1-5"
LCM_LORA = "latent-consistency/lcm-lora-sdv1-5"

_lock = threading.Lock()
_pipes: dict = {}
jobs: dict = {}


def _free():
    _pipes.clear()
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def r16(x):
    return max(256, (int(x) // 16) * 16)


def gen(seed):
    return torch.Generator("cpu").manual_seed(seed) if seed is not None else None


def image_pipe():
    if "image" in _pipes:
        return _pipes["image"]
    _free()
    if TIER == "high":
        from diffusers import FluxPipeline
        bf16_ok = DEVICE == "cuda" and torch.cuda.is_bf16_supported()
        dtype = torch.bfloat16 if bf16_ok else torch.float16
        p = FluxPipeline.from_pretrained(FLUX_ID, torch_dtype=dtype)
        if VRAM >= 22:
            p.to("cuda")
        else:
            p.enable_model_cpu_offload()
        _pipes["image"] = (p, "flux")
    else:
        from diffusers import StableDiffusionPipeline
        dtype = torch.float16 if DEVICE in ("cuda", "mps") else torch.float32
        p = StableDiffusionPipeline.from_pretrained(SD15_ID, torch_dtype=dtype)
        kind = "sd"
        try:
            from diffusers import LCMScheduler
            p.load_lora_weights(LCM_LORA)
            p.fuse_lora()
            p.scheduler = LCMScheduler.from_config(p.scheduler.config)
            kind = "lcm"
        except Exception as e:
            print("LCM مش متاح، هستخدم الوضع العادي:", e)
        p.to(DEVICE)
        if DEVICE == "cuda":
            p.enable_attention_slicing()
        _pipes["image"] = (p, kind)
    return _pipes["image"]


def video_pipe():
    if "video" in _pipes:
        return _pipes["video"]
    _free()
    from diffusers import AutoencoderKLWan, WanPipeline
    vae = AutoencoderKLWan.from_pretrained(WAN_ID, subfolder="vae", torch_dtype=torch.float32)
    video_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    p = WanPipeline.from_pretrained(WAN_ID, vae=vae, torch_dtype=video_dtype)
    p.enable_model_cpu_offload()
    _pipes["video"] = p
    return p


def render_image(prompt, w, h, seed):
    p, kind = image_pipe()
    if kind == "flux":
        return p(prompt=prompt, width=r16(w), height=r16(h), num_inference_steps=4,
                 guidance_scale=0.0, max_sequence_length=256, generator=gen(seed)).images[0]
    w, h = min(r16(w), 768), min(r16(h), 768)
    steps, cfg = (6, 1.5) if kind == "lcm" else (20, 7.5)
    return p(prompt=prompt, width=w, height=h, num_inference_steps=steps,
             guidance_scale=cfg, generator=gen(seed)).images[0]


def publish_result(name, kind, prompt):
    """Optional archive in private Supabase Storage; local output remains the fallback."""
    global _sb
    local_url = f"/files/{name}"
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return local_url
    try:
        from supabase import create_client
        if _sb is None:
            _sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        path = f"local/{name}"
        mime = "video/mp4" if kind == "video" else "image/png"
        with open(OUT / name, "rb") as stream:
            _sb.storage.from_(SUPABASE_BUCKET).upload(
                path, stream.read(), {"content-type": mime, "upsert": "true"}
            )
        _sb.table("generations").insert({
            "user_id": None, "kind": kind, "prompt": prompt,
            "final_prompt": prompt, "file_path": path
        }).execute()
        signed = _sb.storage.from_(SUPABASE_BUCKET).create_signed_url(path, 3600)
        return signed.get("signedURL") or signed.get("signedUrl") or local_url
    except Exception as exc:
        print("Supabase archive unavailable; keeping local file:", type(exc).__name__, exc)
        return local_url


def make_image(r):
    name = f"{uuid.uuid4().hex}.png"
    render_image(r.prompt, r.width, r.height, r.seed).save(OUT / name)
    return publish_result(name, "image", r.prompt)


def make_video(r):
    if DEVICE != "cuda" or TIER != "high":
        raise RuntimeError("توليد الفيديو الحقيقي يحتاج NVIDIA GPU بذاكرة 16GB على الأقل. لم يتم إنشاء فيديو زائف من صورة.")
    name = f"{uuid.uuid4().hex}.mp4"
    from diffusers.utils import export_to_video
    p = video_pipe()
    fps = 16
    frames = (int(r.seconds * fps) // 4) * 4 + 1
    out = p(prompt=r.prompt,
            negative_prompt="blurry, low quality, distorted, watermark, text, jitter",
            width=r16(r.width), height=r16(r.height), num_frames=frames,
            guidance_scale=5.0, num_inference_steps=r.steps,
            generator=gen(r.seed)).frames[0]
    export_to_video(out, str(OUT / name), fps=fps)
    return publish_result(name, "video", r.prompt)


def prune_jobs():
    if len(jobs) > 100:
        for key in list(jobs)[:-80]:
            if jobs.get(key, {}).get("status") in ("done", "error"):
                jobs.pop(key, None)


def run_job(job_id, fn, req):
    try:
        with _lock:
            jobs[job_id]["status"] = "running"
            result_url = fn(req)
        jobs[job_id].update(status="done", url=result_url)
    except Exception as e:
        print(f"Job {job_id} failed: {type(e).__name__}: {e}")
        jobs[job_id].update(status="error", error=f"{type(e).__name__}: {e}")


def submit(fn, req):
    prune_jobs()
    active = sum(1 for item in jobs.values() if item.get("status") in ("queued", "running"))
    if active >= 2:
        raise HTTPException(429, "يوجد طلبان قيد التنفيذ بالفعل. انتظر انتهاء أحدهما.")
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "queued"}
    threading.Thread(target=run_job, args=(job_id, fn, req), daemon=True).start()
    return job_id


def auth(x_api_key: str | None = Header(default=None)):
    if not x_api_key or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "مفتاح الربط غير صحيح.")


class ImageReq(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    width: int = Field(1024, ge=256, le=1536)
    height: int = Field(1024, ge=256, le=1536)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class VideoReq(BaseModel):
    prompt: str = Field(min_length=1, max_length=1500)
    width: int = Field(832, ge=256, le=1280)
    height: int = Field(480, ge=256, le=720)
    seconds: float = Field(3, ge=1, le=5)
    steps: int = Field(30, ge=10, le=40)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


app = FastAPI(title="ELLOOL Local Image Engine")
from fastapi.middleware.cors import CORSMiddleware

_ALLOWED_ORIGINS = [
    "https://ellool-ai-studio.vercel.app",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def private_network_headers(request, call_next):
    response = await call_next(request)
    origin = request.headers.get("origin", "")
    if origin in _ALLOWED_ORIGINS and request.headers.get("access-control-request-private-network") == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


app.mount("/files", StaticFiles(directory=OUT), name="files")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


@app.get("/api/info")
def info():
    return {
        "device": DEVICE,
        "tier": TIER,
        "vram_gb": round(VRAM, 1),
        "image_model": FLUX_ID if TIER == "high" else SD15_ID,
        "video_ready": DEVICE == "cuda" and TIER == "high",
        "engine": "ELLOOL Local Engine",
    }


@app.post("/api/image", dependencies=[Depends(auth)])
def api_image(req: ImageReq):
    return {"job_id": submit(make_image, req)}


@app.post("/api/video", dependencies=[Depends(auth)])
def api_video(req: VideoReq):
    return {"job_id": submit(make_video, req)}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(auth)])
def api_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]


HTML = r"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ELLOOL Local Image Engine</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f1115;color:#e8e8ea;margin:0;padding:16px;max-width:720px;margin-inline:auto}
h1{font-size:20px;margin:0 0 4px}#info{opacity:.6;font-size:13px;margin-bottom:12px}
.tabs{display:flex;gap:8px;margin-bottom:12px}
.tabs button,.go{padding:10px 16px;border:0;border-radius:10px;background:#23262f;color:#fff;font-size:15px}
.tabs button.on,.go{background:#3b82f6}
textarea,input{width:100%;box-sizing:border-box;padding:10px;border-radius:10px;border:1px solid #333;background:#181b22;color:#fff;font-size:15px;margin-bottom:8px}
.row{display:flex;gap:8px}#out{margin-top:16px}img,video{max-width:100%;border-radius:12px}#st{opacity:.8;margin-top:8px}
</style></head><body>
<h1>ELLOOL Local Image Engine</h1><div id="info"></div>
<div class="tabs"><button id="t-image" class="on">صورة</button><button id="t-video">فيديو حقيقي (GPU مناسب)</button></div>
<input id="key" placeholder="مفتاح الربط المطبوع في نافذة التشغيل" type="password">
<textarea id="prompt" rows="4" placeholder="اكتب وصف الصورة أو الفيديو (الإنجليزية غالبًا أفضل للنماذج المحلية)"></textarea>
<div class="row"><input id="w" type="number" value="1024"><input id="h" type="number" value="1024">
<input id="sec" type="number" value="3" style="display:none"></div>
<button class="go" id="go">توليد</button><div id="st"></div><div id="out"></div>
<script>
let mode="image",low=false;const $=id=>document.getElementById(id);
function dims(){const s=low?512:(mode==="video"?[832,480]:[1024,1024]);
 $("w").value=low?512:s[0];$("h").value=low?512:s[1];}
function setMode(m){mode=m;$("t-image").className=m==="image"?"on":"";$("t-video").className=m==="video"?"on":"";$("sec").style.display=m==="video"?"":"none";dims();}
$("t-image").onclick=()=>setMode("image");$("t-video").onclick=()=>setMode("video");
fetch("/api/info").then(r=>r.json()).then(i=>{low=i.tier==="low";dims();
 $("info").textContent=`الجهاز: ${i.device} | وضع الصور: ${low?"خفيف":"احترافي"} | فيديو حقيقي: ${i.video_ready?"متاح تجريبيًا":"غير متاح على هذا الجهاز"}`;});
const H=()=>({"Content-Type":"application/json","X-API-Key":$("key").value});
$("go").onclick=async()=>{
 const b={prompt:$("prompt").value,width:+$("w").value,height:+$("h").value};
 if(mode==="video"){b.seconds=+$("sec").value;b.steps=30;}
 $("out").innerHTML="";$("st").textContent="جاري الإرسال...";
 try{
  const r=await fetch("/api/"+mode,{method:"POST",headers:H(),body:JSON.stringify(b)});
  let data={};try{data=await r.json()}catch{}
  if(!r.ok)throw new Error(data.detail||("خطأ "+r.status));
  const job_id=data.job_id,started=Date.now();
  while(Date.now()-started<30*60*1000){
   await new Promise(resolve=>setTimeout(resolve,2000));
   const jr=await fetch("/api/jobs/"+job_id,{headers:H()});
   const j=await jr.json();
   if(j.status==="done"){
    const url=j.url;
    $("out").innerHTML=mode==="video"?`<video src="${url}" controls autoplay></video>`:`<img src="${url}" alt="صورة مولدة محليًا">`;
    $("st").textContent="اكتمل التوليد محليًا";return;
   }
   if(j.status==="error")throw new Error(j.error||"فشل التوليد");
   $("st").textContent=j.status==="queued"?"في الانتظار...":"جاري التوليد محليًا (قد يستغرق التحميل الأول وقتًا)...";
  }
  throw new Error("انتهت مهلة الانتظار؛ افحص نافذة تشغيل المحرك.");
 }catch(e){$("st").textContent="تعذر التوليد: "+e.message;}
};
</script></body></html>"""


if __name__ == "__main__":
    threading.Timer(2.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    print(f"افتح ELLOOL من نفس الكمبيوتر ثم اضبط عنوان المحرك: http://127.0.0.1:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
