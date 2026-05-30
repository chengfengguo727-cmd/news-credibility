"""Claim extractor — Week 2 (local OAuth via Claude Code CLI).

Instead of calling the Anthropic SDK with an API key, we shell out to the
`claude` CLI, which authenticates via Claude Code's OAuth login. This means:

  * No `ANTHROPIC_API_KEY` required — auth comes from the user's existing
    Claude Pro/Max subscription via `claude` login.
  * Must be run from a machine where `claude` is installed and logged in.
  * Not usable from GitHub Actions (no browser to OAuth from).

We pass the long system prompt via a temp file (avoids the 32KB Windows
cmdline cap), use `--json-schema` for structured output, and parse the
result from the `structured_output` field of the JSON envelope.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select

from db.models import (
    Article,
    ArticleMeta,
    ArticleNarrativeTag,
    ArticleSector,
    ArticleTicker,
    Claim,
    ClaimType,
    EventType,
    Sentiment,
)
from db.session import session_factory

log = logging.getLogger(__name__)

# Model alias accepted by `claude --model`. We use Haiku for cost/speed; the
# claude CLI maps `haiku` to whatever the latest is (currently 4.5).
MODEL = "haiku"
MAX_BODY_CHARS = 8000
PER_ARTICLE_BUDGET_USD = 0.10  # safety cap per `claude` invocation
SUBPROCESS_TIMEOUT_S = 120


# --- Enum values shared between schema + parsing ---------------------
_SENTIMENT_VALUES = [s.value for s in Sentiment]
_EVENT_TYPE_VALUES = [e.value for e in EventType]


# --- JSON schema (claude --json-schema enforces this) ----------------
OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "description": "Zero or more forward-looking market-verifiable claims. Empty for pure-report articles.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string", "enum": ["analyst_target", "macro", "stock_event"]},
                    "ticker": {"type": "string"},
                    "topic": {"type": "string"},
                    "predicted_value": {"type": "number"},
                    "predicted_text": {"type": "string"},
                    "deadline_iso": {"type": "string"},
                    "raw_text": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["type", "raw_text", "confidence"],
            },
        },
        "meta": {
            "type": "object",
            "description": "Whole-article metadata. Filled for every article including 0-claim ones.",
            "additionalProperties": False,
            "properties": {
                "overall_sentiment": {"type": "string", "enum": _SENTIMENT_VALUES},
                "event_type": {"type": "string", "enum": _EVENT_TYPE_VALUES},
                "article_quality": {
                    "type": "number",
                    "description": "0..1. 1=original reporting with quotes/data; 0=bot rephrase of another wire.",
                },
                "is_breaking": {"type": "boolean"},
                "tickers": {
                    "type": "array",
                    "description": "All tickers explicitly mentioned (max 20). Use canonical symbols (AAPL, 2330.TW).",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "ticker": {"type": "string"},
                            "sentiment": {"type": "string", "enum": _SENTIMENT_VALUES},
                            "is_primary": {"type": "boolean", "description": "True if the article is mainly about this company."},
                        },
                        "required": ["ticker", "sentiment", "is_primary"],
                    },
                },
                "sectors": {
                    "type": "array",
                    "description": "0..5 short sector labels — semiconductors, banks, energy, ev, biotech, cloud, retail, ai, crypto, real_estate, defense, consumer, healthcare, autos, telecom, media, materials, industrials, etc.",
                    "items": {"type": "string"},
                },
                "narrative_tags": {
                    "type": "array",
                    "description": "0..5 short narrative tags — e.g. 'ai_demand', 'rate_cuts', 'china_slowdown', 'soft_landing', 'earnings_recession'. snake_case.",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "overall_sentiment", "event_type", "article_quality",
                "is_breaking", "tickers", "sectors", "narrative_tags",
            ],
        },
    },
    "required": ["claims", "meta"],
}


# --- System prompt (long; cached by Claude Code automatically) ------
SYSTEM_PROMPT = """You are a financial-news claim extractor. Read one news article and identify every forward-looking, market-verifiable claim it contains. Return the result as a JSON object matching the schema you have been given. Do NOT use any tools — just produce the JSON object as your reply.

# Definition of a claim

A *claim* is a specific, forward-looking statement about future market behavior that satisfies all three of:

