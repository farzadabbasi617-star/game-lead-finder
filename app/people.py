from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.ai import chat_json
from app.config import get_settings
from app.crm import get_setting, render_site_link
from app.db.models import Lead, Person, PersonActivityLog, PersonLeadLink
from app.db.session import get_db
from app.utils import normalize_instagram, normalize_phone, normalize_telegram, normalize_text_key

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')

PERSON_STATUS_LABELS = {
    'new': 'جدید',
    'contacting': 'در حال ارتباط',
    'replied': 'جواب داده',
    'potential_partner': 'همکار بالقوه',
    'registered': 'ثبت‌نام کرده',
    'not_interested': 'عدم تمایل',
    'irrelevant': 'نامرتبط',
}


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


def layout(title: str, body: str, token: str = '') -> HTMLResponse:
    css = '''<style>:root{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb;--shadow:0 16px 40px rgba(16,24,40,.08)}*{box-sizing:border-box}body{font-family:Tahoma,Arial,sans-serif;background:radial-gradient(circle at top right,rgba(37,99,235,.10),transparent 34%),linear-gradient(180deg,#f8fbff,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}.wrap{max-width:1240px;margin:auto;padding:22px}a{text-decoration:none;color:var(--primary)}.hero{background:linear-gradient(135deg,#111827,#1e3a8a 58%,#2563eb);color:white;border-radius:26px;padding:20px;box-shadow:var(--shadow);margin-bottom:16px}.hero h1{margin:0}.hero .muted{color:#dbeafe}.card{background:rgba(255,255,255,.93);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045)}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.muted{color:var(--muted);font-size:13px;line-height:1.8}.btn,.action,button{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer}.btn2{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe}.badge{display:inline-flex;background:#eef2ff;color:#2546a6;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;font-weight:600}input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}textarea{min-height:90px}.log{border-right:4px solid var(--primary);padding:11px;margin:10px 0;background:#f8fafc;border-radius:12px}table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}th,td{padding:12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top}th{background:#eef4ff}@media(max-width:800px){.wrap{padding:12px}.grid2,.grid3{grid-template-columns:1fr}table{display:block;overflow-x:auto}}</style>'''
    js = '''<script>document.addEventListener('click',async e=>{if(e.target.classList.contains('copy')){const t=e.target.dataset.text||'';try{await navigator.clipboard.writeText(t);e.target.textContent='کپی شد ✅'}catch(_){alert(t)}setTimeout(()=>e.target.textContent='کپی',1200)}});</script>'''
    return HTMLResponse(f'<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title>{css}</head><body><div class="wrap"><div class="hero"><h1>{h(title)}</h1><div class="muted">بانک افراد پشت فروشگاه‌ها، پیج‌ها، کانال‌ها و آگهی‌ها</div><a class="btn btn2" href="/">بانک لیدها</a> <a class="btn btn2" href="/people">بانک افراد</a></div>{body}</div>{js}</body></html>')


def person_keys_from_values(phone=None, telegram=None, instagram=None, email=None, full_name=None, city=None) -> set[str]:
    keys = set()
    p = normalize_phone(phone)
    if p and len(p) >= 8: keys.add(f'phone:{p}')
    tg = normalize_telegram(telegram)
    if tg: keys.add(f'tg:{tg}')
    ig = normalize_instagram(instagram)
    if ig: keys.add(f'ig:{ig}')
    if email: keys.add(f'email:{email.strip().lower()}')
    nk = normalize_text_key(full_name)
    ck = normalize_text_key(city)
    if nk and ck and len(nk) >= 3: keys.add(f'name_city:{nk}:{ck}')
    return keys


def person_keys(person: Person) -> set[str]:
    return person_keys_from_values(phone=person.phone, telegram=person.telegram, instagram=person.instagram, email=person.email, full_name=person.full_name, city=person.city)


def find_duplicate_person(db: Session, data: dict) -> Person | None:
    keys = person_keys_from_values(phone=data.get('phone'), telegram=data.get('telegram'), instagram=data.get('instagram'), email=data.get('email'), full_name=data.get('full_name'), city=data.get('city'))
    if not keys:
        return None
    for person in db.scalars(select(Person)).all():
        if keys & person_keys(person):
            return person
    return None


