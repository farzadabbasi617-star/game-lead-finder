"""Influencer Finder — collector: discovers gaming influencers via web search."""
from __future__ import annotations

import json
import re
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import CrawlerRun
from app.repository import start_run, finish_run
from app.influencer.models import Influencer
from app.influencer.scoring import compute_influencer_score

# ── Search queries ─────────────────────────────────────────────────

INSTAGRAM_QUERIES = [
    'بهترین پیج‌های گیمینگ اینستاگرام ایران',
    'اینفلوئنسر گیمینگ اینستاگرام فارسی',
    'پیج گیم پلی اینستاگرام ایرانی',
    'بهترین گیمرهای ایرانی اینستاگرام',
    'پیج ریویو بازی اینستاگرام',
    'site:instagram.com گیمینگ فارسی',
    'site:instagram.com گیمر ایرانی',
    'site:instagram.com call of duty فارسی',
    'site:instagram.com پابجی ایران',
    'site:instagram.com استریمر ایرانی',
    'بهترین ریلز گیمینگ ایران',
    'پیج فروش اکانت کلش اینستاگرام',
    'پیج فروش سی پی کالاف اینستاگرام',
    'پیج فروش یوسی پابجی اینستاگرام',
]

TELEGRAM_QUERIES = [
    'بهترین کانال‌های گیمینگ تلگرام',
    'کانال گیم پلی تلگرام فارسی',
    'کانال ریویو بازی تلگرام',
    'کانال استریمر تلگرام',
    'site:t.me گیمینگ',
    'site:t.me گیمر',
    'site:t.me گیم پلی',
    'site:t.me call of duty',
    'site:t.me pubg',
    'site:t.me valorant',
    'site:t.me فروش اکانت بازی',
    'site:t.me فروش سی پی',
    'site:t.me فروش یوسی',
]


# ── Username/profile extraction ────────────────────────────────────

def extract_instagram_profiles(text: str) -> list[dict]:
    results = []
    seen = set()
    for m in re.finditer(r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,60})/?', text, re.I):
        username = m.group(1).lower()
        if username not in seen and username not in {'p', 'reel', 'explore', 'accounts', 'stories', 'reels'}:
            seen.add(username)
            results.append({'platform': 'instagram', 'username': username, 'url': f'https://instagram.com/{username}'})
    for m in re.finditer(r'@([A-Za-z0-9_.]{2,60})', text):
        username = m.group(1).lower()
        if username not in seen and not username.startswith(' ') and '.' not in username[-4:]:
            seen.add(username)
            results.append({'platform': 'instagram', 'username': username, 'url': f'https://instagram.com/{username}'})
    return results


def extract_telegram_channels(text: str) -> list[dict]:
    results = []
    seen = set()
    skip = {'joinchat', 'addstickers', 'addemoji', 'share', 'login', 's', 'c', 'iv', 'proxy', 'blog'}
    for m in re.finditer(r'(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})', text):
        username = m.group(1).lower()
        if username not in seen and username not in skip:
            seen.add(username)
            results.append({'platform': 'telegram', 'username': username, 'url': f'https://t.me/{username}'})
    return results


# ── Direct web search (not through run_openrouter_web_search) ──────

async def _direct_web_search(query: str, max_results: int = 8) -> list[dict]:
    """Search the web directly and return raw results with text for extraction."""
    settings = get_settings()
    results = []

    # Try OpenRouter with web plugin
    if settings.openrouter_api_key:
        try:
            results = await _search_openrouter(query, settings.openrouter_api_key, max_results)
            if results:
                return results
        except Exception:
            pass

    # Try Tavily
    if settings.tavily_api_key:
        try:
            results = await _search_tavily(query, settings.tavily_api_key, max_results)
            if results:
                return results
        except Exception:
            pass

    # Try Groq (no web search, but can generate suggestions)
    if settings.groq_api_key:
        try:
            results = await _search_groq(query, settings.groq_api_key, max_results)
            if results:
                return results
        except Exception:
            pass

    return results


