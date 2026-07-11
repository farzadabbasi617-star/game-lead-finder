from __future__ import annotations

import csv
import html
import io
from datetime import timezone
from typing import Annotated
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus, urlparse

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from openpyxl import Workbook
from sqlalchemy import asc, desc, or_, select
from sqlalchemy.orm import Session

from app.collectors.ai_search import run_ai_search
from app.collectors.enrichment import run_enrichment
from app.collectors.openrouter_web_search import run_openrouter_web_search
from app.collectors.orchestrator import run_collector
from app.config import get_settings
from app.db.models import City, CrawlerRun, Keyword, Lead
from app.db.session import Base, engine, get_db
from app.repository import dashboard_stats, init_seed_data, upsert_lead
from app.scoring import detect_category, score_lead
from app.utils import public_invite_message
from app.crm import migrate_crm_columns, seed_crm_data, seed_growth_data, can_use_provider, increment_usage, search_recently_run
from app.crm_routes import router as crm_router
from app.campaigns import router as campaigns_router
from app.people import router as people_router
from app.analytics import router as analytics_router
from app.sponsor.routes import router as sponsor_router
from app.influencer.routes import router as influencer_router

app = FastAPI(title='بانک اطلاعاتی لیدهای گیمینگ')
app.include_router(campaigns_router)
app.include_router(crm_router)
app.include_router(people_router)
app.include_router(analytics_router)
app.include_router(sponsor_router)
app.include_router(influencer_router)

STATUS_LABELS = {
    'new': 'در انتظار بررسی',
    'checked': 'بررسی شد',
    'messaged': 'پیام داده شد',
    'replied': 'جواب داد',
    'registered': 'ثبت‌نام کرد',
    'irrelevant': 'نامرتبط',
}

SOURCE_LABELS = {
    'manual': 'دستی',
    'csv': 'فایل CSV',
    'search_link': 'لینک جستجوی دستی',
    'tavily': 'جستجوی وب Tavily',
    'web': 'وب‌سایت',
    'telegram_web': 'تلگرام عمومی',
    'instagram_web': 'اینستاگرام عمومی',
    'balad_web': 'بلد',
    'divar_web': 'دیوار',
    'sheypoor_web': 'شیپور',
    'torob_web': 'ترب',
    'neshan': 'نشان',
    'google_places': 'گوگل‌مپ',
    'google_cse': 'Google CSE',
    'brave': 'Brave',
    'serper': 'Serper',
    'searchapi': 'SearchAPI',
    'serpapi': 'SerpAPI',
}

FOLDER_META = {
    'instagram': ('📸', 'اینستاگرام', 'پیج‌ها و لینک‌های Instagram'),
    'telegram': ('✈️', 'تلگرام', 'کانال‌ها و لینک‌های Telegram'),
    'divar': ('🏷️', 'دیوار', 'آگهی‌های پیدا شده از Divar'),
    'sheypoor': ('📌', 'شیپور', 'آگهی‌های پیدا شده از Sheypoor'),
    'torob': ('🛒', 'ترب', 'محصولات و فروشگاه‌های Torob'),
    'balad': ('🗺️', 'بلد', 'کسب‌وکارهای Balad'),
    'maps': ('📍', 'مپ‌ها', 'Google Maps / Neshan و منابع مکانی'),
    'website': ('🌐', 'وب‌سایت‌ها', 'فروشگاه‌ها و سایت‌های مستقل'),
    'ai': ('🤖', 'هوش مصنوعی', 'نتایج سرچ مستقیم AI و تحلیل AI'),
    'tavily': ('🔎', 'Tavily', 'نتایج جستجوی Tavily'),
    'manual': ('✍️', 'دستی/Import', 'مخاطبین دستی، CSV و لینک‌های جستجوی دستی'),
    'other': ('📁', 'سایر منابع', 'مواردی که منبع مشخص‌تری ندارند'),
}

FOLDER_ORDER = ['instagram', 'telegram', 'divar', 'sheypoor', 'torob', 'balad', 'maps', 'website', 'ai', 'tavily', 'manual', 'other']


COLLECTOR_LABELS = {
    'all': 'همه منابع فعال',
    'web': 'جستجوی وب؛ همه APIهای فعال',
    'tavily': 'Tavily؛ جستجوی وب',
    'google_cse': 'Google CSE',
    'brave': 'Brave Search',
    'serper': 'Serper',
    'searchapi': 'SearchAPI',
    'serpapi': 'SerpAPI',
    'google_places': 'گوگل‌مپ / Google Places',
    'neshan': 'نشان',
    'search_links': 'رایگان؛ ساخت لینک جستجوی دستی',
}


SORT_LABELS = {
    'newest': 'جدیدترین اضافه‌شده',
    'oldest': 'قدیمی‌ترین اضافه‌شده',
    'score_desc': 'بالاترین امتیاز',
    'score_asc': 'پایین‌ترین امتیاز',
    'updated_desc': 'آخرین بروزرسانی',
    'status': 'وضعیت پیگیری',
    'source': 'منبع',
    'city': 'شهر',
    'title': 'نام/عنوان',
}

SEARCH_SUGGESTIONS = [
    'خرید اکانت کلش رویال', 'فروش اکانت کلش', 'فروش سی پی کالاف', 'خرید CP کالاف',
    'خرید یوسی پابجی', 'فروش UC پابجی', 'جم فری فایر', 'الماس فری فایر',
    'گیفت کارت پلی استیشن', 'گیفت کارت استیم', 'اکانت استیم', 'اکانت ولورانت',
    'فروشگاه کنسول تهران', 'فروشگاه پلی استیشن تهران', 'گیم نت تهران', 'لوازم گیمینگ تهران',
    'site:t.me فروش سی پی کالاف', 'site:instagram.com خرید اکانت کلش رویال',
]

TEHRAN_TZ = ZoneInfo('Asia/Tehran')


def format_dt(value) -> str:
    if not value:
        return '-'
    try:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(TEHRAN_TZ)
        return value.strftime('%Y/%m/%d - %H:%M')
    except Exception:
        return str(value)


def sort_options(selected: str | None) -> str:
    selected = selected or 'newest'
    return ''.join(
        f'<option value="{h(code)}" {"selected" if selected == code else ""}>{h(label)}</option>'
        for code, label in SORT_LABELS.items()
    )


