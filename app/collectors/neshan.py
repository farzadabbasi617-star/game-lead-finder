from __future__ import annotations

from urllib.parse import quote_plus
import httpx


async def neshan_search(api_key: str, term: str, *, city: str, lat: float, lng: float, limit: int = 10) -> list[dict]:
    params = {'term': term, 'lat': lat, 'lng': lng}
    headers = {'Api-Key': api_key}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get('https://api.neshan.org/v1/search', params=params, headers=headers)
        r.raise_for_status()
        payload = r.json()

    leads: list[dict] = []
    for item in (payload.get('items') or [])[:limit]:
        title = item.get('title') or term
        address = item.get('address') or item.get('region')
        loc = item.get('location') or {}
        item_lng = loc.get('x')
        item_lat = loc.get('y')
        # Public search URL; Neshan search API response does not always expose a stable place page URL.
        url = f"https://neshan.org/maps/search/{quote_plus(title)}?lat={item_lat or lat}&lng={item_lng or lng}"
        leads.append({
            'source': 'neshan',
            'entity_type': 'business',
            'title': title,
            'url': url,
            'description': item.get('type') or item.get('category'),
            'address': address,
            'city': city,
            'query': term,
            'keyword': term,
            'lat': item_lat,
            'lng': item_lng,
        })
    return leads