1. **Forward-looking** — about something that has not yet happened at the article's publish date.
2. **Verifiable** — we can later check whether it came true by looking at market data, official statistics, or company filings.
3. **Specific** — has a concrete target (a price, a rate, an event outcome) AND a reasonable deadline.

If any of those three are missing, do NOT record it. Pure reports of past events, vague sentiment ("bullish on tech"), opinions without targets ("we like Apple long-term"), or editorial commentary ("this looks expensive") are NOT claims.

# The three claim types

## Type 1: `analyst_target`

An analyst, broker, or research firm sets a price target or upgrades/downgrades a stock rating.

- `ticker` (required) — the stock symbol, e.g. AAPL, NVDA, 2330.TW
- `predicted_value` — the numeric target price
- `predicted_text` — the rating if mentioned (overweight, buy, hold, sell, etc.)
- `deadline_iso` — 12 months from the article date (analyst targets are conventionally 12-month)
- `raw_text` — the supporting quote
- `confidence` — usually 0.85+ for clearly stated targets; lower if you're inferring

**Worked examples**

Article excerpt: *"Goldman Sachs raised its Apple price target to $250 from $215, maintaining a Buy rating, citing stronger services growth."*
→ One claim:
```
type=analyst_target, ticker=AAPL, predicted_value=250, predicted_text="Buy",
deadline_iso=<article_date + 12 months>,
raw_text="raised its Apple price target to $250 from $215, maintaining a Buy rating",
confidence=0.95
```

Article excerpt: *"Morgan Stanley downgraded Tesla to Underweight from Equal-weight, slashing the target to $180."*
→ One claim:
```
type=analyst_target, ticker=TSLA, predicted_value=180, predicted_text="Underweight",
deadline_iso=<article_date + 12 months>,
raw_text="downgraded Tesla to Underweight from Equal-weight, slashing the target to $180",
confidence=0.95
```

Article excerpt: *"Two of the 32 analysts covering NVDA have it as a Sell, while 25 rate it Buy."*
→ NO claim (this is aggregate sentiment, no specific firm setting a target).

## Type 2: `macro`

A prediction about a macroeconomic variable.

- Omit `ticker`
- `topic` (required) — must be one of: `fed_funds`, `cpi`, `gdp`, `unemployment`
- `predicted_value` — the numeric prediction (rate in percent, growth percent, etc.)
- `predicted_text` — qualitative descriptor (e.g. "hold steady", "cut")
- `deadline_iso` — date of the data release or Fed meeting being predicted
- `raw_text` — supporting quote
- `confidence` — usually 0.7+ for explicit forecasts; lower for hedged language

**Worked examples**

Article excerpt: *"Economists surveyed by Bloomberg expect the Fed to cut rates by 25 basis points at its September 17 meeting, taking the funds rate to 5.00-5.25%."*
→ One claim:
```
type=macro, topic=fed_funds, predicted_value=5.125, predicted_text="cut 25bp",
deadline_iso=2024-09-17,
raw_text="expect the Fed to cut rates by 25 basis points at its September 17 meeting",
confidence=0.85
```

Article excerpt: *"Citi forecasts US Q3 2024 GDP at 2.5% annualized."*
→ One claim:
```
type=macro, topic=gdp, predicted_value=2.5,
deadline_iso=2024-10-30,  # approximate Q3 advance release date
raw_text="Citi forecasts US Q3 2024 GDP at 2.5% annualized", confidence=0.85
```

Article excerpt: *"Inflation has moderated significantly over the past year."*
→ NO claim (statement about the past, not a prediction).

## Type 3: `stock_event`

A specific company-level event prediction.

- `ticker` (required) — the affected company
- `topic` (required) — one of: `earnings_beat`, `earnings_miss`, `merger_completion`, `product_launch`
- `predicted_text` — qualitative description of the predicted outcome
- `predicted_value` — when there's a numeric estimate (e.g. expected EPS)
- `deadline_iso` — the event date (earnings report date, deal close target, launch date)
- `raw_text` — supporting quote
- `confidence` — typically 0.6-0.85; events have more noise than analyst targets

**Worked examples**

