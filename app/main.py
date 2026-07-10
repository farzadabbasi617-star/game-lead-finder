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

app = FastAPI(title='بانک اطلاعاتی لیدهای گیمینگ')

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
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        init_seed_data(db)
    finally:
        db.close()


def check_token(token: str | None = None, x_admin_token: str | None = Header(default=None)):
    settings = get_settings()
    supplied = token or x_admin_token
    if settings.admin_token and supplied != settings.admin_token:
        raise HTTPException(status_code=401, detail='رمز مدیریت اشتباه است')


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


def css() -> str:
    return '''
    <style>
      *{box-sizing:border-box} body{font-family:Tahoma,Arial,sans-serif;background:#f4f6fb;margin:0;color:#111;direction:rtl} a{color:#214ec2;text-decoration:none} a:hover{text-decoration:underline}
      .wrap{max-width:1480px;margin:auto;padding:22px}.top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.brand h1{margin:0;font-size:25px}.muted{color:#667085;font-size:13px;line-height:1.8}.card{background:#fff;border:1px solid #e6e9f2;border-radius:16px;padding:16px;margin:14px 0;box-shadow:0 4px 18px rgba(20,30,60,.045)}
      .nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.nav a,.pill{display:inline-block;padding:9px 12px;border-radius:999px;background:#eef2ff;color:#173c9b;border:1px solid #d6e0ff;font-size:13px}.nav .active{background:#1f55d5;color:white;border-color:#1f55d5}
      .stats{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:10px}.stat{background:linear-gradient(135deg,#fff,#edf4ff);border:1px solid #dce7ff;border-radius:14px;padding:14px}.stat b{display:block;font-size:25px;margin-top:6px;color:#123b94}
      form.inline{display:flex;gap:8px;flex-wrap:wrap;align-items:center} input,select,button,textarea{font-family:inherit;border:1px solid #d6dbe8;border-radius:10px;padding:9px;background:#fff} textarea{min-height:42px} button{cursor:pointer;background:#1f55d5;color:white;border:0}.btn2{background:#eef2ff;color:#143891;border:1px solid #cdd8ff}.btn3{background:#f8fafc;color:#344054;border:1px solid #d0d5dd}.danger{background:#fff2f2;color:#ad1e1e;border:1px solid #ffd0d0}
      table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:12px;overflow:hidden} th,td{padding:10px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top;font-size:13px} th{background:#f1f4ff;color:#344054;position:sticky;top:0;z-index:1} tr:hover{background:#fbfcff}
      .badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#eef2ff;color:#2546a6;font-size:12px;margin:2px}.status{background:#ecfdf3;color:#027a48}.score{font-weight:bold;color:#137333}.url{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;direction:ltr}.small{font-size:12px}.ltr{direction:ltr;text-align:left}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
      .actions{display:flex;gap:6px;flex-wrap:wrap;min-width:230px}.action{display:inline-block;padding:7px 9px;border-radius:9px;background:#f2f4f7;color:#344054;border:1px solid #d0d5dd;font-size:12px;line-height:1.2}.action.primary{background:#1f55d5;color:white;border-color:#1f55d5}.action.insta{background:#fff0f6;color:#c11574;border-color:#ffcce1}.action.tg{background:#eef8ff;color:#026aa2;border-color:#b9e6fe}button.action{cursor:pointer}
      .lead-title{font-weight:700;color:#101828}.editable input{width:130px}.editable .wide{width:210px}.fresh-row{background:#f0fdf4!important}.fresh-badge{display:inline-block;background:#12b76a;color:white;border-radius:999px;padding:4px 8px;font-size:12px;margin:2px}.timebox{line-height:1.9;color:#475467}.suggestions{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.suggestion{background:#f8fafc;color:#344054;border:1px dashed #98a2b3;border-radius:999px;padding:7px 10px;font-size:12px}.remember{display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid #e6e9f2;border-radius:999px;padding:8px 10px;margin-top:8px}.section-title{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.hint{background:#fffaeb;border:1px solid #fedf89;color:#7a4b00;border-radius:12px;padding:10px;margin-top:10px}
      @media(max-width:900px){.stats,.grid2,.grid3{grid-template-columns:1fr}.wrap{padding:12px} table{display:block;overflow-x:auto;direction:rtl}.top{display:block}.editable input{width:150px}}
    </style>
    '''


