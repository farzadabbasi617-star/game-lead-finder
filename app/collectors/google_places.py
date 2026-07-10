from __future__ import annotations

import httpx


async def google_places_text_search(api_key: str, query: str, *, city: str | None = None, limit: int = 10, fetch_details: bool = True) -> list[dict]:
    full_query = f'{query} {city}'.strip() if city else query
    params = {'query': full_query, 'key': api_key, 'language': 'fa'}
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.get('https://maps.googleapis.com/maps/api/place/textsearch/json', params=params)
        r.raise_for_status()
        payload = r.json()
        status = payload.get('status')
        if status not in {'OK', 'ZERO_RESULTS'}:
            raise RuntimeError(f'Google Places status={status}: {payload.get("error_message", "")}'.strip())

        leads: list[dict] = []
        for item in (payload.get('results') or [])[:limit]:
            place_id = item.get('place_id')
            details = {}
            if fetch_details and place_id:
                details = await google_place_details(client, api_key, place_id)
            name = details.get('name') or item.get('name') or full_query
            geometry = (details.get('geometry') or item.get('geometry') or {}).get('location') or {}
            url = details.get('url') or (f'https://www.google.com/maps/place/?q=place_id:{place_id}' if place_id else f'https://www.google.com/maps/search/{full_query}')
            leads.append({
                'source': 'google_places',
                'entity_type': 'business',
                'title': name,
                'url': url,
                'description': ', '.join(item.get('types') or []),
                'address': details.get('formatted_address') or item.get('formatted_address'),
                'phone': details.get('formatted_phone_number') or details.get('international_phone_number'),
                'website': details.get('website'),
                'rating': details.get('rating') or item.get('rating'),
                'review_count': details.get('user_ratings_total') or item.get('user_ratings_total'),
                'city': city,
                'query': full_query,
                'keyword': query,
                'lat': geometry.get('lat'),
                'lng': geometry.get('lng'),
            })
        return leads


async def google_place_details(client: httpx.AsyncClient, api_key: str, place_id: str) -> dict:
    params = {
        'place_id': place_id,
        'key': api_key,
        'language': 'fa',
        'fields': 'name,formatted_address,formatted_phone_number,international_phone_number,website,url,rating,user_ratings_total,geometry',
    }
    r = await client.get('https://maps.googleapis.com/maps/api/place/details/json', params=params)
    r.raise_for_status()
    payload = r.json()
    if payload.get('status') not in {'OK', 'ZERO_RESULTS'}:
        return {}
    return payload.get('result') or {}
