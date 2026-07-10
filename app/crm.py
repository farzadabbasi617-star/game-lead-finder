from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ActivityLog, ApiUsage, Lead, MessageTemplate, SearchQueueItem, SearchRule
from app.utils import normalize_text_key

PROFESSIONAL_STATUSES = {
    'new': 'در انتظار بررسی',
    'reviewing': 'در حال بررسی',
    'checked': 'بررسی شد',
    'messaged': 'پیام اول ارسال شد',
    'followup1': 'پیگیری اول',
    'followup2': 'پیگیری دوم',
    'interested': 'علاقه‌مند',
    'needs_call': 'نیاز به تماس',
    'replied': 'جواب داد',
    'registered': 'ثبت‌نام کرد',
    'rejected': 'رد کرد',
    'no_response': 'بدون پاسخ',
    'irrelevant': 'نامرتبط',
}

DEFAULT_TEMPLATES = [
    ('پیام کوتاه دوستانه', None, 'سلام وقتتون بخیر 🌹\nدیدم در زمینه {category} فعالیت دارید. ما یک پلتفرم تخصصی گیمینگ داریم که ثبت آگهی فروشنده‌ها فعلاً رایگانه. خوشحال می‌شیم آگهی‌هاتون رو اونجا هم ثبت کنید: YOUR_SITE_LINK'),
    ('پیام رسمی فروشگاه', 'فروشگاه گیم', 'سلام وقت بخیر. ما در حال جذب فروشگاه‌های فعال حوزه گیم و کنسول برای ثبت رایگان آگهی در پلتفرم تخصصی گیمینگ هستیم. اگر مایل باشید می‌تونید فروشگاه/محصولاتتون رو ثبت کنید: YOUR_SITE_LINK'),
    ('پیام فروش CP/UC/Gem', 'سی‌پی کالاف', 'سلام وقتتون بخیر. دیدم در زمینه فروش CP/UC/Gem فعالیت دارید. پلتفرم ما مخصوص مشتری‌های گیمینگ هست و ثبت آگهی فعلاً رایگانه. اگر دوست داشتید اینجا هم آگهی بذارید: YOUR_SITE_LINK'),
    ('پیگیری دوم', None, 'سلام مجدد 🌹 فقط خواستم پیگیری کنم اگر تمایل داشتید آگهی/فروشگاهتون رو در پلتفرم تخصصی گیمینگ ما رایگان ثبت کنید: YOUR_SITE_LINK'),
]

DEFAULT_RULES = [
    ('blacklist', 'هک'), ('blacklist', 'چیت'), ('blacklist', 'مود'), ('blacklist', 'دانلود'), ('blacklist', 'خبرگزاری'), ('blacklist', 'آپارات'),
    ('whitelist', 't.me'), ('whitelist', 'instagram.com'), ('whitelist', 'divar.ir'), ('whitelist', 'sheypoor.com'), ('whitelist', 'torob.com'), ('whitelist', 'balad.ir'),
    ('source', 'تلگرام'), ('source', 'اینستاگرام'), ('source', 'دیوار'), ('source', 'شیپور'), ('source', 'ترب'), ('source', 'بلد'), ('source', 'وب‌سایت‌ها'), ('source', 'مپ‌ها'),
]


def migrate_crm_columns(db: Session) -> None:
    # Works for PostgreSQL and SQLite with harmless best-effort ALTERs.
    columns = {
        'follow_up_at': 'TIMESTAMP',
        'preferred_contact': 'VARCHAR(80)',
        'link_status': 'VARCHAR(40)',
        'link_checked_at': 'TIMESTAMP',
    }
    dialect = db.bind.dialect.name
    for name, typ in columns.items():
        try:
            if dialect == 'postgresql':
                db.execute(text(f'ALTER TABLE leads ADD COLUMN IF NOT EXISTS {name} {typ}'))
            else:
                existing = [row[1] for row in db.execute(text('PRAGMA table_info(leads)')).fetchall()]
                if name not in existing:
                    db.execute(text(f'ALTER TABLE leads ADD COLUMN {name} {typ}'))
        except Exception:
            db.rollback()
    db.commit()


def seed_crm_data(db: Session) -> None:
    for name, category, body in DEFAULT_TEMPLATES:
        if not db.scalar(select(MessageTemplate).where(MessageTemplate.name == name)):
            db.add(MessageTemplate(name=name, category=category, body=body))
    for typ, value in DEFAULT_RULES:
        if not db.scalar(select(SearchRule).where(SearchRule.rule_type == typ, SearchRule.value == value)):
            db.add(SearchRule(rule_type=typ, value=value))
    db.commit()


