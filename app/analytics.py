"""Analytics Dashboard with visual charts for Game Lead Finder."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, func, select, case
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crm import conversion_funnel, dashboard_more, source_quality_report, PROFESSIONAL_STATUSES
from app.db.models import ActivityLog, Campaign, CampaignMember, Lead, Person, CrawlerRun
from app.db.session import get_db

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


# ── CSS bar chart helpers ──────────────────────────────────────────

def _bar(value: int, max_val: int, color: str = '#2563eb', label: str = '', width_pct: float | None = None) -> str:
    """Render a single CSS bar."""
    if width_pct is None:
        width_pct = (value / max_val * 100) if max_val else 0
    width_pct = max(0, min(width_pct, 100))
    return (
        f'<div style="display:flex;align-items:center;gap:8px;margin:5px 0">'
        f'<div style="min-width:90px;text-align:left;font-size:12px;color:#475467">{h(label)}</div>'
        f'<div style="flex:1;background:#eef2ff;border-radius:999px;height:22px;overflow:hidden">'
        f'<div style="width:{width_pct:.1f}%;height:100%;background:{color};border-radius:999px;'
        f'transition:width .4s ease"></div></div>'
        f'<div style="min-width:36px;font-weight:700;font-size:13px;color:#101828">{value}</div></div>'
    )


def _mini_bar(value: int, max_val: int, color: str = '#2563eb') -> str:
    pct = (value / max_val * 100) if max_val else 0
    return (
        f'<div style="background:#eef2ff;border-radius:999px;height:10px;overflow:hidden;width:100%">'
        f'<div style="width:{pct:.1f}%;height:100%;background:{color};border-radius:999px"></div></div>'
    )


# ── Donut SVG ─────────────────────────────────────────────────────

def _donut_svg(segments: list[tuple[str, int, str]], size: int = 160) -> str:
    """Create an inline SVG donut chart."""
    total = sum(s[1] for s in segments) or 1
    cx, cy, r = size // 2, size // 2, size // 2 - 10
    circumference = 2 * 3.14159 * r
    offset = 0
    arcs = []
    legends = []
    for label, value, color in segments:
        pct = value / total if total else 0
        dash = pct * circumference
        gap = circumference - dash
        arcs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
            f'stroke-width="24" stroke-dasharray="{dash:.2f} {gap:.2f}" '
            f'stroke-dashoffset="-{offset:.2f}" />'
        )
        offset += dash
        legends.append(
            f'<div style="display:flex;align-items:center;gap:6px;margin:3px 0">'
            f'<div style="width:12px;height:12px;border-radius:3px;background:{color}"></div>'
            f'<span style="font-size:12px;color:#475467">{h(label)}</span>'
            f'<b style="margin-right:auto;font-size:12px">{value}</b></div>'
        )
    svg = (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size}" style="transform:rotate(-90deg)">'
        + ''.join(arcs) +
        f'<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central" '
        f'font-size="22" font-weight="bold" fill="#101828" style="transform:rotate(90deg);transform-origin:center">{total}</text>'
        '</svg>'
    )
    return (
        f'<div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap">'
        f'<div>{svg}</div>'
        f'<div>{"".join(legends)}</div></div>'
    )


# ── Sparkline SVG ─────────────────────────────────────────────────

def _sparkline_svg(values: list[int], width: int = 300, height: int = 60, color: str = '#2563eb') -> str:
    if not values or max(values) == 0:
        return f'<svg width="{width}" height="{height}"></svg>'
    max_val = max(values) or 1
    padding = 4
    inner_w = width - 2 * padding
    inner_h = height - 2 * padding
    step = inner_w / (len(values) - 1) if len(values) > 1 else inner_w
    points = []
    for i, v in enumerate(values):
        x = padding + i * step
        y = padding + inner_h - (v / max_val * inner_h)
        points.append(f'{x:.1f},{y:.1f}')
    polyline = ' '.join(points)
    area_points = f'{padding},{padding + inner_h} {polyline} {padding + (len(values)-1)*step:.1f},{padding + inner_h}'
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<polygon points="{area_points}" fill="{color}" fill-opacity="0.12" />'
        f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />'
        f'</svg>'
    )


# ── Data queries ──────────────────────────────────────────────────

def _daily_new_leads(db: Session, days: int = 30) -> list[tuple[str, int]]:
    """Return (date_str, count) for last N days."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(func.date(Lead.first_seen).label('d'), func.count(Lead.id))
        .where(Lead.first_seen >= since)
        .group_by('d')
        .order_by('d')
    ).all()
    return [(str(r[0]), r[1]) for r in rows]