async def _search_openrouter(query: str, api_key: str, max_results: int) -> list[dict]:
    """Use OpenRouter web search plugin."""
    # First discover free models
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    async with httpx.AsyncClient(timeout=25) as client:
        # Get free models
        try:
            r = await client.get('https://openrouter.ai/api/v1/models', headers=headers)
            models = []
            for m in (r.json().get('data') or []):
                mid = m.get('id', '')
                if ':free' in mid:
                    pricing = m.get('pricing') or {}
                    if str(pricing.get('prompt', '1')) == '0' and str(pricing.get('completion', '1')) == '0':
                        models.append(mid)
            models = models[:4]  # limit to 4 models
        except Exception:
            models = ['meta-llama/llama-3.3-70b-instruct:free', 'qwen/qwen3-14b:free']

        if not models:
            models = ['meta-llama/llama-3.3-70b-instruct:free']

        system = (
            'تو جستجوگر وب هستی. عبارت جستجو شده رو تو وب سرچ کن و نتایج واقعی رو برگردون. '
            'هر نتیجه شامل عنوان، لینک، و توضیح کوتاه باشه. فقط JSON معتبر بده.'
        )
        user = f'عبارت جستجو: {query}\n\nنتایج جستجوی وب رو برگردون. خروجی JSON:\n{{"results":[{{"title":"...","url":"...","description":"..."}}]}}'

        for model in models:
            try:
                headers = {
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                    'HTTP-Referer': 'https://game-lead-finder.onrender.com',
                    'X-Title': 'Game Lead Finder',
                }
                payload = {
                    'model': model,
                    'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                    'temperature': 0.15,
                    'max_tokens': 2000,
                    'plugins': [{'id': 'web', 'max_results': max_results}],
                }
                r = await client.post('https://openrouter.ai/api/v1/chat/completions', headers=headers, json=payload)
                if r.status_code >= 400:
                    continue
                content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
                # Parse JSON
                try:
                    parsed = json.loads(content)
                except json.JSON.JSONDecodeError:
                    match = re.search(r'\{.*\}', content, re.S)
                    if match:
                        parsed = json.loads(match.group(0))
                    else:
                        continue
                raw = parsed.get('results') or parsed.get('leads') or []
                out = []
                for item in raw:
                    if isinstance(item, dict) and item.get('url'):
                        out.append({'title': item.get('title', ''), 'url': item.get('url', ''), 'description': item.get('description', '')})
                if out:
                    return out[:max_results]
            except Exception:
                continue
    return []