def log_activity(db: Session, lead_id: int, action: str, note: str | None = None) -> None:
    db.add(ActivityLog(lead_id=lead_id, action=action, note=note))
    db.commit()


def render_template(body: str, lead: Lead) -> str:
    category = lead.category or 'محصولات و خدمات گیمینگ'
    return (body or '').replace('{title}', lead.title or '').replace('{category}', category).replace('{city}', lead.city or '')


def recommended_contact(lead: Lead) -> tuple[str, str]:
    if lead.telegram or 't.me/' in (lead.url or ''):
        return 'تلگرام', 'لینک/کانال تلگرام دارد'
    if lead.instagram or 'instagram.com' in (lead.url or ''):
        return 'اینستاگرام', 'پیج اینستاگرام دارد'
    if lead.phone:
        return 'تماس تلفنی', 'شماره عمومی دارد'
    if lead.website:
        return 'وب‌سایت', 'وب‌سایت رسمی دارد'
    return 'صفحه اصلی', 'فعلاً فقط لینک اصلی در دسترس است'


def lead_has_blacklist(db: Session, lead: Lead) -> bool:
    text_blob = normalize_text_key(' '.join([lead.title or '', lead.description or '', lead.url or '', lead.category or '']))
    rules = db.scalars(select(SearchRule).where(SearchRule.rule_type == 'blacklist', SearchRule.active == True)).all()
    return any(normalize_text_key(r.value) in text_blob for r in rules if r.value)


def source_preferences(db: Session) -> list[SearchRule]:
    return list(db.scalars(select(SearchRule).where(SearchRule.rule_type == 'source').order_by(SearchRule.id)).all())


def today_key() -> str:
    return datetime.utcnow().strftime('%Y-%m-%d')


def increment_usage(db: Session, provider: str, amount: int = 1) -> int:
    day = today_key()
    usage = db.scalar(select(ApiUsage).where(ApiUsage.provider == provider, ApiUsage.day == day))
    if not usage:
        usage = ApiUsage(provider=provider, day=day, count=0)
        db.add(usage)
    usage.count += amount
    db.commit()
    return usage.count


def get_usage(db: Session, provider: str) -> int:
    usage = db.scalar(select(ApiUsage).where(ApiUsage.provider == provider, ApiUsage.day == today_key()))
    return usage.count if usage else 0


def daily_limit(provider: str) -> int:
    settings = get_settings()
    env_name = f'{provider.upper()}_DAILY_LIMIT'
    import os
    try:
        return int(os.getenv(env_name, '30'))
    except Exception:
        return 30


def can_use_provider(db: Session, provider: str) -> tuple[bool, str]:
    used = get_usage(db, provider)
    limit = daily_limit(provider)
    if used >= limit:
        return False, f'سقف مصرف روزانه {provider} پر شده است ({used}/{limit})'
    return True, f'{used}/{limit}'


async def validate_lead_link(lead: Lead) -> tuple[str, str | None]:
    if not lead.url:
        return 'missing', 'لینک ندارد'
    try:
        headers = {'User-Agent': 'GameLeadFinder/1.0 link validation'}
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            r = await client.head(lead.url)
            if r.status_code >= 400 or r.status_code == 405:
                r = await client.get(lead.url)
            if 200 <= r.status_code < 400:
                return 'ok', f'HTTP {r.status_code}'
            return 'bad', f'HTTP {r.status_code}'
    except Exception as exc:
        return 'unknown', str(exc)[:160]


def dashboard_more(db: Session) -> dict:
    total_today = db.scalar(select(func.count(Lead.id)).where(Lead.first_seen >= datetime.utcnow() - timedelta(days=1))) or 0
    total_week = db.scalar(select(func.count(Lead.id)).where(Lead.first_seen >= datetime.utcnow() - timedelta(days=7))) or 0
    due = db.scalar(select(func.count(Lead.id)).where(Lead.follow_up_at != None, Lead.follow_up_at <= datetime.utcnow())) or 0
    by_source = db.execute(select(Lead.source, func.count(Lead.id)).group_by(Lead.source).order_by(func.count(Lead.id).desc()).limit(5)).all()
    by_category = db.execute(select(Lead.category, func.count(Lead.id)).group_by(Lead.category).order_by(func.count(Lead.id).desc()).limit(5)).all()
    return {'today': total_today, 'week': total_week, 'due': due, 'by_source': by_source, 'by_category': by_category}
