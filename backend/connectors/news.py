"""
News connector — fetches recent headlines for a company via Google News RSS.
Results are cached in data/news_cache.json with a 7-day TTL.

Tags articles automatically:
  acquisition  — merger, acqui, bought, purchase, deal
  leadership   — CEO, CFO, CTO, COO, president, appoint, resign, hire, named, depart
  news         — everything else
"""
import os
import json
import time
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

NEWS_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "news_cache.json"
)
CACHE_TTL_DAYS = 7
MAX_ARTICLES = 5

ACQUISITION_KW = ["acqui", "merger", "merges", "bought", "purchase", "acquires", " deal "]
LEADERSHIP_KW  = ["ceo", "cfo", "cto", "coo", "president", "appoint", "resign",
                  "hire", "hired", "named", "depart", "steps down", "joins as"]


def _tag(title: str) -> str:
    t = title.lower()
    if any(k in t for k in ACQUISITION_KW):
        return "acquisition"
    if any(k in t for k in LEADERSHIP_KW):
        return "leadership"
    return "news"


def _load_cache() -> dict:
    try:
        with open(NEWS_CACHE_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(NEWS_CACHE_PATH), exist_ok=True)
    tmp = NEWS_CACHE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, NEWS_CACHE_PATH)


def _is_stale(entry: dict) -> bool:
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
        return datetime.now() - fetched > timedelta(days=CACHE_TTL_DAYS)
    except Exception:
        return True


def _fetch_news(company_name: str) -> list:
    """Query Google News RSS for company_name. Returns list of article dicts."""
    query = requests.utils.quote(f'"{company_name}"')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        articles = []
        for item in root.findall(".//item")[:MAX_ARTICLES]:
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            pub   = item.findtext("pubDate", "").strip()
            source_el = item.find("{https://news.google.com/rss}source") or item.find("source")
            source = source_el.text if source_el is not None else ""
            # Parse date
            try:
                pub_dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z")
                pub_str = pub_dt.strftime("%Y-%m-%d")
            except Exception:
                pub_str = ""
            articles.append({
                "title": title,
                "source": source,
                "date": pub_str,
                "url": link,
                "tag": _tag(title),
            })
        return articles
    except Exception:
        return []


def get_news(company_name: str, force_refresh: bool = False) -> list:
    """
    Return cached news for company_name. Fetches from Google News if stale/missing.
    Returns list of article dicts (may be empty).
    """
    if not company_name:
        return []

    cache = _load_cache()
    key = company_name.lower().strip()

    if not force_refresh and key in cache and not _is_stale(cache[key]):
        return cache[key]["articles"]

    articles = _fetch_news(company_name)
    cache[key] = {
        "fetched_at": datetime.now().isoformat(),
        "articles": articles,
    }
    _save_cache(cache)
    return articles


def get_news_cached_only(company_name: str) -> list:
    """Return news from cache only — no network call. Returns [] if not cached."""
    if not company_name:
        return []
    cache = _load_cache()
    entry = cache.get(company_name.lower().strip(), {})
    return entry.get("articles", [])


def refresh_news_for_accounts(account_names: list, delay_sec: float = 0.5) -> None:
    """
    Fetch fresh news for a list of company names, respecting a delay between
    requests to avoid rate limiting. Saves to cache as it goes.
    """
    cache = _load_cache()
    for name in account_names:
        key = name.lower().strip()
        if key in cache and not _is_stale(cache[key]):
            continue
        articles = _fetch_news(name)
        cache[key] = {
            "fetched_at": datetime.now().isoformat(),
            "articles": articles,
        }
        _save_cache(cache)
        time.sleep(delay_sec)
