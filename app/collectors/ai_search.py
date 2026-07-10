from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai import generate_queries_with_ai, judge_results_with_ai
from app.collectors.web_alt import tavily_search
from app.config import get_settings
from app.repository import finish_run, start_run, upsert_lead


async def run_ai_search(
    db: Session,
    *,
    topic: str,
    city: str | None = None,
    max_queries: int = 8,
    results_per_query: int = 5,
    min_score: int = 60,
) -> dict:
    settings = get_settings()
    if not settings.tavily_api_key:
        return {'ok': False, 'error': 'TAVILY_API_KEY تنظیم نشده است.', 'queries': [], 'found': 0, 'saved': 0, 'duplicates': 0, 'saved_ids': []}

    max_queries = min(max(max_queries, 1), 20)
    results_per_query = min(max(results_per_query, 1), 20)
    min_score = min(max(min_score, 0), 100)

    run = start_run(db, 'ai_search', f'{topic} | {city or ""}')
    found = 0
    saved = 0
    duplicates = 0
    saved_ids: list[int] = []
    errors: list[str] = []

    queries, query_model, query_error = await generate_queries_with_ai(topic, city, max_queries=max_queries)
    if query_error:
        errors.append('خطای ساخت query با AI؛ fallback استفاده شد: ' + query_error[:300])

    all_results: list[dict] = []
    for query in queries:
        try:
            items = await tavily_search(settings.tavily_api_key, query, keyword=topic, city=city, num=results_per_query)
            for item in items:
                item['query'] = query
                item['keyword'] = topic
                item['city'] = city or item.get('city')
                item['source'] = f"ai_{item.get('source') or 'tavily'}"
            all_results.extend(items)
        except Exception as exc:
            errors.append(f'خطای Tavily برای {query}: {str(exc)[:250]}')

    found = len(all_results)
    verdicts, judge_model, judge_error = await judge_results_with_ai(topic, all_results, min_score=min_score)
    if judge_error:
        errors.append('خطای تحلیل نتایج با AI؛ چیزی ذخیره نشد: ' + judge_error[:300])

    verdict_by_index = {v['index']: v for v in verdicts if v.get('is_lead')}
    for idx, item in enumerate(all_results):
        verdict = verdict_by_index.get(idx)
        if not verdict:
            continue
        item['category'] = verdict.get('category') or item.get('category')
        item['score'] = verdict.get('score') or item.get('score') or 70
        reason = verdict.get('reason')
        if reason:
            item['notes'] = f'AI: {reason}'
        saved_lead, is_new = upsert_lead(db, item)
        if is_new:
            saved += 1
            saved_ids.append(saved_lead.id)
        else:
            duplicates += 1

    error_text = ' | '.join(errors) if errors else None
    finish_run(db, run, found, saved, error_text)
    return {
        'ok': True,
        'topic': topic,
        'city': city,
        'queries': queries,
        'found': found,
        'approved': len(verdict_by_index),
        'saved': saved,
        'duplicates': duplicates,
        'saved_ids': saved_ids,
        'query_model': query_model,
        'judge_model': judge_model,
        'errors': errors,
    }
