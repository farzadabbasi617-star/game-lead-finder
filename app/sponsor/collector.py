"""Collector: find gaming Telegram channels suitable for sponsored ads.

Uses available web search APIs (OpenRouter, Tavily, etc.) to discover
public Telegram channels, then scrapes the public preview page
(t.me/s/<username>) to estimate member counts and engagement.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import CrawlerRun
from app.repository import start_run, finish_run
from app.sponsor.models import SponsorChannel
from app.sponsor.scoring import compute_ad_score

# ── Gaming search queries ──────────────────────────────────────────

GAMING_QUERIES = [
    'کانال تلگرام فروش سی پی کالاف',
    'کانال تلگرام خرید یوسی پابجی',
    'کانال تلگرام فروش اکانت کلش',
    'کانال تلگرام گیفت کارت پلی استیشن',
    'کانال تلگرام فروش اکانت ولورانت',
    'کانال تلگرام جم فری فایر',
    'کانال تلگرام لوازم گیمینگ',
    'کانال تلگرام فروشگاه کنسول',
    'telegram channel pubg uc sell iran',
    'telegram channel call of duty cp iran',
    'telegram gaming store iran',
    'کانال تلگرام گیم نت',
    'کانال تلگرام استیم والت',
    'site:t.me فروش اکانت گیم',
    'site:t.me خرید سی پی کالاف',
    'site:t.me فروش یوسی',
    'site:t.me گیفت کارت',
]


# ── Telegram public preview scraping ───────────────────────────────

def _extract_member_count(html_text: str) -> int | None:
    """Extract subscriber count from t.me/s/ preview page."""
    # Pattern: "X members", "X subscribers", "X عضو"
    patterns = [
        r'([\d,.\s]+)\s*(?:member|subscriber)s?',
        r'([\d,.\s]+)\s*عضو',
        r'tgme_page_extra[^>]*>([^<]*\d[^<]*)</',
        r'counter[^>]*>([^<]*\d[^<]*)</',
    ]
    for pat in patterns:
        m = re.search(pat, html_text, re.I)
        if m:
            num_str = re.sub(r'[^\d]', '', m.group(1))
            if num_str and int(num_str) > 5:
                return int(num_str)
    return None


def _extract_post_count(html_text: str) -> int | None:
    """Estimate post count from preview page."""
    # Count message blocks
    blocks = re.findall(r'class="tgme_widget_message_wrap', html_text)
    return len(blocks) if blocks else None


def _extract_view_counts(html_text: str) -> list[int]:
    """Extract view counts from visible posts."""
    views = []
    for m in re.finditer(r'class="tgme_widget_message_views"[^>]*>([^<]+)<', html_text):
        raw = m.group(1).strip().replace(',', '').replace('.', '')
        # Handle K/M suffixes
        multiplier = 1
        if raw.upper().endswith('K'):
            multiplier = 1000
            raw = raw[:-1]
        elif raw.upper().endswith('M'):
            multiplier = 1_000_000
            raw = raw[:-1]
        try:
            val = int(float(raw.strip()) * multiplier)
            if val > 0:
                views.append(val)
        except (ValueError, TypeError):
            pass
    return views


async def scrape_tg_preview(username: str) -> dict:
    """Scrape t.me/s/<username> for public channel info."""
    url = f'https://t.me/s/{username}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8',
    }
    result = {'username': username, 'url': f'https://t.me/{username}', 'member_count': None, 'post_count': None, 'avg_views': None, 'engagement_rate': None, 'description': None}
    try:
        async with httpx.AsyncClient(timeout=18, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return result
            html = r.text[:2_000_000]

        # Title
        title_m = re.search(r'og:title["\s]+content="([^"]+)"', html)
        if title_m:
            result['title'] = title_m.group(1).strip()
        desc_m = re.search(r'og:description["\s]+content="([^"]+)"', html)
        if desc_m:
            result['description'] = desc_m.group(1).strip()

        result['member_count'] = _extract_member_count(html)
        result['post_count'] = _extract_post_count(html)
        views = _extract_view_counts(html)
        if views:
            result['avg_views'] = round(sum(views) / len(views), 1)
            if result['member_count'] and result['member_count'] > 0:
                result['engagement_rate'] = round((result['avg_views'] / result['member_count']) * 100, 2)
    except Exception:
        pass
    return result


# ── Username extraction ────────────────────────────────────────────

def extract_tg_usernames(text: str) -> list[str]:
    """Extract Telegram usernames from text."""
    patterns = [
        r'(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,80})',
        r'@([A-Za-z0-9_]{3,80})',
    ]
    seen = set()
    results = []
    # Skip generic paths
    skip = {'joinchat', 'addstickers', 'addemoji', 'share', 'login', 's', 'c', 'iv'}
    for pat in patterns:
        for m in re.finditer(pat, text):
            username = m.group(1).lower().strip()
            if username not in seen and username not in skip and len(username) >= 3:
                seen.add(username)
                results.append(username)
    return results


# ── Main collector ─────────────────────────────────────────────────

async def discover_sponsor_channels(
    db: Session,
    *,
    queries: list[str] | None = None,
    max_results_per_query: int = 10,
    min_ad_score: int = 20,
) -> dict:
    """Run web searches to discover gaming Telegram channels for sponsorship."""
    from app.collectors.openrouter_web_search import run_openrouter_web_search
    from app.ai import chat_json

    settings = get_settings()
    queries = queries or GAMING_QUERIES[:6]  # default: first 6 queries

    summary = {'queries_run': 0, 'channels_found': 0, 'new_saved': 0, 'duplicates': 0, 'errors': []}
    all_usernames: set[str] = set()

    for query in queries:
        run = start_run(db, 'sponsor_discovery', query)
        summary['queries_run'] += 1
        try:
            # Use OpenRouter web search to find channels
            if settings.openrouter_api_key:
                result = await run_openrouter_web_search(
                    db,
                    topic=query,
                    city=None,
                    max_results=max_results_per_query,
                    min_score=30,
                )
                # Extract usernames from search results
                for item in (result.get('items') or []):
                    text = f"{item.get('title', '')} {item.get('url', '')} {item.get('description', '')}"
                    for username in extract_tg_usernames(text):
                        all_usernames.add(username)

            finish_run(db, run, len(all_usernames), 0)
        except Exception as exc:
            summary['errors'].append({'query': query, 'error': str(exc)[:200]})
            finish_run(db, run, 0, 0, str(exc)[:200])

    # Also extract from existing leads
    from app.db.models import Lead
    for lead in db.scalars(select(Lead).where(Lead.telegram.isnot(None))).all():
        for username in extract_tg_usernames(lead.telegram or ''):
            all_usernames.add(username)
        for username in extract_tg_usernames(lead.url or ''):
            all_usernames.add(username)

    summary['channels_found'] = len(all_usernames)

    # Scrape each channel's public preview
    for username in all_usernames:
        canonical_url = f'https://t.me/{username}'
        # Skip if already exists
        existing = db.scalar(select(SponsorChannel).where(SponsorChannel.channel_url == canonical_url))
        if existing:
            summary['duplicates'] += 1
            # Update metrics if we have better data
            info = await scrape_tg_preview(username)
            if info.get('member_count') and (not existing.member_count or info['member_count'] > existing.member_count):
                existing.member_count = info['member_count']
            if info.get('avg_views') and (not existing.avg_views or info['avg_views'] > existing.avg_views):
                existing.avg_views = info['avg_views']
            if info.get('engagement_rate'):
                existing.engagement_rate = info['engagement_rate']
            existing.last_seen = datetime.utcnow()
            compute_ad_score(existing)
            db.add(existing)
            continue

        info = await scrape_tg_preview(username)
        title = info.get('title') or username
        desc = info.get('description')

        # Detect category
        blob = f"{title} {desc or ''} {username}".lower()
        category = _detect_sponsor_category(blob)

        ch = SponsorChannel(
            platform='telegram',
            channel_url=canonical_url,
            username=username,
            title=title,
            description=desc,
            member_count=info.get('member_count'),
            post_count=info.get('post_count'),
            avg_views=info.get('avg_views'),
            engagement_rate=info.get('engagement_rate'),
            category=category,
            language='fa' if _has_persian(blob) else 'en',
            source='search',
            status='new',
        )
        compute_ad_score(ch)
        db.add(ch)
        summary['new_saved'] += 1

    db.commit()
    return summary


def _detect_sponsor_category(text: str) -> str | None:
    """Detect channel category from text."""
    rules = {
        'سی‌پی کالاف': ['سی پی', 'cp', 'کالاف', 'call of duty', 'cod'],
        'یوسی پابجی': ['یوسی', 'uc', 'پابجی', 'pubg'],
        'اکانت': ['اکانت', 'account', 'کلش', 'ولورانت', 'valorant', 'استیم'],
        'گیفت کارت': ['گیفت کارت', 'gift card', 'psn', 'استیم والت', 'steam wallet'],
        'جم/الماس': ['جم', 'الماس', 'free fire', 'فری فایر'],
        'فروشگاه گیم': ['فروشگاه', 'کنسول', 'پلی استیشن', 'playstation', 'xbox'],
        'گیم‌نت': ['گیم نت', 'گیم‌نت', 'game net', 'گیم سنتر'],
        'لوازم گیمینگ': ['لوازم گیمینگ', 'هدست', 'ماوس', 'کیبورد', 'گیمینگ'],
    }
    best, best_hits = None, 0
    for cat, terms in rules.items():
        hits = sum(1 for t in terms if t in text)
        if hits > best_hits:
            best_hits = hits
            best = cat
    return best


def _has_persian(text: str) -> bool:
    return bool(re.search(r'[\u0600-\u06FF]', text))
