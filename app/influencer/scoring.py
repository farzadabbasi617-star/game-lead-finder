"""Influencer Finder — scoring logic."""
from __future__ import annotations

import re
from app.influencer.models import Influencer


GAMING_KEYWORDS = {
    # High — core gaming
    'گیم': 12, 'گیمینگ': 15, 'گیمر': 14, 'بازی': 10, 'گیمپلی': 14,
    'پلی استیشن': 12, 'پلی‌استیشن': 12, 'ps5': 12, 'ps4': 10, 'xbox': 10,
    'پی‌سی': 10, 'pc gaming': 12, 'نینتندو': 10, 'سوییچ': 8,
    'کنسول': 10, 'استیم': 10, 'steam': 10, 'اپیک': 8, 'epic': 6,
    # High — popular games
    'کالاف': 15, 'call of duty': 14, 'cod': 12, 'وارزون': 12,
    'پابجی': 15, 'pubg': 14,
    'ولورانت': 13, 'valorant': 12,
    'فورتنایت': 12, 'fortnite': 10,
    'لیگ': 10, 'league': 8, 'lol': 8,
    'کلش': 12, 'clash': 10,
    'فری فایر': 12, 'free fire': 10,
    'جی‌تی‌ای': 10, 'gta': 8,
    'ماینکرفت': 10, 'minecraft': 8,
    'ایپکس': 10, 'apex': 8,
    'فتافایت': 8, 'فیفا': 10, 'efootball': 8,
    # Medium — gaming ecosystem
    'اکانت': 8, 'سی پی': 10, 'cp': 8, 'یوسی': 10, 'uc': 8,
    'جم': 8, 'الماس': 7, 'اسکین': 8, 'گیفت کارت': 10,
    'استریم': 12, 'stream': 10, 'استریمر': 14, 'streamer': 12,
    'آنباکسینگ': 10, 'unboxing': 8, 'ریویو': 10, 'review': 8,
    'تکنولوژی': 6, 'تِک': 6, 'tech': 5,
    # Content type
    'گیمینگ چنل': 15, 'gaming channel': 14, 'گیم پلی': 14, 'gameplay': 12,
}

NEGATIVE_KEYWORDS = {
    'هک': -15, 'چیت': -15, 'تقلب': -10, 'کرک': -10,
    'فیلترشکن': -10, 'vpn': -8,
    'خبرگزاری': -5, 'خبر': -3,
}

# Follower tier thresholds
TIERS = [
    ('nano', 0, 1_000),
    ('micro', 1_000, 10_000),
    ('mid', 10_000, 100_000),
    ('macro', 100_000, 1_000_000),
    ('mega', 1_000_000, 999_999_999),
]


def classify_tier(followers: int | None) -> str | None:
    if not followers:
        return None
    for name, lo, hi in TIERS:
        if lo <= followers < hi:
            return name
    return 'mega'


def compute_influencer_score(inf: Influencer) -> None:
    """Compute relevance, quality, and collab scores."""
    blob = f"{inf.display_name or ''} {inf.bio or ''} {inf.username or ''} {inf.niche or ''} {inf.game_tags or ''}".lower()
    blob = blob.replace('ي', 'ی').replace('ك', 'ک')

    # ── Relevance (0-100) ──
    relevance = 0
    for kw, weight in GAMING_KEYWORDS.items():
        if kw in blob:
            relevance += weight
    for kw, weight in NEGATIVE_KEYWORDS.items():
        if kw in blob:
            relevance += weight

    if inf.niche and any(x in (inf.niche or '').lower() for x in ['گیم', 'بازی', 'game', 'stream']):
        relevance += 20
    if inf.game_tags:
        relevance += 15
    relevance = max(0, min(relevance, 100))
    inf.relevance_score = relevance

    # ── Quality (0-100) ──
    quality = 20  # base

    # Follower count (logarithmic feel)
    f = inf.followers or 0
    if f >= 1_000_000:
        quality += 30
    elif f >= 500_000:
        quality += 27
    elif f >= 100_000:
        quality += 24
    elif f >= 50_000:
        quality += 20
    elif f >= 10_000:
        quality += 15
    elif f >= 1_000:
        quality += 10
    elif f >= 100:
        quality += 5

    # Engagement rate
    er = inf.engagement_rate or 0
    if er >= 10:
        quality += 25  # very high — suspicious if too high, but great if real
    elif er >= 5:
        quality += 22
    elif er >= 3:
        quality += 18
    elif er >= 1:
        quality += 12
    elif er >= 0.5:
        quality += 6

    # Avg views
    if inf.avg_views and inf.avg_views >= 10_000:
        quality += 15
    elif inf.avg_views and inf.avg_views >= 1_000:
        quality += 10
    elif inf.avg_views and inf.avg_views >= 100:
        quality += 5

    # Bio quality
    if inf.bio and len(inf.bio) > 30:
        quality += 5

    quality = max(0, min(quality, 100))
    inf.quality_score = quality

    # ── Tier ──
    inf.tier = classify_tier(inf.followers)

    # ── Collab Score (combined) ──
    # 55% relevance + 35% quality + 10% engagement bonus
    er_bonus = min(er * 2, 10) if er else 0
    inf.collab_score = max(0, min(int(relevance * 0.55 + quality * 0.35 + er_bonus), 100))