def apply_sort(stmt, sort: str | None):
    sort = sort or 'newest'
    if sort == 'oldest':
        return stmt.order_by(asc(Lead.first_seen))
    if sort == 'score_desc':
        return stmt.order_by(desc(Lead.score), desc(Lead.first_seen))
    if sort == 'score_asc':
        return stmt.order_by(asc(Lead.score), desc(Lead.first_seen))
    if sort == 'updated_desc':
        return stmt.order_by(desc(Lead.last_seen), desc(Lead.first_seen))
    if sort == 'status':
        return stmt.order_by(asc(Lead.status), desc(Lead.first_seen))
    if sort == 'source':
        return stmt.order_by(asc(Lead.source), desc(Lead.first_seen))
    if sort == 'city':
        return stmt.order_by(asc(Lead.city), desc(Lead.first_seen))
    if sort == 'title':
        return stmt.order_by(asc(Lead.title), desc(Lead.first_seen))
    return stmt.order_by(desc(Lead.first_seen), desc(Lead.score))


def suggestion_buttons() -> str:
    return '<div class="suggestions">' + ''.join(
        f'<button type="button" class="suggestion" data-topic="{h(item)}">{h(item)}</button>'
        for item in SEARCH_SUGGESTIONS
    ) + '</div>'


def parse_id_list(value: str | None) -> set[int]:
    ids: set[int] = set()
    for part in (value or '').split(','):
        try:
            if part.strip():
                ids.add(int(part.strip()))
        except Exception:
            pass
    return ids


@app.on_event('startup')
def startup():
    from app.sponsor.models import SponsorChannel  # ensure table created
    from app.influencer.models import Influencer, InfluencerTag  # ensure table created
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        migrate_crm_columns(db)
        Base.metadata.create_all(bind=engine)
        init_seed_data(db)
        seed_crm_data(db)
        seed_growth_data(db)
    finally:
        db.close()


def check_token(token: str | None = None, x_admin_token: str | None = Header(default=None)):
    pass


def h(value) -> str:
    return html.escape('' if value is None else str(value), quote=True)


def source_label(source: str | None) -> str:
    if not source:
        return '-'
    for key, label in SOURCE_LABELS.items():
        if source == key or source.startswith(key + '_') or source.endswith('_' + key):
            return label
    return source


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or 'new', status or 'جدید')


def status_options(selected: str | None) -> str:
    return ''.join(
        f'<option value="{h(code)}" {"selected" if selected == code else ""}>{h(label)}</option>'
        for code, label in STATUS_LABELS.items()
    )


def collector_options(selected: str = 'tavily') -> str:
    return ''.join(
        f'<option value="{h(code)}" {"selected" if selected == code else ""}>{h(label)}</option>'
        for code, label in COLLECTOR_LABELS.items()
    )


def extract_social_from_url(url: str | None) -> dict[str, str | None]:
    if not url:
        return {'instagram': None, 'telegram': None}
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.strip('/').split('/') if p]
    if 'instagram.com' in host and parts and parts[0] not in {'p', 'reel', 'explore', 'accounts'}:
        return {'instagram': f'https://instagram.com/{parts[0]}', 'telegram': None}
    if host in {'t.me', 'telegram.me'} and parts:
        return {'instagram': None, 'telegram': f'https://t.me/{parts[0]}'}
    return {'instagram': None, 'telegram': None}


def instagram_dm_url(instagram_url: str | None) -> str | None:
    if not instagram_url:
        return None
    parsed = urlparse(instagram_url)
    parts = [p for p in parsed.path.strip('/').split('/') if p]
    if not parts:
        return instagram_url
    return f'https://ig.me/m/{parts[0]}'


def contact_buttons(lead: Lead) -> str:
    social = extract_social_from_url(lead.url)
    instagram = lead.instagram or social.get('instagram')
    telegram = lead.telegram or social.get('telegram')
    buttons: list[str] = []

    if lead.url:
        buttons.append(f'<a class="action primary" target="_blank" href="{h(lead.url)}">باز کردن صفحه اصلی</a>')
    if lead.website:
        buttons.append(f'<a class="action" target="_blank" href="{h(lead.website)}">وب‌سایت</a>')
    if lead.phone:
        buttons.append(f'<a class="action" href="tel:{h(lead.phone)}">تماس تلفنی</a>')
    if instagram:
        buttons.append(f'<a class="action insta" target="_blank" href="{h(instagram)}">پیج اینستاگرام</a>')
        dm = instagram_dm_url(instagram)
        if dm:
            buttons.append(f'<a class="action insta" target="_blank" href="{h(dm)}">دایرکت اینستاگرام</a>')
    if telegram:
        buttons.append(f'<a class="action tg" target="_blank" href="{h(telegram)}">تلگرام</a>')
    if lead.lat and lead.lng:
        maps_url = f'https://www.google.com/maps/search/?api=1&query={lead.lat},{lead.lng}'
        buttons.append(f'<a class="action" target="_blank" href="{h(maps_url)}">نمایش روی نقشه</a>')
    elif lead.address:
        maps_url = f'https://www.google.com/maps/search/{quote_plus(lead.address)}'
        buttons.append(f'<a class="action" target="_blank" href="{h(maps_url)}">جستجوی آدرس روی نقشه</a>')

    invite_msg = h(public_invite_message(lead.title, lead.category))
    buttons.append(f'<button type="button" class="action copy-msg" data-message="{invite_msg}">کپی متن دعوت</button>')
    return '<div class="actions">' + ''.join(buttons) + '</div>'


def folder_key(lead: Lead) -> str:
    src = (lead.source or '').lower()
    blob = ' '.join([src, lead.url or '', lead.website or '', lead.instagram or '', lead.telegram or '']).lower()
    if 'instagram' in blob:
        return 'instagram'
    if 'telegram' in blob or 't.me/' in blob or 'telegram.me' in blob:
        return 'telegram'
    if 'divar' in blob:
        return 'divar'
    if 'sheypoor' in blob:
        return 'sheypoor'
    if 'torob' in blob:
        return 'torob'
    if 'balad' in blob:
        return 'balad'
    if 'neshan' in blob or 'google_places' in blob or 'google.com/maps' in blob or 'maps.google' in blob:
        return 'maps'
    if 'openrouter' in blob or src.startswith('ai_') or src == 'ai_search':
        return 'ai'
    if 'tavily' in blob:
        return 'tavily'
    if src in {'manual', 'csv', 'search_link'} or 'دستی' in src:
        return 'manual'
    if lead.website or (lead.url and not any(x in blob for x in ['instagram', 't.me/', 'divar', 'sheypoor', 'torob', 'balad'])):
        return 'website'
    return 'other'


