from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class AIModel:
    provider: str
    model: str
    api_key: str
    base_url: str


def _env_any(*names: str) -> str | None:
    for name in names:
        val = os.getenv(name)
        if val and val.strip():
            return val.strip()
    return None


def _split_models(value: str | None, defaults: list[str]) -> list[str]:
    if value and value.strip():
        return [x.strip() for x in re.split(r'[,\n]+', value) if x.strip()]
    return defaults


def configured_models() -> list[AIModel]:
    """Build an ordered provider/model fallback chain from env vars.

    Supported envs:
    - GROQ_API_KEY + GROQ_MODELS
    - OPENROUTER_API_KEY + OPENROUTER_MODELS
    - HUGGINGFACE_API_KEY/HF_API_KEY + HUGGINGFACE_MODELS/HF_MODELS

    The order can be changed with AI_PROVIDER_ORDER=groq,openrouter,huggingface
    """
    order = _split_models(_env_any('AI_PROVIDER_ORDER'), ['groq', 'openrouter', 'huggingface'])
    models: list[AIModel] = []

    for provider in order:
        provider = provider.lower().strip()
        if provider == 'groq':
            key = _env_any('GROQ_API_KEY')
            if not key:
                continue
            for model in _split_models(_env_any('GROQ_MODELS', 'GROQ_MODEL'), [
                'llama-3.1-8b-instant',
                'llama-3.3-70b-versatile',
                'gemma2-9b-it',
            ]):
                models.append(AIModel('groq', model, key, 'https://api.groq.com/openai/v1'))

        elif provider in {'openrouter', 'open_router'}:
            key = _env_any('OPENROUTER_API_KEY', 'OPEN_ROUTER_API_KEY')
            if not key:
                continue
            for model in _split_models(_env_any('OPENROUTER_MODELS', 'OPENROUTER_MODEL', 'OPEN_ROUTER_MODELS'), [
                'google/gemini-2.0-flash-exp:free',
                'meta-llama/llama-3.2-3b-instruct:free',
                'qwen/qwen-2.5-7b-instruct:free',
            ]):
                models.append(AIModel('openrouter', model, key, 'https://openrouter.ai/api/v1'))

        elif provider in {'huggingface', 'hf', 'hugging_face'}:
            key = _env_any('HUGGINGFACE_API_KEY', 'HUGGING_FACE_API_KEY', 'HF_API_KEY', 'HF_TOKEN')
            if not key:
                continue
            for model in _split_models(_env_any('HUGGINGFACE_MODELS', 'HUGGING_FACE_MODELS', 'HF_MODELS', 'HF_MODEL'), [
                'Qwen/Qwen2.5-7B-Instruct',
                'mistralai/Mistral-7B-Instruct-v0.3',
                'HuggingFaceH4/zephyr-7b-beta',
            ]):
                models.append(AIModel('huggingface', model, key, 'https://router.huggingface.co/v1'))

    return models


class AIUnavailable(RuntimeError):
    pass


