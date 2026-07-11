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


class AppSetting(Base):
    __tablename__ = 'app_settings'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SearchPreset(Base):
    __tablename__ = 'search_presets'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(String(80), default='openrouter_web', nullable=False)
    queries: Mapped[str] = mapped_column(Text, nullable=False)  # one query per line
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Person(Base):
    __tablename__ = 'people'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    nickname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    whatsapp: Mapped[str | None] = mapped_column(String(120), nullable=True)
    telegram: Mapped[str | None] = mapped_column(Text, nullable=True)
    instagram: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(60), default='new', nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PersonLeadLink(Base):
    __tablename__ = 'person_lead_links'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    lead_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    relationship: Mapped[str | None] = mapped_column(String(120), default='نامشخص', nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('person_id', 'lead_id', name='uq_person_lead'),
    )


class PersonActivityLog(Base):
    __tablename__ = 'person_activity_logs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, default='note')
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class Campaign(Base):
    __tablename__ = 'campaigns'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_type: Mapped[str] = mapped_column(String(40), default='lead', nullable=False)  # lead/person/both
    target_source: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    target_category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    target_status: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    target_city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    message_template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_template_b_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    site_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    daily_batch_size: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default='active', nullable=False, index=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class CampaignMember(Base):
    __tablename__ = 'campaign_members'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)  # lead/person
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    message_variant: Mapped[str] = mapped_column(String(5), default='A', nullable=False)
    status: Mapped[str] = mapped_column(String(40), default='queued', nullable=False, index=True)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    invite_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('campaign_id', 'entity_type', 'entity_id', name='uq_campaign_member_entity'),
    )
