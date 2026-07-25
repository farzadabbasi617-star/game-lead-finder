"""Influencer Finder — collector: discovers gaming influencers via web search.

بهبودها نسبت به نسخه قبلی:
- رفع باگ json.JSON.JSONDecodeError
- اضافه کردن جستجوی SerpAPI / Google CSE / Brave / Serper / SearchAPI
- Fallback به DuckDuckGo HTML (بدون API key)
- Seed list از پیج/کانال‌های شناخته‌شده گیمینگ ایرانی
- Logging دقیق برای دیباگ
- برداشتن فیلتر سختگیرانه language='fa' تا استخراج بهتر بشه
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from urllib.parse import quote_plus

import asyncio
import httpx
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repository import start_run, finish_run
from app.influencer.models import Influencer
from app.influencer.scoring import compute_influencer_score
from app.influencer.activity import check_activity, ActivityStatus, ACTIVITY_WINDOW_DAYS

logger = logging.getLogger('influencer.collector')
logger.setLevel(logging.INFO)


# ─── Search queries ────────────────────────────────────────────────

INSTAGRAM_QUERIES = [
    'بهترین پیج گیمینگ اینستاگرام ایرانی',
    'اینفلوئنسر گیمینگ اینستاگرام فارسی',
    'پیج گیم پلی اینستاگرام ایران',
    'گیمر ایرانی اینستاگرام',
    'ریویو بازی اینستاگرام فارسی',
    'استریمر ایرانی اینستاگرام',
    'site:instagram.com گیمینگ ایران',
    'site:instagram.com گیمر ایرانی',
    'site:instagram.com پابجی موبایل ایران',
    'site:instagram.com کالاف دیوتی موبایل ایرانی',
    'site:instagram.com فروش یوسی پابجی',
    'site:instagram.com فروش سی پی کالاف',
    'site:instagram.com فروش جم کلش',
    'site:instagram.com فری فایر ایرانی',
    'پیج ریلز گیمینگ ایرانی اینستاگرام',
    'گیم نت ایرانی اینستاگرام',
]

TELEGRAM_QUERIES = [
    'بهترین کانال گیمینگ تلگرام ایرانی',
    'کانال گیم پلی تلگرام فارسی',
    'کانال ریویو بازی تلگرام ایران',
    'کانال استریمر گیم تلگرام',
    'site:t.me گیمینگ ایران',
    'site:t.me گیمر فارسی',
    'site:t.me پابجی موبایل',
    'site:t.me کالاف دیوتی موبایل',
    'site:t.me فروش یوسی',
    'site:t.me فروش سی پی',
    'site:t.me فری فایر',
    'site:t.me کلش رویال',
    'کانال تلگرام آموزش بازی ایرانی',
    'کانال تلگرام فروش اکانت بازی',
]


# ─── Seed list — پیج/کانال‌های شناخته‌شده گیمینگ ایرانی ──────────────
# لیست گسترده — قبل از ذخیره چک میشه که فعال باشن (پست ۳۰ روز اخیر)
# منابع: canalha.ir, shabakeh-mag, streamscharts, atlanticcouncil report

SEED_INSTAGRAM = [
    # رسانه‌های خبری/تحلیلی گیمینگ
    'gamefa', 'gamefa.official', 'zoomg_online', 'zoomgame', 'bazimag',
    'digital_daily', 'digikala_game', 'digipubg', 'shahrsakhtafzar',
    'gamingmaster.ir', 'par30game', 'game2download', 'valdogaming',
    'gameinfo.ir', 'gaming.tv.ir', 'gamefun.ir', 'gamerland.ir',
    'gamer.gamer.gamer', 'oddgamer', 'gameon_ir',
    # پیج‌های استریمر/گیمر
    'melinabeleyk', 'yaser_gamer', 'yaser.gamer', 'amirphanthom',
    'amireyzed', 'keoxer.official', 'nichoqu', 'x1rony',
    'iranian.gamer.official', 'gamer_irani', 'gamer.iran',
    'persiangamer.official', 'gamer.ir', 'gamerandme', 'irangamer.ir',
    # بازی-محور: PUBG/CoD/Free Fire/Clash/Valorant/FIFA/LoL/MLBB
    'iran_pubg', 'pubg.iran.official', 'pubg_iran_official',
    'pubgmobile.iran', 'pubg_mobile_ir', 'callofdutyiran',
    'callofduty.iran', 'codm.iran', 'cod_mobile_iran',
    'freefireiran', 'freefireir', 'freefire.iran.official',
    'clashiran', 'clashroyale.iran', 'clash_of_clans_iran',
    'valorantiran', 'valorant.iran.official', 'lol_iran', 'lolir.official',
    'iranfifa', 'fifagamer.ir', 'fifa_iran.official', 'efootball.iran',
    'mobilelegends.iran', 'mlbb_iran_official', 'apex.iran',
    'fortniteiran', 'fortnite.persian', 'gtav.iran', 'gta_iran_official',
    'minecraft.iran', 'minecraftiran.ir', 'wildrift.iran',
    # کنسول‌ها و PC
    'psniraan', 'ps5.iran', 'ps5iran.official', 'ps.iran',
    'xbox_iran', 'xboxiran.official', 'nintendo_iran', 'nintendoswitch.iran',
    'pc_gamer_iran', 'pcgamer.iran', 'pcgame.ir',
    # فروشگاه/گیم نت (اسپانسر پتانسیل)
    'iran_game_store', 'games4iran', 'gamenet_iran', 'gamenetiran.ir',
    'game_shop_iran', 'gameshop.iran', 'gamestore.ir',
    'mobile_gamer_iran', 'mobilegame.iran',
]

SEED_TELEGRAM = [
    # ── کانال‌های پرمخاطب رسانه‌ای (تایید شده) ──
    'gamefa_official', 'par30game', 'bazimag', 'valdogaming',
    'gamingmaster', 'gamersparadise', 'game2download', 'digikala_game',
    'gaminginfo_ir', 'shahrsakht', 'sarzaminedanlod', 'gamenews_ir',
    'zoomg_online', 'zoomgame_ir', 'gamefa', 'gamefa_adv', 'commentsgamefa',
    # ── کانال‌های بازی-محور ──
    'call_of_duty_fans', 'IRPUBG', 'callofdutypr2', 'codm_iran',
    'codmobile_iran', 'iran_pubg', 'pubg2iran', 'pubgmobile_iran',
    'freefire_ir', 'freefire_iran_official', 'clashroyale_iran',
    'clashofclans_iran', 'valorant_iran', 'valorantfa',
    'fifa_iran', 'fifa_iran_official', 'efootball_ir',
    'mlbb_iran', 'mobilelegends_iran', 'lol_iran_official',
    'apex_iran', 'fortnite_iran', 'gta_iran', 'minecraft_ir',
    'residentevil_iran', 'gaming_raifle', 'irlol',
    'ClashTV', 'codmamadreza',
    # ── فروش/گیم استور ──
    'ps_iran_ch', 'xbox_iranian', 'gamestore_iran', 'gamenet_channel',
    'gameon_ir', 'game_island_ch', 'codtvchannel', 'gaming_iran',
    'gamer_iranian', 'iranian_gamers', 'G4A4_Com', 'psn_star', 'psngames_iran',
]

# گروه‌های تلگرام گیمینگ (public group usernames)
# نکته: خیلی از گروه‌ها private هستند و لینک joinchat دارند.
# اینجا فقط groupهایی که username عمومی دارن جمع شده.
SEED_TELEGRAM_GROUPS = [
    # PUBG
    'pubg2iran_group', 'pubgmobile_iran_group', 'iranianpubg',
    'pubg_iran_chat', 'pubg_gap', 'pubgchat_ir',
    # Call of Duty
    'callofdutygaps', 'codm_iran_chat', 'callofduty_iran_group',
    'cod_iran_gap', 'codmchat_ir',
    # Clash
    'ClashTVx', 'clash_iran_chat', 'clashroyale_iran_group',
    'coc_iran_chat',
    # Free Fire
    'freefire_iran_group', 'freefire_gap',
    # Valorant / LoL
    'valorant_iran_gap', 'lol_iran_chat', 'lol_iran_group',
    # FIFA / eFootball
    'fifa_iran_gap', 'fifagap', 'efootball_iran_gap',
    # PS / Xbox / PC
    'ps4_iran_gap', 'ps5_iran_gap', 'xbox_iran_group',
    'pcgamer_iran_group', 'gaming_iran_group',
    # عمومی گیمینگ
    'gamefa_group', 'zoomg_group', 'bazimag_group',
    'gamer_iranian_chat', 'iran_gamers_gap', 'gamerland_gap',
    'digikala_game_group',
    # Fortnite / Apex / Minecraft / GTA
    'fortnite_iran_gap', 'apex_iran_gap', 'minecraft_iran_gap',
    'gta_iran_gap',
]


# ─── Username/profile extraction ────────────────────────────────────

INSTAGRAM_RESERVED = {
    'p', 'reel', 'reels', 'explore', 'accounts', 'stories', 'tv',
    'about', 'developer', 'directory', 'legal', 'terms', 'privacy',
    'help', 'press', 'api', 'jobs', 'invite', 'go', 'session',
    'graphql', 'ajax', 'oauth', 'web', 'static',
}

TELEGRAM_RESERVED = {
    'joinchat', 'addstickers', 'addemoji', 'share', 'login', 's', 'c',
    'iv', 'proxy', 'blog', 'setwebhook', 'about', 'contact', 'faq',
    'apps', 'privacy', 'tos', 'download', 'passport',
}


def extract_instagram_profiles(text: str) -> list[dict]:
    results = []
    seen = set()
    # URL pattern
    for m in re.finditer(r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,60})/?', text, re.I):
        username = m.group(1).lower().rstrip('.')
        if username and username not in seen and username not in INSTAGRAM_RESERVED:
            seen.add(username)
            results.append({'platform': 'instagram', 'username': username,
                            'url': f'https://instagram.com/{username}'})
    # @mention pattern - فقط وقتی context اینستاگرام هست (نه تلگرام)
    # اگر متن حاوی t.me یا telegram.me یا کلمه "Telegram" باشه، @mention نادیده بگیر
    text_lower = text.lower()
    is_telegram_context = 't.me/' in text_lower or 'telegram.me/' in text_lower or 'telegram:' in text_lower
    if not is_telegram_context:
        for m in re.finditer(r'(?:^|[\s،,\(])@([A-Za-z0-9_][A-Za-z0-9_.]{2,29})(?=[\s،,\.\)]|$)', text):
            username = m.group(1).lower().rstrip('.')
            if username and username not in seen and username not in INSTAGRAM_RESERVED:
                seen.add(username)
                results.append({'platform': 'instagram', 'username': username,
                                'url': f'https://instagram.com/{username}'})
    return results


def extract_telegram_channels(text: str) -> list[dict]:
    results = []
    seen = set()
    for m in re.finditer(r'(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})', text, re.I):
        username = m.group(1).lower()
        if username not in seen and username not in TELEGRAM_RESERVED:
            seen.add(username)
            results.append({'platform': 'telegram', 'username': username,
                            'url': f'https://t.me/{username}'})
    return results


# ─── Web search backends ────────────────────────────────────────────

async def _search_serpapi(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get('https://serpapi.com/search.json', params={
            'q': query, 'api_key': api_key, 'num': max_results, 'hl': 'fa', 'gl': 'ir',
        })
        if r.status_code != 200:
            return []
        data = r.json()
        out = []
        for item in (data.get('organic_results') or [])[:max_results]:
            out.append({'title': item.get('title', ''), 'url': item.get('link', ''),
                        'description': item.get('snippet', '')})
        return out


async def _search_google_cse(query: str, api_key: str, cse_id: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get('https://www.googleapis.com/customsearch/v1', params={
            'key': api_key, 'cx': cse_id, 'q': query, 'num': min(max_results, 10), 'lr': 'lang_fa',
        })
        if r.status_code != 200:
            return []
        data = r.json()
        return [{'title': i.get('title', ''), 'url': i.get('link', ''),
                 'description': i.get('snippet', '')} for i in (data.get('items') or [])]


async def _search_brave(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get('https://api.search.brave.com/res/v1/web/search',
                             headers={'X-Subscription-Token': api_key, 'Accept': 'application/json'},
                             params={'q': query, 'count': max_results})
        if r.status_code != 200:
            return []
        data = r.json()
        return [{'title': i.get('title', ''), 'url': i.get('url', ''),
                 'description': i.get('description', '')}
                for i in (data.get('web', {}).get('results') or [])]


async def _search_serper(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post('https://google.serper.dev/search',
                              headers={'X-API-KEY': api_key, 'Content-Type': 'application/json'},
                              json={'q': query, 'num': max_results, 'gl': 'ir', 'hl': 'fa'})
        if r.status_code != 200:
            return []
        data = r.json()
        return [{'title': i.get('title', ''), 'url': i.get('link', ''),
                 'description': i.get('snippet', '')} for i in (data.get('organic') or [])]


async def _search_searchapi(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get('https://www.searchapi.io/api/v1/search', params={
            'engine': 'google', 'q': query, 'api_key': api_key, 'num': max_results, 'gl': 'ir', 'hl': 'fa',
        })
        if r.status_code != 200:
            return []
        data = r.json()
        return [{'title': i.get('title', ''), 'url': i.get('link', ''),
                 'description': i.get('snippet', '')} for i in (data.get('organic_results') or [])]


async def _search_tavily(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post('https://api.tavily.com/search', json={
            'api_key': api_key, 'query': query, 'max_results': max_results,
            'include_answer': False, 'include_raw_content': False, 'search_depth': 'basic',
        })
        if r.status_code != 200:
            return []
        data = r.json()
        return [{'title': i.get('title', ''), 'url': i.get('url', ''),
                 'description': (i.get('content', '') or '')[:300]}
                for i in (data.get('results') or [])]


async def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    """Fallback رایگان بدون API key — DuckDuckGo HTML endpoint."""
    url = f'https://html.duckduckgo.com/html/?q={quote_plus(query)}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'fa,en;q=0.7',
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.post('https://html.duckduckgo.com/html/',
                                  headers=headers, data={'q': query})
            if r.status_code != 200:
                return []
            html = r.text
        # استخراج نتایج
        results = []
        for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
            raw_url = m.group(1)
            # DuckDuckGo از uddg URL wrapping استفاده میکنه
            dec = re.search(r'uddg=([^&]+)', raw_url)
            actual = httpx.URL(dec.group(1)).unicode_string() if dec else raw_url
            try:
                from urllib.parse import unquote
                if dec:
                    actual = unquote(dec.group(1))
            except Exception:
                actual = raw_url
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            results.append({'title': title, 'url': actual, 'description': ''})
            if len(results) >= max_results:
                break
        return results
    except Exception as exc:
        logger.warning(f'DuckDuckGo failed: {exc}')
        return []


async def _search_openrouter(query: str, api_key: str, max_results: int) -> list[dict]:
    """OpenRouter web plugin — پیدا کردن لینک واقعی."""
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    system = (
        'You are a real web search tool. Search the web for the given query and return actual real results. '
        'Return ONLY valid JSON, no markdown, no explanations. Format: '
        '{"results":[{"title":"...","url":"https://instagram.com/username OR https://t.me/channel","description":"..."}]}'
    )
    user = (
        f'Search query: {query}\n\n'
        f'Find real Iranian gaming Instagram profiles and Telegram channels. '
        f'Return only genuine URLs like https://instagram.com/username or https://t.me/channelname.'
    )
    models_to_try = [
        'meta-llama/llama-3.3-70b-instruct:free',
        'google/gemini-2.0-flash-exp:free',
        'qwen/qwen-2.5-72b-instruct:free',
    ]
    async with httpx.AsyncClient(timeout=30) as client:
        for model in models_to_try:
            try:
                r = await client.post(
                    'https://openrouter.ai/api/v1/chat/completions',
                    headers={**headers, 'Content-Type': 'application/json',
                             'HTTP-Referer': 'https://game-lead-finder.onrender.com',
                             'X-Title': 'Game Lead Finder'},
                    json={
                        'model': model,
                        'messages': [{'role': 'system', 'content': system},
                                     {'role': 'user', 'content': user}],
                        'temperature': 0.1, 'max_tokens': 2000,
                        'plugins': [{'id': 'web', 'max_results': max_results}],
                    },
                )
                if r.status_code >= 400:
                    logger.debug(f'OpenRouter {model} → {r.status_code}')
                    continue
                content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
                # پارس JSON
                parsed = None
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    # سعی کن JSON از داخل متن استخراج کنی
                    match = re.search(r'\{.*\}', content, re.S)
                    if match:
                        try:
                            parsed = json.loads(match.group(0))
                        except json.JSONDecodeError:
                            pass
                # اگر JSON نبود، خود متن رو برگردون تا از regex استفاده بشه
                if not parsed or not isinstance(parsed, dict):
                    if content.strip():
                        return [{'title': 'openrouter_raw', 'url': '', 'description': content}]
                    continue
                raw = parsed.get('results') or parsed.get('leads') or []
                out = [{'title': i.get('title', ''), 'url': i.get('url', ''),
                        'description': i.get('description', '')}
                       for i in raw if isinstance(i, dict) and i.get('url')]
                if out:
                    return out[:max_results]
                # اگه JSON بود ولی خالی، محتوای خام رو بذار
                return [{'title': 'openrouter_raw', 'url': '', 'description': content}]
            except Exception as exc:
                logger.debug(f'OpenRouter {model} exception: {exc}')
                continue
    return []


async def _search_groq(query: str, api_key: str, max_results: int) -> list[dict]:
    """Groq — بدون جستجوی وب واقعی، ولی از دانش خودش لینک‌های پرکاربرد رو میده."""
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    system = (
        'You know famous Iranian gaming influencers on Instagram and Telegram. '
        'Return ONLY valid JSON. No markdown, no explanations.'
    )
    user = (
        f'For the query: "{query}"\n\n'
        f'List real, well-known Iranian gaming Instagram pages (https://instagram.com/USERNAME) '
        f'and Telegram channels (https://t.me/CHANNEL). Only include ones you are confident exist.\n\n'
        f'Format: {{"results":[{{"title":"name","url":"real_url","description":"what they do"}}]}}'
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post('https://api.groq.com/openai/v1/chat/completions', headers=headers, json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'temperature': 0.2, 'max_tokens': 1500,
                'response_format': {'type': 'json_object'},
            })
            if r.status_code != 200:
                return []
            content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return [{'title': 'groq_raw', 'url': '', 'description': content}]
            raw = parsed.get('results') or []
            return [{'title': i.get('title', ''), 'url': i.get('url', ''),
                     'description': i.get('description', '')}
                    for i in raw if isinstance(i, dict) and i.get('url')][:max_results]
    except Exception as exc:
        logger.debug(f'Groq exception: {exc}')
    return []


async def _direct_web_search(query: str, max_results: int = 8) -> tuple[list[dict], list[str]]:
    """جستجوی وب با هر backend که در دسترسه. برمیگردونه (results, used_backends)."""
    settings = get_settings()
    used = []

    # اولویت: SerpAPI > Serper > Brave > Google CSE > SearchAPI > Tavily > OpenRouter > Groq > DuckDuckGo
    backends = [
        ('serpapi', settings.serpapi_key, _search_serpapi),
        ('serper', settings.serper_api_key, _search_serper),
        ('brave', settings.brave_search_api_key, _search_brave),
        ('searchapi', settings.searchapi_key, _search_searchapi),
        ('tavily', settings.tavily_api_key, _search_tavily),
    ]
    for name, key, fn in backends:
        if not key:
            continue
        try:
            results = await fn(query, key, max_results)
            used.append(name)
            if results:
                return results, used
        except Exception as exc:
            logger.warning(f'{name} search failed for "{query[:40]}": {exc}')

    # Google CSE نیاز به دو تا کلید داره
    if settings.google_cse_api_key and settings.google_cse_id:
        try:
            results = await _search_google_cse(query, settings.google_cse_api_key,
                                               settings.google_cse_id, max_results)
            used.append('google_cse')
            if results:
                return results, used
        except Exception as exc:
            logger.warning(f'google_cse failed: {exc}')

    # OpenRouter web plugin
    if settings.openrouter_api_key:
        try:
            results = await _search_openrouter(query, settings.openrouter_api_key, max_results)
            used.append('openrouter')
            if results:
                return results, used
        except Exception as exc:
            logger.warning(f'openrouter failed: {exc}')

    # Groq (knowledge-based)
    if settings.groq_api_key:
        try:
            results = await _search_groq(query, settings.groq_api_key, max_results)
            used.append('groq')
            if results:
                return results, used
        except Exception as exc:
            logger.warning(f'groq failed: {exc}')

    # آخرین fallback: DuckDuckGo رایگان بدون کلید
    try:
        results = await _search_duckduckgo(query, max_results)
        used.append('duckduckgo')
        if results:
            return results, used
    except Exception as exc:
        logger.warning(f'duckduckgo failed: {exc}')

    return [], used


# ─── Scraping ───────────────────────────────────────────────────────

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
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9,fa;q=0.7',
    }
    result = {'username': username, 'url': f'https://instagram.com/{username}', 'display_name': username}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return result
            html = r.text[:1_500_000]
        title_m = re.search(r'og:title["\s]+content="([^"]+)"', html)
        if title_m:
            result['display_name'] = title_m.group(1).strip().split(' (')[0].strip() or username
        desc_m = re.search(r'og:description["\s]+content="([^"]+)"', html)
        if desc_m:
            desc = desc_m.group(1).strip()
            result['bio'] = desc
            # Instagram description معمولاً به این شکله: "X Followers, Y Following, Z Posts - See Instagram photos and videos from @user (name)"
            fm = re.search(r'([\d,.]+[KkMm]?)\s+Followers', desc, re.I)
            if fm:
                result['followers'] = _parse_count(fm.group(1))
            fom = re.search(r'([\d,.]+[KkMm]?)\s+Following', desc, re.I)
            if fom:
                result['following'] = _parse_count(fom.group(1))
            pm = re.search(r'([\d,.]+[KkMm]?)\s+Posts', desc, re.I)
            if pm:
                result['posts_count'] = _parse_count(pm.group(1))
        # اگر از og:description نگرفتیم، از خود HTML هم امتحان کن
        if 'followers' not in result:
            fm = re.search(r'"edge_followed_by":\{"count":(\d+)', html)
            if fm:
                result['followers'] = int(fm.group(1))
        if 'following' not in result:
            fom = re.search(r'"edge_follow":\{"count":(\d+)', html)
            if fom:
                result['following'] = int(fom.group(1))
    except Exception as exc:
        logger.debug(f'IG scrape {username} failed: {exc}')
    return result


async def scrape_telegram_channel(username: str) -> dict:
    url = f'https://t.me/s/{username}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8',
    }
    result = {'username': username, 'url': f'https://t.me/{username}', 'display_name': username}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                # اگر preview نبود، صفحه اصلی رو امتحان کن
                r = await client.get(f'https://t.me/{username}', headers=headers)
                if r.status_code != 200:
                    return result
            html = r.text[:2_000_000]
        title_m = re.search(r'og:title["\s]+content="([^"]+)"', html)
        if title_m:
            result['display_name'] = title_m.group(1).strip()
        desc_m = re.search(r'og:description["\s]+content="([^"]+)"', html)
        if desc_m:
            result['bio'] = desc_m.group(1).strip()
        # تعداد اعضا از چند pattern مختلف
        for pat in [
            r'([\d,.\s]+)\s*(?:member|subscriber)s?',
            r'([\d,.\s]+)\s*عضو',
            r'"tgme_page_extra"[^>]*>([\d,.\s]+)\s*subscriber',
        ]:
            m = re.search(pat, html, re.I)
            if m:
                num = re.sub(r'[^\d]', '', m.group(1))
                if num and int(num) > 5:
                    result['followers'] = int(num)
                    break
        # میانگین views
        views = []
        for m in re.finditer(r'class="tgme_widget_message_views"[^>]*>([^<]+)<', html):
            raw = m.group(1).strip().replace(',', '')
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
    except Exception as exc:
        logger.debug(f'TG scrape {username} failed: {exc}')
    return result


async def _scrape(profile: dict) -> dict:
    if profile['platform'] == 'telegram':
        return await scrape_telegram_channel(profile['username'])
    elif profile['platform'] == 'instagram':
        return await scrape_instagram_profile(profile['username'])
    return profile


# ─── Niche and game tag detection ──────────────────────────────────

def _detect_niche(text: str) -> str | None:
    rules = {
        'استریمر': ['استریم', 'stream', 'streamer', 'لایو', 'live'],
        'آنباکسینگ': ['آنباکس', 'unboxing', 'جعبه‌گشایی'],
        'ریویو بازی': ['ریویو', 'review', 'نقد', 'بررسی بازی'],
        'گیم پلی': ['گیمپلی', 'gameplay', 'گیم پلی', 'پارت'],
        'فروش اکانت/آیتم': ['فروش', 'خرید', 'اکانت', 'سی پی', 'یوسی', 'جم', 'گیفت کارت'],
        'تکنولوژی': ['تکنولوژی', 'tech', 'گجت', 'لپتاپ'],
        'خبر و آموزش': ['خبر', 'آموزش', 'ترفند', 'tip', 'تریلر', 'trailer'],
    }
    blob = text.lower().replace('ي', 'ی').replace('ك', 'ک')
    best, best_hits = None, 0
    for niche, terms in rules.items():
        hits = sum(1 for t in terms if t in blob)
        if hits > best_hits:
            best_hits = hits; best = niche
    return best


def _detect_game_tags(text: str) -> str:
    tag_map = {
        'کالاف': 'کالاف', 'call of duty': 'کالاف', 'cod': 'کالاف', 'warzone': 'کالاف',
        'پابجی': 'پابجی', 'pubg': 'پابجی',
        'ولورانت': 'ولورانت', 'valorant': 'ولورانت',
        'فورتنایت': 'فورتنایت', 'fortnite': 'فورتنایت',
        'کلش': 'کلش', 'clash': 'کلش',
        'فری فایر': 'فری فایر', 'free fire': 'فری فایر', 'freefire': 'فری فایر',
        'gta': 'GTA',
        'ماینکرفت': 'ماینکرفت', 'minecraft': 'ماینکرفت',
        'فیفا': 'فیفا', 'efootball': 'فیفا', 'fifa': 'فیفا',
        'ایپکس': 'ایپکس', 'apex': 'ایپکس',
        'لول': 'LoL', 'league of legends': 'LoL', 'lol': 'LoL',
        'موبایل لجندز': 'MLBB', 'mlbb': 'MLBB', 'mobile legends': 'MLBB',
    }
    blob = text.lower().replace('ي', 'ی').replace('ك', 'ک')
    tags = set()
    for kw, tag in tag_map.items():
        if kw in blob:
            tags.add(tag)
    return ','.join(tags) if tags else ''


def _has_persian(text: str) -> bool:
    return bool(re.search(r'[\u0600-\u06FF]', text))


# ─── Main collector ────────────────────────────────────────────────

async def _process_profile(
    profile: dict,
    db: Session,
    check_activity_first: bool,
    window_days: int,
    allow_telegram_types: tuple[str, ...] = ('channel', 'group'),
) -> tuple[str, dict]:
    """پردازش یک پروفایل: activity check + scrape + آماده‌سازی داده.

    allow_telegram_types: کدوم نوع تلگرام قابل قبول است.
        - ('channel',) → فقط کانال
        - ('group',) → فقط گروه
        - ('channel', 'group') → هر دو (پیش‌فرض)

    برمیگردونه (status, data) که status یکی از:
      - 'skip_inactive': فعال نبود، رد شد
      - 'skip_notfound': پیدا نشد، رد شد
      - 'skip_wrongtype': نوع مجاز نبود (بات/کاربر)
      - 'duplicate': قبلاً بوده، آپدیت شد
      - 'saved': جدید ذخیره شد
      - 'error': خطا
    """
    url = profile['url']
    username = profile.get('username', '')
    platform = profile['platform']

    # ── مرحله ۱: activity check ──
    activity: ActivityStatus | None = None
    if check_activity_first and username:
        try:
            if platform == 'telegram':
                from app.influencer.activity import check_telegram_activity
                activity = await check_telegram_activity(username, window_days,
                                                        allow_types=allow_telegram_types)
            else:
                activity = await check_activity(platform, username, window_days)
            if not activity.exists:
                return ('skip_notfound', {'reason': activity.reason})
            # چک نوع نامعتبر (bot/user)
            if platform == 'telegram' and activity.entity_type in ('bot', 'user'):
                return ('skip_wrongtype', {'reason': f'نوع {activity.entity_type}',
                                           'entity_type': activity.entity_type})
            if not activity.is_active:
                return ('skip_inactive', {'reason': activity.reason,
                                          'days_since': activity.days_since_last_post,
                                          'entity_type': activity.entity_type})
        except Exception as exc:
            logger.debug(f'Activity check {url} failed: {exc}')
            # اگر check شکست خورد، ادامه بده (شاید rate limit)

    # ── مرحله ۲: چک تکراری بودن ──
    existing = db.scalar(select(Influencer).where(Influencer.profile_url == url))

    # ── مرحله ۳: scrape ──
    try:
        info = await _scrape(profile)
    except Exception as exc:
        info = {}
        logger.debug(f'Scrape {url} failed: {exc}')

    if existing:
        # آپدیت
        if info.get('followers') and (not existing.followers or info['followers'] > existing.followers):
            existing.followers = info['followers']
        if activity and activity.members_count and (not existing.followers or activity.members_count > existing.followers):
            existing.followers = activity.members_count
        if info.get('avg_views') and (not existing.avg_views or info['avg_views'] > existing.avg_views):
            existing.avg_views = info['avg_views']
        if info.get('engagement_rate'):
            existing.engagement_rate = info['engagement_rate']
        if info.get('bio') and not existing.bio:
            existing.bio = info['bio']
        existing.last_seen = datetime.utcnow()
        if activity:
            existing.is_active = activity.is_active
            existing.last_post_at = activity.last_post_at
            existing.activity_checked_at = datetime.utcnow()
            existing.activity_reason = activity.reason[:300]
            if activity.entity_type:
                existing.entity_type = activity.entity_type
        compute_influencer_score(existing)
        db.add(existing)
        return ('duplicate', {})

    # ── ذخیره جدید ──
    blob = f"{info.get('display_name', '')} {info.get('bio', '')} {username}"
    # اگر activity تعداد اعضا رو داشت و scrape نداشت، از اون استفاده کن
    followers_final = info.get('followers') or (activity.members_count if activity else None)
    # entity_type: اگر activity گفت، استفاده کن؛ اگر نه، از profile['entity_type_hint']، وگرنه fallback
    entity = None
    if activity and activity.entity_type:
        entity = activity.entity_type
    elif profile.get('entity_type_hint'):
        entity = profile['entity_type_hint']
    else:
        entity = 'profile' if platform == 'instagram' else 'channel'

    inf = Influencer(
        platform=platform,
        profile_url=url,
        username=username,
        display_name=info.get('display_name') or username or 'نامشخص',
        bio=info.get('bio'),
        followers=followers_final,
        following=info.get('following'),
        posts_count=info.get('posts_count'),
        avg_views=info.get('avg_views'),
        engagement_rate=info.get('engagement_rate'),
        niche=_detect_niche(blob),
        game_tags=_detect_game_tags(blob),
        language='fa' if (_has_persian(blob) or not info.get('bio')) else 'en',
        source='seed' if username in (SEED_INSTAGRAM + SEED_TELEGRAM + SEED_TELEGRAM_GROUPS) else 'search',
        status='discovered',
        is_active=activity.is_active if activity else True,
        last_post_at=activity.last_post_at if activity else None,
        activity_checked_at=datetime.utcnow() if activity else None,
        activity_reason=activity.reason[:300] if activity else None,
        entity_type=entity,
    )
    compute_influencer_score(inf)
    db.add(inf)
    return ('saved', {'entity_type': entity})


async def discover_influencers(
    db: Session,
    *,
    platform: str = 'both',
    queries: list[str] | None = None,
    max_results_per_query: int = 8,
    min_collab_score: int = 0,
    use_seed_list: bool = True,
    check_activity_first: bool = True,
    activity_window_days: int = ACTIVITY_WINDOW_DAYS,
    concurrency: int = 8,
    include_groups: bool = True,
    include_channels: bool = True,
) -> dict:
    """Run web searches to discover gaming influencers.

    check_activity_first: اگر True، قبل از ذخیره چک میکنه که پیج/کانال/گروه
    در N روز اخیر پست جدید داشته باشه (فعال باشه).
    include_groups: گروه‌های تلگرام هم پیدا کنه؟
    include_channels: کانال‌های تلگرام هم پیدا کنه؟
    """
    # allow_types برای تلگرام
    allow_types = []
    if include_channels: allow_types.append('channel')
    if include_groups: allow_types.append('group')
    if not allow_types: allow_types = ['channel', 'group']
    allow_types_tuple = tuple(allow_types)
    search_queries: list[str] = []
    if queries:
        search_queries = [q for q in queries if q.strip()]
    else:
        if platform in ('instagram', 'both'):
            search_queries.extend(INSTAGRAM_QUERIES[:6])
        if platform in ('telegram', 'both'):
            search_queries.extend(TELEGRAM_QUERIES[:6])

    summary = {
        'queries_run': 0, 'profiles_found': 0, 'new_saved': 0,
        'duplicates': 0, 'errors': [], 'backends_used': set(),
        'seed_used': 0, 'search_results_total': 0,
        'skipped_inactive': 0, 'skipped_notfound': 0, 'skipped_wrongtype': 0,
        'saved_channels': 0, 'saved_groups': 0, 'saved_ig': 0,
        'activity_check_enabled': check_activity_first,
        'activity_window_days': activity_window_days,
        'allow_telegram_types': list(allow_types_tuple),
    }
    all_profiles: list[dict] = []

    for query in search_queries:
        run = start_run(db, 'influencer_discovery', query)
        summary['queries_run'] += 1
        try:
            raw_results, backends = await _direct_web_search(query, max_results=max_results_per_query)
            summary['backends_used'].update(backends)
            summary['search_results_total'] += len(raw_results)
            logger.info(f'query="{query[:50]}" → {len(raw_results)} results via {backends}')
            for item in raw_results:
                text = f"{item.get('title', '')} {item.get('url', '')} {item.get('description', '')}"
                all_profiles.extend(extract_instagram_profiles(text))
                all_profiles.extend(extract_telegram_channels(text))
            finish_run(db, run, len(raw_results), 0)
        except Exception as exc:
            summary['errors'].append({'query': query, 'error': str(exc)[:200]})
            finish_run(db, run, 0, 0, str(exc)[:200])
            logger.error(f'query="{query}" exception: {exc}')

    # ── Seed list ──
    if use_seed_list:
        seed_added = 0
        if platform in ('instagram', 'both'):
            for u in SEED_INSTAGRAM:
                all_profiles.append({'platform': 'instagram', 'username': u,
                                     'url': f'https://instagram.com/{u}'})
                seed_added += 1
        if platform in ('telegram', 'both', 'telegram_channel'):
            for u in SEED_TELEGRAM:
                all_profiles.append({'platform': 'telegram', 'username': u,
                                     'url': f'https://t.me/{u}',
                                     'entity_type_hint': 'channel'})
                seed_added += 1
        # گروه‌های تلگرام (فقط اگر platform=both یا telegram_group باشه)
        if platform in ('both', 'telegram', 'telegram_group', 'group'):
            for u in SEED_TELEGRAM_GROUPS:
                all_profiles.append({'platform': 'telegram', 'username': u,
                                     'url': f'https://t.me/{u}',
                                     'entity_type_hint': 'group'})
                seed_added += 1
        summary['seed_used'] = seed_added
        logger.info(f'Added {seed_added} seed profiles (channels+groups+IG)')

    # Deduplicate
    seen_urls: set[str] = set()
    unique_profiles: list[dict] = []
    for p in all_profiles:
        if p['url'] not in seen_urls:
            seen_urls.add(p['url'])
            unique_profiles.append(p)

    summary['profiles_found'] = len(unique_profiles)
    logger.info(f'Total unique profiles: {len(unique_profiles)}, '
                f'activity_check={check_activity_first}')

    # ── پردازش موازی (با محدودیت concurrency) ──
    sem = asyncio.Semaphore(concurrency)

    async def _worker(prof):
        async with sem:
            return await _process_profile(prof, db, check_activity_first,
                                          activity_window_days, allow_types_tuple)

    results = await asyncio.gather(*(_worker(p) for p in unique_profiles), return_exceptions=True)

    for res, prof in zip(results, unique_profiles):
        if isinstance(res, Exception):
            summary['errors'].append({'error': str(res)[:200]})
            continue
        status, data = res
        if status == 'saved':
            summary['new_saved'] += 1
            # طبقه‌بندی
            if prof['platform'] == 'instagram':
                summary['saved_ig'] += 1
            elif data.get('entity_type') == 'group':
                summary['saved_groups'] += 1
            else:
                summary['saved_channels'] += 1
        elif status == 'duplicate':
            summary['duplicates'] += 1
        elif status == 'skip_inactive':
            summary['skipped_inactive'] += 1
        elif status == 'skip_notfound':
            summary['skipped_notfound'] += 1
        elif status == 'skip_wrongtype':
            summary['skipped_wrongtype'] += 1

    db.commit()
    summary['backends_used'] = sorted(summary['backends_used'])
    logger.info(f'Discovery complete: {summary}')
    return summary


async def recheck_all_activity(db: Session, window_days: int = ACTIVITY_WINDOW_DAYS,
                                concurrency: int = 8, delete_inactive: bool = False) -> dict:
    """چک مجدد فعالیت همه اینفلوئنسرهای موجود در DB.

    اگر delete_inactive=True، غیرفعال‌ها رو پاک میکنه. وگرنه فقط is_active=False میذاره.
    """
    influencers = list(db.scalars(select(Influencer).where(Influencer.username.isnot(None))).all())
    summary = {
        'total_checked': len(influencers), 'active': 0, 'inactive': 0,
        'notfound': 0, 'errors': 0, 'deleted': 0,
    }
    sem = asyncio.Semaphore(concurrency)

    async def _check(inf):
        async with sem:
            try:
                st = await check_activity(inf.platform, inf.username, window_days)
                return inf, st
            except Exception as exc:
                logger.debug(f'Recheck {inf.username} exc: {exc}')
                return inf, None

    results = await asyncio.gather(*(_check(i) for i in influencers))
    to_delete = []
    for inf, st in results:
        if st is None:
            summary['errors'] += 1
            continue
        inf.activity_checked_at = datetime.utcnow()
        inf.activity_reason = st.reason[:300]
        inf.is_active = st.is_active
        inf.last_post_at = st.last_post_at
        if not st.exists:
            summary['notfound'] += 1
            if delete_inactive:
                to_delete.append(inf)
        elif st.is_active:
            summary['active'] += 1
        else:
            summary['inactive'] += 1
            if delete_inactive:
                to_delete.append(inf)
        db.add(inf)

    for inf in to_delete:
        db.delete(inf)
        summary['deleted'] += 1

    db.commit()
    logger.info(f'Recheck complete: {summary}')
    return summary
