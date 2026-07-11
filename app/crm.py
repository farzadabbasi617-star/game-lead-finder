from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ActivityLog, ApiUsage, AppSetting, CrawlerRun, Lead, MessageTemplate, SearchPreset, SearchQueueItem, SearchRule
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

# ---- Growth / Conversion helpers ----
from app.db.models import AppSetting, SearchPreset
import re

DEFAULT_SETTINGS = {
    'site_link': 'YOUR_SITE_LINK',
    'main_site_api_url': '',
    'main_site_api_key': '',
}

DEFAULT_PRESETS = [
    ('پکیج فروشندگان کالاف', 'فروشندگان CP/اکانت کالاف در تلگرام، اینستاگرام و وب', 'تهران', 'openrouter_web', '\n'.join([
        'فروش سی پی کالاف', 'خرید CP کالاف', 'site:t.me فروش سی پی کالاف', 'site:instagram.com فروش CP کالاف', 'کانال فروش CP کالاف', 'پیج فروش سی پی کالاف'
    ])),
    ('پکیج فروشندگان پابجی', 'فروشندگان UC و اکانت پابجی', 'تهران', 'openrouter_web', '\n'.join([
        'خرید یوسی پابجی', 'فروش UC پابجی', 'site:t.me فروش یوسی پابجی', 'site:instagram.com فروش UC پابجی', 'کانال یوسی پابجی'
    ])),
    ('پکیج فروشندگان فری فایر', 'فروشندگان جم/الماس فری فایر', 'تهران', 'openrouter_web', '\n'.join([
        'جم فری فایر', 'الماس فری فایر', 'site:t.me جم فری فایر', 'site:instagram.com الماس فری فایر', 'فروشگاه جم فری فایر'
    ])),
    ('پکیج فروشگاه‌های کنسول', 'فروشگاه‌های کنسول، پلی‌استیشن و لوازم گیمینگ', 'تهران', 'openrouter_web', '\n'.join([
        'فروشگاه کنسول تهران', 'فروشگاه پلی استیشن تهران', 'لوازم گیمینگ تهران', 'site:balad.ir فروشگاه کنسول تهران', 'site:instagram.com فروشگاه پلی استیشن تهران'
    ])),
    ('پکیج گیم‌نت‌ها', 'گیم‌نت‌ها و گیم سنترها', 'تهران', 'openrouter_web', '\n'.join([
        'گیم نت تهران', 'گیم سنتر تهران', 'site:balad.ir گیم نت تهران', 'site:instagram.com گیم نت تهران'
    ])),
    ('پکیج گیفت کارت', 'فروشندگان گیفت کارت و استیم والت', 'تهران', 'openrouter_web', '\n'.join([
        'گیفت کارت پلی استیشن', 'گیفت کارت استیم', 'استیم والت', 'site:t.me گیفت کارت پلی استیشن', 'site:instagram.com گیفت کارت استیم'
    ])),
]


def seed_growth_data(db: Session) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        if not db.scalar(select(AppSetting).where(AppSetting.key == key)):
            db.add(AppSetting(key=key, value=value))
    for name, description, city, source, queries in DEFAULT_PRESETS:
        if not db.scalar(select(SearchPreset).where(SearchPreset.name == name)):
            db.add(SearchPreset(name=name, description=description, city=city, source=source, queries=queries))
    db.commit()


def get_setting(db: Session, key: str, default: str = '') -> str:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    return (row.value if row and row.value is not None else default) or ''


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if not row:
        row = AppSetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()


def render_site_link(db: Session, text: str) -> str:
    return (text or '').replace('YOUR_SITE_LINK', get_setting(db, 'site_link', 'YOUR_SITE_LINK'))


def search_recently_run(db: Session, source: str, query: str, hours: int = 24) -> CrawlerRun | None:
    since = datetime.utcnow() - timedelta(hours=hours)
    return db.scalar(select(CrawlerRun).where(CrawlerRun.source == source, CrawlerRun.query == query, CrawlerRun.started_at >= since).order_by(CrawlerRun.started_at.desc()))


def source_quality_report(db: Session):
    rows = db.execute(select(Lead.source, func.count(Lead.id)).group_by(Lead.source).order_by(func.count(Lead.id).desc())).all()
    report = []
    for source, total in rows:
        def c(status):
            return db.scalar(select(func.count(Lead.id)).where(Lead.source == source, Lead.status == status)) or 0
        messaged = c('messaged') + c('followup1') + c('followup2')
        replied = c('replied') + c('interested') + c('needs_call')
        registered = c('registered')
        report.append({
            'source': source or 'unknown', 'total': total, 'messaged': messaged,
            'replied': replied, 'registered': registered,
            'reply_rate': round((replied / total) * 100, 1) if total else 0,
            'conversion_rate': round((registered / total) * 100, 1) if total else 0,
        })
    return report


def conversion_funnel(db: Session) -> dict:
    total = db.scalar(select(func.count(Lead.id))) or 0
    messaged = db.scalar(select(func.count(Lead.id)).where(Lead.status.in_(['messaged', 'followup1', 'followup2', 'replied', 'interested', 'needs_call', 'registered']))) or 0
    replied = db.scalar(select(func.count(Lead.id)).where(Lead.status.in_(['replied', 'interested', 'needs_call', 'registered']))) or 0
    registered = db.scalar(select(func.count(Lead.id)).where(Lead.status == 'registered')) or 0
    return {'total': total, 'messaged': messaged, 'replied': replied, 'registered': registered}


def validity_label(db: Session, lead: Lead) -> tuple[str, str]:
    if lead.status == 'irrelevant' or lead_has_blacklist(db, lead):
        return 'مشکوک/نامرتبط', 'کلمات blacklist یا وضعیت نامرتبط دارد'
    contact_count = sum(bool(x) for x in [lead.phone, lead.website, lead.instagram, lead.telegram])
    if lead.link_status == 'bad':
        return 'نیاز به بررسی', 'لینک اصلی مشکل دارد'
    if contact_count >= 2 and (lead.link_status in {None, 'ok', 'unknown'}):
        return 'معتبر', 'چند راه ارتباط عمومی دارد'
    if contact_count >= 1:
        return 'قابل بررسی', 'حداقل یک راه ارتباط دارد'
    return 'ضعیف', 'راه ارتباط مستقیم ندارد'


def extract_contacts_from_text(text: str) -> dict:
    text = text or ''
    phones = re.findall(r'(?:\+98|0098|98|0)?9\d{9}|0\d{2,3}[-\s]?\d{6,8}', text)
    emails = re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text)
    instas = re.findall(r'https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+/?', text, re.I)
    tgs = re.findall(r'https?://(?:t\.me|telegram\.me)/[A-Za-z0-9_]+/?', text, re.I)
    contact_pages = re.findall(r'https?://[^\s"\']*(?:contact|contacts|تماس)[^\s"\']*', text, re.I)
    return {
        'phone': phones[0] if phones else None,
        'email': emails[0] if emails else None,
        'instagram': instas[0] if instas else None,
        'telegram': tgs[0] if tgs else None,
        'contact_page': contact_pages[0] if contact_pages else None,
    }


async def extract_contacts_from_url(url: str) -> dict:
    if not url:
        return {}
    headers = {'User-Agent': 'GameLeadFinder/1.0 contact extraction'}
    async with httpx.AsyncClient(timeout=18, follow_redirects=True, headers=headers) as client:
        r = await client.get(url)
        r.raise_for_status()
        return extract_contacts_from_text(r.text[:1_500_000])