def render_lead_row(lead: Lead, token: str, fresh_ids: set[int]) -> str:
    is_fresh = lead.id in fresh_ids
    fresh_badge = '<span class="fresh-badge">تازه اضافه شد</span><br>' if is_fresh else ''
    row_class = 'fresh-row' if is_fresh else ''
    return f'''
        <tr class="{row_class}">
          <td>
            {fresh_badge}<span class="badge">{h(source_label(lead.source))}</span><br>
            <span class="badge status">{h(status_label(lead.status))}</span><br>
            <span class="small muted">شناسه: {lead.id}</span><br>
            <div class="timebox small">افزوده شد: {h(format_dt(lead.first_seen))}<br>بروزرسانی: {h(format_dt(lead.last_seen))}</div>
          </td>
          <td>
            <div class="lead-title">{h(lead.title)}</div>
            <div class="muted">{h((lead.description or '')[:180])}</div>
            <div class="small muted">دسته: {h(lead.category or '-')} | شهر: {h(lead.city or '-')} | امتیاز: <span class="score">{lead.score}</span></div>
          </td>
          <td>{contact_buttons(lead)}<div style="margin-top:6px"><a class="action" href="/leads/{lead.id}">جزئیات و تاریخچه</a></div></td>
          <td class="editable">
            <form class="inline" method="post" action="/leads/{lead.id}/update">
              <select name="status">{status_options(lead.status)}</select>
              <input name="phone" placeholder="تلفن" value="{h(lead.phone)}">
              <input class="wide" name="website" placeholder="وب‌سایت" value="{h(lead.website)}">
              <input class="wide" name="instagram" placeholder="لینک اینستاگرام" value="{h(lead.instagram)}">
              <input class="wide" name="telegram" placeholder="لینک تلگرام" value="{h(lead.telegram)}">
              <input class="wide" name="notes" placeholder="یادداشت پیگیری" value="{h(lead.notes)}">
              <button class="btn2">ذخیره در بانک</button>
            </form>
          </td>
          <td>
            <a class="url" target="_blank" href="{h(lead.url)}">{h(lead.url)}</a><br>
            <span class="muted">{h(lead.address or '')}</span>
          </td>
        </tr>'''


def folder_source_filter(key: str) -> str:
    return {
        'instagram': 'instagram', 'telegram': 'telegram', 'divar': 'divar', 'sheypoor': 'sheypoor',
        'torob': 'torob', 'balad': 'balad', 'maps': 'neshan', 'ai': 'openrouter', 'tavily': 'tavily',
        'manual': 'manual', 'website': 'web', 'other': ''
    }.get(key, '')


def css() -> str:
    return '''
    <style>
      :root{--bg:#f6f8fc;--surface:#ffffff;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb;--primary2:#1d4ed8;--soft:#eef4ff;--success:#12b76a;--warning:#f79009;--danger:#f04438;--shadow:0 16px 40px rgba(16,24,40,.08);--radius:18px}
      *{box-sizing:border-box}html{scroll-behavior:smooth}body{font-family:Tahoma,Arial,sans-serif;background:radial-gradient(circle at top right,rgba(37,99,235,.10),transparent 34%),linear-gradient(180deg,#f8fbff 0%,var(--bg) 45%,#f3f6fb 100%);margin:0;color:var(--text);direction:rtl;font-size:14px}a{color:var(--primary);text-decoration:none}a:hover{color:var(--primary2)}
      .wrap{max-width:1540px;margin:auto;padding:24px}.top{position:relative;display:flex;gap:18px;align-items:stretch;justify-content:space-between;flex-wrap:wrap;background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 52%,#2563eb 100%);border-radius:28px;padding:22px;color:white;box-shadow:var(--shadow);overflow:hidden}.top:before{content:"";position:absolute;inset:-80px auto auto -80px;width:260px;height:260px;border-radius:50%;background:rgba(255,255,255,.12)}.top:after{content:"";position:absolute;right:35%;bottom:-95px;width:220px;height:220px;border-radius:50%;background:rgba(255,255,255,.08)}
      .brand{position:relative;z-index:1;min-width:300px;flex:1}.brand h1{margin:0;font-size:28px;letter-spacing:-.5px;color:white}.brand .muted{color:#dbeafe;max-width:760px}.top>div:last-child{position:relative;z-index:1;display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap}.nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}.nav a,.pill{display:inline-flex;align-items:center;gap:6px;padding:9px 13px;border-radius:999px;background:rgba(255,255,255,.14);color:white;border:1px solid rgba(255,255,255,.20);font-size:13px;backdrop-filter:blur(10px)}.nav .active,.nav a:hover{background:white;color:#1d4ed8;border-color:white}.pill{background:var(--soft);color:#1d4ed8;border-color:#dbeafe}
      .muted{color:var(--muted);font-size:13px;line-height:1.9}.small{font-size:12px}.ltr{direction:ltr;text-align:left}.card{background:rgba(255,255,255,.9);border:1px solid rgba(228,231,236,.95);border-radius:var(--radius);padding:18px;margin:16px 0;box-shadow:0 8px 26px rgba(16,24,40,.045);backdrop-filter:blur(14px)}.card h3{margin:0 0 14px;font-size:18px}.section-title{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px}.section-title h3{margin:0}
      .stats{display:grid;grid-template-columns:repeat(5,minmax(135px,1fr));gap:14px;margin-top:18px}.stat{position:relative;background:linear-gradient(180deg,#fff,#f8fbff);border:1px solid #e0e7ff;border-radius:20px;padding:17px;box-shadow:0 10px 24px rgba(37,99,235,.07);overflow:hidden}.stat:after{content:"";position:absolute;left:-28px;top:-28px;width:72px;height:72px;border-radius:50%;background:#dbeafe}.stat b{display:block;font-size:28px;margin-top:7px;color:#1e40af}.stat:nth-child(2) b{color:#027a48}.stat:nth-child(3) b{color:#b54708}.stat:nth-child(4) b{color:#7a2e0e}.stat:nth-child(5) b{color:#5925dc}
      .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.mini-card{background:linear-gradient(180deg,#fff,#f8fafc);border:1px solid var(--line);border-radius:16px;padding:15px;box-shadow:0 8px 22px rgba(16,24,40,.04)}
      form.inline{display:flex;gap:9px;flex-wrap:wrap;align-items:center}input,select,button,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px 12px;background:white;outline:none;transition:.16s ease}input:focus,select:focus,textarea:focus{border-color:#93c5fd;box-shadow:0 0 0 4px rgba(37,99,235,.12)}textarea{min-height:48px}button,.btn2,.action{cursor:pointer;background:var(--primary);color:white;border:0;border-radius:12px;padding:9px 12px;font-weight:600}.btn2{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe}.btn3{background:#f8fafc;color:#344054;border:1px solid #d0d5dd}.danger{background:#fff1f2;color:#be123c;border:1px solid #fecdd3}
      table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}th,td{padding:13px 12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top;font-size:13px}th{background:linear-gradient(180deg,#f8fafc,#eef4ff);color:#344054;position:sticky;top:0;z-index:1;font-weight:700}tr:hover{background:#f9fbff}tr:last-child td{border-bottom:0}
      .badge{display:inline-flex;align-items:center;padding:5px 9px;border-radius:999px;background:#eef2ff;color:#2546a6;font-size:12px;margin:2px;font-weight:600}.status{background:#ecfdf3;color:#027a48}.score{font-weight:bold;color:#137333}.url{max-width:270px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;direction:ltr;color:#475467}.lead-title{font-weight:800;color:#101828;font-size:14px;margin-bottom:4px}
      .actions{display:flex;gap:7px;flex-wrap:wrap;min-width:230px}.action{display:inline-flex;align-items:center;justify-content:center;background:#f2f4f7;color:#344054;border:1px solid #d0d5dd;font-size:12px;line-height:1.2}.action.primary{background:var(--primary);color:white;border-color:var(--primary)}.action.insta{background:#fff0f6;color:#c11574;border-color:#ffcce1}.action.tg{background:#eef8ff;color:#026aa2;border-color:#b9e6fe}button.action{cursor:pointer}
      .editable input{width:132px}.editable .wide{width:210px}.fresh-row{background:#f0fdf4!important}.fresh-badge{display:inline-block;background:var(--success);color:white;border-radius:999px;padding:5px 9px;font-size:12px;margin:2px;font-weight:700}.timebox{line-height:1.9;color:#475467}.suggestions{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.suggestion{background:#fff;color:#344054;border:1px dashed #98a2b3;border-radius:999px;padding:8px 11px;font-size:12px}.suggestion:hover{background:#eff6ff;border-color:#60a5fa;color:#1d4ed8}.hint{background:#fffbeb;border:1px solid #fde68a;color:#7a4b00;border-radius:14px;padding:12px;margin-top:12px}.log-item{border-right:4px solid var(--primary);padding:10px;margin:9px 0;background:#f8fafc;border-radius:12px}.folder-grid{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:10px;margin:12px 0}.folder-card{display:block;background:linear-gradient(180deg,#fff,#f8fbff);border:1px solid #dbeafe;border-radius:16px;padding:12px;color:#101828;box-shadow:0 8px 20px rgba(37,99,235,.06)}.folder-card b{display:block;font-size:18px;margin-top:6px}.folder-card span{font-size:12px;color:#667085}.folder-section{border:1px solid var(--line);border-radius:18px;margin:14px 0;background:white;overflow:hidden}.folder-section summary{cursor:pointer;list-style:none;padding:16px 18px;background:linear-gradient(180deg,#f8fafc,#eef4ff);display:flex;align-items:center;justify-content:space-between;gap:10px}.folder-section summary::-webkit-details-marker{display:none}.folder-title{font-weight:800;font-size:16px}.folder-desc{color:#667085;font-size:12px}.folder-count{background:#2563eb;color:#fff;border-radius:999px;padding:5px 10px;font-size:12px;font-weight:700}.folder-table{padding:0 0 8px}.empty-folder{padding:18px;color:#667085}
      @media(max-width:900px){.folder-grid{grid-template-columns:repeat(2,1fr)}.wrap{padding:12px}.top{border-radius:20px;padding:18px}.brand h1{font-size:22px}.stats,.grid2,.grid3{grid-template-columns:1fr}.card{padding:14px;border-radius:16px}table{display:block;overflow-x:auto;direction:rtl}.top{display:block}.editable input{width:150px}.nav a{font-size:12px}.stat b{font-size:24px}}
    </style>
    '''


