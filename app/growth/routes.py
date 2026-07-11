"""Growth module — routes: landing pages, referrals, welcome messages, content calendar.

All public landing page routes use /lp/ prefix (no auth needed).
Admin routes use /growth/ prefix.
"""
from __future__ import annotations

import csv
import io
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.growth.models import (
    LandingPage, LandingSignup, ReferralLink, ReferralClick,
    WelcomeMessage, ContentPost,
)

router = APIRouter()
TEHRAN_TZ = ZoneInfo('Asia/Tehran')


def h(v) -> str:
    import html as html_mod
    return html_mod.escape('' if v is None else str(v), quote=True)


def fmt_dt(value) -> str:
    if not value: return '-'
    try:
        if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(TEHRAN_TZ).strftime('%Y/%m/%d - %H:%M')
    except Exception: return str(value)


def _gen_code(length: int = 8) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def layout(title: str, body: str) -> HTMLResponse:
    css = '''<style>
      :root{--bg:#f6f8fc;--text:#101828;--muted:#667085;--line:#e4e7ec;--primary:#2563eb;--shadow:0 16px 40px rgba(16,24,40,.08)}
      *{box-sizing:border-box}body{font-family:Tahoma,Arial,sans-serif;background:radial-gradient(circle at top right,rgba(16,185,129,.08),transparent 34%),linear-gradient(180deg,#ecfdf5,#f4f6fb);direction:rtl;color:var(--text);margin:0;font-size:14px}
      .wrap{max-width:1400px;margin:auto;padding:22px}a{color:var(--primary);text-decoration:none}
      .hero{background:linear-gradient(135deg,#064e3b,#059669 50%,#34d399);color:white;border-radius:26px;padding:20px;box-shadow:var(--shadow);margin-bottom:16px}
      .hero h1{margin:0;font-size:25px}.hero .muted{color:#d1fae5}
      .card{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:18px;padding:17px;margin:14px 0;box-shadow:0 8px 26px rgba(16,24,40,.045)}
      .card h3{margin-top:0}
      .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
      .muted{color:var(--muted);font-size:13px;line-height:1.8}.small{font-size:12px}
      .btn,.action,button{display:inline-flex;align-items:center;justify-content:center;background:var(--primary);color:#fff;border:0;border-radius:12px;padding:9px 12px;margin:3px;font-weight:600;cursor:pointer;font-size:13px}
      .btn2{background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0}.btn2:hover{background:#d1fae5}
      .badge{display:inline-flex;background:#ecfdf5;color:#065f46;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;font-weight:600}
      .badge.green{background:#ecfdf3;color:#027a48}.badge.orange{background:#fffaeb;color:#b54708}.badge.red{background:#fff1f2;color:#be123c}.badge.blue{background:#eff6ff;color:#1d4ed8}
      input,select,textarea{font-family:inherit;border:1px solid #d0d5dd;border-radius:12px;padding:10px;margin:4px;background:#fff;outline:none}
      input:focus,select:focus{border-color:#6ee7b7;box-shadow:0 0 0 4px rgba(16,185,129,.12)}
      textarea{min-height:80px;width:100%}
      table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}
      th,td{padding:11px 12px;border-bottom:1px solid #edf0f5;text-align:right;vertical-align:top;font-size:13px}
      th{background:linear-gradient(180deg,#ecfdf5,#d1fae5);color:#064e3b;font-weight:700;position:sticky;top:0;z-index:1}
      tr:hover{background:#ecfdf5}tr:last-child td{border-bottom:0}
      .stat-card{background:linear-gradient(180deg,#fff,#ecfdf5);border:1px solid #a7f3d0;border-radius:20px;padding:17px;text-align:center;box-shadow:0 10px 24px rgba(16,185,129,.07)}.stat-card b{display:block;font-size:28px;margin-top:7px}
      .hint{background:#ecfdf5;border:1px solid #a7f3d0;color:#064e3b;border-radius:14px;padding:12px;margin-top:12px}
      @media(max-width:900px){.wrap{padding:12px}.grid2,.grid3,.grid4{grid-template-columns:1fr}table{display:block;overflow-x:auto}.hero{border-radius:20px;padding:16px}}
    </style>'''
    return HTMLResponse(f'<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title>{css}</head><body><div class="wrap"><div class="hero"><h1>{h(title)}</h1><div class="muted">ابزارهای رشد: لندینگ پیج، ریفرال، پیام خوش‌آمد، تقویم محتوا</div><a class="btn btn2" href="/">بانک لیدها</a> <a class="btn btn2" href="/growth">🌱 رشد مخاطب</a> <a class="btn btn2" href="/growth/pages">لندینگ پیج‌ها</a> <a class="btn btn2" href="/growth/referrals">ریفرال</a> <a class="btn btn2" href="/growth/welcome">پیام خوش‌آمد</a> <a class="btn btn2" href="/growth/content">تقویم محتوا</a></div>{body}</div></body></html>')


