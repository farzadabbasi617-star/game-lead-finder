"""Influencer Finder — database models."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Influencer(Base):
    """A gaming influencer / content creator suitable for collaboration."""
    __tablename__ = 'influencers'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)       # instagram / telegram / youtube / tiktok
    profile_url: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metrics
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    following: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posts_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_likes: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_views: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_comments: Mapped[float | None] = mapped_column(Float, nullable=True)
    engagement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)           # (likes+comments)/followers * 100

    # Classification
    niche: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)     # "گیمینگ", "آنباکسینگ", "ریویو", "استریم"
    game_tags: Mapped[str | None] = mapped_column(Text, nullable=True)                     # comma-separated: "کالاف,پابجی,ولورانت"
    content_type: Mapped[str | None] = mapped_column(String(80), nullable=True)            # ویدیو, ریلز, استوری, پست, استریم
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(30), default='fa', nullable=True)

    # Scoring
    relevance_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)       # 0-100 gaming relevance
    quality_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)         # 0-100 audience quality
    collab_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)  # 0-100 collaboration fit

    # Tier classification
    tier: Mapped[str | None] = mapped_column(String(20), nullable=True)                    # nano/micro/mid/macro/mega

    # Status tracking
    status: Mapped[str] = mapped_column(String(40), default='discovered', nullable=False, index=True)
    # discovered → researching → contacted → negotiating →合作 → rejected
    contact_method: Mapped[str | None] = mapped_column(String(120), nullable=True)
    collab_type: Mapped[str | None] = mapped_column(String(120), nullable=True)            # "اسپانسری", "بارتلی", "ریویو", "کالاب"
    collab_price: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(String(80), default='search', nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('profile_url', name='uq_influencer_url'),
    )


class InfluencerTag(Base):
    """Tags for categorizing influencers."""
    __tablename__ = 'influencer_tags'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    influencer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint('influencer_id', 'tag', name='uq_influencer_tag'),
    )
