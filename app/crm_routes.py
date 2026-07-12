from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import asc, desc, select
from sqlalchemy.orm import Session

from app.collectors.ai_search import run_ai_search
from app.collectors.openrouter_web_search import run_openrouter_web_search
from app.ai import chat_json
from app.config import get_settings
from app.crm import (
    PROFESSIONAL_STATUSES, can_use_provider, conversion_funnel, daily_limit, dashboard_more,
    extract_contacts_from_url, get_setting, get_usage, increment_usage, log_activity, recommended_contact,
    render_site_link, render_template, search_recently_run, set_setting, source_preferences,
    source_quality_report, validate_lead_link, validity_label,
)
from app.db.models import ActivityLog, Lead, MessageTemplate, SearchPreset, SearchQueueItem, SearchRule
from app.db.session import get_db
from app.utils import public_invite_message

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')


def h(v) -> str:
    import html
    return html.escape('' if v is None else str(v), quote=True)


def check_token(token: str | None = None):
    pass


def fmt_dt(value) -> str:
    if not value:
        return '-'
    try:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(TEHRAN_TZ).strftime('%Y/%m/%d - %H:%M')
    except Exception:
        return str(value)


def parse_local_datetime(value: str | None):
    if not value:
        return None
    try:
        local = datetime.strptime(value, '%Y-%m-%dT%H:%M').replace(tzinfo=TEHRAN_TZ)
        return local.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def layout(title: str, body: str, token: str = '') -> HTMLResponse:
    css = '''
    <style>
      :root{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb;--primary2:#1d4ed8;--soft:#eef4ff;--shadow:0 16px 40px rgba(16,24,40,.08)}
      *{box-sizing:border-box}body{font-family:Tahoma,Arial,sans-serif;background:radial-gradient(circle at top right,rgba(37,99,235,.10),transparent 34%),linear-gradient(180deg,#f8fbff,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}.wrap{max-width:1200px;margin:auto;padding:22px}a{color:var(--primary);text-decoration:none}
      .crm-hero{background:linear-gradient(135deg,#0f172a,#1e3a8a 58%,#2563eb);color:white;border-radius:26px;padding:20px;box-shadow:var(--shadow);margin-bottom:16px}.crm-hero h1{margin:0;font-size:25px}.crm-hero .muted{color:#dbeafe}.card{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045);backdrop-filter:blur(12px)}.card h1,.card h3{margin-top:0}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.muted{color:var(--muted);font-size:13px;line-height:1.8}.btn,.action,button{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer}.btn2{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe}.badge{display:inline-flex;background:#eef2ff;color:#2546a6;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;font-weight:600}input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}input:focus,select:focus,textarea:focus{border-color:#93c5fd;box-shadow:0 0 0 4px rgba(37,99,235,.12)}textarea{min-height:90px}.log{border-right:4px solid var(--primary);padding:11px;margin:10px 0;background:#f8fafc;border-radius:12px}.danger{background:#fff1f2;color:#be123c;border:1px solid #fecdd3}@media(max-width:800px){.wrap{padding:12px}.grid2,.grid3{grid-template-columns:1fr}.crm-hero{border-radius:20px}}
    </style>'''
    js = '''<script>document.addEventListener('click',async e=>{if(e.target.classList.contains('copy')){const t=e.target.dataset.text||'';try{await navigator.clipboard.writeText(t);e.target.textContent='کپی شد ✅'}catch(_){alert(t)}setTimeout(()=>e.target.textContent='کپی',1200)}});</script>'''
    return HTMLResponse(f'<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title>{css}</head><body><div class="wrap"><div class="crm-hero"><h1>{h(title)}</h1><div class="muted">مدیریت حرفه‌ای مخاطبین، پیگیری‌ها، قالب‌ها و سرچ‌ها</div><a class="btn btn2" href="/">بازگشت به بانک اطلاعاتی</a> <a class="btn btn2" href="/crm">داشبورد CRM</a> <a class="btn btn2" href="/analytics">📊 آنالیتیکس</a> <a class="btn btn2" href="/contacts">📒 مخاطبین</a></div>{body}</div>{js}</body></html>')