# ============================================================
# ADMIN: Growth Dashboard
# ============================================================

@router.get('/growth', response_class=HTMLResponse)
def growth_dashboard(db: Session = Depends(get_db)):
    pages_count = db.scalar(select(func.count(LandingPage.id))) or 0
    signups_count = db.scalar(select(func.count(LandingSignup.id))) or 0
    referrals_count = db.scalar(select(func.count(ReferralLink.id))) or 0
    welcome_count = db.scalar(select(func.count(WelcomeMessage.id))) or 0
    content_count = db.scalar(select(func.count(ContentPost.id))) or 0
    today_signups = db.scalar(select(func.count(LandingSignup.id)).where(LandingSignup.created_at >= datetime.utcnow() - timedelta(days=1))) or 0
    total_clicks = db.scalar(select(func.sum(ReferralLink.click_count))) or 0
    total_ref_signups = db.scalar(select(func.sum(ReferralLink.signup_count))) or 0

    body = f'''<div class="grid4">
      <div class="stat-card">لندینگ پیج‌ها<b style="color:#059669">{pages_count}</b></div>
      <div class="stat-card">کل ثبت‌نام‌ها<b style="color:#2563eb">{signups_count}</b></div>
      <div class="stat-card">لینک‌های ریفرال<b style="color:#7c3aed">{referrals_count}</b></div>
      <div class="stat-card">ثبت‌نام امروز<b style="color:#f59e0b">{today_signups}</b></div>
    </div>
    <div class="grid3">
      <div class="card"><h3>📊 آمار ریفرال</h3><p>کلیک: <b>{total_clicks}</b></p><p>ثبت‌نام از ریفرال: <b>{total_ref_signups}</b></p></div>
      <div class="card"><h3>📝 محتوای برنامه‌ریزی شده</h3><p>{content_count} پست</p></div>
      <div class="card"><h3>👋 پیام خوش‌آمد</h3><p>{welcome_count} قالب فعال</p></div>
    </div>
    <div class="grid3">
      <a class="card" href="/growth/pages" style="display:block;text-align:center"><h3>📄 لندینگ پیج‌ها</h3><p class="muted">ساخت صفحه فرود با فرم ثبت‌نام</p></a>
      <a class="card" href="/growth/referrals" style="display:block;text-align:center"><h3>🔗 سیستم ریفرال</h3><p class="muted">لینک اختصاصی + امتیازدهی</p></a>
      <a class="card" href="/growth/content" style="display:block;text-align:center"><h3>📅 تقویم محتوا</h3><p class="muted">برنامه‌ریزی پست + ایده AI</p></a>
    </div>'''
    return layout('🌱 رشد مخاطب', body)


# ============================================================
# ADMIN: Landing Pages
# ============================================================

@router.get('/growth/pages', response_class=HTMLResponse)
def pages_list(db: Session = Depends(get_db)):
    pages = list(db.scalars(select(LandingPage).order_by(desc(LandingPage.created_at))).all())
    rows = ''
    for p in pages:
        signups = db.scalar(select(func.count(LandingSignup.id)).where(LandingSignup.landing_page_id == p.id)) or 0
        rows += f'''<tr><td><b>{h(p.title)}</b><br><span class="muted">/{h(p.slug)}</span></td><td><span class="badge {'green' if p.is_active else 'red'}">{'فعال' if p.is_active else 'غیرفعال'}</span></td><td>{signups}</td><td>{h(p.platform_target or '-')}</td><td><a class="btn" href="/lp/{h(p.slug)}" target="_blank" style="font-size:11px;padding:5px 8px">باز کردن</a> <a class="btn2 btn" href="/growth/pages/{p.id}" style="font-size:11px;padding:5px 8px">ویرایش</a> <a class="btn2 btn" href="/growth/pages/{p.id}/signups" style="font-size:11px;padding:5px 8px">ثبت‌نام‌ها ({signups})</a></td></tr>'''
    body = f'''<div class="card"><h3>📄 لندینگ پیج‌ها</h3><table><thead><tr><th>نام</th><th>وضعیت</th><th>ثبت‌نام</th><th>پلتفرم</th><th>عملیات</th></tr></thead><tbody>{rows or '<tr><td colspan="5">هنوز لندینگ پیجی ساخته نشده</td></tr>'}</tbody></table></div>
    <div class="card"><h3>➕ ساخت لندینگ پیج جدید</h3>
    <form method="post" action="/growth/pages"><input name="title" placeholder="عنوان" required style="min-width:300px"><input name="slug" placeholder="slug (مثلاً: join-us)" required style="width:150px"><br><textarea name="description" placeholder="توضیحات صفحه"></textarea><br><input name="cta_text" placeholder="دکمه (مثلاً: ثبت‌نام رایگان)" value="ثبت‌نام رایگان"><select name="platform_target"><option value="app">اپ گیمینگ</option><option value="instagram">اینستاگرام</option><option value="telegram">تلگرام</option><option value="website">وب‌سایت</option></select><br><input name="telegram_link" placeholder="لینک تلگرام" style="min-width:300px"><input name="instagram_link" placeholder="لینک اینستاگرام" style="min-width:300px"><input name="app_link" placeholder="لینک اپ (مثلاً بازار/گوگل‌پلی)" style="min-width:300px"><br><input name="theme_color" value="#2563eb" style="width:100px"><label><input type="checkbox" name="collect_phone" checked> شماره تلفن</label><label><input type="checkbox" name="collect_name" checked> نام</label><label><input type="checkbox" name="collect_email"> ایمیل</label><button>ساخت لندینگ پیج</button></form></div>'''
    return layout('لندینگ پیج‌ها', body)


