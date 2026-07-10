from __future__ import annotations

from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db.models import City, CrawlerRun, Keyword, Lead
from app.scoring import detect_category, score_lead
from app.utils import normalize_domain, normalize_instagram, normalize_phone, normalize_telegram, normalize_text_key, normalize_url


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



def lead_identity_keys_from_values(
    *,
    url: str | None = None,
    phone: str | None = None,
    website: str | None = None,
    instagram: str | None = None,
    telegram: str | None = None,
    title: str | None = None,
    city: str | None = None,
    address: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> set[str]:
    keys: set[str] = set()
    n_url = normalize_url(url)
    if n_url:
        keys.add(f'url:{n_url}')
        ig_from_url = normalize_instagram(n_url)
        tg_from_url = normalize_telegram(n_url)
        if ig_from_url:
            keys.add(f'ig:{ig_from_url}')
        if tg_from_url:
            keys.add(f'tg:{tg_from_url}')

    n_phone = normalize_phone(phone)
    if n_phone and len(n_phone) >= 8:
        keys.add(f'phone:{n_phone}')

    n_ig = normalize_instagram(instagram)
    if n_ig:
        keys.add(f'ig:{n_ig}')

    n_tg = normalize_telegram(telegram)
    if n_tg:
        keys.add(f'tg:{n_tg}')

    domain = normalize_domain(website or url)
    if domain:
        keys.add(f'domain:{domain}')

    if lat is not None and lng is not None:
        try:
            keys.add(f'geo:{round(float(lat), 5)}:{round(float(lng), 5)}')
        except Exception:
            pass

    name_key = normalize_text_key(title)
    city_key = normalize_text_key(city)
    address_key = normalize_text_key(address)
    if name_key and city_key and len(name_key) >= 4:
        keys.add(f'name_city:{name_key}:{city_key}')
    if name_key and address_key and len(address_key) >= 8:
        keys.add(f'name_addr:{name_key}:{address_key[:80]}')

    return keys


def lead_identity_keys(lead: Lead) -> set[str]:
    return lead_identity_keys_from_values(
        url=lead.url, phone=lead.phone, website=lead.website, instagram=lead.instagram,
        telegram=lead.telegram, title=lead.title, city=lead.city, address=lead.address,
        lat=lead.lat, lng=lead.lng,
    )


def status_priority(status: str | None) -> int:
    priorities = {'irrelevant': 0, 'new': 1, 'checked': 2, 'messaged': 3, 'replied': 4, 'registered': 5}
    return priorities.get(status or 'new', 1)


def merge_lead_data(target: Lead, data: dict) -> None:
    now = datetime.utcnow()
    target.last_seen = now

    # If the old record is just a manual Google search task and the new one is a real target, keep the real URL.
    new_url = normalize_url(data.get('url'))
    if target.source == 'search_link' and new_url and 'google.com/search' not in new_url:
        target.url = new_url

    for field in ['phone', 'website', 'instagram', 'telegram', 'address', 'description', 'rating', 'review_count', 'lat', 'lng', 'city', 'category']:
        val = data.get(field)
        if val and not getattr(target, field):
            setattr(target, field, val)

    if data.get('score') and data['score'] > (target.score or 0):
        target.score = data['score']

    if data.get('status') and status_priority(data.get('status')) > status_priority(target.status):
        target.status = data['status']


def find_duplicate_lead(db: Session, data: dict, normalized_url: str) -> Lead | None:
    # Fast exact URL check first.
    existing = db.scalar(select(Lead).where(Lead.url == normalized_url))
    if existing:
        return existing

    incoming_keys = lead_identity_keys_from_values(
        url=normalized_url, phone=data.get('phone'), website=data.get('website'),
        instagram=data.get('instagram'), telegram=data.get('telegram'), title=data.get('title'),
        city=data.get('city'), address=data.get('address'), lat=data.get('lat'), lng=data.get('lng'),
    )
    if not incoming_keys:
        return None

    # Strict dedupe: compare normalized identity keys against all existing leads.
    for lead in db.scalars(select(Lead)).all():
        if incoming_keys & lead_identity_keys(lead):
            return lead
    return None


def upsert_lead(db: Session, data: dict) -> tuple[Lead, bool]:
    url = normalize_url(data.get('url'))
    if not url:
        raise ValueError('Lead url is required')

    category = data.get('category') or detect_category(data.get('title'), data.get('description'), data.get('query'), data.get('keyword'))
    score = data.get('score')
    if score is None:
        score = score_lead(
            title=data.get('title'), description=data.get('description'), url=url,
            phone=data.get('phone'), website=data.get('website'), instagram=data.get('instagram'),
            telegram=data.get('telegram'), rating=data.get('rating'), review_count=data.get('review_count')
        )
    data = {**data, 'url': url, 'category': category, 'score': score}

    existing = find_duplicate_lead(db, data, url)
    if existing:
        merge_lead_data(existing, data)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing, False

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


def merge_existing_duplicates(db: Session) -> int:
    leads = list(db.scalars(select(Lead).order_by(Lead.id)).all())
    key_owner: dict[str, Lead] = {}
    deleted = 0
    for lead in leads:
        keys = lead_identity_keys(lead)
        duplicate_of = None
        for key in keys:
            if key in key_owner:
                duplicate_of = key_owner[key]
                break
        if duplicate_of and duplicate_of.id != lead.id:
            merge_lead_data(duplicate_of, {
                'url': lead.url, 'phone': lead.phone, 'website': lead.website, 'instagram': lead.instagram,
                'telegram': lead.telegram, 'address': lead.address, 'description': lead.description,
                'rating': lead.rating, 'review_count': lead.review_count, 'lat': lead.lat, 'lng': lead.lng,
                'city': lead.city, 'category': lead.category, 'score': lead.score, 'status': lead.status,
            })
            if lead.notes and not duplicate_of.notes:
                duplicate_of.notes = lead.notes
            db.delete(lead)
            deleted += 1
            for key in keys:
                key_owner[key] = duplicate_of
        else:
            for key in keys:
                key_owner[key] = lead
    if deleted:
        db.commit()
    return deleted

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
