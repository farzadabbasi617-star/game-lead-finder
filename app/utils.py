from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS_PREFIXES = ('utm_',)
TRACKING_PARAMS = {'fbclid', 'gclid', 'yclid', 'mc_cid', 'mc_eid'}
MARKETPLACE_DOMAINS = {
    'instagram.com', 'www.instagram.com', 't.me', 'telegram.me', 'www.t.me',
    'divar.ir', 'www.divar.ir', 'sheypoor.com', 'www.sheypoor.com',
    'torob.com', 'www.torob.com', 'balad.ir', 'www.balad.ir',
    'google.com', 'www.google.com', 'maps.google.com', 'neshan.org', 'www.neshan.org',
}

PERSIAN_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def normalize_url(url: str | None) -> str:
    """Normalize URLs enough for de-duplication without changing their actual target."""
    if not url:
        return ''
    url = url.strip().strip('"\'')
    if not url:
        return ''
    if '://' not in url:
        url = 'https://' + url
    parts = urlsplit(url)
    scheme = (parts.scheme or 'https').lower()
    netloc = parts.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    path = parts.path.rstrip('/') or '/'
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lk = key.lower()
        if lk in TRACKING_PARAMS or any(lk.startswith(p) for p in TRACKING_PARAMS_PREFIXES):
            continue
        query_items.append((key, value))
    query = urlencode(query_items, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ''))


def normalize_phone(phone: str | None) -> str:
    if not phone:
        return ''
    digits = re.sub(r'\D+', '', phone.translate(PERSIAN_DIGITS))
    if digits.startswith('0098'):
        digits = '0' + digits[4:]
    elif digits.startswith('98') and len(digits) >= 12:
        digits = '0' + digits[2:]
    return digits


def _first_path_part(url: str | None) -> tuple[str, str] | tuple[None, None]:
    normalized = normalize_url(url)
    if not normalized:
        return None, None
    parts = urlsplit(normalized)
    host = parts.netloc.lower()
    path_parts = [p for p in parts.path.strip('/').split('/') if p]
    return host, (path_parts[0] if path_parts else '')


def normalize_instagram(url_or_username: str | None) -> str:
    if not url_or_username:
        return ''
    value = url_or_username.strip().strip('@').strip('/').lower()
    if 'instagram.com' in value:
        host, first = _first_path_part(value)
        value = first or ''
    value = value.strip('@').strip('/').lower()
    if value in {'p', 'reel', 'explore', 'accounts', 'stories'}:
        return ''
    return value if re.fullmatch(r'[a-z0-9_.]{2,60}', value or '') else ''


def normalize_telegram(url_or_username: str | None) -> str:
    if not url_or_username:
        return ''
    value = url_or_username.strip().strip('@').strip('/').lower()
    if 't.me' in value or 'telegram.me' in value:
        host, first = _first_path_part(value)
        value = first or ''
    value = value.strip('@').strip('/').lower()
    return value if re.fullmatch(r'[a-z0-9_]{3,80}', value or '') else ''


def normalize_domain(url: str | None) -> str:
    normalized = normalize_url(url)
    if not normalized:
        return ''
    host = urlsplit(normalized).netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    if not host or host in MARKETPLACE_DOMAINS:
        return ''
    return host


def normalize_text_key(text: str | None) -> str:
    text = (text or '').lower().replace('ي', 'ی').replace('ك', 'ک').translate(PERSIAN_DIGITS)
    text = re.sub(r'[^0-9a-zA-Zآ-ی]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


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
        username = normalize_instagram(ig_match.group(1))
        if username:
            instagram = f'https://instagram.com/{username}'

    tg_match = re.search(r'https?://(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})/?', text, re.I)
    if tg_match:
        username = normalize_telegram(tg_match.group(1))
        if username:
            telegram = f'https://t.me/{username}'

    return {'instagram': instagram, 'telegram': telegram}