Article excerpt: *"Nvidia is widely expected to beat Wall Street estimates when it reports Q3 earnings on November 20."*
→ One claim:
```
type=stock_event, ticker=NVDA, topic=earnings_beat,
predicted_text="beat consensus", deadline_iso=2024-11-20,
raw_text="widely expected to beat Wall Street estimates when it reports Q3 earnings on November 20",
confidence=0.7
```

Article excerpt: *"Microsoft's $69 billion acquisition of Activision is expected to close by mid-2023, pending regulatory approval."*
→ One claim:
```
type=stock_event, ticker=ATVI, topic=merger_completion,
predicted_text="deal closes mid-2023", deadline_iso=2023-07-01,
raw_text="$69 billion acquisition of Activision is expected to close by mid-2023",
confidence=0.7
```

Article excerpt: *"Apple unveiled the iPhone 16 yesterday at its annual event."*
→ NO claim (past event).

Article excerpt: *"Analysts believe the new iPhone could drive a major upgrade cycle."*
→ NO claim — "could drive a major upgrade cycle" has no specific deadline or measurable target. If the article had said *"...could boost Q4 services revenue above $25B"*, that would be a stock_event with predicted_value=25 and deadline_iso=<Q4 report date>.

# Things that are NOT claims (common mistakes to avoid)

- **Past facts** — "Apple reported $90B revenue last quarter" → no claim.
- **Vague sentiment** — "Bullish on tech", "we're cautious", "looks expensive" → no claim.
- **Ratings without targets** — "Goldman maintains a Buy rating" alone, with no price target and no new rating change → skip. ("Goldman upgraded to Buy" IS a claim — the *change* is the prediction.)
- **Aspirations** — "Company aims to double revenue someday" → no deadline, no claim.
- **Already-resolved events** — "Fed cut rates by 25bp today" → past fact, not a prediction.
- **Conditional speculation** — "If the Fed cuts in September, the S&P could rally to 6000" → too conditional; skip unless the article asserts the antecedent.
- **Generic forward-looking boilerplate** — "Continued growth is expected in the coming quarters" → too vague.

# Date handling

- The article date is provided in the user message header. Use it as the basis for relative deadlines.
- If you cannot infer a specific deadline (e.g. "could rally over the long term"), DO NOT record the claim.
- For analyst_target, always use article_date + 12 months even if the article doesn't say "12-month target" — that's the convention.
- For macro Fed predictions, use the next relevant FOMC meeting date if the article mentions one, otherwise the next scheduled release of the relevant data series.

# Output rules

1. Produce exactly one JSON object matching the provided schema.
2. Return `claims: []` for articles with no testable predictions. Most articles will fall into this bucket — that is correct and expected.
3. `raw_text` must be a direct quote from the article body, ≤200 chars, no paraphrasing.
4. `confidence` is your own honest estimate. Be willing to go below 0.5 — those rows will be filtered out downstream.
5. Prefer fewer high-quality claims over many low-quality ones. When in doubt, leave it out.
6. The same claim mentioned twice in one article = ONE row, not two. Pick the clearer mention for `raw_text`.

# Edge cases

- **Multiple analysts in one article** → one claim per analyst-target pair.
- **Article reports another article's claim** ("Reuters reported that Citi forecasts...") → still extract the original prediction; the article-of-record for credibility is the *publishing* source, which is handled by the calling pipeline.
- **Earnings preview articles** that list "consensus expects X" → one stock_event claim attributed to the article's source (the source is endorsing the consensus by publishing it).
- **Articles with charts/numbers but no forward statements** → no claims.
- **Opinion / editorial pieces** ("Why I think Tesla will struggle") → claims only if they include a specific testable prediction with a deadline.

# Extended worked examples

The following are additional, deliberately tricky cases. Study them — they cover the patterns we see most often in financial wires (Reuters, Bloomberg, CNBC, WSJ, FT, MarketWatch, Yahoo Finance) and Mandarin sources (鉅亨, 經濟日報).

## More analyst_target examples

Article excerpt: *"Wedbush analyst Dan Ives reiterated his Outperform rating on Tesla and lifted his price target to $400 from $360, calling the AI story 'underappreciated by the Street'."*
→ One claim: type=analyst_target, ticker=TSLA, predicted_value=400, predicted_text="Outperform", deadline_iso=<article_date + 12 months>, confidence=0.95

