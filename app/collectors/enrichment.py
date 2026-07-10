from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import Lead
from app.utils import extract_social_links


async def fetch_public_page(url: str) -> str:
    headers = {
        'User-Agent': 'GameLeadFinder/1.0 (+public lead enrichment; no login; no captcha bypass)'
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        r = await client.get(url)
        r.raise_for_status()
        content_type = r.headers.get('content-type', '')
        if 'text/html' not in content_type and 'text/plain' not in content_type:
            return ''
        return r.text[:1_000_000]


async def enrich_lead_from_public_website(lead: Lead) -> dict[str, str | None]:
    # Only public websites. We don't log in and we don't bypass protections.
    target = lead.website
    if not target:
        return {'instagram': None, 'telegram': None}
    html = await fetch_public_page(target)
    return extract_social_links(html, target)


async def run_enrichment(db: Session, limit: int = 50) -> dict:
    leads = list(db.scalars(
        select(Lead)
        .where(Lead.website.is_not(None))
        .where((Lead.instagram.is_(None)) | (Lead.telegram.is_(None)))
        .limit(limit)
    ).all())
    checked = 0
    updated = 0
    errors: list[dict] = []
    for lead in leads:
        checked += 1
        try:
            found = await enrich_lead_from_public_website(lead)
            changed = False
            if found.get('instagram') and not lead.instagram:
                lead.instagram = found['instagram']
                changed = True
            if found.get('telegram') and not lead.telegram:
                lead.telegram = found['telegram']
                changed = True
            if changed:
                updated += 1
                db.add(lead)
                db.commit()
        except Exception as exc:
            errors.append({'lead_id': lead.id, 'url': lead.website, 'error': str(exc)[:300]})
    return {'checked': checked, 'updated': updated, 'errors': errors}
