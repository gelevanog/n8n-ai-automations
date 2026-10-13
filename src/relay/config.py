"""Runtime settings from environment variables (and an optional .env file). See .env.example."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProviderKind = Literal["fake", "openrouter", "qwen", "openai", "compat"]

# Free OpenRouter models chosen from the smoke test before the real run (results/smoke.json).
DEFAULT_FREE_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_FREE_FALLBACKS = ["nvidia/nemotron-3-ultra-550b-a55b:free"]
DEFAULT_QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODELS: dict[str, str] = {
    "fake": "fake-rules",
    "openrouter": DEFAULT_FREE_MODEL,
    "qwen": "qwen3.7-plus",
    "openai": "gpt-5-mini",
    "compat": "",  # any OpenAI-compatible endpoint: the model id must be given (RELAY_LLM_MODEL)
}


def _split(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RELAY_", extra="ignore")

    # ---- storage
    database_url: str = "sqlite:///.cache/relay.db"
    """SQLAlchemy URL of the ops store (runs, review queue, dead letters, idempotency keys, gateway calls).
    SQLite for a laptop, PostgreSQL in Docker (postgresql+psycopg://...)."""
    mocks_database_url: str = "sqlite:///.cache/mocks.db"
    """The mock CRM / helpdesk / accounting / chat systems keep their records here."""
    data_dir: Path = Path("data")
    """Datasets (emails, invoices, leads, FAQ questions), the knowledge base and the fixture company sites."""

    # ---- AI gateway: confidence gates and validation
    triage_min_confidence: float = 0.75
    """Inbox triage: below this the email goes to the human review queue instead of being routed."""
    spam_min_confidence: float = 0.9
    """Spam is only archived automatically when the model is at least this sure."""
    invoice_min_confidence: float = 0.8
    invoice_amount_tolerance: float = 0.02
    """Absolute tolerance (in the invoice currency) for line sums, VAT and totals."""
    invoice_auto_post_limit: float = 5000.0
    """Invoices above this total always need a human approval, however clean the extraction."""
    faq_min_retrieval_score: float = 0.25
    """FAQ auto-reply: the best knowledge-base article must score at least this (normalized BM25, 0-1)."""
    faq_min_confidence: float = 0.75
    faq_top_k: int = 4
    lead_hot_score: int = 70
    lead_warm_score: int = 40

    # ---- fixture company sites (lead enrichment fetches only these)
    sites_base_url: str = ""
    """If set (e.g. http://mocks:8200/sites), the gateway fetches company sites over HTTP from this server;
    otherwise straight from data/sites on disk. Any other host is refused (no requests to the real internet)."""

    # ---- LLM
    llm_provider: LLMProviderKind = "fake"
    llm_model: str = ""
    """Empty = the provider's default (DEFAULT_MODELS)."""
    llm_fallback_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    llm_timeout_seconds: float = 180.0
    llm_max_tokens: int = 8000
    """A ceiling, not a target: free reasoning models spend tokens thinking before the JSON."""
    llm_temperature: float = 0.0
    llm_reasoning_effort: str = "low"
    require_free_models: bool = True
    """Refuse any OpenRouter model id that does not end in ":free" (requests, fallbacks and the model that answered)."""
    allow_paid_models: bool = False
    """The explicit opt-in for paid models: RELAY_ALLOW_PAID_MODELS=true lifts the free-only guard for the
    `openrouter` and `compat` providers. Off by default, so a fresh clone refuses every non-":free" id."""
    llm_base_url: str = ""
    """Endpoint of the `compat` provider (any OpenAI-compatible /chat/completions server)."""
    llm_api_key: str | None = None
    llm_send_temperature: bool = True
    llm_structured_retries: int = 1
    """When a reply fails schema validation, the model is asked again once with the validation errors."""

    openrouter_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENROUTER_API_KEY"))
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias=AliasChoices("OPENROUTER_BASE_URL")
    )
    qwen_api_key: str | None = Field(default=None, validation_alias=AliasChoices("QWEN_API_KEY", "DASHSCOPE_API_KEY"))
    qwen_base_url: str = DEFAULT_QWEN_BASE_URL
    qwen_enable_thinking: bool | None = None
    openai_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY"))
    openai_base_url: str = "https://api.openai.com/v1"

    # ---- budget for real API calls
    llm_cache: bool = True
    llm_cache_dir: Path = Path(".cache/llm")
    llm_ledger: Path | None = Path("results/calls.jsonl")
    llm_max_calls: int = 900
    llm_min_seconds_between_requests: float = 3.0
    llm_max_retries: int = 3
    prices_file: Path = Path("configs/prices.yaml")
    """USD per million tokens for paid models (the free ones cost $0); used for the cost-per-item metric."""

    # ---- n8n and the services the workflows talk to
    n8n_url: str = "http://localhost:5678"
    """Where Relay reaches n8n (webhooks, waiting executions). In Docker: http://n8n:5678."""
    n8n_webhook_token: str = "relay-local-webhook-token"
    """Shared secret the workflows' webhooks require in the X-Relay-Token header (n8n Header Auth credential)."""
    mocks_url: str = "http://localhost:8200"
    mailpit_url: str = "http://localhost:8025"

    # ---- API
    api_token: str = ""
    """If set, every /ops and /ai request needs `Authorization: Bearer <token>` or `X-Relay-Token`."""
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    results_dir: Path = Path("results")

    @field_validator("llm_fallback_models", "cors_origins", mode="before")
    @classmethod
    def _split_lists(cls, value: object) -> object:
        return _split(value)

    @field_validator("llm_ledger", "qwen_enable_thinking", "llm_api_key", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def free_only(self) -> bool:
        """The free-only guard is on unless paid models were allowed deliberately (either flag)."""
        return self.require_free_models and not self.allow_paid_models


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
