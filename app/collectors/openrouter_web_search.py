from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai import openrouter_web_search_leads
from app.repository import finish_run, start_run, upsert_lead


async def run_openrouter_web_search(
    db: Session,
    *,
    topic: str,
    city: str | None = None,
    max_results: int = 10,
    min_score: int = 60,
) -> dict:
    run = start_run(db, 'openrouter_web_ai', f'{topic} | {city or ""}')
    leads, model, error = await openrouter_web_search_leads(
        topic=topic,
        city=city,
        max_results=max_results,
        min_score=min_score,
    )
    saved = 0
    duplicates = 0
    saved_ids: list[int] = []
    if not error:
        for lead in leads:
            saved_lead, is_new = upsert_lead(db, lead)
            if is_new:
                saved += 1
                saved_ids.append(saved_lead.id)
            else:
                duplicates += 1
    finish_run(db, run, len(leads), saved, error)
    return {
        'ok': error is None,
        'topic': topic,
        'city': city,
        'found': len(leads),
        'saved': saved,
        'duplicates': duplicates,
        'saved_ids': saved_ids,
        'model': model,
        'error': error,
    }
