"""
Backend Bridge
==============
اپ Render (بدون GPU) درخواست تولید ویدیو رو به یک "GPU backend" میفرسته
که میتونه Colab+ngrok، RunPod، Modal، یا هر endpoint HTTP دیگه باشه.

آدرس backend از env var `VIDEO_GPU_BACKEND_URL` خونده میشه.
مثال:
    VIDEO_GPU_BACKEND_URL=https://abc123.ngrok-free.app
"""
from __future__ import annotations
import os
import time
import httpx
from typing import Optional
from .runners import GenerationRequest, GenerationResult


DEFAULT_TIMEOUT = 900  # 15 دقیقه برای مدل‌های سنگین


def get_backend_url() -> Optional[str]:
    """آدرس GPU backend فعلی رو برمیگردونه."""
    url = os.getenv("VIDEO_GPU_BACKEND_URL", "").strip().rstrip("/")
    return url or None


def set_backend_url(url: str) -> None:
    """آدرس backend رو در runtime تنظیم میکنه (بدون restart)."""
    os.environ["VIDEO_GPU_BACKEND_URL"] = url.strip().rstrip("/")


def backend_health() -> dict:
    """چک میکنه که backend فعاله یا نه."""
    url = get_backend_url()
    if not url:
        return {"connected": False, "error": "VIDEO_GPU_BACKEND_URL تنظیم نشده"}
    try:
        r = httpx.get(f"{url}/health", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {
                "connected": True,
                "url": url,
                "gpu": data.get("gpu"),
                "vram_gb": data.get("vram_gb"),
                "loaded_models": data.get("loaded_models", []),
            }
        return {"connected": False, "error": f"HTTP {r.status_code}", "url": url}
    except Exception as e:
        return {"connected": False, "error": str(e), "url": url}


def remote_generate(req: GenerationRequest, timeout: int = DEFAULT_TIMEOUT) -> GenerationResult:
    """درخواست تولید ویدیو رو به backend Colab میفرسته و ویدیو رو دانلود میکنه."""
    t0 = time.time()
    url = get_backend_url()
    if not url:
        return GenerationResult(
            ok=False,
            error="هیچ GPU backend وصل نیست. Colab notebook رو باز کن و آدرس ngrok رو در VIDEO_GPU_BACKEND_URL بذار.",
            model_id=req.model_id,
        )
    try:
        payload = {
            "model_id": req.model_id,
            "prompt": req.prompt,
            "task": req.task.value if hasattr(req.task, "value") else str(req.task),
            "negative_prompt": req.negative_prompt,
            "num_frames": req.num_frames,
            "fps": req.fps,
            "width": req.width,
            "height": req.height,
            "seed": req.seed,
            "guidance_scale": req.guidance_scale,
            "num_inference_steps": req.num_inference_steps,
        }
        # اگر عکس ورودی هست، آپلود کن
        files = None
        if req.input_image_path and os.path.exists(req.input_image_path):
            files = {"image": open(req.input_image_path, "rb")}

        r = httpx.post(
            f"{url}/generate",
            data=payload,
            files=files,
            timeout=timeout,
        )
        if r.status_code != 200:
            return GenerationResult(
                ok=False,
                error=f"Backend HTTP {r.status_code}: {r.text[:400]}",
                model_id=req.model_id,
                elapsed_sec=time.time() - t0,
            )

        # پاسخ یا JSON با URL ویدیو، یا مستقیم بایت‌های ویدیو
        content_type = r.headers.get("content-type", "")
        if "video" in content_type or "octet-stream" in content_type:
            # مستقیم بایت‌ها
            from pathlib import Path
            out_dir = Path(req.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            outpath = str(out_dir / f"{req.model_id}_{int(time.time() * 1000)}.mp4")
            with open(outpath, "wb") as f:
                f.write(r.content)
            return GenerationResult(
                ok=True, output_path=outpath, model_id=req.model_id,
                elapsed_sec=time.time() - t0,
            )
        # JSON
        data = r.json()
        if not data.get("ok"):
            return GenerationResult(
                ok=False, error=data.get("error", "unknown"),
                model_id=req.model_id, elapsed_sec=time.time() - t0,
            )
        # دانلود ویدیو از URL برگشتی
        video_url = data.get("video_url")
        if video_url:
            if not video_url.startswith("http"):
                video_url = f"{url}{video_url}"
            vr = httpx.get(video_url, timeout=180)
            from pathlib import Path
            out_dir = Path(req.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            outpath = str(out_dir / f"{req.model_id}_{int(time.time() * 1000)}.mp4")
            with open(outpath, "wb") as f:
                f.write(vr.content)
            return GenerationResult(
                ok=True, output_path=outpath, model_id=req.model_id,
                logs=data.get("logs", ""),
                elapsed_sec=time.time() - t0,
            )
        return GenerationResult(
            ok=False, error="پاسخ backend نه video_url داشت نه بایت ویدیو",
            model_id=req.model_id, elapsed_sec=time.time() - t0,
        )
    except httpx.TimeoutException:
        return GenerationResult(
            ok=False,
            error=f"Timeout بعد از {timeout}s. مدل خیلی سنگینه یا Colab قطع شده.",
            model_id=req.model_id, elapsed_sec=time.time() - t0,
        )
    except Exception as e:
        return GenerationResult(
            ok=False, error=f"{type(e).__name__}: {e}",
            model_id=req.model_id, elapsed_sec=time.time() - t0,
        )
