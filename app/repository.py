from __future__ import annotations

from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db.models import City, CrawlerRun, Keyword, Lead
from app.scoring import detect_category, score_lead
from app.utils import normalize_url


def init_seed_data(db: Session) -> None:
    default_keywords = [
        'فروشگاه بازی', 'فروشگاه کنسول', 'فروشگاه پلی استیشن', 'فروشگاه ایکس باکس',
        'لوازم گیمینگ', 'گیم نت', 'گیفت کارت', 'خدمات پلی استیشن', 'استیم والت',
        'اکانت کالاف', 'سی پی کالاف', 'یوسی پابجی', 'جم فری فایر', 'فروش اکانت بازی',
    ]
    default_cities = [
        ('تهران', 35.6892, 51.3890), ('کرج', 35.8400, 50.9391), ('مشهد', 36.2605, 59.6168),
        ('اصفهان', 32.6546, 51.6680), ('شیراز', 29.5918, 52.5837), ('تبریز', 38.0962, 46.2738),
        ('اهواز', 31.3183, 48.6706), ('قم', 34.6416, 50.8746), ('رشت', 37.2808, 49.5832),
        ('کرمانشاه', 34.3142, 47.0650), ('یزد', 31.8974, 54.3569), ('ارومیه', 37.5527, 45.0761),
        ('ساری', 36.5633, 53.0601), ('بندرعباس', 27.1832, 56.2666),
    ]
    for kw in default_keywords:
        if not db.scalar(select(Keyword).where(Keyword.keyword == kw)):
            db.add(Keyword(keyword=kw))
    for name, lat, lng in default_cities:
        if not db.scalar(select(City).where(City.name == name)):
            db.add(City(name=name, lat=lat, lng=lng))
    db.commit()


def upsert_lead(db: Session, data: dict) -> tuple[Lead, bool]:
    url = normalize_url(data.get('url'))
    if not url:
        raise ValueError('Lead url is required')
    existing = db.scalar(select(Lead).where(Lead.url == url))
    now = datetime.utcnow()
    if existing:
        # Keep user's workflow/status/notes, refresh discoverable fields where empty.
        existing.last_seen = now
        for field in ['phone', 'website', 'instagram', 'telegram', 'address', 'description', 'rating', 'review_count', 'lat', 'lng']:
            val = data.get(field)
            if val and not getattr(existing, field):
                setattr(existing, field, val)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing, False

    category = data.get('category') or detect_category(data.get('title'), data.get('description'), data.get('query'), data.get('keyword'))
    score = data.get('score')
    if score is None:
        score = score_lead(
            title=data.get('title'), description=data.get('description'), url=url,
            phone=data.get('phone'), website=data.get('website'), instagram=data.get('instagram'),
            telegram=data.get('telegram'), rating=data.get('rating'), review_count=data.get('review_count')
        )
    lead = Lead(
        source=data.get('source') or 'unknown',
        entity_type=data.get('entity_type'),
        title=(data.get('title') or 'بدون عنوان')[:500],
        url=url,
        query=data.get('query'),
        keyword=data.get('keyword'),
        category=category,
        city=data.get('city'),
        description=data.get('description'),
        address=data.get('address'),
        phone=data.get('phone'),
        website=data.get('website'),
        instagram=data.get('instagram'),
        telegram=data.get('telegram'),
        rating=data.get('rating'),
        review_count=data.get('review_count'),
        lat=data.get('lat'),
        lng=data.get('lng'),
        score=score,
        status=data.get('status') or 'new',
        notes=data.get('notes'),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead, True


def start_run(db: Session, source: str, query: str | None = None) -> CrawlerRun:
    run = CrawlerRun(source=source, query=query)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def finish_run(db: Session, run: CrawlerRun, found_count: int, new_count: int, error: str | None = None) -> None:
    run.finished_at = datetime.utcnow()
    run.found_count = found_count
    run.new_count = new_count
    run.error = error
    db.add(run)
    db.commit()


def dashboard_stats(db: Session) -> dict:
    total = db.scalar(select(func.count(Lead.id))) or 0
    new = db.scalar(select(func.count(Lead.id)).where(Lead.status == 'new')) or 0
    messaged = db.scalar(select(func.count(Lead.id)).where(Lead.status == 'messaged')) or 0
    replied = db.scalar(select(func.count(Lead.id)).where(Lead.status == 'replied')) or 0
    registered = db.scalar(select(func.count(Lead.id)).where(Lead.status == 'registered')) or 0
    return {'total': total, 'new': new, 'messaged': messaged, 'replied': replied, 'registered': registered}