@router.post('/growth/pages')
def page_create(db: Session = Depends(get_db), title: Annotated[str, Form()] = '', slug: Annotated[str, Form()] = '', description: Annotated[str, Form()] = '', cta_text: Annotated[str, Form()] = 'ثبت‌نام رایگان', platform_target: Annotated[str, Form()] = 'app', telegram_link: Annotated[str, Form()] = '', instagram_link: Annotated[str, Form()] = '', app_link: Annotated[str, Form()] = '', website_link: Annotated[str, Form()] = '', theme_color: Annotated[str, Form()] = '#2563eb', collect_phone: Annotated[str | None, Form()] = None, collect_name: Annotated[str | None, Form()] = None, collect_email: Annotated[str | None, Form()] = None):
    slug = slug.strip().lower().replace(' ', '-')
    if not slug or not title.strip(): return RedirectResponse(url='/growth/pages', status_code=303)
    existing = db.scalar(select(LandingPage).where(LandingPage.slug == slug))
    if existing: return RedirectResponse(url=f'/growth/pages/{existing.id}', status_code=303)
    page = LandingPage(slug=slug, title=title.strip(), description=description.strip() or None, cta_text=cta_text.strip(), platform_target=platform_target, telegram_link=telegram_link.strip() or None, instagram_link=instagram_link.strip() or None, app_link=app_link.strip() or None, website_link=website_link.strip() or None, theme_color=theme_color.strip(), collect_phone=collect_phone is not None, collect_name=collect_name is not None, collect_email=collect_email is not None)
    db.add(page); db.commit(); db.refresh(page)
    return RedirectResponse(url=f'/growth/pages/{page.id}', status_code=303)


@router.get('/growth/pages/{page_id}', response_class=HTMLResponse)
def page_edit(page_id: int, db: Session = Depends(get_db)):
    p = db.get(LandingPage, page_id)
    if not p: raise HTTPException(404)
    signups_count = db.scalar(select(func.count(LandingSignup.id)).where(LandingSignup.landing_page_id == p.id)) or 0
    body = f'''<div class="card"><h3>ویرایش لندینگ پیج: {h(p.title)}</h3>
    <form method="post" action="/growth/pages/{p.id}/update">
    <label>عنوان<br><input name="title" value="{h(p.title)}" style="min-width:300px"></label><br>
    <label>Slug<br><input name="slug" value="{h(p.slug)}" style="width:150px"></label><br>
    <label>توضیحات<br><textarea name="description">{h(p.description or '')}</textarea></label><br>
    <label>متن دکمه<br><input name="cta_text" value="{h(p.cta_text)}"></label><br>
    <label>پلتفرم هدف<br><select name="platform_target"><option value="app" {'selected' if p.platform_target=='app' else ''}>اپ</option><option value="instagram" {'selected' if p.platform_target=='instagram' else ''}>اینستاگرام</option><option value="telegram" {'selected' if p.platform_target=='telegram' else ''}>تلگرام</option><option value="website" {'selected' if p.platform_target=='website' else ''}>وب‌سایت</option></select></label><br>
    <label>لینک تلگرام<br><input name="telegram_link" value="{h(p.telegram_link or '')}" style="min-width:300px"></label><br>
    <label>لینک اینستاگرام<br><input name="instagram_link" value="{h(p.instagram_link or '')}" style="min-width:300px"></label><br>
    <label>لینک اپ<br><input name="app_link" value="{h(p.app_link or '')}" style="min-width:300px"></label><br>
    <label>رنگ تم<br><input name="theme_color" value="{h(p.theme_color)}" style="width:100px"></label><br>
    <label><input type="checkbox" name="collect_name" {'checked' if p.collect_name else ''}> نام</label>
    <label><input type="checkbox" name="collect_phone" {'checked' if p.collect_phone else ''}> تلفن</label>
    <label><input type="checkbox" name="collect_email" {'checked' if p.collect_email else ''}> ایمیل</label>
    <label><input type="checkbox" name="is_active" {'checked' if p.is_active else ''}> فعال</label><br><br>
    <button>ذخیره</button>
    </form></div>
    <div class="card"><h3>🔗 لینک مستقیم</h3><p><a target="_blank" href="/lp/{h(p.slug)}">http://your-domain.com/lp/{h(p.slug)}</a></p><p class="muted">ثبت‌نام‌ها: {signups_count}</p></div>'''
    return layout(f'ویرایش: {p.title}', body)