def contact_links(lead: Lead) -> str:
    links = []
    if lead.url: links.append(f'<a class="action" target="_blank" href="{h(lead.url)}">صفحه اصلی</a>')
    if lead.website: links.append(f'<a class="action btn2" target="_blank" href="{h(lead.website)}">وب‌سایت</a>')
    if lead.telegram: links.append(f'<a class="action btn2" target="_blank" href="{h(lead.telegram)}">تلگرام</a>')
    if lead.instagram: links.append(f'<a class="action btn2" target="_blank" href="{h(lead.instagram)}">اینستاگرام</a>')
    if lead.phone: links.append(f'<a class="action btn2" href="tel:{h(lead.phone)}">تماس</a>')
    msg = public_invite_message(lead.title, lead.category)
    links.append(f'<button class="action btn2 copy" data-text="{h(msg)}">کپی پیام عمومی</button>')
    return ''.join(links)


@router.get('/crm', response_class=HTMLResponse)
def crm_dashboard(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    stats = dashboard_more(db)
    body = f'''
    <div class="card"><h1>داشبورد CRM</h1><div class="grid3"><div class="card">لید امروز<br><b>{stats['today']}</b></div><div class="card">۷ روز اخیر<br><b>{stats['week']}</b></div><div class="card">پیگیری‌های سررسید<br><b>{stats['due']}</b></div></div></div>
    <div class="grid2"><div class="card"><h3>دسترسی سریع</h3><a class="btn" href="/crm/templates">قالب پیام</a><a class="btn" href="/crm/queue">صف جستجو</a><a class="btn" href="/crm/rules">Blacklist/Whitelist</a><a class="btn" href="/crm/api-status">وضعیت API</a><a class="btn" href="/crm/presets">Search Preset</a><a class="btn" href="/crm/settings">تنظیمات سایت</a><a class="btn" href="/crm/conversion">گزارش تبدیل</a></div><div class="card"><h3>منابع فعال</h3>{''.join(f'<span class="badge">{h(r.value)}</span>' for r in source_preferences(db) if r.active)}</div></div>
    '''
    return layout('داشبورد CRM', body, token)


@router.get('/leads/{lead_id}', response_class=HTMLResponse)
def lead_detail(lead_id: int, db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, 'مخاطب پیدا نشد')
    logs = list(db.scalars(select(ActivityLog).where(ActivityLog.lead_id == lead_id).order_by(desc(ActivityLog.created_at)).limit(80)).all())
    templates = list(db.scalars(select(MessageTemplate).where(MessageTemplate.active == True).order_by(MessageTemplate.id)).all())
    rec, why = recommended_contact(lead)
    valid_label, valid_reason = validity_label(db, lead)
    status_opts = ''.join(f'<option value="{h(k)}" {"selected" if lead.status==k else ""}>{h(v)}</option>' for k,v in PROFESSIONAL_STATUSES.items())
    template_blocks = ''.join(f'<div class="card"><b>{h(t.name)}</b><br><textarea style="width:100%">{h(render_template(t.body, lead))}</textarea><br><button class="btn2 copy" data-text="{h(render_template(t.body, lead))}">کپی</button></div>' for t in templates)
    log_blocks = ''.join(f'<div class="log"><b>{h(l.action)}</b> - {h(fmt_dt(l.created_at))}<br>{h(l.note)}</div>' for l in logs) or '<span class="muted">هنوز پیگیری ثبت نشده.</span>'
    body = f'''
    <div class="card"><h1>{h(lead.title)}</h1><p class="muted">شناسه #{lead.id} | افزوده شد: {h(fmt_dt(lead.first_seen))} | بروزرسانی: {h(fmt_dt(lead.last_seen))}</p><p>کانال پیشنهادی: <b>{h(rec)}</b> - {h(why)}</p><p>اعتبار مخاطب: <b>{h(valid_label)}</b> - {h(valid_reason)}</p>{contact_links(lead)}<br><form method="post" action="/crm/leads/{lead.id}/ai-message" style="display:inline"><button class="btn2">ساخت پیام اختصاصی AI</button></form><form method="post" action="/crm/leads/{lead.id}/extract" style="display:inline"><button class="btn2">استخراج تماس از صفحه</button></form><form method="post" action="/crm/leads/{lead.id}/send-main" style="display:inline"><button class="btn2">ارسال به سایت اصلی</button></form><hr><form method="post" action="/leads/{lead.id}/people"><input name="full_name" placeholder="نام فرد مرتبط؛ اگر خالی باشد از عنوان لید استفاده می‌شود" style="min-width:280px"><input name="role" placeholder="نقش" value="ادمین/مسئول"><input name="relationship" placeholder="رابطه با لید" value="ادمین"><button class="btn2">ساخت فرد مرتبط</button></form></div>
    <div class="grid2"><div class="card"><h3>ویرایش CRM</h3><form method="post" action="/crm/leads/{lead.id}/update"><select name="status">{status_opts}</select><input type="datetime-local" name="follow_up_at"><input name="preferred_contact" placeholder="کانال ترجیحی" value="{h(lead.preferred_contact)}"><textarea name="notes" placeholder="یادداشت">{h(lead.notes)}</textarea><button>ذخیره</button></form><form method="post" action="/crm/leads/{lead.id}/validate"><button class="btn2">اعتبارسنجی لینک</button> <span class="muted">{h(lead.link_status or '-')}</span></form></div><div class="card"><h3>ثبت فعالیت</h3><form method="post" action="/crm/leads/{lead.id}/activity"><select name="action"><option value="note">یادداشت</option><option value="messaged">پیام داده شد</option><option value="followup1">پیگیری اول</option><option value="followup2">پیگیری دوم</option><option value="call">تماس</option></select><textarea name="note" placeholder="شرح فعالیت"></textarea><button>ثبت</button></form></div></div>
    <div class="card"><h3>قالب‌های پیام</h3><div class="grid2">{template_blocks}</div></div>
    <div class="card"><h3>تاریخچه پیگیری</h3>{log_blocks}</div>
    '''
    return layout('جزئیات مخاطب', body, token)


@router.post('/crm/leads/{lead_id}/update')
def crm_update_lead(lead_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = '', status: Annotated[str, Form()] = 'new', follow_up_at: Annotated[str, Form()] = '', preferred_contact: Annotated[str, Form()] = '', notes: Annotated[str, Form()] = ''):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(404, 'مخاطب پیدا نشد')
    lead.status = status
    lead.follow_up_at = parse_local_datetime(follow_up_at)
    lead.preferred_contact = preferred_contact.strip() or None
    lead.notes = notes.strip() or None
    db.add(lead); db.commit()
    log_activity(db, lead_id, 'update', f'وضعیت: {PROFESSIONAL_STATUSES.get(status,status)}')
    return RedirectResponse(url=f'/leads/{lead_id}', status_code=303)


@router.post('/crm/leads/{lead_id}/activity')
def crm_activity(lead_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = '', action: Annotated[str, Form()] = 'note', note: Annotated[str, Form()] = ''):
    check_token(token)
    if not db.get(Lead, lead_id): raise HTTPException(404, 'مخاطب پیدا نشد')
    log_activity(db, lead_id, action, note)
    return RedirectResponse(url=f'/leads/{lead_id}', status_code=303)


@router.post('/crm/leads/{lead_id}/validate')
async def crm_validate(lead_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = ''):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(404, 'مخاطب پیدا نشد')
    status, note = await validate_lead_link(lead)
    lead.link_status = status
    lead.link_checked_at = datetime.utcnow()
    db.add(lead); db.commit()
    log_activity(db, lead_id, 'link_validation', note)
    return RedirectResponse(url=f'/leads/{lead_id}', status_code=303)


@router.get('/crm/templates', response_class=HTMLResponse)
def templates_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    templates = list(db.scalars(select(MessageTemplate).order_by(MessageTemplate.id)).all())
    rows = ''.join(f'<div class="log"><b>{h(t.name)}</b> <span class="badge">{h(t.category)}</span><br>{h(t.body)}</div>' for t in templates)
    body = f'<div class="card"><h1>قالب‌های پیام</h1><form class="" method="post" action="/crm/templates"><input name="name" placeholder="نام قالب"><input name="category" placeholder="دسته"><textarea name="body" placeholder="متن قالب"></textarea><button>افزودن</button></form></div><div class="card">{rows}</div>'
    return layout('قالب پیام', body, token)


@router.post('/crm/templates')
def templates_add(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', name: Annotated[str, Form()] = '', category: Annotated[str, Form()] = '', body: Annotated[str, Form()] = ''):
    check_token(token)
    if name.strip() and body.strip():
        db.add(MessageTemplate(name=name.strip(), category=category.strip() or None, body=body.strip()))
        db.commit()
    return RedirectResponse(url=f'/crm/templates', status_code=303)


@router.get('/crm/queue', response_class=HTMLResponse)
def queue_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    items = list(db.scalars(select(SearchQueueItem).order_by(desc(SearchQueueItem.created_at)).limit(100)).all())
    rows = ''.join(f'<div class="log">#{q.id} <b>{h(q.topic)}</b> - {h(q.city or "ایران")} - {h(q.source)} - آخرین اجرا: {h(fmt_dt(q.last_run_at))}</div>' for q in items) or '<span class="muted">صف خالی است.</span>'
    body = f'<div class="card"><h1>صف جستجو</h1><form method="post" action="/crm/queue/add"><input name="topic" placeholder="موضوع"><input name="city" placeholder="شهر"><select name="source"><option value="openrouter_web">OpenRouter مستقیم</option><option value="ai_tavily">AI + Tavily</option></select><button>افزودن</button></form><form method="post" action="/crm/queue/run-next"><button class="btn2">اجرای مورد بعدی</button></form></div><div class="card">{rows}</div>'
    return layout('صف جستجو', body, token)


@router.post('/crm/queue/add')
def queue_add(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', topic: Annotated[str, Form()] = '', city: Annotated[str, Form()] = '', source: Annotated[str, Form()] = 'openrouter_web'):
    check_token(token)
    if topic.strip():
        db.add(SearchQueueItem(topic=topic.strip(), city=city.strip() or None, source=source))
        db.commit()
    return RedirectResponse(url=f'/crm/queue', status_code=303)


@router.post('/crm/queue/run-next')
async def queue_run_next(db: Session = Depends(get_db), token: Annotated[str, Form()] = ''):
    check_token(token)
    item = db.scalar(select(SearchQueueItem).where(SearchQueueItem.active == True).order_by(asc(SearchQueueItem.last_run_at).nullsfirst(), asc(SearchQueueItem.id)))
    if not item:
        return RedirectResponse(url=f'/crm/queue', status_code=303)
    if item.source == 'ai_tavily':
        ok, msg = can_use_provider(db, 'tavily')
        if not ok: return RedirectResponse(url=f'/crm/queue', status_code=303)
        increment_usage(db, 'tavily')
        result = await run_ai_search(db, topic=item.topic, city=item.city, max_queries=4, results_per_query=4, min_score=60)
    else:
        ok, msg = can_use_provider(db, 'openrouter')
        if not ok: return RedirectResponse(url=f'/crm/queue', status_code=303)
        increment_usage(db, 'openrouter')
        result = await run_openrouter_web_search(db, topic=item.topic, city=item.city, max_results=8, min_score=60)
    item.last_run_at = datetime.utcnow(); db.add(item); db.commit()
    return RedirectResponse(url=f'/?new_ids={quote_plus(",".join(str(x) for x in result.get("saved_ids",[]) or []))}&sort=newest#queue', status_code=303)


@router.get('/crm/rules', response_class=HTMLResponse)
def rules_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    rules = list(db.scalars(select(SearchRule).order_by(SearchRule.rule_type, SearchRule.id)).all())
    rows = ''.join(f'<span class="badge">{h(r.rule_type)}: {h(r.value)}</span>' for r in rules)
    body = f'<div class="card"><h1>قوانین Blacklist / Whitelist / منابع</h1><form method="post" action="/crm/rules/add"><select name="rule_type"><option value="blacklist">Blacklist</option><option value="whitelist">Whitelist</option><option value="source">منبع سرچ</option></select><input name="value" placeholder="کلمه یا دامنه"><button>افزودن</button></form></div><div class="card">{rows}</div>'
    return layout('قوانین', body, token)


@router.post('/crm/rules/add')
def rules_add(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', rule_type: Annotated[str, Form()] = 'blacklist', value: Annotated[str, Form()] = ''):
    check_token(token)
    if value.strip():
        db.add(SearchRule(rule_type=rule_type, value=value.strip()))
        db.commit()
    return RedirectResponse(url=f'/crm/rules', status_code=303)


@router.get('/crm/api-status', response_class=HTMLResponse)
def api_status_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    settings = get_settings()
    body = f'''<div class="card"><h1>وضعیت APIها و سقف مصرف</h1><div class="grid2"><div class="card">OpenRouter Key: {bool(settings.openrouter_api_key)}<br>مصرف امروز: {get_usage(db,'openrouter')}/{daily_limit('openrouter')}</div><div class="card">Tavily Key: {bool(settings.tavily_api_key)}<br>مصرف امروز: {get_usage(db,'tavily')}/{daily_limit('tavily')}</div><div class="card">Groq Key: {bool(settings.groq_api_key)}</div><div class="card">HuggingFace Key: {bool(settings.huggingface_api_key)}</div></div></div>'''
    return layout('وضعیت API', body, token)


@router.get('/api/status')
def api_status_json(token: str = Query(''), db: Session = Depends(get_db)):
    check_token(token)
    settings = get_settings()
    return {'database': 'ok', 'openrouter_key': bool(settings.openrouter_api_key), 'tavily_key': bool(settings.tavily_api_key), 'groq_key': bool(settings.groq_api_key), 'huggingface_key': bool(settings.huggingface_api_key), 'usage': {'openrouter': f'{get_usage(db,"openrouter")}/{daily_limit("openrouter")}', 'tavily': f'{get_usage(db,"tavily")}/{daily_limit("tavily")}'}}


@router.get('/crm/settings', response_class=HTMLResponse)
def settings_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    body = f'''
    <div class="card"><h1>تنظیمات پروژه</h1>
      <form method="post" action="/crm/settings">
        <label>لینک ثبت آگهی سایت شما<br><input name="site_link" style="min-width:360px" value="{h(get_setting(db, 'site_link', 'YOUR_SITE_LINK'))}"></label><br>
        <label>API URL سایت اصلی<br><input name="main_site_api_url" style="min-width:360px" value="{h(get_setting(db, 'main_site_api_url'))}"></label><br>
        <label>API Key سایت اصلی<br><input name="main_site_api_key" style="min-width:360px" value="{h(get_setting(db, 'main_site_api_key'))}"></label><br>
        <button>ذخیره تنظیمات</button>
      </form><p class="muted">در قالب پیام‌ها، YOUR_SITE_LINK با لینک سایت شما جایگزین می‌شود.</p>
    </div>'''
    return layout('تنظیمات پروژه', body, token)


@router.post('/crm/settings')
def settings_save(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', site_link: Annotated[str, Form()] = '', main_site_api_url: Annotated[str, Form()] = '', main_site_api_key: Annotated[str, Form()] = ''):
    check_token(token)
    set_setting(db, 'site_link', site_link.strip())
    set_setting(db, 'main_site_api_url', main_site_api_url.strip())
    set_setting(db, 'main_site_api_key', main_site_api_key.strip())
    return RedirectResponse(url=f'/crm/settings', status_code=303)


@router.get('/crm/presets', response_class=HTMLResponse)
def presets_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    presets = list(db.scalars(select(SearchPreset).order_by(SearchPreset.id)).all())
    rows = ''.join(f'<div class="log"><b>{h(p.name)}</b> <span class="badge">{h(p.source)}</span> <span class="badge">{h(p.city or "ایران")}</span><p class="muted">{h(p.description)}</p><pre>{h(p.queries)}</pre><form method="post" action="/crm/presets/{p.id}/run"><label><input type="checkbox" name="force" value="1"> اجرای مجدد حتی اگر امروز اجرا شده</label><button>اجرای پکیج</button></form></div>' for p in presets)
    body = f'''<div class="card"><h1>Search Preset حرفه‌ای</h1><form method="post" action="/crm/presets"><input name="name" placeholder="نام پکیج"><input name="city" placeholder="شهر"><select name="source"><option value="openrouter_web">OpenRouter مستقیم</option><option value="ai_tavily">AI + Tavily</option></select><textarea name="queries" placeholder="هر query در یک خط"></textarea><button>افزودن Preset</button></form></div><div class="card">{rows}</div>'''
    return layout('Search Preset', body, token)


@router.post('/crm/presets')
def presets_add(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', name: Annotated[str, Form()] = '', city: Annotated[str, Form()] = '', source: Annotated[str, Form()] = 'openrouter_web', queries: Annotated[str, Form()] = ''):
    check_token(token)
    if name.strip() and queries.strip():
        db.add(SearchPreset(name=name.strip(), city=city.strip() or None, source=source, queries=queries.strip()))
        db.commit()
    return RedirectResponse(url=f'/crm/presets', status_code=303)


@router.post('/crm/presets/{preset_id}/run')
async def presets_run(preset_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = '', force: Annotated[str | None, Form()] = None):
    check_token(token)
    preset = db.get(SearchPreset, preset_id)
    if not preset:
        raise HTTPException(404, 'Preset پیدا نشد')
    saved_ids = []
    skipped = 0
    for query in [q.strip() for q in preset.queries.splitlines() if q.strip()]:
        source_key = 'openrouter_web_ai' if preset.source == 'openrouter_web' else 'ai_search'
        if not force and search_recently_run(db, source_key, (query + ' | ' + (preset.city or '')), hours=24):
            skipped += 1
            continue
        if preset.source == 'ai_tavily':
            ok, msg = can_use_provider(db, 'tavily')
            if not ok: break
            increment_usage(db, 'tavily')
            result = await run_ai_search(db, topic=query, city=preset.city, max_queries=3, results_per_query=4, min_score=60)
        else:
            ok, msg = can_use_provider(db, 'openrouter')
            if not ok: break
            increment_usage(db, 'openrouter')
            result = await run_openrouter_web_search(db, topic=query, city=preset.city, max_results=8, min_score=60)
        saved_ids.extend(result.get('saved_ids', []) or [])
    preset.last_run_at = datetime.utcnow()
    db.add(preset); db.commit()
    message = f'Preset اجرا شد. جدید: {len(saved_ids)} | سرچ‌های تکراری رد شده: {skipped}'
    return RedirectResponse(url=f'/?ai_msg={quote_plus(message)}&new_ids={quote_plus(",".join(str(x) for x in saved_ids))}&sort=newest', status_code=303)


@router.post('/crm/leads/{lead_id}/ai-message')
async def ai_message(lead_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = ''):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(404, 'مخاطب پیدا نشد')
    prompt = f'برای این مخاطب یک پیام دعوت کوتاه، محترمانه و فارسی بنویس. عنوان: {lead.title} دسته: {lead.category} شهر: {lead.city}. لینک سایت: {get_setting(db, "site_link", "YOUR_SITE_LINK")}. فقط JSON بده: {{"message":"..."}}'
    try:
        data, used = await chat_json([{'role':'system','content':'فقط JSON معتبر بده.'},{'role':'user','content':prompt}], max_tokens=500)
        msg = data.get('message') or ''
    except Exception:
        msg = render_site_link(db, public_invite_message(lead.title, lead.category))
    log_activity(db, lead.id, 'ai_message', msg)
    return RedirectResponse(url=f'/leads/{lead.id}', status_code=303)


@router.post('/crm/leads/{lead_id}/extract')
async def extract_contact_route(lead_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = ''):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(404, 'مخاطب پیدا نشد')
    found = {}
    for url in [lead.website, lead.url]:
        if not url: continue
        try:
            found = await extract_contacts_from_url(url)
            if found: break
        except Exception:
            continue
    if found.get('phone') and not lead.phone: lead.phone = found['phone']
    if found.get('instagram') and not lead.instagram: lead.instagram = found['instagram']
    if found.get('telegram') and not lead.telegram: lead.telegram = found['telegram']
    if found.get('contact_page') and not lead.website: lead.website = found['contact_page']
    db.add(lead); db.commit()
    log_activity(db, lead.id, 'extract_contact', str(found) if found else 'چیزی پیدا نشد')
    return RedirectResponse(url=f'/leads/{lead.id}', status_code=303)


@router.post('/crm/leads/{lead_id}/send-main')
async def send_to_main_site(lead_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = ''):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(404, 'مخاطب پیدا نشد')
    api_url = get_setting(db, 'main_site_api_url')
    api_key = get_setting(db, 'main_site_api_key')
    if not api_url:
        log_activity(db, lead.id, 'send_main_site', 'API URL سایت اصلی تنظیم نشده است')
        return RedirectResponse(url=f'/leads/{lead.id}', status_code=303)
    payload = {'id': lead.id, 'title': lead.title, 'url': lead.url, 'phone': lead.phone, 'website': lead.website, 'instagram': lead.instagram, 'telegram': lead.telegram, 'category': lead.category, 'city': lead.city}
    try:
        headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(api_url, json=payload, headers=headers)
        log_activity(db, lead.id, 'send_main_site', f'ارسال شد: HTTP {r.status_code} {r.text[:150]}')
    except Exception as exc:
        log_activity(db, lead.id, 'send_main_site', f'خطا: {str(exc)[:200]}')
    return RedirectResponse(url=f'/leads/{lead.id}', status_code=303)


@router.get('/crm/conversion', response_class=HTMLResponse)
def conversion_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    funnel = conversion_funnel(db)
    report = source_quality_report(db)
    rows = ''.join(f'<tr><td>{h(r["source"])}</td><td>{r["total"]}</td><td>{r["messaged"]}</td><td>{r["replied"]}</td><td>{r["registered"]}</td><td>{r["reply_rate"]}%</td><td>{r["conversion_rate"]}%</td></tr>' for r in report)
    body = f'''<div class="card"><h1>گزارش تبدیل</h1><div class="grid3"><div class="card">کل مخاطب<br><b>{funnel['total']}</b></div><div class="card">پیام داده شده<br><b>{funnel['messaged']}</b></div><div class="card">جواب داده<br><b>{funnel['replied']}</b></div><div class="card">ثبت‌نام کرده<br><b>{funnel['registered']}</b></div></div></div><div class="card"><h3>کیفیت منبع‌ها</h3><table style="width:100%"><thead><tr><th>منبع</th><th>کل</th><th>پیام</th><th>جواب</th><th>ثبت‌نام</th><th>نرخ جواب</th><th>نرخ تبدیل</th></tr></thead><tbody>{rows}</tbody></table></div>'''
    return layout('گزارش تبدیل', body, token)
