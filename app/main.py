from __future__ import annotations

import csv
import io
import html
from openpyxl import Workbook
from typing import Annotated
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.collectors.enrichment import run_enrichment
from app.collectors.orchestrator import run_collector
from app.config import get_settings
from app.db.models import City, CrawlerRun, Keyword, Lead
from app.db.session import Base, engine, get_db
from app.repository import dashboard_stats, init_seed_data, upsert_lead
from app.scoring import detect_category, score_lead
from app.utils import public_invite_message

app = FastAPI(title='Game Lead Finder')


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
        raise HTTPException(status_code=401, detail='Invalid ADMIN_TOKEN')


def css() -> str:
    return '''
    <style>
      *{box-sizing:border-box} body{font-family:Tahoma,Arial,sans-serif;background:#f6f7fb;margin:0;color:#111;direction:rtl}
      a{color:#2457c5;text-decoration:none} a:hover{text-decoration:underline}
      .wrap{max-width:1320px;margin:auto;padding:22px}.top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
      h1{margin:0 0 8px;font-size:26px}.muted{color:#666;font-size:13px}.card{background:white;border:1px solid #e7e8ef;border-radius:14px;padding:16px;margin:14px 0;box-shadow:0 3px 14px rgba(20,30,60,.04)}
      .stats{display:grid;grid-template-columns:repeat(5,minmax(110px,1fr));gap:10px}.stat{background:linear-gradient(135deg,#fff,#eef3ff);border:1px solid #dfe7ff;border-radius:12px;padding:14px}.stat b{display:block;font-size:24px;margin-top:5px}
      form.inline{display:flex;gap:8px;flex-wrap:wrap;align-items:center} input,select,button,textarea{font-family:inherit;border:1px solid #d8dbe6;border-radius:10px;padding:9px;background:white} button{cursor:pointer;background:#1f55d5;color:white;border:0}.btn2{background:#eef2ff;color:#143891;border:1px solid #cdd8ff}.danger{background:#fff2f2;color:#ad1e1e;border:1px solid #ffd0d0}
      table{width:100%;border-collapse:collapse;background:white;border-radius:12px;overflow:hidden} th,td{padding:10px;border-bottom:1px solid #eee;text-align:right;vertical-align:top;font-size:13px} th{background:#f1f4ff;color:#333;position:sticky;top:0} tr:hover{background:#fbfcff}
      .badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#eef2ff;color:#2546a6;font-size:12px}.score{font-weight:bold;color:#137333}.url{max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;direction:ltr}.small{font-size:12px}.ltr{direction:ltr;text-align:left}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
      @media(max-width:800px){.stats,.grid2{grid-template-columns:1fr}.wrap{padding:12px} table{display:block;overflow-x:auto;direction:rtl}.top{display:block}}
    </style>
    '''


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f'''<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>{css()}</head><body><div class="wrap">{body}</div><script>document.addEventListener('click',async function(e){{if(e.target.classList.contains('copy-msg')){{const t=e.target.dataset.message||'';try{{await navigator.clipboard.writeText(t);e.target.textContent='کپی شد ✅';}}catch(_ ){{const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove();e.target.textContent='کپی شد ✅';}}setTimeout(()=>e.target.textContent='کپی متن پیام',1600);}}}});</script></body></html>''')