@router.post('/growth/pages/{page_id}/update')
def page_update(page_id: int, db: Session = Depends(get_db), title: Annotated[str, Form()] = '', slug: Annotated[str, Form()] = '', description: Annotated[str, Form()] = '', cta_text: Annotated[str, Form()] = '', platform_target: Annotated[str, Form()] = '', telegram_link: Annotated[str, Form()] = '', instagram_link: Annotated[str, Form()] = '', app_link: Annotated[str, Form()] = '', theme_color: Annotated[str, Form()] = '#2563eb', collect_phone: Annotated[str | None, Form()] = None, collect_name: Annotated[str | None, Form()] = None, collect_email: Annotated[str | None, Form()] = None, is_active: Annotated[str | None, Form()] = None):
    p = db.get(LandingPage, page_id)
    if not p: raise HTTPException(404)
    p.title = title.strip() or p.title; p.slug = slug.strip() or p.slug; p.description = description.strip() or None
    p.cta_text = cta_text.strip() or p.cta_text; p.platform_target = platform_target
    p.telegram_link = telegram_link.strip() or None; p.instagram_link = instagram_link.strip() or None; p.app_link = app_link.strip() or None
    p.theme_color = theme_color.strip(); p.collect_phone = collect_phone is not None; p.collect_name = collect_name is not None; p.collect_email = collect_email is not None; p.is_active = is_active is not None
    db.add(p); db.commit()
    return RedirectResponse(url=f'/growth/pages/{page_id}', status_code=303)


@router.get('/growth/pages/{page_id}/signups', response_class=HTMLResponse)
def page_signups(page_id: int, db: Session = Depends(get_db)):
    p = db.get(LandingPage, page_id)
    if not p: raise HTTPException(404)
    signups = list(db.scalars(select(LandingSignup).where(LandingSignup.landing_page_id == page_id).order_by(desc(LandingSignup.created_at)).limit(200)).all())
    rows = ''.join(f'''<tr><td>{h(s.name or '-')}</td><td>{h(s.phone or '-')}</td><td>{h(s.email or '-')}</td><td>{h(s.referral_code or '-')}</td><td>{h(s.source or '-')}</td><td>{fmt_dt(s.created_at)}</td></tr>''' for s in signups)
    body = f'''<div class="card"><h3>ثبت‌نام‌های لندینگ پیج: {h(p.title)}</h3><p class="muted">کل: {len(signups)}</p>
    <table><thead><tr><th>نام</th><th>تلفن</th><th>ایمیل</th><th>کد ریفرال</th><th>منبع</th><th>زمان</th></tr></thead><tbody>{rows or '<tr><td colspan="6">ثبت‌نامی نداشته</td></tr>'}</tbody></table>
    <a class="btn" href="/growth/pages/{p.id}/signups/export.csv">📥 CSV</a></div>'''
    return layout(f'ثبت‌نام‌ها: {p.title}', body)


@router.get('/growth/pages/{page_id}/signups/export.csv')
def signups_export(page_id: int, db: Session = Depends(get_db)):
    p = db.get(LandingPage, page_id)
    signups = list(db.scalars(select(LandingSignup).where(LandingSignup.landing_page_id == page_id).order_by(desc(LandingSignup.created_at))).all())
    output = io.StringIO(); output.write('\ufeff')
    w = csv.writer(output)
    w.writerow(['نام','تلفن','ایمیل','یوزرنیم','کد ریفرال','منبع','زمان'])
    for s in signups: w.writerow([s.name, s.phone, s.email, s.username, s.referral_code, s.source, s.created_at])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename=signups-{p.slug if p else "page"}.csv'})


# ============================================================
# PUBLIC: Landing Page (no auth)
# ============================================================

