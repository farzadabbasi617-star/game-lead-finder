from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import get_settings
from app.collectors.google_places import google_places_text_search
from app.collectors.neshan import neshan_search
from app.collectors.serpapi import build_web_queries, serpapi_search
from app.collectors.search_links import build_search_link_leads
from app.db.models import City, Keyword
from app.repository import finish_run, start_run, upsert_lead


async def run_collector(
    db: Session,
    *,
    source: str = 'all',
    keyword_limit: int = 5,
    city_limit: int = 3,
    result_limit: int = 8,
) -> dict:
    settings = get_settings()
    keywords = list(db.scalars(select(Keyword).where(Keyword.active == True).limit(keyword_limit)).all())
    cities = list(db.scalars(select(City).where(City.active == True).limit(city_limit)).all())

    summary = {'runs': 0, 'found': 0, 'new': 0, 'errors': []}

    async def save_many(run_source: str, query: str, items: list[dict]):
        new_count = 0
        for data in items:
            _, is_new = upsert_lead(db, data)
            if is_new:
                new_count += 1
        summary['found'] += len(items)
        summary['new'] += new_count
        return new_count

    for kw in keywords:
        for city in cities:

            # Free fallback: create manual Google search links (no scraping, no API key)
            if source in {'search_links'}:
                query = f'{kw.keyword} {city.name}'
                run = start_run(db, 'search_link', query)
                summary['runs'] += 1
                try:
                    items = build_search_link_leads(kw.keyword, city.name)
                    new_count = await save_many('search_link', query, items)
                    finish_run(db, run, len(items), new_count)
                except Exception as exc:
                    msg = str(exc)
                    summary['errors'].append({'source': 'search_link', 'query': query, 'error': msg})
                    finish_run(db, run, 0, 0, msg)

            # Google Maps/Places - official API
            if source in {'all', 'google_places'} and settings.google_places_api_key:
                query = f'{kw.keyword} {city.name}'
                run = start_run(db, 'google_places', query)
                summary['runs'] += 1
                try:
                    items = await google_places_text_search(settings.google_places_api_key, kw.keyword, city=city.name, limit=result_limit)
                    new_count = await save_many('google_places', query, items)
                    finish_run(db, run, len(items), new_count)
                except Exception as exc:
                    msg = str(exc)
                    summary['errors'].append({'source': 'google_places', 'query': query, 'error': msg})
                    finish_run(db, run, 0, 0, msg)

            # Neshan - official API
            if source in {'all', 'neshan'} and settings.neshan_api_key and city.lat and city.lng:
                query = f'{kw.keyword} {city.name}'
                run = start_run(db, 'neshan', query)
                summary['runs'] += 1
                try:
                    items = await neshan_search(settings.neshan_api_key, kw.keyword, city=city.name, lat=city.lat, lng=city.lng, limit=result_limit)
                    new_count = await save_many('neshan', query, items)
                    finish_run(db, run, len(items), new_count)
                except Exception as exc:
                    msg = str(exc)
                    summary['errors'].append({'source': 'neshan', 'query': query, 'error': msg})
                    finish_run(db, run, 0, 0, msg)

            # Web search via SerpAPI - Telegram/Instagram/Balad/Divar/Sheypoor/Torob + general websites
            if source in {'all', 'web', 'serpapi'} and settings.serpapi_key:
                for query in build_web_queries(kw.keyword, city.name):
                    run = start_run(db, 'serpapi', query)
                    summary['runs'] += 1
                    try:
                        items = await serpapi_search(settings.serpapi_key, query, keyword=kw.keyword, city=city.name, num=result_limit)
                        new_count = await save_many('serpapi', query, items)
                        finish_run(db, run, len(items), new_count)
                    except Exception as exc:
                        msg = str(exc)
                        summary['errors'].append({'source': 'serpapi', 'query': query, 'error': msg})
                        finish_run(db, run, 0, 0, msg)

    if source != 'search_links' and not any([settings.google_places_api_key, settings.neshan_api_key, settings.serpapi_key]):
        summary['errors'].append({
            'source': 'config',
            'query': None,
            'error': 'No collector API key configured. Set GOOGLE_PLACES_API_KEY, NESHAN_API_KEY or SERPAPI_KEY.'
        })
    return summary
