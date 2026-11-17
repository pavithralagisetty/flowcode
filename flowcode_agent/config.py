"""Load and validate config.yaml and .env."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

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


class Config(BaseModel):
    search: SearchConfig
    llm: LLMConfig
    resolve: ResolveConfig = ResolveConfig()
    research: ResearchConfig = ResearchConfig()
    verify: VerifyConfig = VerifyConfig()
    scoring: ScoringConfig
    batch: BatchConfig = BatchConfig()
    paths: PathsConfig = PathsConfig()
    base_dir: Path = Path(".")

    def path(self, name: str) -> Path:
        """Resolve a path from the `paths` section against the config's folder."""
        return self.base_dir / getattr(self.paths, name)


def load_config(path: str | Path = "config.yaml") -> Config:
    """Read config.yaml (and .env next to it) and validate it."""
    path = Path(path)
    load_dotenv(path.parent / ".env")
    data = yaml.safe_load(path.read_text()) or {}
    return Config(**data, base_dir=path.parent)


def require_env(name: str) -> str:
    """Return an environment variable or fail with a clear message."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to .env (see .env.example).")
    return value
