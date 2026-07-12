"""Contacts — phonebook for saving contacts with phone/Instagram/Telegram.

Route order: static /contacts, /contacts/add, /contacts/import, /contacts/export.*, /api/contacts
             THEN parameterized /contacts/{id}, /contacts/{id}/update, /contacts/{id}/delete
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Lead

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')

CONTACT_TYPES = {'customer': '🛍️ مشتری', 'seller': '🏪 فروشنده', 'admin': '👤 ادمین', 'influencer': '🌟 اینفلوئنسر', 'gamer': '🎮 گیمر', 'shop': '🏬 فروشگاه', 'other': '📁 سایر'}


def h(v) -> str:
    import html as m; return m.escape('' if v is None else str(v), quote=True)

def fmt_dt(value) -> str:
    if not value: return '-'
    try:
        if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(TEHRAN_TZ).strftime('%Y/%m/%d - %H:%M')
    except: return str(value)

def _norm_ig(val: str) -> str | None:
    val = val.strip()
    if not val: return None
    if not val.startswith('http'): val = f'https://instagram.com/{val.lstrip("@")}'
    return val

def _norm_tg(val: str) -> str | None:
    val = val.strip()
    if not val: return None
    if not val.startswith('http'): val = f'https://t.me/{val.lstrip("@")}'
    return val


def layout(title: str, body: str) -> HTMLResponse:
    css = '<style>:root{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb}*{box-sizing:border-box}body{font-family:Tahoma,Arial,sans-serif;background:linear-gradient(180deg,#f0fdf4,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}.wrap{max-width:1200px;margin:auto;padding:22px}a{color:var(--primary);text-decoration:none}.hero{background:linear-gradient(135deg,#064e3b,#059669 50%,#34d399);color:white;border-radius:26px;padding:20px;box-shadow:0 16px 40px rgba(16,24,40,.08);margin-bottom:16px}.hero h1{margin:0;font-size:25px}.hero .muted{color:#d1fae5}.card{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045)}.card h3{margin-top:0}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.muted{color:var(--muted);font-size:13px;line-height:1.8}.small{font-size:12px}.btn,.action,button{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer;font-size:13px}.btn2{background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0}.btn-danger{background:#fff1f2;color:#be123c;border:1px solid #fecdd3;font-size:11px;padding:5px 8px}.badge{display:inline-flex;background:#ecfdf5;color:#065f46;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;font-weight:600}.badge.blue{background:#eff6ff;color:#1d4ed8}input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}input:focus,select:focus{border-color:#6ee7b7;box-shadow:0 0 0 4px rgba(16,185,129,.12)}table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}th,td{padding:11px 12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top;font-size:13px}th{background:linear-gradient(180deg,#ecfdf5,#d1fae5);color:#064e3b;font-weight:700;position:sticky;top:0;z-index:1}tr:hover{background:#ecfdf5}tr:last-child td{border-bottom:0}.stat-card{background:linear-gradient(180deg,#fff,#ecfdf5);border:1px solid #a7f3d0;border-radius:20px;padding:17px;text-align:center;box-shadow:0 10px 24px rgba(16,185,129,.07)}.stat-card b{display:block;font-size:28px;margin-top:7px}.quick-add{background:#f0fdf4;border:2px dashed #86efac;border-radius:16px;padding:16px;margin:10px 0}.phone-link{color:#059669;font-weight:700;text-decoration:none}.ig-link{color:#e1306c;text-decoration:none;font-weight:600}.tg-link{color:#0088cc;text-decoration:none;font-weight:600}@media(max-width:900px){.wrap{padding:12px}.grid2,.grid3,.grid4{grid-template-columns:1fr}table{display:block;overflow-x:auto}.hero{border-radius:20px;padding:16px}}</style>'
    js = '<script>function confirmDelete(id,name){if(confirm(name+" حذف بشه؟")){window.location.href="/contacts/"+id+"/delete"}}</script>'
    nav = '<a class="btn btn2" href="/">🏠 خانه</a> <a class="btn btn2" href="/contacts">📒 مخاطبین</a> <a class="btn btn2" href="/contacts/add">➕ افزودن</a> <a class="btn btn2" href="/contacts/import">📥 ورود CSV</a>'
    page = '<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + h(title) + '</title>' + css + '</head><body><div class="wrap"><div class="hero"><h1>' + h(title) + '</h1><div class="muted">ذخیره سریع مخاطبین با شماره تماس، آیدی اینستاگرام و تلگرام</div>' + nav + '</div>' + body + '</div>' + js + '</body></html>'
    return HTMLResponse(page)


def _stats(db: Session) -> dict:
    total = db.scalar(select(func.count(Lead.id)).where(or_(Lead.phone.isnot(None), Lead.instagram.isnot(None), Lead.telegram.isnot(None)))) or 0
    phone = db.scalar(select(func.count(Lead.id)).where(Lead.phone.isnot(None))) or 0
    ig = db.scalar(select(func.count(Lead.id)).where(Lead.instagram.isnot(None))) or 0
    tg = db.scalar(select(func.count(Lead.id)).where(Lead.telegram.isnot(None))) or 0
    return {'total': total, 'phone': phone, 'instagram': ig, 'telegram': tg}


def _row(lead: Lead) -> str:
    ph = f'<a class="phone-link" href="tel:{h(lead.phone)}">📞 {h(lead.phone)}</a>' if lead.phone else '<span class="muted">📞 -</span>'
    ig = ''
    if lead.instagram:
        m = re.search(r'instagram\.com/([A-Za-z0-9_.]+)', lead.instagram)
        ig = '@' + m.group(1) if m else lead.instagram
    ig_html = f'<a class="ig-link" target="_blank" href="{h(lead.instagram)}">📸 {h(ig)}</a>' if lead.instagram else '<span class="muted">📸 -</span>'
    tg = ''
    if lead.telegram:
        m = re.search(r't\.me/([A-Za-z0-9_]+)', lead.telegram)
        tg = '@' + m.group(1) if m else lead.telegram
    tg_html = f'<a class="tg-link" target="_blank" href="{h(lead.telegram)}">✈️ {h(tg)}</a>' if lead.telegram else '<span class="muted">✈️ -</span>'
    tags = ''
    if lead.category: tags += f'<span class="badge">{h(lead.category)}</span>'
    if lead.city: tags += f'<span class="badge blue">{h(lead.city)}</span>'
    btns = f'<a class="btn" href="/contacts/{lead.id}" style="font-size:11px;padding:5px 8px">جزئیات</a>'
    if lead.phone: btns += f'<a class="btn2 btn" href="tel:{h(lead.phone)}" style="font-size:11px;padding:5px 8px">📞</a>'
    if lead.telegram: btns += f'<a class="btn2 btn" target="_blank" href="{h(lead.telegram)}" style="font-size:11px;padding:5px 8px">✈️</a>'
    btns += f'<button class="btn-danger" onclick="confirmDelete({lead.id},\'{h(lead.title)}\')">🗑</button>'
    return f'<tr><td><b style="font-size:15px">{h(lead.title)}</b><br>{tags}<br><span class="small muted">{fmt_dt(lead.first_seen)}</span></td><td>{ph}<br>{ig_html}<br>{tg_html}</td><td><span class="small muted">{h((lead.notes or "")[:80])}</span></td><td>{btns}</td></tr>'


# ============================================================
# 1. STATIC ROUTES FIRST
# ============================================================

@router.get('/contacts', response_class=HTMLResponse)
def contacts_index(db: Session = Depends(get_db), q: str = Query(''), has: str = Query(''), msg: str = Query('')):
    stats = _stats(db)
    stmt = select(Lead).where(or_(Lead.phone.isnot(None), Lead.instagram.isnot(None), Lead.telegram.isnot(None)))
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(Lead.title.ilike(like), Lead.phone.ilike(like), Lead.instagram.ilike(like), Lead.telegram.ilike(like), Lead.notes.ilike(like), Lead.city.ilike(like)))
    if has == 'phone': stmt = stmt.where(Lead.phone.isnot(None))
    elif has == 'instagram': stmt = stmt.where(Lead.instagram.isnot(None))
    elif has == 'telegram': stmt = stmt.where(Lead.telegram.isnot(None))
    elif has == 'all': stmt = stmt.where(Lead.phone.isnot(None), Lead.instagram.isnot(None), Lead.telegram.isnot(None))
    contacts = list(db.scalars(stmt.order_by(desc(Lead.first_seen)).limit(200)).all())
    rows = ''.join(_row(c) for c in contacts)
    has_opts = ''.join(f'<option value="{k}" {"selected" if has==k else ""}>{v}</option>' for k,v in [('', 'همه'), ('phone', 'فقط تلفن'), ('instagram', 'فقط اینستاگرام'), ('telegram', 'فقط تلگرام'), ('all', 'همه ۳')])
    cat_opts = ''.join(f'<option value="{k}">{v}</option>' for k,v in CONTACT_TYPES.items())
    body = f'''<div class="grid4"><div class="stat-card">کل<b style="color:#059669">{stats["total"]}</b></div><div class="stat-card">📞 تلفن<b style="color:#2563eb">{stats["phone"]}</b></div><div class="stat-card">📸 اینستاگرام<b style="color:#e1306c">{stats["instagram"]}</b></div><div class="stat-card">✈️ تلگرام<b style="color:#0088cc">{stats["telegram"]}</b></div></div>
    {f'<div class="card" style="background:#ecfdf3;border-color:#a7f3d0">{h(msg)}</div>' if msg else ''}
    <div class="quick-add"><h3 style="margin:0 0 8px">➕ افزودن سریع</h3><form method="post" action="/contacts/add"><input name="title" placeholder="نام" required style="min-width:150px"><input name="phone" placeholder="📞 شماره" style="width:140px"><input name="instagram" placeholder="📸 آیدی اینستا" style="width:150px"><input name="telegram" placeholder="✈️ آیدی تلگرام" style="width:140px"><input name="city" placeholder="شهر" style="width:80px"><select name="category" style="width:100px">{cat_opts}</select><button style="background:#059669">ذخیره</button></form></div>
    <div class="card"><h3>🔍 جستجو</h3><form method="get" action="/contacts"><input name="q" placeholder="نام، شماره، آیدی، شهر" value="{h(q)}" style="min-width:250px"><select name="has">{has_opts}</select><button>فیلتر</button></form></div>
    <div class="card"><h3>📒 مخاطبین ({len(contacts)} نفر)</h3><div style="overflow-x:auto"><table><thead><tr><th>مخاطب</th><th style="width:220px">اطلاعات تماس</th><th style="width:150px">یادداشت</th><th style="width:160px">عملیات</th></tr></thead><tbody>{rows or '<tr><td colspan="4" style="text-align:center;padding:30px" class="muted">مخاطبی نداری!</td></tr>'}</tbody></table></div></div>
    <div class="card"><a class="btn" href="/contacts/export.xlsx">📥 Excel</a> <a class="btn2 btn" href="/contacts/export.csv">📥 CSV</a> <a class="btn2 btn" href="/contacts/import">📥 ورود CSV</a></div>'''
    return layout('📒 مخاطبین من', body)


@router.get('/contacts/add', response_class=HTMLResponse)
def contacts_add_page():
    cat_opts = ''.join(f'<option value="{k}">{v}</option>' for k,v in CONTACT_TYPES.items())
    body = f'''<div class="card"><h3>➕ افزودن مخاطب</h3><form method="post" action="/contacts/add">
    <label>نام *<br><input name="title" required style="min-width:300px"></label><br>
    <div class="grid3"><label>📞 تلفن<br><input name="phone" style="width:100%"></label><label>📸 اینستاگرام<br><input name="instagram" style="width:100%"></label><label>✈️ تلگرام<br><input name="telegram" style="width:100%"></label></div><br>
    <div class="grid3"><label>شهر<br><input name="city" style="width:100%"></label><label>دسته<br><select name="category" style="width:100%">{cat_opts}</select></label><label>لینک<br><input name="url" style="width:100%"></label></div><br>
    <label>یادداشت<br><textarea name="notes" style="width:100%;min-height:60px"></textarea></label><br>
    <button style="background:#059669;font-size:16px;padding:12px 24px">✅ ذخیره</button></form></div>'''
    return layout('افزودن مخاطب', body)


@router.post('/contacts/add')
def contacts_add(db: Session = Depends(get_db), title: Annotated[str, Form()] = '', phone: Annotated[str, Form()] = '', instagram: Annotated[str, Form()] = '', telegram: Annotated[str, Form()] = '', city: Annotated[str, Form()] = '', category: Annotated[str, Form()] = '', url: Annotated[str, Form()] = '', notes: Annotated[str, Form()] = ''):
    title = title.strip()
    if not title: return RedirectResponse(url='/contacts', status_code=303)
    ph = phone.strip() or None
    ig = _norm_ig(instagram)
    tg = _norm_tg(telegram)
    if not url.strip(): url = tg or ig or f'contact://{ph or title}'
    existing = None
    if ph: existing = db.scalar(select(Lead).where(Lead.phone == ph))
    if not existing and ig: existing = db.scalar(select(Lead).where(Lead.instagram == ig))
    if not existing and tg: existing = db.scalar(select(Lead).where(Lead.telegram == tg))
    if existing:
        if ph and not existing.phone: existing.phone = ph
        if ig and not existing.instagram: existing.instagram = ig
        if tg and not existing.telegram: existing.telegram = tg
        if city and not existing.city: existing.city = city
        if category and not existing.category: existing.category = category
        if notes and not existing.notes: existing.notes = notes
        db.add(existing); db.commit()
        return RedirectResponse(url=f'/contacts?msg={quote_plus(f"{title} آپدیت شد")}', status_code=303)
    lead = Lead(source='contact', entity_type='contact', title=title[:500], url=url, phone=ph, instagram=ig, telegram=tg, city=city.strip() or None, category=category.strip() or None, notes=notes.strip() or None, status='new', score=50)
    db.add(lead); db.commit()
    return RedirectResponse(url=f'/contacts?msg={quote_plus(f"{title} ذخیره شد ✅")}', status_code=303)


@router.get('/contacts/import', response_class=HTMLResponse)
def contacts_import_page():
    body = '<div class="card"><h3>📥 ورود از CSV</h3><p class="muted">ستون‌ها: title, phone, instagram, telegram, city, category, notes</p><form method="post" action="/import.csv" enctype="multipart/form-data"><input type="file" name="file" accept=".csv" required><button style="background:#059669">وارد کردن</button></form></div>'
    return layout('ورود مخاطبین', body)


@router.get('/contacts/export.csv')
def contacts_csv(db: Session = Depends(get_db)):
    stmt = select(Lead).where(or_(Lead.phone.isnot(None), Lead.instagram.isnot(None), Lead.telegram.isnot(None))).order_by(desc(Lead.first_seen))
    items = list(db.scalars(stmt).all())
    out = io.StringIO(); out.write('\ufeff')
    w = csv.writer(out)
    w.writerow(['نام','تلفن','اینستاگرام','تلگرام','شهر','دسته','یادداشت','لینک','تاریخ'])
    for c in items: w.writerow([c.title, c.phone, c.instagram, c.telegram, c.city, c.category, c.notes, c.url, c.first_seen])
    out.seek(0)
    return StreamingResponse(iter([out.getvalue()]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename=contacts.csv'})


@router.get('/contacts/export.xlsx')
def contacts_xlsx(db: Session = Depends(get_db)):
    from openpyxl import Workbook
    stmt = select(Lead).where(or_(Lead.phone.isnot(None), Lead.instagram.isnot(None), Lead.telegram.isnot(None))).order_by(desc(Lead.first_seen))
    items = list(db.scalars(stmt).all())
    wb = Workbook(); ws = wb.active; ws.title = 'مخاطبین'
    ws.append(['نام','تلفن','اینستاگرام','تلگرام','شهر','دسته','یادداشت','لینک','تاریخ'])
    for c in items: ws.append([c.title, c.phone, c.instagram, c.telegram, c.city, c.category, c.notes, c.url, str(c.first_seen)])
    for col in ws.columns:
        ml = max(len(str(cell.value or '')) for cell in col[:50])
        ws.column_dimensions[col[0].column_letter].width = min(max(ml+2, 12), 40)
    out = io.BytesIO(); wb.save(out); out.seek(0)
    return StreamingResponse(out, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=contacts.xlsx'})


@router.get('/api/contacts')
def api_contacts(db: Session = Depends(get_db), q: str = '', limit: int = Query(100)):
    stmt = select(Lead).where(or_(Lead.phone.isnot(None), Lead.instagram.isnot(None), Lead.telegram.isnot(None)))
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(Lead.title.ilike(like), Lead.phone.ilike(like), Lead.instagram.ilike(like), Lead.telegram.ilike(like)))
    items = list(db.scalars(stmt.order_by(desc(Lead.first_seen)).limit(limit)).all())
    return [{'id': c.id, 'name': c.title, 'phone': c.phone, 'instagram': c.instagram, 'telegram': c.telegram, 'city': c.city, 'category': c.category, 'notes': c.notes} for c in items]


# ============================================================
# 2. PARAMETERIZED ROUTES LAST
# ============================================================

@router.get('/contacts/{contact_id}', response_class=HTMLResponse)
def contact_detail(contact_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, contact_id)
    if not lead: raise HTTPException(404)
    phone = lead.phone or ''
    ig = ''
    if lead.instagram:
        m = re.search(r'instagram\.com/([A-Za-z0-9_.]+)', lead.instagram)
        ig = m.group(1) if m else lead.instagram
    tg = ''
    if lead.telegram:
        m = re.search(r't\.me/([A-Za-z0-9_]+)', lead.telegram)
        tg = m.group(1) if m else lead.telegram
    body = f'''<div class="card"><h1>👤 {h(lead.title)}</h1><p class="muted">#{lead.id} | {fmt_dt(lead.first_seen)}</p>
    <div class="grid4">
      <div style="text-align:center;padding:16px;background:#f0fdf4;border-radius:14px"><div style="font-size:24px">📞</div><div style="font-size:18px;font-weight:800;color:#059669">{h(phone or '-')}</div>{f'<a class="btn" href="tel:{h(phone)}" style="margin-top:8px">تماس</a>' if phone else ''}{f'<a class="btn2 btn" href="https://wa.me/98{h(phone.lstrip("0"))}" target="_blank" style="margin-top:4px">واتساپ</a>' if phone else ''}</div>
      <div style="text-align:center;padding:16px;background:#fdf2f8;border-radius:14px"><div style="font-size:24px">📸</div><div style="font-size:18px;font-weight:800;color:#e1306c">{h(ig or '-')}</div>{f'<a class="btn" target="_blank" href="{h(lead.instagram)}" style="margin-top:8px;background:#e1306c">پیج</a>' if lead.instagram else ''}{f'<a class="btn2 btn" target="_blank" href="https://ig.me/m/{h(ig)}" style="margin-top:4px">دایرکت</a>' if ig else ''}</div>
      <div style="text-align:center;padding:16px;background:#eff6ff;border-radius:14px"><div style="font-size:24px">✈️</div><div style="font-size:18px;font-weight:800;color:#0088cc">{h(tg or '-')}</div>{f'<a class="btn" target="_blank" href="{h(lead.telegram)}" style="margin-top:8px;background:#0088cc">تلگرام</a>' if lead.telegram else ''}</div>
      <div style="text-align:center;padding:16px;background:#faf5ff;border-radius:14px"><div style="font-size:24px">🏙</div><div style="font-size:18px;font-weight:800;color:#7c3aed">{h(lead.city or '-')}</div><div class="badge" style="margin-top:8px">{h(lead.category or '-')}</div></div>
    </div></div>
    <div class="grid2"><div class="card"><h3>📝 ویرایش</h3><form method="post" action="/contacts/{lead.id}/update">
    <label>نام<br><input name="title" value="{h(lead.title)}" style="min-width:300px"></label><br>
    <label>تلفن<br><input name="phone" value="{h(lead.phone or '')}" style="width:200px"></label><br>
    <label>اینستاگرام<br><input name="instagram" value="{h(lead.instagram or '')}" style="min-width:250px"></label><br>
    <label>تلگرام<br><input name="telegram" value="{h(lead.telegram or '')}" style="min-width:250px"></label><br>
    <label>شهر<br><input name="city" value="{h(lead.city or '')}"></label><br>
    <label>یادداشت<br><textarea name="notes" style="width:100%;min-height:80px">{h(lead.notes or '')}</textarea></label><br>
    <button>ذخیره</button></form></div>
    <div class="card"><h3>🔗 لینک‌ها</h3>{f'<p><a target="_blank" href="{h(lead.url)}">🔗 لینک اصلی</a></p>' if lead.url else ''}{f'<p><a target="_blank" href="{h(lead.website)}">🌐 وب‌سایت</a></p>' if lead.website else ''}{f'<p>📍 {h(lead.address)}</p>' if lead.address else ''}<p class="muted" style="margin-top:16px">{h(lead.description or "")}</p></div></div>'''
    return layout(f'👤 {lead.title}', body)


@router.post('/contacts/{contact_id}/update')
def contact_update(contact_id: int, db: Session = Depends(get_db), title: Annotated[str, Form()] = '', phone: Annotated[str, Form()] = '', instagram: Annotated[str, Form()] = '', telegram: Annotated[str, Form()] = '', city: Annotated[str, Form()] = '', notes: Annotated[str, Form()] = ''):
    lead = db.get(Lead, contact_id)
    if not lead: raise HTTPException(404)
    lead.title = title.strip() or lead.title
    lead.phone = phone.strip() or None
    lead.instagram = _norm_ig(instagram) or lead.instagram if instagram.strip() else None
    lead.telegram = _norm_tg(telegram) or lead.telegram if telegram.strip() else None
    lead.city = city.strip() or None
    lead.notes = notes.strip() or None
    db.add(lead); db.commit()
    return RedirectResponse(url=f'/contacts/{contact_id}', status_code=303)


@router.get('/contacts/{contact_id}/delete')
def contact_delete(contact_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, contact_id)
    if not lead: raise HTTPException(404)
    name = lead.title
    db.delete(lead); db.commit()
    return RedirectResponse(url=f'/contacts?msg={quote_plus(f"{name} حذف شد")}', status_code=303)