def page(title: str, body: str) -> HTMLResponse:
    script = '''
    <script>
      function fillStoredToken(){
        const saved = localStorage.getItem('glf_admin_token') || '';
        if(saved){ document.querySelectorAll('input[name="token"]').forEach(i=>{ if(!i.value) i.value=saved; }); }
        const cb=document.getElementById('remember-token'); if(cb) cb.checked=!!saved;
      }
      function saveTokenIfNeeded(){
        const cb=document.getElementById('remember-token');
        const first=document.querySelector('input[name="token"]');
        if(cb && cb.checked && first && first.value){ localStorage.setItem('glf_admin_token', first.value); }
      }
      document.addEventListener('DOMContentLoaded', fillStoredToken);
      document.addEventListener('input', function(e){
        if(e.target && e.target.name==='token'){
          document.querySelectorAll('input[name="token"]').forEach(i=>{ if(i!==e.target) i.value=e.target.value; });
          saveTokenIfNeeded();
        }
      });
      document.addEventListener('change', function(e){
        if(e.target && e.target.id==='remember-token'){
          if(e.target.checked){ saveTokenIfNeeded(); } else { localStorage.removeItem('glf_admin_token'); }
        }
      });
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
    token: str = Query('', description='رمز مدیریت'),
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

    lead_rows = ''
    for lead in leads:
        is_fresh = lead.id in fresh_ids
        fresh_badge = '<span class="fresh-badge">تازه اضافه شد</span><br>' if is_fresh else ''
        row_class = 'fresh-row' if is_fresh else ''
        lead_rows += f'''
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
          <td>{contact_buttons(lead)}</td>
          <td class="editable">
            <form class="inline" method="post" action="/leads/{lead.id}/update">
              <input type="hidden" name="token" value="{h(token)}">
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
          <a class="active" href="/?token={h(token)}">بانک اطلاعاتی</a>
          <a href="#openrouter-web-search">سرچ مستقیم AI</a>
          <a href="#ai-search">AI + Tavily</a>
          <a href="#collector">جمع‌آوری مخاطب</a>
          <a href="#settings">کلمات و شهرها</a>
          <a href="#reports">گزارش اجراها</a>
        </div>
        <label class="remember"><input type="checkbox" id="remember-token"> رمز مدیریت را در این مرورگر به خاطر بسپار</label>
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
        <input type="password" name="token" placeholder="رمز مدیریت" value="{h(token)}">
        <input name="topic" placeholder="مثلاً خرید اکانت کلش رویال" required style="min-width:280px">
        <input name="city" placeholder="شهر؛ اختیاری" value="تهران" style="width:120px">
        <label>حداکثر لید <input type="number" name="max_results" value="10" min="1" max="30" style="width:80px"></label>
        <label>حداقل امتیاز <input type="number" name="min_score" value="60" min="0" max="100" style="width:80px"></label>
        <button>فقط با AI سرچ کن و ذخیره کن</button>
      </form>
      <div class="muted" style="margin-top:10px">پیشنهادهای آماده سرچ:</div>{suggestion_buttons()}
      <div class="hint">این بخش از Tavily استفاده نمی‌کند و فقط با قابلیت Web Search خود OpenRouter کار می‌کند. Groq و HuggingFace سرچ وب مستقیم ندارند؛ اگر OpenRouter web search یا مدل‌های رایگان limit بخورند، برنامه مدل بعدی OpenRouter را امتحان می‌کند. شماره/سایت/پیج فقط وقتی ذخیره می‌شود که عمومی پیدا شود.</div>
    </div>

    <div id="ai-search" class="card">
      <div class="section-title"><h3>جستجوی هوشمند با هوش مصنوعی</h3><span class="pill">AI + Tavily + حذف تکراری سختگیرانه</span></div>
      <form class="inline" method="post" action="/ai-search">
        <input type="password" name="token" placeholder="رمز مدیریت" value="{h(token)}">
        <input name="topic" placeholder="مثلاً فروش سی پی کالاف" required style="min-width:260px">
        <input name="city" placeholder="شهر؛ اختیاری" value="تهران" style="width:120px">
        <label>تعداد query <input type="number" name="max_queries" value="6" min="1" max="20" style="width:80px"></label>
        <label>نتیجه هر query <input type="number" name="results_per_query" value="5" min="1" max="20" style="width:80px"></label>
        <label>حداقل امتیاز AI <input type="number" name="min_score" value="60" min="0" max="100" style="width:80px"></label>
        <button>سرچ کن و لیدهای خوب را ذخیره کن</button>
      </form>
      <div class="muted" style="margin-top:10px">پیشنهادهای آماده سرچ:</div>{suggestion_buttons()}
      <div class="hint">هوش مصنوعی اول عبارت‌های جستجوی بهتر می‌سازد، Tavily سرچ واقعی انجام می‌دهد، سپس AI نتایج نامرتبط را حذف می‌کند. فقط موارد تأییدشده و غیرتکراری در بانک ذخیره می‌شوند. اگر یک مدل لیمیت بخورد، خودکار مدل/سرویس بعدی امتحان می‌شود.</div>
    </div>

    <div id="collector" class="card">
      <div class="section-title"><h3>جمع‌آوری مخاطب جدید</h3><span class="pill">پیشنهاد فعلی: Tavily با تعداد کم</span></div>
      <form class="inline" method="post" action="/run">
        <input type="password" name="token" placeholder="رمز مدیریت" value="{h(token)}">
        <select name="source">{collector_options('tavily')}</select>
        <label>تعداد کلمات <input type="number" name="keyword_limit" value="1" min="1" max="30" style="width:80px"></label>
        <label>تعداد شهرها <input type="number" name="city_limit" value="1" min="1" max="30" style="width:80px"></label>
        <label>نتیجه برای هر جستجو <input type="number" name="result_limit" value="5" min="1" max="20" style="width:80px"></label>
        <button>شروع جمع‌آوری و ذخیره در بانک</button>
      </form>
      <div class="hint">برای اینکه اعتبار Tavily سریع مصرف نشود، اول با ۱ کلمه و ۱ شهر تست کن. همه نتایج مستقیم داخل بانک اطلاعاتی ذخیره می‌شوند.</div>
      <hr style="border:0;border-top:1px solid #eee;margin:14px 0">
      <form class="inline" method="post" action="/enrich">
        <input type="hidden" name="token" value="{h(token)}">
        <label>بررسی سایت‌های عمومی <input type="number" name="limit" value="30" min="1" max="200" style="width:90px"></label>
        <button class="btn2">پیدا کردن لینک اینستاگرام/تلگرام از سایت‌ها</button>
      </form>
    </div>

    <div class="card">
      <h3>افزودن مخاطب دستی به بانک</h3>
      <form class="inline" method="post" action="/leads">
        <input type="hidden" name="token" value="{h(token)}">
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
        <input name="token" placeholder="رمز مدیریت" value="{h(token)}" type="password">
        <input name="q" placeholder="جستجو در نام، لینک، شهر، تلفن و توضیحات" value="{h(q)}" style="min-width:280px">
        <input name="source" placeholder="منبع؛ مثلا tavily یا instagram" value="{h(source)}">
        <input name="category" placeholder="دسته؛ مثلا گیفت کارت" value="{h(category)}">
        <select name="status"><option value="">همه وضعیت‌ها</option>{status_options(status)}</select>
        <select name="sort">{sort_options(sort)}</select>
        <input name="limit" type="number" min="1" max="500" value="{limit}" style="width:90px">
        <button>نمایش</button>
      </form>
    </div>

    <div class="card" style="overflow:auto">
      <div class="section-title"><h3>بانک اطلاعاتی مخاطبین</h3><span class="muted">مرتب‌سازی فعلی: {h(SORT_LABELS.get(sort, sort))} | ارتباط فقط با دکمه‌های باز کردن پلتفرم انجام می‌شود؛ ارسال خودکار پیام نداریم.</span></div>
      <table>
        <thead><tr><th>منبع/وضعیت/زمان</th><th>مشخصات مخاطب</th><th>راه‌های ارتباط</th><th>ویرایش و پیگیری</th><th>لینک/آدرس</th></tr></thead>
        <tbody>{lead_rows or '<tr><td colspan="5">هنوز مخاطبی در بانک اطلاعاتی ذخیره نشده است.</td></tr>'}</tbody>
      </table>
    </div>

    <div id="settings" class="grid2">
      <div class="card"><h3>کلمات کلیدی جمع‌آوری</h3><form class="inline" method="post" action="/keywords"><input type="hidden" name="token" value="{h(token)}"><input name="keyword" placeholder="مثلاً فروشگاه کنسول"><button class="btn2">افزودن کلمه</button></form><p class="muted">{h(keyword_list)}</p></div>
      <div class="card"><h3>شهرهای هدف</h3><form class="inline" method="post" action="/cities"><input type="hidden" name="token" value="{h(token)}"><input name="name" placeholder="نام شهر"><input name="lat" placeholder="عرض جغرافیایی" style="width:110px"><input name="lng" placeholder="طول جغرافیایی" style="width:110px"><button class="btn2">افزودن شهر</button></form><p class="muted">{h(city_list)}</p></div>
    </div>

    <div class="card">
      <h3>ورود لیست از فایل CSV</h3>
      <form class="inline" method="post" action="/import.csv" enctype="multipart/form-data">
        <input type="hidden" name="token" value="{h(token)}">
        <input type="file" name="file" accept=".csv,text/csv" required>
        <button class="btn2">وارد کردن و ذخیره در بانک</button>
      </form>
      <div class="muted">ستون‌های قابل قبول: title,url,source,city,phone,website,instagram,telegram,address,description,category</div>
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
):
    check_token(token)
    topic = topic.strip()
    city = city.strip() or None
    if not topic:
        return RedirectResponse(url=f'/?token={quote_plus(token)}&ai_msg={quote_plus("موضوع جستجو خالی است")}', status_code=303)
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
    return RedirectResponse(url=f'/?token={quote_plus(token)}&ai_msg={quote_plus(msg)}&new_ids={quote_plus(ids)}&sort=newest#openrouter-web-search', status_code=303)


