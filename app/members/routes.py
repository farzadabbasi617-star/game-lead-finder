"""Member Finder — routes for finding gamers/users."""
from __future__ import annotations

from datetime import timezone
from typing import Annotated
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Lead
from app.members.collector import find_members, find_members_from_existing_groups

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')


def h(v) -> str:
    import html as m; return m.escape('' if v is None else str(v), quote=True)


def layout(title: str, body: str) -> HTMLResponse:
    css = '<style>:root{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb}*{box-sizing:border-box}body{font-family:Tahoma,Arial,sans-serif;background:linear-gradient(180deg,#ede9fe,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}.wrap{max-width:1300px;margin:auto;padding:22px}a{color:var(--primary);text-decoration:none}.hero{background:linear-gradient(135deg,#3b0764,#7c3aed 50%,#a78bfa);color:white;border-radius:26px;padding:20px;box-shadow:0 16px 40px rgba(16,24,40,.08);margin-bottom:16px}.hero h1{margin:0;font-size:25px}.hero .muted{color:#e9d5ff}.card{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045)}.card h3{margin-top:0}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.grid5{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.grid6{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.muted{color:var(--muted);font-size:13px;line-height:1.8}.small{font-size:12px}.btn,.action,button{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer;font-size:13px}.btn2{background:#ede9fe;color:#5b21b6;border:1px solid #c4b5fd}.btn2:hover{background:#ddd6fe}.btn-danger{background:#fff1f2;color:#be123c;border:1px solid #fecdd3;font-size:11px;padding:5px 8px}.badge{display:inline-flex;background:#ede9fe;color:#5b21b6;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;font-weight:600}.badge.green{background:#ecfdf3;color:#027a48}.badge.blue{background:#eff6ff;color:#1d4ed8}.badge.orange{background:#fffaeb;color:#b54708}.badge.purple{background:#faf5ff;color:#7c3aed}.badge.pink{background:#fdf2f8;color:#9d174d}input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}input:focus,select:focus{border-color:#c4b5fd;box-shadow:0 0 0 4px rgba(124,58,237,.12)}table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}th,td{padding:11px 12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top;font-size:13px}th{background:linear-gradient(180deg,#f5f3ff,#ede9fe);color:#4c1d95;font-weight:700;position:sticky;top:0;z-index:1}tr:hover{background:#faf5ff}tr:last-child td{border-bottom:0}.stat-card{background:linear-gradient(180deg,#fff,#f5f3ff);border:1px solid #c4b5fd;border-radius:20px;padding:17px;text-align:center;box-shadow:0 10px 24px rgba(124,58,237,.07)}.stat-card b{display:block;font-size:28px;margin-top:7px}.hint{background:#f5f3ff;border:1px solid #c4b5fd;color:#4c1d95;border-radius:14px;padding:12px;margin-top:12px}.source-card{background:linear-gradient(180deg,#fff,#faf5ff);border:1px solid #e9d5ff;border-radius:16px;padding:16px;text-align:center}.source-card h4{margin:0 0 8px}.source-card p{margin:0;font-size:12px;color:#667085}@media(max-width:900px){.wrap{padding:12px}.grid2,.grid3,.grid4,.grid5,.grid6{grid-template-columns:1fr}table{display:block;overflow-x:auto}.hero{border-radius:20px;padding:16px}}</style>'
    js = '<script>function confirmDelete(id,name){if(confirm(name+" حذف بشه؟")){window.location.href="/contacts/"+id+"/delete"}}</script>'
    nav = '<a class="btn btn2" href="/">🏠 خانه</a> <a class="btn btn2" href="/members">👥 ممبریاب</a> <a class="btn btn2" href="/contacts">📒 مخاطبین</a> <a class="btn btn2" href="/community">👥 جامعه</a>'
    page = '<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + h(title) + '</title>' + css + '</head><body><div class="wrap"><div class="hero"><h1>' + h(title) + '</h1><div class="muted">پیدا کردن گیمرها و کاربران واقعی — نه فروشگاه، نه کانال — خودِ آدم‌ها</div>' + nav + '</div>' + body + '</div>' + js + '</body></html>'
    return HTMLResponse(page)