@router.get('/lp/{slug}', response_class=HTMLResponse)
def public_landing_page(slug: str, request: Request, ref: str = Query(''), utm_source: str = Query(''), utm_medium: str = Query(''), utm_campaign: str = Query(''), db: Session = Depends(get_db)):
    page = db.scalar(select(LandingPage).where(LandingPage.slug == slug, LandingPage.is_active == True))
    if not page: raise HTTPException(404, 'صفحه پیدا نشد')
    color = page.theme_color or '#2563eb'

    # Track referral click if ref code present
    if ref:
        rlink = db.scalar(select(ReferralLink).where(ReferralLink.code == ref, ReferralLink.is_active == True))
        if rlink:
            rlink.click_count += 1
            db.add(ReferralClick(referral_link_id=rlink.id, ip_address=request.client.host if request.client else None, user_agent=str(request.headers.get('user-agent', ''))[:500]))
            db.add(rlink); db.commit()

    # Build social links section
    social_html = ''
    if page.telegram_link: social_html += f'<a href="{h(page.telegram_link)}" target="_blank" class="social-btn telegram">✈️ کانال تلگرام</a>'
    if page.instagram_link: social_html += f'<a href="{h(page.instagram_link)}" target="_blank" class="social-btn instagram">📸 پیج اینستاگرام</a>'
    if page.app_link: social_html += f'<a href="{h(page.app_link)}" target="_blank" class="social-btn app">🎮 دانلود اپ</a>'
    if page.website_link: social_html += f'<a href="{h(page.website_link)}" target="_blank" class="social-btn web">🌐 وب‌سایت</a>'

    # Form fields
    form_fields = ''
    if page.collect_name: form_fields += '<input name="name" placeholder="نام و نام خانوادگی" required style="width:100%;padding:14px;border-radius:12px;border:1px solid #d0d5dd;margin-bottom:10px;font-size:15px">'
    if page.collect_phone: form_fields += '<input name="phone" placeholder="شماره تلفن" type="tel" required style="width:100%;padding:14px;border-radius:12px;border:1px solid #d0d5dd;margin-bottom:10px;font-size:15px">'
    if page.collect_email: form_fields += '<input name="email" placeholder="ایمیل (اختیاری)" type="email" style="width:100%;padding:14px;border-radius:12px;border:1px solid #d0d5dd;margin-bottom:10px;font-size:15px">'
    form_fields += '<input name="username" placeholder="آیدی تلگرام/اینستاگرام (اختیاری)" style="width:100%;padding:14px;border-radius:12px;border:1px solid #d0d5dd;margin-bottom:10px;font-size:15px">'

    html = f'''<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(page.title)}</title>
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:Tahoma,Arial,sans-serif;direction:rtl;min-height:100vh;background:linear-gradient(135deg,{color}15,{color}08,#fff)}}
      .lp-wrap{{max-width:500px;margin:0 auto;padding:30px 20px;min-height:100vh;display:flex;flex-direction:column;justify-content:center}}
      .lp-hero{{text-align:center;margin-bottom:30px}}
      .lp-hero h1{{font-size:28px;color:#101828;margin-bottom:12px;line-height:1.5}}
      .lp-hero p{{color:#667085;font-size:15px;line-height:1.8}}
      .lp-form{{background:white;border-radius:20px;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,.1);margin-bottom:20px}}
      .lp-form h3{{text-align:center;margin-bottom:18px;color:#101828}}
      .submit-btn{{width:100%;padding:16px;border-radius:12px;border:0;background:{color};color:white;font-size:17px;font-weight:700;cursor:pointer;margin-top:8px}}
      .submit-btn:hover{{opacity:.9}}
      .social-links{{display:flex;flex-direction:column;gap:10px;margin-top:20px}}
      .social-btn{{display:block;text-align:center;padding:14px;border-radius:12px;text-decoration:none;font-weight:600;font-size:15px;border:2px solid #e4e7ec;color:#344054;background:white}}
      .social-btn.telegram{{border-color:#0088cc;color:#0088cc}}.social-btn.instagram{{border-color:#e1306c;color:#e1306c}}
      .social-btn.app{{border-color:{color};color:{color};background:{color}10}}.social-btn.web{{border-color:#059669;color:#059669}}
      .footer{{text-align:center;color:#9ca3af;font-size:12px;margin-top:20px}}
    </style></head><body>
    <div class="lp-wrap">
      <div class="lp-hero"><h1>{h(page.title)}</h1>{f'<p>{h(page.description)}</p>' if page.description else ''}</div>
      <div class="lp-form"><h3>{h(page.cta_text)}</h3>
        <form method="post" action="/lp/{h(page.slug)}/signup">
          <input type="hidden" name="ref" value="{h(ref)}">
          <input type="hidden" name="utm_source" value="{h(utm_source)}">
          <input type="hidden" name="utm_medium" value="{h(utm_medium)}">
          <input type="hidden" name="utm_campaign" value="{h(utm_campaign)}">
          {form_fields}
          <button type="submit" class="submit-btn">{h(page.cta_text)}</button>
        </form>
      </div>
      {f'<div class="social-links">{social_html}</div>' if social_html else ''}
      <div class="footer">🎮 گیمینگ پلتفرم</div>
    </div></body></html>'''
    return HTMLResponse(html)


