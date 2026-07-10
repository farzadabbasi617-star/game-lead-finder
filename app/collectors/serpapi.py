from __future__ import annotations

from urllib.parse import urlparse
import httpx


def classify_url(url: str) -> tuple[str, str]:
    host = urlparse(url).netloc.lower()
    if 'instagram.com' in host:
        return 'instagram_web', 'page'
    if host in {'t.me', 'telegram.me'} or host.endswith('.t.me') or 'telegram.me' in host:
        return 'telegram_web', 'channel'
    if 'balad.ir' in host:
        return 'balad_web', 'business'
    if 'divar.ir' in host:
        return 'divar_web', 'ad'
    if 'sheypoor.com' in host:
        return 'sheypoor_web', 'ad'
    if 'torob.com' in host:
        return 'torob_web', 'product'
    return 'web', 'website'


async def serpapi_search(api_key: str, query: str, *, keyword: str | None = None, city: str | None = None, num: int = 10) -> list[dict]:
    params = {
        'engine': 'google',
        'q': query,
        'api_key': api_key,
        'hl': 'fa',
        'gl': 'ir',
        'num': min(max(num, 1), 20),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get('https://serpapi.com/search.json', params=params)
        r.raise_for_status()
        payload = r.json()

    leads: list[dict] = []
    for item in payload.get('organic_results', []) or []:
        url = item.get('link')
        if not url:
            continue
        source, entity_type = classify_url(url)
        leads.append({
            'source': source,
            'entity_type': entity_type,
            'title': item.get('title') or url,
            'url': url,
            'description': item.get('snippet') or item.get('displayed_link'),
            'query': query,
            'keyword': keyword,
            'city': city,
        })
    return leads


def build_web_queries(keyword: str, city: str | None = None) -> list[str]:
    base = f'{keyword} {city}'.strip() if city else keyword
    return [
        base,
        f'site:t.me {base}',
        f'site:instagram.com {base}',
        f'site:balad.ir {base}',
        f'site:divar.ir {base}',
        f'site:sheypoor.com {base}',
        f'site:torob.com {base}',
    ]
