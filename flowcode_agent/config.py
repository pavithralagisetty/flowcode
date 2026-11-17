"""Load and validate config.yaml and .env."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

ALWAYS_BLOCKED = "flowcode.com"


class SearchConfig(BaseModel):
    provider: str = "gemini"
    gemini_model: str = "gemini-3.5-flash-lite"
    claude_model: str = "claude-sonnet-5-5"
    max_results_per_query: int = 10


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    main_model: str
    cheap_model: str
    gemini_main_model: str = "gemini-3.8-flash"
    gemini_cheap_model: str = "gemini-3.5-flash-lite"
    max_tokens: int = 16000

    def models(self) -> tuple[str, str]:
        """Return (main, cheap) model names for the selected provider."""
        if self.provider == "gemini":
            return self.gemini_main_model, self.gemini_cheap_model
        return self.main_model, self.cheap_model


class ResolveConfig(BaseModel):
    min_confidence: float = 0.6


class ResearchConfig(BaseModel):
    recency_days: int = 180
    max_pages_per_company: int = 60
    fetch_timeout_seconds: float = 15
    max_chars_per_page: int = 12000
    evidence_batch_size: int = 6
    blocked_domains: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _always_block_flowcode(self) -> "ResearchConfig":
        if ALWAYS_BLOCKED not in self.blocked_domains:
            self.blocked_domains.append(ALWAYS_BLOCKED)
        return self


class VerifyConfig(BaseModel):
    quote_match_threshold: float = 0.9


class ScoringConfig(BaseModel):
    strong_threshold: float = 3.5
    strong_industries: list[str] = Field(default_factory=list)
    opportunity_weights: dict[str, float]
    company_weights: dict[str, float]

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "ScoringConfig":
        for label, weights in (
            ("opportunity_weights", self.opportunity_weights),
            ("company_weights", self.company_weights),
        ):
            total = sum(weights.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"scoring.{label} must add up to 1, got {total:.4f}")
        return self


class BatchConfig(BaseModel):
    concurrency: int = 4


class PathsConfig(BaseModel):
    db: str = "flowcode.db"
    outputs: str = "outputs"
    prompts: str = "prompts"
    playbook: str = "playbook.yaml"
    existing_customers: str = "existing_customers.yaml"
    handoff: str = "handoff"


class StorageConfig(BaseModel):
    """Where research data is kept: a local SQLite file or a PostgreSQL server."""

    backend: str = "sqlite"  # sqlite or postgres
    url_env: str = "DATABASE_URL"  # environment variable holding the postgres:// connection string

    @field_validator("backend")
    @classmethod
    def _known_backend(cls, v: str) -> str:
        if v not in ("sqlite", "postgres"):
            raise ValueError("storage.backend must be sqlite or postgres")
        return v


class ModelPrice(BaseModel):
    """US dollars per million tokens. Only used to estimate cost in the Usage page."""

    input_per_mtok: float = 0
    output_per_mtok: float = 0


class PricingConfig(BaseModel):
    models: dict[str, ModelPrice] = {}
    web_search_per_1k: float = 0


class Config(BaseModel):
    search: SearchConfig
    llm: LLMConfig
    resolve: ResolveConfig = ResolveConfig()
    research: ResearchConfig = ResearchConfig()
    verify: VerifyConfig = VerifyConfig()
    scoring: ScoringConfig
    batch: BatchConfig = BatchConfig()
    paths: PathsConfig = PathsConfig()
    pricing: PricingConfig = PricingConfig()
    storage: StorageConfig = StorageConfig()
    base_dir: Path = Path(".")

    def path(self, name: str) -> Path:
        """Resolve a path from the `paths` section against the config's folder."""
        return self.base_dir / getattr(self.paths, name)

    def db_target(self) -> str:
        """What to open the Database with: the SQLite file path, or the Postgres URL from the environment."""
        if self.storage.backend == "postgres":
            url = require_env(self.storage.url_env)
            if url.startswith("postgres://"):  # some hosts hand out the short scheme
                url = "postgresql://" + url[len("postgres://"):]
            return url
        return str(self.path("db"))


# Named provider pairs (search, llm). FLOWCODE_MODE in the environment picks one and overrides config.yaml,
# which is how a deployed server is pinned to API mode.
PROVIDER_MODES = {"api": ("claude", "anthropic"), "claude_code": ("claude_code", "claude_code")}
MODE_ENV = "FLOWCODE_MODE"


def forced_mode() -> str | None:
    """The mode set by FLOWCODE_MODE, if any."""
    mode = os.environ.get(MODE_ENV, "").strip()
    if mode and mode not in PROVIDER_MODES:
        raise RuntimeError(f"{MODE_ENV} must be one of {', '.join(PROVIDER_MODES)}, not {mode!r}")
    return mode or None


def load_config(path: str | Path = "config.yaml") -> Config:
    """Read config.yaml (and .env next to it) and validate it."""
    path = Path(path)
    load_dotenv(path.parent / ".env")
    data = yaml.safe_load(path.read_text()) or {}
    mode = forced_mode()
    if mode:
        data.setdefault("search", {})["provider"], data.setdefault("llm", {})["provider"] = PROVIDER_MODES[mode]
    return Config(**data, base_dir=path.parent)


def require_env(name: str) -> str:
    """Return an environment variable or fail with a clear message."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to .env (see .env.example).")
    return value
