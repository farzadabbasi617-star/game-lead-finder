"""
Runners
=======
هر provider یک runner داره که در زمان اجرا ویدیو تولید میکنه.
دیزاین: هر runner یک تابع generate() داره که ورودی رو استاندارد میگیره
و مسیر فایل ویدیو خروجی رو برمیگردونه.

نکته: import های سنگین (torch, diffusers) داخل تابع انجام میشن تا
بارگذاری کل رجیستری روی سرور FastAPI سبک بمونه.
"""
from __future__ import annotations
import os
import io
import time
import base64
import tempfile
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .registry import VideoModel, Provider, VideoTaskType, get_model


@dataclass
class GenerationRequest:
    model_id: str
    prompt: str
    task: VideoTaskType = VideoTaskType.TEXT_TO_VIDEO
    negative_prompt: Optional[str] = None
    input_image_path: Optional[str] = None
    input_video_path: Optional[str] = None
    num_frames: int = 49
    fps: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    seed: int = 42
    guidance_scale: float = 6.0
    num_inference_steps: int = 50
    output_dir: str = "/tmp/video_gen_outputs"


@dataclass
class GenerationResult:
    ok: bool
    output_path: Optional[str] = None
    error: Optional[str] = None
    logs: str = ""
    elapsed_sec: float = 0.0
    model_id: str = ""


