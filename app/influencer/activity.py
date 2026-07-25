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
    entity_type: str = ""  # 'channel', 'group', 'bot', 'user' (فقط برای تلگرام)
    members_count: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            'is_active': self.is_active,
            'exists': self.exists,
            'is_private': self.is_private,
            'last_post_at': self.last_post_at.isoformat() if self.last_post_at else None,
            'days_since_last_post': self.days_since_last_post,
            'reason': self.reason,
            'entity_type': self.entity_type,
            'members_count': self.members_count,
        }


# ─── Telegram Activity Check ───────────────────────────────────────

def _detect_telegram_entity_type(html: str) -> tuple[str, int]:
    """از HTML صفحه t.me نوع entity رو تشخیص میده و تعداد اعضا/subscribers رو برمیگردونه.

    منبع اصلی: <div class="tgme_page_extra">TEXT</div>
        - "23 876 subscribers"           → کانال
        - "1 234 members"                → گروه
        - "1 234 members, 45 online"     → گروه
        - "@BotFather"                   → بات
        - (empty or username)            → کاربر یا placeholder

    Returns:
        (entity_type, count) که entity_type یکی از: 'channel', 'group', 'bot', 'user', 'unknown'
    """
    # اول از tgme_page_extra بخون
    extra_m = re.search(r'<div class="tgme_page_extra">([^<]+)</div>', html)
    extra_text = extra_m.group(1).strip() if extra_m else ''

    # چک subscribers (کانال)
    m = re.search(r'([\d,.\s]+)\s*subscribers?\b', extra_text, re.I)
    if m:
        num_str = re.sub(r'[^\d]', '', m.group(1))
        return ('channel', int(num_str) if num_str else 0)

    # چک members (گروه)
    m = re.search(r'([\d,.\s]+)\s*members?\b', extra_text, re.I)
    if m:
        num_str = re.sub(r'[^\d]', '', m.group(1))
        return ('group', int(num_str) if num_str else 0)

    # چک فارسی: عضو
    m = re.search(r'([\d,.\s]+)\s*عضو', extra_text)
    if m:
        num_str = re.sub(r'[^\d]', '', m.group(1))
        count = int(num_str) if num_str else 0
        # تشخیص از URL یا context اضافه
        if 'مشترک' in extra_text or 'دنبال' in extra_text:
            return ('channel', count)
        return ('group', count)

    # اگه فقط @username توی extra بود → بات یا کاربر
    if extra_text.startswith('@'):
        # بات‌ها معمولاً به bot ختم میشن
        if extra_text.lower().endswith('bot'):
            return ('bot', 0)
        return ('user', 0)

    # fallback: کل HTML رو چک کن
    m = re.search(r'([\d,.\s]+)\s*subscribers?\b', html, re.I)
    if m:
        num_str = re.sub(r'[^\d]', '', m.group(1))
        return ('channel', int(num_str) if num_str else 0)

    m = re.search(r'([\d,.\s]+)\s*members?\b', html, re.I)
    if m:
        num_str = re.sub(r'[^\d]', '', m.group(1))
        return ('group', int(num_str) if num_str else 0)

    # هیچی پیدا نشد
    return ('unknown', 0)


