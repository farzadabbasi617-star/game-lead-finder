"""Community Finder — collector.

Finds active gaming community members through LEGAL public data:
1. Marketplace ads (Divar/Sheypoor) — people selling gaming stuff
2. Public Telegram group previews — visible messages with usernames
3. Web search — gaming community posts with contact info
4. Gaming forums/channels — public posts with seller info

NO scraping of private data, NO member list extraction, NO login required.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Lead, CrawlerRun
from app.repository import start_run, finish_run, upsert_lead

# ── Search queries for finding active gaming people ────────────────

MARKETPLACE_QUERIES = [
    'فروش اکانت کلش رویال site:divar.ir',
    'فروش سی پی کالاف site:divar.ir',
    'فروش یوسی پابجی site:divar.ir',
    'فروش اکانت ولورانت site:divar.ir',
    'گیفت کارت پلی استیشن site:divar.ir',
    'لوازم گیمینگ site:divar.ir',
    'فروش اکانت بازی site:sheypoor.com',
    'فروش سی پی site:sheypoor.com',
    'فروش یوسی site:sheypoor.com',
    'گیفت کارت استیم site:sheypoor.com',
    'کنسول بازی site:sheypoor.com',
]

TELEGRAM_GROUP_QUERIES = [
    'گروه تلگرام فروش اکانت کلش',
    'گروه تلگرام فروش سی پی کالاف',
    'گروه تلگرام خرید یوسی پابجی',
    'گروه تلگرام گیفت کارت',
    'گروه تلگرام لوازم گیمینگ',
    'گروه تلگرام فروش اکانت ولورانت',
    'site:t.me گروه فروش اکانت بازی',
    'site:t.me خرید فروش گیم',
]

INSTAGRAM_QUERIES = [
    'کامنت فروش سی پی کالاف اینستاگرام',
    'کامنت فروش اکانت کلش اینستاگرام',
    'کامنت فروش یوسی پابجی اینستاگرام',
    'اینستاگرام فروش اکانت گیم ایران',
    'پیج فروش گیفت کارت پلی استیشن',
    'پیج فروش اکانت ولورانت ایران',
]

FORUM_QUERIES = [
    'فروش اکانت کلش انجمن گیمینگ',
    'فروش سی پی کالاف انجمن',
    'فروش یوسی پابجی تربیح',
    'فروش اکانت بازی باما',
]


# ── Web search helpers ─────────────────────────────────────────────

async def _web_search(query: str, max_results: int = 10) -> list[dict]:
    settings = get_settings()
    if settings.openrouter_api_key:
        try:
            return await _search_openrouter(query, settings.openrouter_api_key, max_results)
        except Exception:
            pass
    if settings.tavily_api_key:
        try:
            return await _search_tavily(query, settings.tavily_api_key, max_results)
        except Exception:
            pass
    if settings.groq_api_key:
        try:
            return await _search_groq(query, settings.groq_api_key, max_results)
        except Exception:
            pass
    return []


async def _search_openrouter(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=25) as client:
        headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
        try:
            r = await client.get('https://openrouter.ai/api/v1/models', headers=headers)
            models = [m['id'] for m in (r.json().get('data') or []) if ':free' in m.get('id', '')][:4]
        except Exception:
            models = ['meta-llama/llama-3.3-70b-instruct:free']
        if not models:
            models = ['meta-llama/llama-3.3-70b-instruct:free']
        system = 'تو جستجوگر وب هستی. فقط JSON معتبر بده.'
        user = f'عبارت: {query}\n\nنتایج واقعی:\n{{"results":[{{"title":"...","url":"...","description":"..."}}]}}'
        for model in models:
            try:
                h = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'HTTP-Referer': 'https://game-lead-finder.onrender.com', 'X-Title': 'Game Lead Finder'}
                r = await client.post('https://openrouter.ai/api/v1/chat/completions', headers=h, json={
                    'model': model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                    'temperature': 0.15, 'max_tokens': 2000, 'plugins': [{'id': 'web', 'max_results': max_results}],
                })
                if r.status_code >= 400:
                    continue
                content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    match = re.search(r'\{.*\}', content, re.S)
                    if match:
                        parsed = json.loads(match.group(0))
                    else:
                        continue
                raw = parsed.get('results') or parsed.get('leads') or []
                out = [{'title': i.get('title', ''), 'url': i.get('url', ''), 'description': i.get('description', '')} for i in raw if isinstance(i, dict) and i.get('url')]
                if out:
                    return out[:max_results]
            except Exception:
                continue
    return []


async def _search_tavily(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post('https://api.tavily.com/search', json={'api_key': api_key, 'query': query, 'max_results': max_results})
        if r.status_code != 200:
            return []
        return [{'title': i.get('title', ''), 'url': i.get('url', ''), 'description': i.get('content', '')[:300]} for i in (r.json().get('results') or [])]


async def _search_groq(query: str, api_key: str, max_results: int) -> list[dict]:
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    system = 'تو لیست فروشنده‌های گیمینگ ایرانی رو میشناسی. فقط JSON بده.'
    user = f'برای "{query}" فروشنده‌های واقعی رو برگردون.\n{{"results":[{{"title":"...","url":"...","description":"..."}}]}}'
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post('https://api.groq.com/openai/v1/chat/completions', headers=headers, json={
                'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'temperature': 0.2, 'max_tokens': 1500,
            })
            if r.status_code != 200:
                return []
            content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', content, re.S)
                parsed = json.loads(match.group(0)) if match else {'results': []}
            return [{'title': i.get('title', ''), 'url': i.get('url', ''), 'description': i.get('description', '')} for i in (parsed.get('results') or []) if isinstance(i, dict) and i.get('url')][:max_results]
    except Exception:
        return []


# ── Extract contacts from search results ───────────────────────────

def _extract_leads_from_results(results: list[dict], source_prefix: str, city: str = 'تهران') -> list[dict]:
    leads = []
    for item in results:
        url = item.get('url', '')
        title = item.get('title', '')
        desc = item.get('description', '')
        text = f'{title} {desc} {url}'

        # استخراج شماره تلفن
        phones = re.findall(r'(?:\+98|0098|0)?9\d{9}', text)

        # استخراج آیدی اینستاگرام
        ig_matches = re.findall(r'(?:instagram\.com/|@)([A-Za-z0-9_.]{2,60})', text)
        ig = [x for x in ig_matches if x.lower() not in {'p', 'reel', 'explore', 'accounts', 'stories'}]

        # استخراج آیدی تلگرام
        tg_matches = re.findall(r'(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})', text)
        tg = [x for x in tg_matches if x.lower() not in {'joinchat', 'addstickers', 'share', 'login', 's', 'c', 'iv'}]

        # فقط اگه حداقل یه راه ارتباط داشت ذخیره کن
        if not phones and not ig and not tg:
            continue

        # تشخیص دسته
        category = _detect_category(text)

        lead = {
            'source': source_prefix,
            'entity_type': 'community_member',
            'title': title[:500],
            'url': url,
            'description': desc[:500],
            'city': city,
            'phone': phones[0] if phones else None,
            'instagram': f'https://instagram.com/{ig[0]}' if ig else None,
            'telegram': f'https://t.me/{tg[0]}' if tg else None,
            'category': category,
            'keyword': source_prefix,
        }
        leads.append(lead)
    return leads


def _detect_category(text: str) -> str | None:
    text = text.lower().replace('ي', 'ی').replace('ك', 'ک')
    rules = {
        'اکانت': ['اکانت', 'account', 'کلش', 'ولورانت', 'valorant'],
        'سی‌پی کالاف': ['سی پی', 'cp', 'کالاف', 'call of duty', 'cod'],
        'یوسی پابجی': ['یوسی', 'uc', 'پابجی', 'pubg'],
        'گیفت کارت': ['گیفت کارت', 'gift card', 'psn', 'استیم والت'],
        'جم/الماس': ['جم', 'الماس', 'free fire', 'فری فایر'],
        'فروشگاه گیم': ['فروشگاه', 'کنسول', 'پلی استیشن', 'لوازم گیمینگ'],
        'گیم‌نت': ['گیم نت', 'گیم سنتر'],
    }
    best, best_hits = None, 0
    for cat, terms in rules.items():
        hits = sum(1 for t in terms if t in text)
        if hits > best_hits:
            best_hits = hits
            best = cat
    return best


# ── Scrape public Telegram group messages ──────────────────────────

async def _scrape_tg_group_messages(username: str) -> list[dict]:
    """Extract usernames from public Telegram group message previews."""
    url = f'https://t.me/s/{username}'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8'}
    found = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return found
            html = r.text[:2_000_000]

        # استخراج یوزرنیم‌هایی که پیام فرستادن
        usernames = re.findall(r'class="tgme_widget_message_author[^"]*"[^>]*>[^<]*<a[^>]*href="https://t\.me/([A-Za-z0-9_]+)"', html)
        # همچنین لینک‌های داخل پیام‌ها
        links = re.findall(r'href="(https://t\.me/[A-Za-z0-9_]+)"', html)
        ig_links = re.findall(r'href="(https://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+)"', html)
        phones = re.findall(r'(?:\+98|0098|0)?9\d{9}', html)

        seen = set()
        for u in usernames:
            u = u.lower()
            if u not in seen and u != username.lower() and len(u) >= 3:
                seen.add(u)
                found.append({'type': 'telegram_user', 'username': u, 'source_group': username})

        for link in links:
            m = re.search(r't\.me/([A-Za-z0-9_]+)', link)
            if m:
                u = m.group(1).lower()
                if u not in seen and u != username.lower() and u not in {'joinchat', 'addstickers', 'share', 's', 'c'}:
                    seen.add(u)
                    found.append({'type': 'telegram_link', 'username': u, 'source_group': username})

        for link in ig_links:
            m = re.search(r'instagram\.com/([A-Za-z0-9_.]+)', link)
            if m:
                u = m.group(1).lower()
                if u not in seen and u not in {'p', 'reel', 'explore'}:
                    seen.add(u)
                    found.append({'type': 'instagram_link', 'username': u, 'source_group': username})

        for phone in set(phones):
            found.append({'type': 'phone', 'phone': phone, 'source_group': username})

    except Exception:
        pass
    return found


# ── Main collector ─────────────────────────────────────────────────

async def discover_community_members(
    db: Session,
    *,
    sources: list[str] | None = None,
    city: str = 'تهران',
    max_results_per_query: int = 8,
) -> dict:
    """Find active gaming community members.

    sources: list of 'marketplace', 'telegram_groups', 'instagram', 'forums'
    """
    sources = sources or ['marketplace', 'telegram_groups']
    summary = {'total_found': 0, 'total_saved': 0, 'total_duplicates': 0, 'by_source': {}, 'errors': []}
    all_leads: list[dict] = []

    # ── Marketplace (Divar/Sheypoor) ──
    if 'marketplace' in sources:
        run = start_run(db, 'community_marketplace', f'marketplace | {city}')
        count = 0
        try:
            for query in MARKETPLACE_QUERIES[:6]:
                results = await _web_search(query, max_results_per_query)
                leads = _extract_leads_from_results(results, 'marketplace', city)
                all_leads.extend(leads)
                count += len(leads)
            summary['by_source']['marketplace'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'Marketplace: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Telegram Groups ──
    if 'telegram_groups' in sources:
        run = start_run(db, 'community_telegram', f'telegram_groups | {city}')
        count = 0
        try:
            # اول گروه‌های مرتبط رو پیدا کن
            for query in TELEGRAM_GROUP_QUERIES[:4]:
                results = await _web_search(query, max_results_per_query)
                for item in results:
                    url = item.get('url', '')
                    # استخراج یوزرنیم تلگرام
                    tg_match = re.search(r't\.me/([A-Za-z0-9_]{3,80})', url)
                    if not tg_match:
                        continue
                    username = tg_match.group(1).lower()
                    if username in {'joinchat', 'addstickers', 'share', 'login', 's', 'c', 'iv'}:
                        continue

                    # اسکرپ پیام‌های عمومی گروه
                    members = await _scrape_tg_group_messages(username)
                    for member in members:
                        if member['type'] == 'phone':
                            all_leads.append({
                                'source': 'telegram_group', 'entity_type': 'group_member',
                                'title': f'عضو گروه {username}', 'url': f'https://t.me/{username}',
                                'phone': member['phone'], 'city': city, 'keyword': 'گروه تلگرام',
                            })
                        elif member['type'] == 'telegram_user':
                            all_leads.append({
                                'source': 'telegram_group', 'entity_type': 'group_member',
                                'title': f'@{member["username"]} از گروه {username}',
                                'url': f'https://t.me/{member["username"]}',
                                'telegram': f'https://t.me/{member["username"]}',
                                'city': city, 'keyword': 'گروه تلگرام',
                            })
                        elif member['type'] == 'instagram_link':
                            all_leads.append({
                                'source': 'telegram_group', 'entity_type': 'group_member',
                                'title': f'@{member["username"]} از گروه {username}',
                                'url': f'https://instagram.com/{member["username"]}',
                                'instagram': f'https://instagram.com/{member["username"]}',
                                'city': city, 'keyword': 'گروه تلگرام',
                            })
                        count += 1

            summary['by_source']['telegram_groups'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'Telegram: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Instagram ──
    if 'instagram' in sources:
        run = start_run(db, 'community_instagram', f'instagram | {city}')
        count = 0
        try:
            for query in INSTAGRAM_QUERIES[:4]:
                results = await _web_search(query, max_results_per_query)
                leads = _extract_leads_from_results(results, 'instagram_community', city)
                all_leads.extend(leads)
                count += len(leads)
            summary['by_source']['instagram'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'Instagram: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Forums ──
    if 'forums' in sources:
        run = start_run(db, 'community_forums', f'forums | {city}')
        count = 0
        try:
            for query in FORUM_QUERIES[:3]:
                results = await _web_search(query, max_results_per_query)
                leads = _extract_leads_from_results(results, 'forum', city)
                all_leads.extend(leads)
                count += len(leads)
            summary['by_source']['forums'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'Forums: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Save all leads ──
    summary['total_found'] = len(all_leads)
    for lead_data in all_leads:
        try:
            _, is_new = upsert_lead(db, lead_data)
            if is_new:
                summary['total_saved'] += 1
            else:
                summary['total_duplicates'] += 1
        except Exception:
            pass

    return summary
