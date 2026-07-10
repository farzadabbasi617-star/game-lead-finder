from __future__ import annotations

import httpx
from app.collectors.serpapi import classify_url


def _lead_from_result(*, query: str, keyword: str | None, city: str | None, title: str | None, url: str | None, description: str | None, provider: str) -> dict | None:
    if not url:
        return None
    source, entity_type = classify_url(url)
    if source == 'web':
        source = provider
    else:
        source = f'{source}_{provider}'
    return {
        'source': source,
        'entity_type': entity_type,
        'title': title or url,
        'url': url,
        'description': description,
        'query': query,
        'keyword': keyword,
        'city': city,
    }


async def google_cse_search(api_key: str, cx: str, query: str, *, keyword: str | None = None, city: str | None = None, num: int = 10) -> list[dict]:
    params = {
        'key': api_key,
        'cx': cx,
        'q': query,
        'num': min(max(num, 1), 10),
        'hl': 'fa',
        'gl': 'ir',
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get('https://www.googleapis.com/customsearch/v1', params=params)
        r.raise_for_status()
        payload = r.json()
    leads = []
    for item in payload.get('items', []) or []:
        lead = _lead_from_result(
            query=query, keyword=keyword, city=city, provider='google_cse',
            title=item.get('title'), url=item.get('link'), description=item.get('snippet')
        )
        if lead:
            leads.append(lead)
    return leads


async def brave_search(api_key: str, query: str, *, keyword: str | None = None, city: str | None = None, num: int = 10) -> list[dict]:
    headers = {'X-Subscription-Token': api_key, 'Accept': 'application/json'}
    params = {
        'q': query,
        'count': min(max(num, 1), 20),
        'country': 'IR',
        'search_lang': 'fa',
        'ui_lang': 'fa-IR',
        'safesearch': 'moderate',
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get('https://api.search.brave.com/res/v1/web/search', headers=headers, params=params)
        r.raise_for_status()
        payload = r.json()
    leads = []
    for item in (payload.get('web') or {}).get('results', []) or []:
        lead = _lead_from_result(
            query=query, keyword=keyword, city=city, provider='brave',
            title=item.get('title'), url=item.get('url'), description=item.get('description')
        )
        if lead:
            leads.append(lead)
    return leads


async def serper_search(api_key: str, query: str, *, keyword: str | None = None, city: str | None = None, num: int = 10) -> list[dict]:
    headers = {'X-API-KEY': api_key, 'Content-Type': 'application/json'}
    payload = {'q': query, 'num': min(max(num, 1), 20), 'hl': 'fa', 'gl': 'ir'}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post('https://google.serper.dev/search', headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    leads = []
    for item in data.get('organic', []) or []:
        lead = _lead_from_result(
            query=query, keyword=keyword, city=city, provider='serper',
            title=item.get('title'), url=item.get('link'), description=item.get('snippet')
        )
        if lead:
            leads.append(lead)
    return leads


async def searchapi_search(api_key: str, query: str, *, keyword: str | None = None, city: str | None = None, num: int = 10) -> list[dict]:
    params = {
        'engine': 'google',
        'q': query,
        'api_key': api_key,
        'num': min(max(num, 1), 20),
        'hl': 'fa',
        'gl': 'ir',
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get('https://www.searchapi.io/api/v1/search', params=params)
        r.raise_for_status()
        payload = r.json()
    leads = []
    for item in payload.get('organic_results', []) or []:
        lead = _lead_from_result(
            query=query, keyword=keyword, city=city, provider='searchapi',
            title=item.get('title'), url=item.get('link'), description=item.get('snippet')
        )
        if lead:
            leads.append(lead)
    return leads


async def tavily_search(api_key: str, query: str, *, keyword: str | None = None, city: str | None = None, num: int = 10) -> list[dict]:
    # Tavily's current API expects the key in the Authorization header.
    # Older examples accepted api_key in the JSON body; using Bearer is the safer/current method.
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'query': query,
        'max_results': min(max(num, 1), 20),
        'search_depth': 'basic',
        'include_answer': False,
        'include_raw_content': False,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post('https://api.tavily.com/search', headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    leads = []
    for item in data.get('results', []) or []:
        lead = _lead_from_result(
            query=query, keyword=keyword, city=city, provider='tavily',
            title=item.get('title'), url=item.get('url'), description=item.get('content')
        )
        if lead:
            leads.append(lead)
    return leads
