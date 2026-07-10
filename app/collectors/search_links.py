from __future__ import annotations

from urllib.parse import quote_plus
from app.collectors.serpapi import build_web_queries


def build_search_link_leads(keyword: str, city: str | None = None) -> list[dict]:
    """Free fallback: creates manual Google search task links, not scraped results."""
    leads = []
    for query in build_web_queries(keyword, city):
        url = f'https://www.google.com/search?q={quote_plus(query)}'
        leads.append({
            'source': 'search_link',
            'entity_type': 'manual_search',
            'title': f'جستجوی دستی: {query}',
            'url': url,
            'description': 'لینک جستجوی دستی رایگان؛ خودتان نتایج عمومی را بازبینی کنید.',
            'query': query,
            'keyword': keyword,
            'city': city,
            'score': 25,
            'category': 'جستجوی دستی',
        })
    return leads
