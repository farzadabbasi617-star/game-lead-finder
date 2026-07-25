"""
Activity Checker
================
تشخیص اینکه یک پیج اینستاگرام / کانال تلگرام واقعاً فعاله یا نه.

معیار فعال بودن:
- پست جدید در ۳۰ روز اخیر
- پروفایل موجود (۴۰۴ نگیره)
- Private نباشه (برای اینستاگرام)
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger('influencer.activity')

ACTIVITY_WINDOW_DAYS = 30
_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


@dataclass
class ActivityStatus:
    is_active: bool
    exists: bool
    is_private: bool = False
    last_post_at: Optional[datetime] = None
    days_since_last_post: Optional[int] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            'is_active': self.is_active,
            'exists': self.exists,
            'is_private': self.is_private,
            'last_post_at': self.last_post_at.isoformat() if self.last_post_at else None,
            'days_since_last_post': self.days_since_last_post,
            'reason': self.reason,
        }


# ─── Telegram Activity Check ───────────────────────────────────────

async def check_telegram_activity(username: str, window_days: int = ACTIVITY_WINDOW_DAYS) -> ActivityStatus:
    """چک میکنه که کانال تلگرام در N روز اخیر پست جدید داشته یا نه.

    استراتژی چند مرحله‌ای:
    1) t.me/s/USERNAME - صفحه preview (بهترین منبع)
    2) اگر preview نداد، t.me/USERNAME - صفحه اصلی
    3) اگر هر کدوم wide-open HTML داد و subscribers > 0 → موجود (unknown activity, فرض فعال)
    """
    headers = {'User-Agent': _UA, 'Accept-Language': 'en-US,en;q=0.9'}

    async def fetch(url):
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            return await client.get(url, headers=headers)

    try:
        # مرحله ۱: preview
        r = await fetch(f'https://t.me/s/{username}')
        if r.status_code == 404:
            return ActivityStatus(is_active=False, exists=False, reason='کانال پیدا نشد (۴۰۴)')
        if r.status_code != 200:
            return ActivityStatus(is_active=False, exists=False, reason=f'HTTP {r.status_code}')

        html = r.text
        # پیدا کردن تاریخ آخرین پست از preview
        dates = []
        for m in re.finditer(r'<time[^>]+datetime="([^"]+)"', html):
            try:
                dt_str = m.group(1)
                if dt_str.endswith('Z'):
                    dt_str = dt_str[:-1] + '+00:00'
                dates.append(datetime.fromisoformat(dt_str))
            except (ValueError, TypeError):
                continue

        if dates:
            latest = max(dates)
            now = datetime.now(timezone.utc)
            days_ago = (now - latest).days
            is_active = days_ago <= window_days
            return ActivityStatus(
                is_active=is_active, exists=True,
                last_post_at=latest, days_since_last_post=days_ago,
                reason=(f'آخرین پست {days_ago} روز پیش' if is_active
                        else f'آخرین پست {days_ago} روز پیش (>{window_days} روز)'),
            )

        # مرحله ۲: preview پست نداشت، از صفحه اصلی چک کن که موجوده و subscribers داره
        r2 = await fetch(f'https://t.me/{username}')
        if r2.status_code != 200:
            return ActivityStatus(is_active=False, exists=False, reason='صفحه اصلی هم پاسخ نداد')

        html2 = r2.text
        # چک: آیا صفحه join دارد (یعنی کانال معتبره)
        has_join = 'tgme_action_button' in html2 or 'tgme_page_extra' in html2 or 'View in Telegram' in html2
        if not has_join:
            return ActivityStatus(is_active=False, exists=False, reason='صفحه معتبر پیدا نشد')

        # چک تعداد subscribers
        subs = 0
        for pat in [
            r'([\d,.\s]+)\s*subscriber',
            r'([\d,.\s]+)\s*member',
            r'([\d,.\s]+)\s*عضو',
        ]:
            m = re.search(pat, html2, re.I)
            if m:
                num = re.sub(r'[^\d]', '', m.group(1))
                if num:
                    subs = int(num)
                    break

        if subs > 0:
            # کانال موجود و subscribers داره ولی preview نمیده - فرض فعال با confidence پایین
            return ActivityStatus(
                is_active=True, exists=True,
                reason=f'کانال {subs} عضو - preview مخفی (فرض فعال)',
            )

        return ActivityStatus(
            is_active=False, exists=True, is_private=True,
            reason='کانال بدون preview و subscribers',
        )
    except Exception as exc:
        logger.debug(f'TG activity {username} failed: {exc}')
        return ActivityStatus(is_active=False, exists=False, reason=f'خطا: {str(exc)[:80]}')


# ─── Instagram Activity Check ──────────────────────────────────────

async def check_instagram_activity(username: str, window_days: int = ACTIVITY_WINDOW_DAYS) -> ActivityStatus:
    """چک میکنه که پیج اینستاگرام فعاله یا نه.

    Instagram معمولاً از سرور Render بلاک میکنه، اما میتونیم چند چیز رو چک کنیم:
    - آیا پیج وجود داره (۴۰۴ نگرفتن)
    - آیا در og:description تعداد پست بزرگه (>10)
    - سعی کن از Instaloader یا نرم‌افزارهای دیگه استفاده کنی (fallback)

    نکته: چون Instagram تاریخ پست رو در HTML public نمیده، به عنوان heuristic:
    - اگر پیج موجود و public باشه، فرض میکنیم فعاله (تا وقتی که خلافش ثابت بشه)
    - اگر ۴۰۴ باشه، غیرفعاله
    - اگر private باشه، غیرفعال محسوب میشه
    """
    url = f'https://www.instagram.com/{username}/'
    headers = {
        'User-Agent': _UA,
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9,fa;q=0.7',
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)

        if r.status_code == 404:
            return ActivityStatus(is_active=False, exists=False, reason='پیج پیدا نشد (۴۰۴)')

        if r.status_code == 429:
            # rate limit - فرض کن موجوده و فعال (نمیتونیم مطمئن بشیم)
            return ActivityStatus(
                is_active=True, exists=True,
                reason='Rate limit (فرض فعال)',
            )

        if r.status_code != 200:
            return ActivityStatus(is_active=False, exists=False, reason=f'HTTP {r.status_code}')

        html = r.text[:500_000]
        # چک: پیج حذف شده یا نه
        if 'Sorry, this page isn' in html or 'کاربر یافت نشد' in html:
            return ActivityStatus(is_active=False, exists=False, reason='پیج حذف شده')

        # چک: private است یا نه
        # og:description معمولاً برای private فقط اسم رو نشون میده بدون تعداد پست
        # الگو: "1,234 Followers, 567 Following, 89 Posts - ..."
        desc_m = re.search(r'og:description["\s]+content="([^"]+)"', html)
        if desc_m:
            desc = desc_m.group(1)
            # چک تعداد پست
            pm = re.search(r'([\d,.]+[KkMm]?)\s+Posts', desc, re.I)
            if pm:
                posts_str = pm.group(1).replace(',', '').replace('.', '')
                mult = 1
                if posts_str[-1:].upper() == 'K':
                    mult = 1000; posts_str = posts_str[:-1]
                elif posts_str[-1:].upper() == 'M':
                    mult = 1_000_000; posts_str = posts_str[:-1]
                try:
                    posts_count = int(float(posts_str) * mult)
                    if posts_count == 0:
                        return ActivityStatus(
                            is_active=False, exists=True,
                            reason='صفر پست - غیرفعال',
                        )
                    # اگر پست داره، فرض کن فعاله
                    return ActivityStatus(
                        is_active=True, exists=True,
                        reason=f'{posts_count} پست (private نیست)',
                    )
                except (ValueError, TypeError):
                    pass

        # اگر og:description نداشت، ممکنه private یا مسدود شده باشه
        if 'This Account is Private' in html or 'private_account' in html:
            return ActivityStatus(
                is_active=False, exists=True, is_private=True,
                reason='پیج private',
            )

        # fallback: پیج موجود ولی مطمئن نیستیم - فرض فعال
        return ActivityStatus(
            is_active=True, exists=True,
            reason='پیج موجود (فرض فعال)',
        )
    except Exception as exc:
        logger.debug(f'IG activity {username} failed: {exc}')
        return ActivityStatus(is_active=False, exists=False, reason=f'خطا: {exc}')


# ─── Unified check ──────────────────────────────────────────────────

async def check_activity(platform: str, username: str, window_days: int = ACTIVITY_WINDOW_DAYS) -> ActivityStatus:
    """چک فعالیت یکپارچه برای هر پلتفرم."""
    if platform == 'telegram':
        return await check_telegram_activity(username, window_days)
    elif platform == 'instagram':
        return await check_instagram_activity(username, window_days)
    return ActivityStatus(is_active=False, exists=False, reason=f'پلتفرم پشتیبانی نمیشه: {platform}')