def upsert_person(db: Session, data: dict) -> tuple[Person, bool]:
    existing = find_duplicate_person(db, data)
    if existing:
        for field in ['nickname','role','phone','whatsapp','telegram','instagram','email','city','source','notes']:
            val = data.get(field)
            if val and not getattr(existing, field):
                setattr(existing, field, val)
        existing.last_seen = datetime.utcnow()
        db.add(existing); db.commit(); db.refresh(existing)
        return existing, False
    person = Person(**{k:v for k,v in data.items() if k in {'full_name','nickname','role','phone','whatsapp','telegram','instagram','email','city','source','status','notes'}})
    if not person.status: person.status = 'new'
    db.add(person); db.commit(); db.refresh(person)
    return person, True


def link_person_lead(db: Session, person_id: int, lead_id: int, relationship: str = 'نامشخص') -> None:
    existing = db.scalar(select(PersonLeadLink).where(PersonLeadLink.person_id == person_id, PersonLeadLink.lead_id == lead_id))
    if not existing:
        db.add(PersonLeadLink(person_id=person_id, lead_id=lead_id, relationship=relationship))
        db.commit()


def person_contact_buttons(person: Person) -> str:
    buttons = []
    if person.phone: buttons.append(f'<a class="action btn2" href="tel:{h(person.phone)}">تماس</a>')
    if person.whatsapp: buttons.append(f'<a class="action btn2" target="_blank" href="https://wa.me/{h(normalize_phone(person.whatsapp) or person.whatsapp)}">واتساپ</a>')
    if person.telegram: buttons.append(f'<a class="action btn2" target="_blank" href="{h(person.telegram if str(person.telegram).startswith("http") else "https://t.me/" + str(person.telegram).lstrip("@"))}">تلگرام</a>')
    if person.instagram: buttons.append(f'<a class="action btn2" target="_blank" href="{h(person.instagram if str(person.instagram).startswith("http") else "https://instagram.com/" + str(person.instagram).lstrip("@"))}">اینستاگرام</a>')
    if person.email: buttons.append(f'<a class="action btn2" href="mailto:{h(person.email)}">ایمیل</a>')
    return ''.join(buttons) or '<span class="muted">راه ارتباطی ثبت نشده</span>'


@router.get('/people', response_class=HTMLResponse)
def people_index(db: Session = Depends(get_db), token: str = Query(''), q: str = '', status: str = '', role: str = ''):
    check_token(token)
    stmt = select(Person)
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(Person.full_name.ilike(like), Person.phone.ilike(like), Person.telegram.ilike(like), Person.instagram.ilike(like), Person.email.ilike(like), Person.city.ilike(like)))
    if status: stmt = stmt.where(Person.status == status)
    if role: stmt = stmt.where(Person.role.ilike(f'%{role}%'))
    people = list(db.scalars(stmt.order_by(desc(Person.last_seen)).limit(300)).all())
    rows = ''.join(f'<tr><td><b>{h(p.full_name)}</b><br><span class="muted">{h(p.role or "-")} | {h(p.city or "-")}</span></td><td>{person_contact_buttons(p)}</td><td><span class="badge">{h(PERSON_STATUS_LABELS.get(p.status,p.status))}</span></td><td>{h(fmt_dt(p.first_seen))}</td><td><a class="btn2" href="/people/{p.id}">جزئیات</a></td></tr>' for p in people)
    status_opts = ''.join(f'<option value="{h(k)}">{h(v)}</option>' for k,v in PERSON_STATUS_LABELS.items())
    body = f'''
    <div class="card"><h2>افزودن فرد</h2><form method="post" action="/people"><input name="full_name" placeholder="نام کامل" required><input name="role" placeholder="نقش؛ مالک/ادمین/فروشنده"><input name="phone" placeholder="تلفن"><input name="telegram" placeholder="تلگرام"><input name="instagram" placeholder="اینستاگرام"><input name="city" placeholder="شهر"><button>ذخیره فرد</button></form></div>
    <div class="card"><h2>جستجو و فیلتر افراد</h2><form><input name="q" value="{h(q)}" placeholder="نام، شماره، آیدی، شهر"><input name="role" value="{h(role)}" placeholder="نقش"><select name="status"><option value="">همه وضعیت‌ها</option>{status_opts}</select><button>نمایش</button></form></div>
    <div class="card"><h2>بانک افراد</h2><table><thead><tr><th>فرد</th><th>ارتباط</th><th>وضعیت</th><th>اولین ثبت</th><th>عملیات</th></tr></thead><tbody>{rows or '<tr><td colspan="5">هنوز فردی ثبت نشده</td></tr>'}</tbody></table></div>'''
    return layout('بانک افراد', body, token)