@router.post('/lp/{slug}/signup')
async def public_signup(slug: str, request: Request, db: Session = Depends(get_db), name: Annotated[str, Form()] = '', phone: Annotated[str, Form()] = '', email: Annotated[str, Form()] = '', username: Annotated[str, Form()] = '', ref: Annotated[str, Form()] = '', utm_source: Annotated[str, Form()] = '', utm_medium: Annotated[str, Form()] = '', utm_campaign: Annotated[str, Form()] = ''):
    page = db.scalar(select(LandingPage).where(LandingPage.slug == slug, LandingPage.is_active == True))
    if not page: raise HTTPException(404)

    signup = LandingSignup(landing_page_id=page.id, name=name.strip() or None, phone=phone.strip() or None, email=email.strip() or None, username=username.strip() or None, referral_code=ref.strip() or None, source=utm_source.strip() or None, medium=utm_medium.strip() or None, campaign=utm_campaign.strip() or None, ip_address=request.client.host if request.client else None, user_agent=str(request.headers.get('user-agent', ''))[:500])
    db.add(signup)

    # Track referral signup
    if ref:
        rlink = db.scalar(select(ReferralLink).where(ReferralLink.code == ref, ReferralLink.is_active == True))
        if rlink:
            rlink.signup_count += 1
            rlink.points += 10  # 10 points per signup
            db.add(rlink)

    db.commit()

    # Redirect to social links or thank you
    if page.redirect_url:
        return RedirectResponse(url=page.redirect_url, status_code=303)
    return RedirectResponse(url=f'/lp/{slug}/thanks', status_code=303)


@router.get('/lp/{slug}/thanks', response_class=HTMLResponse)
def public_thanks(slug: str, db: Session = Depends(get_db)):
    page = db.scalar(select(LandingPage).where(LandingPage.slug == slug, LandingPage.is_active == True))
    if not page: raise HTTPException(404)
    color = page.theme_color or '#2563eb'

    social_html = ''
    if page.telegram_link: social_html += f'<a href="{h(page.telegram_link)}" target="_blank" class="social-btn telegram">✈️ عضویت در کانال تلگرام</a>'
    if page.instagram_link: social_html += f'<a href="{h(page.instagram_link)}" target="_blank" class="social-btn instagram">📸 فالو در اینستاگرام</a>'
    if page.app_link: social_html += f'<a href="{h(page.app_link)}" target="_blank" class="social-btn app">🎮 دانلود اپ گیمینگ</a>'

    return HTMLResponse(f'''<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ثبت‌نام موفق</title>
    <style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:Tahoma,Arial,sans-serif;direction:rtl;min-height:100vh;background:linear-gradient(135deg,{color}15,#fff);display:flex;align-items:center;justify-content:center}}
    .box{{max-width:450px;padding:40px;text-align:center;background:white;border-radius:24px;box-shadow:0 20px 60px rgba(0,0,0,.1)}}
    .box h1{{font-size:48px;margin-bottom:10px}}.box h2{{color:#101828;margin-bottom:10px}}.box p{{color:#667085;margin-bottom:24px;font-size:15px;line-height:1.8}}
    .social-btn{{display:block;text-align:center;padding:14px;border-radius:12px;text-decoration:none;font-weight:600;font-size:15px;border:2px solid #e4e7ec;color:#344054;background:white;margin:10px 0}}
    .social-btn.telegram{{border-color:#0088cc;color:#0088cc}}.social-btn.instagram{{border-color:#e1306c;color:#e1306c}}.social-btn.app{{border-color:{color};color:{color}}}
    </style></head><body><div class="box"><h1>✅</h1><h2>ثبت‌نام شما موفق بود!</h2><p>ممنون از ثبت‌نامتون. حالا می‌تونید به جمع گیمرهای ما بپیوندید:</p>
    {social_html}<p style="margin-top:20px;font-size:13px;color:#9ca3af">🎮 به جمع ما خوش اومدید!</p></div></body></html>''')


# ============================================================
# ADMIN: Referral System
# ============================================================