async def _search_tavily(query: str, api_key: str, max_results: int) -> list[dict]:
    """Use Tavily search API."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post('https://api.tavily.com/search', json={
            'api_key': api_key, 'query': query, 'max_results': max_results,
            'include_answer': False, 'include_raw_content': False,
        })
        if r.status_code != 200:
            return []
        data = r.json()
        return [{'title': r.get('title', ''), 'url': r.get('url', ''), 'description': r.get('content', '')[:300]} for r in (data.get('results') or [])]


async def _search_groq(query: str, api_key: str, max_results: int) -> list[dict]:
    """Use Groq to generate known influencer suggestions."""
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    system = 'تو لیست اینفلوئنسرهای گیمینگ ایرانی رو میشناسی. فقط JSON معتبر بده.'
    user = f'برای عبارت "{query}" لیست اینفلوئنسرها/کانال‌ها/پیج‌های واقعی و عمومی رو برگردون.\nهر کدوم شامل اسم، لینک واقعی، و توضیح کوتاه باشه.\nخروجی: {{"results":[{{"title":"...","url":"...","description":"..."}}]}}'
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post('https://api.groq.com/openai/v1/chat/completions', headers=headers, json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'temperature': 0.2, 'max_tokens': 1500,
            })
            if r.status_code != 200:
                return []
            content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', content, re.S)
                if match:
                    parsed = json.loads(match.group(0))
                else:
                    return []
            raw = parsed.get('results') or []
            return [{'title': i.get('title', ''), 'url': i.get('url', ''), 'description': i.get('description', '')} for i in raw if isinstance(i, dict) and i.get('url')][:max_results]
    except Exception:
        return []


# ── Scraping ───────────────────────────────────────────────────────

def _parse_count(raw: str) -> int | None:
    raw = raw.strip().replace(',', '')
    mult = 1
    if raw.upper().endswith('K'):
        mult = 1000; raw = raw[:-1]
    elif raw.upper().endswith('M'):
        mult = 1_000_000; raw = raw[:-1]
    try:
        return int(float(raw) * mult)
    except (ValueError, TypeError):
        return None


async def scrape_instagram_profile(username: str) -> dict:
    url = f'https://www.instagram.com/{username}/'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'en-US,en;q=0.9'}
    result = {'username': username, 'url': f'https://instagram.com/{username}', 'display_name': username}
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return result
            html = r.text[:1_000_000]
        title_m = re.search(r'og:title["\s]+content="([^"]+)"', html)
        if title_m:
            result['display_name'] = title_m.group(1).strip().split(' (')[0].split(' ')[0] if title_m else username
        desc_m = re.search(r'og:description["\s]+content="([^"]+)"', html)
        if desc_m:
            result['bio'] = desc_m.group(1).strip()
        meta_m = re.search(r'([\d,.]+[KkMm]?)\s+Followers', html, re.I)
        if meta_m:
            result['followers'] = _parse_count(meta_m.group(1))
        following_m = re.search(r'([\d,.]+[KkMm]?)\s+Following', html, re.I)
        if following_m:
            result['following'] = _parse_count(following_m.group(1))
        posts_m = re.search(r'([\d,.]+[KkMm]?)\s+Posts', html, re.I)
        if posts_m:
            result['posts_count'] = _parse_count(posts_m.group(1))
    except Exception:
        pass
    return result


async def scrape_telegram_channel(username: str) -> dict:
    url = f'https://t.me/s/{username}'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8'}
    result = {'username': username, 'url': f'https://t.me/{username}', 'display_name': username}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return result
            html = r.text[:2_000_000]
        title_m = re.search(r'og:title["\s]+content="([^"]+)"', html)
        if title_m:
            result['display_name'] = title_m.group(1).strip()
        desc_m = re.search(r'og:description["\s]+content="([^"]+)"', html)
        if desc_m:
            result['bio'] = desc_m.group(1).strip()
        for pat in [r'([\d,.\s]+)\s*(?:member|subscriber)s?', r'([\d,.\s]+)\s*عضو']:
            m = re.search(pat, html, re.I)
            if m:
                num = re.sub(r'[^\d]', '', m.group(1))
                if num and int(num) > 5:
                    result['followers'] = int(num)
                    break
        views = []
        for m in re.finditer(r'class="tgme_widget_message_views"[^>]*>([^<]+)<', html):
            raw = m.group(1).strip().replace(',', '').replace('.', '')
            mult = 1
            if raw.upper().endswith('K'):
                mult = 1000; raw = raw[:-1]
            elif raw.upper().endswith('M'):
                mult = 1_000_000; raw = raw[:-1]
            try:
                val = int(float(raw.strip()) * mult)
                if val > 0:
                    views.append(val)
            except (ValueError, TypeError):
                pass
        if views:
            result['avg_views'] = round(sum(views) / len(views), 1)
            if result.get('followers') and result['followers'] > 0:
                result['engagement_rate'] = round((result['avg_views'] / result['followers']) * 100, 2)
    except Exception:
        pass
    return result


async def _scrape(profile: dict) -> dict:
    if profile['platform'] == 'telegram':
        return await scrape_telegram_channel(profile['username'])
    elif profile['platform'] == 'instagram':
        return await scrape_instagram_profile(profile['username'])
    return profile


# ── Niche and game tag detection ───────────────────────────────────

def _detect_niche(text: str) -> str | None:
    rules = {
        'استریمر': ['استریم', 'stream', 'استریمر', 'streamer', 'لایو', 'live'],
        'آنباکسینگ': ['آنباکس', 'unboxing', 'جعبه‌گشایی'],
        'ریویو بازی': ['ریویو', 'review', 'نقد', 'بررسی بازی'],
        'گیم پلی': ['گیمپلی', 'gameplay', 'گیم پلی', 'پارت'],
        'فروش اکانت/آیتم': ['فروش', 'خرید', 'اکانت', 'سی پی', 'یوسی', 'جم', 'گیفت کارت'],
        'تکنولوژی': ['تکنولوژی', 'tech', 'تِک', 'گجت', 'لپتاپ'],
        'خبر و آموزش': ['خبر', 'آموزش', 'ترفند', 'tip', 'تریلر', 'trailer'],
    }
    blob = text.lower().replace('ي', 'ی').replace('ك', 'ک')
    best, best_hits = None, 0
    for niche, terms in rules.items():
        hits = sum(1 for t in terms if t in blob)
        if hits > best_hits:
            best_hits = hits
            best = niche
    return best


def _detect_game_tags(text: str) -> str:
    tag_map = {
        'کالاف': 'کالاف', 'call of duty': 'کالاف', 'cod': 'کالاف', 'warzone': 'کالاف',
        'پابجی': 'پابجی', 'pubg': 'پابجی',
        'ولورانت': 'ولورانت', 'valorant': 'ولورانت',
        'فورتنایت': 'فورتنایت', 'fortnite': 'فورتنایت',
        'کلش': 'کلش', 'clash': 'کلش',
        'فری فایر': 'فری فایر', 'free fire': 'فری فایر',
        'گی‌تی‌ای': 'GTA', 'gta': 'GTA',
        'ماینکرفت': 'ماینکرفت', 'minecraft': 'ماینکرفت',
        'فیفا': 'فیفا', 'efootball': 'فیفا',
        'ایپکس': 'ایپکس', 'apex': 'ایپکس',
    }
    blob = text.lower().replace('ي', 'ی').replace('ك', 'ک')
    tags = set()
    for kw, tag in tag_map.items():
        if kw in blob:
            tags.add(tag)
    return ','.join(tags) if tags else ''


def _has_persian(text: str) -> bool:
    return bool(re.search(r'[\u0600-\u06FF]', text))


# ── Main collector ─────────────────────────────────────────────────

async def discover_influencers(
    db: Session,
    *,
    platform: str = 'both',
    queries: list[str] | None = None,
    max_results_per_query: int = 8,
    min_collab_score: int = 15,
) -> dict:
    """Run web searches to discover gaming influencers."""
    search_queries = []
    if platform in ('instagram', 'both'):
        search_queries.extend(queries or INSTAGRAM_QUERIES[:5])
    if platform in ('telegram', 'both'):
        search_queries.extend(queries or TELEGRAM_QUERIES[:5])

    summary = {'queries_run': 0, 'profiles_found': 0, 'new_saved': 0, 'duplicates': 0, 'errors': []}
    all_profiles: list[dict] = []

    for query in search_queries:
        run = start_run(db, 'influencer_discovery', query)
        summary['queries_run'] += 1
        try:
            # Direct web search — returns raw results
            raw_results = await _direct_web_search(query, max_results=max_results_per_query)
            for item in raw_results:
                text = f"{item.get('title', '')} {item.get('url', '')} {item.get('description', '')}"
                all_profiles.extend(extract_instagram_profiles(text))
                all_profiles.extend(extract_telegram_channels(text))
            finish_run(db, run, len(raw_results), 0)
        except Exception as exc:
            summary['errors'].append({'query': query, 'error': str(exc)[:200]})
            finish_run(db, run, 0, 0, str(exc)[:200])

    # Deduplicate
    seen_urls: set[str] = set()
    unique_profiles: list[dict] = []
    for p in all_profiles:
        if p['url'] not in seen_urls:
            seen_urls.add(p['url'])
            unique_profiles.append(p)

    summary['profiles_found'] = len(unique_profiles)

    # Scrape and save each profile
    for profile in unique_profiles:
        url = profile['url']
        existing = db.scalar(select(Influencer).where(Influencer.profile_url == url))
        if existing:
            summary['duplicates'] += 1
            info = await _scrape(profile)
            if info.get('followers') and (not existing.followers or info['followers'] > existing.followers):
                existing.followers = info['followers']
            if info.get('avg_views') and (not existing.avg_views or info['avg_views'] > existing.avg_views):
                existing.avg_views = info['avg_views']
            if info.get('engagement_rate'):
                existing.engagement_rate = info['engagement_rate']
            existing.last_seen = datetime.utcnow()
            compute_influencer_score(existing)
            db.add(existing)
            continue

        info = await _scrape(profile)
        blob = f"{info.get('display_name', '')} {info.get('bio', '')} {profile.get('username', '')}"

        inf = Influencer(
            platform=profile['platform'],
            profile_url=url,
            username=profile.get('username'),
            display_name=info.get('display_name') or profile.get('username') or 'نامشخص',
            bio=info.get('bio'),
            followers=info.get('followers'),
            following=info.get('following'),
            posts_count=info.get('posts_count'),
            avg_views=info.get('avg_views'),
            engagement_rate=info.get('engagement_rate'),
            niche=_detect_niche(blob),
            game_tags=_detect_game_tags(blob),
            language='fa' if _has_persian(blob) else 'en',
            source='search',
            status='discovered',
        )
        compute_influencer_score(inf)

        # ALWAYS save — don't filter by score
        db.add(inf)
        summary['new_saved'] += 1

    db.commit()
    return summary