@app.get('/', response_class=HTMLResponse)
def index(
    request: Request,
    db: Session = Depends(get_db),
    status: str = Query(''),
    source: str = Query(''),
    category: str = Query(''),
    q: str = Query(''),
    limit: int = Query(100, ge=1, le=500),
    token: str = Query('', description='ADMIN_TOKEN for forms'),
):
    stats = dashboard_stats(db)
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source == source)
    if category:
        stmt = stmt.where(Lead.category == category)
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(Lead.title.ilike(like), Lead.description.ilike(like), Lead.url.ilike(like), Lead.city.ilike(like)))
    leads = list(db.scalars(stmt.order_by(desc(Lead.score), desc(Lead.first_seen)).limit(limit)).all())
    keywords = list(db.scalars(select(Keyword).order_by(Keyword.id)).all())
    cities = list(db.scalars(select(City).order_by(City.id)).all())
    runs = list(db.scalars(select(CrawlerRun).order_by(desc(CrawlerRun.started_at)).limit(8)).all())

    lead_rows = ''
    statuses = ['new', 'checked', 'messaged', 'replied', 'registered', 'irrelevant']
    for lead in leads:
        invite_msg = html.escape(public_invite_message(lead.title, lead.category), quote=True)
        status_opts = ''.join([f'<option value="{s}" {"selected" if lead.status==s else ""}>{s}</option>' for s in statuses])
        lead_rows += f'''
        <tr>
          <td><span class="badge">{lead.source}</span><br><span class="small">{lead.entity_type or ''}</span></td>
          <td><b>{lead.title}</b><br><span class="muted">{(lead.description or '')[:160]}</span></td>
          <td>{lead.category or ''}<br><span class="muted">{lead.city or ''}</span></td>
          <td class="score">{lead.score}</td>
          <td>{lead.phone or ''}<br>{f'<a href="{lead.website}" target="_blank">وب‌سایت</a>' if lead.website else ''}</td>
          <td><a class="url" href="{lead.url}" target="_blank">{lead.url}</a><br><span class="muted">{lead.address or ''}</span></td>
          <td>
            <form class="inline" method="post" action="/leads/{lead.id}/status">
              <input type="hidden" name="token" value="{token}">
              <select name="status">{status_opts}</select>
              <input name="notes" placeholder="یادداشت" value="{lead.notes or ''}" style="width:150px">
              <button class="btn2">ذخیره</button>
            </form>
            <button type="button" class="btn2 copy-msg" data-message="{invite_msg}" style="margin-top:6px">کپی متن پیام</button>
          </td>
        </tr>'''

    keyword_list = '، '.join([kw.keyword for kw in keywords[:25]])
    city_list = '، '.join([c.name for c in cities[:25]])
    run_rows = ''.join([f'<tr><td>{r.source}</td><td>{r.query or ""}</td><td>{r.found_count}</td><td>{r.new_count}</td><td>{r.error or ""}</td><td class="ltr">{r.started_at}</td></tr>' for r in runs])

    body = f'''
    <div class="top">
      <div><h1>🎮 Game Lead Finder</h1><div class="muted">لیست‌ساز قانونی لیدهای حوزه گیم؛ مناسب Neon + Render</div></div>
      <div><a class="btn2" style="padding:10px;border-radius:10px" href="/export.csv?status={status}&source={source}&category={category}&q={q}">خروجی CSV</a> <a class="btn2" style="padding:10px;border-radius:10px" href="/export.xlsx?status={status}&source={source}&category={category}&q={q}">خروجی Excel</a></div>
    </div>

    <div class="stats card">
      <div class="stat">کل لیدها<b>{stats['total']}</b></div><div class="stat">جدید<b>{stats['new']}</b></div><div class="stat">پیام داده شده<b>{stats['messaged']}</b></div><div class="stat">جواب داده<b>{stats['replied']}</b></div><div class="stat">ثبت‌نام کرده<b>{stats['registered']}</b></div>
    </div>

    <div class="card">
      <h3>اجرای کالکتور</h3>
      <form class="inline" method="post" action="/run">
        <input type="password" name="token" placeholder="ADMIN_TOKEN" value="{token}">
        <select name="source"><option value="all">همه APIهای فعال</option><option value="google_places">Google Places</option><option value="neshan">Neshan</option><option value="web">Web Search APIها</option><option value="google_cse">Google CSE</option><option value="brave">Brave Search</option><option value="serper">Serper</option><option value="searchapi">SearchAPI</option><option value="tavily">Tavily</option><option value="serpapi">SerpAPI</option><option value="search_links">Search Links رایگان</option></select>
        <label>کلمه‌ها <input type="number" name="keyword_limit" value="5" min="1" max="30" style="width:80px"></label>
        <label>شهرها <input type="number" name="city_limit" value="3" min="1" max="30" style="width:80px"></label>
        <label>نتیجه <input type="number" name="result_limit" value="8" min="1" max="20" style="width:80px"></label>
        <button>شروع جمع‌آوری</button>
      </form>
      <div class="muted" style="margin-top:8px">اگر کلید API نداشته باشی، گزینه Search Links رایگان لینک‌های جستجوی دستی می‌سازد. برای لید واقعی از APIها استفاده می‌کنیم.</div>
      <hr style="border:0;border-top:1px solid #eee;margin:14px 0">
      <form class="inline" method="post" action="/enrich">
        <input type="hidden" name="token" value="{token}">
        <label>Enrich سایت‌های عمومی <input type="number" name="limit" value="30" min="1" max="200" style="width:90px"></label>
        <button class="btn2">پیدا کردن اینستاگرام/تلگرام از سایت‌ها</button>
      </form>
    </div>

    <div class="grid2">
      <div class="card"><h3>کلمات کلیدی</h3><form class="inline" method="post" action="/keywords"><input type="hidden" name="token" value="{token}"><input name="keyword" placeholder="مثلاً فروشگاه کنسول"><button class="btn2">افزودن</button></form><p class="muted">{keyword_list}</p></div>
      <div class="card"><h3>شهرها</h3><form class="inline" method="post" action="/cities"><input type="hidden" name="token" value="{token}"><input name="name" placeholder="نام شهر"><input name="lat" placeholder="lat" style="width:90px"><input name="lng" placeholder="lng" style="width:90px"><button class="btn2">افزودن</button></form><p class="muted">{city_list}</p></div>
    </div>

    <div class="card">
      <h3>افزودن دستی لید</h3>
      <form class="inline" method="post" action="/leads">
        <input type="hidden" name="token" value="{token}"><input name="title" placeholder="عنوان" required><input name="url" placeholder="لینک" required style="min-width:260px"><input name="source" value="manual" style="width:90px"><input name="city" placeholder="شهر" style="width:90px"><input name="phone" placeholder="تلفن"><button class="btn2">افزودن</button>
      </form>
    </div>

    <div class="card">
      <h3>Import CSV</h3>
      <form class="inline" method="post" action="/import.csv" enctype="multipart/form-data">
        <input type="hidden" name="token" value="{token}">
        <input type="file" name="file" accept=".csv,text/csv" required>
        <button class="btn2">وارد کردن لیست</button>
      </form>
      <div class="muted">ستون‌های قابل قبول: title,url,source,city,phone,website,address,description,category</div>
    </div>

    <div class="card">
      <h3>فیلتر لیدها</h3>
      <form class="inline" method="get" action="/">
        <input name="token" placeholder="ADMIN_TOKEN برای فرم‌ها" value="{token}" type="password">
        <input name="q" placeholder="جستجو" value="{q}">
        <input name="source" placeholder="source" value="{source}">
        <input name="category" placeholder="category" value="{category}">
        <select name="status"><option value="">همه وضعیت‌ها</option>{''.join([f'<option value="{s}" {"selected" if status==s else ""}>{s}</option>' for s in statuses])}</select>
        <input name="limit" type="number" min="1" max="500" value="{limit}" style="width:90px">
        <button>فیلتر</button>
      </form>
    </div>

    <div class="card" style="overflow:auto">
      <h3>لیدها</h3>
      <table><thead><tr><th>منبع</th><th>عنوان/توضیح</th><th>دسته/شهر</th><th>امتیاز</th><th>تماس</th><th>لینک/آدرس</th><th>وضعیت</th></tr></thead><tbody>{lead_rows or '<tr><td colspan="7">هنوز لید نداریم.</td></tr>'}</tbody></table>
    </div>

    <div class="card" style="overflow:auto">
      <h3>آخرین اجراها</h3>
      <table><thead><tr><th>source</th><th>query</th><th>found</th><th>new</th><th>error</th><th>started</th></tr></thead><tbody>{run_rows}</tbody></table>
    </div>
    '''
    return page('Game Lead Finder', body)


