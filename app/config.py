"""SmartDoc configuration.

All settings can be overridden via environment variables prefixed with
SMARTDOC_, e.g. SMARTDOC_LLM_API_KEY / SMARTDOC_LLM_BASE_URL.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMARTDOC_", env_file=".env", extra="ignore")

    # LLM provider (any OpenAI-compatible endpoint)
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2

    # Storage
    db_path: str = "smartdoc.db"

    # Behaviour
    max_text_chars: int = 12_000
    cache_enabled: bool = True
    rule_fallback_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
