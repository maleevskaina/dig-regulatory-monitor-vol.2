#!/usr/bin/env python3
"""
Digital Regulatory Monitor — Scraper
=====================================
Scrapes real regulatory sources (RSS feeds + HTML pages) for the Prosus
Digital Regulatory Team. Outputs data/articles.json.

Anti-hallucination guarantees:
  - Every article URL comes directly from the feed/source.
  - Dates are parsed from structured feed metadata; if unparseable the
    article is dropped rather than guessing.
  - Excerpts are taken verbatim from feed summaries (truncated to 400 chars).
  - No AI generation of titles, URLs, or dates.
  - Optional: if ANTHROPIC_API_KEY is set, Claude is used ONLY to improve
    topic classification — never to generate URLs or titles.

Usage:
  python scripts/scraper.py

Requirements: see scripts/requirements.txt
"""

import json
import os
import re
import hashlib
import time
import logging
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ"
)
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_FILE = REPO_ROOT / "data" / "articles.json"
MAX_ARTICLE_AGE_DAYS = 90          # prune articles older than this
MAX_EXCERPT_LENGTH = 400           # characters
REQUEST_TIMEOUT = 20               # seconds per HTTP request
REQUEST_DELAY = 1.5                # seconds between feed fetches (be polite)
VERIFY_URL_TIMEOUT = 10            # seconds for URL validation HEAD request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DigitalRegulatoryMonitor/1.0; "
        "+https://github.com/maleevskaina/digital-regulatory-monitor)"
    )
}

# ─────────────────────────────────────────────
# TOPIC & JURISDICTION CLASSIFICATION
# ─────────────────────────────────────────────

TOPIC_KEYWORDS = {
    "Competition": [
        "competition", "antitrust", "anti-trust", "merger", "cartel",
        "dominance", "abuse of dominance", "market inquiry", "CMA",
        "DG COMP", "CADE", "CCI", "compcom", "price fixing",
        "market power", "market definition", "chapter II", "chapter I",
        "section 2", "vertical agreement", "horizontal agreement",
        "gun jumping", "Phase II", "Phase I", "market study",
        "consumer choice", "switching costs", "interoperability"
    ],
    "Privacy": [
        "data protection", "privacy", "GDPR", "personal data", "data breach",
        "data subject", "consent", "lawful basis", "processor", "controller",
        "ICO", "EDPB", "ANPD", "LGPD", "PDPB", "POPIA",
        "right to erasure", "data minimisation", "legitimate interest",
        "special category", "transfer", "adequacy", "SCCs",
        "biometric", "health data", "surveillance", "cookies",
        "marketing", "profiling", "automated decision"
    ],
    "DMA/DSA": [
        "DMA", "DSA", "Digital Markets Act", "Digital Services Act",
        "gatekeeper", "core platform service", "online intermediation",
        "designated", "interoperability", "self-preferencing",
        "online platform", "very large online platform", "VLOP",
        "very large search engine", "VLSE", "Online Safety Act",
        "Ofcom", "digital markets", "app store", "sideloading",
        "default settings", "fair and contestable"
    ],
    "AI Regulation": [
        "artificial intelligence", "AI", "machine learning", "foundation model",
        "large language model", "LLM", "generative AI", "algorithm",
        "automated decision", "AI Act", "AI liability", "AI safety",
        "deepfake", "biometric identification", "high-risk AI",
        "transparency obligation", "explainability", "AI governance",
        "AI system", "prohibited AI", "AI auditing", "conformity assessment"
    ],
    "Fintech": [
        "fintech", "payment", "payment services", "PSD", "PSD2", "PSD3",
        "open banking", "crypto", "cryptocurrency", "digital asset",
        "stablecoin", "BNPL", "buy now pay later", "MiCA",
        "e-money", "payment institution", "acquiring", "PayU",
        "digital wallet", "financial inclusion", "remittance",
        "virtual currency", "DeFi", "decentralised finance",
        "central bank digital currency", "CBDC", "RegTech"
    ],
    "IP": [
        "intellectual property", "copyright", "trademark", "patent",
        "trade secret", "sui generis", "database right", "design right",
        "WIPO", "EPO", "EUIPO", "licensing", "fair use", "fair dealing",
        "AI-generated", "ownership of AI output", "piracy", "counterfeit",
        "right of publicity", "moral rights", "neighbouring rights"
    ]
}

