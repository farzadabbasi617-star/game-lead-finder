from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crm import get_setting, render_template
from app.db.models import Campaign, CampaignMember, Lead, MessageTemplate, Person
from app.db.session import get_db
from app.utils import public_invite_message

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')

CAMPAIGN_STATUS = {
    'active': 'فعال',
    'paused': 'متوقف',
    'finished': 'تمام‌شده',
}

MEMBER_STATUS = {
    'queued': 'در صف',
    'ready': 'آماده ارسال',
    'sent': 'پیام ارسال شد',
    'replied': 'جواب داد',
    'registered': 'ثبت‌نام کرد',
    'no_response': 'بدون پاسخ',
    'irrelevant': 'نامرتبط',
    'skipped': 'رد شد',
}


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


def layout(title: str, body: str, token: str = '') -> HTMLResponse:
    css = '''<style>:root{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb;--shadow:0 16px 40px rgba(16,24,40,.08);--success:#12b76a;--warn:#f79009}*{box-sizing:border-box}body{font-family:Tahoma,Arial,sans-serif;background:radial-gradient(circle at top right,rgba(37,99,235,.10),transparent 34%),linear-gradient(180deg,#f8fbff,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}.wrap{max-width:1320px;margin:auto;padding:22px}a{text-decoration:none;color:var(--primary)}.hero{background:linear-gradient(135deg,#111827,#1e3a8a 58%,#2563eb);color:white;border-radius:26px;padding:20px;box-shadow:var(--shadow);margin-bottom:16px}.hero h1{margin:0}.hero .muted{color:#dbeafe}.card{background:rgba(255,255,255,.94);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045)}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.muted{color:var(--muted);font-size:13px;line-height:1.8}.btn,.action,button{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer}.btn2{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe}.badge{display:inline-flex;background:#eef2ff;color:#2546a6;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;font-weight:600}.badge.green{background:#ecfdf3;color:#027a48}.badge.orange{background:#fffaeb;color:#b54708}input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}textarea{min-height:90px}.log{border-right:4px solid var(--primary);padding:11px;margin:10px 0;background:#f8fafc;border-radius:12px}table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}th,td{padding:12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top}th{background:#eef4ff}.campaign-card{border:1px solid #dbeafe;background:linear-gradient(180deg,#fff,#f8fbff);border-radius:18px;padding:14px;margin:10px 0}.message{background:#f8fafc;border:1px dashed #98a2b3;border-radius:12px;padding:10px;white-space:pre-wrap}@media(max-width:850px){.wrap{padding:12px}.grid2,.grid3{grid-template-columns:1fr}table{display:block;overflow-x:auto}}</style>'''
    js = '''<script>document.addEventListener('click',async e=>{if(e.target.classList.contains('copy')){const t=e.target.dataset.text||'';try{await navigator.clipboard.writeText(t);e.target.textContent='کپی شد ✅'}catch(_){alert(t)}setTimeout(()=>e.target.textContent='کپی پیام',1200)}});</script>'''
    return HTMLResponse(f'<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title>{css}</head><body><div class="wrap"><div class="hero"><h1>{h(title)}</h1><div class="muted">مدیریت کمپین جذب فروشنده، پیام دستی، گزارش و پیگیری</div><a class="btn btn2" href="/?token={h(token)}">بانک لیدها</a> <a class="btn btn2" href="/campaigns?token={h(token)}">کمپین‌ها</a> <a class="btn btn2" href="/crm?token={h(token)}">CRM</a></div>{body}</div>{js}</body></html>')


def status_options(selected: str, mapping: dict[str, str]) -> str:
    return ''.join(f'<option value="{h(k)}" {"selected" if selected==k else ""}>{h(v)}</option>' for k, v in mapping.items())


def entity_name(db: Session, entity_type: str, entity_id: int) -> str:
    if entity_type == 'person':
        p = db.get(Person, entity_id)
        return p.full_name if p else f'Person #{entity_id}'
    l = db.get(Lead, entity_id)
    return l.title if l else f'Lead #{entity_id}'