@app.post('/ai-search')
async def ai_search_now(
    db: Session = Depends(get_db),
    token: Annotated[str, Form()] = '',
    topic: Annotated[str, Form()] = '',
    city: Annotated[str, Form()] = '',
    max_queries: Annotated[int, Form()] = 6,
    results_per_query: Annotated[int, Form()] = 5,
    min_score: Annotated[int, Form()] = 60,
):
    check_token(token)
    topic = topic.strip()
    city = city.strip() or None
    if not topic:
        return RedirectResponse(url=f'/?token={quote_plus(token)}&ai_msg={quote_plus("موضوع جستجو خالی است")}', status_code=303)
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
    return RedirectResponse(url=f'/?token={quote_plus(token)}&ai_msg={quote_plus(msg)}&new_ids={quote_plus(ids)}&sort=newest#ai-search', status_code=303)


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
    return RedirectResponse(url=f'/?token={quote_plus(token)}', status_code=303)


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
    return RedirectResponse(url=f'/?token={quote_plus(token)}#settings', status_code=303)


@app.post('/cities')
def add_city(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', name: Annotated[str, Form()] = '', lat: Annotated[str, Form()] = '', lng: Annotated[str, Form()] = ''):
    check_token(token)
    name = name.strip()
    if name and not db.scalar(select(City).where(City.name == name)):
        db.add(City(name=name, lat=float(lat) if lat else None, lng=float(lng) if lng else None))
        db.commit()
    return RedirectResponse(url=f'/?token={quote_plus(token)}#settings', status_code=303)


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
    return RedirectResponse(url=f'/?token={quote_plus(token)}', status_code=303)


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
    return RedirectResponse(url=f'/?token={quote_plus(token)}', status_code=303)


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
    return RedirectResponse(url=f'/?token={quote_plus(token)}', status_code=303)


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
    return RedirectResponse(url=f'/?token={quote_plus(token)}', status_code=303)


@app.post('/enrich')
async def enrich(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', limit: Annotated[int, Form()] = 30):
    check_token(token)
    await run_enrichment(db, limit=limit)
    return RedirectResponse(url=f'/?token={quote_plus(token)}', status_code=303)


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