@app.post('/run')
async def run_now(
    db: Session = Depends(get_db),
    token: Annotated[str, Form()] = '',
    source: Annotated[str, Form()] = 'all',
    keyword_limit: Annotated[int, Form()] = 5,
    city_limit: Annotated[int, Form()] = 3,
    result_limit: Annotated[int, Form()] = 8,
):
    check_token(token)
    await run_collector(db, source=source, keyword_limit=keyword_limit, city_limit=city_limit, result_limit=result_limit)
    return RedirectResponse(url=f'/?token={token}', status_code=303)


@app.get('/api/run')
async def api_run(
    db: Session = Depends(get_db),
    token: str = Query(...),
    source: str = 'all',
    keyword_limit: int = 5,
    city_limit: int = 3,
    result_limit: int = 8,
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
    return RedirectResponse(url=f'/?token={token}', status_code=303)


@app.post('/cities')
def add_city(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', name: Annotated[str, Form()] = '', lat: Annotated[str, Form()] = '', lng: Annotated[str, Form()] = ''):
    check_token(token)
    name = name.strip()
    if name and not db.scalar(select(City).where(City.name == name)):
        db.add(City(name=name, lat=float(lat) if lat else None, lng=float(lng) if lng else None))
        db.commit()
    return RedirectResponse(url=f'/?token={token}', status_code=303)


@app.post('/leads')
def add_lead(
    db: Session = Depends(get_db), token: Annotated[str, Form()] = '', title: Annotated[str, Form()] = '', url: Annotated[str, Form()] = '',
    source: Annotated[str, Form()] = 'manual', city: Annotated[str, Form()] = '', phone: Annotated[str, Form()] = ''
):
    check_token(token)
    category = detect_category(title, url)
    score = score_lead(title=title, url=url, phone=phone)
    upsert_lead(db, {'source': source, 'entity_type': 'manual', 'title': title, 'url': url, 'city': city, 'phone': phone, 'category': category, 'score': score})
    return RedirectResponse(url=f'/?token={token}', status_code=303)


@app.post('/leads/{lead_id}/status')
def update_status(db: Session = Depends(get_db), lead_id: int = 0, token: Annotated[str, Form()] = '', status: Annotated[str, Form()] = 'new', notes: Annotated[str, Form()] = ''):
    check_token(token)
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, 'Lead not found')
    lead.status = status
    lead.notes = notes
    db.add(lead)
    db.commit()
    return RedirectResponse(url=f'/?token={token}', status_code=303)


@app.get('/export.csv')
def export_csv(db: Session = Depends(get_db), status: str = '', source: str = '', category: str = '', q: str = ''):
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source == source)
    if category:
        stmt = stmt.where(Lead.category == category)
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(Lead.title.ilike(like), Lead.description.ilike(like), Lead.url.ilike(like), Lead.city.ilike(like)))
    leads = list(db.scalars(stmt.order_by(desc(Lead.score), desc(Lead.first_seen))).all())

    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM for Excel Persian compatibility
    writer = csv.writer(output)
    writer.writerow(['id', 'source', 'type', 'title', 'url', 'category', 'city', 'score', 'status', 'phone', 'website', 'address', 'description', 'rating', 'review_count', 'notes', 'first_seen', 'last_seen'])
    for l in leads:
        writer.writerow([l.id, l.source, l.entity_type, l.title, l.url, l.category, l.city, l.score, l.status, l.phone, l.website, l.address, l.description, l.rating, l.review_count, l.notes, l.first_seen, l.last_seen])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename=game-leads.csv'})


