"""
GPU Backend Server (روی Colab اجرا میشه)
========================================
این سرور FastAPI روی Colab اجرا میشه، از GPU T4 استفاده میکنه،
و از طریق ngrok به اپ Render وصل میشه.

اجرا:
    python gpu_backend_server.py
یا داخل نوت‌بوک Colab سلول‌های آماده رو بزن.
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# مسیر ریپو رو اضافه کن
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from app.video_gen import generate, GenerationRequest, VideoTaskType, get_model

app = FastAPI(title="Video GPU Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = Path("/content/video_outputs")
OUTPUT_DIR.mkdir(exist_ok=True)


@app.get("/health")
def health():
    try:
        import torch
        gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        vram = torch.cuda.get_device_properties(0).total_memory / 1e9 if torch.cuda.is_available() else 0
        return {
            "ok": True,
            "gpu": gpu,
            "vram_gb": round(vram, 1),
            "cuda_available": torch.cuda.is_available(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/generate")
async def generate_endpoint(
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
    m = get_model(model_id)
    if not m:
        raise HTTPException(404, f"Model {model_id} not found")

    input_image_path = None
    if image:
        input_image_path = str(OUTPUT_DIR / f"input_{int(time.time())}.png")
        with open(input_image_path, "wb") as f:
            f.write(await image.read())

    req = GenerationRequest(
        model_id=model_id, prompt=prompt,
        task=VideoTaskType(task),
        negative_prompt=negative_prompt,
        input_image_path=input_image_path,
        num_frames=num_frames, fps=fps, width=width, height=height,
        seed=seed, guidance_scale=guidance_scale,
        num_inference_steps=num_inference_steps,
        output_dir=str(OUTPUT_DIR),
    )
    result = generate(req)

    if not result.ok:
        return JSONResponse({
            "ok": False, "error": result.error, "logs": result.logs,
            "elapsed_sec": round(result.elapsed_sec, 2),
        })

    # نام فایل رو برگردون - client میتونه /download?path= رو بخونه
    filename = Path(result.output_path).name
    return {
        "ok": True,
        "video_url": f"/download/{filename}",
        "elapsed_sec": round(result.elapsed_sec, 2),
        "logs": result.logs,
        "model_id": result.model_id,
    }


@app.get("/download/{filename}")
def download(filename: str):
    p = OUTPUT_DIR / filename
    if not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(str(p), media_type="video/mp4", filename=filename)


@app.get("/")
def root():
    return {
        "service": "Video GPU Backend",
        "endpoints": ["/health", "/generate (POST)", "/download/{filename}"],
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