def _ensure_outdir(req: GenerationRequest) -> Path:
    p = Path(req.output_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _outfile(req: GenerationRequest, ext: str = "mp4") -> str:
    d = _ensure_outdir(req)
    ts = int(time.time() * 1000)
    return str(d / f"{req.model_id}_{ts}.{ext}")


# ---------------------------------------------------------------------------
# HF Diffusers Runner
# ---------------------------------------------------------------------------
def run_hf_diffusers(model: VideoModel, req: GenerationRequest) -> GenerationResult:
    """اجرای مدل HF از طریق diffusers لوکالی (روی Colab یا GPU لوکال)"""
    t0 = time.time()
    try:
        import torch  # type: ignore
        from diffusers.utils import export_to_video  # type: ignore
    except ImportError as e:
        return GenerationResult(
            ok=False, error=f"diffusers/torch نصب نیست: {e}",
            model_id=model.id, elapsed_sec=time.time() - t0,
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    logs = [f"→ device={device} dtype={dtype}"]
    repo = model.repo_or_endpoint

    try:
        # per-model pipeline selection
        if model.id.startswith("cogvideox"):
            from diffusers import CogVideoXPipeline  # type: ignore
            pipe = CogVideoXPipeline.from_pretrained(repo, torch_dtype=dtype)
            pipe.enable_model_cpu_offload()
            pipe.vae.enable_tiling()
            out = pipe(
                prompt=req.prompt,
                num_videos_per_prompt=1,
                num_inference_steps=req.num_inference_steps,
                num_frames=req.num_frames,
                guidance_scale=req.guidance_scale,
                generator=torch.Generator(device="cpu").manual_seed(req.seed),
            ).frames[0]

        elif model.id in ("hunyuanvideo",):
            from diffusers import HunyuanVideoPipeline, HunyuanVideoTransformer3DModel  # type: ignore
            transformer = HunyuanVideoTransformer3DModel.from_pretrained(
                repo, subfolder="transformer", torch_dtype=torch.bfloat16
            )
            pipe = HunyuanVideoPipeline.from_pretrained(
                repo, transformer=transformer, torch_dtype=torch.float16
            )
            pipe.vae.enable_tiling()
            pipe.enable_model_cpu_offload()
            out = pipe(
                prompt=req.prompt,
                num_frames=req.num_frames,
                num_inference_steps=req.num_inference_steps,
                height=req.height or 544,
                width=req.width or 960,
            ).frames[0]

        elif model.id == "mochi-1":
            from diffusers import MochiPipeline  # type: ignore
            pipe = MochiPipeline.from_pretrained(repo, variant="bf16", torch_dtype=torch.bfloat16)
            pipe.enable_model_cpu_offload()
            pipe.enable_vae_tiling()
            out = pipe(
                prompt=req.prompt,
                num_frames=req.num_frames,
                num_inference_steps=req.num_inference_steps,
                guidance_scale=req.guidance_scale,
            ).frames[0]

        elif model.id.startswith("ltx-video"):
            from diffusers import LTXPipeline, LTXImageToVideoPipeline  # type: ignore
            if req.task == VideoTaskType.IMAGE_TO_VIDEO and req.input_image_path:
                pipe = LTXImageToVideoPipeline.from_pretrained(repo, torch_dtype=torch.bfloat16)
                from PIL import Image
                img = Image.open(req.input_image_path).convert("RGB")
                pipe.to(device)
                out = pipe(
                    image=img, prompt=req.prompt,
                    width=req.width or 704, height=req.height or 480,
                    num_frames=req.num_frames,
                    num_inference_steps=req.num_inference_steps,
                ).frames[0]
            else:
                pipe = LTXPipeline.from_pretrained(repo, torch_dtype=torch.bfloat16)
                pipe.to(device)
                out = pipe(
                    prompt=req.prompt,
                    width=req.width or 704, height=req.height or 480,
                    num_frames=req.num_frames,
                    num_inference_steps=req.num_inference_steps,
                ).frames[0]

        elif model.id.startswith("wan-2.1"):
            # پشتیبانی diffusers از Wan در حال گسترش است؛ fallback: پیام راهنما
            from diffusers import DiffusionPipeline  # type: ignore
            pipe = DiffusionPipeline.from_pretrained(repo, torch_dtype=dtype)
            pipe.to(device)
            out = pipe(prompt=req.prompt, num_frames=req.num_frames).frames[0]

        elif model.id == "stable-video-diffusion":
            if not req.input_image_path:
                return GenerationResult(ok=False, error="SVD نیاز به input_image_path داره",
                                        model_id=model.id, elapsed_sec=time.time() - t0)
            from diffusers import StableVideoDiffusionPipeline  # type: ignore
            from PIL import Image
            pipe = StableVideoDiffusionPipeline.from_pretrained(repo, torch_dtype=dtype)
            pipe.enable_model_cpu_offload()
            img = Image.open(req.input_image_path).convert("RGB")
            out = pipe(img, num_frames=25, decode_chunk_size=8,
                       generator=torch.manual_seed(req.seed)).frames[0]

        elif model.id == "animatediff":
            from diffusers import AnimateDiffPipeline, MotionAdapter, DDIMScheduler  # type: ignore
            adapter = MotionAdapter.from_pretrained(repo, torch_dtype=dtype)
            pipe = AnimateDiffPipeline.from_pretrained(
                "SG161222/Realistic_Vision_V5.1_noVAE", motion_adapter=adapter, torch_dtype=dtype
            )
            pipe.scheduler = DDIMScheduler.from_config(pipe.scheduler.config, beta_schedule="linear")
            pipe.enable_vae_slicing()
            pipe.enable_model_cpu_offload()
            out = pipe(
                prompt=req.prompt,
                negative_prompt=req.negative_prompt or "bad quality, worse quality",
                num_frames=16, guidance_scale=7.5, num_inference_steps=25,
                generator=torch.Generator("cpu").manual_seed(req.seed),
            ).frames[0]

        elif model.id.startswith("zeroscope") or model.id == "modelscope-t2v":
            from diffusers import DiffusionPipeline  # type: ignore
            pipe = DiffusionPipeline.from_pretrained(repo, torch_dtype=dtype, variant="fp16")
            pipe.enable_model_cpu_offload()
            pipe.enable_vae_slicing()
            frames = pipe(req.prompt, num_inference_steps=25,
                          height=req.height or 320, width=req.width or 576,
                          num_frames=24).frames
            out = frames[0] if isinstance(frames, list) else frames

        elif model.id == "allegro":
            from diffusers import AllegroPipeline  # type: ignore
            pipe = AllegroPipeline.from_pretrained(repo, torch_dtype=torch.bfloat16)
            pipe.enable_model_cpu_offload()
            pipe.vae.enable_tiling()
            out = pipe(
                prompt=req.prompt,
                num_inference_steps=req.num_inference_steps,
                guidance_scale=req.guidance_scale,
                max_sequence_length=512,
            ).frames[0]

        else:
            # generic fallback
            from diffusers import DiffusionPipeline  # type: ignore
            pipe = DiffusionPipeline.from_pretrained(repo, torch_dtype=dtype)
            pipe.to(device)
            out = pipe(prompt=req.prompt).frames[0]

        outpath = _outfile(req, "mp4")
        export_to_video(out, outpath, fps=req.fps or model.fps)
        return GenerationResult(
            ok=True, output_path=outpath, model_id=model.id,
            logs="\n".join(logs), elapsed_sec=time.time() - t0,
        )
    except Exception as e:
        return GenerationResult(
            ok=False, error=f"{type(e).__name__}: {e}", model_id=model.id,
            logs="\n".join(logs), elapsed_sec=time.time() - t0,
        )


# ---------------------------------------------------------------------------
# HuggingFace Inference API Runner
# ---------------------------------------------------------------------------
def run_hf_inference(model: VideoModel, req: GenerationRequest) -> GenerationResult:
    """استفاده از HF Inference API (بدون دانلود مدل)."""
    import httpx
    t0 = time.time()
    token = os.getenv(model.api_key_env or "HF_TOKEN")
    if not token:
        return GenerationResult(ok=False, error="HF_TOKEN تنظیم نشده",
                                model_id=model.id, elapsed_sec=time.time() - t0)
    # اگر id دقیق مدل ندیم، از repo استفاده کن
    target_repo = req.model_id if "/" in req.model_id else model.repo_or_endpoint
    url = f"https://api-inference.huggingface.co/models/{target_repo}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = httpx.post(url, headers=headers, json={"inputs": req.prompt}, timeout=600)
        if r.status_code != 200:
            return GenerationResult(ok=False, error=f"HTTP {r.status_code}: {r.text[:400]}",
                                    model_id=model.id, elapsed_sec=time.time() - t0)
        # اگر پاسخ بایت ویدیو باشه:
        content_type = r.headers.get("content-type", "")
        outpath = _outfile(req, "mp4" if "mp4" in content_type or "video" in content_type else "bin")
        with open(outpath, "wb") as f:
            f.write(r.content)
        return GenerationResult(ok=True, output_path=outpath, model_id=model.id,
                                elapsed_sec=time.time() - t0)
    except Exception as e:
        return GenerationResult(ok=False, error=str(e), model_id=model.id,
                                elapsed_sec=time.time() - t0)


# ---------------------------------------------------------------------------
# OpenRouter Runner
# ---------------------------------------------------------------------------
def run_openrouter(model: VideoModel, req: GenerationRequest) -> GenerationResult:
    """
    OpenRouter بیشتر برای LLM هست. این runner:
    - اگر مدل video داشت مستقیم صدا میزنه
    - اگر نداشت، از LLM برای غنی‌سازی prompt استفاده میکنه و
      برمیگردونه تا کاربر بتونه اون prompt رو در مدل دیگه استفاده کنه
    """
    import httpx
    t0 = time.time()
    token = os.getenv(model.api_key_env or "OPENROUTER_API_KEY")
    if not token:
        return GenerationResult(ok=False, error="OPENROUTER_API_KEY تنظیم نشده",
                                model_id=model.id, elapsed_sec=time.time() - t0)
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if model.id == "openrouter-prompt-enhancer":
        body = {
            "model": model.repo_or_endpoint,
            "messages": [
                {"role": "system", "content": "You are a cinematic video prompt engineer. "
                 "Rewrite user prompts into vivid, detailed 60-word cinematic prompts with "
                 "camera moves, lighting, style. Return ONLY the enhanced prompt."},
                {"role": "user", "content": req.prompt},
            ],
        }
        try:
            r = httpx.post(url, headers=headers, json=body, timeout=60)
            data = r.json()
            enhanced = data["choices"][0]["message"]["content"].strip()
            outpath = _outfile(req, "txt")
            with open(outpath, "w") as f:
                f.write(enhanced)
            return GenerationResult(
                ok=True, output_path=outpath, model_id=model.id,
                logs=f"Enhanced prompt:\n{enhanced}",
                elapsed_sec=time.time() - t0,
            )
        except Exception as e:
            return GenerationResult(ok=False, error=str(e), model_id=model.id,
                                    elapsed_sec=time.time() - t0)
    # سایر مدل‌های OpenRouter (اگر روزی endpoint ویدیو داشت)
    return GenerationResult(
        ok=False,
        error="این مدل OpenRouter هنوز endpoint ویدیو نداره - از HF یا Replicate استفاده کن",
        model_id=model.id, elapsed_sec=time.time() - t0,
    )


# ---------------------------------------------------------------------------
# GitHub Runner (راهنما)
# ---------------------------------------------------------------------------
def run_github(model: VideoModel, req: GenerationRequest) -> GenerationResult:
    """
    مدل‌های GitHub معمولاً setup پیچیده دارن. این runner:
    - چک میکنه repo کلون شده یا نه
    - اگر نه، دستور clone و راهنما برمیگردونه
    - اگر آره، سعی میکنه inference script رو صدا بزنه
    """
    t0 = time.time()
    repo_url = model.repo_or_endpoint
    clone_dir = Path(f"/tmp/gh_models/{model.id}")

    if not clone_dir.exists():
        instructions = (
            f"این مدل نیاز به setup دستی داره. لطفاً اجرا کن:\n\n"
            f"  git clone {repo_url} {clone_dir}\n"
            f"  cd {clone_dir}\n"
            f"  pip install -r requirements.txt\n\n"
            f"سپس دفترچه inference پروژه رو ببین:\n  {model.github_url}\n"
        )
        outpath = _outfile(req, "txt")
        with open(outpath, "w") as f:
            f.write(instructions)
        return GenerationResult(
            ok=False, output_path=outpath, error="Setup لازم",
            logs=instructions, model_id=model.id, elapsed_sec=time.time() - t0,
        )
    # اگر hf_url وجود داره سعی کن از diffusers استفاده کنی
    if model.hf_url:
        alt = VideoModel(**{**model.__dict__, "provider": Provider.HF_DIFFUSERS,
                            "repo_or_endpoint": model.hf_url.split("huggingface.co/")[-1]})
        return run_hf_diffusers(alt, req)
    return GenerationResult(
        ok=False, error="اجرای مستقیم پشتیبانی نمیشه - inference script این ریپو رو ببین",
        model_id=model.id, elapsed_sec=time.time() - t0,
    )


# ---------------------------------------------------------------------------
# Replicate & fal Runners
# ---------------------------------------------------------------------------
def run_replicate(model: VideoModel, req: GenerationRequest) -> GenerationResult:
    import httpx
    t0 = time.time()
    token = os.getenv("REPLICATE_API_TOKEN")
    if not token:
        return GenerationResult(ok=False, error="REPLICATE_API_TOKEN تنظیم نشده",
                                model_id=model.id, elapsed_sec=time.time() - t0)
    # کاربر باید model_id کامل Replicate رو بده (owner/name:version)
    # از req.model_id استفاده میکنیم اگر "/" داشت
    version = req.model_id if "/" in req.model_id else "lucataco/hunyuan-video:latest"
    url = "https://api.replicate.com/v1/predictions"
    headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
    try:
        r = httpx.post(url, headers=headers, json={
            "version": version,
            "input": {"prompt": req.prompt},
        }, timeout=60)
        prediction = r.json()
        pred_url = prediction.get("urls", {}).get("get")
        # poll
        for _ in range(120):
            time.sleep(3)
            pr = httpx.get(pred_url, headers=headers, timeout=30).json()
            if pr["status"] == "succeeded":
                video_url = pr["output"] if isinstance(pr["output"], str) else pr["output"][0]
                vr = httpx.get(video_url, timeout=120)
                outpath = _outfile(req, "mp4")
                with open(outpath, "wb") as f:
                    f.write(vr.content)
                return GenerationResult(ok=True, output_path=outpath, model_id=model.id,
                                        elapsed_sec=time.time() - t0)
            if pr["status"] == "failed":
                return GenerationResult(ok=False, error=pr.get("error", "failed"),
                                        model_id=model.id, elapsed_sec=time.time() - t0)
        return GenerationResult(ok=False, error="Timeout", model_id=model.id,
                                elapsed_sec=time.time() - t0)
    except Exception as e:
        return GenerationResult(ok=False, error=str(e), model_id=model.id,
                                elapsed_sec=time.time() - t0)


def run_fal(model: VideoModel, req: GenerationRequest) -> GenerationResult:
    import httpx
    t0 = time.time()
    token = os.getenv("FAL_KEY")
    if not token:
        return GenerationResult(ok=False, error="FAL_KEY تنظیم نشده",
                                model_id=model.id, elapsed_sec=time.time() - t0)
    endpoint = req.model_id if "/" in req.model_id else "fal-ai/ltx-video"
    url = f"https://fal.run/{endpoint}"
    headers = {"Authorization": f"Key {token}", "Content-Type": "application/json"}
    try:
        r = httpx.post(url, headers=headers, json={"prompt": req.prompt}, timeout=600)
        data = r.json()
        video_url = data.get("video", {}).get("url") if isinstance(data.get("video"), dict) else data.get("video")
        if not video_url:
            return GenerationResult(ok=False, error=f"No video in response: {data}",
                                    model_id=model.id, elapsed_sec=time.time() - t0)
        vr = httpx.get(video_url, timeout=120)
        outpath = _outfile(req, "mp4")
        with open(outpath, "wb") as f:
            f.write(vr.content)
        return GenerationResult(ok=True, output_path=outpath, model_id=model.id,
                                elapsed_sec=time.time() - t0)
    except Exception as e:
        return GenerationResult(ok=False, error=str(e), model_id=model.id,
                                elapsed_sec=time.time() - t0)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def generate(req: GenerationRequest) -> GenerationResult:
    model = get_model(req.model_id)
    if not model:
        return GenerationResult(ok=False, error=f"Model '{req.model_id}' not found",
                                model_id=req.model_id)
    dispatch = {
        Provider.HF_DIFFUSERS: run_hf_diffusers,
        Provider.HUGGINGFACE: run_hf_inference,
        Provider.OPENROUTER: run_openrouter,
        Provider.GITHUB: run_github,
        Provider.REPLICATE: run_replicate,
        Provider.FAL: run_fal,
    }
    fn = dispatch.get(model.provider)
    if not fn:
        return GenerationResult(ok=False, error=f"No runner for provider {model.provider}",
                                model_id=req.model_id)
    return fn(model, req)