def _member_stats(db: Session) -> dict:
    # همه لیدهایی که entity_type=gamer هستن یا از منابع ممبریاب اومدن
    gamer_filter = or_(
        Lead.entity_type == 'gamer',
        Lead.source.in_(['tg_group_member', 'marketplace_gamer', 'ig_gamer', 'forum_gamer', 'existing_group_member']),
        Lead.source.ilike('%gamer%'),
        Lead.source.ilike('%group_member%'),
    )
    total = db.scalar(select(func.count(Lead.id)).where(gamer_filter)) or 0
    with_phone = db.scalar(select(func.count(Lead.id)).where(gamer_filter, Lead.phone.isnot(None))) or 0
    with_ig = db.scalar(select(func.count(Lead.id)).where(gamer_filter, Lead.instagram.isnot(None))) or 0
    with_tg = db.scalar(select(func.count(Lead.id)).where(gamer_filter, Lead.telegram.isnot(None))) or 0
    # گروه‌های تلگرام توی دیتابیس
    tg_groups = db.scalar(select(func.count(Lead.id)).where(Lead.telegram.ilike('%t.me%') | Lead.url.ilike('%t.me%'))) or 0
    # شمارش بر اساس منبع
    by_src = {}
    for src in ['tg_group_member', 'marketplace_gamer', 'ig_gamer', 'forum_gamer', 'existing_group_member']:
        by_src[src] = db.scalar(select(func.count(Lead.id)).where(Lead.source == src)) or 0
    return {'total': total, 'by_src': by_src, 'phone': with_phone, 'ig': with_ig, 'tg': with_tg, 'groups': tg_groups}


