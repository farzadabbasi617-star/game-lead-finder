from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_name: str = 'Game Lead Finder'
    database_url: str = 'sqlite:///./leads.db'
    admin_token: str = 'change-this-token'

    google_places_api_key: str | None = None
    neshan_api_key: str | None = None
    serpapi_key: str | None = None
    default_city: str = 'تهران'


@lru_cache
def get_settings() -> Settings:
    return Settings()
