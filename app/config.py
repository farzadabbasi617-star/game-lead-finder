from functools import lru_cache
from html import unescape
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_name: str = 'Game Lead Finder'
    database_url: str = 'sqlite:///./leads.db'
    admin_token: str = 'change-this-token'

    google_places_api_key: str | None = None
    neshan_api_key: str | None = None
    serpapi_key: str | None = None
    google_cse_api_key: str | None = None
    google_cse_id: str | None = None
    brave_search_api_key: str | None = None
    serper_api_key: str | None = None
    searchapi_key: str | None = None
    tavily_api_key: str | None = None

    # Optional AI providers. Multiple models can be comma-separated.
    ai_provider_order: str | None = 'groq,openrouter,huggingface'
    groq_api_key: str | None = None
    groq_models: str | None = None
    openrouter_api_key: str | None = None
    openrouter_models: str | None = None
    huggingface_api_key: str | None = None
    huggingface_models: str | None = None

    default_city: str = 'تهران'


def normalize_database_url(url: str | None) -> str:
    """Accept common Render/Neon pasted URLs.

    - Converts HTML escaped ampersands: &amp; -> &
    - Converts Neon's postgresql:// URL to SQLAlchemy psycopg driver URL
    - Trims accidental whitespace/quotes
    """
    if not url:
        return 'sqlite:///./leads.db'
    url = unescape(url.strip().strip('\'"'))
    if url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.database_url = normalize_database_url(settings.database_url)
    return settings