def page(title: str, body: str) -> HTMLResponse:
    script = '''
    <script>
      document.addEventListener('click', async function(e){
        if(e.target.classList.contains('copy-msg')){
          const t=e.target.dataset.message||'';
          try{await navigator.clipboard.writeText(t); e.target.textContent='کپی شد ✅';}
          catch(_){const a=document.createElement('textarea'); a.value=t; document.body.appendChild(a); a.select(); document.execCommand('copy'); a.remove(); e.target.textContent='کپی شد ✅';}
          setTimeout(()=>e.target.textContent='کپی متن دعوت',1600);
        }
        if(e.target.classList.contains('suggestion')){
          const topic=e.target.dataset.topic||'';
          const card=e.target.closest('.card') || document;
          const inp=card.querySelector('input[name="topic"]') || document.querySelector('input[name="topic"]');
          if(inp){ inp.value=topic; inp.focus(); }
        }
      });
    </script>
    '''
    return HTMLResponse(f'<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title>{css()}</head><body><div class="wrap">{body}</div>{script}</body></html>')


def build_query(db: Session, status: str, source: str, category: str, q: str):
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source.ilike(f'%{source}%'))
    if category:
        stmt = stmt.where(Lead.category.ilike(f'%{category}%'))
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(Lead.title.ilike(like), Lead.description.ilike(like), Lead.url.ilike(like), Lead.city.ilike(like), Lead.phone.ilike(like), Lead.website.ilike(like), Lead.instagram.ilike(like), Lead.telegram.ilike(like)))
    return stmt