@app.get('/export.xlsx')
def export_xlsx(db: Session = Depends(get_db), status: str = '', source: str = '', category: str = '', q: str = ''):
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source == source)
    if category:
        stmt = stmt.where(Lead.category == category)
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(Lead.title.ilike(like), Lead.description.ilike(like), Lead.url.ilike(like), Lead.city.ilike(like)))
    leads = list(db.scalars(stmt.order_by(desc(Lead.score), desc(Lead.first_seen))).all())

    wb = Workbook()
    ws = wb.active
    ws.title = 'leads'
    headers = ['id', 'source', 'type', 'title', 'url', 'category', 'city', 'score', 'status', 'phone', 'website', 'instagram', 'telegram', 'address', 'description', 'rating', 'review_count', 'notes', 'first_seen', 'last_seen']
    ws.append(headers)
    for l in leads:
        ws.append([l.id, l.source, l.entity_type, l.title, l.url, l.category, l.city, l.score, l.status, l.phone, l.website, l.instagram, l.telegram, l.address, l.description, l.rating, l.review_count, l.notes, str(l.first_seen), str(l.last_seen)])
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col[:50])
        ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 10), 45)
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
    imported = 0
    new_count = 0
    for row in reader:
        title = (row.get('title') or row.get('عنوان') or '').strip()
        url = (row.get('url') or row.get('link') or row.get('لینک') or '').strip()
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
            'website': row.get('website') or row.get('سایت'),
            'address': row.get('address') or row.get('آدرس'),
            'description': row.get('description') or row.get('توضیح'),
            'instagram': row.get('instagram'),
            'telegram': row.get('telegram'),
        }
        _, is_new = upsert_lead(db, data)
        imported += 1
        if is_new:
            new_count += 1
    return RedirectResponse(url=f'/?token={token}', status_code=303)


@app.post('/enrich')
async def enrich(db: Session = Depends(get_db), token: Annotated[str, Form()] = '', limit: Annotated[int, Form()] = 30):
    check_token(token)
    await run_enrichment(db, limit=limit)
    return RedirectResponse(url=f'/?token={token}', status_code=303)


@app.get('/api/leads')
def api_leads(
    db: Session = Depends(get_db),
    token: str = Query(...),
    status: str = '',
    source: str = '',
    limit: int = Query(100, ge=1, le=500),
):
    check_token(token)
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source == source)
    leads = list(db.scalars(stmt.order_by(desc(Lead.score), desc(Lead.first_seen)).limit(limit)).all())
    return [
        {
            'id': l.id, 'source': l.source, 'type': l.entity_type, 'title': l.title, 'url': l.url,
            'category': l.category, 'city': l.city, 'score': l.score, 'status': l.status,
            'phone': l.phone, 'website': l.website, 'instagram': l.instagram, 'telegram': l.telegram,
            'address': l.address, 'description': l.description, 'rating': l.rating, 'review_count': l.review_count,
            'first_seen': l.first_seen.isoformat(), 'last_seen': l.last_seen.isoformat(),
        }
        for l in leads
    ]


@app.get('/health')
def health():
    return {'ok': True}