async def chat_json(messages: list[dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 1200) -> tuple[dict[str, Any], dict[str, str]]:
    """Call configured models in order until one returns valid JSON."""
    chain = configured_models()
    if not chain:
        raise AIUnavailable('هیچ API هوش مصنوعی در env تنظیم نشده است.')

    errors: list[str] = []
    async with httpx.AsyncClient(timeout=45) as client:
        for item in chain:
            try:
                headers = {
                    'Authorization': f'Bearer {item.api_key}',
                    'Content-Type': 'application/json',
                }
                if item.provider == 'openrouter':
                    headers['HTTP-Referer'] = 'https://game-lead-finder.onrender.com'
                    headers['X-Title'] = 'Game Lead Finder'

                payload = {
                    'model': item.model,
                    'messages': messages,
                    'temperature': temperature,
                    'max_tokens': max_tokens,
                    'response_format': {'type': 'json_object'},
                }
                r = await client.post(f'{item.base_url}/chat/completions', headers=headers, json=payload)

                # Some free/open models do not support OpenAI's response_format parameter.
                # Retry once without it before switching to the next model.
                if r.status_code in {400, 422} and 'response_format' in r.text:
                    payload.pop('response_format', None)
                    r = await client.post(f'{item.base_url}/chat/completions', headers=headers, json=payload)

                if r.status_code in {400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504}:
                    errors.append(f'{item.provider}/{item.model}: HTTP {r.status_code} {r.text[:180]}')
                    continue
                r.raise_for_status()
                data = r.json()
                content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
                parsed = parse_json_object(content)
                return parsed, {'provider': item.provider, 'model': item.model}
            except Exception as exc:
                errors.append(f'{item.provider}/{item.model}: {str(exc)[:220]}')
                continue

    raise AIUnavailable('همه مدل‌های AI خطا دادند یا به لیمیت خوردند: ' + ' | '.join(errors[-6:]))


def parse_json_object(text: str) -> dict[str, Any]:
    text = (text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?', '', text).strip()
        text = re.sub(r'```$', '', text).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    match = re.search(r'\{.*\}', text, re.S)
    if match:
        obj = json.loads(match.group(0))
        if isinstance(obj, dict):
            return obj
    raise ValueError('AI JSON معتبر برنگرداند')


async def generate_queries_with_ai(topic: str, city: str | None = None, *, max_queries: int = 8) -> tuple[list[str], dict[str, str] | None, str | None]:
    system = (
        'تو دستیار تولید عبارت جستجو برای پیدا کردن فروشنده‌های عمومی حوزه گیم هستی. '
        'فقط JSON معتبر بده. هیچ متن اضافه‌ای نده. '
        'هدف فقط پیدا کردن لینک‌های عمومی و قانونی است؛ نه اطلاعات خصوصی، نه دور زدن محدودیت‌ها.'
    )
    user = f'''
موضوع هدف: {topic}
شهر هدف: {city or 'ایران'}
حداکثر تعداد query: {max_queries}

برای Tavily queryهای فارسی/انگلیسی هدفمند بساز که فروشنده واقعی، پیج، کانال یا سایت پیدا کند.
حتماً چند query برای این منابع هم بساز اگر مرتبط بود:
site:t.me
site:instagram.com
site:balad.ir
site:divar.ir
site:sheypoor.com
site:torob.com

خروجی دقیقاً این JSON باشد:
{{"queries":["..."]}}
'''
    try:
        data, used = await chat_json([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ], temperature=0.25, max_tokens=900)
        queries = [str(q).strip() for q in data.get('queries', []) if str(q).strip()]
        return dedupe_keep_order(queries)[:max_queries], used, None
    except Exception as exc:
        return fallback_queries(topic, city, max_queries), None, str(exc)


def fallback_queries(topic: str, city: str | None, max_queries: int) -> list[str]:
    base = f'{topic} {city}'.strip() if city else topic
    queries = [
        base,
        f'site:t.me {base}',
        f'site:instagram.com {base}',
        f'site:balad.ir {base}',
        f'site:divar.ir {base}',
        f'site:sheypoor.com {base}',
        f'site:torob.com {base}',
        f'فروشگاه {base}',
        f'خرید {base}',
        f'{base} تلگرام اینستاگرام',
    ]
    return dedupe_keep_order(queries)[:max_queries]


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


async def judge_results_with_ai(topic: str, results: list[dict[str, Any]], *, min_score: int = 60) -> tuple[list[dict[str, Any]], dict[str, str] | None, str | None]:
    if not results:
        return [], None, None

    compact = []
    for i, r in enumerate(results):
        compact.append({
            'index': i,
            'title': r.get('title'),
            'url': r.get('url'),
            'description': (r.get('description') or '')[:500],
            'source': r.get('source'),
            'city': r.get('city'),
        })

    system = (
        'تو تحلیل‌گر لیدهای فروشندگان گیمینگ هستی. فقط JSON معتبر بده. '
        'لید خوب یعنی فروشنده/فروشگاه/کانال/پیج/آگهی عمومی مرتبط با بازی، جم، CP، UC، گیفت کارت، اکانت، کنسول یا گیم‌نت. '
        'مقاله، خبر، آموزش، دانلود، هک/چیت، محتوای نامرتبط یا لینک بدون نشانه فروش را رد کن.'
    )
    user = f'''
موضوع هدف: {topic}
حداقل امتیاز قابل ذخیره: {min_score}

نتایج جستجو:
{json.dumps(compact, ensure_ascii=False)}

برای هر نتیجه تصمیم بگیر آیا ارزش ذخیره در بانک اطلاعاتی دارد یا نه.
خروجی دقیقاً این JSON باشد:
{{"items":[{{"index":0,"is_lead":true,"category":"سی‌پی کالاف","score":85,"reason":"فروش مستقیم و لینک عمومی ارتباط دارد"}}]}}
score عدد 0 تا 100 باشد.
'''
    try:
        data, used = await chat_json([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ], temperature=0.1, max_tokens=1800)
        items = data.get('items', [])
        verdicts: list[dict[str, Any]] = []
        for item in items:
            try:
                idx = int(item.get('index'))
                score = int(item.get('score') or 0)
                is_lead = bool(item.get('is_lead')) and score >= min_score
                verdicts.append({
                    'index': idx,
                    'is_lead': is_lead,
                    'category': str(item.get('category') or '').strip() or None,
                    'score': max(0, min(score, 100)),
                    'reason': str(item.get('reason') or '').strip(),
                })
            except Exception:
                continue
        return verdicts, used, None
    except Exception as exc:
        # Safe fallback: do not save blindly when AI filter is unavailable.
        return [], None, str(exc)


def _openrouter_api_key() -> str | None:
    return _env_any('OPENROUTER_API_KEY', 'OPEN_ROUTER_API_KEY')


def _is_env_auto(value: str | None) -> bool:
    return not value or value.strip().lower() in {'auto', 'true', '1', 'yes'}


def _default_openrouter_web_fallback_models() -> list[str]:
    # Static fallback only. The app also auto-discovers current free models from OpenRouter.
    return [
        'deepseek/deepseek-chat-v3-0324:free',
        'deepseek/deepseek-r1-0528:free',
        'meta-llama/llama-3.3-70b-instruct:free',
        'mistralai/mistral-7b-instruct:free',
        'qwen/qwen3-14b:free',
        'qwen/qwen3-32b:free',
        'google/gemma-3-27b-it:free',
        'nousresearch/deephermes-3-llama-3-8b-preview:free',
    ]


def _models_from_env_for_web(api_key: str) -> list[AIModel]:
    raw = _env_any('OPENROUTER_WEB_MODELS')
    if _is_env_auto(raw):
        model_ids: list[str] = []
    else:
        model_ids = _split_models(raw, [])
    return [AIModel('openrouter_web_env', m, api_key, 'https://openrouter.ai/api/v1') for m in model_ids]


async def _discover_openrouter_free_models(client: httpx.AsyncClient, api_key: str, limit: int = 18) -> list[AIModel]:
    """Fetch currently available free OpenRouter models because free slugs change often."""
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    try:
        r = await client.get('https://openrouter.ai/api/v1/models', headers=headers)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return []

    discovered: list[tuple[int, str]] = []
    for model in payload.get('data', []) or []:
        model_id = model.get('id')
        if not model_id or ':free' not in model_id:
            continue
        pricing = model.get('pricing') or {}
        prompt_price = str(pricing.get('prompt', '0'))
        completion_price = str(pricing.get('completion', '0'))
        if prompt_price not in {'0', '0.0', '0.000000'} or completion_price not in {'0', '0.0', '0.000000'}:
            continue
        context = int(model.get('context_length') or 0)
        discovered.append((context, model_id))

    discovered.sort(reverse=True)
    out: list[AIModel] = []
    seen: set[str] = set()
    for _, model_id in discovered:
        if model_id in seen:
            continue
        seen.add(model_id)
        out.append(AIModel('openrouter_web_auto', model_id, api_key, 'https://openrouter.ai/api/v1'))
        if len(out) >= limit:
            break
    return out


def _merge_model_chain(*chains: list[AIModel]) -> list[AIModel]:
    out: list[AIModel] = []
    seen: set[str] = set()
    for chain in chains:
        for item in chain:
            if item.model in seen:
                continue
            seen.add(item.model)
            out.append(item)
    return out


def _brief_openrouter_error(model: str, status_code: int | None = None, text: str | None = None, exc: Exception | None = None) -> str:
    if exc:
        return f'{model}: {str(exc)[:160]}'
    message = text or ''
    try:
        data = json.loads(message)
        message = ((data.get('error') or {}).get('message') or message)
    except Exception:
        pass
    message = re.sub(r'"user_id"\s*:\s*"[^"]+"', '', message)
    message = re.sub(r'user_[A-Za-z0-9_\-]+', 'user_***', message)
    message = re.sub(r'\s+', ' ', message).strip()
    if status_code == 429:
        return f'{model}: لیمیت/شلوغی موقت مدل'
    if status_code == 404:
        return f'{model}: این مدل endpoint رایگان فعال ندارد'
    if status_code in {401, 403}:
        return f'{model}: کلید OpenRouter یا دسترسی Web Search مشکل دارد'
    return f'{model}: HTTP {status_code} {message[:160]}'


async def openrouter_web_search_leads(
    *,
    topic: str,
    city: str | None = None,
    max_results: int = 10,
    min_score: int = 60,
) -> tuple[list[dict[str, Any]], dict[str, str] | None, str | None]:
    """Use OpenRouter's web plugin directly. No Tavily or external search API."""
    api_key = _openrouter_api_key()
    if not api_key:
        return [], None, 'OPENROUTER_API_KEY تنظیم نشده است.'

    max_results = min(max(max_results, 1), 30)
    min_score = min(max(min_score, 0), 100)
    city_text = city or 'ایران'

    system = (
        'تو یک عامل جستجوی وب برای پیدا کردن لیدهای عمومی و قانونی حوزه گیم هستی. '
        'باید از جستجوی وب استفاده کنی و فقط فروشنده‌ها/فروشگاه‌ها/پیج‌ها/کانال‌ها/آگهی‌های واقعی و مرتبط را برگردانی. '
        'اطلاعات خصوصی یا مخفی را حدس نزن. اگر شماره/سایت/اینستاگرام/تلگرام عمومی ندیدی، null بگذار. '
        'هیچ لینک خیالی نساز. فقط JSON معتبر بده.'
    )
    user = f'''
موضوع جستجو: {topic}
شهر/محدوده: {city_text}
حداکثر لید قابل برگشت: {max_results}
حداقل امتیاز ذخیره: {min_score}

وب را برای فروشنده‌های عمومی مرتبط جستجو کن. منابع خوب:
- سایت‌های فروشگاهی
- کانال‌های عمومی تلگرام
- پیج‌های عمومی اینستاگرام
- آگهی‌های عمومی دیوار/شیپور
- بلد/نشان/گوگل‌مپ/ترب در صورت مرتبط بودن

هر نتیجه باید فروشنده/کسب‌وکار/صفحه قابل ارتباط باشد، نه مقاله، خبر، دانلود، آموزش، هک یا چیت.

خروجی دقیقاً JSON با این ساختار باشد:
{{
  "leads": [
    {{
      "title": "نام فروشنده یا عنوان صفحه",
      "url": "لینک عمومی اصلی",
      "description": "خلاصه کوتاه فعالیت",
      "category": "مثلاً سی‌پی کالاف، اکانت، گیفت کارت، فروشگاه کنسول، گیم‌نت",
      "city": "شهر یا null",
      "phone": "شماره عمومی یا null",
      "website": "سایت رسمی یا null",
      "instagram": "لینک پیج یا null",
      "telegram": "لینک کانال/آیدی یا null",
      "address": "آدرس عمومی یا null",
      "score": 0,
      "reason": "چرا این لید مرتبط است"
    }}
  ]
}}
'''

    errors: list[str] = []
    async with httpx.AsyncClient(timeout=75) as client:
        env_chain = _models_from_env_for_web(api_key)
        auto_limit = int(os.getenv('OPENROUTER_AUTO_MODEL_LIMIT', '18'))
        auto_chain = await _discover_openrouter_free_models(client, api_key, limit=auto_limit)
        fallback_chain = [AIModel('openrouter_web_fallback', m, api_key, 'https://openrouter.ai/api/v1') for m in _default_openrouter_web_fallback_models()]
        chain = _merge_model_chain(env_chain, auto_chain, fallback_chain)
        if not chain:
            return [], None, 'هیچ مدل رایگان قابل استفاده‌ای از OpenRouter پیدا نشد. OPENROUTER_WEB_MODELS را روی auto بگذار.'

        for item in chain:
            try:
                headers = {
                    'Authorization': f'Bearer {item.api_key}',
                    'Content-Type': 'application/json',
                    'HTTP-Referer': 'https://game-lead-finder.onrender.com',
                    'X-Title': 'Game Lead Finder',
                }
                payload = {
                    'model': item.model,
                    'messages': [
                        {'role': 'system', 'content': system},
                        {'role': 'user', 'content': user},
                    ],
                    'temperature': 0.15,
                    'max_tokens': 2500,
                    'plugins': [{'id': 'web', 'max_results': max_results}],
                    'response_format': {'type': 'json_object'},
                }
                r = await client.post(f'{item.base_url}/chat/completions', headers=headers, json=payload)
                if r.status_code in {400, 422} and 'response_format' in r.text:
                    payload.pop('response_format', None)
                    r = await client.post(f'{item.base_url}/chat/completions', headers=headers, json=payload)
                if r.status_code in {400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504}:
                    errors.append(_brief_openrouter_error(item.model, r.status_code, r.text))
                    continue
                r.raise_for_status()
                data = r.json()
                content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
                parsed = parse_json_object(content)
                raw_leads = parsed.get('leads', [])
                leads: list[dict[str, Any]] = []
                for lead in raw_leads:
                    if not isinstance(lead, dict):
                        continue
                    score = int(lead.get('score') or 0)
                    if score < min_score:
                        continue
                    url = str(lead.get('url') or '').strip()
                    title = str(lead.get('title') or '').strip()
                    if not url or not title:
                        continue
                    leads.append({
                        'source': 'openrouter_web_ai',
                        'entity_type': 'ai_web_lead',
                        'title': title[:500],
                        'url': url,
                        'description': lead.get('description'),
                        'category': lead.get('category'),
                        'city': lead.get('city') or city,
                        'phone': lead.get('phone'),
                        'website': lead.get('website'),
                        'instagram': lead.get('instagram'),
                        'telegram': lead.get('telegram'),
                        'address': lead.get('address'),
                        'score': max(0, min(score, 100)),
                        'notes': 'AI Web Search: ' + str(lead.get('reason') or '').strip(),
                        'keyword': topic,
                        'query': topic,
                    })
                return leads[:max_results], {'provider': item.provider, 'model': item.model}, None
            except Exception as exc:
                errors.append(_brief_openrouter_error(item.model, exc=exc))
                continue

    return [], None, 'مدل‌های رایگان OpenRouter فعلاً جواب ندادند. خلاصه خطاها: ' + ' | '.join(errors[-8:])
