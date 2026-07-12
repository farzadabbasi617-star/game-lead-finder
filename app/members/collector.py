"""Member Finder — finds actual gamers/users through legal public data.

Focus: finding PEOPLE who play games, not stores.
Sources:
1. Telegram gaming group messages → extract active members
2. Marketplace buyers/sellers → they're gamers
3. Gaming community posts → people with contact info
4. Instagram gaming profiles → public profiles
5. Phone/social extraction from public web
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Lead, CrawlerRun
from app.repository import start_run, finish_run, upsert_lead

# ── Queries focused on finding GAMERS (not stores) ─────────────────

GAMER_TELEGRAM_QUERIES = [
    'گروه تلگرام گیمرهای ایرانی',
    'گروه تلگرام بازی آنلاین',
    'گروه تلگرام کالاف دیوتی موبایل',
    'گروه تلگرام پابجی موبایل',
    'گروه تلگرام فری فایر ایران',
    'گروه تلگرام ولورانت ایران',
    'گروه تلگرام کلش رویال',
    'گروه تلگرام فورتنایت',
    'گروه تلگرام گیفت کارت',
    'گروه تلگرام خرید اکانت بازی',
    'گروه تلگرام ماینکرفت فارسی',
    'site:t.me گیمر ایران',
    'site:t.me call of duty mobile فارسی',
    'site:t.me pubg mobile ایران',
    'site:t.me خرید اکانت کلش',
    'site:t.me فروش سی پی کالاف',
    'site:t.me فروش یوسی پابجی',
]

GAMER_INSTAGRAM_QUERIES = [
    'پیج گیمرهای ایرانی اینستاگرام',
    'گیمر ایرانی اینستاگرام',
    'پیج کالاف دیوتی موبایل ایران',
    'پیج پابجی موبایل ایران',
    'پیج فری فایر ایران',
    'پیج گیفت کارت پلی استیشن',
    'site:instagram.com گیمر ایرانی',
    'site:instagram.com call of duty mobile فارسی',
    'site:instagram.com pubg mobile ایران',
    'site:instagram.com فروش اکانت کلش',
]

GAMER_MARKETPLACE_QUERIES = [
    'خرید اکانت کلش رویال site:divar.ir',
    'خرید سی پی کالاف site:divar.ir',
    'خرید یوسی پابجی site:divar.ir',
    'خرید اکانت ولورانت site:divar.ir',
    'گیفت کارت پلی استیشن site:divar.ir',
    'لوازم گیمینگ دست دوم site:divar.ir',
    'هدست گیمینگ site:divar.ir',
    'ماوس گیمینگ site:divar.ir',
    'خرید اکانت بازی site:sheypoor.com',
    'گیفت کارت site:sheypoor.com',
    'کنسول بازی دست دوم site:sheypoor.com',
]

GAMER_FORUM_QUERIES = [
    'گیمر ایرانی شماره تماس',
    'فروش اکانت کلش شماره',
    'فروش سی پی کالاف شماره',
    'فروش یوسی پابجی شماره',
    'گیفت کارت شماره تماس',
    'بازی کلش رویال ایرانی',
    'بازی کالاف دیوتی موبایل ایران',
    'بازی پابجی موبایل ایران',
]

# ── Web search ─────────────────────────────────────────────────────

async def _web_search(query: str, max_results: int = 10) -> list[dict]:
    settings = get_settings()
    if settings.openrouter_api_key:
        try: return await _search_openrouter(query, settings.openrouter_api_key, max_results)
        except: pass
    if settings.tavily_api_key:
        try: return await _search_tavily(query, settings.tavily_api_key, max_results)
        except: pass
    if settings.groq_api_key:
        try: return await _search_groq(query, settings.groq_api_key, max_results)
        except: pass
    return []


async def _search_openrouter(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=25) as client:
        headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
        try:
            r = await client.get('https://openrouter.ai/api/v1/models', headers=headers)
            models = [m['id'] for m in (r.json().get('data') or []) if ':free' in m.get('id', '')][:4]
        except: models = ['meta-llama/llama-3.3-70b-instruct:free']
        if not models: models = ['meta-llama/llama-3.3-70b-instruct:free']
        system = 'تو جستجوگر وب هستی. فقط JSON معتبر بده.'
        user = f'عبارت: {query}\n\nنتایج واقعی:\n{{"results":[{{"title":"...","url":"...","description":"..."}}]}}'
        for model in models:
            try:
                h = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'HTTP-Referer': 'https://game-lead-finder.onrender.com', 'X-Title': 'Game Lead Finder'}
                r = await client.post('https://openrouter.ai/api/v1/chat/completions', headers=h, json={
                    'model': model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                    'temperature': 0.15, 'max_tokens': 2000, 'plugins': [{'id': 'web', 'max_results': max_results}],
                })
                if r.status_code >= 400: continue
                content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
                try: parsed = json.loads(content)
                except json.JSONDecodeError:
                    match = re.search(r'\{.*\}', content, re.S)
                    if match: parsed = json.loads(match.group(0))
                    else: continue
                raw = parsed.get('results') or parsed.get('leads') or []
                out = [{'title': i.get('title', ''), 'url': i.get('url', ''), 'description': i.get('description', '')} for i in raw if isinstance(i, dict) and i.get('url')]
                if out: return out[:max_results]
            except: continue
    return []


async def _search_tavily(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post('https://api.tavily.com/search', json={'api_key': api_key, 'query': query, 'max_results': max_results})
        if r.status_code != 200: return []
        return [{'title': i.get('title', ''), 'url': i.get('url', ''), 'description': i.get('content', '')[:300]} for i in (r.json().get('results') or [])]


async def _search_groq(query: str, api_key: str, max_results: int) -> list[dict]:
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    system = 'تو لیست گیمرهای ایرانی رو میشناسی. فقط JSON بده.'
    user = f'برای "{query}" افراد/گروه‌های واقعی رو برگردون.\n{{"results":[{{"title":"...","url":"...","description":"..."}}]}}'
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post('https://api.groq.com/openai/v1/chat/completions', headers=headers, json={
                'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'temperature': 0.2, 'max_tokens': 1500,
            })
            if r.status_code != 200: return []
            content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
            try: parsed = json.loads(content)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', content, re.S)
                parsed = json.loads(match.group(0)) if match else {'results': []}
            return [{'title': i.get('title', ''), 'url': i.get('url', ''), 'description': i.get('description', '')} for i in (parsed.get('results') or []) if isinstance(i, dict) and i.get('url')][:max_results]
    except: return []


# ── Telegram group scraping ────────────────────────────────────────

async def scrape_tg_group(username: str) -> list[dict]:
    """Extract active members from public Telegram group preview."""
    url = f'https://t.me/s/{username}'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Accept-Language': 'fa,en;q=0.9'}
    found = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200: return found
            html = r.text[:2_000_000]

        # 1. Message authors (people who posted in the group)
        authors = re.findall(r'class="tgme_widget_message_author[^"]*"[^>]*>[^<]*<a[^>]*href="https://t\.me/([A-Za-z0-9_]+)"', html)
        seen = set()
        for u in authors:
            u = u.lower()
            if u not in seen and u != username.lower() and len(u) >= 3:
                seen.add(u)
                found.append({'type': 'tg_member', 'username': u, 'source_group': username, 'platform': 'telegram'})

        # 2. Phone numbers in messages
        phones = re.findall(r'(?:\+98|0098|0)?9\d{9}', html)
        for phone in set(phones):
            if phone not in seen:
                seen.add(phone)
                found.append({'type': 'phone', 'phone': phone, 'source_group': username, 'platform': 'phone'})

        # 3. Instagram links in messages
        ig_links = re.findall(r'instagram\.com/([A-Za-z0-9_.]{2,60})', html)
        for ig in set(ig_links):
            ig = ig.lower()
            if ig not in seen and ig not in {'p', 'reel', 'explore', 'accounts'}:
                seen.add(ig)
                found.append({'type': 'ig_member', 'username': ig, 'source_group': username, 'platform': 'instagram'})

        # 4. Other Telegram links mentioned
        tg_links = re.findall(r't\.me/([A-Za-z0-9_]{3,80})', html)
        for tg in set(tg_links):
            tg = tg.lower()
            if tg not in seen and tg != username.lower() and tg not in {'joinchat', 'addstickers', 'share', 's', 'c', 'iv'}:
                seen.add(tg)
                found.append({'type': 'tg_mentioned', 'username': tg, 'source_group': username, 'platform': 'telegram'})

    except: pass
    return found


# ── Extract leads from search results ──────────────────────────────

def _extract_people(results: list[dict], source: str) -> list[dict]:
    leads = []
    for item in results:
        url = item.get('url', '')
        title = item.get('title', '')
        desc = item.get('description', '')
        text = f'{title} {desc} {url}'

        phones = re.findall(r'(?:\+98|0098|0)?9\d{9}', text)
        ig = re.findall(r'(?:instagram\.com/|@)([A-Za-z0-9_.]{2,60})', text)
        ig = [x for x in ig if x.lower() not in {'p', 'reel', 'explore', 'accounts', 'stories'}]
        tg = re.findall(r'(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})', text)
        tg = [x for x in tg if x.lower() not in {'joinchat', 'addstickers', 'share', 'login', 's', 'c', 'iv'}]

        if not phones and not ig and not tg:
            continue

        cat = _detect_game(text)

        leads.append({
            'source': source, 'entity_type': 'gamer',
            'title': title[:500], 'url': url, 'description': desc[:500],
            'phone': phones[0] if phones else None,
            'instagram': f'https://instagram.com/{ig[0]}' if ig else None,
            'telegram': f'https://t.me/{tg[0]}' if tg else None,
            'category': cat, 'keyword': source,
        })
    return leads


def _detect_game(text: str) -> str | None:
    text = text.lower().replace('ي', 'ی').replace('ك', 'ک')
    games = {
        'کالاف': ['کالاف', 'call of duty', 'cod', 'warzone'],
        'پابجی': ['پابجی', 'pubg'],
        'کلش': ['کلش', 'clash', 'رویال'],
        'ولورانت': ['ولورانت', 'valorant'],
        'فری فایر': ['فری فایر', 'free fire'],
        'فورتنایت': ['فورتنایت', 'fortnite'],
        'گیفت کارت': ['گیفت کارت', 'gift card', 'psn', 'استیم'],
        'اکانت': ['اکانت', 'account'],
    }
    best, best_hits = None, 0
    for g, terms in games.items():
        hits = sum(1 for t in terms if t in text)
        if hits > best_hits:
            best_hits = hits
            best = g
    return best


# ── Main collector ─────────────────────────────────────────────────

async def find_members(
    db: Session,
    *,
    sources: list[str] | None = None,
    city: str = 'تهران',
    max_per_query: int = 8,
) -> dict:
    """Find gaming community members/users.

    sources: telegram_groups, instagram, marketplace, forums, all
    """
    sources = sources or ['telegram_groups', 'marketplace']
    summary = {'total_found': 0, 'saved': 0, 'duplicates': 0, 'by_source': {}, 'errors': []}
    all_leads: list[dict] = []

    # ── Telegram Groups ──
    if 'telegram_groups' in sources:
        run = start_run(db, 'member_finder_tg', f'telegram | {city}')
        count = 0
        try:
            for query in GAMER_TELEGRAM_QUERIES[:8]:
                results = await _web_search(query, max_per_query)
                for item in results:
                    url = item.get('url', '')
                    m = re.search(r't\.me/([A-Za-z0-9_]{3,80})', url)
                    if not m: continue
                    username = m.group(1).lower()
                    if username in {'joinchat', 'addstickers', 'share', 's', 'c', 'iv'}: continue

                    # اسکرپ اعضای فعال گروه
                    members = await scrape_tg_group(username)
                    for member in members:
                        if member['type'] == 'tg_member':
                            all_leads.append({
                                'source': 'tg_group_member', 'entity_type': 'gamer',
                                'title': f'@{member["username"]} عضو گروه {username}',
                                'url': f'https://t.me/{member["username"]}',
                                'telegram': f'https://t.me/{member["username"]}',
                                'city': city, 'keyword': 'گروه تلگرام',
                            })
                        elif member['type'] == 'phone':
                            all_leads.append({
                                'source': 'tg_group_member', 'entity_type': 'gamer',
                                'title': f'شماره از گروه {username}',
                                'url': f'https://t.me/{username}',
                                'phone': member['phone'], 'city': city, 'keyword': 'گروه تلگرام',
                            })
                        elif member['type'] == 'ig_member':
                            all_leads.append({
                                'source': 'tg_group_member', 'entity_type': 'gamer',
                                'title': f'@{member["username"]} از گروه {username}',
                                'url': f'https://instagram.com/{member["username"]}',
                                'instagram': f'https://instagram.com/{member["username"]}',
                                'city': city, 'keyword': 'گروه تلگرام',
                            })
                        count += 1
            summary['by_source']['telegram_groups'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'TG: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Marketplace (Divar/Sheypoor) ──
    if 'marketplace' in sources:
        run = start_run(db, 'member_finder_marketplace', f'marketplace | {city}')
        count = 0
        try:
            for query in GAMER_MARKETPLACE_QUERIES[:6]:
                results = await _web_search(query, max_per_query)
                leads = _extract_people(results, 'marketplace_gamer')
                all_leads.extend(leads)
                count += len(leads)
            summary['by_source']['marketplace'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'Marketplace: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Instagram ──
    if 'instagram' in sources:
        run = start_run(db, 'member_finder_ig', f'instagram | {city}')
        count = 0
        try:
            for query in GAMER_INSTAGRAM_QUERIES[:5]:
                results = await _web_search(query, max_per_query)
                leads = _extract_people(results, 'ig_gamer')
                all_leads.extend(leads)
                count += len(leads)
            summary['by_source']['instagram'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'IG: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Forums ──
    if 'forums' in sources:
        run = start_run(db, 'member_finder_forums', f'forums | {city}')
        count = 0
        try:
            for query in GAMER_FORUM_QUERIES[:4]:
                results = await _web_search(query, max_per_query)
                leads = _extract_people(results, 'forum_gamer')
                all_leads.extend(leads)
                count += len(leads)
            summary['by_source']['forums'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'Forums: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Save ──
    summary['total_found'] = len(all_leads)
    for lead_data in all_leads:
        try:
            _, is_new = upsert_lead(db, lead_data)
            if is_new: summary['saved'] += 1
            else: summary['duplicates'] += 1
        except: pass

    return summary


async def find_members_from_existing_groups(db: Session) -> dict:
    """Extract members from Telegram groups already in the database."""
    summary = {'groups_scanned': 0, 'members_found': 0, 'saved': 0}

    # پیدا کردن همه لینک‌های تلگرام توی دیتابیس
    leads = list(db.scalars(
        select(Lead).where(
            or_(Lead.telegram.isnot(None), Lead.url.ilike('%t.me%'))
        ).limit(100)
    ).all())

    seen_groups = set()
    for lead in leads:
        # استخراج یوزرنیم گروه
        url = lead.telegram or lead.url or ''
        m = re.search(r't\.me/([A-Za-z0-9_]{3,80})', url)
        if not m: continue
        username = m.group(1).lower()
        if username in seen_groups or username in {'joinchat', 'addstickers', 'share', 's', 'c'}:
            continue
        seen_groups.add(username)
        summary['groups_scanned'] += 1

        members = await scrape_tg_group(username)
        for member in members:
            summary['members_found'] += 1
            if member['type'] == 'tg_member':
                data = {
                    'source': 'existing_group_member', 'entity_type': 'gamer',
                    'title': f'@{member["username"]} از گروه {username}',
                    'url': f'https://t.me/{member["username"]}',
                    'telegram': f'https://t.me/{member["username"]}',
                    'keyword': 'گروه موجود',
                }
            elif member['type'] == 'phone':
                data = {
                    'source': 'existing_group_member', 'entity_type': 'gamer',
                    'title': f'شماره از گروه {username}',
                    'url': f'https://t.me/{username}',
                    'phone': member['phone'], 'keyword': 'گروه موجود',
                }
            else:
                continue
            try:
                _, is_new = upsert_lead(db, data)
                if is_new: summary['saved'] += 1
            except: pass

    return summary
