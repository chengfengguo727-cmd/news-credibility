from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ClaimType(str, enum.Enum):
    analyst_target = "analyst_target"
    macro = "macro"
    stock_event = "stock_event"


class Outcome(str, enum.Enum):
    pending = "pending"
    hit = "hit"
    partial = "partial"
    miss = "miss"


class Sentiment(str, enum.Enum):
    bullish = "bullish"
    bearish = "bearish"
    neutral = "neutral"
    mixed = "mixed"


class EventType(str, enum.Enum):
    earnings = "earnings"
    m_and_a = "m_and_a"
    regulatory = "regulatory"
    exec_change = "exec_change"
    product_launch = "product_launch"
    macro_release = "macro_release"
    market_summary = "market_summary"
    opinion = "opinion"
    lawsuit = "lawsuit"
    guidance = "guidance"
    other = "other"


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    title: Mapped[str] = mapped_column(String(1024))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    extracted: Mapped[bool] = mapped_column(default=False, index=True)

    claims: Mapped[list[Claim]] = relationship(back_populates="article", cascade="all, delete-orphan")
    meta: Mapped[ArticleMeta | None] = relationship(
        back_populates="article", cascade="all, delete-orphan", uselist=False
    )
    tickers: Mapped[list[ArticleTicker]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    sectors: Mapped[list[ArticleSector]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    narrative_tags: Mapped[list[ArticleNarrativeTag]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    # The Postgres enum was created as `claim_type` (snake_case) in schema.sql.
    # Without `name=...`, SQLAlchemy would derive `claimtype` and fail at SELECT
    # with: type "claimtype" does not exist. `create_type=False` prevents
    # SQLAlchemy from trying to redundantly CREATE TYPE at startup.
    type: Mapped[ClaimType] = mapped_column(
        Enum(ClaimType, name="claim_type", create_type=False),
        index=True,
    )
    ticker: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    predicted_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_text: Mapped[str | None] = mapped_column(String(256), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    llm_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    article: Mapped[Article] = relationship(back_populates="claims")
    outcome: Mapped[ClaimOutcome | None] = relationship(
        back_populates="claim", cascade="all, delete-orphan", uselist=False
    )


class ClaimOutcome(Base):
    __tablename__ = "claim_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), unique=True, index=True
    )
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Postgres enum was created as `outcome_t` in schema.sql; same gotcha as claim_type above.
    outcome: Mapped[Outcome] = mapped_column(
        Enum(Outcome, name="outcome_t", create_type=False),
        default=Outcome.pending,
        index=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    claim: Mapped[Claim] = relationship(back_populates="outcome")


class ArticleMeta(Base):
    """Tier 1–3 metadata extracted by the LLM alongside claims."""
    __tablename__ = "article_meta"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    overall_sentiment: Mapped[Sentiment | None] = mapped_column(
        Enum(Sentiment, name="sentiment_t", create_type=False), nullable=True
    )
    event_type: Mapped[EventType | None] = mapped_column(
        Enum(EventType, name="event_type_t", create_type=False), nullable=True, index=True
    )
    article_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_breaking: Mapped[bool | None] = mapped_column(default=None, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    article: Mapped[Article] = relationship(back_populates="meta")


class ArticleTicker(Base):
    """One row per (article, ticker) pair — model can flag is_primary."""
    __tablename__ = "article_tickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    sentiment: Mapped[Sentiment | None] = mapped_column(
        Enum(Sentiment, name="sentiment_t", create_type=False), nullable=True
    )
    is_primary: Mapped[bool] = mapped_column(default=False)

    article: Mapped[Article] = relationship(back_populates="tickers")


class ArticleSector(Base):
    __tablename__ = "article_sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    sector: Mapped[str] = mapped_column(String(64), index=True)

    article: Mapped[Article] = relationship(back_populates="sectors")


class ArticleNarrativeTag(Base):
    __tablename__ = "article_narrative_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    tag: Mapped[str] = mapped_column(String(64), index=True)

    article: Mapped[Article] = relationship(back_populates="narrative_tags")


class SourceScore(Base):
    __tablename__ = "source_scores"
    __table_args__ = (UniqueConstraint("source", "type", name="uq_source_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[ClaimType] = mapped_column(
        Enum(ClaimType, name="claim_type", create_type=False),
        index=True,
    )
    alpha: Mapped[float] = mapped_column(Float, default=1.0)
    beta: Mapped[float] = mapped_column(Float, default=1.0)
    score: Mapped[float] = mapped_column(Float, default=0.5)
    ci_low: Mapped[float] = mapped_column(Float, default=0.0)
    ci_high: Mapped[float] = mapped_column(Float, default=1.0)
    n: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
