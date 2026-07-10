from __future__ import annotations

CATEGORY_RULES: dict[str, list[str]] = {
    'اکانت': ['اکانت', 'account', 'کلش', 'ولورانت', 'valorant', 'steam account', 'استیم اکانت'],
    'سی‌پی کالاف': ['سی پی', 'cp', 'کالاف', 'call of duty', 'cod', 'وارزون'],
    'یوسی پابجی': ['یوسی', 'uc', 'پابجی', 'pubg'],
    'جم/الماس': ['جم', 'الماس', 'free fire', 'فری فایر', 'gem', 'diamond'],
    'گیفت کارت': ['گیفت کارت', 'gift card', 'psn', 'playstation gift', 'استیم والت', 'steam wallet', 'ایکس باکس', 'xbox'],
    'فروشگاه گیم': ['فروشگاه بازی', 'فروشگاه کنسول', 'کنسول بازی', 'پلی استیشن', 'playstation', 'xbox', 'نینتندو', 'گیمینگ'],
    'گیم‌نت': ['گیم نت', 'گیم‌نت', 'game net', 'گیم سنتر', 'باشگاه بازی'],
    'آیتم/اسکین': ['اسکین', 'آیتم', 'skin', 'item'],
}

POSITIVE_TERMS = [
    'فروش', 'خرید', 'شارژ', 'ارزان', 'فوری', 'تحویل', 'معتبر', 'فروشگاه', 'خدمات',
    'اکانت', 'جم', 'سی پی', 'cp', 'یوسی', 'uc', 'گیفت کارت', 'پلی استیشن', 'استیم',
    'کالاف', 'پابجی', 'فری فایر', 'کلش', 'ولورانت', 'گیم', 'کنسول', 'گیمینگ',
]

NEGATIVE_TERMS = [
    'استخدام', 'دانلود', 'خبر', 'آموزش رایگان', 'رایگان', 'هک', 'چیت', 'تقلب',
]


def normalize_text(value: str | None) -> str:
    if not value:
        return ''
    return value.lower().replace('ي', 'ی').replace('ك', 'ک').strip()


def detect_category(*texts: str | None) -> str | None:
    body = normalize_text(' '.join([t or '' for t in texts]))
    best_category = None
    best_hits = 0
    for category, terms in CATEGORY_RULES.items():
        hits = sum(1 for term in terms if normalize_text(term) in body)
        if hits > best_hits:
            best_hits = hits
            best_category = category
    return best_category


def score_lead(
    title: str | None = None,
    description: str | None = None,
    url: str | None = None,
    phone: str | None = None,
    website: str | None = None,
    instagram: str | None = None,
    telegram: str | None = None,
    rating: float | None = None,
    review_count: int | None = None,
) -> int:
    body = normalize_text(' '.join([title or '', description or '', url or '']))
    score = 0
    for term in POSITIVE_TERMS:
        if normalize_text(term) in body:
            score += 8
    for term in NEGATIVE_TERMS:
        if normalize_text(term) in body:
            score -= 15
    if phone:
        score += 20
    if website:
        score += 18
    if instagram:
        score += 15
    if telegram:
        score += 15
    if rating and rating >= 4:
        score += 8
    if review_count and review_count >= 10:
        score += 8
    if any(domain in body for domain in ['instagram.com', 't.me', 'telegram.me']):
        score += 12
    if any(domain in body for domain in ['balad.ir', 'neshan.org', 'google.com/maps', 'maps.app.goo.gl']):
        score += 5
    return max(0, min(score, 100))