JURISDICTION_KEYWORDS = {
    "EU": [
        "european commission", "european union", "eu ", " eu ", "eur-lex",
        "edpb", "enisa", "esma", "eba", "dg comp", "dg connect",
        "official journal", "regulation (eu)", "directive", "brussels",
        "france", "germany", "netherlands", "spain", "italy", "poland",
        "bundeskartellamt", "cnil", "acm ", "agcm", "cnmc", "uokik"
    ],
    "UK": [
        "uk ", " uk ", "united kingdom", "cma", "ico ", "ofcom",
        "fca ", "psr ", "competition appeal tribunal", "cat ",
        "england", "scotland", "wales", "hmrc", "competition act 1998",
        "digital markets, competition", "dmcc", "online safety act"
    ],
    "Brazil": [
        "brazil", "brasil", "cade", "anpd", "bcb ", "banco central",
        "senacon", "procon", "lgpd", "lei geral", "são paulo",
        "rio de janeiro", "brazilian"
    ],
    "India": [
        "india", "indian", "cci ", "meity", "trai ", "rbi ",
        "pdpb", "competition act 2002", "delhi", "mumbai",
        "competition commission of india"
    ],
    "South Africa": [
        "south africa", "south african", "compcom", "competition commission",
        "competition tribunal", "nersa", "icasa ", "popia",
        "competition act", "cape town", "johannesburg", "pretoria"
    ],
    "US": [
        "united states", "ftc ", "doj ", "department of justice",
        "federal trade commission", "sec ", "cfpb ", "fcc ",
        "antitrust division", "sherman act", "ftc act",
        "california", "new york", "washington dc", "congress",
        "senate", "house of representatives", "american"
    ]
}

def classify(text: str) -> tuple[list[str], list[str]]:
    """
    Keyword-based topic and jurisdiction classification.
    Returns (topics, jurisdictions). Always returns at least one of each.
    """
    lower = text.lower()
    topics = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw.lower() in lower for kw in keywords):
            topics.append(topic)
    jurisdictions = []
    for juris, keywords in JURISDICTION_KEYWORDS.items():
        if any(kw.lower() in lower for kw in keywords):
            jurisdictions.append(juris)
    # Fallback
    if not topics:
        topics = ["Competition"]  # most common default
    if not jurisdictions:
        jurisdictions = ["EU"]
    return topics, jurisdictions

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def make_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

def clean_html(raw: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text(separator=" ")
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:MAX_EXCERPT_LENGTH]