@app.get('/', response_class=HTMLResponse)
def index(
    db: Session = Depends(get_db),
    status: str = Query(''),
    source: str = Query(''),
    category: str = Query(''),
    q: str = Query(''),
    limit: int = Query(100, ge=1, le=500),
    sort: str = Query('newest'),
    token: str = Query('', include_in_schema=False),
    ai_msg: str = Query(''),
    new_ids: str = Query(''),
):
    stats = dashboard_stats(db)
    stmt = build_query(db, status, source, category, q)
    fresh_ids = parse_id_list(new_ids)
    leads = list(db.scalars(apply_sort(stmt, sort).limit(limit)).all())
    keywords = list(db.scalars(select(Keyword).order_by(Keyword.id)).all())
    cities = list(db.scalars(select(City).order_by(City.id)).all())
    runs = list(db.scalars(select(CrawlerRun).order_by(desc(CrawlerRun.started_at)).limit(8)).all())

    grouped: dict[str, list[Lead]] = {key: [] for key in FOLDER_ORDER}
    for lead in leads:
        grouped.setdefault(folder_key(lead), []).append(lead)

    folder_cards = ''.join(
        f'<a class="folder-card" href="#folder-{key}"><span>{h(FOLDER_META[key][0])} {h(FOLDER_META[key][1])}</span><b>{len(grouped.get(key, []))}</b><span>{h(FOLDER_META[key][2])}</span></a>'
        for key in FOLDER_ORDER if grouped.get(key)
    ) or '<div class="muted">هنوز نتیجه‌ای برای پوشه‌بندی وجود ندارد.</div>'

    grouped_sections = ''
    first_open = True
    for key in FOLDER_ORDER:
        items = grouped.get(key, [])
        if not items:
            continue
        icon, label, folder_desc = FOLDER_META.get(key, FOLDER_META['other'])
        rows = ''.join(render_lead_row(lead, token, fresh_ids) for lead in items)
        open_attr = ' open' if first_open or any(lead.id in fresh_ids for lead in items) else ''
        first_open = False
        filter_value = folder_source_filter(key)
        grouped_sections += f'''
        <details id="folder-{h(key)}" class="folder-section"{open_attr}>
          <summary>
            <div><div class="folder-title">{h(icon)} پوشه {h(label)}</div><div class="folder-desc">{h(folder_desc)}</div></div>
            <div><span class="folder-count">{len(items)} مخاطب</span> <a class="btn2" href="/?source={h(filter_value)}&sort={h(sort)}#folder-{h(key)}">فیلتر فقط این پوشه</a></div>
          </summary>
          <div class="folder-table">
            <table>
              <thead><tr><th>منبع/وضعیت/زمان</th><th>مشخصات مخاطب</th><th>راه‌های ارتباط</th><th>ویرایش و پیگیری</th><th>لینک/آدرس</th></tr></thead>
              <tbody>{rows}</tbody>
            </table>
          </div>
        </details>'''


    keyword_list = '، '.join([kw.keyword for kw in keywords[:30]])
    city_list = '، '.join([c.name for c in cities[:30]])
    run_rows = ''.join([
        f'<tr><td>{h(source_label(r.source))}</td><td>{h(r.query or "")}</td><td>{r.found_count}</td><td>{r.new_count}</td><td>{h(r.error or "")}</td><td class="ltr">{h(r.started_at)}</td></tr>'
        for r in runs
    ])

    body = f'''
    <div class="top">
      <div class="brand">
        <h1>🎮 بانک اطلاعاتی فروشنده‌های گیمینگ</h1>
        <div class="muted">همه مخاطبین، فروشگاه‌ها، پیج‌ها، کانال‌ها و آگهی‌های پیدا شده اینجا داخل دیتابیس ذخیره می‌شوند.</div>
        <div class="nav">
          <a class="active" href="/">بانک اطلاعاتی</a>
          <a href="#openrouter-web-search">سرچ مستقیم AI</a>
          <a href="#ai-search">AI + Tavily</a>
          <a href="#collector">جمع‌آوری مخاطب</a>
          <a href="#settings">کلمات و شهرها</a>
          <a href="#reports">گزارش اجراها</a>
          <a href="/crm">CRM پیشرفته</a>
          <a href="/analytics">📊 آنالیتیکس</a>
          <a href="/sponsor">🎯 اسپانسری</a>
          <a href="/influencer">🌟 اینفلوئنسرها</a>
          <a href="/campaigns">کمپین‌ها</a>
          <a href="/people">بانک افراد</a>
          <a href="/crm/queue">صف سرچ</a>
          <a href="/crm/templates">قالب پیام</a>
        </div>
      </div>
      <div>
        <a class="btn2" style="padding:10px;border-radius:10px" href="/export.xlsx?status={h(status)}&source={h(source)}&category={h(category)}&q={h(q)}">دانلود پشتیبان Excel</a>
      </div>
    </div>

    <div class="stats card">
      <div class="stat">کل مخاطبین<b>{stats['total']}</b></div>
      <div class="stat">در انتظار بررسی<b>{stats['new']}</b></div>
      <div class="stat">پیام داده شده<b>{stats['messaged']}</b></div>
      <div class="stat">جواب داده<b>{stats['replied']}</b></div>
      <div class="stat">ثبت‌نام کرده<b>{stats['registered']}</b></div>
    </div>

    {f'<div class="card hint">{h(ai_msg)}</div>' if ai_msg else ''}

    <div id="openrouter-web-search" class="card">
      <div class="section-title"><h3>سرچ مستقیم با هوش مصنوعی OpenRouter</h3><span class="pill">بدون Tavily</span></div>
      <form class="inline" method="post" action="/openrouter-web-search">
        <input name="topic" placeholder="مثلاً خرید اکانت کلش رویال" required style="min-width:280px">
        <input name="city" placeholder="شهر؛ اختیاری" value="تهران" style="width:120px">
        <label>حداکثر لید <input type="number" name="max_results" value="10" min="1" max="30" style="width:80px"></label>
        <label>حداقل امتیاز <input type="number" name="min_score" value="60" min="0" max="100" style="width:80px"></label>
        <label><input type="checkbox" name="force" value="1"> اجرای مجدد حتی اگر امروز اجرا شده</label>
        <button>فقط با AI سرچ کن و ذخیره کن</button>
      </form>
      <div class="muted" style="margin-top:10px">پیشنهادهای آماده سرچ:</div>{suggestion_buttons()}
      <div class="hint">این بخش از Tavily استفاده نمی‌کند و فقط با قابلیت Web Search خود OpenRouter کار می‌کند. Groq و HuggingFace سرچ وب مستقیم ندارند؛ اگر OpenRouter web search یا مدل‌های رایگان limit بخورند، برنامه مدل بعدی OpenRouter را امتحان می‌کند. شماره/سایت/پیج فقط وقتی ذخیره می‌شود که عمومی پیدا شود.</div>
    </div>

    <div id="ai-search" class="card">
      <div class="section-title"><h3>جستجوی هوشمند با هوش مصنوعی</h3><span class="pill">AI + Tavily + حذف تکراری سختگیرانه</span></div>
      <form class="inline" method="post" action="/ai-search">
        <input name="topic" placeholder="مثلاً فروش سی پی کالاف" required style="min-width:260px">
        <input name="city" placeholder="شهر؛ اختیاری" value="تهران" style="width:120px">
        <label>تعداد query <input type="number" name="max_queries" value="6" min="1" max="20" style="width:80px"></label>
        <label>نتیجه هر query <input type="number" name="results_per_query" value="5" min="1" max="20" style="width:80px"></label>
        <label>حداقل امتیاز AI <input type="number" name="min_score" value="60" min="0" max="100" style="width:80px"></label>
        <label><input type="checkbox" name="force" value="1"> اجرای مجدد حتی اگر امروز اجرا شده</label>
        <button>سرچ کن و لیدهای خوب را ذخیره کن</button>
      </form>
      <div class="muted" style="margin-top:10px">پیشنهادهای آماده سرچ:</div>{suggestion_buttons()}
      <div class="hint">هوش مصنوعی اول عبارت‌های جستجوی بهتر می‌سازد، Tavily سرچ واقعی انجام می‌دهد، سپس AI نتایج نامرتبط را حذف می‌کند. فقط موارد تأییدشده و غیرتکراری در بانک ذخیره می‌شوند. اگر یک مدل لیمیت بخورد، خودکار مدل/سرویس بعدی امتحان می‌شود.</div>
    </div>

    <div id="collector" class="card">
      <div class="section-title"><h3>جمع‌آوری مخاطب جدید</h3><span class="pill">پیشنهاد فعلی: Tavily با تعداد کم</span></div>
      <form class="inline" method="post" action="/run">
        <select name="source">{collector_options('tavily')}</select>
        <label>تعداد کلمات <input type="number" name="keyword_limit" value="1" min="1" max="30" style="width:80px"></label>
        <label>تعداد شهرها <input type="number" name="city_limit" value="1" min="1" max="30" style="width:80px"></label>
        <label>نتیجه برای هر جستجو <input type="number" name="result_limit" value="5" min="1" max="20" style="width:80px"></label>
        <button>شروع جمع‌آوری و ذخیره در بانک</button>
      </form>
      <div class="hint">برای اینکه اعتبار Tavily سریع مصرف نشود، اول با ۱ کلمه و ۱ شهر تست کن. همه نتایج مستقیم داخل بانک اطلاعاتی ذخیره می‌شوند.</div>
      <hr style="border:0;border-top:1px solid #eee;margin:14px 0">
      <form class="inline" method="post" action="/enrich">
        <label>بررسی سایت‌های عمومی <input type="number" name="limit" value="30" min="1" max="200" style="width:90px"></label>
        <button class="btn2">پیدا کردن لینک اینستاگرام/تلگرام از سایت‌ها</button>
      </form>
    </div>

    <div class="card">
      <h3>افزودن مخاطب دستی به بانک</h3>
      <form class="inline" method="post" action="/leads">
        <input name="title" placeholder="نام فروشنده/عنوان آگهی" required>
        <input name="url" placeholder="لینک اصلی" required style="min-width:260px">
        <input name="source" value="دستی" style="width:90px">
        <input name="city" placeholder="شهر" style="width:90px">
        <input name="phone" placeholder="تلفن">
        <input name="website" placeholder="وب‌سایت">
        <input name="instagram" placeholder="اینستاگرام">
        <input name="telegram" placeholder="تلگرام">
        <button class="btn2">ذخیره مخاطب</button>
      </form>
    </div>

    <div class="card">
      <h3>جستجو و فیلتر بانک اطلاعاتی</h3>
      <form class="inline" method="get" action="/">
        <input name="q" placeholder="جستجو در نام، لینک، شهر، تلفن و توضیحات" value="{h(q)}" style="min-width:280px">
        <input name="source" placeholder="منبع؛ مثلا tavily یا instagram" value="{h(source)}">
        <input name="category" placeholder="دسته؛ مثلا گیفت کارت" value="{h(category)}">
        <select name="status"><option value="">همه وضعیت‌ها</option>{status_options(status)}</select>
        <select name="sort">{sort_options(sort)}</select>
        <input name="limit" type="number" min="1" max="500" value="{limit}" style="width:90px">
        <button>نمایش</button>
      </form>
    </div>

    <div class="card">
      <div class="section-title"><h3>بانک اطلاعاتی مخاطبین</h3><span class="muted">نتایج بر اساس منبع داخل پوشه‌های جدا تفکیک شده‌اند | مرتب‌سازی فعلی: {h(SORT_LABELS.get(sort, sort))}</span></div>
      <div class="folder-grid">{folder_cards}</div>
      {grouped_sections or '<div class="empty-folder">هنوز مخاطبی در بانک اطلاعاتی ذخیره نشده است.</div>'}
    </div>

    <div id="settings" class="grid2">
      <div class="card"><h3>کلمات کلیدی جمع‌آوری</h3><form class="inline" method="post" action="/keywords"><input name="keyword" placeholder="مثلاً فروشگاه کنسول"><button class="btn2">افزودن کلمه</button></form><p class="muted">{h(keyword_list)}</p></div>
      <div class="card"><h3>شهرهای هدف</h3><form class="inline" method="post" action="/cities"><input name="name" placeholder="نام شهر"><input name="lat" placeholder="عرض جغرافیایی" style="width:110px"><input name="lng" placeholder="طول جغرافیایی" style="width:110px"><button class="btn2">افزودن شهر</button></form><p class="muted">{h(city_list)}</p></div>
    </div>

    <div class="card">
      <h3>ورود لیست از فایل CSV</h3>
      <form class="inline" method="post" action="/import.csv" enctype="multipart/form-data">
        <input type="file" name="file" accept=".csv,text/csv" required>
        <button class="btn2">وارد کردن و ذخیره در بانک</button>
      </form>
      <div class="muted">ستون‌های قابل قبول: title,url,source,city,phone,website,instagram,telegram,address,description,category</div>
    </div>

    <div id="crm-shortcuts" class="card">
      <div class="section-title"><h3>ابزارهای CRM و مدیریت پروژه</h3><span class="pill">جدید</span></div>
      <a class="action" href="/crm">داشبورد CRM</a>
      <a class="action" href="/analytics">📊 آنالیتیکس و نمودارها</a>
      <a class="action" href="/sponsor">🎯 کانال‌های اسپانسری</a>
      <a class="action" href="/influencer">🌟 اینفلوئنسرهای گیمینگ</a>
      <a class="action" href="/campaigns">کمپین‌های تبلیغاتی</a>
      <a class="action" href="/people">بانک افراد</a>
      <a class="action" href="/crm/templates">قالب‌های پیام</a>
      <a class="action" href="/crm/queue">صف جستجو</a>
      <a class="action" href="/crm/rules">Blacklist / Whitelist</a>
      <a class="action" href="/crm/api-status">وضعیت API و مصرف</a>
      <a class="action" href="/crm/presets">Search Preset</a>
      <a class="action" href="/crm/settings">تنظیمات سایت</a>
      <a class="action" href="/crm/conversion">گزارش تبدیل</a>
      <div class="muted" style="margin-top:8px">صفحه جزئیات هر مخاطب از دکمه «جزئیات و تاریخچه» داخل جدول باز می‌شود.</div>
    </div>

    <div id="reports" class="card" style="overflow:auto">
      <h3>گزارش آخرین اجراهای جمع‌آوری</h3>
      <table><thead><tr><th>منبع</th><th>عبارت جستجو</th><th>تعداد پیدا شده</th><th>جدید</th><th>خطا</th><th>زمان شروع</th></tr></thead><tbody>{run_rows}</tbody></table>
    </div>
    '''
    return page('بانک اطلاعاتی فروشنده‌های گیمینگ', body)


