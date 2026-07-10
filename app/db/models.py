from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class Keyword(Base):
    __tablename__ = 'keywords'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class City(Base):
    __tablename__ = 'cities'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Lead(Base):
    __tablename__ = 'leads'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, index=True)  # google_places, neshan, web, telegram, instagram, balad_web...
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)  # ad, business, website, channel, page
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    query: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    keyword: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(120), nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    instagram: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram: Mapped[str | None] = mapped_column(Text, nullable=True)

    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default='new', nullable=False, index=True)  # CRM workflow status
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    preferred_contact: Mapped[str | None] = mapped_column(String(80), nullable=True)
    link_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    link_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('url', name='uq_leads_url'),
        Index('ix_leads_source_status_score', 'source', 'status', 'score'),
    )


class CrawlerRun(Base):
    __tablename__ = 'crawler_runs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    query: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    found_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ActivityLog(Base):
    __tablename__ = 'activity_logs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, default='note')
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class MessageTemplate(Base):
    __tablename__ = 'message_templates'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SearchQueueItem(Base):
    __tablename__ = 'search_queue_items'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(String(80), default='openrouter_web', nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SearchRule(Base):
    __tablename__ = 'search_rules'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)  # blacklist / whitelist / source
    value: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ApiUsage(Base):
    __tablename__ = 'api_usage'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    day: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint('provider', 'day', name='uq_api_usage_provider_day'),
    )
