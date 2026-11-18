"""Pydantic models shared by every step."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

MomentType = Literal[
    "product_launch",
    "packaging",
    "tv_ad",
    "digital_campaign",
    "sponsorship",
    "live_event",
    "retail_expansion",
    "loyalty_or_contest",
    "other",
]
TimingStatus = Literal["upcoming", "ongoing", "past"]


class SearchResult(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None
    query: str
    provider: str


# ---------- Step 1: resolve ----------


class Candidate(BaseModel):
    name: str
    domain: str | None = None
    note: str | None = None


class ResolvedCompany(BaseModel):
    """What the AI returns when resolving a company."""

    is_company: bool = True
    official_name: str
    official_domain: str
    industry: str
    brands: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    candidates: list[Candidate] = Field(default_factory=list)


class Company(BaseModel):
    id: int | None = None
    name: str
    domain: str
    industry: str = "unknown"
    brands: list[str] = Field(default_factory=list)
    is_existing_customer: bool = False
    customer_tier: str | None = None  # "case_study", "likely", or None


# ---------- Step 2: evidence ----------


class Evidence(BaseModel):
    id: int | None = None
    company_id: int
    run_id: int
    url: str
    final_url: str
    url_hash: str
    text_hash: str
    title: str | None = None
    publisher: str | None = None
    published_date: date | None = None
    date_known: bool = False
    fetched_at: str
    text: str


# ---------- Step 3: moments ----------


class EvidenceLink(BaseModel):
    evidence_id: int
    exact_quote: str


class Timing(BaseModel):
    date_text: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: TimingStatus


class Moment(BaseModel):
    title: str
    type: MomentType
    summary: str
    timing: Timing
    physical_touchpoints: list[str] = Field(default_factory=list)
    evidence: list[EvidenceLink] = Field(default_factory=list)


class MomentList(BaseModel):
    moments: list[Moment] = Field(default_factory=list)


class MergeGroups(BaseModel):
    """Groups of moment indexes that describe the same thing."""

    groups: list[list[int]] = Field(default_factory=list)


# ---------- Step 4: match ----------


class Match(BaseModel):
    moment_index: int
    play_id: str | None = None
    why_flowcode_fits: str = ""
    flowcode_products: list[str] = Field(default_factory=list)
    suggested_first_pitch: str = ""


class MatchList(BaseModel):
    matches: list[Match] = Field(default_factory=list)


class SimilarStory(BaseModel):
    name: str
    url: str
    result: str


# ---------- Step 5: verify ----------


class VerifyAnswer(BaseModel):
    verdict: Literal["yes", "partly", "no"]
    supported_claim: str = ""
    reason: str = ""


class Verification(BaseModel):
    quotes_checked: int = 0
    quotes_passed: int = 0
    verdict: Literal["yes", "partly", "no"] | None = None
    reason: str = ""
    original_summary: str | None = None


# ---------- Step 6: score ----------


class FactorRating(BaseModel):
    rating: int = Field(ge=1, le=5)
    reason: str


class OpportunityRatings(BaseModel):
    index: int
    timing: FactorRating
    playbook_fit: FactorRating
    reach: FactorRating
    evidence_strength: FactorRating
    openness: FactorRating


class ScoreAnswer(BaseModel):
    opportunities: list[OpportunityRatings] = Field(default_factory=list)
    company_size: FactorRating
    snapshot: str = ""


class Opportunity(BaseModel):
    id: int | None = None
    moment_id: int | None = None
    moment: Moment
    play_id: str | None = None
    why_flowcode_fits: str = ""
    flowcode_products: list[str] = Field(default_factory=list)
    similar_story: SimilarStory | None = None
    suggested_first_pitch: str = ""
    verification: Verification | None = None
    verified: bool = False
    factor_ratings: dict[str, FactorRating] = Field(default_factory=dict)
    score: float | None = None


class CompanyScore(BaseModel):
    score: float
    factors: dict[str, FactorRating]
    snapshot: str = ""
