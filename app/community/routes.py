"""Community Finder — routes for discovering active gaming community members."""
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
from app.community.collector import discover_community_members

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')


def h(v) -> str:
    import html as m; return m.escape('' if v is None else str(v), quote=True)


def layout(title: str, body: str) -> HTMLResponse:
    css = '<style>:root{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb}*{box-sizing:border-box}body{font-family:Tahoma,Arial,sans-serif;background:linear-gradient(180deg,#fef3c7,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}.wrap{max-width:1200px;margin:auto;padding:22px}a{color:var(--primary);text-decoration:none}.hero{background:linear-gradient(135deg,#78350f,#d97706 50%,#f59e0b);color:white;border-radius:26px;padding:20px;box-shadow:0 16px 40px rgba(16,24,40,.08);margin-bottom:16px}.hero h1{margin:0;font-size:25px}.hero .muted{color:#fef3c7}.card{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045)}.card h3{margin-top:0}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.grid5{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.muted{color:var(--muted);font-size:13px;line-height:1.8}.small{font-size:12px}.btn,.action,button{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer;font-size:13px}.btn2{background:#fef3c7;color:#92400e;border:1px solid #fcd34d}.btn-danger{background:#fff1f2;color:#be123c;border:1px solid #fecdd3;font-size:11px;padding:5px 8px}.badge{display:inline-flex;background:#fef3c7;color:#92400e;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;font-weight:600}.badge.green{background:#ecfdf3;color:#027a48}.badge.blue{background:#eff6ff;color:#1d4ed8}.badge.orange{background:#fffaeb;color:#b54708}.badge.red{background:#fff1f2;color:#be123c}input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}input:focus,select:focus{border-color:#fcd34d;box-shadow:0 0 0 4px rgba(245,158,11,.12)}table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}th,td{padding:11px 12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top;font-size:13px}th{background:linear-gradient(180deg,#fef3c7,#fde68a);color:#78350f;font-weight:700;position:sticky;top:0;z-index:1}tr:hover{background:#fefce8}tr:last-child td{border-bottom:0}.stat-card{background:linear-gradient(180deg,#fff,#fef3c7);border:1px solid #fcd34d;border-radius:20px;padding:17px;text-align:center;box-shadow:0 10px 24px rgba(245,158,11,.07)}.stat-card b{display:block;font-size:28px;margin-top:7px}.hint{background:#fef3c7;border:1px solid #fcd34d;color:#78350f;border-radius:14px;padding:12px;margin-top:12px}@media(max-width:900px){.wrap{padding:12px}.grid2,.grid3,.grid4,.grid5{grid-template-columns:1fr}table{display:block;overflow-x:auto}.hero{border-radius:20px;padding:16px}}</style>'
    js = '<script>function confirmDelete(id,name){if(confirm(name+" حذف بشه؟")){window.location.href="/contacts/"+id+"/delete"}}</script>'
    nav = '<a class="btn btn2" href="/">🏠 خانه</a> <a class="btn btn2" href="/community">👥 اعضای جامعه</a> <a class="btn btn2" href="/contacts">📒 مخاطبین</a>'
    page = '<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + h(title) + '</title>' + css + '</head><body><div class="wrap"><div class="hero"><h1>' + h(title) + '</h1><div class="muted">استخراج اعضای فعال جامعه گیمینگ از آگهی‌ها، گروه‌های تلگرام و پیج‌های اینستاگرام</div>' + nav + '</div>' + body + '</div>' + js + '</body></html>'
    return HTMLResponse(page)


def _community_stats(db: Session) -> dict:
    """Count community-discovered leads."""
    sources = ['marketplace', 'telegram_group', 'instagram_community', 'forum']
    stats = {}
    for src in sources:
        stats[src] = db.scalar(select(func.count(Lead.id)).where(Lead.source == src)) or 0
    stats['total'] = sum(stats.values())
    # کسانی که شماره دارن
    stats['with_phone'] = db.scalar(select(func.count(Lead.id)).where(Lead.source.in_(sources), Lead.phone.isnot(None))) or 0
    stats['with_ig'] = db.scalar(select(func.count(Lead.id)).where(Lead.source.in_(sources), Lead.instagram.isnot(None))) or 0
    stats['with_tg'] = db.scalar(select(func.count(Lead.id)).where(Lead.source.in_(sources), Lead.telegram.isnot(None))) or 0
    return stats