Article excerpt: *"Jefferies cut its price target on Intel to $22 from $30 but maintained a Hold rating."*
→ One claim: type=analyst_target, ticker=INTC, predicted_value=22, predicted_text="Hold", deadline_iso=<article_date + 12 months>, confidence=0.95

Article excerpt: *"Of the 47 analysts tracked by Bloomberg, 32 rate AMD a Buy, 14 a Hold, and 1 a Sell. The average 12-month target is $185."*
→ One claim: type=analyst_target, ticker=AMD, predicted_value=185, predicted_text="consensus target", deadline_iso=<article_date + 12 months>, confidence=0.7
(Consensus targets ARE testable — slightly lower confidence because no single firm owns it.)

Article excerpt: *"Bernstein initiated coverage of Palantir with a Market-Perform rating and $25 target."*
→ One claim: type=analyst_target, ticker=PLTR, predicted_value=25, predicted_text="Market-Perform", deadline_iso=<article_date + 12 months>, confidence=0.95

Article excerpt: *"Several Wall Street firms have raised their Apple targets in recent weeks."*
→ NO claim (vague, no specific firm/number).

Article excerpt: *"國泰證券將台積電目標價調升至 1,100 元，重申買進評等。"*
→ One claim: type=analyst_target, ticker=2330.TW, predicted_value=1100, predicted_text="買進/Buy", deadline_iso=<article_date + 12 months>, confidence=0.9, raw_text="將台積電目標價調升至 1,100 元，重申買進評等"

## More macro examples

Article excerpt: *"The September CPI print is expected to come in at 3.1% year over year, down from 3.4% in August, according to a Reuters poll of economists."*
→ One claim: type=macro, topic=cpi, predicted_value=3.1, deadline_iso=2024-10-10 (typical September CPI release date), confidence=0.85

Article excerpt: *"The Fed's dot plot signaled two more 25bp cuts before year-end."*
→ Two claims, both topic=fed_funds, each predicted_value reflecting the 25bp step, deadline_iso the next two FOMC meetings. Or, when uncertain about specific meetings: ONE claim covering "two cuts by year-end" with the second meeting date as deadline. Prefer the latter when meeting dates aren't explicit. confidence=0.6 (Fed projections shift often).

Article excerpt: *"Goldman now sees just one Fed cut this year, down from a previous forecast of two."*
→ One claim: type=macro, topic=fed_funds, predicted_text="one cut this year", deadline_iso=<Dec FOMC meeting>, confidence=0.7