@app.post('/openrouter-web-search')
async def openrouter_web_search_now(
    db: Session = Depends(get_db),
    token: Annotated[str, Form()] = '',
    topic: Annotated[str, Form()] = '',
    city: Annotated[str, Form()] = '',
    max_results: Annotated[int, Form()] = 10,
    min_score: Annotated[int, Form()] = 60,
    force: Annotated[str | None, Form()] = None,
):
    check_token(token)
    topic = topic.strip()
    city = city.strip() or None
    if not topic:
        return RedirectResponse(url=f'/?ai_msg={quote_plus("موضوع جستجو خالی است")}', status_code=303)
    if not force and search_recently_run(db, 'openrouter_web_ai', (topic + ' | ' + (city or '')), hours=24):
        msg = 'این سرچ در ۲۴ ساعت اخیر اجرا شده؛ برای اجرای دوباره گزینه اجرای مجدد را بزن.'
        return RedirectResponse(url=f'/?ai_msg={quote_plus(msg)}#openrouter-web-search', status_code=303)
    ok, usage_msg = can_use_provider(db, 'openrouter')
    if not ok:
        return RedirectResponse(url=f'/?ai_msg={quote_plus(usage_msg)}#openrouter-web-search', status_code=303)
    increment_usage(db, 'openrouter')
    result = await run_openrouter_web_search(
        db,
        topic=topic,
        city=city,
        max_results=max_results,
        min_score=min_score,
    )
    if not result.get('ok'):
        msg = 'خطا در سرچ مستقیم AI: ' + str(result.get('error', 'نامشخص'))
    else:
        used_model = (result.get('model') or {}).get('model') if isinstance(result.get('model'), dict) else ''
        msg = f"سرچ مستقیم AI انجام شد: {result.get('found',0)} لید پیدا شد، {result.get('saved',0)} مخاطب تازه ذخیره شد، {result.get('duplicates',0)} مورد تکراری merge شد. مدل: {used_model}"
    ids = ','.join(str(x) for x in result.get('saved_ids', []) or [])
    return RedirectResponse(url=f'/?ai_msg={quote_plus(msg)}&new_ids={quote_plus(ids)}&sort=newest#openrouter-web-search', status_code=303)


