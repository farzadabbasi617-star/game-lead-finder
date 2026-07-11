"""Unified search endpoint — replaces separate OpenRouter/AI/Collector sections."""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repository import upsert_lead, start_run, finish_run


# ── Divar / Sheypoor direct search ────────────────────────────────

async def search_divar(query: str, city: str = 'تهران', max_results: int = 10) -> list[dict]:
    """Search Divar for gaming-related ads using web search."""
    settings = get_settings()
    search_query = f'site:divar.ir {query} {city}'
    results = await _web_search(search_query, settings, max_results)
    leads = []
    for item in results:
        url = item.get('url', '')
        if 'divar.ir' not in url:
            continue
        title = item.get('title', '')
        desc = item.get('description', '')
        # Extract phone from description if available
        phones = re.findall(r'(?:\+98|0098|0)?9\d{9}', desc)
        leads.append({
            'source': 'divar_web',
            'entity_type': 'ad',
            'title': title[:500],
            'url': url,
            'description': desc[:500],
            'city': city,
            'phone': phones[0] if phones else None,
        })
    return leads


async def search_sheypoor(query: str, city: str = 'تهران', max_results: int = 10) -> list[dict]:
    """Search Sheypoor for gaming-related ads."""
    settings = get_settings()
    search_query = f'site:sheypoor.com {query} {city}'
    results = await _web_search(search_query, settings, max_results)
    leads = []
    for item in results:
        url = item.get('url', '')
        if 'sheypoor.com' not in url:
            continue
        title = item.get('title', '')
        desc = item.get('description', '')
        phones = re.findall(r'(?:\+98|0098|0)?9\d{9}', desc)
        leads.append({
            'source': 'sheypoor_web',
            'entity_type': 'ad',
            'title': title[:500],
            'url': url,
            'description': desc[:500],
            'city': city,
            'phone': phones[0] if phones else None,
        })
    return leads


async def search_divar_sheypoor(query: str, city: str = 'تهران', max_results: int = 10) -> list[dict]:
    """Search both Divar and Sheypoor concurrently."""
    divar_task = search_divar(query, city, max_results)
    sheypoor_task = search_sheypoor(query, city, max_results)
    divar_results, sheypoor_results = await asyncio.gather(divar_task, sheypoor_task, return_exceptions=True)
    all_leads = []
    if isinstance(divar_results, list):
        all_leads.extend(divar_results)
    if isinstance(sheypoor_results, list):
        all_leads.extend(sheypoor_results)
    return all_leads


# ── Generic web search (OpenRouter → Tavily → Groq) ───────────────

async def _web_search(query: str, settings: Any, max_results: int = 10) -> list[dict]:
    """Search the web using available APIs. Returns list of {title, url, description}."""

    # Try OpenRouter
    if settings.openrouter_api_key:
        try:
            return await _search_openrouter(query, settings.openrouter_api_key, max_results)
        except Exception:
            pass

    # Try Tavily
    if settings.tavily_api_key:
        try:
            return await _search_tavily(query, settings.tavily_api_key, max_results)
        except Exception:
            pass

    # Try Groq
    if settings.groq_api_key:
        try:
            return await _search_groq(query, settings.groq_api_key, max_results)
        except Exception:
            pass

    return []


async def _search_openrouter(query: str, api_key: str, max_results: int) -> list[dict]:
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    async with httpx.AsyncClient(timeout=25) as client:
        try:
            r = await client.get('https://openrouter.ai/api/v1/models', headers=headers)
            models = []
            for m in (r.json().get('data') or []):
                mid = m.get('id', '')
                if ':free' in mid:
                    pricing = m.get('pricing') or {}
                    if str(pricing.get('prompt', '1')) == '0' and str(pricing.get('completion', '1')) == '0':
                        models.append(mid)
            models = models[:4]
        except Exception:
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
                import json
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
    import json
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


# ── Unified search: combines all sources ───────────────────────────