def entity_contact_html(db: Session, entity_type: str, entity_id: int) -> str:
    if entity_type == 'person':
        p = db.get(Person, entity_id)
        if not p: return '-'
        links = []
        if p.telegram: links.append(f'<a class="btn2" target="_blank" href="{h(p.telegram if str(p.telegram).startswith("http") else "https://t.me/" + str(p.telegram).lstrip("@"))}">تلگرام</a>')
        if p.instagram: links.append(f'<a class="btn2" target="_blank" href="{h(p.instagram if str(p.instagram).startswith("http") else "https://instagram.com/" + str(p.instagram).lstrip("@"))}">اینستاگرام</a>')
        if p.phone: links.append(f'<a class="btn2" href="tel:{h(p.phone)}">تماس</a>')
        return ''.join(links) or '-'
    l = db.get(Lead, entity_id)
    if not l: return '-'
    links = []
    if l.url: links.append(f'<a class="btn2" target="_blank" href="{h(l.url)}">صفحه</a>')
    if l.telegram: links.append(f'<a class="btn2" target="_blank" href="{h(l.telegram)}">تلگرام</a>')
    if l.instagram: links.append(f'<a class="btn2" target="_blank" href="{h(l.instagram)}">اینستاگرام</a>')
    if l.phone: links.append(f'<a class="btn2" href="tel:{h(l.phone)}">تماس</a>')
    return ''.join(links) or '-'


def build_message(db: Session, campaign: Campaign, entity_type: str, entity_id: int, variant: str = 'A') -> str:
    template_id = campaign.message_template_b_id if variant == 'B' and campaign.message_template_b_id else campaign.message_template_id
    template = db.get(MessageTemplate, template_id) if template_id else None
    if entity_type == 'person':
        person = db.get(Person, entity_id)
        base = template.body if template else 'سلام {title} عزیز، وقتتون بخیر. ما یک پلتفرم تخصصی گیمینگ داریم و خوشحال می‌شیم همکاری کنیم: YOUR_SITE_LINK'
        text = (base or '').replace('{title}', person.full_name if person else '').replace('{category}', person.role if person and person.role else 'گیمینگ').replace('{city}', person.city if person and person.city else '')
    else:
        lead = db.get(Lead, entity_id)
        text = render_template(template.body, lead) if template and lead else public_invite_message(lead.title if lead else '', lead.category if lead else '')
    return text.replace('YOUR_SITE_LINK', campaign.site_link or get_setting(db, 'site_link', 'YOUR_SITE_LINK'))


def invite_link(campaign: Campaign, entity_type: str, entity_id: int) -> str:
    base = campaign.site_link or ''
    if not base:
        return ''
    sep = '&' if '?' in base else '?'
    return f'{base}{sep}utm_campaign=campaign_{campaign.id}&{entity_type}={entity_id}'


def campaign_stats(db: Session, campaign_id: int) -> dict:
    total = db.scalar(select(func.count(CampaignMember.id)).where(CampaignMember.campaign_id == campaign_id)) or 0
    def c(status): return db.scalar(select(func.count(CampaignMember.id)).where(CampaignMember.campaign_id == campaign_id, CampaignMember.status == status)) or 0
    sent = c('sent') + c('replied') + c('registered') + c('no_response')
    replied = c('replied') + c('registered')
    registered = c('registered')
    return {'total': total, 'queued': c('queued'), 'sent': sent, 'replied': replied, 'registered': registered, 'reply_rate': round((replied/sent)*100,1) if sent else 0, 'conversion_rate': round((registered/sent)*100,1) if sent else 0}