@app.post('/ai-search')
async def ai_search_now(
    db: Session = Depends(get_db),
    token: Annotated[str, Form()] = '',
    topic: Annotated[str, Form()] = '',
    city: Annotated[str, Form()] = '',
    max_queries: Annotated[int, Form()] = 6,
    results_per_query: Annotated[int, Form()] = 5,
    min_score: Annotated[int, Form()] = 60,
    force: Annotated[str | None, Form()] = None,
):
    check_token(token)
    topic = topic.strip()
    city = city.strip() or None
    if not topic:
        return RedirectResponse(url=f'/?ai_msg={quote_plus("موضوع جستجو خالی است")}', status_code=303)
    if not force and search_recently_run(db, 'ai_search', (topic + ' | ' + (city or '')), hours=24):
        msg = 'این سرچ در ۲۴ ساعت اخیر اجرا شده؛ برای اجرای دوباره گزینه اجرای مجدد را بزن.'
        return RedirectResponse(url=f'/?ai_msg={quote_plus(msg)}#ai-search', status_code=303)
    ok, usage_msg = can_use_provider(db, 'tavily')
    if not ok:
        return RedirectResponse(url=f'/?ai_msg={quote_plus(usage_msg)}#ai-search', status_code=303)
    increment_usage(db, 'tavily')
    result = await run_ai_search(
        db,
        topic=topic,
        city=city,
        max_queries=max_queries,
        results_per_query=results_per_query,
        min_score=min_score,
    )
    if not result.get('ok'):
        msg = 'خطا در جستجوی هوشمند: ' + str(result.get('error', 'نامشخص'))
    else:
        msg = f"جستجوی هوشمند انجام شد: {result.get('found',0)} نتیجه بررسی شد، {result.get('approved',0)} مورد تأیید AI بود، {result.get('saved',0)} مخاطب تازه ذخیره شد، {result.get('duplicates',0)} مورد تکراری merge شد."
        if result.get('errors'):
            msg += ' هشدار: ' + ' | '.join(result.get('errors', [])[:2])[:350]
    ids = ','.join(str(x) for x in result.get('saved_ids', []) or [])
    return RedirectResponse(url=f'/?ai_msg={quote_plus(msg)}&new_ids={quote_plus(ids)}&sort=newest#ai-search', status_code=303)


@app.post('/run')
async def run_now(
    db: Session = Depends(get_db),
    token: Annotated[str, Form()] = '',
    source: Annotated[str, Form()] = 'tavily',
    keyword_limit: Annotated[int, Form()] = 1,
    city_limit: Annotated[int, Form()] = 1,
    result_limit: Annotated[int, Form()] = 5,
):
    check_token(token)
    await run_collector(db, source=source, keyword_limit=keyword_limit, city_limit=city_limit, result_limit=result_limit)
    return RedirectResponse(url=f'/', status_code=303)


@app.get('/api/run')
async def api_run(
    db: Session = Depends(get_db),
    token: str = Query(...),
    source: str = 'tavily',
    keyword_limit: int = 1,
    city_limit: int = 1,
    result_limit: int = 5,
):
    check_token(token)
    return await run_collector(db, source=source, keyword_limit=keyword_limit, city_limit=city_limit, result_limit=result_limit)


