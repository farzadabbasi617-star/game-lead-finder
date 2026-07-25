"""
FastAPI routes برای تجمیع مدل‌های ویدیو.
مسیرها با prefix `/video` mount میشن.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

from .registry import (
    get_all_models, get_model, filter_models, registry_stats,
    Provider, VideoTaskType,
)
from .runners import GenerationRequest, generate
from .backend_bridge import (
    get_backend_url, set_backend_url, backend_health, remote_generate,
)

router = APIRouter(prefix="/video", tags=["video-gen"])


def _has_local_gpu() -> bool:
    """چک میکنه GPU لوکال داره یا باید از backend استفاده کنه."""
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


@router.get("/models")
def list_models(
    provider: Optional[str] = None,
    task: Optional[str] = None,
    free_only: bool = False,
    max_vram: Optional[float] = None,
    tag: Optional[str] = None,
):
    """لیست همه مدل‌های ویدیو با فیلتر."""
    prov = Provider(provider) if provider else None
    tsk = VideoTaskType(task) if task else None
    models = filter_models(provider=prov, task=tsk, free_only=free_only,
                           max_vram_gb=max_vram, tag=tag)
    return {"count": len(models), "models": [m.to_dict() for m in models]}


@router.get("/models/{model_id}")
def model_detail(model_id: str):
    m = get_model(model_id)
    if not m:
        raise HTTPException(404, "Model not found")
    return m.to_dict()


@router.get("/stats")
def stats():
    return registry_stats()


@router.post("/generate")
def generate_video(
    background: BackgroundTasks,
    model_id: str = Form(...),
    prompt: str = Form(...),
    task: str = Form("t2v"),
    negative_prompt: Optional[str] = Form(None),
    num_frames: int = Form(49),
    fps: Optional[int] = Form(None),
    width: Optional[int] = Form(None),
    height: Optional[int] = Form(None),
    seed: int = Form(42),
    guidance_scale: float = Form(6.0),
    num_inference_steps: int = Form(50),
    image: Optional[UploadFile] = File(None),
):
    """درخواست تولید ویدیو. توجه: روی سرور معمولی FastAPI (بدون GPU)
    مدل‌های سنگین کار نمیکنن - از Colab notebook استفاده کن."""
    m = get_model(model_id)
    if not m:
        raise HTTPException(404, "Model not found")

    input_image_path = None
    if image:
        p = Path("/tmp/video_gen_inputs")
        p.mkdir(parents=True, exist_ok=True)
        input_image_path = str(p / image.filename)
        with open(input_image_path, "wb") as f:
            f.write(image.file.read())

    req = GenerationRequest(
        model_id=model_id, prompt=prompt, task=VideoTaskType(task),
        negative_prompt=negative_prompt, input_image_path=input_image_path,
        num_frames=num_frames, fps=fps, width=width, height=height,
        seed=seed, guidance_scale=guidance_scale,
        num_inference_steps=num_inference_steps,
    )

    # اگر backend GPU وصله و مدل diffusers هست، بفرست به backend
    use_backend = get_backend_url() and m.provider.value in ("hf_diffusers", "huggingface", "github")
    if use_backend and not _has_local_gpu():
        result = remote_generate(req)
        source = "remote_backend"
    else:
        result = generate(req)
        source = "local"

    return {
        "ok": result.ok, "error": result.error, "output_path": result.output_path,
        "elapsed_sec": round(result.elapsed_sec, 2), "logs": result.logs,
        "model_id": result.model_id, "source": source,
    }


@router.get("/backend")
def backend_info():
    """اطلاعات backend GPU متصل (Colab/RunPod/...)."""
    return backend_health()


@router.post("/backend")
def set_backend(url: str = Form(...)):
    """آدرس backend رو تنظیم میکنه (مثلاً آدرس ngrok از Colab)."""
    set_backend_url(url)
    return {"ok": True, "url": get_backend_url(), "health": backend_health()}


@router.get("/download")
def download(path: str):
    p = Path(path)
    if not p.exists() or not str(p).startswith("/tmp/"):
        raise HTTPException(404, "Not found")
    return FileResponse(str(p), filename=p.name)


# ---------------------------------------------------------------------------
# UI - صفحه ساده HTML برای مرور و تست
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def video_ui():
    from .registry import get_all_models
    models = get_all_models()

    def badge(text, color="#4a5568"):
        return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:12px;font-size:11px;margin:2px;display:inline-block">{text}</span>'

    provider_colors = {
        "hf_diffusers": "#ff9d00",
        "huggingface": "#ffcc00",
        "openrouter": "#6b46c1",
        "github": "#333",
        "replicate": "#0ea5e9",
        "fal": "#ec4899",
    }
    task_colors = {"t2v": "#10b981", "i2v": "#3b82f6", "v2v": "#8b5cf6", "t2v+audio": "#ef4444"}

    cards = []
    for m in models:
        prov_badge = badge(m.provider.value, provider_colors.get(m.provider.value, "#666"))
        task_badges = "".join(badge(t.value, task_colors.get(t.value, "#666")) for t in m.task_types)
        tag_badges = "".join(badge(t, "#94a3b8") for t in m.tags)
        vram = f"~{m.vram_gb_min}GB" if m.vram_gb_min else "—"
        free_badge = badge("رایگان" if m.free else "پولی", "#22c55e" if m.free else "#ef4444")
        links = []
        if m.hf_url: links.append(f'<a href="{m.hf_url}" target="_blank">HF</a>')
        if m.github_url: links.append(f'<a href="{m.github_url}" target="_blank">GitHub</a>')
        if m.homepage: links.append(f'<a href="{m.homepage}" target="_blank">Home</a>')
        cards.append(f"""
        <div class="card">
          <h3>{m.name}</h3>
          <div>{prov_badge}{task_badges}{free_badge}</div>
          <p>{m.description}</p>
          <div class="meta">
            📐 {m.default_resolution} · ⏱️ {m.max_seconds}s @ {m.fps}fps · 🎮 VRAM: {vram}<br>
            📜 {m.license}
          </div>
          <div>{tag_badges}</div>
          <div class="links">{' · '.join(links)}</div>
          <button onclick="pickModel('{m.id}')">انتخاب برای تولید ←</button>
        </div>
        """)

    options = "\n".join(
        f'<option value="{m.id}">{m.name} ({m.provider.value})</option>' for m in models
    )
    stats = registry_stats()

    html = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<title>🎬 Video Model Hub</title>
<style>
body{{font-family:Tahoma,sans-serif;background:#f8fafc;margin:0;padding:20px;color:#1e293b}}
.header{{background:linear-gradient(135deg,#667eea,#764ba2);color:white;padding:30px;border-radius:16px;margin-bottom:20px}}
.header h1{{margin:0}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}}
.stat{{background:rgba(255,255,255,0.2);padding:8px 16px;border-radius:8px}}
.filters{{background:white;padding:16px;border-radius:12px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.1)}}
.filters input,.filters select{{margin:4px;padding:6px;border:1px solid #e2e8f0;border-radius:6px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}}
.card{{background:white;padding:16px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,0.08);transition:transform 0.15s}}
.card:hover{{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,0.15)}}
.card h3{{margin:0 0 8px 0;font-size:16px}}
.card p{{color:#475569;font-size:13px;margin:8px 0}}
.card .meta{{color:#64748b;font-size:12px;margin:8px 0}}
.card .links{{margin:8px 0;font-size:12px}}
.card .links a{{color:#3b82f6;text-decoration:none;margin:0 4px}}
.card button{{background:#3b82f6;color:white;border:none;padding:8px 12px;border-radius:6px;cursor:pointer;width:100%;margin-top:8px}}
.card button:hover{{background:#2563eb}}
.generator{{background:white;padding:20px;border-radius:12px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,0.1)}}
.generator textarea{{width:100%;min-height:80px;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-family:inherit}}
.generator select,.generator input{{padding:6px;border:1px solid #e2e8f0;border-radius:6px;margin:4px}}
#result{{margin-top:12px;padding:12px;background:#f1f5f9;border-radius:8px;white-space:pre-wrap;font-family:monospace;font-size:12px;max-height:300px;overflow:auto}}
.warn{{background:#fef3c7;border-left:4px solid #f59e0b;padding:12px;border-radius:6px;margin:8px 0;font-size:13px}}
</style>
</head>
<body>
<div class="header">
  <h1>🎬 Video Model Hub</h1>
  <p>تجمیع {stats['total']} مدل ساخت ویدیو از HuggingFace، OpenRouter و GitHub</p>
  <div class="stats">
    <div class="stat">📦 کل: {stats['total']}</div>
    <div class="stat">🆓 رایگان: {stats['free']}</div>
    <div class="stat">💻 مناسب Colab (≤16GB): {stats['colab_friendly']}</div>
    <div class="stat">🎬 T2V: {stats['by_task'].get('t2v', 0)}</div>
    <div class="stat">🖼️ I2V: {stats['by_task'].get('i2v', 0)}</div>
  </div>
</div>

<div class="generator">
  <h2>🚀 تولید ویدیو</h2>
  <div class="warn" id="backend-status">
    ⏳ در حال بررسی اتصال به GPU backend...
  </div>
  <div style="background:#e0e7ff;padding:12px;border-radius:8px;margin:8px 0">
    <b>🔗 GPU Backend (Colab):</b><br>
    <small>برای تولید ویدیو، نوت‌بوک <code>colab/video_hub_colab.ipynb</code> رو در Colab باز کن،
    اجرا کن، بعد آدرس ngrok که میده رو اینجا بذار:</small><br>
    <input id="backend-url" placeholder="https://abc123.ngrok-free.app" style="width:60%;padding:6px;margin-top:6px">
    <button onclick="setBackend()" style="background:#6366f1;color:white;border:none;padding:6px 12px;border-radius:6px;cursor:pointer">اتصال</button>
  </div>
  <label>مدل:</label>
  <select id="model">{options}</select>
  <br>
  <label>Prompt:</label>
  <textarea id="prompt" placeholder="یک اژدهای طلایی که روی برج ایفل پرواز میکنه، غروب سینمایی..."></textarea>
  <br>
  <label>Task:</label>
  <select id="task">
    <option value="t2v">Text-to-Video</option>
    <option value="i2v">Image-to-Video</option>
    <option value="v2v">Video-to-Video</option>
  </select>
  Frames: <input type="number" id="frames" value="49" style="width:70px">
  Seed: <input type="number" id="seed" value="42" style="width:70px">
  Steps: <input type="number" id="steps" value="50" style="width:70px">
  <br>
  <button onclick="generate()" style="background:#10b981;color:white;padding:10px 20px;border:none;border-radius:6px;cursor:pointer;margin-top:8px">
    🎬 تولید ویدیو
  </button>
  <div id="result"></div>
</div>

<div class="filters">
  🔍 <input id="search" placeholder="جستجو..." oninput="filter()">
  <select id="fprovider" onchange="filter()">
    <option value="">همه Providerها</option>
    <option value="hf_diffusers">HF Diffusers</option>
    <option value="huggingface">HF Inference API</option>
    <option value="openrouter">OpenRouter</option>
    <option value="github">GitHub</option>
    <option value="replicate">Replicate</option>
    <option value="fal">fal.ai</option>
  </select>
  <select id="ftask" onchange="filter()">
    <option value="">همه Taskها</option>
    <option value="t2v">Text-to-Video</option>
    <option value="i2v">Image-to-Video</option>
    <option value="v2v">Video-to-Video</option>
  </select>
  <label><input type="checkbox" id="ffree" onchange="filter()"> فقط رایگان</label>
  <label><input type="checkbox" id="fcolab" onchange="filter()"> مناسب Colab (≤16GB)</label>
</div>

<div class="grid" id="grid">{''.join(cards)}</div>

<script>
async function checkBackend(){{
  try{{
    const r=await fetch('/video/backend');
    const j=await r.json();
    const el=document.getElementById('backend-status');
    if(j.connected){{
      el.style.background='#d1fae5';
      el.style.borderLeftColor='#10b981';
      el.innerHTML='✅ متصل به GPU backend: <b>'+j.url+'</b> · GPU: '+(j.gpu||'?')+' · VRAM: '+(j.vram_gb||'?')+'GB';
    }}else{{
      el.innerHTML='⚠️ GPU backend وصل نیست: '+(j.error||'')+'<br><small>روی Render بدون Colab فقط میتونی مدل‌ها رو مرور کنی، تولید کار نمیکنه.</small>';
    }}
  }}catch(e){{}}
}}
async function setBackend(){{
  const url=document.getElementById('backend-url').value;
  const fd=new FormData(); fd.append('url',url);
  const r=await fetch('/video/backend',{{method:'POST',body:fd}});
  const j=await r.json();
  alert(j.health.connected?'✅ متصل شد!':'❌ اتصال شکست: '+j.health.error);
  checkBackend();
}}
checkBackend();
setInterval(checkBackend, 30000);

function pickModel(id){{
  document.getElementById('model').value=id;
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
async function generate(){{
  const fd=new FormData();
  fd.append('model_id',document.getElementById('model').value);
  fd.append('prompt',document.getElementById('prompt').value);
  fd.append('task',document.getElementById('task').value);
  fd.append('num_frames',document.getElementById('frames').value);
  fd.append('seed',document.getElementById('seed').value);
  fd.append('num_inference_steps',document.getElementById('steps').value);
  document.getElementById('result').textContent='در حال ارسال...';
  const r=await fetch('/video/generate',{{method:'POST',body:fd}});
  const j=await r.json();
  document.getElementById('result').textContent=JSON.stringify(j,null,2);
}}
function filter(){{
  const q=document.getElementById('search').value.toLowerCase();
  const p=document.getElementById('fprovider').value;
  const t=document.getElementById('ftask').value;
  const free=document.getElementById('ffree').checked;
  const colab=document.getElementById('fcolab').checked;
  document.querySelectorAll('.card').forEach(c=>{{
    const txt=c.textContent.toLowerCase();
    let show=txt.includes(q);
    if(p&&!txt.includes(p))show=false;
    if(t&&!c.innerHTML.includes('>'+t+'<'))show=false;
    if(free&&!c.innerHTML.includes('رایگان'))show=false;
    c.style.display=show?'':'none';
  }});
}}
</script>
</body></html>
"""
    return html


@router.get("/health")
def health():
    return {"status": "ok", "models": len(get_all_models())}