@router.get('/community', response_class=HTMLResponse)
def community_index(db: Session = Depends(get_db), msg: str = Query('')):
    stats = _community_stats(db)

    # آخرین اعضای کشف شده
    recent = list(db.scalars(
        select(Lead).where(Lead.source.in_(['marketplace', 'telegram_group', 'instagram_community', 'forum']))
        .order_by(desc(Lead.first_seen)).limit(30)
    ).all())

    rows = ''
    for lead in recent:
        src_badge = {'marketplace': '🏷️ آگهی', 'telegram_group': '✈️ گروه تلگرام', 'instagram_community': '📸 اینستاگرام', 'forum': '💬 انجمن'}.get(lead.source, lead.source)
        contact = ''
        if lead.phone: contact += f'<a href="tel:{h(lead.phone)}" style="color:#059669;font-weight:700">📞 {h(lead.phone)}</a> '
        if lead.telegram: contact += f'<a target="_blank" href="{h(lead.telegram)}" style="color:#0088cc">✈️ تلگرام</a> '
        if lead.instagram: contact += f'<a target="_blank" href="{h(lead.instagram)}" style="color:#e1306c">📸 اینستاگرام</a> '
        cat = f'<span class="badge">{h(lead.category)}</span>' if lead.category else ''
        rows += f'<tr><td><b>{h(lead.title)}</b><br><span class="badge orange">{src_badge}</span> {cat}<br><span class="small muted">{h(lead.city or "")}</span></td><td>{contact or "-"}</td><td><span class="small muted">{h((lead.description or "")[:80])}</span></td><td><a class="btn" href="/contacts/{lead.id}" style="font-size:11px;padding:5px 8px">جزئیات</a></td></tr>'

    body = f'''
    <div class="grid5">
      <div class="stat-card">کل اعضا<b style="color:#d97706">{stats["total"]}</b></div>
      <div class="stat-card">🏷️ آگهی‌ها<b style="color:#2563eb">{stats["marketplace"]}</b></div>
      <div class="stat-card">✈️ گروه تلگرام<b style="color:#0088cc">{stats["telegram_group"]}</b></div>
      <div class="stat-card">📸 اینستاگرام<b style="color:#e1306c">{stats["instagram_community"]}</b></div>
      <div class="stat-card">📞 با شماره<b style="color:#059669">{stats["with_phone"]}</b></div>
    </div>

    {f'<div class="card hint">{h(msg)}</div>' if msg else ''}

    <div class="card">
      <h3>🔍 کشف اعضای جامعه گیمینگ</h3>
      <p class="muted">این ابزار از راه‌های قانونی اعضای فعال جامعه گیمینگ رو پیدا می‌کنه:</p>
      <div class="grid2" style="margin:10px 0">
        <div style="background:#fffbeb;padding:12px;border-radius:12px;border:1px solid #fcd34d">
          <b>🏷️ آگهی‌های دیوار/شیپور</b>
          <p class="small">کسانی که آگهی فروش اکانت، سی‌پی، یوسی، گیفت کارت گذاشتن</p>
        </div>
        <div style="background:#eff6ff;padding:12px;border-radius:12px;border:1px solid #bfdbfe">
          <b>✈️ گروه‌های تلگرامی</b>
          <p class="small">اعضای فعال گروه‌های خرید/فروش گیم (پیام‌های عمومی)</p>
        </div>
        <div style="background:#fdf2f8;padding:12px;border-radius:12px;border:1px solid #fbcfe8">
          <b>📸 اینستاگرام</b>
          <p class="small">پیج‌های فروش گیمینگ + کامنت‌گذارهای فعال</p>
        </div>
        <div style="background:#f0fdf4;padding:12px;border-radius:12px;border:1px solid #a7f3d0">
          <b>💬 انجمن‌ها</b>
          <p class="small">انجمن‌های گیمینگ + سایت‌های خرید/فروش</p>
        </div>
      </div>
      <form method="post" action="/community/discover">
        <h4>منابع جستجو:</h4>
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin:8px 0">
          <label><input type="checkbox" name="sources" value="marketplace" checked> 🏷️ آگهی‌های دیوار/شیپور</label>
          <label><input type="checkbox" name="sources" value="telegram_groups" checked> ✈️ گروه‌های تلگرامی</label>
          <label><input type="checkbox" name="sources" value="instagram"> 📸 اینستاگرام</label>
          <label><input type="checkbox" name="sources" value="forums"> 💬 انجمن‌ها</label>
        </div>
        <input name="city" placeholder="شهر" value="تهران" style="width:120px">
        <label>نتیجه هر جستجو <input type="number" name="max_results" value="8" min="1" max="20" style="width:80px"></label>
        <button style="background:#d97706">🚀 شروع کشف</button>
      </form>
      <div class="hint">
        <b>⚠️ نکته مهم:</b> این ابزار فقط از داده‌های عمومی و قانونی استفاده می‌کنه.
        هیچ اطلاعات خصوصی استخراج نمیشه. فقط آگهی‌های عمومی، پیام‌های عمومی گروه‌ها، و پیج‌های عمومی بررسی میشن.
      </div>
    </div>

    <div class="card">
      <h3>📋 آخرین اعضای کشف شده ({len(recent)} نفر)</h3>
      <div style="overflow-x:auto">
        <table>
          <thead><tr><th>مخاطب</th><th style="width:220px">اطلاعات تماس</th><th style="width:180px">توضیحات</th><th style="width:80px">عملیات</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4" style="text-align:center;padding:30px" class="muted">هنوز کسی کشف نشده. از فرم بالا استفاده کنید.</td></tr>'}</tbody>
        </table>
      </div>
    </div>
    '''
    return layout('👥 اعضای جامعه گیمینگ', body)


@router.post('/community/discover')
async def community_discover(
    db: Session = Depends(get_db),
    sources: Annotated[list[str] | None, Form()] = None,
    city: Annotated[str, Form()] = 'تهران',
    max_results: Annotated[int, Form()] = 8,
):
    if not sources:
        sources = ['marketplace', 'telegram_groups']
    result = await discover_community_members(db, sources=sources, city=city, max_results_per_query=max_results)
    src_names = ', '.join(result.get('by_source', {}).keys())
    msg = f"کشف انجام شد: {result['total_found']} نتیجه، {result['total_saved']} جدید، {result['total_duplicates']} تکراری. منابع: {src_names}"
    if result.get('errors'):
        msg += f' | خطاها: {len(result["errors"])}'
    return RedirectResponse(url=f'/community?msg={quote_plus(msg)}', status_code=303)