@app.post('/keywords')
def add_keyword(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', keyword: Annotated[str, Form()] = ''):
    check_token(token)
    keyword = keyword.strip()
    if keyword and not db.scalar(select(Keyword).where(Keyword.keyword == keyword)):
        db.add(Keyword(keyword=keyword))
        db.commit()
    return RedirectResponse(url=f'/#settings', status_code=303)


@app.post('/cities')
def add_city(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', name: Annotated[str, Form()] = '', lat: Annotated[str, Form()] = '', lng: Annotated[str, Form()] = ''):
    check_token(token)
    name = name.strip()
    if name and not db.scalar(select(City).where(City.name == name)):
        db.add(City(name=name, lat=float(lat) if lat else None, lng=float(lng) if lng else None))
        db.commit()
    return RedirectResponse(url=f'/#settings', status_code=303)


@app.post('/leads')
def add_lead(
    db: Session = Depends(get_db),
    token: Annotated[str, Form()] = '',
    title: Annotated[str, Form()] = '',
    url: Annotated[str, Form()] = '',
    source: Annotated[str, Form()] = 'manual',
    city: Annotated[str, Form()] = '',
    phone: Annotated[str, Form()] = '',
    website: Annotated[str, Form()] = '',
    instagram: Annotated[str, Form()] = '',
    telegram: Annotated[str, Form()] = '',
):
    check_token(token)
    category = detect_category(title, url)
    score = score_lead(title=title, url=url, phone=phone, website=website, instagram=instagram, telegram=telegram)
    upsert_lead(db, {
        'source': source or 'manual', 'entity_type': 'manual', 'title': title, 'url': url,
        'city': city, 'phone': phone, 'website': website, 'instagram': instagram, 'telegram': telegram,
        'category': category, 'score': score,
    })
    return RedirectResponse(url=f'/', status_code=303)


@app.post('/leads/{lead_id}/update')
def update_lead(
    db: Session = Depends(get_db),
    lead_id: int = 0,
    token: Annotated[str, Form()] = '',
    status: Annotated[str, Form()] = 'new',
    phone: Annotated[str, Form()] = '',
    website: Annotated[str, Form()] = '',
    instagram: Annotated[str, Form()] = '',
    telegram: Annotated[str, Form()] = '',
    notes: Annotated[str, Form()] = '',
):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, 'مخاطب پیدا نشد')
    lead.status = status
    lead.phone = phone.strip() or None
    lead.website = website.strip() or None
    lead.instagram = instagram.strip() or None
    lead.telegram = telegram.strip() or None
    lead.notes = notes.strip() or None
    db.add(lead)
    db.commit()
    return RedirectResponse(url=f'/', status_code=303)


@app.post('/leads/{lead_id}/status')
def update_status(db: Session = Depends(get_db), lead_id: int = 0, token: Annotated[str, Form()] = '', status: Annotated[str, Form()] = 'new', notes: Annotated[str, Form()] = ''):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, 'مخاطب پیدا نشد')
    lead.status = status
    lead.notes = notes
    db.add(lead)
    db.commit()
    return RedirectResponse(url=f'/', status_code=303)


@app.get('/export.csv')
def export_csv(db: Session = Depends(get_db), status: str = '', source: str = '', category: str = '', q: str = ''):
    stmt = build_query(db, status, source, category, q)
    leads = list(db.scalars(stmt.order_by(desc(Lead.score), desc(Lead.first_seen))).all())
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(['شناسه', 'منبع', 'نوع', 'نام/عنوان', 'لینک اصلی', 'دسته', 'شهر', 'امتیاز', 'وضعیت', 'تلفن', 'وب‌سایت', 'اینستاگرام', 'تلگرام', 'آدرس', 'توضیحات', 'امتیاز مپ', 'تعداد نظر', 'یادداشت', 'اولین مشاهده', 'آخرین مشاهده'])
    for l in leads:
        writer.writerow([l.id, source_label(l.source), l.entity_type, l.title, l.url, l.category, l.city, l.score, status_label(l.status), l.phone, l.website, l.instagram, l.telegram, l.address, l.description, l.rating, l.review_count, l.notes, l.first_seen, l.last_seen])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename=game-leads.csv'})


@app.get('/export.xlsx')
def export_xlsx(db: Session = Depends(get_db), status: str = '', source: str = '', category: str = '', q: str = ''):
    stmt = build_query(db, status, source, category, q)
    leads = list(db.scalars(stmt.order_by(desc(Lead.score), desc(Lead.first_seen))).all())
    wb = Workbook()
    ws = wb.active
    ws.title = 'بانک مخاطبین'
    headers = ['شناسه', 'منبع', 'نوع', 'نام/عنوان', 'لینک اصلی', 'دسته', 'شهر', 'امتیاز', 'وضعیت', 'تلفن', 'وب‌سایت', 'اینستاگرام', 'تلگرام', 'آدرس', 'توضیحات', 'امتیاز مپ', 'تعداد نظر', 'یادداشت', 'اولین مشاهده', 'آخرین مشاهده']
    ws.append(headers)
    for l in leads:
        ws.append([l.id, source_label(l.source), l.entity_type, l.title, l.url, l.category, l.city, l.score, status_label(l.status), l.phone, l.website, l.instagram, l.telegram, l.address, l.description, l.rating, l.review_count, l.notes, str(l.first_seen), str(l.last_seen)])
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col[:50])
        ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 48)
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=game-leads.xlsx'})


@app.post('/import.csv')
async def import_csv(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', file: UploadFile = File(...)):
    check_token(token)
    raw = await file.read()
    text = raw.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        title = (row.get('title') or row.get('عنوان') or row.get('نام/عنوان') or '').strip()
        url = (row.get('url') or row.get('link') or row.get('لینک') or row.get('لینک اصلی') or '').strip()
        if not title or not url:
            continue
        data = {
            'source': (row.get('source') or row.get('منبع') or 'csv').strip(),
            'entity_type': row.get('type') or row.get('entity_type') or 'imported',
            'title': title,
            'url': url,
            'category': row.get('category') or row.get('دسته'),
            'city': row.get('city') or row.get('شهر'),
            'phone': row.get('phone') or row.get('تلفن'),
            'website': row.get('website') or row.get('سایت') or row.get('وب‌سایت'),
            'address': row.get('address') or row.get('آدرس'),
            'description': row.get('description') or row.get('توضیح') or row.get('توضیحات'),
            'instagram': row.get('instagram') or row.get('اینستاگرام'),
            'telegram': row.get('telegram') or row.get('تلگرام'),
        }
        upsert_lead(db, data)
    return RedirectResponse(url=f'/', status_code=303)


@app.post('/enrich')
async def enrich(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', limit: Annotated[int, Form()] = 30):
    check_token(token)
    await run_enrichment(db, limit=limit)
    return RedirectResponse(url=f'/', status_code=303)


@app.get('/api/leads')
def api_leads(db: Session = Depends(get_db), token: str = Query(...), status: str = '', source: str = '', sort: str = 'newest', limit: int = Query(100, ge=1, le=500)):
    check_token(token)
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source.ilike(f'%{source}%'))
    leads = list(db.scalars(apply_sort(stmt, sort).limit(limit)).all())
    return [
        {
            'id': l.id, 'source': source_label(l.source), 'source_code': l.source, 'type': l.entity_type,
            'title': l.title, 'url': l.url, 'category': l.category, 'city': l.city,
            'score': l.score, 'status': status_label(l.status), 'status_code': l.status,
            'phone': l.phone, 'website': l.website, 'instagram': l.instagram, 'telegram': l.telegram,
            'address': l.address, 'description': l.description, 'rating': l.rating, 'review_count': l.review_count,
            'first_seen': l.first_seen.isoformat(), 'last_seen': l.last_seen.isoformat(),
        }
        for l in leads
    ]


@app.get('/health')
def health():
    return {'ok': True, 'message': 'برنامه و دیتابیس فعال هستند'}