@router.get('/members', response_class=HTMLResponse)
def members_index(db: Session = Depends(get_db), msg: str = Query('')):
    stats = _member_stats(db)

    # همه لیدهایی که entity_type=gamer هستن یا از منابع ممبریاب اومدن
    gamer_filter = or_(
        Lead.entity_type == 'gamer',
        Lead.source.in_(['tg_group_member', 'marketplace_gamer', 'ig_gamer', 'forum_gamer', 'existing_group_member']),
        Lead.source.ilike('%gamer%'),
        Lead.source.ilike('%group_member%'),
    )
    recent = list(db.scalars(
        select(Lead).where(gamer_filter)
        .order_by(desc(Lead.first_seen)).limit(50)
    ).all())

    rows = ''
    for lead in recent:
        src_badge = {'tg_group_member': '✈️ گروه تلگرام', 'marketplace_gamer': '🏷️ آگهی', 'ig_gamer': '📸 اینستاگرام', 'forum_gamer': '💬 انجمن', 'existing_group_member': '🔍 اسکن گروه'}.get(lead.source, lead.source)
        contact = ''
        if lead.phone: contact += f'<a href="tel:{h(lead.phone)}" style="color:#059669;font-weight:700">📞 {h(lead.phone)}</a> '
        if lead.telegram: contact += f'<a target="_blank" href="{h(lead.telegram)}" style="color:#0088cc">✈️ تلگرام</a> '
        if lead.instagram: contact += f'<a target="_blank" href="{h(lead.instagram)}" style="color:#e1306c">📸 اینستاگرام</a> '
        cat = f'<span class="badge purple">{h(lead.category)}</span>' if lead.category else ''
        rows += f'<tr><td><b>{h(lead.title)}</b><br><span class="badge orange">{src_badge}</span> {cat}</td><td>{contact or "-"}</td><td><span class="small muted">{h((lead.description or "")[:60])}</span></td><td><a class="btn" href="/contacts/{lead.id}" style="font-size:11px;padding:5px 8px">جزئیات</a></td></tr>'

    body = f'''
    <div class="grid6">
      <div class="stat-card">کل ممبرها<b style="color:#7c3aed">{stats["total"]}</b></div>
      <div class="stat-card">✈️ از گروه تلگرام<b style="color:#0088cc">{stats["by_src"].get("tg_group_member",0) + stats["by_src"].get("existing_group_member",0)}</b></div>
      <div class="stat-card">🏷️ از آگهی<b style="color:#2563eb">{stats["by_src"].get("marketplace_gamer",0)}</b></div>
      <div class="stat-card">📸 از اینستاگرام<b style="color:#e1306c">{stats["by_src"].get("ig_gamer",0)}</b></div>
      <div class="stat-card">📞 با شماره<b style="color:#059669">{stats["phone"]}</b></div>
      <div class="stat-card">✈️ با تلگرام<b style="color:#0088cc">{stats["tg"]}</b></div>
    </div>

    {f'<div class="card hint">{h(msg)}</div>' if msg else ''}

    <!-- کشف جدید -->
    <div class="card">
      <h3>🔍 کشف ممبرهای جدید</h3>
      <p class="muted">از این روش‌ها گیمرهای واقعی رو پیدا کن:</p>
      <div class="grid4" style="margin:12px 0">
        <div class="source-card"><h4>✈️ گروه‌های تلگرامی</h4><p>اسکرپ پیام‌های عمومی گروه‌های گیمینگ<br>→ یوزرنیم + شماره تلفن</p></div>
        <div class="source-card"><h4>🏷️ آگهی‌های دیوار/شیپور</h4><p>کسایی که آگهی خرید/فروش گیم گذاشتن<br>→ شماره + آیدی</p></div>
        <div class="source-card"><h4>📸 اینستاگرام</h4><p>پیج‌های گیمری + فروشندگان<br>→ آیدی اینستاگرام</p></div>
        <div class="source-card"><h4>🔍 اسکن گروه‌های موجود</h4><p>از لینک‌های تلگرام توی دیتابیس<br>→ اعضای فعال</p></div>
      </div>
      <form method="post" action="/members/discover">
        <div style="display:flex;gap:14px;flex-wrap:wrap;margin:10px 0">
          <label><input type="checkbox" name="sources" value="telegram_groups" checked> ✈️ گروه‌های تلگرامی</label>
          <label><input type="checkbox" name="sources" value="marketplace" checked> 🏷️ آگهی‌های دیوار/شیپور</label>
          <label><input type="checkbox" name="sources" value="instagram"> 📸 اینستاگرام</label>
          <label><input type="checkbox" name="sources" value="forums"> 💬 انجمن‌ها</label>
        </div>
        <input name="city" placeholder="شهر" value="تهران" style="width:120px">
        <label>نتیجه هر جستجو <input type="number" name="max_per_query" value="8" min="1" max="20" style="width:80px"></label>
        <button style="background:#7c3aed">🚀 شروع ممبریابی</button>
      </form>
    </div>

    <!-- اسکن گروه‌های موجود -->
    <div class="card">
      <h3>🔍 اسکن گروه‌های تلگرامی موجود در دیتابیس</h3>
      <p class="muted">از لینک‌های تلگرامی که قبلاً ذخیره کردی، اعضای فعال رو استخراج می‌کنه. ({stats["groups"]} لینک تلگرام توی دیتابیس)</p>
      <form method="post" action="/members/scan-existing">
        <button style="background:#0088cc">🔍 اسکن همه گروه‌های موجود</button>
      </form>
    </div>

    <!-- لیست -->
    <div class="card">
      <h3>👥 آخرین ممبرهای پیدا شده ({len(recent)} نفر)</h3>
      <div style="overflow-x:auto">
        <table>
          <thead><tr><th>فرد</th><th style="width:250px">اطلاعات تماس</th><th style="width:150px">توضیحات</th><th style="width:80px">عملیات</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4" style="text-align:center;padding:30px" class="muted">هنوز ممبری پیدا نشده. از فرم بالا شروع کن!</td></tr>'}</tbody>
        </table>
      </div>
    </div>

    <div class="hint">
      <b>💡 نکته:</b> برای نتیجه بهتر، اول <a href="/keywords">کلمات کلیدی</a> رو تنظیم کن و مطمئن شو حداقل یک API Key (OpenRouter/Tavily/Groq) تنظیم شده.
      همه داده‌ها از منابع عمومی و قانونی استخراج میشن.
    </div>
    '''
    return layout('👥 ممبریاب گیمینگ', body)


@router.post('/members/discover')
async def members_discover(
    db: Session = Depends(get_db),
    sources: Annotated[list[str] | None, Form()] = None,
    city: Annotated[str, Form()] = 'تهران',
    max_per_query: Annotated[int, Form()] = 8,
):
    if not sources: sources = ['telegram_groups', 'marketplace']
    result = await find_members(db, sources=sources, city=city, max_per_query=max_per_query)
    src_names = ', '.join(result.get('by_source', {}).keys())
    msg = f"ممبریابی: {result['total_found']} نفر پیدا شد، {result['saved']} جدید ذخیره شد، {result['duplicates']} تکراری. منابع: {src_names}"
    if result.get('errors'): msg += f' | خطاها: {len(result["errors"])}'
    return RedirectResponse(url=f'/members?msg={quote_plus(msg)}', status_code=303)


@router.post('/members/scan-existing')
async def members_scan(db: Session = Depends(get_db)):
    result = await find_members_from_existing_groups(db)
    msg = f"اسکن {result['groups_scanned']} گروه انجام شد: {result['members_found']} عضو پیدا شد، {result['saved']} جدید ذخیره شد."
    return RedirectResponse(url=f'/members?msg={quote_plus(msg)}', status_code=303)
