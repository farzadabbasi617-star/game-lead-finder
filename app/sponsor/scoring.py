"""Scoring logic for sponsor channel ad placement suitability."""
from __future__ import annotations

import re
from app.sponsor.models import SponsorChannel


# Gaming-relevant keywords (higher weight = more relevant)
GAMING_KEYWORDS = {
    # High relevance
    'فروش': 10, 'خرید': 10, 'سی پی': 15, 'cp': 12, 'کالاف': 15, 'یوسی': 15, 'uc': 12,
    'پابجی': 15, 'pubg': 12, 'اکانت': 12, 'کلش': 12, 'ولورانت': 12, 'valorant': 10,
    'گیفت کارت': 15, 'gift card': 12, 'استیم': 12, 'steam': 10, 'جم': 12, 'الماس': 10,
    'فری فایر': 12, 'free fire': 10, 'پلی استیشن': 12, 'playstation': 10, 'psn': 10,
    'ایکس باکس': 10, 'xbox': 8, 'کنسول': 10, 'گیم': 8, 'گیمینگ': 8,
    # Medium relevance
    'فروشگاه': 6, 'آگهی': 5, 'تخفیف': 6, 'ارزان': 6, 'فوری': 4, 'تحویل': 4,
    'فروش سی پی': 18, 'فروش یوسی': 18, 'خرید اکانت': 18, 'گیم نت': 10,
    'game net': 8, 'فروشگاه کنسول': 12, 'لوازم گیمینگ': 10, 'هدست': 6, 'ماوس': 5,
}

# Negative keywords (reduce score)
NEGATIVE_KEYWORDS = {
    'هک': -20, 'چیت': -20, 'تقلب': -15, 'کرک': -15, 'دانلود بازی': -10,
    'خبرگزاری': -8, 'اخبار': -5, 'آموزش رایگان': -5, 'رایگان': -8,
    'فیلترشکن': -10, 'vpn': -10, 'وی پی ان': -10,
}


def compute_ad_score(ch: SponsorChannel) -> None:
    """Compute relevance_score, quality_score, and ad_score for a channel."""
    blob = f"{ch.title or ''} {ch.description or ''} {ch.username or ''} {ch.category or ''}".lower()
    blob = blob.replace('ي', 'ی').replace('ك', 'ک')

    # ── Relevance (0-100) ──
    relevance = 0
    for kw, weight in GAMING_KEYWORDS.items():
        if kw in blob:
            relevance += weight
    for kw, weight in NEGATIVE_KEYWORDS.items():
        if kw in blob:
            relevance += weight  # weight is negative
    # Category bonus
    if ch.category:
        relevance += 15
    relevance = max(0, min(relevance, 100))
    ch.relevance_score = relevance

    # ── Quality (0-100) ──
    quality = 30  # base
    if ch.member_count:
        if ch.member_count >= 100_000:
            quality += 30
        elif ch.member_count >= 50_000:
            quality += 25
        elif ch.member_count >= 10_000:
            quality += 20
        elif ch.member_count >= 1_000:
            quality += 10
        elif ch.member_count >= 100:
            quality += 5

    if ch.engagement_rate:
        if ch.engagement_rate >= 30:
            quality += 25  # very high engagement
        elif ch.engagement_rate >= 15:
            quality += 20
        elif ch.engagement_rate >= 5:
            quality += 10
        elif ch.engagement_rate >= 1:
            quality += 5

    if ch.avg_views and ch.avg_views >= 1000:
        quality += 10
    elif ch.avg_views and ch.avg_views >= 100:
        quality += 5

    # Description quality
    if ch.description and len(ch.description) > 50:
        quality += 5

    quality = max(0, min(quality, 100))
    ch.quality_score = quality

    # ── Ad Score (combined) ──
    # Weighted: 60% relevance + 40% quality
    ch.ad_score = max(0, min(int(relevance * 0.6 + quality * 0.4), 100))