async def check_telegram_activity(
    username: str,
    window_days: int = ACTIVITY_WINDOW_DAYS,
    allow_types: tuple[str, ...] = ('channel', 'group'),
) -> ActivityStatus:
    """چک میکنه که کانال یا گروه تلگرام فعال و از نوع مجاز باشه.

    allow_types: کدوم نوع‌ها قابل قبولن. پیش‌فرض: کانال + گروه (بدون bot و user).

    استراتژی چند مرحله‌ای:
    1) t.me/s/USERNAME → صفحه preview (فقط کانال‌ها preview دارن)
    2) اگر preview نداد → t.me/USERNAME → تشخیص نوع + تعداد
    3) گروه‌ها معمولاً preview ندارن، پس با تعداد members جدا بررسی میشن
    """
    headers = {'User-Agent': _UA, 'Accept-Language': 'en-US,en;q=0.9'}

    async def fetch(url):
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            return await client.get(url, headers=headers)

    try:
        # مرحله ۱: preview (فقط برای کانال کار میکنه)
        r = await fetch(f'https://t.me/s/{username}')
        if r.status_code == 404:
            return ActivityStatus(is_active=False, exists=False, reason='پیدا نشد (۴۰۴)')
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

        # همچنین از خود HTML این مرحله چک کن entity_type رو (چون t.me/s/GROUP → redirect به t.me/GROUP)
        entity_type_early, count_early = _detect_telegram_entity_type(html)

        if dates:
            latest = max(dates)
            now = datetime.now(timezone.utc)
            days_ago = (now - latest).days
            is_active = days_ago <= window_days
            entity_type = entity_type_early
            if entity_type == 'unknown':
                entity_type = 'channel'  # preview فقط برای کانال هست معمولاً
            # چک: نوع مجاز هست؟
            if entity_type not in allow_types:
                return ActivityStatus(
                    is_active=False, exists=True, entity_type=entity_type,
                    members_count=count_early, last_post_at=latest, days_since_last_post=days_ago,
                    reason=f'نوع {entity_type} مجاز نیست',
                )
            type_label = {'channel': 'کانال', 'group': 'گروه', 'bot': 'بات', 'user': 'کاربر'}.get(entity_type, entity_type)
            return ActivityStatus(
                is_active=is_active, exists=True, entity_type=entity_type,
                members_count=count_early if count_early > 0 else None,
                last_post_at=latest, days_since_last_post=days_ago,
                reason=(f'{type_label} · آخرین پست {days_ago} روز پیش' if is_active
                        else f'{type_label} · آخرین پست {days_ago} روز پیش (>{window_days} روز)'),
            )

        # مرحله ۲: preview نداد → از HTML قبلی استفاده کن (redirect شده) یا صفحه اصلی
        if entity_type_early != 'unknown' and count_early > 0:
            html2 = html
            entity_type, count = entity_type_early, count_early
        else:
            r2 = await fetch(f'https://t.me/{username}')
            if r2.status_code != 200:
                return ActivityStatus(is_active=False, exists=False, reason='صفحه اصلی پاسخ نداد')
            html2 = r2.text
            has_join = 'tgme_action_button' in html2 or 'tgme_page_extra' in html2 or 'View in Telegram' in html2
            if not has_join:
                return ActivityStatus(is_active=False, exists=False, reason='صفحه معتبر نیست')
            entity_type, count = _detect_telegram_entity_type(html2)

        # چک: نوع مجاز هست؟
        if entity_type not in allow_types:
            type_label = {'channel': 'کانال', 'group': 'گروه', 'bot': 'بات', 'user': 'کاربر', 'unknown': 'نامشخص'}.get(entity_type, entity_type)
            return ActivityStatus(
                is_active=False, exists=True, entity_type=entity_type,
                members_count=count if count > 0 else None,
                reason=f'نوع {type_label} مجاز نیست',
            )

        # گروه‌های تلگرام preview ندارن ولی معتبرن اگه اعضای معنی‌داری داشته باشه
        # زیر ۱۰ عضو = گروه بی‌کاربر یا newly-created، رد میکنیم
        MIN_GROUP_MEMBERS = 10
        if entity_type == 'group':
            if count >= MIN_GROUP_MEMBERS:
                return ActivityStatus(
                    is_active=True, exists=True, entity_type='group',
                    members_count=count,
                    reason=f'گروه · {count:,} عضو (preview ندارد - فرض فعال)',
                )
            return ActivityStatus(
                is_active=False, exists=True, entity_type='group',
                members_count=count,
                reason=f'گروه · فقط {count} عضو (زیر {MIN_GROUP_MEMBERS} - بی‌فعالیت)',
            )

        # کانال با preview مخفی
        if entity_type == 'channel' and count > 0:
            return ActivityStatus(
                is_active=True, exists=True, entity_type='channel',
                members_count=count,
                reason=f'کانال · {count:,} عضو - preview مخفی (فرض فعال)',
            )

        # unknown با subscribers - فرض کانال
        if count > 0:
            return ActivityStatus(
                is_active=True, exists=True, entity_type='channel',
                members_count=count,
                reason=f'{count:,} عضو (نوع نامشخص)',
            )

        return ActivityStatus(
            is_active=False, exists=True, is_private=True, entity_type=entity_type,
            reason='بدون preview و بدون اعضا',
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

async def check_activity(
    platform: str,
    username: str,
    window_days: int = ACTIVITY_WINDOW_DAYS,
    telegram_allow_types: tuple[str, ...] = ('channel', 'group'),
) -> ActivityStatus:
    """چک فعالیت یکپارچه برای هر پلتفرم."""
    if platform == 'telegram':
        return await check_telegram_activity(username, window_days, allow_types=telegram_allow_types)
    elif platform in ('telegram_group', 'group'):
        return await check_telegram_activity(username, window_days, allow_types=('group',))
    elif platform == 'instagram':
        return await check_instagram_activity(username, window_days)
    return ActivityStatus(is_active=False, exists=False, reason=f'پلتفرم پشتیبانی نمیشه: {platform}')