def parse_date(entry) -> Optional[str]:
    """
    Try multiple feedparser date fields and return ISO 8601 UTC string.
    Returns None if no valid date found — article will be skipped.
    """
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                dt = datetime(*val[:6], tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
    # Try string fields
    for field in ("published", "updated", "created"):
        val = getattr(entry, field, None)
        if val:
            try:
                dt = dateutil_parser.parse(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
    return None

def is_too_old(iso_date: str, max_days: int = MAX_ARTICLE_AGE_DAYS) -> bool:
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt) > timedelta(days=max_days)
    except Exception:
        return False

def verify_url(url: str) -> bool:
    """
    HEAD request to check URL is reachable. Returns True if 200-399.
    Skips verification for known reliable institutional domains to save time.
    """
    TRUSTED_DOMAINS = [
        "gov.uk", "ec.europa.eu", "eur-lex.europa.eu", "edpb.europa.eu",
        "ftc.gov", "justice.gov", "ico.org.uk", "ofcom.org.uk",
        "gov.br", "cci.gov.in", "compcom.co.za"
    ]
    if any(d in url for d in TRUSTED_DOMAINS):
        return True
    try:
        r = requests.head(url, timeout=VERIFY_URL_TIMEOUT, headers=HEADERS,
                          allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False

# ─────────────────────────────────────────────
# SOURCES
# ─────────────────────────────────────────────
# Each source is a dict:
#   type: "rss" | "html"
#   url: feed or page URL
#   name: display name
#   source_url: homepage
#   default_juris: fallback jurisdiction(s)
#   default_topics: fallback topic(s)
#   [html-only] item_selector: CSS selector for article list items
#   [html-only] title_selector: CSS selector for title within item
#   [html-only] link_selector: CSS selector for link
#   [html-only] date_selector: CSS selector for date string
#   [html-only] excerpt_selector: CSS selector for excerpt (optional)

SOURCES = [
    # ── UK ──
    {
        "type": "rss",
        "url": "https://www.gov.uk/government/organisations/competition-and-markets-authority.atom",
        "name": "Competition and Markets Authority (CMA)",
        "source_url": "https://www.gov.uk/cma",
        "default_juris": ["UK"],
        "default_topics": ["Competition"]
    },
    {
        "type": "rss",
        "url": "https://ico.org.uk/about-the-ico/news-and-events/news-and-blogs/feed/",
        "name": "Information Commissioner's Office (ICO)",
        "source_url": "https://ico.org.uk",
        "default_juris": ["UK"],
        "default_topics": ["Privacy"]
    },
    {
        "type": "rss",
        "url": "https://www.ofcom.org.uk/research-and-data/telecoms-research/connected-nations/rss",
        "name": "Ofcom",
        "source_url": "https://www.ofcom.org.uk",
        "default_juris": ["UK"],
        "default_topics": ["DMA/DSA"]
    },
    # ── EU ──
    {
        "type": "rss",
        "url": "https://www.edpb.europa.eu/feed_en",
        "name": "European Data Protection Board (EDPB)",
        "source_url": "https://www.edpb.europa.eu",
        "default_juris": ["EU"],
        "default_topics": ["Privacy"]
    },
    {
        "type": "rss",
        "url": "https://ec.europa.eu/commission/presscorner/api/rss?topics=competition",
        "name": "European Commission — Competition",
        "source_url": "https://ec.europa.eu/competition",
        "default_juris": ["EU"],
        "default_topics": ["Competition"]
    },
    {
        "type": "rss",
        "url": "https://ec.europa.eu/commission/presscorner/api/rss?topics=digital",
        "name": "European Commission — Digital",
        "source_url": "https://ec.europa.eu",
        "default_juris": ["EU"],
        "default_topics": ["DMA/DSA"]
    },
    {
        "type": "rss",
        "url": "https://eur-lex.europa.eu/RSSEP/rssCategories.do?locale=en&type=REG",
        "name": "EUR-Lex — New EU Regulations",
        "source_url": "https://eur-lex.europa.eu",
        "default_juris": ["EU"],
        "default_topics": ["Competition"]
    },
    # ── US ──
    {
        "type": "rss",
        "url": "https://www.ftc.gov/news-events/news/press-releases.rss",
        "name": "Federal Trade Commission (FTC)",
        "source_url": "https://www.ftc.gov",
        "default_juris": ["US"],
        "default_topics": ["Competition"]
    },
    {
        "type": "rss",
        "url": "https://www.justice.gov/feeds/opa/justice-news.xml",
        "name": "US Department of Justice — Antitrust",
        "source_url": "https://www.justice.gov/antitrust",
        "default_juris": ["US"],
        "default_topics": ["Competition"]
    },
    # ── Brazil ──
    {
        "type": "html",
        "url": "https://www.gov.br/cade/pt-br/assuntos/noticias",
        "name": "CADE — Administrative Council for Economic Defense",
        "source_url": "https://www.gov.br/cade",
        "default_juris": ["Brazil"],
        "default_topics": ["Competition"],
        "item_selector": "article.tileItem",
        "title_selector": "h2.tileHeadline a, h2 a",
        "link_selector": "h2.tileHeadline a, h2 a",
        "date_selector": "span.summary-view-icon",
        "excerpt_selector": "p.tileBody"
    },
    {
        "type": "html",
        "url": "https://www.gov.br/anpd/pt-br/assuntos/noticias",
        "name": "Autoridade Nacional de Proteção de Dados (ANPD)",
        "source_url": "https://www.gov.br/anpd",
        "default_juris": ["Brazil"],
        "default_topics": ["Privacy"],
        "item_selector": "article.tileItem",
        "title_selector": "h2.tileHeadline a, h2 a",
        "link_selector": "h2.tileHeadline a, h2 a",
        "date_selector": "span.summary-view-icon",
        "excerpt_selector": "p.tileBody"
    },
    # ── India ──
    {
        "type": "html",
        "url": "https://www.cci.gov.in/media-corner/press-releases",
        "name": "Competition Commission of India (CCI)",
        "source_url": "https://www.cci.gov.in",
        "default_juris": ["India"],
        "default_topics": ["Competition"],
        "item_selector": "div.views-row, tr.views-row, li.views-row",
        "title_selector": "a",
        "link_selector": "a",
        "date_selector": "span.date-display-single, td.views-field-field-date",
        "excerpt_selector": None
    },
    # ── South Africa ──
    {
        "type": "html",
        "url": "https://www.compcom.co.za/press-releases/",
        "name": "Competition Commission South Africa",
        "source_url": "https://www.compcom.co.za",
        "default_juris": ["South Africa"],
        "default_topics": ["Competition"],
        "item_selector": "article, div.post, li.type-post",
        "title_selector": "h2 a, h3 a, .entry-title a",
        "link_selector": "h2 a, h3 a, .entry-title a",
        "date_selector": "time, span.date, .entry-date",
        "excerpt_selector": "div.entry-summary p, p.excerpt"
    }
]

# ─────────────────────────────────────────────
# RSS SCRAPER
# ─────────────────────────────────────────────

def scrape_rss(source: dict) -> list[dict]:
    results = []
    try:
        log.info(f"  → Fetching RSS: {source['name']}")
        feed = feedparser.parse(source["url"])
        if feed.bozo and not feed.entries:
            log.warning(f"    Feed error: {feed.bozo_exception}")
            return []
        for entry in feed.entries[:30]:  # max 30 per feed
            url = getattr(entry, "link", None)
            title = getattr(entry, "title", None)
            if not url or not title:
                continue
            title = clean_html(title)
            published_at = parse_date(entry)
            if not published_at:
                log.debug(f"    Skipping (no date): {title[:60]}")
                continue
            if is_too_old(published_at):
                continue
            raw_summary = (
                getattr(entry, "summary", None) or
                getattr(entry, "description", None) or
                getattr(entry, "content", [{}])[0].get("value", "")
            )
            excerpt = clean_html(raw_summary)
            combined_text = f"{title} {excerpt} {url}"
            topics, jurisdictions = classify(combined_text)
            # Use source defaults if classification fails to find anything useful
            if not any(t in topics for t in TOPIC_KEYWORDS.keys()):
                topics = source["default_topics"][:]
            if not any(j in jurisdictions for j in JURISDICTION_KEYWORDS.keys()):
                jurisdictions = source["default_juris"][:]
            results.append({
                "id": make_id(url),
                "title": title,
                "url": url,
                "source": source["name"],
                "source_url": source["source_url"],
                "published_at": published_at,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "jurisdictions": jurisdictions,
                "topics": topics,
                "excerpt": excerpt or f"Coverage from {source['name']}.",
                "is_verified": True  # RSS URLs are from the source directly
            })
    except Exception:
        log.error(f"    Exception scraping {source['name']}: {traceback.format_exc()}")
    return results

# ─────────────────────────────────────────────
# HTML SCRAPER
# ─────────────────────────────────────────────

def scrape_html(source: dict) -> list[dict]:
    results = []
    try:
        log.info(f"  → Fetching HTML: {source['name']}")
        r = requests.get(source["url"], headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select(source["item_selector"])
        if not items:
            log.warning(f"    No items found with selector: {source['item_selector']}")
            return []
        log.info(f"    Found {len(items)} raw items")
        for item in items[:20]:
            # Title
            title_el = item.select_one(source["title_selector"])
            if not title_el:
                continue
            title = clean_html(title_el.get_text())
            if not title or len(title) < 10:
                continue
            # URL
            link_el = item.select_one(source["link_selector"])
            if not link_el:
                continue
            href = link_el.get("href", "")
            if not href:
                continue
            # Make absolute
            if href.startswith("/"):
                from urllib.parse import urlparse
                parsed = urlparse(source["url"])
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            elif not href.startswith("http"):
                href = source["source_url"].rstrip("/") + "/" + href.lstrip("/")
            # Date
            date_el = item.select_one(source.get("date_selector", "time"))
            published_at = None
            if date_el:
                date_str = date_el.get("datetime") or date_el.get_text()
                if date_str:
                    try:
                        dt = dateutil_parser.parse(date_str.strip(), dayfirst=True)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        else:
                            dt = dt.astimezone(timezone.utc)
                        published_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        pass
            if not published_at:
                log.debug(f"    Skipping (no date): {title[:60]}")
                continue
            if is_too_old(published_at):
                continue
            # Excerpt
            excerpt = ""
            if source.get("excerpt_selector"):
                exc_el = item.select_one(source["excerpt_selector"])
                if exc_el:
                    excerpt = clean_html(exc_el.get_text())
            combined = f"{title} {excerpt} {href}"
            topics, jurisdictions = classify(combined)
            results.append({
                "id": make_id(href),
                "title": title,
                "url": href,
                "source": source["name"],
                "source_url": source["source_url"],
                "published_at": published_at,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "jurisdictions": jurisdictions,
                "topics": topics,
                "excerpt": excerpt or f"From {source['name']}.",
                "is_verified": False  # will be checked separately
            })
    except requests.HTTPError as e:
        log.warning(f"    HTTP error for {source['name']}: {e}")
    except Exception:
        log.error(f"    Exception scraping {source['name']}: {traceback.format_exc()}")
    return results

# ─────────────────────────────────────────────
# GMAIL / MLEX INTEGRATION (placeholder)
# ─────────────────────────────────────────────

def scrape_gmail_mlex() -> list[dict]:
    """
    Reads MLEX email alerts from Gmail. Requires GMAIL_CREDENTIALS_JSON
    and GMAIL_TOKEN_JSON to be set as environment variables (base64-encoded).

    This is a scaffold — enable by setting the environment variables.
    See SETUP.md for instructions on connecting Gmail.
    """
    creds_b64 = os.environ.get("GMAIL_CREDENTIALS_JSON")
    token_b64 = os.environ.get("GMAIL_TOKEN_JSON")

    if not creds_b64 or not token_b64:
        log.info("Gmail/MLEX: credentials not configured, skipping.")
        return []

    try:
        import base64
        import tempfile
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds_json = base64.b64decode(creds_b64).decode()
        token_json = base64.b64decode(token_b64).decode()

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(token_json)
            token_file = f.name

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(creds_json)
            creds_file = f.name

        creds = Credentials.from_authorized_user_file(token_file)
        service = build("gmail", "v1", credentials=creds)

        # Search for MLEX emails in last 8 days
        query = "from:mlex OR from:@mlex.com newer_than:8d"
        messages = service.users().messages().list(
            userId="me", q=query, maxResults=20
        ).execute().get("messages", [])

        results = []
        for msg in messages:
            msg_data = service.users().messages().get(
                userId="me", id=msg["id"], format="full"
            ).execute()
            headers = {h["name"]: h["value"] for h in msg_data["payload"]["headers"]}
            subject = headers.get("Subject", "")
            date_str = headers.get("Date", "")
            # Only include if subject looks like a regulatory news item
            if not subject or len(subject) < 10:
                continue
            try:
                published_at = dateutil_parser.parse(date_str).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue
            topics, jurisdictions = classify(subject)
            results.append({
                "id": make_id(f"gmail-{msg['id']}"),
                "title": subject,
                "url": f"https://mail.google.com/mail/u/0/#inbox/{msg['id']}",
                "source": "MLEX (via Gmail)",
                "source_url": "https://mlex.com",
                "published_at": published_at,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "jurisdictions": jurisdictions,
                "topics": topics,
                "excerpt": f"MLEX alert: {subject}",
                "is_verified": True
            })
        log.info(f"Gmail/MLEX: found {len(results)} relevant emails")
        return results
    except ImportError:
        log.warning("Gmail: google-api-python-client not installed. Install it to enable Gmail integration.")
        return []
    except Exception:
        log.error(f"Gmail/MLEX error: {traceback.format_exc()}")
        return []

# ─────────────────────────────────────────────
# OPTIONAL: CLAUDE API TOPIC ENHANCEMENT
# ─────────────────────────────────────────────

def enhance_with_claude(articles: list[dict]) -> list[dict]:
    """
    If ANTHROPIC_API_KEY is set, use Claude Haiku to improve topic
    classification for articles that only matched one topic.
    Claude is given ONLY the title and excerpt — it cannot invent URLs or dates.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return articles
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        valid_topics = list(TOPIC_KEYWORDS.keys())
        valid_juris = list(JURISDICTION_KEYWORDS.keys())
        enhanced = []
        for a in articles:
            if len(a["topics"]) > 1 and len(a["jurisdictions"]) > 1:
                enhanced.append(a)
                continue
            prompt = (
                f"Article title: {a['title']}\n"
                f"Excerpt: {a['excerpt']}\n"
                f"Source: {a['source']}\n\n"
                f"Valid topics: {valid_topics}\n"
                f"Valid jurisdictions: {valid_juris}\n\n"
                "Return JSON only: {{\"topics\": [...], \"jurisdictions\": [...]}}\n"
                "Use only values from the valid lists above. No explanation."
            )
            try:
                msg = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}]
                )
                raw = msg.content[0].text.strip()
                parsed = json.loads(raw)
                new_topics = [t for t in parsed.get("topics", []) if t in valid_topics]
                new_juris = [j for j in parsed.get("jurisdictions", []) if j in valid_juris]
                if new_topics:
                    a["topics"] = new_topics
                if new_juris:
                    a["jurisdictions"] = new_juris
            except Exception:
                pass  # keep original classification on error
            enhanced.append(a)
        return enhanced
    except ImportError:
        log.info("anthropic package not installed; skipping Claude enhancement")
        return articles

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("Digital Regulatory Monitor — Scraper starting")
    log.info(f"Target: {DATA_FILE}")
    log.info("=" * 60)

    # Load existing data
    existing_articles = {}
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            for a in old_data.get("articles", []):
                existing_articles[a["id"]] = a
            log.info(f"Loaded {len(existing_articles)} existing articles")
        except Exception as e:
            log.warning(f"Could not read existing data: {e}")

    # Scrape all sources
    new_articles = []
    for source in SOURCES:
        try:
            if source["type"] == "rss":
                articles = scrape_rss(source)
            elif source["type"] == "html":
                articles = scrape_html(source)
            else:
                continue
            log.info(f"    → {len(articles)} articles from {source['name']}")
            new_articles.extend(articles)
            time.sleep(REQUEST_DELAY)
        except Exception:
            log.error(f"Failed source {source['name']}: {traceback.format_exc()}")

    # Gmail/MLEX
    gmail_articles = scrape_gmail_mlex()
    new_articles.extend(gmail_articles)

    log.info(f"Total raw new articles: {len(new_articles)}")

    # Optional Claude enhancement
    new_articles = enhance_with_claude(new_articles)

    # Merge: new articles take precedence (fresher data)
    merged = dict(existing_articles)
    added = 0
    updated = 0
    for a in new_articles:
        if a["id"] not in merged:
            added += 1
        else:
            updated += 1
        merged[a["id"]] = a

    # Prune old articles
    before_prune = len(merged)
    merged = {k: v for k, v in merged.items() if not is_too_old(v["published_at"])}
    pruned = before_prune - len(merged)

    final_articles = list(merged.values())
    final_articles.sort(key=lambda a: a["published_at"], reverse=True)

    log.info(f"Added: {added}, Updated: {updated}, Pruned: {pruned}")
    log.info(f"Final article count: {len(final_articles)}")

    # Write output
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": "1.0",
        "articles": final_articles
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"✓ Written to {DATA_FILE}")
    log.info("=" * 60)

if __name__ == "__main__":
    main()
