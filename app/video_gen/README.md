# 🎬 Video Generation Hub

ماژول تجمیع همه‌ی مدل‌های ساخت ویدیو از منابع مختلف (HuggingFace, OpenRouter, GitHub, Replicate, fal.ai) داخل یک اپ یکپارچه.

## ✨ چی داره؟

- **۴۰+ مدل ساخت ویدیو** در یک رجیستری واحد
- **۶ Provider** پشتیبانی میشه: HF Diffusers، HF Inference API، OpenRouter، GitHub، Replicate، fal.ai
- **۳ نوع Task**: Text-to-Video، Image-to-Video، Video-to-Video
- **API یکپارچه** برای فراخوانی هر مدلی با یک تابع `generate()`
- **رابط وب** روی `/video/` با فیلتر، جستجو و تولید تعاملی
- **نوت‌بوک Colab** آماده برای اجرا با GPU رایگان T4

## 📋 مدل‌های موجود

### 🤗 HuggingFace / Diffusers (اپن سورس، لوکال)
HunyuanVideo, Mochi 1, LTX-Video (0.9 & 0.9.5), CogVideoX (2B/5B/1.5-5B), Wan 2.1 (T2V 1.3B/14B، I2V 14B), Stable Video Diffusion, AnimateDiff, Zeroscope v2 (XL/576w), ModelScope T2V, Allegro, Pyramid Flow, Open-Sora 1.2, Step-Video-T2V, SkyReels V1

### 🔀 OpenRouter
Prompt Enhancer (Claude/GPT برای بهبود prompt ویدیو)، Google Veo (اگر روی OpenRouter فعال باشد)

### 🐙 GitHub (اپن سورس، setup دستی)
Animate Anyone, DynamiCrafter, ToonCrafter, VideoCrafter2, MagicTime, Show-1, I2VGen-XL, Latte, EasyAnimate v5, CogVideo, Open-Sora Plan, Cinemo, MotionCtrl, Framer, MimicMotion

### 💳 Commercial APIs (اختیاری)
Replicate (Kling, Luma, Runway...), fal.ai (LTX, Kling, Veo...), HuggingFace Inference API

## 🚀 استفاده سریع

### روش ۱: نوت‌بوک Colab (توصیه شده)
```
colab/video_hub_colab.ipynb
```
فایل رو در Colab باز کن، Runtime رو روی T4 GPU بذار، و سلول‌ها رو اجرا کن.

### روش ۲: API از FastAPI
```bash
uvicorn app.main:app --reload
# مرورگر: http://127.0.0.1:8000/video/
```

### روش ۳: از پایتون
```python
from app.video_gen import generate, GenerationRequest, VideoTaskType

req = GenerationRequest(
    model_id="cogvideox-2b",
    prompt="A cat playing piano in a jazz bar, cinematic lighting",
    num_frames=49,
    seed=42,
)
result = generate(req)
print(result.output_path)  # /tmp/video_gen_outputs/cogvideox-2b_1234.mp4
```

## 🔑 متغیرهای محیطی (اختیاری)

```bash
export HF_TOKEN=hf_...                   # HuggingFace Inference API
export OPENROUTER_API_KEY=sk-or-...      # OpenRouter
export REPLICATE_API_TOKEN=r8_...        # Replicate
export FAL_KEY=fal_...                   # fal.ai
```

## 🛠️ Endpointهای HTTP

| Method | Path | توضیح |
|--------|------|-------|
| GET | `/video/` | رابط وب HTML |
| GET | `/video/models` | لیست مدل‌ها با فیلتر |
| GET | `/video/models/{id}` | جزئیات یک مدل |
| GET | `/video/stats` | آمار رجیستری |
| POST | `/video/generate` | تولید ویدیو |
| GET | `/video/download?path=...` | دانلود ویدیوی تولیدشده |
| GET | `/video/health` | health check |

## 📁 ساختار

```
app/video_gen/
├── __init__.py               # export های عمومی
├── registry.py               # تعریف همه مدل‌ها
├── runners.py                # اجراکننده‌های هر Provider
├── routes.py                 # FastAPI endpoints + HTML UI
├── requirements-video.txt    # وابستگی‌های سنگین (فقط برای GPU node)
└── README.md                 # همین فایل

colab/
└── video_hub_colab.ipynb     # نوت‌بوک آماده برای Colab
```

## ⚠️ نکات

- **سرور FastAPI اصلی GPU نداره** - مدل‌های سنگین اونجا اجرا نمیشن. از Colab استفاده کن.
- برای CogVideoX-2B، Wan 2.1 1.3B، LTX-Video، AnimateDiff → GPU T4 رایگان کافیه.
- برای HunyuanVideo، Mochi، Wan 14B، Step-Video → نیاز به A100 یا H100.
- برای مدل‌های GitHub که diffusers ازشون پشتیبانی نمیکنه، ابتدا repo رو clone کن.

## 🧩 اضافه کردن مدل جدید

فقط یک `VideoModel(...)` جدید در `registry.py` تعریف کن و به لیست مربوطه اضافه کن. اگر Provider جدیدی نیاز داری، runner ش رو در `runners.py` پیاده کن و در `dispatch` اضافه کن.
