"""Growth module — database models."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


# ── Landing Pages ──────────────────────────────────────────────────

class LandingPage(Base):
    """A public landing page for collecting signups."""
    __tablename__ = 'landing_pages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hero_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta_text: Mapped[str] = mapped_column(String(200), default='ثبت‌نام رایگان', nullable=False)
    redirect_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # after signup
    platform_target: Mapped[str | None] = mapped_column(String(80), nullable=True)  # app / instagram / telegram / website
    telegram_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    instagram_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    website_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    collect_phone: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    collect_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    collect_name: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    collect_username: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    theme_color: Mapped[str] = mapped_column(String(20), default='#2563eb', nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class LandingSignup(Base):
    """A signup from a landing page."""
    __tablename__ = 'landing_signups'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landing_page_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    referral_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)  # utm_source
    medium: Mapped[str | None] = mapped_column(String(100), nullable=True)  # utm_medium
    campaign: Mapped[str | None] = mapped_column(String(100), nullable=True)  # utm_campaign
    ip_address: Mapped[str | None] = mapped_column(String(50), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


# ── Referral System ────────────────────────────────────────────────

class ReferralLink(Base):
    """A unique referral link for tracking who brings whom."""
    __tablename__ = 'referral_links'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    owner_name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    owner_telegram: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner_instagram: Mapped[str | None] = mapped_column(String(200), nullable=True)
    landing_page_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    click_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    signup_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ReferralClick(Base):
    """Tracks each click on a referral link."""
    __tablename__ = 'referral_clicks'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referral_link_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(50), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


# ── Welcome Messages ───────────────────────────────────────────────

class WelcomeMessage(Base):
    """Templates for welcome messages sent to new followers/members."""
    __tablename__ = 'welcome_messages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # telegram / instagram / app / general
    body: Mapped[str] = mapped_column(Text, nullable=False)
    include_referral_link: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    include_social_links: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


# ── Content Calendar ───────────────────────────────────────────────

class ContentPost(Base):
    """A scheduled content post for social media."""
    __tablename__ = 'content_posts'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # instagram / telegram / both
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)  # post / story / reel / channel_post
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    hashtags: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='draft', nullable=False, index=True)
    # draft / scheduled / posted / cancelled
    engagement_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