async def unified_search(
    db: Session,
    *,
    topic: str,
    city: str = 'تهران',
    sources: list[str] | None = None,
    max_results: int = 10,
) -> dict:
    """Run a unified search across selected sources.

    sources: list of 'openrouter', 'tavily', 'divar', 'sheypoor', 'search_links'
    """
    settings = get_settings()
    sources = sources or ['openrouter']
    summary = {'total_found': 0, 'total_saved': 0, 'total_duplicates': 0, 'by_source': {}, 'errors': []}

    # Build search queries based on topic
    base_queries = [
        f'{topic} {city}',
        f'site:t.me {topic} {city}',
        f'site:instagram.com {topic} {city}',
    ]

    all_leads: list[dict] = []

    # ── OpenRouter direct web search ──
    if 'openrouter' in sources and settings.openrouter_api_key:
        run = start_run(db, 'unified_openrouter', topic)
        try:
            for q in base_queries[:2]:
                results = await _search_openrouter(q, settings.openrouter_api_key, max_results)
                for item in results:
                    url = item.get('url', '')
                    title = item.get('title', '')
                    desc = item.get('description', '')
                    phones = re.findall(r'(?:\+98|0098|0)?9\d{9}', desc)
                    ig = re.findall(r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,60})', desc + ' ' + url)
                    tg = re.findall(r'(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})', desc + ' ' + url)
                    all_leads.append({
                        'source': 'openrouter_web_ai', 'entity_type': 'ai_web_lead',
                        'title': title[:500], 'url': url, 'description': desc[:500],
                        'city': city, 'phone': phones[0] if phones else None,
                        'instagram': f'https://instagram.com/{ig[0]}' if ig else None,
                        'telegram': f'https://t.me/{tg[0]}' if tg else None,
                        'keyword': topic, 'query': topic,
                    })
            summary['by_source']['openrouter'] = len([l for l in all_leads if l['source'] == 'openrouter_web_ai'])
            finish_run(db, run, summary['by_source']['openrouter'], 0)
        except Exception as exc:
            summary['errors'].append(f'OpenRouter: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Tavily ──
    if 'tavily' in sources and settings.tavily_api_key:
        run = start_run(db, 'unified_tavily', topic)
        try:
            for q in base_queries:
                results = await _search_tavily(q, settings.tavily_api_key, max_results)
                for item in results:
                    url = item.get('url', '')
                    title = item.get('title', '')
                    desc = item.get('description', '')
                    phones = re.findall(r'(?:\+98|0098|0)?9\d{9}', desc)
                    ig = re.findall(r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,60})', desc + ' ' + url)
                    tg = re.findall(r'(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})', desc + ' ' + url)
                    all_leads.append({
                        'source': 'tavily', 'entity_type': 'web_lead',
                        'title': title[:500], 'url': url, 'description': desc[:500],
                        'city': city, 'phone': phones[0] if phones else None,
                        'instagram': f'https://instagram.com/{ig[0]}' if ig else None,
                        'telegram': f'https://t.me/{tg[0]}' if tg else None,
                        'keyword': topic, 'query': topic,
                    })
            summary['by_source']['tavily'] = len([l for l in all_leads if l['source'] == 'tavily'])
            finish_run(db, run, summary['by_source']['tavily'], 0)
        except Exception as exc:
            summary['errors'].append(f'Tavily: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Divar ──
    if 'divar' in sources:
        run = start_run(db, 'unified_divar', topic)
        try:
            divar_leads = await search_divar(topic, city, max_results)
            all_leads.extend(divar_leads)
            summary['by_source']['divar'] = len(divar_leads)
            finish_run(db, run, len(divar_leads), 0)
        except Exception as exc:
            summary['errors'].append(f'Divar: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Sheypoor ──
    if 'sheypoor' in sources:
        run = start_run(db, 'unified_sheypoor', topic)
        try:
            sheypoor_leads = await search_sheypoor(topic, city, max_results)
            all_leads.extend(sheypoor_leads)
            summary['by_source']['sheypoor'] = len(sheypoor_leads)
            finish_run(db, run, len(sheypoor_leads), 0)
        except Exception as exc:
            summary['errors'].append(f'Sheypoor: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Search Links (free, no API) ──
    if 'search_links' in sources:
        from app.collectors.search_links import build_search_link_leads
        run = start_run(db, 'unified_search_links', topic)
        try:
            links = build_search_link_leads(topic, city)
            all_leads.extend(links)
            summary['by_source']['search_links'] = len(links)
            finish_run(db, run, len(links), 0)
        except Exception as exc:
            summary['errors'].append(f'Search Links: {str(exc)[:150]}')
            finish_run(db, run, 0, 0, str(exc)[:150])

    # ── Save all leads ──
    summary['total_found'] = len(all_leads)
    saved_ids = []
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