@router.get('/growth/referrals', response_class=HTMLResponse)
def referrals_list(db: Session = Depends(get_db)):
    links = list(db.scalars(select(ReferralLink).order_by(desc(ReferralLink.points))).all())
    rows = ''
    for r in links:
        rows += f'''<tr><td><b>{h(r.owner_name)}</b><br><span class="muted">{h(r.owner_telegram or '')} {h(r.owner_instagram or '')}</span></td><td><code>{h(r.code)}</code></td><td>{r.click_count}</td><td>{r.signup_count}</td><td><b style="color:#059669">{r.points}</b></td><td><span class="badge {'green' if r.is_active else 'red'}">{'فعال' if r.is_active else 'غیرفعال'}</span></td></tr>'''
    body = f'''<div class="card"><h3>🔗 لینک‌های ریفرال</h3><table><thead><tr><th>مالک</th><th>کد</th><th>کلیک</th><th>ثبت‌نام</th><th>امتیاز</th><th>وضعیت</th></tr></thead><tbody>{rows or '<tr><td colspan="6">لینک ریفرالی ندارید</td></tr>'}</tbody></table></div>
    <div class="card"><h3>➕ ساخت لینک ریفرال جدید</h3>
    <form method="post" action="/growth/referrals"><input name="owner_name" placeholder="نام صاحب لینک" required><input name="owner_phone" placeholder="تلفن"><input name="owner_telegram" placeholder="آیدی تلگرام"><input name="owner_instagram" placeholder="آیدی اینستاگرام"><input name="target_url" placeholder="لینک هدف (اختیاری)" style="min-width:300px"><button>ساخت لینک</button></form>
    <div class="hint"><b>نحوه استفاده:</b> هر لینک ریفرال یک کد اختصاصی داره. لینک رو به این شکل بدید:<br><code>/lp/your-slug?ref=CODE</code><br>هر کلیک = ۱ امتیاز، هر ثبت‌نام = ۱۰ امتیاز</div></div>'''
    return layout('سیستم ریفرال', body)


@router.post('/growth/referrals')
def referral_create(db: Session = Depends(get_db), owner_name: Annotated[str, Form()] = '', owner_phone: Annotated[str, Form()] = '', owner_telegram: Annotated[str, Form()] = '', owner_instagram: Annotated[str, Form()] = '', target_url: Annotated[str, Form()] = ''):
    if not owner_name.strip(): return RedirectResponse(url='/growth/referrals', status_code=303)
    code = _gen_code(8)
    while db.scalar(select(ReferralLink).where(ReferralLink.code == code)):
        code = _gen_code(8)
    link = ReferralLink(code=code, owner_name=owner_name.strip(), owner_phone=owner_phone.strip() or None, owner_telegram=owner_telegram.strip() or None, owner_instagram=owner_instagram.strip() or None, target_url=target_url.strip() or None)
    db.add(link); db.commit()
    return RedirectResponse(url='/growth/referrals', status_code=303)


# ============================================================
# ADMIN: Welcome Messages
# ============================================================

@router.get('/growth/welcome', response_class=HTMLResponse)
def welcome_list(db: Session = Depends(get_db)):
    messages = list(db.scalars(select(WelcomeMessage).order_by(WelcomeMessage.id)).all())
    rows = ''
    for m in messages:
        rows += f'''<tr><td><b>{h(m.name)}</b></td><td>{h(m.platform)}</td><td class="muted">{h(m.body[:100])}...</td><td><span class="badge {'green' if m.is_active else 'red'}">{'فعال' if m.is_active else 'غیرفعال'}</span></td></tr>'''
    body = f'''<div class="card"><h3>👋 قالب‌های پیام خوش‌آمد</h3><table><thead><tr><th>نام</th><th>پلتفرم</th><th>متن</th><th>وضعیت</th></tr></thead><tbody>{rows or '<tr><td colspan="4">قالبی ندارید</td></tr>'}</tbody></table></div>
    <div class="card"><h3>➕ ساخت قالب خوش‌آمد</h3>
    <form method="post" action="/growth/welcome"><input name="name" placeholder="نام قالب" required><select name="platform"><option value="general">عمومی</option><option value="telegram">تلگرام</option><option value="instagram">اینستاگرام</option><option value="app">اپ</option></select><br><textarea name="body_text" placeholder="متن پیام خوش‌آمد" style="min-height:100px"></textarea><br><label><input type="checkbox" name="include_referral" checked> لینک ریفرال اضافه بشه</label><label><input type="checkbox" name="include_social" checked> لینک‌های اجتماعی اضافه بشن</label><button>ذخیره</button></form>
    <div class="hint"><b>متغیرهای قابل استفاده:</b> <code>{{name}}</code> = نام مخاطب، <code>{{referral_link}}</code> = لینک ریفرال، <code>{{telegram_link}}</code> = لینک تلگرام، <code>{{instagram_link}}</code> = لینک اینستاگرام</div></div>'''
    return layout('پیام خوش‌آمد', body)


@router.post('/growth/welcome')
def welcome_create(db: Session = Depends(get_db), name: Annotated[str, Form()] = '', platform: Annotated[str, Form()] = 'general', body_text: Annotated[str, Form()] = '', include_referral: Annotated[str | None, Form()] = None, include_social: Annotated[str | None, Form()] = None):
    if name.strip() and body_text.strip():
        db.add(WelcomeMessage(name=name.strip(), platform=platform, body=body_text.strip(), include_referral_link=include_referral is not None, include_social_links=include_social is not None))
        db.commit()
    return RedirectResponse(url='/growth/welcome', status_code=303)


