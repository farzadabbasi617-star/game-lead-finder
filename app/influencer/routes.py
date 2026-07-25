"""Influencer Finder — UI routes and API.

Route order: static paths FIRST, then parameterized /{id} last.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.influencer.models import Influencer
from app.influencer.collector import discover_influencers, scrape_instagram_profile, scrape_telegram_channel, _detect_niche, _detect_game_tags, recheck_all_activity
from app.influencer.activity import check_activity, ACTIVITY_WINDOW_DAYS
from app.influencer.scoring import compute_influencer_score

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')

INF_STATUS = {'discovered': 'کشف شده', 'researching': 'در حال بررسی', 'contacted': 'تماس گرفته شده', 'negotiating': 'در حال مذاکره', 'collaborating': 'همکاری فعال', 'rejected': 'رد شده'}
TIER_LABELS = {'nano': ('⚪', 'نانو (زیر ۱K)', '#9ca3af'), 'micro': ('🔵', 'مایکرو (۱-۱۰K)', '#2563eb'), 'mid': ('🟢', 'مید (۱۰-۱۰۰K)', '#12b76a'), 'macro': ('🟡', 'ماکرو (۱۰۰K-1M)', '#f79009'), 'mega': ('🔴', 'مگا (بالای 1M)', '#f04438')}
PLATFORM_META = {'instagram': ('📸', 'اینستاگرام', '#e1306c'), 'telegram': ('✈️', 'تلگرام', '#0088cc')}
ENTITY_META = {
    'channel': ('📢', 'کانال', '#0088cc'),
    'group': ('👥', 'گروه', '#22c55e'),
    'profile': ('📸', 'پیج', '#e1306c'),
    'bot': ('🤖', 'بات', '#94a3b8'),
    'user': ('👤', 'کاربر', '#94a3b8'),
}

# میکرو اینفلوئنسر: از ۱۰۰۰ فالوور به بالا. برای اینکه چیزی پیدا بشه، در حالت نمایش کاهش دادیم به ۵۰۰
# چون خیلی از پیج‌های تازه‌کار زیر ۱۰۰۰ هستن ولی مرتبطن
MIN_FOLLOWERS = 500  # حداقل فالوور برای نمایش (قبلاً ۱۰۰۰ بود)


def h(v) -> str:
    import html as html_mod
    return html_mod.escape('' if v is None else str(v), quote=True)


def _format_count(count: int | None) -> str:
    if not count: return '-'
    if count >= 1_000_000: return f'{count / 1_000_000:.1f}M'
    if count >= 1_000: return f'{count / 1_000:.1f}K'
    return str(count)


def _score_bar(score: int, color: str = '#2563eb') -> str:
    pct = min(score, 100)
    return (f'<div style="display:flex;align-items:center;gap:6px"><div style="flex:1;background:#eef2ff;border-radius:999px;height:14px;overflow:hidden;min-width:50px"><div style="width:{pct}%;height:100%;background:{color};border-radius:999px"></div></div><b style="min-width:24px;font-size:12px">{score}</b></div>')


def _collab_color(score: int) -> str:
    if score >= 70: return '#12b76a'
    if score >= 45: return '#f79009'
    if score >= 25: return '#2563eb'
    return '#9ca3af'


def _platform_icon(platform: str) -> str:
    return {'instagram': '📸', 'telegram': '✈️', 'youtube': '🎬', 'tiktok': '🎵'}.get(platform, '📱')


def layout(title: str, body: str) -> HTMLResponse:
    css = '''<style>
      :root{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb;--shadow:0 16px 40px rgba(16,24,40,.08)}
      *{box-sizing:border-box}body{font-family:Tahoma,Arial,sans-serif;background:radial-gradient(circle at top right,rgba(236,72,153,.08),transparent 34%),linear-gradient(180deg,#fdf2f8,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}
      .wrap{max-width:1400px;margin:auto;padding:22px}a{color:var(--primary);text-decoration:none}
      .hero{background:linear-gradient(135deg,#831843,#be185d 50%,#ec4899);color:white;border-radius:26px;padding:20px;box-shadow:var(--shadow);margin-bottom:16px}
      .hero h1{margin:0;font-size:25px}.hero .muted{color:#fce7f3}
      .card{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045);backdrop-filter:blur(12px)}.card h3{margin-top:0}
      .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.grid5{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}
      .muted{color:var(--muted);font-size:13px;line-height:1.8}.small{font-size:12px}
      .btn,.action,button{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer;font-size:13px}
      .btn2{background:#fdf2f8;color:#be185d;border:1px solid #fbcfe8}.btn2:hover{background:#fce7f3}
      .btn-danger{background:#fff1f2;color:#be123c;border:1px solid #fecdd3;font-size:11px;padding:5px 8px}.btn-danger:hover{background:#fecdd3}
      .badge{display:inline-flex;background:#fdf2f8;color:#9d174d;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;font-weight:600}
      .badge.green{background:#ecfdf3;color:#027a48}.badge.orange{background:#fffaeb;color:#b54708}.badge.red{background:#fff1f2;color:#be123c}.badge.blue{background:#eff6ff;color:#1d4ed8}.badge.purple{background:#faf5ff;color:#7c3aed}
      input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}
      input:focus,select:focus{border-color:#f9a8d4;box-shadow:0 0 0 4px rgba(236,72,153,.12)}
      table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}
      th,td{padding:11px 12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top;font-size:13px}
      th{background:linear-gradient(180deg,#fdf2f8,#fce7f3);color:#831843;font-weight:700;position:sticky;top:0;z-index:1}
      tr:hover{background:#fdf2f8}tr:last-child td{border-bottom:0}
      .inf-row{border-right:3px solid transparent;transition:.15s}.inf-row:hover{border-right-color:#ec4899}
      .url{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;direction:ltr;color:#be185d}
      .hint{background:#fdf2f8;border:1px solid #fbcfe8;color:#831843;border-radius:14px;padding:12px;margin-top:12px}
      .stat-card{background:linear-gradient(180deg,#fff,#fdf2f8);border:1px solid #fbcfe8;border-radius:20px;padding:17px;text-align:center;box-shadow:0 10px 24px rgba(236,72,153,.07)}.stat-card b{display:block;font-size:28px;margin-top:7px}
      .folder-section{border:1px solid var(--line);border-radius:18px;margin:14px 0;background:white;overflow:hidden}
      .folder-section summary{cursor:pointer;list-style:none;padding:16px 18px;background:linear-gradient(180deg,#fdf2f8,#fce7f3);display:flex;align-items:center;justify-content:space-between;gap:10px}
      .folder-section summary::-webkit-details-marker{display:none}
      .folder-title{font-weight:800;font-size:16px}.folder-count{background:#be185d;color:#fff;border-radius:999px;padding:5px 10px;font-size:12px;font-weight:700}
      @media(max-width:900px){.wrap{padding:12px}.grid2,.grid3,.grid4,.grid5{grid-template-columns:1fr}table{display:block;overflow-x:auto}.hero{border-radius:20px;padding:16px}}
    </style>'''
    js = '''<script>
    document.addEventListener('click',async e=>{
      if(e.target.classList.contains('copy')){const t=e.target.dataset.text||'';try{await navigator.clipboard.writeText(t);e.target.textContent='کپی شد ✅'}catch(_){alert(t)}setTimeout(()=>e.target.textContent='کپی',1200)}
    });
    function confirmDelete(id,name){if(confirm('آیا مطمئنی میخوای «'+name+'» رو حذف کنی؟')){window.location.href='/influencer/'+id+'/delete'}}
    </script>'''
    return HTMLResponse(f'<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title>{css}</head><body><div class="wrap"><div class="hero"><h1>{h(title)}</h1><div class="muted">کشف و مدیریت اینفلوئنسرهای گیمینگ ایرانی — فقط بالای ۱۰۰۰ فالوور</div><a class="btn btn2" href="/">بانک لیدها</a> <a class="btn btn2" href="/sponsor">🎯 اسپانسری</a> <a class="btn btn2" href="/influencer">🌟 اینفلوئنسرها</a> <a class="btn btn2" href="/influencer/discover">🔍 کشف اینفلوئنسر</a></div>{body}</div>{js}</body></html>')


def _stats(db: Session, active_only: bool = True) -> dict:
    base = select(Influencer).where(Influencer.language == 'fa')
    if active_only:
        base = base.where(Influencer.is_active == True)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_all = db.scalar(select(func.count()).select_from(select(Influencer).subquery())) or 0
    inactive = db.scalar(select(func.count()).select_from(select(Influencer).where(Influencer.is_active == False).subquery())) or 0
    by_platform = {}
    for p in ['instagram', 'telegram']:
        by_platform[p] = db.scalar(select(func.count()).select_from(base.where(Influencer.platform == p).subquery())) or 0
    # آمار بر اساس نوع entity
    by_entity = {}
    for e in ['channel', 'group', 'profile']:
        by_entity[e] = db.scalar(select(func.count()).select_from(base.where(Influencer.entity_type == e).subquery())) or 0
    high_score = db.scalar(select(func.count()).select_from(base.where(Influencer.collab_score >= 50).subquery())) or 0
    collaborating = db.scalar(select(func.count()).select_from(base.where(Influencer.status == 'collaborating').subquery())) or 0
    by_tier = {}
    for t in ['micro', 'mid', 'macro', 'mega']:
        by_tier[t] = db.scalar(select(func.count()).select_from(base.where(Influencer.tier == t).subquery())) or 0
    return {'total': total, 'total_all': total_all, 'inactive': inactive, 'activity_window_days': 30,
            'by_platform': by_platform, 'by_entity': by_entity, 'high_score': high_score,
            'collaborating': collaborating, 'by_tier': by_tier}


def _build_query(db: Session, active_only: bool = True, **kwargs):
    stmt = select(Influencer).where(Influencer.language == 'fa')
    if active_only:
        stmt = stmt.where(Influencer.is_active == True)
    if kwargs.get('platform'): stmt = stmt.where(Influencer.platform == kwargs['platform'])
    if kwargs.get('status'): stmt = stmt.where(Influencer.status == kwargs['status'])
    return stmt.order_by(desc(Influencer.collab_score))


def _has_persian(text: str) -> bool:
    return bool(re.search(r'[\u0600-\u06FF]', text))


def _render_row(inf: Influencer) -> str:
    cc = _collab_color(inf.collab_score)
    tier_info = TIER_LABELS.get(inf.tier or '', ('❓', inf.tier or '-', '#9ca3af'))
    status_cls = {'discovered': '', 'researching': 'blue', 'contacted': 'orange', 'negotiating': 'purple', 'collaborating': 'green', 'rejected': 'red'}.get(inf.status, '')
    gt = ''.join(f'<span class="badge" style="font-size:11px">{h(t.strip())}</span>' for t in (inf.game_tags or '').split(',') if t.strip())

    # نشان فعال بودن
    if inf.is_active:
        if inf.last_post_at:
            from datetime import datetime, timezone
            try:
                post_dt = inf.last_post_at if inf.last_post_at.tzinfo else inf.last_post_at.replace(tzinfo=timezone.utc)
                days_ago = (datetime.now(timezone.utc) - post_dt).days
                activity_badge = f'<span class="badge green">✅ فعال · {days_ago}d</span>'
            except Exception:
                activity_badge = '<span class="badge green">✅ فعال</span>'
        else:
            activity_badge = '<span class="badge green">✅ فعال</span>'
    else:
        activity_badge = f'<span class="badge red" title="{h(inf.activity_reason or "")}">⚪ غیرفعال</span>'

    # نشان نوع entity (کانال / گروه / پیج)
    entity_type = inf.entity_type or ('profile' if inf.platform == 'instagram' else 'channel')
    e_icon, e_label, e_color = ENTITY_META.get(entity_type, ('❓', entity_type, '#666'))
    entity_badge = f'<span class="badge" style="background:{e_color}22;color:{e_color};font-weight:700">{e_icon} {e_label}</span>'

    # followers label بر اساس نوع
    followers_label = {'channel': 'مشترک', 'group': 'عضو', 'profile': 'فالوور'}.get(entity_type, 'دنبال‌کننده')

    return f'''<tr class="inf-row"><td><div style="font-weight:800;font-size:15px;margin-bottom:3px">{e_icon} {h(inf.display_name)}</div><a class="url" target="_blank" href="{h(inf.profile_url)}">{h(inf.username or inf.profile_url)}</a><div class="small muted" style="margin-top:3px">{h((inf.bio or '')[:100])}</div><div style="margin-top:5px">{entity_badge}{activity_badge}<span class="badge {status_cls}">{h(INF_STATUS.get(inf.status, inf.status))}</span>{f'<span class="badge purple">{h(inf.niche)}</span>' if inf.niche else ''}{f'<span class="badge" style="background:#f0fdf4;color:#166534">{tier_info[0]} {tier_info[1]}</span>' if inf.tier else ''}</div><div style="margin-top:3px">{gt}</div></td><td style="text-align:center"><div style="font-size:22px;font-weight:800;color:#be185d">{_format_count(inf.followers)}</div><div class="small muted">{followers_label}</div><div style="margin-top:6px" class="small"><div>👁 {round(inf.avg_views or 0)} بازدید</div><div>❤️ {round(inf.avg_likes or 0)} لایک</div><div>💬 {round(inf.avg_comments or 0)} کامنت</div><div>📊 {round(inf.engagement_rate or 0, 1)}% تعامل</div></div></td><td><div style="margin-bottom:5px"><span class="small">مرتبط‌بودن</span>{_score_bar(inf.relevance_score, '#be185d')}</div><div style="margin-bottom:5px"><span class="small">کیفیت</span>{_score_bar(inf.quality_score, '#12b76a')}</div><div><span class="small">امتیاز همکاری</span>{_score_bar(inf.collab_score, cc)}</div></td><td><div class="small muted">قیمت: {h(inf.collab_price or '-')}</div><div class="small muted">نوع: {h(inf.collab_type or '-')}</div></td><td><a class="btn" href="/influencer/{inf.id}" style="font-size:11px;padding:6px 10px">جزئیات</a><a class="btn2 btn" target="_blank" href="{h(inf.profile_url)}" style="font-size:11px;padding:6px 10px">پروفایل</a><button class="btn-danger" onclick="confirmDelete({inf.id},'{h(inf.display_name)}')">🗑 حذف</button></td></tr>'''


# ============================================================
# 1. STATIC ROUTES FIRST
# ============================================================

@router.get('/influencer', response_class=HTMLResponse)
def influencer_index(db: Session = Depends(get_db), platform: str = Query(''), entity: str = Query(''), niche: str = Query(''), tier: str = Query(''), q: str = Query(''), sort: str = Query('collab_score'), min_followers: int = Query(0, ge=0), min_score: int = Query(0, ge=0, le=100), status: str = Query(''), limit: int = Query(200, ge=1, le=500), msg: str = Query(''), show_inactive: str = Query('')):
    active_only = show_inactive != 'yes'
    stats = _stats(db, active_only=active_only)

    # فقط ایرانی + پیش‌فرض فقط فعال
    stmt = select(Influencer).where(Influencer.language == 'fa')
    if active_only:
        stmt = stmt.where(Influencer.is_active == True)
    if min_followers:
        stmt = stmt.where(Influencer.followers >= min_followers)
    if platform: stmt = stmt.where(Influencer.platform == platform)
    if entity: stmt = stmt.where(Influencer.entity_type == entity)
    if niche: stmt = stmt.where(Influencer.niche.ilike(f'%{niche}%'))
    if tier: stmt = stmt.where(Influencer.tier == tier)
    if status: stmt = stmt.where(Influencer.status == status)
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(Influencer.display_name.ilike(like), Influencer.username.ilike(like), Influencer.bio.ilike(like), Influencer.game_tags.ilike(like)))
    if min_score: stmt = stmt.where(Influencer.collab_score >= min_score)

    sort_map = {'collab_score': desc(Influencer.collab_score), 'followers': desc(Influencer.followers), 'engagement': desc(Influencer.engagement_rate), 'newest': desc(Influencer.first_seen), 'relevance': desc(Influencer.relevance_score)}
    stmt = stmt.order_by(sort_map.get(sort, desc(Influencer.collab_score)))
    influencers = list(db.scalars(stmt.limit(limit)).all())

    # ── پوشه‌بندی بر اساس پلتفرم + نوع entity ──
    # ۳ پوشه: پیج اینستاگرام / کانال تلگرام / گروه تلگرام
    folders: dict[str, list] = {
        'instagram_profile': [],
        'telegram_channel': [],
        'telegram_group': [],
    }
    for inf in influencers:
        et = inf.entity_type or ('profile' if inf.platform == 'instagram' else 'channel')
        if inf.platform == 'instagram':
            folders['instagram_profile'].append(inf)
        elif inf.platform == 'telegram':
            if et == 'group':
                folders['telegram_group'].append(inf)
            else:
                folders['telegram_channel'].append(inf)

    folder_meta = {
        'instagram_profile': ('📸', 'پیج‌های اینستاگرام', '#e1306c'),
        'telegram_channel': ('📢', 'کانال‌های تلگرام', '#0088cc'),
        'telegram_group': ('👥', 'گروه‌های تلگرام', '#22c55e'),
    }

    folder_sections = ''
    for fkey in ['instagram_profile', 'telegram_channel', 'telegram_group']:
        items = folders.get(fkey, [])
        if not items:
            continue
        icon, label, color = folder_meta[fkey]
        rows = ''.join(_render_row(inf) for inf in items)
        folder_sections += f'''
        <details id="folder-{fkey}" class="folder-section" open>
          <summary>
            <div><div class="folder-title" style="color:{color}">{icon} پوشه {label}</div></div>
            <div><span class="folder-count" style="background:{color}">{len(items)}</span></div>
          </summary>
          <div style="padding:0 0 8px">
            <table><thead><tr><th>اینفلوئنسر</th><th style="text-align:center;width:110px">آمار</th><th style="width:180px">امتیازها</th><th style="width:120px">همکاری</th><th style="width:130px">عملیات</th></tr></thead><tbody>{rows}</tbody></table>
          </div>
        </details>'''

    # ── فیلترها ──
    sort_opts = ''.join(f'<option value="{k}" {"selected" if sort==k else ""}>{v}</option>' for k,v in [('collab_score','بهترین امتیاز'),('followers','بیشترین فالوور'),('engagement','بیشترین تعامل'),('newest','جدیدترین'),('relevance','مرتبط‌ترین')])
    status_opts = '<option value="">همه وضعیت‌ها</option>' + ''.join(f'<option value="{k}" {"selected" if status==k else ""}>{v}</option>' for k,v in INF_STATUS.items())
    tier_opts = '<option value="">همه سطوح</option>' + ''.join(f'<option value="{k}" {"selected" if tier==k else ""}>{v[1]}</option>' for k,v in TIER_LABELS.items() if k != 'nano')
    platform_opts = '<option value="">همه پلتفرم‌ها</option>' + ''.join(f'<option value="{p}" {"selected" if platform==p else ""}>{PLATFORM_META[p][1]}</option>' for p in ['instagram', 'telegram'])
    entity_opts = ('<option value="">همه انواع</option>'
                   f'<option value="profile" {"selected" if entity=="profile" else ""}>📸 پیج اینستاگرام</option>'
                   f'<option value="channel" {"selected" if entity=="channel" else ""}>📢 کانال تلگرام</option>'
                   f'<option value="group" {"selected" if entity=="group" else ""}>👥 گروه تلگرام</option>')
    tier_cards = ''.join(f'<div class="stat-card"><span style="font-size:18px">{TIER_LABELS[t][0]}</span><div class="small">{TIER_LABELS[t][1]}</div><b style="color:{TIER_LABELS[t][2]}">{stats["by_tier"][t]}</b></div>' for t in ['micro','mid','macro','mega'] if stats['by_tier'].get(t, 0) > 0)

    body = f'''
    <div class="grid5">
      <div class="stat-card">
        <span class="small">✅ فعال ({stats["activity_window_days"] if "activity_window_days" in stats else 30} روز)</span>
        <b style="color:#12b76a">{stats["total"]}</b>
        <div class="small muted">از کل {stats["total_all"]}</div>
      </div>
      <div class="stat-card">📸 پیج اینستا<b style="color:#e1306c">{stats["by_entity"].get("profile", stats["by_platform"].get("instagram", 0))}</b></div>
      <div class="stat-card">📢 کانال تلگرام<b style="color:#0088cc">{stats["by_entity"].get("channel", 0)}</b></div>
      <div class="stat-card">👥 گروه تلگرام<b style="color:#22c55e">{stats["by_entity"].get("group", 0)}</b></div>
      <div class="stat-card">🤝 همکاری فعال<b style="color:#f79009">{stats["collaborating"]}</b></div>
    </div>
    {f'<div class="grid5">{tier_cards}</div>' if tier_cards else ''}
    {f'<div class="card hint">{h(msg)}</div>' if msg else ''}
    <div class="card">
      <h3>🔍 جستجو و فیلتر</h3>
      <form method="get" action="/influencer">
        <input name="q" placeholder="جستجو" value="{h(q)}" style="min-width:240px">
        <select name="entity">{entity_opts}</select>
        <select name="platform">{platform_opts}</select>
        <select name="tier">{tier_opts}</select>
        <select name="status">{status_opts}</select>
        <input name="niche" placeholder="نیچ" value="{h(niche)}" style="width:100px">
        <select name="sort">{sort_opts}</select>
        <label style="display:inline-flex;align-items:center;background:#fef3c7;padding:6px 10px;border-radius:8px;margin:4px">
          <input type="checkbox" name="show_inactive" value="yes" {'checked' if show_inactive == 'yes' else ''} style="margin:0 4px">
          🟡 نمایش غیرفعال‌ها ({stats["inactive"]})
        </label>
        <button>فیلتر</button>
      </form>
    </div>
    <div class="card">
      <h3>🌟 اینفلوئنسرهای ایرانی {'(فقط فعال)' if not show_inactive == 'yes' else '(همه)'} - {len(influencers)} نفر</h3>
      {folder_sections or '<div style="text-align:center;padding:30px" class="muted">هنوز اینفلوئنسری کشف نشده. از دکمه «کشف اینفلوئنسر» استفاده کنید.</div>'}
    </div>
    <div class="card">
      <a class="btn" href="/influencer/export.xlsx">📥 Excel</a>
      <a class="btn2 btn" href="/influencer/export.csv">📥 CSV</a>
      <a class="btn2 btn" href="/influencer/recheck" onclick="return confirm('چک مجدد فعالیت همه اینفلوئنسرها؟ ممکنه چند دقیقه طول بکشه')">🔄 چک مجدد فعالیت همه</a>
      <a class="btn-danger" href="/influencer/cleanup" onclick="return confirm('حذف همه پروفایل‌های غیرفعال؟')">🧹 حذف غیرفعال‌ها</a>
    </div>'''
    return layout('🌟 اینفلوئنسرهای گیمینگ ایرانی', body)


@router.get('/influencer/discover', response_class=HTMLResponse)
def discover_page():
    from app.influencer.collector import INSTAGRAM_QUERIES, TELEGRAM_QUERIES, SEED_INSTAGRAM, SEED_TELEGRAM
    from app.config import get_settings
    settings = get_settings()

    backends_status = []
    for name, key_attr in [('SerpAPI', 'serpapi_key'), ('Serper', 'serper_api_key'),
                            ('Brave', 'brave_search_api_key'), ('SearchAPI', 'searchapi_key'),
                            ('Tavily', 'tavily_api_key'), ('OpenRouter', 'openrouter_api_key'),
                            ('Groq', 'groq_api_key')]:
        active = bool(getattr(settings, key_attr, None))
        icon = '✅' if active else '⚪'
        backends_status.append(f'{icon} {name}')
    if settings.google_cse_api_key and settings.google_cse_id:
        backends_status.append('✅ Google CSE')
    backends_status.append('🆓 DuckDuckGo (بدون کلید)')

    ig_cb = ''.join(f'<label style="display:block;margin:3px 0"><input type="checkbox" name="queries" value="{h(q)}" checked> {h(q)}</label>' for q in INSTAGRAM_QUERIES)
    tg_cb = ''.join(f'<label style="display:block;margin:3px 0"><input type="checkbox" name="queries" value="{h(q)}" checked> {h(q)}</label>' for q in TELEGRAM_QUERIES)

    body = f'''
    <div class="card">
      <h3>🔍 کشف اینفلوئنسرهای گیمینگ ایرانی</h3>
      <p class="muted">
        <b>حداقل ۵۰۰ فالوور</b> ذخیره میشن. اینفلوئنسرها از دو منبع:<br>
        ۱) <b>جستجوی وب</b> با کوئری‌های پایین<br>
        ۲) <b>لیست seed</b> از {len(SEED_INSTAGRAM)} پیج اینستا + {len(SEED_TELEGRAM)} کانال تلگرام شناخته‌شده ایرانی
      </p>
      <div class="hint">
        <b>🔌 Search Backends:</b><br>{" · ".join(backends_status)}
        <br><small>حتی اگه هیچ API key نداشته باشی، DuckDuckGo و لیست seed کار میکنن.</small>
      </div>
      <form method="post" action="/influencer/discover" style="margin-top:14px">
        <div style="margin-bottom:12px;background:#dcfce7;padding:10px;border-radius:8px">
          <label style="display:block;margin-bottom:6px">
            <input type="checkbox" name="check_activity" value="yes" checked>
            <b>✅ فقط پیج/کانال‌های فعال (پست جدید در N روز اخیر)</b>
          </label>
          <label style="display:block">
            پنجره زمانی فعالیت:
            <input type="number" name="activity_window" value="30" min="1" max="365" style="width:70px"> روز
            <small class="muted">(پیشنهاد: ۳۰ روز)</small>
          </label>
        </div>
        <div style="margin-bottom:12px">
          <label><input type="checkbox" name="use_seed" value="yes" checked> ✨ استفاده از لیست seed (توصیه شده)</label>
        </div>
        <div style="margin-bottom:12px;background:#eff6ff;padding:10px;border-radius:8px">
          <b>📂 نوع entity تلگرام:</b><br>
          <label><input type="checkbox" name="include_channels" value="yes" checked> 📢 کانال‌ها</label>
          <label style="margin-right:20px"><input type="checkbox" name="include_groups" value="yes" checked> 👥 گروه‌ها</label>
          <br><small class="muted">پیج اینستاگرام همیشه شامله. بات/کاربر خودکار رد میشه.</small>
        </div>
        <div class="grid2">
          <div>
            <h4>📸 کوئری‌های اینستاگرام ({len(INSTAGRAM_QUERIES)})</h4>
            <div style="max-height:260px;overflow:auto;padding:8px;background:#fdf2f8;border-radius:8px">{ig_cb}</div>
          </div>
          <div>
            <h4>✈️ کوئری‌های تلگرام ({len(TELEGRAM_QUERIES)})</h4>
            <div style="max-height:260px;overflow:auto;padding:8px;background:#fdf2f8;border-radius:8px">{tg_cb}</div>
          </div>
        </div>
        <div style="margin-top:14px">
          <label>پلتفرم:
            <select name="platform">
              <option value="both">اینستا + تلگرام</option>
              <option value="instagram">فقط اینستا</option>
              <option value="telegram">فقط تلگرام</option>
            </select>
          </label>
          <label style="margin-right:10px">نتیجه هر جستجو:
            <input type="number" name="max_results" value="8" min="1" max="20" style="width:70px">
          </label>
        </div>
        <div style="margin-top:14px">
          <button style="font-size:15px;padding:11px 20px">🚀 شروع کشف اینفلوئنسر</button>
          <small class="muted" style="margin-right:10px">۳۰-۹۰ ثانیه طول میکشه</small>
        </div>
      </form>
    </div>
    <div class="card">
      <h3>➕ افزودن دستی</h3>
      <p class="muted">اگر پیج/کانال خاصی میشناسی:</p>
      <form method="post" action="/influencer/manual">
        <input name="profile_url" placeholder="https://instagram.com/user یا https://t.me/channel" required style="min-width:340px">
        <input name="display_name" placeholder="نام (اختیاری)">
        <input name="niche" placeholder="نیچ (اختیاری)">
        <button class="btn2">افزودن</button>
      </form>
    </div>
    '''
    return layout('کشف اینفلوئنسر', body)


@router.post('/influencer/discover')
async def discover_run(
    db: Session = Depends(get_db),
    platform: Annotated[str, Form()] = 'both',
    max_results: Annotated[int, Form()] = 8,
    queries: Annotated[list[str] | None, Form()] = None,
    use_seed: Annotated[str, Form()] = 'yes',
    check_activity: Annotated[str, Form()] = 'yes',
    activity_window: Annotated[int, Form()] = ACTIVITY_WINDOW_DAYS,
    include_channels: Annotated[str, Form()] = 'yes',
    include_groups: Annotated[str, Form()] = 'yes',
):
    queries_clean = [q for q in (queries or []) if q and q.strip()] or None
    result = await discover_influencers(
        db, platform=platform, queries=queries_clean,
        max_results_per_query=max_results,
        use_seed_list=(use_seed == 'yes'),
        check_activity_first=(check_activity == 'yes'),
        activity_window_days=activity_window,
        include_channels=(include_channels == 'yes'),
        include_groups=(include_groups == 'yes'),
    )

    backends = ', '.join(result.get('backends_used') or []) or 'DuckDuckGo'
    breakdown = (f"📸 {result.get('saved_ig', 0)} پیج · "
                 f"📢 {result.get('saved_channels', 0)} کانال · "
                 f"👥 {result.get('saved_groups', 0)} گروه")
    activity_status = ''
    if result.get('activity_check_enabled'):
        activity_status = (f" | 🎯 چک فعالیت ({result['activity_window_days']} روز): "
                          f"⚪ {result.get('skipped_inactive', 0)} غیرفعال، "
                          f"❌ {result.get('skipped_notfound', 0)} پیدا نشد، "
                          f"🤖 {result.get('skipped_wrongtype', 0)} بات/کاربر رد شد")

    msg = (f"✅ کشف: {result['queries_run']} جستجو، {result.get('seed_used', 0)} seed، "
           f"{result['new_saved']} جدید ذخیره شد ({breakdown})، "
           f"{result['duplicates']} تکراری."
           f"{activity_status} | Backends: {backends}")
    if result.get('errors'):
        msg += f" ⚠️ {len(result['errors'])} خطا."
    return RedirectResponse(url=f'/influencer?msg={quote_plus(msg)}', status_code=303)


@router.post('/influencer/manual')
async def manual_add(db: Session = Depends(get_db), profile_url: Annotated[str, Form()] = '', display_name: Annotated[str, Form()] = '', niche: Annotated[str, Form()] = ''):
    profile_url = profile_url.strip()
    if not profile_url: return RedirectResponse(url='/influencer/discover', status_code=303)
    if 'instagram.com' in profile_url:
        platform = 'instagram'; parts = profile_url.split('instagram.com/'); username = parts[-1].strip('/').split('/')[0] if len(parts) > 1 else None; profile_url = f'https://instagram.com/{username}' if username else profile_url
    elif 't.me' in profile_url:
        platform = 'telegram'; parts = profile_url.split('t.me/'); username = parts[-1].strip('/').split('/')[0] if len(parts) > 1 else None; profile_url = f'https://t.me/{username}' if username else profile_url
    else: platform = 'instagram'; username = None
    existing = db.scalar(select(Influencer).where(Influencer.profile_url == profile_url))
    if existing: return RedirectResponse(url=f'/influencer/{existing.id}', status_code=303)
    info = await (scrape_telegram_channel(username) if platform == 'telegram' and username else scrape_instagram_profile(username) if username else {})
    blob = f"{info.get('display_name', '')} {info.get('bio', '')} {username or ''}"
    inf = Influencer(platform=platform, profile_url=profile_url, username=username, display_name=display_name or info.get('display_name') or username or 'نامشخص', bio=info.get('bio'), followers=info.get('followers'), following=info.get('following'), posts_count=info.get('posts_count'), avg_views=info.get('avg_views'), engagement_rate=info.get('engagement_rate'), niche=niche.strip() or _detect_niche(blob), game_tags=_detect_game_tags(blob), language='fa' if _has_persian(blob) else 'en', source='manual', status='discovered')
    compute_influencer_score(inf); db.add(inf); db.commit(); db.refresh(inf)
    return RedirectResponse(url=f'/influencer/{inf.id}', status_code=303)


@router.get('/influencer/cleanup')
def cleanup_profiles(db: Session = Depends(get_db)):
    """حذف پروفایل‌های غیرفعال (is_active=False)"""
    bad = db.execute(select(Influencer).where(Influencer.is_active == False)).scalars().all()
    count = len(bad)
    for inf in bad:
        db.delete(inf)
    db.commit()
    return RedirectResponse(url=f'/influencer?msg={quote_plus(f"🧹 {count} پروفایل غیرفعال حذف شد")}', status_code=303)


@router.get('/influencer/recheck')
async def recheck_activity(db: Session = Depends(get_db), delete: str = Query(''), window: int = Query(ACTIVITY_WINDOW_DAYS, ge=1, le=365)):
    """چک مجدد فعالیت همه اینفلوئنسرها. اگر ?delete=yes باشه غیرفعال‌ها حذف میشن."""
    result = await recheck_all_activity(db, window_days=window, delete_inactive=(delete == 'yes'))
    deleted_part = f', 🧹 حذف شده: {result["deleted"]}' if result.get('deleted') else ''
    msg = (f"🔄 چک مجدد فعالیت انجام شد: بررسی {result['total_checked']} نفر، "
           f"✅ فعال: {result['active']}، ⚪ غیرفعال: {result['inactive']}، "
           f"❌ پیدا نشد: {result['notfound']}، خطا: {result['errors']}{deleted_part}")
    return RedirectResponse(url=f'/influencer?msg={quote_plus(msg)}', status_code=303)


@router.get('/influencer/export.csv')
def export_csv(db: Session = Depends(get_db)):
    items = list(db.scalars(_build_query(db)).all())
    output = io.StringIO(); output.write('\ufeff')
    w = csv.writer(output)
    w.writerow(['ID','پلتفرم','نام','یوزرنیم','لینک','فالوور','بازدید','لایک','کامنت','تعامل%','نیچ','بازی‌ها','سطح','امتیاز همکاری','وضعیت','قیمت','نوع همکاری','یادداشت'])
    for inf in items: w.writerow([inf.id, inf.platform, inf.display_name, inf.username, inf.profile_url, inf.followers, inf.avg_views, inf.avg_likes, inf.avg_comments, inf.engagement_rate, inf.niche, inf.game_tags, inf.tier, inf.collab_score, INF_STATUS.get(inf.status, inf.status), inf.collab_price, inf.collab_type, inf.notes])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename=influencers.csv'})


@router.get('/influencer/export.xlsx')
def export_xlsx(db: Session = Depends(get_db)):
    from openpyxl import Workbook
    items = list(db.scalars(_build_query(db)).all())
    wb = Workbook(); ws = wb.active; ws.title = 'اینفلوئنسرها'
    ws.append(['ID','پلتفرم','نام','یوزرنیم','لینک','فالوور','بازدید','لایک','کامنت','تعامل%','نیچ','بازی‌ها','سطح','امتیاز همکاری','وضعیت','قیمت','نوع همکاری','یادداشت'])
    for inf in items: ws.append([inf.id, inf.platform, inf.display_name, inf.username, inf.profile_url, inf.followers, inf.avg_views, inf.avg_likes, inf.avg_comments, inf.engagement_rate, inf.niche, inf.game_tags, inf.tier, inf.collab_score, INF_STATUS.get(inf.status, inf.status), inf.collab_price, inf.collab_type, inf.notes])
    for col in ws.columns:
        ml = max(len(str(c.value or '')) for c in col[:50])
        ws.column_dimensions[col[0].column_letter].width = min(max(ml+2, 12), 40)
    output = io.BytesIO(); wb.save(output); output.seek(0)
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=influencers.xlsx'})


@router.get('/api/influencer')
def api_influencer(db: Session = Depends(get_db), platform: str = '', min_score: int = 0, limit: int = Query(50)):
    stmt = select(Influencer).where(Influencer.language == 'fa', Influencer.followers >= MIN_FOLLOWERS)
    if platform: stmt = stmt.where(Influencer.platform == platform)
    if min_score: stmt = stmt.where(Influencer.collab_score >= min_score)
    items = list(db.scalars(stmt.order_by(desc(Influencer.collab_score)).limit(limit)).all())
    return [{'id': i.id, 'platform': i.platform, 'name': i.display_name, 'username': i.username, 'url': i.profile_url, 'followers': i.followers, 'avg_views': i.avg_views, 'engagement_rate': i.engagement_rate, 'niche': i.niche, 'game_tags': i.game_tags, 'tier': i.tier, 'collab_score': i.collab_score, 'status': i.status} for i in items]


# ============================================================
# 2. PARAMETERIZED ROUTES LAST
# ============================================================

@router.get('/influencer/{inf_id}', response_class=HTMLResponse)
def influencer_detail(inf_id: int, db: Session = Depends(get_db)):
    inf = db.get(Influencer, inf_id)
    if not inf: raise HTTPException(404, 'اینفلوئنسر پیدا نشد')
    status_opts = ''.join(f'<option value="{k}" {"selected" if inf.status==k else ""}>{v}</option>' for k,v in INF_STATUS.items())
    cc = _collab_color(inf.collab_score)
    tier_info = TIER_LABELS.get(inf.tier or '', ('❓', '-', '#9ca3af'))
    body = f'''<div class="card"><h1>{_platform_icon(inf.platform)} {h(inf.display_name)}</h1><p class="muted">#{inf.id} | {h(inf.platform)} | نیچ: {h(inf.niche or '-')} | سطح: {tier_info[0]} {tier_info[1]}</p><a class="btn" target="_blank" href="{h(inf.profile_url)}">باز کردن پروفایل</a><form method="post" action="/influencer/{inf.id}/refresh" style="display:inline"><button class="btn2 btn">🔄 بروزرسانی آمار</button></form><button class="btn-danger" onclick="confirmDelete({inf.id},'{h(inf.display_name)}')">🗑 حذف</button></div>
    <div class="grid4"><div class="stat-card">فالوور<b style="color:#be185d">{_format_count(inf.followers)}</b></div><div class="stat-card">بازدید<b style="color:#2563eb">{round(inf.avg_views or 0)}</b></div><div class="stat-card">تعامل<b style="color:#12b76a">{round(inf.engagement_rate or 0,1)}%</b></div><div class="stat-card">امتیاز همکاری<b style="color:{cc}">{inf.collab_score}</b></div></div>
    <div class="grid2"><div class="card"><h3>📊 امتیازها</h3><div style="margin-bottom:10px"><b>مرتبط‌بودن</b>{_score_bar(inf.relevance_score, '#be185d')}</div><div style="margin-bottom:10px"><b>کیفیت</b>{_score_bar(inf.quality_score, '#12b76a')}</div><div><b>امتیاز همکاری</b>{_score_bar(inf.collab_score, cc)}</div></div>
    <div class="card"><h3>📝 ویرایش</h3><form method="post" action="/influencer/{inf.id}/update"><label>وضعیت<br><select name="status">{status_opts}</select></label><br><label>نیچ<br><input name="niche" value="{h(inf.niche or '')}"></label><br><label>تگ بازی‌ها<br><input name="game_tags" value="{h(inf.game_tags or '')}"></label><br><label>نوع همکاری<br><input name="collab_type" value="{h(inf.collab_type or '')}"></label><br><label>قیمت<br><input name="collab_price" value="{h(inf.collab_price or '')}"></label><br><label>یادداشت<br><textarea name="notes" style="width:100%;min-height:70px">{h(inf.notes or '')}</textarea></label><br><button>ذخیره</button></form></div></div>
    <div class="card"><h3>📄 بیوگرافی</h3><p>{h(inf.bio or 'بدون بیو')}</p><p class="muted">زبان: {h(inf.language or '-')} | منبع: {h(inf.source)} | فالووینگ: {_format_count(inf.following)} | پست: {inf.posts_count or '-'}</p></div>'''
    return layout('جزئیات اینفلوئنسر', body)


@router.get('/influencer/{inf_id}/delete')
def inf_delete(inf_id: int, db: Session = Depends(get_db)):
    inf = db.get(Influencer, inf_id)
    if not inf: raise HTTPException(404)
    name = inf.display_name
    db.delete(inf); db.commit()
    return RedirectResponse(url=f'/influencer?msg={quote_plus(f"«{name}» حذف شد")}', status_code=303)


@router.post('/influencer/{inf_id}/update')
def inf_update(inf_id: int, db: Session = Depends(get_db), status: Annotated[str, Form()] = 'discovered', niche: Annotated[str, Form()] = '', game_tags: Annotated[str, Form()] = '', collab_type: Annotated[str, Form()] = '', collab_price: Annotated[str, Form()] = '', notes: Annotated[str, Form()] = ''):
    inf = db.get(Influencer, inf_id)
    if not inf: raise HTTPException(404)
    inf.status = status; inf.niche = niche.strip() or None; inf.game_tags = game_tags.strip() or None; inf.collab_type = collab_type.strip() or None; inf.collab_price = collab_price.strip() or None; inf.notes = notes.strip() or None
    compute_influencer_score(inf); db.add(inf); db.commit()
    return RedirectResponse(url=f'/influencer/{inf_id}', status_code=303)


@router.post('/influencer/{inf_id}/refresh')
async def inf_refresh(inf_id: int, db: Session = Depends(get_db)):
    inf = db.get(Influencer, inf_id)
    if not inf or not inf.username: return RedirectResponse(url=f'/influencer/{inf_id}', status_code=303)
    info = await (scrape_telegram_channel(inf.username) if inf.platform == 'telegram' else scrape_instagram_profile(inf.username))
    for field in ['followers', 'following', 'posts_count', 'avg_views', 'engagement_rate', 'bio']:
        if info.get(field): setattr(inf, field, info[field])
    inf.last_seen = datetime.utcnow(); compute_influencer_score(inf); db.add(inf); db.commit()
    return RedirectResponse(url=f'/influencer/{inf_id}', status_code=303)
