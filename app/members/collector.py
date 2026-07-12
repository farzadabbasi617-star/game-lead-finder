"""Member Finder — finds actual gamers/users through legal public data.

ENHANCED version with robust Telegram scraping and multiple extraction methods.
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

# ── Known gaming Telegram groups (direct scraping) ─────────────────
KNOWN_GAMING_GROUPS = [
    'pubg_mobile_iran', 'CallofDutyMobileFarsi', 'FreeFireIran', 'ValorantFarsi',
    'ClashRoyaleIran', 'ClashOfClansFarsi', 'SteamFarsi', 'PlayStationIran',
    'XboxFarsi', 'NintendoFarsi', 'GamingIran', 'GameNetIran',
    'GiftCardIran', 'PSNGiftCard', 'SteamGiftCard',
    'pubg_mobile_farsi', 'cod_mobile_iran', 'free_fire_iran',
    'valorant_iran', 'clash_royale_iran', 'fortnite_iran',
    'apex_legends_iran', 'gta_iran', 'minecraft_iran',
    'ps5_iran', 'ps4_iran', 'xbox_iran',
    'gaming_community_iran', 'gamer_iran', 'game_store_iran',
    'gift_card_iran', 'account_game_iran',
]

# ── Queries focused on finding GAMERS ──────────────────────────────

GAMER_TG_QUERIES = [
    'گروه تلگرام گیمرهای ایرانی',
    'گروه تلگرام کالاف دیوتی موبایل',
    'گروه تلگرام پابجی موبایل',
    'گروه تلگرام فروش اکانت بازی',
    'گروه تلگرام خرید سی پی کالاف',
    'گروه تلگرام خرید یوسی پابجی',
    'گروه تلگرام گیفت کارت',
    'گروه تلگرام فری فایر ایران',
    'site:t.me کالاف',
    'site:t.me پابجی',
    'site:t.me فروش اکانت',
    'site:t.me گیفت کارت',
    'site:t.me خرید سی پی',
    'site:t.me خرید یوسی',
]

GAMER_IG_QUERIES = [
    'گیمر ایرانی اینستاگرام',
    'پیج فروش اکانت کلش اینستاگرام',
    'پیج فروش سی پی کالاف اینستاگرام',
    'site:instagram.com گیمر ایرانی',
    'site:instagram.com فروش اکانت کلش',
    'site:instagram.com فروش سی پی',
]

GAMER_DIVAR_QUERIES = [
    'خرید اکانت کلش site:divar.ir',
    'خرید سی پی کالاف site:divar.ir',
    'خرید یوسی پابجی site:divar.ir',
    'گیفت کارت site:divar.ir',
    'اکانت ولورانت site:divar.ir',
    'هدست گیمینگ site:divar.ir',
    'خرید اکانت بازی site:sheypoor.com',
    'گیفت کارت site:sheypoor.com',
]


# ── Web search ─────────────────────────────────────────────────────

async def _web_search(query: str, max_results: int = 10) -> list[dict]:
    settings = get_settings()
    if settings.openrouter_api_key:
        try: return await _or_search(query, settings.openrouter_api_key, max_results)
        except: pass
    if settings.tavily_api_key:
        try: return await _tavily_search(query, settings.tavily_api_key, max_results)
        except: pass
    if settings.groq_api_key:
        try: return await _groq_search(query, settings.groq_api_key, max_results)
        except: pass
    return []


async def _or_search(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=25) as client:
        headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
        try:
            r = await client.get('https://openrouter.ai/api/v1/models', headers=headers)
            models = [m['id'] for m in (r.json().get('data') or []) if ':free' in m.get('id', '')][:4]
        except: models = ['meta-llama/llama-3.3-70b-instruct:free']
        if not models: models = ['meta-llama/llama-3.3-70b-instruct:free']
        system = 'تو جستجوگر وب هستی. فقط JSON بده.'
        user = f'عبارت: {query}\n\nنتایج:\n{{"results":[{{"title":"...","url":"...","description":"..."}}]}}'
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
                except: 
                    m = re.search(r'\{.*\}', content, re.S)
                    if m: parsed = json.loads(m.group(0))
                    else: continue
                raw = parsed.get('results') or parsed.get('leads') or []
                out = [{'title': i.get('title',''), 'url': i.get('url',''), 'description': i.get('description','')} for i in raw if isinstance(i, dict) and i.get('url')]
                if out: return out[:max_results]
            except: continue
    return []


async def _tavily_search(query: str, api_key: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post('https://api.tavily.com/search', json={'api_key': api_key, 'query': query, 'max_results': max_results})
        if r.status_code != 200: return []
        return [{'title': i.get('title',''), 'url': i.get('url',''), 'description': i.get('content','')[:300]} for i in (r.json().get('results') or [])]


async def _groq_search(query: str, api_key: str, max_results: int) -> list[dict]:
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    system = 'تو لیست گیمرهای ایرانی رو میشناسی. فقط JSON بده.'
    user = f'برای "{query}" افراد واقعی رو برگردون.\n{{"results":[{{"title":"...","url":"...","description":"..."}}]}}'
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post('https://api.groq.com/openai/v1/chat/completions', headers=headers, json={
                'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'temperature': 0.2, 'max_tokens': 1500,
            })
            if r.status_code != 200: return []
            content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
            try: parsed = json.loads(content)
            except:
                m = re.search(r'\{.*\}', content, re.S)
                parsed = json.loads(m.group(0)) if m else {'results': []}
            return [{'title': i.get('title',''), 'url': i.get('url',''), 'description': i.get('description','')} for i in (parsed.get('results') or []) if isinstance(i, dict) and i.get('url')][:max_results]
    except: return []


# ── Telegram group scraping (ENHANCED) ─────────────────────────────

async def scrape_tg_group(username: str) -> list[dict]:
    """Extract active members from public Telegram group preview.
    
    Multiple extraction methods for maximum coverage.
    """
    url = f'https://t.me/s/{username}'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', 'Accept-Language': 'fa,en;q=0.9'}
    found = []
    seen = set()
    
    try:
        async with httpx.AsyncClient(timeout=18, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return found
            html = r.text[:3_000_000]

        # === METHOD 1: Message author links ===
        # Pattern: <a class="tgme_widget_message_owner_name" ... href="https://t.me/username">
        author_patterns = [
            r'href="https://t\.me/([A-Za-z0-9_]{3,80})"[^>]*class="tgme_widget_message_author',
            r'class="tgme_widget_message_author[^"]*"[^>]*>[^<]*<a[^>]*href="https://t\.me/([A-Za-z0-9_]+)"',
            r'data-post="[^"]*/([A-Za-z0-9_]{3,80})/"',
            r'class="tgme_widget_message_from_author[^"]*"[^>]*>([^<]+)<',
        ]
        for pat in author_patterns:
            for m in re.finditer(pat, html):
                u = m.group(1).strip().lstrip('@').lower()
                if u and u not in seen and u != username.lower() and len(u) >= 3 and u not in {'joinchat','addstickers','share','login','s','c','iv','proxy','blog','stickers'}:
                    seen.add(u)
                    found.append({'type': 'tg_member', 'username': u, 'source_group': username, 'platform': 'telegram'})

        # === METHOD 2: All t.me links in messages ===
        for m in re.finditer(r'https?://t\.me/([A-Za-z0-9_]{3,80})', html):
            u = m.group(1).lower()
            if u not in seen and u != username.lower() and u not in {'joinchat','addstickers','share','login','s','c','iv','proxy','blog','stickers'}:
                seen.add(u)
                found.append({'type': 'tg_mentioned', 'username': u, 'source_group': username, 'platform': 'telegram'})

        # === METHOD 3: @username mentions in message text ===
        skip_words = {'telegram', 'instagram', 'facebook', 'twitter', 'youtube', 'gmail', 'yahoo', 'hotmail', 'joinchat', 'addstickers', 'share', 'login', 'proxy', 'blog', 'stickers', 'username', 'channel', 'group', 'chat', 'message', 'photo', 'video', 'file', 'document'}
        for m in re.finditer(r'@([A-Za-z0-9_]{3,80})', html):
            u = m.group(1).lower()
            if u not in seen and u != username.lower() and len(u) >= 3 and u not in skip_words and not u.endswith('.com') and not u.endswith('.ir'):
                seen.add(u)
                found.append({'type': 'tg_mentioned', 'username': u, 'source_group': username, 'platform': 'telegram'})

        # === METHOD 4: Phone numbers in messages ===
        for m in re.finditer(r'(?:\+98|0098|0)?9\d{9}', html):
            phone = m.group(0)
            if phone not in seen and len(phone) >= 10:
                seen.add(phone)
                found.append({'type': 'phone', 'phone': phone, 'source_group': username, 'platform': 'phone'})

        # === METHOD 5: Instagram links in messages ===
        for m in re.finditer(r'instagram\.com/([A-Za-z0-9_.]{2,60})', html):
            ig = m.group(1).lower()
            if ig not in seen and ig not in {'p','reel','explore','accounts','stories'}:
                seen.add(ig)
                found.append({'type': 'ig_member', 'username': ig, 'source_group': username, 'platform': 'instagram'})

    except Exception:
        pass
    return found


# ── Extract people from search results (ENHANCED) ──────────────────

def _extract_people(results: list[dict], source: str) -> list[dict]:
    leads = []
    for item in results:
        url = item.get('url', '')
        title = item.get('title', '')
        desc = item.get('description', '')
        text = f'{title} {desc} {url}'

        # Phone numbers (Iranian format)
        phones = re.findall(r'(?:\+98|0098|0)?9\d{9}', text)

        # Instagram usernames
        ig_patterns = [
            r'instagram\.com/([A-Za-z0-9_.]{2,60})',
            r'@([A-Za-z0-9_.]{2,60})',  # @username
        ]
        ig = []
        for pat in ig_patterns:
            for m in re.finditer(pat, text):
                u = m.group(1).lower()
                if u not in {'p','reel','explore','accounts','stories','gmail','yahoo','hotmail'} and len(u) >= 2:
                    ig.append(u)
                    break
        ig = list(dict.fromkeys(ig))  # dedupe preserving order

        # Telegram usernames
        tg_patterns = [
            r'(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})',
            r'@([A-Za-z0-9_]{3,80})',  # @username (check after IG)
        ]
        tg = []
        for pat in tg_patterns:
            for m in re.finditer(pat, text):
                u = m.group(1).lower()
                if u not in {'joinchat','addstickers','share','login','s','c','iv','gmail','yahoo','hotmail'} and len(u) >= 3:
                    tg.append(u)
                    break
        tg = list(dict.fromkeys(tg))

        if not phones and not ig and not tg:
            continue

        cat = _detect_game(text)

        lead = {
            'source': source, 'entity_type': 'gamer',
            'title': title[:500], 'url': url, 'description': desc[:500],
            'phone': phones[0] if phones else None,
            'instagram': f'https://instagram.com/{ig[0]}' if ig else None,
            'telegram': f'https://t.me/{tg[0]}' if tg else None,
            'category': cat, 'keyword': source,
        }
        leads.append(lead)
    return leads


def _detect_game(text: str) -> str | None:
    text = text.lower().replace('ي', 'ی').replace('ك', 'ک')
    games = {
        'کالاف': ['کالاف', 'call of duty', 'cod', 'سی پی', 'cp'],
        'پابجی': ['پابجی', 'pubg', 'یوسی', 'uc'],
        'کلش': ['کلش', 'clash', 'رویال'],
        'ولورانت': ['ولورانت', 'valorant'],
        'فری فایر': ['فری فایر', 'free fire', 'جم', 'الماس'],
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


# ── Main collector (ENHANCED) ──────────────────────────────────────

async def find_members(
    db: Session,
    *,
    sources: list[str] | None = None,
    city: str = 'تهران',
    max_per_query: int = 8,
) -> dict:
    sources = sources or ['telegram_groups', 'marketplace']
    summary = {'total_found': 0, 'saved': 0, 'duplicates': 0, 'by_source': {}, 'errors': []}
    all_leads: list[dict] = []

    # ── Telegram Groups ──
    if 'telegram_groups' in sources:
        run = start_run(db, 'member_finder_tg', f'telegram | {city}')
        count = 0
        try:
            # 1. اسکرپ مستقیم گروه‌های شناخته‌شده
            for username in KNOWN_GAMING_GROUPS:
                members = await scrape_tg_group(username)
                for member in members:
                    lead = _member_to_lead(member, username, city)
                    if lead:
                        all_leads.append(lead)
                        count += 1

            # 2. جستجوی وب برای پیدا کردن گروه‌های جدید
            for query in GAMER_TG_QUERIES[:8]:
                results = await _web_search(query, max_per_query)
                for item in results:
                    url = item.get('url', '')
                    desc = item.get('description', '')
                    for m in re.finditer(r'(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})', url + ' ' + desc):
                        username = m.group(1).lower()
                        if username in {'joinchat','addstickers','share','login','s','c','iv'}: continue
                        members = await scrape_tg_group(username)
                        for member in members:
                            lead = _member_to_lead(member, username, city)
                            if lead:
                                all_leads.append(lead)
                                count += 1
            summary['by_source']['telegram_groups'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'TG: {str(exc)[:200]}')
            finish_run(db, run, 0, 0, str(exc)[:200])

    # ── Marketplace ──
    if 'marketplace' in sources:
        run = start_run(db, 'member_finder_mkt', f'marketplace | {city}')
        count = 0
        try:
            for query in GAMER_DIVAR_QUERIES[:6]:
                results = await _web_search(query, max_per_query)
                leads = _extract_people(results, 'marketplace_gamer')
                for l in leads:
                    l['city'] = city
                all_leads.extend(leads)
                count += len(leads)
            summary['by_source']['marketplace'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'Marketplace: {str(exc)[:200]}')
            finish_run(db, run, 0, 0, str(exc)[:200])

    # ── Instagram ──
    if 'instagram' in sources:
        run = start_run(db, 'member_finder_ig', f'instagram | {city}')
        count = 0
        try:
            for query in GAMER_IG_QUERIES[:5]:
                results = await _web_search(query, max_per_query)
                leads = _extract_people(results, 'ig_gamer')
                for l in leads:
                    l['city'] = city
                all_leads.extend(leads)
                count += len(leads)
            summary['by_source']['instagram'] = count
            finish_run(db, run, count, 0)
        except Exception as exc:
            summary['errors'].append(f'IG: {str(exc)[:200]}')
            finish_run(db, run, 0, 0, str(exc)[:200])

    # ── Save all ──
    summary['total_found'] = len(all_leads)
    for lead_data in all_leads:
        try:
            _, is_new = upsert_lead(db, lead_data)
            if is_new: summary['saved'] += 1
            else: summary['duplicates'] += 1
        except: pass

    return summary


def _member_to_lead(member: dict, source_group: str, city: str) -> dict | None:
    if member['type'] == 'tg_member':
        return {
            'source': 'tg_group_member', 'entity_type': 'gamer',
            'title': f'@{member["username"]} از گروه {source_group}',
            'url': f'https://t.me/{member["username"]}',
            'telegram': f'https://t.me/{member["username"]}',
            'city': city, 'keyword': 'گروه تلگرام',
        }
    elif member['type'] == 'phone':
        return {
            'source': 'tg_group_member', 'entity_type': 'gamer',
            'title': f'شماره از گروه {source_group}',
            'url': f'https://t.me/{source_group}',
            'phone': member['phone'], 'city': city, 'keyword': 'گروه تلگرام',
        }
    elif member['type'] == 'ig_member':
        return {
            'source': 'tg_group_member', 'entity_type': 'gamer',
            'title': f'@{member["username"]} از گروه {source_group}',
            'url': f'https://instagram.com/{member["username"]}',
            'instagram': f'https://instagram.com/{member["username"]}',
            'city': city, 'keyword': 'گروه تلگرام',
        }
    elif member['type'] == 'tg_mentioned':
        return {
            'source': 'tg_group_member', 'entity_type': 'gamer',
            'title': f'@{member["username"]} از گروه {source_group}',
            'url': f'https://t.me/{member["username"]}',
            'telegram': f'https://t.me/{member["username"]}',
            'city': city, 'keyword': 'گروه تلگرام',
        }
    return None


async def find_members_from_existing_groups(db: Session) -> dict:
    summary = {'groups_scanned': 0, 'members_found': 0, 'saved': 0}
    leads = list(db.scalars(
        select(Lead).where(or_(Lead.telegram.isnot(None), Lead.url.ilike('%t.me%'))).limit(200)
    ).all())
    seen_groups = set()
    for lead in leads:
        url = lead.telegram or lead.url or ''
        for m in re.finditer(r't\.me/([A-Za-z0-9_]{3,80})', url):
            username = m.group(1).lower()
            if username in seen_groups or username in {'joinchat','addstickers','share','s','c','iv'}: continue
            seen_groups.add(username)
            summary['groups_scanned'] += 1
            members = await scrape_tg_group(username)
            for member in members:
                summary['members_found'] += 1
                data = _member_to_lead(member, username, lead.city or 'تهران')
                if data:
                    try:
                        _, is_new = upsert_lead(db, data)
                        if is_new: summary['saved'] += 1
                    except: pass
    return summary