@router.get('/campaigns', response_class=HTMLResponse)
def campaigns_index(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    campaigns = list(db.scalars(select(Campaign).order_by(desc(Campaign.created_at)).limit(100)).all())
    templates = list(db.scalars(select(MessageTemplate).where(MessageTemplate.active == True).order_by(MessageTemplate.id)).all())
    tpl_opts = '<option value="">بدون قالب</option>' + ''.join(f'<option value="{t.id}">{h(t.name)}</option>' for t in templates)
    cards = ''
    for c in campaigns:
        st = campaign_stats(db, c.id)
        cards += f'<div class="campaign-card"><h3>{h(c.name)}</h3><p class="muted">{h(c.goal)}</p><span class="badge">{h(CAMPAIGN_STATUS.get(c.status,c.status))}</span><span class="badge green">کل: {st["total"]}</span><span class="badge orange">ارسال: {st["sent"]}</span><span class="badge">ثبت‌نام: {st["registered"]}</span><br><a class="btn" href="/campaigns/{c.id}?token={h(token)}">ورود به کمپین</a></div>'
    body = f'''
    <div class="card"><h2>ساخت کمپین جدید</h2>
      <form method="post" action="/campaigns">
        <input type="hidden" name="token" value="{h(token)}">
        <input name="name" placeholder="نام کمپین" required><input name="goal" placeholder="هدف کمپین" style="min-width:260px">
        <select name="target_type"><option value="lead">فقط لیدها</option><option value="person">فقط افراد</option><option value="both">لید + فرد</option></select>
        <input name="target_source" placeholder="منبع؛ مثلا instagram"><input name="target_category" placeholder="دسته"><input name="target_city" placeholder="شهر"><input name="target_status" placeholder="وضعیت">
        <select name="message_template_id">{tpl_opts}</select><select name="message_template_b_id">{tpl_opts}</select>
        <input name="daily_batch_size" type="number" value="30" min="1" max="500"><button>ساخت کمپین</button>
      </form>
    </div>
    <div class="card"><h2>کمپین‌ها</h2>{cards or '<span class="muted">هنوز کمپینی ساخته نشده</span>'}</div>'''
    return layout('کمپین‌های تبلیغاتی', body, token)


@router.post('/campaigns')
def campaigns_create(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', name: Annotated[str, Form()] = '', goal: Annotated[str, Form()] = '', target_type: Annotated[str, Form()] = 'lead', target_source: Annotated[str, Form()] = '', target_category: Annotated[str, Form()] = '', target_status: Annotated[str, Form()] = '', target_city: Annotated[str, Form()] = '', message_template_id: Annotated[str, Form()] = '', message_template_b_id: Annotated[str, Form()] = '', daily_batch_size: Annotated[int, Form()] = 30):
    check_token(token)
    camp = Campaign(name=name.strip(), goal=goal.strip() or None, target_type=target_type, target_source=target_source.strip() or None, target_category=target_category.strip() or None, target_status=target_status.strip() or None, target_city=target_city.strip() or None, message_template_id=int(message_template_id) if message_template_id else None, message_template_b_id=int(message_template_b_id) if message_template_b_id else None, site_link=get_setting(db, 'site_link', 'YOUR_SITE_LINK'), daily_batch_size=daily_batch_size)
    db.add(camp); db.commit(); db.refresh(camp)
    return RedirectResponse(url=f'/campaigns/{camp.id}?token={quote_plus(token)}', status_code=303)


def target_leads(db: Session, c: Campaign):
    stmt = select(Lead)
    if c.target_source: stmt = stmt.where(Lead.source.ilike(f'%{c.target_source}%'))
    if c.target_category: stmt = stmt.where(Lead.category.ilike(f'%{c.target_category}%'))
    if c.target_city: stmt = stmt.where(Lead.city.ilike(f'%{c.target_city}%'))
    if c.target_status: stmt = stmt.where(Lead.status == c.target_status)
    return list(db.scalars(stmt.limit(1000)).all())


def target_people(db: Session, c: Campaign):
    stmt = select(Person)
    if c.target_source: stmt = stmt.where(Person.source.ilike(f'%{c.target_source}%'))
    if c.target_city: stmt = stmt.where(Person.city.ilike(f'%{c.target_city}%'))
    if c.target_status: stmt = stmt.where(Person.status == c.target_status)
    return list(db.scalars(stmt.limit(1000)).all())


def recently_contacted(db: Session, entity_type: str, entity_id: int, days: int = 7) -> bool:
    since = datetime.utcnow() - timedelta(days=days)
    return bool(db.scalar(select(CampaignMember).where(CampaignMember.entity_type == entity_type, CampaignMember.entity_id == entity_id, CampaignMember.sent_at != None, CampaignMember.sent_at >= since)))


@router.post('/campaigns/{campaign_id}/populate')
def campaigns_populate(campaign_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = '', skip_recent: Annotated[str | None, Form()] = '1'):
    check_token(token)
    c = db.get(Campaign, campaign_id)
    if not c: raise HTTPException(404, 'کمپین پیدا نشد')
    added = skipped = 0
    entities = []
    if c.target_type in {'lead','both'}: entities += [('lead', l.id) for l in target_leads(db, c)]
    if c.target_type in {'person','both'}: entities += [('person', p.id) for p in target_people(db, c)]
    for idx, (etype, eid) in enumerate(entities):
        if skip_recent and recently_contacted(db, etype, eid, days=7):
            skipped += 1; continue
        exists = db.scalar(select(CampaignMember).where(CampaignMember.campaign_id == c.id, CampaignMember.entity_type == etype, CampaignMember.entity_id == eid))
        if exists: skipped += 1; continue
        variant = 'B' if c.message_template_b_id and idx % 2 else 'A'
        msg = build_message(db, c, etype, eid, variant)
        member = CampaignMember(campaign_id=c.id, entity_type=etype, entity_id=eid, message_variant=variant, status='queued', message_text=msg, invite_link=invite_link(c, etype, eid))
        db.add(member); added += 1
    db.commit()
    return RedirectResponse(url=f'/campaigns/{c.id}?token={quote_plus(token)}&msg={quote_plus(f"{added} عضو اضافه شد، {skipped} مورد رد شد")}', status_code=303)


@router.get('/campaigns/{campaign_id}', response_class=HTMLResponse)
def campaign_detail(campaign_id: int, db: Session = Depends(get_db), token: str = Query(''), msg: str = ''):
    check_token(token)
    c = db.get(Campaign, campaign_id)
    if not c: raise HTTPException(404, 'کمپین پیدا نشد')
    st = campaign_stats(db, c.id)
    batch = list(db.scalars(select(CampaignMember).where(CampaignMember.campaign_id == c.id, CampaignMember.status.in_(['queued','ready'])).order_by(CampaignMember.id).limit(c.daily_batch_size)).all())
    all_members = list(db.scalars(select(CampaignMember).where(CampaignMember.campaign_id == c.id).order_by(desc(CampaignMember.created_at)).limit(300)).all())
    rows = ''
    for m in all_members:
        name = entity_name(db, m.entity_type, m.entity_id)
        action_buttons = ''.join(f'<form method="post" action="/campaigns/{c.id}/members/{m.id}/status" style="display:inline"><input type="hidden" name="token" value="{h(token)}"><input type="hidden" name="status" value="{h(code)}"><button class="btn2">{h(label)}</button></form>' for code,label in [('sent','پیام ارسال شد'),('replied','جواب داد'),('registered','ثبت‌نام کرد'),('irrelevant','نامرتبط'),('skipped','رد شد')])
        rows += f'<tr><td><b>{h(name)}</b><br><span class="badge">{h(m.entity_type)}</span><span class="badge">{h(m.message_variant)}</span></td><td>{entity_contact_html(db,m.entity_type,m.entity_id)}</td><td><span class="badge">{h(MEMBER_STATUS.get(m.status,m.status))}</span><br>{h(fmt_dt(m.sent_at))}</td><td><div class="message">{h(m.message_text or "")}</div><button class="btn2 copy" data-text="{h(m.message_text or "")}">کپی پیام</button>{f"<a class=\"btn2\" target=\"_blank\" href=\"{h(m.invite_link)}\">لینک دعوت</a>" if m.invite_link else ""}</td><td>{action_buttons}</td></tr>'
    body = f'''
    <div class="card"><h2>{h(c.name)}</h2><p class="muted">{h(c.goal)}</p>{f'<div class="badge orange">{h(msg)}</div>' if msg else ''}<div class="grid3"><div class="card">کل اعضا<br><b>{st['total']}</b></div><div class="card">ارسال شده<br><b>{st['sent']}</b></div><div class="card">ثبت‌نام<br><b>{st['registered']}</b></div><div class="card">نرخ جواب<br><b>{st['reply_rate']}%</b></div><div class="card">نرخ تبدیل<br><b>{st['conversion_rate']}%</b></div><div class="card">Batch امروز<br><b>{len(batch)}</b></div></div></div>
    <div class="card"><form method="post" action="/campaigns/{c.id}/populate"><input type="hidden" name="token" value="{h(token)}"><label><input type="checkbox" name="skip_recent" value="1" checked> حذف کسانی که ۷ روز اخیر پیام گرفته‌اند</label><button>افزودن مخاطبین هدف به کمپین</button></form><a class="btn2" href="/campaigns/{c.id}/export.csv?token={h(token)}">خروجی CSV کمپین</a></div>
    <div class="card"><h3>اعضای کمپین</h3><table><thead><tr><th>مخاطب</th><th>ارتباط</th><th>وضعیت</th><th>پیام</th><th>عملیات</th></tr></thead><tbody>{rows or '<tr><td colspan="5">هنوز عضوی اضافه نشده</td></tr>'}</tbody></table></div>'''
    return layout('جزئیات کمپین', body, token)


@router.post('/campaigns/{campaign_id}/members/{member_id}/status')
def campaign_member_status(campaign_id: int, member_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = '', status: Annotated[str, Form()] = 'sent'):
    check_token(token)
    m = db.get(CampaignMember, member_id)
    if not m or m.campaign_id != campaign_id: raise HTTPException(404, 'عضو کمپین پیدا نشد')
    now = datetime.utcnow()
    m.status = status
    if status == 'sent': m.sent_at = m.sent_at or now; m.follow_up_at = now + timedelta(days=2)
    if status == 'replied': m.replied_at = m.replied_at or now
    if status == 'registered': m.registered_at = m.registered_at or now
    db.add(m)
    # sync broad CRM status
    if m.entity_type == 'lead':
        lead = db.get(Lead, m.entity_id)
        if lead:
            lead.status = {'sent':'messaged','replied':'replied','registered':'registered','irrelevant':'irrelevant','skipped':'no_response'}.get(status, lead.status)
            db.add(lead)
    elif m.entity_type == 'person':
        p = db.get(Person, m.entity_id)
        if p:
            p.status = {'sent':'contacting','replied':'replied','registered':'registered','irrelevant':'irrelevant','skipped':'not_interested'}.get(status, p.status)
            db.add(p)
    db.commit()
    return RedirectResponse(url=f'/campaigns/{campaign_id}?token={quote_plus(token)}', status_code=303)


@router.get('/campaigns/{campaign_id}/export.csv')
def campaign_export(campaign_id: int, db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    members = list(db.scalars(select(CampaignMember).where(CampaignMember.campaign_id == campaign_id).order_by(CampaignMember.id)).all())
    output = io.StringIO(); output.write('\ufeff')
    w = csv.writer(output)
    w.writerow(['نام','نوع','وضعیت کمپین','Variant','پیام','لینک دعوت','ارسال','جواب','ثبت‌نام'])
    for m in members:
        w.writerow([entity_name(db,m.entity_type,m.entity_id), m.entity_type, MEMBER_STATUS.get(m.status,m.status), m.message_variant, m.message_text, m.invite_link, m.sent_at, m.replied_at, m.registered_at])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename=campaign.csv'})
