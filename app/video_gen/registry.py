"""
Video Generation Model Registry
================================
تجمیع همه مدل‌های ساخت ویدیو از منابع مختلف:
- HuggingFace Inference API + Diffusers (اپن سورس)
- OpenRouter (اگر مدل ویدیو ارائه بده)
- GitHub Open Source Projects (کلون و اجرای لوکال)

هر مدل با یک schema یکسان تعریف میشه که provider، نوع (t2v/i2v/v2v)،
resolution، مدت زمان، لایسنس، لینک منبع و روش فراخوانی رو مشخص میکنه.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional
from enum import Enum


class Provider(str, Enum):
    HUGGINGFACE = "huggingface"          # HF Inference API / hosted
    HF_DIFFUSERS = "hf_diffusers"        # download & run locally via diffusers
    OPENROUTER = "openrouter"            # OpenRouter API
    GITHUB = "github"                    # clone from github repo
    REPLICATE = "replicate"              # replicate.com API (bonus)
    FAL = "fal"                          # fal.ai API (bonus)


class VideoTaskType(str, Enum):
    TEXT_TO_VIDEO = "t2v"
    IMAGE_TO_VIDEO = "i2v"
    VIDEO_TO_VIDEO = "v2v"
    TEXT_TO_VIDEO_AUDIO = "t2v+audio"


@dataclass
class VideoModel:
    id: str                                    # unique key, e.g. "hunyuanvideo"
    name: str                                  # display name
    provider: Provider
    task_types: list[VideoTaskType]
    repo_or_endpoint: str                      # HF repo id, github url, or endpoint
    description: str
    license: str = "unknown"
    max_seconds: float = 4.0
    default_resolution: str = "512x512"
    supported_resolutions: list[str] = field(default_factory=list)
    fps: int = 24
    vram_gb_min: Optional[float] = None        # for local models
    free: bool = True
    requires_api_key: bool = False
    api_key_env: Optional[str] = None
    homepage: Optional[str] = None
    github_url: Optional[str] = None
    hf_url: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["provider"] = self.provider.value
        d["task_types"] = [t.value for t in self.task_types]
        return d


# =============================================================================
# HuggingFace / Diffusers Open Source Models
# =============================================================================
HF_MODELS: list[VideoModel] = [
    VideoModel(
        id="hunyuanvideo",
        name="HunyuanVideo (Tencent)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="tencent/HunyuanVideo",
        description="مدل ۱۳ میلیارد پارامتری Tencent، کیفیت سینمایی، اپن‌سورس",
        license="Tencent Hunyuan Community",
        max_seconds=5.0,
        default_resolution="720x1280",
        supported_resolutions=["544x960", "720x1280"],
        fps=24,
        vram_gb_min=60.0,
        hf_url="https://huggingface.co/tencent/HunyuanVideo",
        github_url="https://github.com/Tencent/HunyuanVideo",
        tags=["cinematic", "sota", "heavy"],
    ),
    VideoModel(
        id="mochi-1",
        name="Mochi 1 (Genmo)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="genmo/mochi-1-preview",
        description="مدل ۱۰B اپن‌سورس Genmo با حرکت روان و prompt adherence عالی",
        license="Apache 2.0",
        max_seconds=5.4,
        default_resolution="480x848",
        fps=30,
        vram_gb_min=42.0,
        hf_url="https://huggingface.co/genmo/mochi-1-preview",
        github_url="https://github.com/genmoai/models",
        tags=["motion", "apache2"],
    ),
    VideoModel(
        id="ltx-video",
        name="LTX-Video (Lightricks)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="Lightricks/LTX-Video",
        description="مدل real-time، خیلی سریع (۴ ثانیه ویدیو در چند ثانیه!)، ۲B پارامتر",
        license="OpenRAIL",
        max_seconds=5.0,
        default_resolution="704x480",
        fps=25,
        vram_gb_min=12.0,
        hf_url="https://huggingface.co/Lightricks/LTX-Video",
        github_url="https://github.com/Lightricks/LTX-Video",
        tags=["fast", "realtime", "efficient"],
    ),
    VideoModel(
        id="cogvideox-5b",
        name="CogVideoX-5B (THUDM)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="THUDM/CogVideoX-5b",
        description="مدل ۵B با کیفیت بالا از THUDM، سازگاری خوب با prompt",
        license="CogVideoX License",
        max_seconds=6.0,
        default_resolution="720x480",
        fps=8,
        vram_gb_min=18.0,
        hf_url="https://huggingface.co/THUDM/CogVideoX-5b",
        github_url="https://github.com/THUDM/CogVideo",
        tags=["balanced"],
    ),
    VideoModel(
        id="cogvideox-2b",
        name="CogVideoX-2B (THUDM)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="THUDM/CogVideoX-2b",
        description="نسخه سبک‌تر CogVideoX، مناسب Colab رایگان T4",
        license="Apache 2.0",
        max_seconds=6.0,
        default_resolution="720x480",
        fps=8,
        vram_gb_min=8.0,
        hf_url="https://huggingface.co/THUDM/CogVideoX-2b",
        tags=["lightweight", "colab-friendly"],
    ),
    VideoModel(
        id="wan-2.1-t2v-1.3b",
        name="Wan 2.1 T2V-1.3B (Alibaba)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="Wan-AI/Wan2.1-T2V-1.3B",
        description="مدل جدید Alibaba، فقط ۱.۳B، اجرا رو GPU مصرفی!",
        license="Apache 2.0",
        max_seconds=5.0,
        default_resolution="832x480",
        fps=16,
        vram_gb_min=8.0,
        hf_url="https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B",
        github_url="https://github.com/Wan-Video/Wan2.1",
        tags=["consumer-gpu", "new"],
    ),
    VideoModel(
        id="wan-2.1-t2v-14b",
        name="Wan 2.1 T2V-14B (Alibaba)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="Wan-AI/Wan2.1-T2V-14B",
        description="نسخه بزرگ Wan، کیفیت بالاتر",
        license="Apache 2.0",
        max_seconds=5.0,
        default_resolution="1280x720",
        fps=16,
        vram_gb_min=40.0,
        hf_url="https://huggingface.co/Wan-AI/Wan2.1-T2V-14B",
        tags=["hq"],
    ),
    VideoModel(
        id="wan-2.1-i2v-14b",
        name="Wan 2.1 I2V-14B (Alibaba)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="Wan-AI/Wan2.1-I2V-14B-720P",
        description="Wan Image-to-Video، انیمیت کردن عکس‌ها",
        license="Apache 2.0",
        max_seconds=5.0,
        default_resolution="1280x720",
        fps=16,
        vram_gb_min=40.0,
        hf_url="https://huggingface.co/Wan-AI/Wan2.1-I2V-14B-720P",
        tags=["i2v"],
    ),
    VideoModel(
        id="stable-video-diffusion",
        name="Stable Video Diffusion (StabilityAI)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="stabilityai/stable-video-diffusion-img2vid-xt",
        description="کلاسیک i2v از StabilityAI، ۲۵ فریم از یک عکس",
        license="Stability AI Community",
        max_seconds=4.0,
        default_resolution="1024x576",
        fps=7,
        vram_gb_min=16.0,
        hf_url="https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt",
        tags=["i2v", "classic"],
    ),
    VideoModel(
        id="animatediff",
        name="AnimateDiff (Motion Module)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="guoyww/animatediff-motion-adapter-v1-5-2",
        description="روی مدل‌های SD1.5 حرکت اضافه میکنه، سبک و سریع",
        license="Apache 2.0",
        max_seconds=2.0,
        default_resolution="512x512",
        fps=8,
        vram_gb_min=6.0,
        hf_url="https://huggingface.co/guoyww/animatediff-motion-adapter-v1-5-2",
        github_url="https://github.com/guoyww/AnimateDiff",
        tags=["lightweight", "sd1.5"],
    ),
    VideoModel(
        id="zeroscope-v2-xl",
        name="Zeroscope V2 XL",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="cerspense/zeroscope_v2_XL",
        description="مدل t2v قدیمی اما پایدار، مناسب upscale",
        license="CreativeML OpenRAIL-M",
        max_seconds=3.0,
        default_resolution="1024x576",
        fps=8,
        vram_gb_min=15.0,
        hf_url="https://huggingface.co/cerspense/zeroscope_v2_XL",
        tags=["classic"],
    ),
    VideoModel(
        id="zeroscope-v2-576w",
        name="Zeroscope V2 576w",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="cerspense/zeroscope_v2_576w",
        description="نسخه سبک zeroscope",
        license="CreativeML OpenRAIL-M",
        max_seconds=3.0,
        default_resolution="576x320",
        fps=8,
        vram_gb_min=7.0,
        hf_url="https://huggingface.co/cerspense/zeroscope_v2_576w",
        tags=["lightweight"],
    ),
    VideoModel(
        id="modelscope-t2v",
        name="ModelScope T2V (DAMO)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="damo-vilab/text-to-video-ms-1.7b",
        description="اولین مدل معروف اپن سورس t2v از Alibaba DAMO",
        license="CC-BY-NC 4.0",
        max_seconds=2.0,
        default_resolution="256x256",
        fps=8,
        vram_gb_min=6.0,
        hf_url="https://huggingface.co/damo-vilab/text-to-video-ms-1.7b",
        tags=["classic", "lightweight"],
    ),
    VideoModel(
        id="allegro",
        name="Allegro (RhymesAI)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="rhymes-ai/Allegro",
        description="مدل ۳B، ۶ ثانیه ویدیو ۷۲۰p، اپن‌سورس کامل",
        license="Apache 2.0",
        max_seconds=6.0,
        default_resolution="1280x720",
        fps=15,
        vram_gb_min=20.0,
        hf_url="https://huggingface.co/rhymes-ai/Allegro",
        tags=["hq", "apache2"],
    ),
    VideoModel(
        id="pyramid-flow",
        name="Pyramid Flow",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="rain1011/pyramid-flow-sd3",
        description="Autoregressive flow-matching model، تا ۱۰ ثانیه ۷۶۸p",
        license="MIT",
        max_seconds=10.0,
        default_resolution="1280x768",
        fps=24,
        vram_gb_min=12.0,
        hf_url="https://huggingface.co/rain1011/pyramid-flow-sd3",
        github_url="https://github.com/jy0205/Pyramid-Flow",
        tags=["long", "mit"],
    ),
    VideoModel(
        id="open-sora",
        name="Open-Sora 1.2 (HPC-AI)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="hpcai-tech/OpenSora-STDiT-v3",
        description="پروژه اپن سورس بازتولید Sora، تا ۱۶ ثانیه",
        license="Apache 2.0",
        max_seconds=16.0,
        default_resolution="1280x720",
        fps=24,
        vram_gb_min=24.0,
        hf_url="https://huggingface.co/hpcai-tech/OpenSora-STDiT-v3",
        github_url="https://github.com/hpcaitech/Open-Sora",
        tags=["sora-like", "long"],
    ),
    VideoModel(
        id="cogvideox-1.5-5b",
        name="CogVideoX 1.5 5B",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="THUDM/CogVideoX1.5-5B",
        description="نسل جدید CogVideoX، ۱۰ ثانیه، ۱۳۶۰x۷۶۸",
        license="CogVideoX License",
        max_seconds=10.0,
        default_resolution="1360x768",
        fps=16,
        vram_gb_min=20.0,
        hf_url="https://huggingface.co/THUDM/CogVideoX1.5-5B",
        tags=["hq", "new"],
    ),
    VideoModel(
        id="stepvideo-t2v",
        name="Step-Video-T2V",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="stepfun-ai/stepvideo-t2v",
        description="مدل ۳۰B پارامتری StepFun، sota در ماه‌های اخیر",
        license="MIT",
        max_seconds=8.0,
        default_resolution="992x544",
        fps=25,
        vram_gb_min=80.0,
        hf_url="https://huggingface.co/stepfun-ai/stepvideo-t2v",
        github_url="https://github.com/stepfun-ai/Step-Video-T2V",
        tags=["sota", "huge"],
    ),
    VideoModel(
        id="skyreels-v1",
        name="SkyReels V1 (Skywork)",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="Skywork/SkyReels-V1-Hunyuan-T2V",
        description="fine-tune انسان محور از HunyuanVideo، عالی برای پرتره",
        license="Skywork Community",
        max_seconds=4.0,
        default_resolution="960x544",
        fps=24,
        vram_gb_min=40.0,
        hf_url="https://huggingface.co/Skywork/SkyReels-V1-Hunyuan-T2V",
        tags=["human", "portrait"],
    ),
    VideoModel(
        id="ltx-video-0.9.5",
        name="LTX-Video 0.9.5",
        provider=Provider.HF_DIFFUSERS,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="Lightricks/LTX-Video-0.9.5",
        description="نسخه به‌روزشده LTX، سریع‌تر و بهتر",
        license="OpenRAIL",
        max_seconds=5.0,
        default_resolution="768x512",
        fps=25,
        vram_gb_min=10.0,
        hf_url="https://huggingface.co/Lightricks/LTX-Video-0.9.5",
        tags=["fast", "new"],
    ),
]


# =============================================================================
# OpenRouter Models
# ============================================================================
# نکته: OpenRouter در حال حاضر عمدتاً مدل‌های LLM و multimodal رو ارائه میده.
# مدل ویدیو خاصی مستقیم روی endpoint داره اما میتونیم از مدل‌های multimodal
# برای prompt enhancement و از routing برای Sora (وقتی اضافه بشه) استفاده کنیم.
OPENROUTER_MODELS: list[VideoModel] = [
    VideoModel(
        id="openrouter-prompt-enhancer",
        name="OpenRouter Prompt Enhancer (Claude/GPT)",
        provider=Provider.OPENROUTER,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="anthropic/claude-3.5-sonnet",
        description="از LLM قوی برای بهبود prompt ویدیو استفاده میکنه (متا-مدل)",
        license="Commercial",
        free=False,
        requires_api_key=True,
        api_key_env="OPENROUTER_API_KEY",
        homepage="https://openrouter.ai",
        tags=["prompt-helper", "utility"],
    ),
    VideoModel(
        id="openrouter-google-veo",
        name="Google Veo via OpenRouter (اگر فعال)",
        provider=Provider.OPENROUTER,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="google/veo-3",
        description="در صورت فعال بودن روی OpenRouter؛ در حال حاضر ممکنه در دسترس نباشه",
        license="Commercial",
        free=False,
        requires_api_key=True,
        api_key_env="OPENROUTER_API_KEY",
        max_seconds=8.0,
        default_resolution="1920x1080",
        homepage="https://openrouter.ai/models?modality=text-to-video",
        tags=["commercial", "sota", "if-available"],
    ),
]


# =============================================================================
# GitHub Open Source (non-HF or with special install)
# =============================================================================
GITHUB_MODELS: list[VideoModel] = [
    VideoModel(
        id="animateanyone",
        name="Animate Anyone (Moore-AnimateAnyone)",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://github.com/MooreThreads/Moore-AnimateAnyone",
        description="انیمیت کردن یک شخص از عکس بر اساس pose",
        license="Apache 2.0",
        max_seconds=5.0,
        default_resolution="512x768",
        fps=24,
        vram_gb_min=16.0,
        github_url="https://github.com/MooreThreads/Moore-AnimateAnyone",
        tags=["character", "pose"],
    ),
    VideoModel(
        id="dynamicrafter",
        name="DynamiCrafter",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://github.com/Doubiiu/DynamiCrafter",
        description="متحرک کردن عکس‌ها با prompt متنی",
        license="Apache 2.0",
        max_seconds=2.0,
        default_resolution="576x1024",
        fps=8,
        vram_gb_min=12.0,
        github_url="https://github.com/Doubiiu/DynamiCrafter",
        hf_url="https://huggingface.co/Doubiiu/DynamiCrafter",
        tags=["i2v"],
    ),
    VideoModel(
        id="tooncrafter",
        name="ToonCrafter",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.IMAGE_TO_VIDEO, VideoTaskType.VIDEO_TO_VIDEO],
        repo_or_endpoint="https://github.com/ToonCrafter/ToonCrafter",
        description="interpolate بین دو فریم کارتونی",
        license="Apache 2.0",
        max_seconds=2.0,
        default_resolution="512x320",
        fps=8,
        vram_gb_min=12.0,
        github_url="https://github.com/ToonCrafter/ToonCrafter",
        tags=["cartoon", "interpolation"],
    ),
    VideoModel(
        id="videocrafter2",
        name="VideoCrafter2",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="https://github.com/AILab-CVC/VideoCrafter",
        description="t2v از AILab-CVC",
        license="Apache 2.0",
        max_seconds=2.0,
        default_resolution="512x320",
        fps=10,
        vram_gb_min=10.0,
        github_url="https://github.com/AILab-CVC/VideoCrafter",
        tags=["classic"],
    ),
    VideoModel(
        id="magictime",
        name="MagicTime (Metamorphic)",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="https://github.com/PKU-YuanGroup/MagicTime",
        description="ویدیوهای metamorphic و time-lapse",
        license="Apache 2.0",
        max_seconds=2.0,
        default_resolution="512x512",
        fps=8,
        vram_gb_min=10.0,
        github_url="https://github.com/PKU-YuanGroup/MagicTime",
        tags=["timelapse"],
    ),
    VideoModel(
        id="show-1",
        name="Show-1",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="https://github.com/showlab/Show-1",
        description="pixel+latent hybrid برای کیفیت بالا",
        license="Apache 2.0",
        max_seconds=2.0,
        default_resolution="576x320",
        fps=8,
        vram_gb_min=14.0,
        github_url="https://github.com/showlab/Show-1",
        tags=["hybrid"],
    ),
    VideoModel(
        id="i2vgen-xl",
        name="I2VGen-XL",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://github.com/ali-vilab/VGen",
        description="i2v از Alibaba VGen، کیفیت بالا",
        license="MIT",
        max_seconds=2.0,
        default_resolution="1280x720",
        fps=8,
        vram_gb_min=12.0,
        github_url="https://github.com/ali-vilab/VGen",
        hf_url="https://huggingface.co/ali-vilab/i2vgen-xl",
        tags=["i2v", "hq"],
    ),
    VideoModel(
        id="latte",
        name="Latte (Latent Diffusion Transformer)",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="https://github.com/Vchitect/Latte",
        description="مدل transformer برای ویدیو، pre-Sora",
        license="Apache 2.0",
        max_seconds=2.0,
        default_resolution="512x512",
        fps=8,
        vram_gb_min=12.0,
        github_url="https://github.com/Vchitect/Latte",
        hf_url="https://huggingface.co/maxin-cn/Latte-1",
        tags=["transformer"],
    ),
    VideoModel(
        id="easyanimate",
        name="EasyAnimate v5 (Alibaba PAI)",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://github.com/aigc-apps/EasyAnimate",
        description="پایپلاین کامل t2v/i2v از Alibaba PAI",
        license="Apache 2.0",
        max_seconds=6.0,
        default_resolution="672x384",
        fps=8,
        vram_gb_min=16.0,
        github_url="https://github.com/aigc-apps/EasyAnimate",
        tags=["pipeline", "flexible"],
    ),
    VideoModel(
        id="cogvideo-github",
        name="CogVideo (GitHub reference)",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="https://github.com/THUDM/CogVideo",
        description="پیاده‌سازی رسمی + LoRA training",
        license="Apache 2.0",
        github_url="https://github.com/THUDM/CogVideo",
        tags=["reference"],
    ),
    VideoModel(
        id="opensora-plan",
        name="Open-Sora Plan (PKU-YuanGroup)",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.TEXT_TO_VIDEO],
        repo_or_endpoint="https://github.com/PKU-YuanGroup/Open-Sora-Plan",
        description="پروژه دیگر بازتولید Sora از PKU",
        license="MIT",
        max_seconds=10.0,
        default_resolution="720x480",
        fps=24,
        vram_gb_min=24.0,
        github_url="https://github.com/PKU-YuanGroup/Open-Sora-Plan",
        hf_url="https://huggingface.co/LanguageBind/Open-Sora-Plan-v1.3.0",
        tags=["sora-like"],
    ),
    VideoModel(
        id="cinemo",
        name="Cinemo",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://github.com/maxin-cn/Cinemo",
        description="کنترل دقیق حرکت در i2v",
        license="MIT",
        github_url="https://github.com/maxin-cn/Cinemo",
        tags=["i2v", "controllable"],
    ),
    VideoModel(
        id="motionctrl",
        name="MotionCtrl (Tencent ARC)",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://github.com/TencentARC/MotionCtrl",
        description="کنترل دوربین و object motion",
        license="Apache 2.0",
        github_url="https://github.com/TencentARC/MotionCtrl",
        tags=["camera-control"],
    ),
    VideoModel(
        id="framer",
        name="Framer (Interactive Frame Interpolation)",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.VIDEO_TO_VIDEO],
        repo_or_endpoint="https://github.com/aim-uofa/Framer",
        description="interpolate کنترل شده بین دو فریم",
        license="Apache 2.0",
        github_url="https://github.com/aim-uofa/Framer",
        tags=["interpolation"],
    ),
    VideoModel(
        id="mimicmotion",
        name="MimicMotion (Tencent)",
        provider=Provider.GITHUB,
        task_types=[VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://github.com/Tencent/MimicMotion",
        description="pose-guided human video generation",
        license="Apache 2.0",
        vram_gb_min=16.0,
        github_url="https://github.com/Tencent/MimicMotion",
        tags=["human", "pose"],
    ),
]


# =============================================================================
# Bonus: Commercial APIs (اختیاری، فقط با API key)
# =============================================================================
BONUS_API_MODELS: list[VideoModel] = [
    VideoModel(
        id="replicate-generic",
        name="Replicate (any video model)",
        provider=Provider.REPLICATE,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://api.replicate.com/v1/predictions",
        description="اجرای هر مدل ویدیویی روی Replicate (Kling, Luma, Runway, ...)",
        free=False,
        requires_api_key=True,
        api_key_env="REPLICATE_API_TOKEN",
        homepage="https://replicate.com/collections/text-to-video",
        tags=["commercial", "gateway"],
    ),
    VideoModel(
        id="fal-generic",
        name="fal.ai (any video model)",
        provider=Provider.FAL,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://fal.run/",
        description="fal.ai serverless برای LTX, Kling, Veo, Runway, Mochi",
        free=False,
        requires_api_key=True,
        api_key_env="FAL_KEY",
        homepage="https://fal.ai/models?categories=text-to-video",
        tags=["commercial", "gateway", "fast"],
    ),
    VideoModel(
        id="hf-inference-any",
        name="HuggingFace Inference API",
        provider=Provider.HUGGINGFACE,
        task_types=[VideoTaskType.TEXT_TO_VIDEO, VideoTaskType.IMAGE_TO_VIDEO],
        repo_or_endpoint="https://api-inference.huggingface.co/models/",
        description="اجرای مدل HF از راه دور بدون دانلود (اگر endpoint فعال باشه)",
        free=True,
        requires_api_key=True,
        api_key_env="HF_TOKEN",
        homepage="https://huggingface.co/tasks/text-to-video",
        tags=["hosted", "no-download"],
    ),
]


# =============================================================================
# Registry API
# =============================================================================
ALL_MODELS: list[VideoModel] = HF_MODELS + OPENROUTER_MODELS + GITHUB_MODELS + BONUS_API_MODELS


def get_all_models() -> list[VideoModel]:
    return ALL_MODELS


def get_model(model_id: str) -> Optional[VideoModel]:
    for m in ALL_MODELS:
        if m.id == model_id:
            return m
    return None


def filter_models(
    provider: Optional[Provider] = None,
    task: Optional[VideoTaskType] = None,
    free_only: bool = False,
    max_vram_gb: Optional[float] = None,
    tag: Optional[str] = None,
) -> list[VideoModel]:
    out = ALL_MODELS
    if provider:
        out = [m for m in out if m.provider == provider]
    if task:
        out = [m for m in out if task in m.task_types]
    if free_only:
        out = [m for m in out if m.free]
    if max_vram_gb is not None:
        out = [m for m in out if (m.vram_gb_min is None or m.vram_gb_min <= max_vram_gb)]
    if tag:
        out = [m for m in out if tag in m.tags]
    return out


def registry_stats() -> dict:
    return {
        "total": len(ALL_MODELS),
        "by_provider": {
            p.value: len([m for m in ALL_MODELS if m.provider == p]) for p in Provider
        },
        "by_task": {
            t.value: len([m for m in ALL_MODELS if t in m.task_types]) for t in VideoTaskType
        },
        "free": len([m for m in ALL_MODELS if m.free]),
        "colab_friendly": len([m for m in ALL_MODELS if m.vram_gb_min and m.vram_gb_min <= 16]),
    }
