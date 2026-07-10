from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS_PREFIXES = ('utm_',)
TRACKING_PARAMS = {'fbclid', 'gclid', 'yclid', 'mc_cid', 'mc_eid'}


def normalize_url(url: str | None) -> str:
    """Normalize URLs enough for de-duplication without changing their actual target."""
    if not url:
        return ''
    url = url.strip()
    parts = urlsplit(url)
    scheme = (parts.scheme or 'https').lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip('/') or '/'
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lk = key.lower()
        if lk in TRACKING_PARAMS or any(lk.startswith(p) for p in TRACKING_PARAMS_PREFIXES):
            continue
        query_items.append((key, value))
    query = urlencode(query_items, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ''))


def public_invite_message(title: str | None = None, category: str | None = None) -> str:
    label = category or 'محصولات و خدمات گیمینگ'
    return (
        'سلام وقتتون بخیر 🌹\n'
        f'دیدم در زمینه {label} فعالیت دارید.\n'
        'ما یک پلتفرم تخصصی برای آگهی و فروش محصولات گیمینگ راه‌اندازی کردیم. '
        'ثبت آگهی برای فروشنده‌ها فعلاً رایگانه و مخاطب‌ها کاملاً هدفمند هستن.\n'
        'اگر مایل بودید خوشحال می‌شیم آگهی/فروشگاهتون رو اونجا هم ثبت کنید.\n'
        'لینک ثبت آگهی: YOUR_SITE_LINK\n'
        'موفق باشید 🙏'
    )


def extract_social_links(text: str, base_url: str | None = None) -> dict[str, str | None]:
    text = text or ''
    instagram = None
    telegram = None

    ig_match = re.search(r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,60})/?', text, re.I)
    if ig_match:
        username = ig_match.group(1).strip('.').lower()
        if username not in {'p', 'reel', 'explore', 'accounts'}:
            instagram = f'https://instagram.com/{username}'

    tg_match = re.search(r'https?://(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})/?', text, re.I)
    if tg_match:
        telegram = f'https://t.me/{tg_match.group(1)}'

    return {'instagram': instagram, 'telegram': telegram}
