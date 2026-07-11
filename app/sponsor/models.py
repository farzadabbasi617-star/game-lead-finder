"""Sponsor Channel Finder — discovers gaming Telegram channels for paid sponsorships."""
from __future__ import annotations

import re
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SponsorChannel(Base):
    """A Telegram (or Instagram) channel/page suitable for sponsored ads."""
    __tablename__ = 'sponsor_channels'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)          # telegram / instagram
    channel_url: Mapped[str] = mapped_column(Text, nullable=False)                          # canonical URL
    username: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)    # @handle
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metrics (best-effort from public data)
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    post_count: Mapped[int | None] = mapped_column(Integer, nullable=True)                  # visible posts
    avg_views: Mapped[float | None] = mapped_column(Float, nullable=True)                   # avg views per post (estimate)
    engagement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)             # views/members * 100

    # Ad scoring
    relevance_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)        # 0-100 how gaming-relevant
    quality_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)          # 0-100 overall quality
    ad_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)   # combined score for ad placement

    # Tracking
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)    # e.g. "فروش CP", "گیفت کارت"
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(30), default='fa', nullable=True)   # fa / en / ar
    status: Mapped[str] = mapped_column(String(40), default='new', nullable=False, index=True)
    # new → researching → quoted → contacted → booked → rejected
    contact_method: Mapped[str | None] = mapped_column(String(120), nullable=True)          # DM, bot, email...
    ad_price: Mapped[str | None] = mapped_column(String(120), nullable=True)                # "500k Toman", "15 USD"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(String(80), default='search', nullable=False)       # search / ai / manual / import
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('channel_url', name='uq_sponsor_channel_url'),
    )
