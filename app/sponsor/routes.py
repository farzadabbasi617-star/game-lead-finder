"""Sponsor Channel Finder — routes for UI and API.

Route order: static paths FIRST, then parameterized /{id} last.
فقط ایرانی، فقط بالای ۱۰۰۰ عضو، پوشه‌بندی بر اساس دسته، حذف سریع.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.sponsor.models import SponsorChannel
from app.sponsor.collector import discover_sponsor_channels, scrape_tg_preview
from app.sponsor.scoring import compute_ad_score

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')

MIN_MEMBERS = 1000

SPONSOR_STATUS = {
    'new': 'جدید', 'researching': 'در حال بررسی', 'quoted': 'قیمت گرفته شده',
    'contacted': 'تماس گرفته شده', 'booked': 'رزرو شده', 'rejected': 'رد شده',
}

CATEGORY_META = {
    'سی‌پی کالاف': ('🎯', '#dc2626'),
    'یوسی پابجی': ('🔫', '#ea580c'),
    'اکانت': ('🎮', '#2563eb'),
    'گیفت کارت': ('💳', '#7c3aed'),
    'جم/الماس': ('💎', '#be185d'),
    'فروشگاه گیم': ('🏪', '#0891b2'),
    'گیم‌نت': ('🕹️', '#059669'),
    'لوازم گیمینگ': ('🎧', '#d97706'),
}


def h(v) -> str:
    import html
    return html.escape('' if v is None else str(v), quote=True)


def fmt_dt(value) -> str:
    if not value: return '-'
    try:
        if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(TEHRAN_TZ).strftime('%Y/%m/%d - %H:%M')
    except Exception: return str(value)


def _score_bar(score: int, max_val: int = 100, color: str = '#2563eb') -> str:
    pct = min(score / max_val * 100, 100) if max_val else 0
    return (f'<div style="display:flex;align-items:center;gap:6px">'
            f'<div style="flex:1;background:#eef2ff;border-radius:999px;height:14px;overflow:hidden;min-width:60px">'
            f'<div style="width:{pct:.0f}%;height:100%;background:{color};border-radius:999px"></div></div>'
            f'<b style="min-width:28px;font-size:12px">{score}</b></div>')


def _ad_score_color(score: int) -> str:
    if score >= 70: return '#12b76a'
    if score >= 45: return '#f79009'
    if score >= 25: return '#2563eb'
    return '#9ca3af'


def _format_members(count: int | None) -> str:
    if not count: return '-'
    if count >= 1_000_000: return f'{count / 1_000_000:.1f}M'
    if count >= 1_000: return f'{count / 1_000:.1f}K'
    return str(count)


def layout(title: str, body: str) -> HTMLResponse:
    css = '''<style>
      :root{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb;--shadow:0 16px 40px rgba(16,24,40,.08)}
      *{box-sizing:border-box}body{font-family:Tahoma,Arial,sans-serif;background:radial-gradient(circle at top right,rgba(37,99,235,.10),transparent 34%),linear-gradient(180deg,#f8fbff,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}
      .wrap{max-width:1400px;margin:auto;padding:22px}a{color:var(--primary);text-decoration:none}
      .hero{background:linear-gradient(135deg,#0f172a,#7c3aed 58%,#a855f7);color:white;border-radius:26px;padding:20px;box-shadow:var(--shadow);margin-bottom:16px}
      .hero h1{margin:0;font-size:25px}.hero .muted{color:#e9d5ff}
      .card{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045);backdrop-filter:blur(12px)}.card h3{margin-top:0}
      .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
      .muted{color:var(--muted);font-size:13px;line-height:1.8}.small{font-size:12px}
      .btn,.action,button{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer;font-size:13px}
      .btn2{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe}
      .btn-danger{background:#fff1f2;color:#be123c;border:1px solid #fecdd3;font-size:11px;padding:5px 8px}.btn-danger:hover{background:#fecdd3}
      .badge{display:inline-flex;background:#eef2ff;color:#2546a6;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;font-weight:600}
      .badge.green{background:#ecfdf3;color:#027a48}.badge.orange{background:#fffaeb;color:#b54708}.badge.red{background:#fff1f2;color:#be123c}.badge.purple{background:#faf5ff;color:#7c3aed}
      input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}
      input:focus,select:focus{border-color:#93c5fd;box-shadow:0 0 0 4px rgba(37,99,235,.12)}
      table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}
      th,td{padding:11px 12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top;font-size:13px}
      th{background:linear-gradient(180deg,#f5f3ff,#ede9fe);color:#4c1d95;font-weight:700;position:sticky;top:0;z-index:1}
      tr:hover{background:#faf5ff}tr:last-child td{border-bottom:0}
      .channel-row{border-right:4px solid transparent;transition:.15s}.channel-row:hover{border-right-color:#7c3aed}
      .url{max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;direction:ltr;color:#6b21a8}
      .hint{background:#faf5ff;border:1px solid #e9d5ff;color:#581c87;border-radius:14px;padding:12px;margin-top:12px}
      .stat-card{background:linear-gradient(180deg,#fff,#faf5ff);border:1px solid #e9d5ff;border-radius:20px;padding:17px;text-align:center;box-shadow:0 10px 24px rgba(124,58,237,.07)}.stat-card b{display:block;font-size:28px;margin-top:7px}
      .folder-section{border:1px solid var(--line);border-radius:18px;margin:14px 0;background:white;overflow:hidden}
      .folder-section summary{cursor:pointer;list-style:none;padding:16px 18px;background:linear-gradient(180deg,#f5f3ff,#ede9fe);display:flex;align-items:center;justify-content:space-between;gap:10px}
      .folder-section summary::-webkit-details-marker{display:none}
      .folder-title{font-weight:800;font-size:16px}.folder-count{background:#7c3aed;color:#fff;border-radius:999px;padding:5px 10px;font-size:12px;font-weight:700}
      @media(max-width:900px){.wrap{padding:12px}.grid2,.grid3,.grid4{grid-template-columns:1fr}table{display:block;overflow-x:auto}.hero{border-radius:20px;padding:16px}}
    </style>'''
    js = '''<script>
    function confirmDelete(id,name){if(confirm('آیا مطمئنی «'+name+'» رو حذف کنی؟')){window.location.href='/sponsor/'+id+'/delete'}}
    document.addEventListener('click',async e=>{if(e.target.classList.contains('copy')){const t=e.target.dataset.text||'';try{await navigator.clipboard.writeText(t);e.target.textContent='کپی شد ✅'}catch(_){alert(t)}setTimeout(()=>e.target.textContent='کپی',1200)}});
    </script>'''
    return HTMLResponse(f'<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title>{css}</head><body><div class="wrap"><div class="hero"><h1>{h(title)}</h1><div class="muted">فقط کانال‌های ایرانی بالای ۱۰۰۰ عضو — با امتیازدهی و دسته‌بندی</div><a class="btn btn2" href="/">بانک لیدها</a> <a class="btn btn2" href="/analytics">📊 آنالیتیکس</a> <a class="btn btn2" href="/sponsor">🎯 اسپانسری</a> <a class="btn btn2" href="/sponsor/discover">🔍 کشف کانال</a></div>{body}</div>{js}</body></html>')


def _stats(db: Session) -> dict:
    base = select(SponsorChannel).where(SponsorChannel.language == 'fa', SponsorChannel.member_count >= MIN_MEMBERS)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    high_score = db.scalar(select(func.count()).select_from(base.where(SponsorChannel.ad_score >= 50).subquery())) or 0
    booked = db.scalar(select(func.count()).select_from(base.where(SponsorChannel.status == 'booked').subquery())) or 0
    # تعداد بر اساس دسته
    by_cat = db.execute(
        select(SponsorChannel.category, func.count(SponsorChannel.id))
        .where(SponsorChannel.language == 'fa', SponsorChannel.member_count >= MIN_MEMBERS)
        .group_by(SponsorChannel.category)
        .order_by(func.count(SponsorChannel.id).desc())
    ).all()
    return {'total': total, 'high_score': high_score, 'booked': booked, 'by_cat': by_cat}


def _render_row(ch: SponsorChannel) -> str:
    ad_color = _ad_score_color(ch.ad_score)
    sbc = {'new': '', 'researching': 'orange', 'quoted': 'purple', 'contacted': 'orange', 'booked': 'green', 'rejected': 'red'}.get(ch.status, '')
    return f'''<tr class="channel-row"><td><div style="font-weight:800;font-size:15px;margin-bottom:4px">{h(ch.title)}</div><a class="url" target="_blank" href="{h(ch.channel_url)}">{h(ch.channel_url)}</a><div class="small muted" style="margin-top:3px">{h(ch.description or '')[:120]}</div><div style="margin-top:5px"><span class="badge {sbc}">{h(SPONSOR_STATUS.get(ch.status, ch.status))}</span>{f'<span class="badge">{h(ch.category)}</span>' if ch.category else ''}</div></td><td style="text-align:center"><div style="font-size:22px;font-weight:800;color:#7c3aed">{_format_members(ch.member_count)}</div><div class="small muted">عضو</div><div style="margin-top:8px"><div class="small">👁 {round(ch.avg_views or 0)} بازدید</div><div class="small">📊 {round(ch.engagement_rate or 0, 1)}% تعامل</div></div></td><td><div style="margin-bottom:6px"><b class="small">مرتبط‌بودن</b>{_score_bar(ch.relevance_score, color='#7c3aed')}</div><div style="margin-bottom:6px"><b class="small">کیفیت</b>{_score_bar(ch.quality_score, color='#12b76a')}</div><div><b class="small">امتیاز تبلیغ</b>{_score_bar(ch.ad_score, color=ad_color)}</div></td><td><div class="small muted">قیمت: {h(ch.ad_price or '-')}</div><div class="small muted">تماس: {h(ch.contact_method or '-')}</div></td><td><a class="btn" href="/sponsor/{ch.id}" style="font-size:11px;padding:6px 10px">جزئیات</a><a class="btn2 btn" target="_blank" href="{h(ch.channel_url)}" style="font-size:11px;padding:6px 10px">باز کردن</a>{f'<a class="btn2 btn" target="_blank" href="https://t.me/s/{h(ch.username)}" style="font-size:11px;padding:6px 10px">پیش‌نمایش</a>' if ch.username and ch.platform == 'telegram' else ''}<button class="btn-danger" onclick="confirmDelete({ch.id},'{h(ch.title)}')">🗑</button></td></tr>'''


# ============================================================
# 1. STATIC ROUTES FIRST
# ============================================================

@router.get('/sponsor', response_class=HTMLResponse)
def sponsor_index(db: Session = Depends(get_db), status: str = Query(''), category: str = Query(''), q: str = Query(''), sort: str = Query('ad_score'), min_members: int = Query(0, ge=0), min_score: int = Query(0, ge=0, le=100), limit: int = Query(200, ge=1, le=500), msg: str = Query('')):
    stats = _stats(db)
    min_m = max(min_members, MIN_MEMBERS)
    stmt = select(SponsorChannel).where(SponsorChannel.language == 'fa', SponsorChannel.member_count >= min_m)
    if status: stmt = stmt.where(SponsorChannel.status == status)
    if category: stmt = stmt.where(SponsorChannel.category.ilike(f'%{category}%'))
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(SponsorChannel.title.ilike(like), SponsorChannel.username.ilike(like), SponsorChannel.description.ilike(like)))
    if min_score: stmt = stmt.where(SponsorChannel.ad_score >= min_score)
    sort_map = {'ad_score': desc(SponsorChannel.ad_score), 'members': desc(SponsorChannel.member_count), 'engagement': desc(SponsorChannel.engagement_rate), 'newest': desc(SponsorChannel.first_seen), 'relevance': desc(SponsorChannel.relevance_score)}
    stmt = stmt.order_by(sort_map.get(sort, desc(SponsorChannel.ad_score)))
    channels = list(db.scalars(stmt.limit(limit)).all())

    # ── پوشه‌بندی بر اساس دسته ──
    folders: dict[str, list] = {}
    for ch in channels:
        cat = ch.category or 'سایر'
        folders.setdefault(cat, []).append(ch)

    folder_sections = ''
    for cat, items in sorted(folders.items(), key=lambda x: -len(x[1])):
        icon, color = CATEGORY_META.get(cat, ('📁', '#6b7280'))
        rows = ''.join(_render_row(ch) for ch in items)
        folder_sections += f'''
        <details class="folder-section" {"open" if len(folder_sections) < 300 else ""}>
          <summary>
            <div><div class="folder-title">{icon} {h(cat)}</div></div>
            <div><span class="folder-count">{len(items)} کانال</span></div>
          </summary>
          <div style="padding:0 0 8px">
            <table><thead><tr><th>کانال</th><th style="text-align:center;width:120px">آمار</th><th style="width:200px">امتیازها</th><th style="width:140px">اطلاعات تبلیغ</th><th style="width:160px">عملیات</th></tr></thead><tbody>{rows}</tbody></table>
          </div>
        </details>'''

    sort_opts = ''.join(f'<option value="{k}" {"selected" if sort==k else ""}>{v}</option>' for k,v in [('ad_score','بالاترین امتیاز'),('members','بیشترین عضو'),('engagement','بیشترین تعامل'),('newest','جدیدترین'),('relevance','مرتبط‌ترین')])
    status_opts = '<option value="">همه وضعیت‌ها</option>' + ''.join(f'<option value="{k}" {"selected" if status==k else ""}>{v}</option>' for k,v in SPONSOR_STATUS.items())

    # آمار دسته‌ها
    cat_cards = ''.join(
        f'<div class="stat-card"><span style="font-size:18px">{CATEGORY_META.get(cat, ("📁","#6b7280"))[0]}</span><div class="small">{h(cat or "سایر")}</div><b style="color:{CATEGORY_META.get(cat, ("","#6b7280"))[1]}">{cnt}</b></div>'
        for cat, cnt in stats['by_cat'][:8] if cat
    )

    body = f'''<div class="grid4"><div class="stat-card">کل کانال‌های ایرانی<b style="color:#7c3aed">{stats["total"]}</b></div><div class="stat-card">⭐ امتیاز بالا<b style="color:#12b76a">{stats["high_score"]}</b></div><div class="stat-card">رزرو شده<b style="color:#f79009">{stats["booked"]}</b></div><div class="stat-card">حداقل عضو<b style="color:#2563eb">{_format_members(MIN_MEMBERS)}</b></div></div>
    {f'<div style="display:flex;gap:10px;flex-wrap:wrap">{cat_cards}</div>' if cat_cards else ''}
    {f'<div class="card hint">{h(msg)}</div>' if msg else ''}
    <div class="card"><h3>🔍 جستجو و فیلتر</h3><form method="get" action="/sponsor"><input name="q" placeholder="جستجو" value="{h(q)}" style="min-width:240px"><select name="status">{status_opts}</select><input name="category" placeholder="دسته" value="{h(category)}" style="width:120px"><label>حداقل امتیاز <input type="number" name="min_score" value="{min_score}" min="0" max="100" style="width:80px"></label><select name="sort">{sort_opts}</select><button>فیلتر</button></form></div>
    <div class="card"><h3>🎯 کانال‌های اسپانسری ایرانی بالای ۱۰۰۰ عضو ({len(channels)} کانال)</h3>{folder_sections or '<div style="text-align:center;padding:30px" class="muted">هنوز کانالی کشف نشده.</div>'}</div>
    <div class="card"><a class="btn" href="/sponsor/export.xlsx">📥 Excel</a> <a class="btn2 btn" href="/sponsor/export.csv">📥 CSV</a> <a class="btn-danger" href="/sponsor/cleanup" onclick="return confirm('کانال‌های زیر ۱۰۰۰ عضو و خارجی حذف بشن؟')">🧹 پاکسازی</a></div>'''
    return layout('🎯 کانال‌های اسپانسری گیمینگ', body)


@router.get('/sponsor/discover', response_class=HTMLResponse)
def discover_page(db: Session = Depends(get_db)):
    from app.sponsor.collector import GAMING_QUERIES
    qcb = ''.join(f'<label style="display:block;margin:4px 0"><input type="checkbox" name="queries" value="{h(q)}" checked> {h(q)}</label>' for q in GAMING_QUERIES[:10])
    body = f'''<div class="card"><h3>🔍 کشف کانال‌های ایرانی جدید</h3><p class="muted">فقط کانال‌های ایرانی بالای ۱۰۰۰ عضو ذخیره میشن.</p><form method="post" action="/sponsor/discover"><h4>عبارت‌های جستجو:</h4>{qcb}<div style="margin-top:12px"><label>نتیجه هر جستجو <input type="number" name="max_results" value="10" min="1" max="30" style="width:80px"></label></div><div style="margin-top:12px"><button>🚀 شروع کشف</button></div></form></div>
    <div class="card"><h3>➕ افزودن کانال دستی</h3><form method="post" action="/sponsor/manual"><input name="channel_url" placeholder="لینک کانال" required style="min-width:300px"><input name="title" placeholder="نام کانال"><input name="category" placeholder="دسته‌بندی"><button class="btn2">افزودن</button></form></div>'''
    return layout('کشف کانال', body)


@router.post('/sponsor/discover')
async def discover_run(db: Session = Depends(get_db), max_results: Annotated[int, Form()] = 10, min_score: Annotated[int, Form()] = 20, queries: Annotated[list[str] | None, Form()] = None):
    result = await discover_sponsor_channels(db, queries=queries, max_results_per_query=max_results, min_ad_score=0)
    # پاکسازی: حذف کانال‌های غیرایرانی و زیر ۱۰۰۰
    bad = db.execute(select(SponsorChannel).where((SponsorChannel.language != 'fa') | (SponsorChannel.member_count < MIN_MEMBERS) | (SponsorChannel.member_count.is_(None)))).scalars().all()
    clean_count = len(bad)
    for ch in bad:
        db.delete(ch)
    db.commit()
    msg = f"کشف: {result['queries_run']} جستجو، {result['channels_found']} کانال، {result['new_saved']} جدید. پاکسازی: {clean_count} کانال ضعیف/خارجی حذف شد."
    return RedirectResponse(url=f'/sponsor?msg={quote_plus(msg)}', status_code=303)


@router.post('/sponsor/manual')
async def manual_add(db: Session = Depends(get_db), channel_url: Annotated[str, Form()] = '', title: Annotated[str, Form()] = '', category: Annotated[str, Form()] = ''):
    channel_url = channel_url.strip()
    if not channel_url: return RedirectResponse(url='/sponsor/discover', status_code=303)
    username = None
    if 't.me/' in channel_url and 't.me/s/' not in channel_url:
        parts = channel_url.split('t.me/'); username = parts[-1].strip('/').split('/')[0]; channel_url = f'https://t.me/{username}'
    elif 'instagram.com/' in channel_url:
        parts = channel_url.split('instagram.com/'); username = parts[-1].strip('/').split('/')[0]; channel_url = f'https://instagram.com/{username}'
    existing = db.scalar(select(SponsorChannel).where(SponsorChannel.channel_url == channel_url))
    if existing: return RedirectResponse(url=f'/sponsor/{existing.id}', status_code=303)
    member_count = avg_views = engagement_rate = description = None
    if username and 't.me' in channel_url:
        info = await scrape_tg_preview(username)
        if not title: title = info.get('title') or username
        member_count = info.get('member_count'); avg_views = info.get('avg_views'); engagement_rate = info.get('engagement_rate'); description = info.get('description')
    ch = SponsorChannel(platform='telegram' if 't.me' in channel_url else 'instagram', channel_url=channel_url, username=username, title=title or username or 'نامشخص', description=description, member_count=member_count, avg_views=avg_views, engagement_rate=engagement_rate, category=category.strip() or None, language='fa', source='manual', status='new')
    compute_ad_score(ch); db.add(ch); db.commit(); db.refresh(ch)
    return RedirectResponse(url=f'/sponsor/{ch.id}', status_code=303)


@router.get('/sponsor/cleanup')
def cleanup_channels(db: Session = Depends(get_db)):
    bad = db.execute(select(SponsorChannel).where((SponsorChannel.language != 'fa') | (SponsorChannel.member_count < MIN_MEMBERS) | (SponsorChannel.member_count.is_(None)))).scalars().all()
    count = len(bad)
    for ch in bad: db.delete(ch)
    db.commit()
    return RedirectResponse(url=f'/sponsor?msg={quote_plus(f"{count} کانال ضعیف/خارجی حذف شد")}', status_code=303)


@router.get('/sponsor/export.csv')
def export_csv(db: Session = Depends(get_db), status: str = '', category: str = '', q: str = ''):
    stmt = select(SponsorChannel).where(SponsorChannel.language == 'fa', SponsorChannel.member_count >= MIN_MEMBERS)
    if status: stmt = stmt.where(SponsorChannel.status == status)
    if category: stmt = stmt.where(SponsorChannel.category.ilike(f'%{category}%'))
    if q: stmt = stmt.where(or_(SponsorChannel.title.ilike(f'%{q}%'), SponsorChannel.username.ilike(f'%{q}%')))
    channels = list(db.scalars(stmt.order_by(desc(SponsorChannel.ad_score))).all())
    output = io.StringIO(); output.write('\ufeff')
    w = csv.writer(output)
    w.writerow(['شناسه','پلتفرم','نام','یوزرنیم','لینک','اعضا','بازدید','تعامل','امتیاز تبلیغ','مرتبط','کیفیت','دسته','وضعیت','قیمت','تماس','توضیحات'])
    for ch in channels: w.writerow([ch.id, ch.platform, ch.title, ch.username, ch.channel_url, ch.member_count, ch.avg_views, ch.engagement_rate, ch.ad_score, ch.relevance_score, ch.quality_score, ch.category, SPONSOR_STATUS.get(ch.status, ch.status), ch.ad_price, ch.contact_method, ch.description])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename=sponsor-channels.csv'})


@router.get('/sponsor/export.xlsx')
def export_xlsx(db: Session = Depends(get_db), status: str = '', category: str = '', q: str = ''):
    from openpyxl import Workbook
    stmt = select(SponsorChannel).where(SponsorChannel.language == 'fa', SponsorChannel.member_count >= MIN_MEMBERS)
    if status: stmt = stmt.where(SponsorChannel.status == status)
    if category: stmt = stmt.where(SponsorChannel.category.ilike(f'%{category}%'))
    if q: stmt = stmt.where(or_(SponsorChannel.title.ilike(f'%{q}%'), SponsorChannel.username.ilike(f'%{q}%')))
    channels = list(db.scalars(stmt.order_by(desc(SponsorChannel.ad_score))).all())
    wb = Workbook(); ws = wb.active; ws.title = 'کانال‌های اسپانسری'
    ws.append(['شناسه','پلتفرم','نام','یوزرنیم','لینک','اعضا','بازدید','تعامل%','امتیاز تبلیغ','مرتبط','کیفیت','دسته','وضعیت','قیمت','تماس','توضیحات'])
    for ch in channels: ws.append([ch.id, ch.platform, ch.title, ch.username, ch.channel_url, ch.member_count, ch.avg_views, ch.engagement_rate, ch.ad_score, ch.relevance_score, ch.quality_score, ch.category, SPONSOR_STATUS.get(ch.status, ch.status), ch.ad_price, ch.contact_method, ch.description])
    for col in ws.columns:
        ml = max(len(str(c.value or '')) for c in col[:50])
        ws.column_dimensions[col[0].column_letter].width = min(max(ml+2, 12), 40)
    output = io.BytesIO(); wb.save(output); output.seek(0)
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=sponsor-channels.xlsx'})


@router.get('/api/sponsor')
def api_sponsor(db: Session = Depends(get_db), status: str = '', category: str = '', min_score: int = 0, limit: int = Query(50, ge=1, le=500)):
    stmt = select(SponsorChannel).where(SponsorChannel.language == 'fa', SponsorChannel.member_count >= MIN_MEMBERS)
    if status: stmt = stmt.where(SponsorChannel.status == status)
    if category: stmt = stmt.where(SponsorChannel.category.ilike(f'%{category}%'))
    if min_score: stmt = stmt.where(SponsorChannel.ad_score >= min_score)
    channels = list(db.scalars(stmt.order_by(desc(SponsorChannel.ad_score)).limit(limit)).all())
    return [{'id': ch.id, 'platform': ch.platform, 'title': ch.title, 'username': ch.username, 'url': ch.channel_url, 'member_count': ch.member_count, 'avg_views': ch.avg_views, 'engagement_rate': ch.engagement_rate, 'ad_score': ch.ad_score, 'category': ch.category, 'status': ch.status} for ch in channels]


# ============================================================
# 2. PARAMETERIZED ROUTES LAST
# ============================================================

@router.get('/sponsor/{channel_id}', response_class=HTMLResponse)
def sponsor_detail(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(SponsorChannel, channel_id)
    if not ch: raise HTTPException(404, 'کانال پیدا نشد')
    status_opts = ''.join(f'<option value="{h(k)}" {"selected" if ch.status==k else ""}>{h(v)}</option>' for k,v in SPONSOR_STATUS.items())
    ad_color = _ad_score_color(ch.ad_score)
    body = f'''<div class="card"><h1>{h(ch.title)}</h1><p class="muted">#{ch.id} | {h(ch.platform)} | افزوده: {h(fmt_dt(ch.first_seen))}</p><a class="btn" target="_blank" href="{h(ch.channel_url)}">باز کردن</a>{f'<a class="btn2 btn" target="_blank" href="https://t.me/s/{h(ch.username)}">پیش‌نمایش</a>' if ch.username and ch.platform=='telegram' else ''}<form method="post" action="/sponsor/{ch.id}/refresh" style="display:inline"><button class="btn2 btn">🔄 بروزرسانی آمار</button></form><button class="btn-danger" onclick="confirmDelete({ch.id},'{h(ch.title)}')">🗑 حذف</button></div>
    <div class="grid4"><div class="stat-card">اعضا<b style="color:#7c3aed">{_format_members(ch.member_count)}</b></div><div class="stat-card">بازدید<b style="color:#2563eb">{round(ch.avg_views or 0)}</b></div><div class="stat-card">تعامل<b style="color:#12b76a">{round(ch.engagement_rate or 0,1)}%</b></div><div class="stat-card">امتیاز تبلیغ<b style="color:{ad_color}">{ch.ad_score}</b></div></div>
    <div class="grid2"><div class="card"><h3>📊 امتیازها</h3><div style="margin-bottom:10px"><b>مرتبط‌بودن</b>{_score_bar(ch.relevance_score, color='#7c3aed')}</div><div style="margin-bottom:10px"><b>کیفیت</b>{_score_bar(ch.quality_score, color='#12b76a')}</div><div><b>امتیاز تبلیغ</b>{_score_bar(ch.ad_score, color=ad_color)}</div></div>
    <div class="card"><h3>📝 ویرایش</h3><form method="post" action="/sponsor/{ch.id}/update"><label>وضعیت<br><select name="status">{status_opts}</select></label><br><label>دسته<br><input name="category" value="{h(ch.category or '')}"></label><br><label>قیمت<br><input name="ad_price" value="{h(ch.ad_price or '')}"></label><br><label>تماس<br><input name="contact_method" value="{h(ch.contact_method or '')}"></label><br><label>یادداشت<br><textarea name="notes" style="width:100%;min-height:70px">{h(ch.notes or '')}</textarea></label><br><button>ذخیره</button></form></div></div>
    <div class="card"><h3>📄 توضیحات</h3><p>{h(ch.description or 'بدون توضیحات')}</p><p class="muted">زبان: {h(ch.language or '-')} | منبع: {h(ch.source)} | شهر: {h(ch.city or '-')}</p></div>'''
    return layout('جزئیات کانال اسپانسری', body)


@router.get('/sponsor/{channel_id}/delete')
def sponsor_delete(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(SponsorChannel, channel_id)
    if not ch: raise HTTPException(404)
    name = ch.title
    db.delete(ch); db.commit()
    return RedirectResponse(url=f'/sponsor?msg={quote_plus(f"«{name}» حذف شد")}', status_code=303)


@router.post('/sponsor/{channel_id}/update')
def sponsor_update(channel_id: int, db: Session = Depends(get_db), status: Annotated[str, Form()] = 'new', category: Annotated[str, Form()] = '', ad_price: Annotated[str, Form()] = '', contact_method: Annotated[str, Form()] = '', notes: Annotated[str, Form()] = ''):
    ch = db.get(SponsorChannel, channel_id)
    if not ch: raise HTTPException(404)
    ch.status = status; ch.category = category.strip() or None; ch.ad_price = ad_price.strip() or None; ch.contact_method = contact_method.strip() or None; ch.notes = notes.strip() or None
    compute_ad_score(ch); db.add(ch); db.commit()
    return RedirectResponse(url=f'/sponsor/{channel_id}', status_code=303)


@router.post('/sponsor/{channel_id}/refresh')
async def sponsor_refresh(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(SponsorChannel, channel_id)
    if not ch or not ch.username: return RedirectResponse(url=f'/sponsor/{channel_id}', status_code=303)
    info = await scrape_tg_preview(ch.username)
    for field in ['member_count', 'avg_views', 'engagement_rate', 'post_count', 'description']:
        if info.get(field): setattr(ch, field, info[field])
    ch.last_seen = datetime.utcnow(); compute_ad_score(ch); db.add(ch); db.commit()
    return RedirectResponse(url=f'/sponsor/{channel_id}', status_code=303)