@router.post('/people')
def people_add(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', full_name: Annotated[str, Form()] = '', role: Annotated[str, Form()] = '', phone: Annotated[str, Form()] = '', whatsapp: Annotated[str, Form()] = '', telegram: Annotated[str, Form()] = '', instagram: Annotated[str, Form()] = '', email: Annotated[str, Form()] = '', city: Annotated[str, Form()] = '', source: Annotated[str, Form()] = 'manual', notes: Annotated[str, Form()] = ''):
    check_token(token)
    if not full_name.strip():
        raise HTTPException(400, 'نام فرد الزامی است')
    person, is_new = upsert_person(db, {'full_name': full_name.strip(), 'role': role.strip() or None, 'phone': phone.strip() or None, 'whatsapp': whatsapp.strip() or None, 'telegram': telegram.strip() or None, 'instagram': instagram.strip() or None, 'email': email.strip() or None, 'city': city.strip() or None, 'source': source, 'notes': notes.strip() or None, 'status': 'new'})
    db.add(PersonActivityLog(person_id=person.id, action='create' if is_new else 'merge', note='ثبت/ادغام فرد'))
    db.commit()
    return RedirectResponse(url=f'/people/{person.id}', status_code=303)


@router.get('/people/{person_id}', response_class=HTMLResponse)
def person_detail(person_id: int, db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    person = db.get(Person, person_id)
    if not person: raise HTTPException(404, 'فرد پیدا نشد')
    links = list(db.scalars(select(PersonLeadLink).where(PersonLeadLink.person_id == person_id)).all())
    lead_blocks = ''
    for link in links:
        lead = db.get(Lead, link.lead_id)
        if lead:
            lead_blocks += f'<div class="log"><b>{h(lead.title)}</b> <span class="badge">{h(link.relationship)}</span><br><a href="/leads/{lead.id}">باز کردن لید</a></div>'
    logs = list(db.scalars(select(PersonActivityLog).where(PersonActivityLog.person_id == person_id).order_by(desc(PersonActivityLog.created_at)).limit(80)).all())
    log_blocks = ''.join(f'<div class="log"><b>{h(l.action)}</b> - {h(fmt_dt(l.created_at))}<br>{h(l.note)}</div>' for l in logs) or '<span class="muted">تاریخچه‌ای ثبت نشده</span>'
    status_opts = ''.join(f'<option value="{h(k)}" {"selected" if person.status==k else ""}>{h(v)}</option>' for k,v in PERSON_STATUS_LABELS.items())
    body = f'''
    <div class="card"><h1>{h(person.full_name)}</h1><p class="muted">{h(person.role or '-')} | {h(person.city or '-')} | ثبت: {h(fmt_dt(person.first_seen))}</p>{person_contact_buttons(person)}<form method="post" action="/people/{person.id}/ai-message" style="display:inline"><button class="btn2">پیام اختصاصی AI برای فرد</button></form></div>
    <div class="grid2"><div class="card"><h3>ویرایش فرد</h3><form method="post" action="/people/{person.id}/update"><input name="full_name" value="{h(person.full_name)}"><input name="role" value="{h(person.role)}"><select name="status">{status_opts}</select><input name="phone" value="{h(person.phone)}" placeholder="تلفن"><input name="whatsapp" value="{h(person.whatsapp)}" placeholder="واتساپ"><input name="telegram" value="{h(person.telegram)}" placeholder="تلگرام"><input name="instagram" value="{h(person.instagram)}" placeholder="اینستاگرام"><input name="email" value="{h(person.email)}" placeholder="ایمیل"><input name="city" value="{h(person.city)}" placeholder="شهر"><textarea name="notes">{h(person.notes)}</textarea><button>ذخیره</button></form></div><div class="card"><h3>ثبت فعالیت</h3><form method="post" action="/people/{person.id}/activity"><select name="action"><option value="note">یادداشت</option><option value="message">پیام</option><option value="call">تماس</option><option value="followup">پیگیری</option></select><textarea name="note"></textarea><button>ثبت</button></form></div></div>
    <div class="card"><h3>لیدهای مرتبط</h3>{lead_blocks or '<span class="muted">هنوز به لید وصل نشده</span>'}</div>
    <div class="card"><h3>تاریخچه فرد</h3>{log_blocks}</div>'''
    return layout('جزئیات فرد', body, token)


@router.post('/people/{person_id}/update')
def person_update(person_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = '', full_name: Annotated[str, Form()] = '', role: Annotated[str, Form()] = '', status: Annotated[str, Form()] = 'new', phone: Annotated[str, Form()] = '', whatsapp: Annotated[str, Form()] = '', telegram: Annotated[str, Form()] = '', instagram: Annotated[str, Form()] = '', email: Annotated[str, Form()] = '', city: Annotated[str, Form()] = '', notes: Annotated[str, Form()] = ''):
    check_token(token)
    person = db.get(Person, person_id)
    if not person: raise HTTPException(404, 'فرد پیدا نشد')
    person.full_name = full_name.strip() or person.full_name
    person.role = role.strip() or None
    person.status = status
    person.phone = phone.strip() or None
    person.whatsapp = whatsapp.strip() or None
    person.telegram = telegram.strip() or None
    person.instagram = instagram.strip() or None
    person.email = email.strip() or None
    person.city = city.strip() or None
    person.notes = notes.strip() or None
    person.last_seen = datetime.utcnow()
    db.add(person); db.commit()
    db.add(PersonActivityLog(person_id=person.id, action='update', note='اطلاعات فرد بروزرسانی شد')); db.commit()
    return RedirectResponse(url=f'/people/{person.id}', status_code=303)


@router.post('/people/{person_id}/activity')
def person_activity(person_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = '', action: Annotated[str, Form()] = 'note', note: Annotated[str, Form()] = ''):
    check_token(token)
    if not db.get(Person, person_id): raise HTTPException(404, 'فرد پیدا نشد')
    db.add(PersonActivityLog(person_id=person_id, action=action, note=note)); db.commit()
    return RedirectResponse(url=f'/people/{person_id}', status_code=303)


@router.post('/people/{person_id}/ai-message')
async def person_ai_message(person_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = ''):
    check_token(token)
    person = db.get(Person, person_id)
    if not person: raise HTTPException(404, 'فرد پیدا نشد')
    prompt = f'برای این فرد یک پیام دعوت کوتاه و انسانی فارسی بنویس. نام: {person.full_name} نقش: {person.role} شهر: {person.city} لینک سایت: {get_setting(db, "site_link", "YOUR_SITE_LINK")}. فقط JSON بده: {{"message":"..."}}'
    try:
        data, used = await chat_json([{'role':'system','content':'فقط JSON معتبر بده.'},{'role':'user','content':prompt}], max_tokens=500)
        msg = data.get('message') or ''
    except Exception:
        msg = f'سلام {person.full_name} وقتتون بخیر. ما یک پلتفرم تخصصی گیمینگ برای ثبت رایگان آگهی فروشنده‌ها داریم. خوشحال می‌شیم اگر مایل بودید همکاری کنیم: {get_setting(db, "site_link", "YOUR_SITE_LINK")}'
    db.add(PersonActivityLog(person_id=person.id, action='ai_message', note=msg)); db.commit()
    return RedirectResponse(url=f'/people/{person.id}', status_code=303)


@router.post('/leads/{lead_id}/people')
def create_person_from_lead(lead_id: int, db: Session = Depends(get_db), token: Annotated[str, Form()] = '', full_name: Annotated[str, Form()] = '', role: Annotated[str, Form()] = 'ادمین/مسئول', relationship: Annotated[str, Form()] = 'ادمین'):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(404, 'لید پیدا نشد')
    name = full_name.strip() or lead.title
    person, is_new = upsert_person(db, {'full_name': name, 'role': role, 'phone': lead.phone, 'telegram': lead.telegram, 'instagram': lead.instagram, 'city': lead.city, 'source': lead.source, 'notes': f'ساخته شده از لید #{lead.id}', 'status': 'new'})
    link_person_lead(db, person.id, lead.id, relationship)
    db.add(PersonActivityLog(person_id=person.id, action='linked_lead', note=f'اتصال به لید #{lead.id}: {lead.title}'))
    db.commit()
    return RedirectResponse(url=f'/people/{person.id}', status_code=303)