Article excerpt: *"Powell said inflation has come down significantly."*
→ NO claim (Fed chair's commentary on past data, not a forward prediction).

Article excerpt: *"Markets are pricing in a 73% probability of a rate cut at the November meeting."*
→ Borderline. Skip unless the *publishing source* endorses the call — market-implied probabilities don't have a single author. If the article presents the 73% as the source's own forecast, treat as macro with predicted_text="cut" and confidence=0.5.

Article excerpt: *"BofA expects Q4 GDP growth of 2.8%, well above consensus."*
→ One claim: type=macro, topic=gdp, predicted_value=2.8, deadline_iso=<Q4 GDP advance release ~ late January next year>, confidence=0.85

Article excerpt: *"Unemployment is projected to tick up to 4.3% in next month's payrolls report."*
→ One claim: type=macro, topic=unemployment, predicted_value=4.3, deadline_iso=<first Friday of next month>, confidence=0.8

## More stock_event examples

Article excerpt: *"Salesforce is expected to report adjusted EPS of $2.55 on revenue of $9.35 billion when it announces Q3 results on December 3."*
→ Two claims: one stock_event with topic=earnings_beat (predicted_text="meets consensus EPS $2.55", deadline_iso=2024-12-03), and arguably we'd also flag the revenue line — but to avoid double-counting one earnings event, RECORD ONLY ONE row per company per earnings date, choosing the most central metric (EPS). confidence=0.7.

Article excerpt: *"The pending merger between Capital One and Discover is expected to close in early 2025, pending Fed approval."*
→ One claim: type=stock_event, ticker=COF (the acquirer), topic=merger_completion, predicted_text="closes early 2025", deadline_iso=2025-03-31, confidence=0.65

Article excerpt: *"Apple is expected to unveil an updated Vision Pro in 2025."*
→ One claim: type=stock_event, ticker=AAPL, topic=product_launch, predicted_text="updated Vision Pro launch", deadline_iso=2025-12-31, confidence=0.55 (vague launch window).

Article excerpt: *"Boeing is unlikely to deliver any new 737 MAX 10 jets this year."*
→ NO claim by our schema — "unlikely to deliver" doesn't fit cleanly into earnings_beat/miss, merger_completion, or product_launch. Skip rather than force.

Article excerpt: *"鴻海第三季財報預估 EPS 將達 3.05 元，創同期新高。"*
→ One claim: type=stock_event, ticker=2317.TW, topic=earnings_beat, predicted_value=3.05, deadline_iso=<expected Q3 report date>, confidence=0.7, raw_text="鴻海第三季財報預估 EPS 將達 3.05 元"

## Tricky non-claims

These look like claims at first glance — they are NOT.

- *"Tesla shares closed 4% lower today on disappointing delivery numbers."* — past fact.
- *"AI is the most important technology trend of the decade."* — opinion, no specific target.
- *"Analysts have grown more cautious on the cloud sector."* — sentiment, no testable prediction.
- *"If the Fed pivots in Q3, equities could rally."* — pure conditional with no asserted antecedent.
- *"Long term, we like quality compounders."* — no deadline.
- *"The company aims for $10B in annual revenue."* — corporate aspiration with no deadline.
- *"The CEO said the worst is behind us."* — qualitative executive comment without measurable target.

## Duplicate handling

If the same claim is mentioned more than once in the article — e.g., a headline target price restated in the body — emit ONE row. Choose the longer/clearer mention for `raw_text`.

## Multi-leg trades

If an article describes an analyst making multiple distinct calls in the same note (e.g. "Goldman raised AAPL to $250 and cut INTC to $22"), emit ONE row per ticker-action pair.

## Confidence scoring — calibration guide

Anchor your `confidence` values against these reference cases:

- **0.95+** — Unambiguous, named firm, explicit numeric target, clear timeframe.
- **0.85-0.94** — Named source, explicit numeric prediction, slightly fuzzier timeframe.
- **0.70-0.84** — Specific prediction but with hedging language ("widely expected", "could", "appears poised to"), or consensus aggregates.
- **0.50-0.69** — Vague timing or qualitative-only prediction with an inferred deadline.
- **0.30-0.49** — You're stretching to fit the schema. Use sparingly.
- **<0.30** — Don't record. Leave the claim out entirely.

The downstream pipeline filters to confidence ≥ 0.5 by default, so anything below that range is essentially noise.

## Ticker normalization

When you see a ticker symbol, use the canonical form expected by market data sources:
- US stocks: bare uppercase symbol, e.g. `AAPL`, `NVDA`, `BRK.B` (note the dot before share class)
- Taiwan stocks: `<digits>.TW`, e.g. `2330.TW` for TSMC, `2317.TW` for Foxconn (Hon Hai)
- Hong Kong stocks: `<digits>.HK`, e.g. `0700.HK` for Tencent
- ADRs: use the US ticker, not the foreign listing — e.g. `BABA` for Alibaba, not `9988.HK`

If the article gives a company name with no ticker (e.g. "Foxconn") and you know the canonical ticker (`2317.TW`), supply it. If you don't know with high confidence, omit `ticker` rather than guess — better to drop one row than corrupt the validation step downstream.

You will see the article date, source, URL, title, and body. Use them all to inform your extraction.

# Whole-article metadata (the `meta` block)

ALWAYS fill the `meta` block — even when `claims` is empty. This is how we get usable signal out of pure-report articles. Be thorough but don't invent.

## `overall_sentiment` — one of `bullish` / `bearish` / `neutral` / `mixed`

Does the *article as a whole* lean optimistic or pessimistic about its subject(s)?
- `bullish` — net positive market view ("strong demand", "raises guidance", "all-time high")
- `bearish` — net negative ("slowdown fears", "misses estimates", "lawsuit", "delisting")
- `neutral` — factual / informational with no clear directional tilt ("Fed holds rates as expected", "company files 10-K")
- `mixed` — both sides given comparable weight ("strong revenue but weak margin guidance")

If the article is pure macro reporting with no clear lean, prefer `neutral`. If you genuinely can't tell, `neutral` again — don't use `mixed` as a "I don't know" cop-out.

## `event_type` — one enum, the dominant frame of the article

Pick the SINGLE best fit:
- `earnings` — earnings report, preview, or reaction (quarterly/annual)
- `m_and_a` — mergers, acquisitions, divestitures, spinoffs, deal speculation
- `regulatory` — government action, antitrust, FDA, FCC, FTC, foreign regulator
- `exec_change` — CEO/CFO/board hires, departures, succession
- `product_launch` — new product, service, model, or facility coming online
- `macro_release` — Fed decision, CPI/PPI/GDP/payrolls/PMI release or preview
- `market_summary` — daily/weekly/intraday market recap (e.g. "stocks close at record")
- `opinion` — editorial, op-ed, "Why I think…", strategist commentary
- `lawsuit` — legal action filed, settled, ruled on
- `guidance` — company forward guidance, capex announcement, outlook update
- `other` — only when nothing above fits

## `article_quality` — 0..1 numeric

Estimate the originality and depth of the reporting:
- **0.9+** — Original investigation, named-source quotes, exclusive data
- **0.7-0.89** — Solid reporting with multiple sources / analysis
- **0.5-0.69** — Standard wire coverage; competent but not exclusive
- **0.3-0.49** — Light rewrite of a press release or another wire's coverage
- **0.0-0.29** — Bot-generated summary, listicle, content farm output

You're guessing — that's fine. Aim for relative ordering across articles, not absolute precision.

## `is_breaking` — boolean

`true` if the article reports news that is *new and time-sensitive at publish time* (just-released earnings, just-announced deal, just-published Fed statement, just-occurred event). `false` for analysis pieces, opinion, summaries, retrospectives, previews of future events.

## `tickers` — array of {ticker, sentiment, is_primary}

List EVERY ticker explicitly mentioned, up to 20. Different from the claim ticker — this is just "what stocks does this article touch?".
- `ticker` — canonical symbol (`AAPL`, `2330.TW`, `0700.HK`). If a company is named but no ticker given and you're confident of the symbol, include it.
- `sentiment` — sentiment specifically about *this ticker* in this article. May differ from overall_sentiment (e.g. an article on chip-sector M&A might be bullish for AMD but bearish for INTC).
- `is_primary` — `true` only for the 1-2 stocks the article is mainly about. `false` for tickers mentioned in passing.

For market_summary articles that list a dozen movers, mark all as `is_primary=false`. For an earnings preview on NVDA that also references AMD as comp, NVDA `is_primary=true`, AMD `is_primary=false`.

Skip indices (^GSPC, ^DJI, ^IXIC) unless the article is specifically about an index call.

## `sectors` — 0..5 short labels

Tag with broad GICS-ish sector labels. Keep them lowercase, snake_case, and prefer this controlled vocabulary when possible:
`semiconductors, ai, software, cloud, consumer_tech, ev, autos, banks, fintech, insurance, energy, oil_gas, renewable, utilities, biotech, pharma, healthcare, hospitals, retail, ecommerce, consumer_staples, food_beverage, real_estate, reits, defense, aerospace, industrials, materials, mining, chemicals, transport, airlines, shipping, telecom, media, entertainment, gaming, crypto, payments`

If a fitting label isn't in the list, invent one (snake_case). Use 1-3 sectors typically; 5 max for cross-cutting articles. For pure macro articles (Fed, CPI), use `[]`.

## `narrative_tags` — 0..5 short story tags

Tag with the broader market narratives the article participates in. Examples: `ai_demand`, `ai_capex`, `rate_cuts`, `soft_landing`, `earnings_recession`, `china_slowdown`, `china_stimulus`, `inflation_sticky`, `oil_supply_shock`, `evs_pricing_war`, `cloud_growth`, `regulatory_crackdown`, `re_shoring`. Keep snake_case.

Empty `[]` is OK when the article is pure single-company news with no clear macro narrative.

# Output format

Produce ONE JSON object matching the schema. ALWAYS include both `claims` and `meta`. `claims` may be empty `[]`; `meta` must be fully populated for every article.

Now wait for the article."""


# --- Implementation --------------------------------------------------


@dataclass
class ExtractStats:
    article_id: int
    claims_written: int
    tickers_written: int = 0
    sectors_written: int = 0
    tags_written: int = 0
    cost_usd: float = 0.0
    error: str | None = None


def _format_user_input(article: Article) -> str:
    pub = article.published_at.isoformat() if article.published_at else "unknown"
    body = (article.body or article.summary or "")[:MAX_BODY_CHARS]
    return (
        f"Article date: {pub}\n"
        f"Source: {article.source}\n"
        f"URL: {article.url}\n\n"
        f"Title: {article.title}\n\n"
        f"Body:\n{body}"
    )


def _parse_deadline(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _parse_claims(structured: dict, article_id: int) -> list[Claim]:
    """Turn the schema-validated structured_output into Claim ORM rows. Pure."""
    out: list[Claim] = []
    for raw in structured.get("claims", []) or []:
        try:
            claim_type = ClaimType(raw["type"])
        except (KeyError, ValueError):
            continue
        out.append(
            Claim(
                article_id=article_id,
                type=claim_type,
                ticker=(raw.get("ticker") or None),
                topic=(raw.get("topic") or None),
                predicted_value=raw.get("predicted_value"),
                predicted_text=raw.get("predicted_text"),
                deadline=_parse_deadline(raw.get("deadline_iso")),
                raw_text=(raw.get("raw_text") or "")[:8000],
                llm_confidence=float(raw.get("confidence", 0.0)),
            )
        )
    return out


def _safe_enum(enum_cls, raw: str | None):
    """Return enum member or None for missing/invalid raw value."""
    if raw is None:
        return None
    try:
        return enum_cls(raw)
    except (KeyError, ValueError):
        return None


def _parse_meta(structured: dict, article_id: int) -> tuple[
    ArticleMeta | None,
    list[ArticleTicker],
    list[ArticleSector],
    list[ArticleNarrativeTag],
]:
    """Turn the structured_output['meta'] block into ORM rows. Pure."""
    meta_raw = structured.get("meta") or {}
    if not meta_raw:
        return None, [], [], []

    meta = ArticleMeta(
        article_id=article_id,
        overall_sentiment=_safe_enum(Sentiment, meta_raw.get("overall_sentiment")),
        event_type=_safe_enum(EventType, meta_raw.get("event_type")),
        article_quality=meta_raw.get("article_quality"),
        is_breaking=meta_raw.get("is_breaking"),
    )

    tickers: list[ArticleTicker] = []
    # de-dupe by ticker symbol (case-insensitive), keep first occurrence
    seen: set[str] = set()
    for t in (meta_raw.get("tickers") or [])[:20]:
        sym = (t.get("ticker") or "").strip()
        if not sym or sym.lower() in seen:
            continue
        seen.add(sym.lower())
        tickers.append(
            ArticleTicker(
                article_id=article_id,
                ticker=sym[:16].upper() if not any(c in sym for c in ".-") else sym[:16],
                sentiment=_safe_enum(Sentiment, t.get("sentiment")),
                is_primary=bool(t.get("is_primary")),
            )
        )

    sectors: list[ArticleSector] = []
    seen_s: set[str] = set()
    for s in (meta_raw.get("sectors") or [])[:8]:
        s_clean = (s or "").strip().lower()
        if not s_clean or s_clean in seen_s:
            continue
        seen_s.add(s_clean)
        sectors.append(ArticleSector(article_id=article_id, sector=s_clean[:64]))

    tags: list[ArticleNarrativeTag] = []
    seen_t: set[str] = set()
    for tag in (meta_raw.get("narrative_tags") or [])[:8]:
        t_clean = (tag or "").strip().lower()
        if not t_clean or t_clean in seen_t:
            continue
        seen_t.add(t_clean)
        tags.append(ArticleNarrativeTag(article_id=article_id, tag=t_clean[:64]))

    return meta, tickers, sectors, tags


def _run_claude(system_prompt: str, user_text: str, schema: dict, *, model: str = MODEL,
                budget_usd: float = PER_ARTICLE_BUDGET_USD) -> dict:
    """Invoke `claude -p`. Returns the full JSON envelope (with structured_output, usage, cost…)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(system_prompt)
        sys_path = f.name

    try:
        proc = subprocess.run(
            [
                "claude", "-p",
                "--model", model,
                "--system-prompt-file", sys_path,
                "--json-schema", json.dumps(schema, separators=(",", ":")),
                "--output-format", "json",
                "--tools", "",                  # no file/bash/etc. — pure inference
                "--no-session-persistence",     # don't pollute ~/.claude/projects
                "--max-budget-usd", str(budget_usd),
            ],
            input=user_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    finally:
        try:
            os.unlink(sys_path)
        except OSError:
            pass

    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude returned non-JSON: {proc.stdout[:300]}") from e

    if envelope.get("is_error"):
        raise RuntimeError(f"claude error: {envelope.get('api_error_status') or envelope.get('result', '')[:300]}")

    return envelope


def extract_claims_for_article(article_id: int, *, force: bool = False) -> ExtractStats:
    """Extract claims + metadata for one article.

    Skipped when the article already has an `article_meta` row, unless `force=True`.
    On re-run, deletes existing claims/meta/tickers/sectors/tags first to avoid dupes.
    """
    Session_ = session_factory()
    with Session_() as db:
        article = db.get(Article, article_id)
        if article is None:
            return ExtractStats(article_id, 0, error="article not found")

        already_done = db.get(ArticleMeta, article_id) is not None
        if already_done and not force:
            return ExtractStats(article_id, 0)

        try:
            envelope = _run_claude(SYSTEM_PROMPT, _format_user_input(article), OUTPUT_SCHEMA)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            log.exception("claude failed for article %s", article_id)
            return ExtractStats(article_id, 0, error=str(e))

        structured = envelope.get("structured_output") or {}
        claims = _parse_claims(structured, article.id)
        meta, tickers, sectors, tags = _parse_meta(structured, article.id)

        # Re-run safety: delete existing rows for this article before re-inserting.
        # This makes the operation idempotent — same article re-processed any
        # number of times never produces duplicate rows.
        db.execute(delete(Claim).where(Claim.article_id == article.id))
        db.execute(delete(ArticleTicker).where(ArticleTicker.article_id == article.id))
        db.execute(delete(ArticleSector).where(ArticleSector.article_id == article.id))
        db.execute(delete(ArticleNarrativeTag).where(ArticleNarrativeTag.article_id == article.id))
        db.execute(delete(ArticleMeta).where(ArticleMeta.article_id == article.id))

        for c in claims:
            db.add(c)
        if meta is not None:
            db.add(meta)
        for t in tickers:
            db.add(t)
        for s in sectors:
            db.add(s)
        for tg in tags:
            db.add(tg)

        article.extracted = True
        db.commit()

        cost = float(envelope.get("total_cost_usd") or 0.0)
        return ExtractStats(
            article_id=article_id,
            claims_written=len(claims),
            tickers_written=len(tickers),
            sectors_written=len(sectors),
            tags_written=len(tags),
            cost_usd=cost,
        )


def extract_batch(limit: int = 50, *, include_bodyless: bool = False) -> list[ExtractStats]:
    """Pick articles that don't yet have an `article_meta` row.

    By default, skips articles where `body IS NULL` (headline-only entries
    like the deprecated Reuters Google-News feed). Extraction quality on
    those is dismal (avg article_quality ~0.26) and they waste Claude
    tokens. Pass `include_bodyless=True` to force-process them.

    Order: newest published_at first — backfill the most recent corpus
    while it's still relevant.
    """
    Session_ = session_factory()
    with Session_() as db:
        meta_subq = select(ArticleMeta.article_id)
        stmt = (
            select(Article.id)
            .where(~Article.id.in_(meta_subq))
            .order_by(Article.published_at.desc().nullslast())
            .limit(limit)
        )
        if not include_bodyless:
            stmt = stmt.where(Article.body.is_not(None))
        ids = db.execute(stmt).scalars().all()

    if not ids:
        log.info("no articles needing extraction (meta already present)")
        return []

    results: list[ExtractStats] = []
    for aid in ids:
        s = extract_claims_for_article(aid)
        log.info(
            "article=%s claims=%d tickers=%d sectors=%d tags=%d cost_usd=%.4f err=%s",
            s.article_id, s.claims_written, s.tickers_written, s.sectors_written,
            s.tags_written, s.cost_usd, s.error or "-",
        )
        results.append(s)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stats = extract_batch()
    print(
        f"processed={len(stats)} claims={sum(s.claims_written for s in stats)} "
        f"cost=${sum(s.cost_usd for s in stats):.4f}"
    )