def _daily_status_changes(db: Session, days: int = 14) -> list[tuple[str, str, int]]:
    """Return (date, action, count) from activity logs."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(func.date(ActivityLog.created_at).label('d'), ActivityLog.action, func.count(ActivityLog.id))
        .where(ActivityLog.created_at >= since)
        .where(ActivityLog.action.in_(['messaged', 'followup1', 'followup2', 'call']))
        .group_by('d', 'activity_logs.action')
        .order_by('d')
    ).all()
    return [(str(r[0]), r[1], r[2]) for r in rows]


def _category_breakdown(db: Session) -> list[tuple[str | None, int]]:
    return db.execute(
        select(Lead.category, func.count(Lead.id))
        .group_by(Lead.category)
        .order_by(func.count(Lead.id).desc())
        .limit(10)
    ).all()


def _city_breakdown(db: Session) -> list[tuple[str | None, int]]:
    return db.execute(
        select(Lead.city, func.count(Lead.id))
        .where(Lead.city.isnot(None))
        .where(Lead.city != '')
        .group_by(Lead.city)
        .order_by(func.count(Lead.id).desc())
        .limit(10)
    ).all()


def _source_breakdown(db: Session) -> list[tuple[str | None, int]]:
    return db.execute(
        select(Lead.source, func.count(Lead.id))
        .group_by(Lead.source)
        .order_by(func.count(Lead.id).desc())
        .limit(12)
    ).all()


def _due_followups(db: Session) -> list[Lead]:
    return list(
        db.scalars(
            select(Lead)
            .where(Lead.follow_up_at.isnot(None))
            .where(Lead.follow_up_at <= datetime.utcnow())
            .where(Lead.status.notin_(['registered', 'irrelevant', 'rejected']))
            .order_by(Lead.follow_up_at)
            .limit(50)
        ).all()
    )


def _upcoming_followups(db: Session) -> list[Lead]:
    now = datetime.utcnow()
    return list(
        db.scalars(
            select(Lead)
            .where(Lead.follow_up_at.isnot(None))
            .where(Lead.follow_up_at > now)
            .where(Lead.follow_up_at <= now + timedelta(days=7))
            .where(Lead.status.notin_(['registered', 'irrelevant', 'rejected']))
            .order_by(Lead.follow_up_at)
            .limit(30)
        ).all()
    )


def _score_distribution(db: Session) -> list[tuple[str, int]]:
    """Bucket scores into ranges."""
    buckets = [
        ('0-20', 0, 20), ('21-40', 21, 40), ('41-60', 41, 60),
        ('61-80', 61, 80), ('81-100', 81, 100),
    ]
    result = []
    for label, lo, hi in buckets:
        count = db.scalar(select(func.count(Lead.id)).where(Lead.score >= lo, Lead.score <= hi)) or 0
        result.append((label, count))
    return result


def _stale_leads(db: Session, days: int = 14) -> int:
    since = datetime.utcnow() - timedelta(days=days)
    return db.scalar(
        select(func.count(Lead.id))
        .where(Lead.status.in_(['new', 'checked']))
        .where(Lead.last_seen < since)
    ) or 0


def _lead_velocity(db: Session, days: int = 7) -> float:
    count = db.scalar(
        select(func.count(Lead.id))
        .where(Lead.first_seen >= datetime.utcnow() - timedelta(days=days))
    ) or 0
    return round(count / days, 1)


# ── Main analytics page ───────────────────────────────────────────

@router.get('/analytics', response_class=HTMLResponse)
def analytics_page(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)

    # Data
    funnel = conversion_funnel(db)
    more = dashboard_more(db)
    src_report = source_quality_report(db)
    categories = _category_breakdown(db)
    cities = _city_breakdown(db)
    sources = _source_breakdown(db)
    score_dist = _score_distribution(db)
    daily = _daily_new_leads(db, days=30)
    due = _due_followups(db)
    upcoming = _upcoming_followups(db)
    stale = _stale_leads(db)
    velocity = _lead_velocity(db)

    # Colors
    COLORS = ['#2563eb', '#12b76a', '#f79009', '#f04438', '#7c3aed', '#0891b2', '#be185d', '#65a30d', '#ea580c', '#6366f1']
    STATUS_COLORS = {
        'new': '#2563eb', 'checked': '#0891b2', 'messaged': '#f79009',
        'followup1': '#ea580c', 'followup2': '#be185d',
        'replied': '#12b76a', 'interested': '#12b76a', 'registered': '#7c3aed',
        'rejected': '#f04438', 'no_response': '#9ca3af', 'irrelevant': '#f04438',
    }

    # ── Funnel donut ──
    funnel_segments = [
        ('جدید', funnel['total'] - funnel['messaged'], '#2563eb'),
        ('پیام داده شده', funnel['messaged'] - funnel['replied'], '#f79009'),
        ('جواب داده', funnel['replied'] - funnel['registered'], '#12b76a'),
        ('ثبت‌نام', funnel['registered'], '#7c3aed'),
    ]
    funnel_svg = _donut_svg(funnel_segments)

    # ── Daily sparkline ──
    daily_values = [d[1] for d in daily[-30:]]
    sparkline = _sparkline_svg(daily_values, width=400, height=70)

    # ── Source bars ──
    src_max = max((s[1] for s in sources), default=1)
    source_bars = ''.join(_bar(count, src_max, COLORS[i % len(COLORS)], label=src or 'نامشخص') for i, (src, count) in enumerate(sources))

    # ── Category bars ──
    cat_max = max((c[1] for c in categories), default=1)
    cat_colors = {'اکانت': '#2563eb', 'سی‌پی کالاف': '#f04438', 'یوسی پابجی': '#f79009', 'جم/الماس': '#7c3aed', 'گیفت کارت': '#12b76a', 'فروشگاه گیم': '#0891b2', 'گیم‌نت': '#be185d'}
    cat_bars = ''.join(_bar(count, cat_max, cat_colors.get(cat or '', '#6b7280'), label=cat or 'بدون دسته') for cat, count in categories)

    # ── City bars ──
    city_max = max((c[1] for c in cities), default=1)
    city_bars = ''.join(_bar(count, city_max, '#0891b2', label=city or 'نامشخص') for city, count in cities)

    # ── Score distribution ──
    score_max = max((s[1] for s in score_dist), default=1)
    score_colors = ['#f04438', '#f79009', '#eab308', '#12b76a', '#2563eb']
    score_bars = ''.join(_bar(count, score_max, score_colors[i], label=label) for i, (label, count) in enumerate(score_dist))

    # ── Source quality table ──
    src_rows = ''.join(
        f'<tr><td>{h(r["source"])}</td><td>{r["total"]}</td>'
        f'<td>{_mini_bar(r["total"], src_max, "#2563eb")}</td>'
        f'<td>{r["messaged"]}</td><td>{r["replied"]}</td><td>{r["registered"]}</td>'
        f'<td><b>{r["reply_rate"]}%</b></td><td><b>{r["conversion_rate"]}%</b></td></tr>'
        for r in src_report
    )

    # ── Due followups ──
    due_rows = ''
    for lead in due:
        overdue_days = (datetime.utcnow() - lead.follow_up_at).days if lead.follow_up_at else 0
        urgency_color = '#f04438' if overdue_days > 3 else '#f79009' if overdue_days > 1 else '#eab308'
        due_rows += (
            f'<tr>'
            f'<td><b>{h(lead.title)}</b><br><span style="font-size:12px;color:#667085">{h(lead.category or "")}</span></td>'
            f'<td>{h(lead.city or "-")}</td>'
            f'<td><span style="color:{urgency_color};font-weight:700">{overdue_days} روز تأخیر</span></td>'
            f'<td>{h(lead.status)}</td>'
            f'<td><a class="btn" href="/leads/{lead.id}" style="font-size:11px;padding:5px 10px">پیگیری</a></td>'
            f'</tr>'
        )

    # ── Upcoming followups ──
    upcoming_rows = ''
    for lead in upcoming:
        days_left = (lead.follow_up_at - datetime.utcnow()).days if lead.follow_up_at else 0
        upcoming_rows += (
            f'<tr>'
            f'<td><b>{h(lead.title)}</b></td><td>{h(lead.city or "-")}</td>'
            f'<td>{days_left} روز دیگر</td><td>{h(lead.status)}</td>'
            f'<td><a class="btn" href="/leads/{lead.id}" style="font-size:11px;padding:5px 10px">مشاهده</a></td>'
            f'</tr>'
        )

    # ── KPI cards ──
    total_leads = funnel['total']
    conversion_pct = round((funnel['registered'] / total_leads * 100), 1) if total_leads else 0
    reply_pct = round((funnel['replied'] / total_leads * 100), 1) if total_leads else 0
    msg_pct = round((funnel['messaged'] / total_leads * 100), 1) if total_leads else 0

    body = f'''
    <div class="crm-hero">
      <h1>📊 داشبورد آنالیتیکس</h1>
      <div class="muted">تحلیل جامع عملکرد، کیفیت منابع و وضعیت لیدها</div>
      <a class="btn btn2" href="/">بانک لیدها</a>
      <a class="btn btn2" href="/crm">CRM</a>
      <a class="btn btn2" href="/campaigns">کمپین‌ها</a>
    </div>

    <!-- KPI Cards -->
    <div class="grid3">
      <div class="card" style="text-align:center">
        <div style="font-size:13px;color:#667085">کل مخاطبین</div>
        <div style="font-size:36px;font-weight:800;color:#2563eb">{total_leads}</div>
        <div style="font-size:12px;color:#667085">سرعت: {velocity} لید/روز</div>
      </div>
      <div class="card" style="text-align:center">
        <div style="font-size:13px;color:#667085">نرخ پیام</div>
        <div style="font-size:36px;font-weight:800;color:#f79009">{msg_pct}%</div>
        <div style="font-size:12px;color:#667085">{funnel["messaged"]} نفر پیام گرفته</div>
      </div>
      <div class="card" style="text-align:center">
        <div style="font-size:13px;color:#667085">نرخ جواب</div>
        <div style="font-size:36px;font-weight:800;color:#12b76a">{reply_pct}%</div>
        <div style="font-size:12px;color:#667085">{funnel["replied"]} نفر جواب داده</div>
      </div>
      <div class="card" style="text-align:center">
        <div style="font-size:13px;color:#667085">نرخ ثبت‌نام</div>
        <div style="font-size:36px;font-weight:800;color:#7c3aed">{conversion_pct}%</div>
        <div style="font-size:12px;color:#667085">{funnel["registered"]} نفر ثبت‌نام</div>
      </div>
      <div class="card" style="text-align:center">
        <div style="font-size:13px;color:#667085">پیگیری سررسید</div>
        <div style="font-size:36px;font-weight:800;color:#f04438">{len(due)}</div>
        <div style="font-size:12px;color:#667085">نیاز به پیگیری فوری</div>
      </div>
      <div class="card" style="text-align:center">
        <div style="font-size:13px;color:#667085">لیدهای راکد</div>
        <div style="font-size:36px;font-weight:800;color:#9ca3af">{stale}</div>
        <div style="font-size:12px;color:#667085">بدون فعالیت ۱۴ روز اخیر</div>
      </div>
    </div>

    <!-- Funnel + Sparkline -->
    <div class="grid2">
      <div class="card">
        <h3>🔄 قیف تبدیل</h3>
        {funnel_svg}
      </div>
      <div class="card">
        <h3>📈 روند لیدهای جدید (۳۰ روز اخیر)</h3>
        <div style="padding:10px 0">{sparkline}</div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:#667085">
          <span>{daily[0][0] if daily else ""}</span>
          <span>{daily[-1][0] if daily else ""}</span>
        </div>
      </div>
    </div>

    <!-- Source + Category -->
    <div class="grid2">
      <div class="card">
        <h3>📡 توزیع منابع</h3>
        {source_bars or '<span class="muted">هنوز داده‌ای نیست</span>'}
      </div>
      <div class="card">
        <h3>🏷️ دسته‌بندی‌ها</h3>
        {cat_bars or '<span class="muted">هنوز دسته‌ای شناسایی نشده</span>'}
      </div>
    </div>

    <!-- City + Score -->
    <div class="grid2">
      <div class="card">
        <h3>🏙️ شهرها</h3>
        {city_bars or '<span class="muted">هنوز شهری ثبت نشده</span>'}
      </div>
      <div class="card">
        <h3>⭐ توزیع امتیاز</h3>
        {score_bars}
      </div>
    </div>

    <!-- Source Quality Table -->
    <div class="card">
      <h3>📋 جدول کیفیت منابع</h3>
      <div style="overflow-x:auto">
        <table style="width:100%">
          <thead>
            <tr><th>منبع</th><th>کل</th><th>نمودار</th><th>پیام</th><th>جواب</th><th>ثبت‌نام</th><th>نرخ جواب</th><th>نرخ تبدیل</th></tr>
          </thead>
          <tbody>{src_rows or '<tr><td colspan="8" class="muted">داده‌ای نیست</td></tr>'}</tbody>
        </table>
      </div>
    </div>

    <!-- Due Followups -->
    <div class="card">
      <h3>⏰ پیگیری‌های سررسید ({len(due)} مورد)</h3>
      {"<table style='width:100%'><thead><tr><th>مخاطب</th><th>شهر</th><th>وضعیت تأخیر</th><th>وضعیت CRM</th><th>عملیات</th></tr></thead><tbody>" + due_rows + "</tbody></table>" if due else '<span class="muted">پیگیری سررسید ندارید 🎉</span>'}
    </div>

    <!-- Upcoming Followups -->
    <div class="card">
      <h3>📅 پیگیری‌های آینده (۷ روز آینده)</h3>
      {"<table style='width:100%'><thead><tr><th>مخاطب</th><th>شهر</th><th>زمان باقیمانده</th><th>وضعیت</th><th>عملیات</th></tr></thead><tbody>" + upcoming_rows + "</tbody></table>" if upcoming else '<span class="muted">پیگیری آینده‌ای برنامه‌ریزی نشده</span>'}
    </div>

    <!-- Bulk Operations -->
    <div class="card" id="bulk">
      <h3>📦 عملیات گروهی</h3>
      <div class="muted" style="margin-bottom:10px">تغییر وضعیت یا حذف گروهی لیدها بر اساس فیلتر</div>

      <!-- Merge Duplicates -->
      <form method="post" action="/analytics/merge-duplicates" style="margin-bottom:16px;padding:12px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:14px">
        <h4 style="margin:0 0 8px;color:#166534">🔄 ادغام لیدهای تکراری</h4>
        <div class="muted" style="margin-bottom:8px">لیدهایی که URL، اینستاگرام، تلگرام، شماره تلفن یا دامنه یکسان دارند ادغام می‌شوند</div>
        <button style="background:#12b76a;color:white">ادغام تکراری‌ها</button>
      </form>
      <form method="post" action="/analytics/bulk-status" style="margin-bottom:12px">
        <h4>تغییر وضعیت گروهی</h4>
        <select name="from_status">
          <option value="">-- همه وضعیت‌ها --</option>
          {''.join(f'<option value="{h(k)}">{h(v)}</option>' for k, v in PROFESSIONAL_STATUSES.items())}
        </select>
        <span>← تبدیل به →</span>
        <select name="to_status">
          {''.join(f'<option value="{h(k)}">{h(v)}</option>' for k, v in PROFESSIONAL_STATUSES.items())}
        </select>
        <input name="source_filter" placeholder="فیلتر منبع (اختیاری)" style="width:140px">
        <input name="category_filter" placeholder="فیلتر دسته (اختیاری)" style="width:140px">
        <input name="city_filter" placeholder="فیلتر شهر (اختیاری)" style="width:120px">
        <button style="background:#f79009;color:white">اعمال تغییر وضعیت</button>
      </form>
      <hr style="border:0;border-top:1px solid #e4e7ec;margin:12px 0">
      <form method="post" action="/analytics/bulk-delete" onsubmit="return confirm('آیا مطمئن هستید؟ این عمل غیرقابل بازگشت است.')">
        <h4>حذف گروهی</h4>
        <select name="status">
          <option value="">-- انتخاب وضعیت --</option>
          {''.join(f'<option value="{h(k)}">{h(v)}</option>' for k, v in PROFESSIONAL_STATUSES.items())}
        </select>
        <input name="source_filter" placeholder="فیلتر منبع" style="width:140px">
        <label style="color:#be123c"><input type="checkbox" name="confirm" value="1" required> تأیید حذف</label>
        <button style="background:#f04438;color:white">حذف گروهی</button>
      </form>
    </div>
    '''
    return HTMLResponse(f'''<!doctype html><html lang="fa"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>داشبورد آنالیتیکس</title>
    <style>
      :root{{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb;--shadow:0 16px 40px rgba(16,24,40,.08)}}
      *{{box-sizing:border-box}}body{{font-family:Tahoma,Arial,sans-serif;background:radial-gradient(circle at top right,rgba(37,99,235,.10),transparent 34%),linear-gradient(180deg,#f8fbff,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}}
      .wrap{{max-width:1320px;margin:auto;padding:22px}}a{{color:var(--primary);text-decoration:none}}
      .crm-hero{{background:linear-gradient(135deg,#0f172a,#1e3a8a 58%,#2563eb);color:white;border-radius:26px;padding:20px;box-shadow:var(--shadow);margin-bottom:16px}}
      .crm-hero h1{{margin:0;font-size:25px}}.crm-hero .muted{{color:#dbeafe}}
      .card{{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045);backdrop-filter:blur(12px)}}
      .card h3{{margin-top:0}}
      .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
      .muted{{color:var(--muted);font-size:13px;line-height:1.8}}
      .btn,.action,button{{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer}}
      .btn2{{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe}}
      input,select,textarea{{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}}
      input:focus,select:focus{{border-color:#93c5fd;box-shadow:0 0 0 4px rgba(37,99,235,.12)}}
      table{{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}}
      th,td{{padding:10px 12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top;font-size:13px}}
      th{{background:#eef4ff;font-weight:700}}tr:hover{{background:#f9fbff}}
      @media(max-width:850px){{.wrap{{padding:12px}}.grid2,.grid3{{grid-template-columns:1fr}}table{{display:block;overflow-x:auto}}}}
    </style></head><body><div class="wrap">{body}</div></body></html>''')


# ── Bulk Operations ───────────────────────────────────────────────

@router.post('/analytics/bulk-status')
def bulk_status_change(
    db: Session = Depends(get_db),
    token: Annotated[str, Form()] = '',
    from_status: Annotated[str, Form()] = '',
    to_status: Annotated[str, Form()] = 'new',
    source_filter: Annotated[str, Form()] = '',
    category_filter: Annotated[str, Form()] = '',
    city_filter: Annotated[str, Form()] = '',
):
    check_token(token)
    stmt = select(Lead)
    if from_status:
        stmt = stmt.where(Lead.status == from_status)
    if source_filter:
        stmt = stmt.where(Lead.source.ilike(f'%{source_filter}%'))
    if category_filter:
        stmt = stmt.where(Lead.category.ilike(f'%{category_filter}%'))
    if city_filter:
        stmt = stmt.where(Lead.city.ilike(f'%{city_filter}%'))
    leads = list(db.scalars(stmt).all())
    count = 0
    for lead in leads:
        lead.status = to_status
        db.add(lead)
        count += 1
    db.commit()
    msg = f'{count} مخاطب از وضعیت {from_status or "همه"} به {to_status} تغییر کرد'
    return RedirectResponse(url=f'/analytics?msg={quote_plus(msg)}#bulk', status_code=303)


@router.post('/analytics/bulk-delete')
def bulk_delete(
    db: Session = Depends(get_db),
    token: Annotated[str, Form()] = '',
    status: Annotated[str, Form()] = '',
    source_filter: Annotated[str, Form()] = '',
    confirm: Annotated[str | None, Form()] = None,
):
    check_token(token)
    if not confirm:
        return RedirectResponse(url=f'/analytics?msg={quote_plus("تأیید حذف لازم است")}#bulk', status_code=303)
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    if source_filter:
        stmt = stmt.where(Lead.source.ilike(f'%{source_filter}%'))
    leads = list(db.scalars(stmt).all())
    count = len(leads)
    for lead in leads:
        db.delete(lead)
    db.commit()
    msg = f'{count} مخاطب حذف شد'
    return RedirectResponse(url=f'/analytics?msg={quote_plus(msg)}#bulk', status_code=303)


# ── JSON API for analytics ────────────────────────────────────────

@router.get('/api/analytics')
def api_analytics(db: Session = Depends(get_db), token: str = Query('')):
    check_token(token)
    funnel = conversion_funnel(db)
    more = dashboard_more(db)
    categories = _category_breakdown(db)
    sources = _source_breakdown(db)
    score_dist = _score_distribution(db)
    daily = _daily_new_leads(db, days=30)
    return {
        'funnel': funnel,
        'daily_new': [{'date': d, 'count': c} for d, c in daily],
        'categories': [{'name': n, 'count': c} for n, c in categories],
        'sources': [{'name': n, 'count': c} for n, c in sources],
        'score_distribution': [{'range': r, 'count': c} for r, c in score_dist],
        'velocity': _lead_velocity(db),
        'stale': _stale_leads(db),
        'due_followups': len(_due_followups(db)),
    }


# ── Merge Duplicates ──────────────────────────────────────────────

@router.post('/analytics/merge-duplicates')
def merge_duplicates_route(
    db: Session = Depends(get_db),
    token: Annotated[str, Form()] = '',
):
    """Find and merge duplicate leads."""
    check_token(token)
    from app.repository import merge_existing_duplicates
    deleted = merge_existing_duplicates(db)
    msg = f'{deleted} لید تکراری ادغام و حذف شد'
    return RedirectResponse(url=f'/analytics?msg={quote_plus(msg)}#bulk', status_code=303)
