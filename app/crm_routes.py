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
from app.config import get_settings
from app.crm import (
    PROFESSIONAL_STATUSES, can_use_provider, daily_limit, dashboard_more, get_usage,
    increment_usage, log_activity, recommended_contact, render_template, source_preferences,
    validate_lead_link,
)
from app.db.models import ActivityLog, Lead, MessageTemplate, SearchQueueItem, SearchRule
from app.db.session import get_db
from app.utils import public_invite_message

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')


def h(v) -> str:
    import html
    return html.escape('' if v is None else str(v), quote=True)


def check_token(token: str | None = None):
    settings = get_settings()
    if settings.admin_token and token != settings.admin_token:
        raise HTTPException(status_code=401, detail='رمز مدیریت اشتباه است')


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
    css = '''<style>body{font-family:Tahoma,Arial;background:#f4f6fb;direction:rtl;color:#101828;margin:0}.wrap{max-width:1180px;margin:auto;padding:18px}.card{background:#fff;border:1px solid #e6e9f2;border-radius:16px;padding:16px;margin:12px 0;box-shadow:0 4px 18px rgba(20,30,60,.04)}a{color:#214ec2;text-decoration:none}.btn,.action,button{display:inline-block;background:#1f55d5;color:#fff;border:0;border-radius:10px;padding:9px 12px;margin:3px;cursor:pointer}.btn2{background:#eef2ff;color:#173c9b;border:1px solid #d6e0ff}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.muted{color:#667085;font-size:13px;line-height:1.8}.badge{display:inline-block;background:#eef2ff;color:#2546a6;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px}input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:10px;padding:9px;margin:3px;background:#fff}textarea{min-height:80px}.log{border-right:3px solid #1f55d5;background:#f8fafc;border-radius:10px;padding:10px;margin:8px 0}.danger{background:#fff2f2;color:#b42318;border:1px solid #fecdca}@media(max-width:800px){.grid2,.grid3{grid-template-columns:1fr}.wrap{padding:10px}}</style>'''
    js = '''<script>document.addEventListener('click',async e=>{if(e.target.classList.contains('copy')){const t=e.target.dataset.text||'';try{await navigator.clipboard.writeText(t);e.target.textContent='کپی شد ✅'}catch(_){alert(t)}setTimeout(()=>e.target.textContent='کپی',1200)}});</script>'''
    return HTMLResponse(f'<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title>{css}</head><body><div class="wrap"><a class="btn btn2" href="/?token={h(token)}">بازگشت به بانک اطلاعاتی</a>{body}</div>{js}</body></html>')


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
    <div class="grid2"><div class="card"><h3>دسترسی سریع</h3><a class="btn" href="/crm/templates?token={h(token)}">قالب پیام</a><a class="btn" href="/crm/queue?token={h(token)}">صف جستجو</a><a class="btn" href="/crm/rules?token={h(token)}">Blacklist/Whitelist</a><a class="btn" href="/crm/api-status?token={h(token)}">وضعیت API</a></div><div class="card"><h3>منابع فعال</h3>{''.join(f'<span class="badge">{h(r.value)}</span>' for r in source_preferences(db) if r.active)}</div></div>
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
    status_opts = ''.join(f'<option value="{h(k)}" {"selected" if lead.status==k else ""}>{h(v)}</option>' for k,v in PROFESSIONAL_STATUSES.items())
    template_blocks = ''.join(f'<div class="card"><b>{h(t.name)}</b><br><textarea style="width:100%">{h(render_template(t.body, lead))}</textarea><br><button class="btn2 copy" data-text="{h(render_template(t.body, lead))}">کپی</button></div>' for t in templates)
    log_blocks = ''.join(f'<div class="log"><b>{h(l.action)}</b> - {h(fmt_dt(l.created_at))}<br>{h(l.note)}</div>' for l in logs) or '<span class="muted">هنوز پیگیری ثبت نشده.</span>'
    body = f'''
    <div class="card"><h1>{h(lead.title)}</h1><p class="muted">شناسه #{lead.id} | افزوده شد: {h(fmt_dt(lead.first_seen))} | بروزرسانی: {h(fmt_dt(lead.last_seen))}</p><p>کانال پیشنهادی: <b>{h(rec)}</b> - {h(why)}</p>{contact_links(lead)}</div>
    <div class="grid2"><div class="card"><h3>ویرایش CRM</h3><form method="post" action="/crm/leads/{lead.id}/update"><input type="hidden" name="token" value="{h(token)}"><select name="status">{status_opts}</select><input type="datetime-local" name="follow_up_at"><input name="preferred_contact" placeholder="کانال ترجیحی" value="{h(lead.preferred_contact)}"><textarea name="notes" placeholder="یادداشت">{h(lead.notes)}</textarea><button>ذخیره</button></form><form method="post" action="/crm/leads/{lead.id}/validate"><input type="hidden" name="token" value="{h(token)}"><button class="btn2">اعتبارسنجی لینک</button> <span class="muted">{h(lead.link_status or '-')}</span></form></div><div class="card"><h3>ثبت فعالیت</h3><form method="post" action="/crm/leads/{lead.id}/activity"><input type="hidden" name="token" value="{h(token)}"><select name="action"><option value="note">یادداشت</option><option value="messaged">پیام داده شد</option><option value="followup1">پیگیری اول</option><option value="followup2">پیگیری دوم</option><option value="call">تماس</option></select><textarea name="note" placeholder="شرح فعالیت"></textarea><button>ثبت</button></form></div></div>
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
    return RedirectResponse(url=f'/leads/{lead_id}?token={quote_plus(token)}', status_code=303)


@router.post('/crm/leads/{lead_id}/activity')
def crm_activity(lead_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = '', action: Annotated[str, Form()] = 'note', note: Annotated[str, Form()] = ''):
    check_token(token)
    if not db.get(Lead, lead_id): raise HTTPException(404, 'مخاطب پیدا نشد')
    log_activity(db, lead_id, action, note)
    return RedirectResponse(url=f'/leads/{lead_id}?token={quote_plus(token)}', status_code=303)


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
    return RedirectResponse(url=f'/leads/{lead_id}?token={quote_plus(token)}', status_code=303)


@router.get('/crm/templates', response_class=HTMLResponse)
def templates_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    templates = list(db.scalars(select(MessageTemplate).order_by(MessageTemplate.id)).all())
    rows = ''.join(f'<div class="log"><b>{h(t.name)}</b> <span class="badge">{h(t.category)}</span><br>{h(t.body)}</div>' for t in templates)
    body = f'<div class="card"><h1>قالب‌های پیام</h1><form class="" method="post" action="/crm/templates"><input type="hidden" name="token" value="{h(token)}"><input name="name" placeholder="نام قالب"><input name="category" placeholder="دسته"><textarea name="body" placeholder="متن قالب"></textarea><button>افزودن</button></form></div><div class="card">{rows}</div>'
    return layout('قالب پیام', body, token)


@router.post('/crm/templates')
def templates_add(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', name: Annotated[str, Form()] = '', category: Annotated[str, Form()] = '', body: Annotated[str, Form()] = ''):
    check_token(token)
    if name.strip() and body.strip():
        db.add(MessageTemplate(name=name.strip(), category=category.strip() or None, body=body.strip()))
        db.commit()
    return RedirectResponse(url=f'/crm/templates?token={quote_plus(token)}', status_code=303)


@router.get('/crm/queue', response_class=HTMLResponse)
def queue_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    items = list(db.scalars(select(SearchQueueItem).order_by(desc(SearchQueueItem.created_at)).limit(100)).all())
    rows = ''.join(f'<div class="log">#{q.id} <b>{h(q.topic)}</b> - {h(q.city or "ایران")} - {h(q.source)} - آخرین اجرا: {h(fmt_dt(q.last_run_at))}</div>' for q in items) or '<span class="muted">صف خالی است.</span>'
    body = f'<div class="card"><h1>صف جستجو</h1><form method="post" action="/crm/queue/add"><input type="hidden" name="token" value="{h(token)}"><input name="topic" placeholder="موضوع"><input name="city" placeholder="شهر"><select name="source"><option value="openrouter_web">OpenRouter مستقیم</option><option value="ai_tavily">AI + Tavily</option></select><button>افزودن</button></form><form method="post" action="/crm/queue/run-next"><input type="hidden" name="token" value="{h(token)}"><button class="btn2">اجرای مورد بعدی</button></form></div><div class="card">{rows}</div>'
    return layout('صف جستجو', body, token)


@router.post('/crm/queue/add')
def queue_add(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', topic: Annotated[str, Form()] = '', city: Annotated[str, Form()] = '', source: Annotated[str, Form()] = 'openrouter_web'):
    check_token(token)
    if topic.strip():
        db.add(SearchQueueItem(topic=topic.strip(), city=city.strip() or None, source=source))
        db.commit()
    return RedirectResponse(url=f'/crm/queue?token={quote_plus(token)}', status_code=303)


@router.post('/crm/queue/run-next')
async def queue_run_next(db: Session = Depends(get_db), token: Annotated[str, Form()] = ''):
    check_token(token)
    item = db.scalar(select(SearchQueueItem).where(SearchQueueItem.active == True).order_by(asc(SearchQueueItem.last_run_at).nullsfirst(), asc(SearchQueueItem.id)))
    if not item:
        return RedirectResponse(url=f'/crm/queue?token={quote_plus(token)}', status_code=303)
    if item.source == 'ai_tavily':
        ok, msg = can_use_provider(db, 'tavily')
        if not ok: return RedirectResponse(url=f'/crm/queue?token={quote_plus(token)}', status_code=303)
        increment_usage(db, 'tavily')
        result = await run_ai_search(db, topic=item.topic, city=item.city, max_queries=4, results_per_query=4, min_score=60)
    else:
        ok, msg = can_use_provider(db, 'openrouter')
        if not ok: return RedirectResponse(url=f'/crm/queue?token={quote_plus(token)}', status_code=303)
        increment_usage(db, 'openrouter')
        result = await run_openrouter_web_search(db, topic=item.topic, city=item.city, max_results=8, min_score=60)
    item.last_run_at = datetime.utcnow(); db.add(item); db.commit()
    return RedirectResponse(url=f'/?token={quote_plus(token)}&new_ids={quote_plus(",".join(str(x) for x in result.get("saved_ids",[]) or []))}&sort=newest#queue', status_code=303)


@router.get('/crm/rules', response_class=HTMLResponse)
def rules_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    rules = list(db.scalars(select(SearchRule).order_by(SearchRule.rule_type, SearchRule.id)).all())
    rows = ''.join(f'<span class="badge">{h(r.rule_type)}: {h(r.value)}</span>' for r in rules)
    body = f'<div class="card"><h1>قوانین Blacklist / Whitelist / منابع</h1><form method="post" action="/crm/rules/add"><input type="hidden" name="token" value="{h(token)}"><select name="rule_type"><option value="blacklist">Blacklist</option><option value="whitelist">Whitelist</option><option value="source">منبع سرچ</option></select><input name="value" placeholder="کلمه یا دامنه"><button>افزودن</button></form></div><div class="card">{rows}</div>'
    return layout('قوانین', body, token)


@router.post('/crm/rules/add')
def rules_add(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', rule_type: Annotated[str, Form()] = 'blacklist', value: Annotated[str, Form()] = ''):
    check_token(token)
    if value.strip():
        db.add(SearchRule(rule_type=rule_type, value=value.strip()))
        db.commit()
    return RedirectResponse(url=f'/crm/rules?token={quote_plus(token)}', status_code=303)


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