# ============================================================
# ADMIN: Content Calendar
# ============================================================

@router.get('/growth/content', response_class=HTMLResponse)
def content_list(db: Session = Depends(get_db)):
    posts = list(db.scalars(select(ContentPost).order_by(ContentPost.scheduled_at.nullsfirst(), desc(ContentPost.created_at)).limit(50)).all())
    rows = ''
    for p in posts:
        status_cls = {'draft': '', 'scheduled': 'blue', 'posted': 'green', 'cancelled': 'red'}.get(p.status, '')
        rows += f'''<tr><td><b>{h(p.title or p.text[:50])}</b></td><td>{h(p.platform)}</td><td>{h(p.content_type)}</td><td><span class="badge {status_cls}">{h(p.status)}</span></td><td>{fmt_dt(p.scheduled_at)}</td><td>{fmt_dt(p.posted_at)}</td></tr>'''
    body = f'''<div class="card"><h3>📅 تقویم محتوایی</h3><table><thead><tr><th>محتوا</th><th>پلتفرم</th><th>نوع</th><th>وضعیت</th><th>زمان‌بندی</th><th>ارسال</th></tr></thead><tbody>{rows or '<tr><td colspan="6">محتوایی ندارید</td></tr>'}</tbody></table></div>
    <div class="card"><h3>➕ افزودن محتوا</h3>
    <form method="post" action="/growth/content"><input name="title" placeholder="عنوان" style="min-width:300px"><select name="platform"><option value="both">اینستاگرام + تلگرام</option><option value="instagram">اینستاگرام</option><option value="telegram">تلگرام</option></select><select name="content_type"><option value="post">پست</option><option value="story">استوری</option><option value="reel">ریلز</option><option value="channel_post">پست کانال</option></select><br><textarea name="text" placeholder="متن محتوا" style="min-height:100px"></textarea><br><input name="hashtags" placeholder="هشتگ‌ها"><input name="scheduled_at" type="datetime-local"><select name="status"><option value="draft">پیش‌نویس</option><option value="scheduled">زمان‌بندی شده</option></select><button>ذخیره</button></form></div>
    <div class="card"><h3>💡 ایده‌های محتوایی برای جذب مخاطب</h3>
    <div class="grid2">
      <div class="hint"><b>🎮 ایده‌های اینستاگرام:</b><ul style="margin-top:8px;line-height:2"><li>ریلز گیم‌پلی با صدا ترند</li><li>استوری نظرسنجی: کدوم بازی بهتره؟</li><li>پست آموزشی: ۵ تاپ برای CP رایگان</li><li>کاروسل: معرفی فروشگاه‌های برتر</li><li>لایو: آنباکسینگ گیفت کارت</li></ul></div>
      <div class="hint"><b>✈️ ایده‌های تلگرام:</b><ul style="margin-top:8px;line-height:2"><li>کانال: خبر آپدیت بازی‌ها</li><li>کانال: آفرهای تخفیف گیفت کارت</li><li>گروه: چالش هفتگی + جایزه</li><li>ربات: چک قیمت لحظه‌ای</li><li>نظرسنجی: بهترین بازی ماه</li></ul></div>
    </div></div>'''
    return layout('تقویم محتوا', body)


@router.post('/growth/content')
def content_create(db: Session = Depends(get_db), title: Annotated[str, Form()] = '', platform: Annotated[str, Form()] = 'both', content_type: Annotated[str, Form()] = 'post', text: Annotated[str, Form()] = '', hashtags: Annotated[str, Form()] = '', scheduled_at: Annotated[str, Form()] = '', status: Annotated[str, Form()] = 'draft'):
    if not text.strip(): return RedirectResponse(url='/growth/content', status_code=303)
    sched = None
    if scheduled_at:
        try: sched = datetime.strptime(scheduled_at, '%Y-%m-%dT%H:%M')
        except: pass
    db.add(ContentPost(platform=platform, content_type=content_type, title=title.strip() or None, text=text.strip(), hashtags=hashtags.strip() or None, scheduled_at=sched, status=status))
    db.commit()
    return RedirectResponse(url='/growth/content', status_code=303)


# ============================================================
# API
# ============================================================

@router.get('/api/growth/stats')
def api_growth_stats(db: Session = Depends(get_db)):
    return {
        'landing_pages': db.scalar(select(func.count(LandingPage.id))) or 0,
        'total_signups': db.scalar(select(func.count(LandingSignup.id))) or 0,
        'referral_links': db.scalar(select(func.count(ReferralLink.id))) or 0,
        'total_referral_clicks': db.scalar(select(func.sum(ReferralLink.click_count))) or 0,
        'welcome_templates': db.scalar(select(func.count(WelcomeMessage.id))) or 0,
        'content_posts': db.scalar(select(func.count(ContentPost.id))) or 0,
    }
