import json
import os
import re
import requests
from datetime import datetime, timezone, timedelta

PAGE_IDS = {
    "overview":       "1Z2cqpFFAWMFEV_6Bs6lHFclY0o",  # /dashboard
    "legacy_list":    "V0K2ZXAfF7XhY-RIPKy1R4W921Y",  # /surveys
    "legacy_detail":  "xhL2Z5jqbnkWX3Uvjj5YVndOPoo",  # /surveys/**
}

PAGE_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "pendo_pages_cache.json")

PENDO_BASE = "https://app.pendo.io/api/v1"


def _pendo_headers():
    key = os.getenv("PENDO_API_KEY")
    if not key:
        raise RuntimeError("PENDO_API_KEY not set in environment")
    return {"x-pendo-integration-key": key, "content-type": "application/json"}


def _pendo_available() -> bool:
    return bool(os.getenv("PENDO_API_KEY"))


def _normalize(name: str) -> str:
    if not name:
        return ""
    name = name.lower()
    name = re.sub(
        r'\b(llc|inc|corp|co|ltd|dba|the|group|staffing|solutions|services|talent|consulting|associates|lp|llp)\b',
        '', name
    )
    name = re.sub(r'[^a-z0-9 ]', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def get_pendo_account_stats() -> dict[str, dict]:
    """
    Fetch all Pendo accounts and visitors. Returns a dict keyed by normalized
    account name. Each value contains:
        lastvisit_dt          — datetime of most recent login (any user), or None
        visitor_lastvisits_ms — list of each visitor's last-visit timestamp (ms)
                                used to compute unique user counts for any window
        pendo_account_id      — Pendo internal account ID
    """
    if not _pendo_available():
        return {}
    headers = _pendo_headers()

    # Pull all accounts
    r = requests.post(
        f"{PENDO_BASE}/aggregation", headers=headers, timeout=60,
        json={"response": {"mimeType": "application/json"},
              "request": {"pipeline": [{"source": {"accounts": None}}]}}
    )
    r.raise_for_status()
    accounts = r.json().get("results", [])

    # Pull all identified visitors with their last-visit timestamp and account
    vr = requests.post(
        f"{PENDO_BASE}/aggregation", headers=headers, timeout=60,
        json={"response": {"mimeType": "application/json"},
              "request": {"pipeline": [{"source": {"visitors": {"identified": True}}}]}}
    )
    vr.raise_for_status()
    visitors = vr.json().get("results", [])

    # Group visitor last-visit timestamps by Pendo account ID
    visitor_ts_by_account: dict[str, list[int]] = {}
    for v in visitors:
        auto = v.get("metadata", {}).get("auto", {})
        lv = auto.get("lastvisit")
        if not lv:
            continue
        acct_id = str(auto.get("accountid") or "")
        if acct_id:
            visitor_ts_by_account.setdefault(acct_id, []).append(int(lv))

    # Build lookup keyed by normalized account name
    stats: dict[str, dict] = {}
    for acc in accounts:
        auto = acc.get("metadata", {}).get("auto", {})
        agent = acc.get("metadata", {}).get("agent", {})
        acct_id = str(acc.get("accountId", ""))
        name = agent.get("account_name", "")
        key = _normalize(name)
        if not key:
            continue

        lv_ms = auto.get("lastvisit")
        lastvisit_dt = (
            datetime.fromtimestamp(lv_ms / 1000, tz=timezone.utc) if lv_ms else None
        )

        stats[key] = {
            "lastvisit_dt": lastvisit_dt,
            "visitor_lastvisits_ms": visitor_ts_by_account.get(acct_id, []),
            "pendo_account_id": acct_id,
            "pendo_account_name": name,
        }

    return stats


def _fetch_page_stats_for_page(page_id: str, days: int) -> dict[str, dict]:
    """
    Fetch per-account volume stats + per-visit duration distribution for one page.
    Returns dict: pendo_account_id → {
        total_views, total_minutes, unique_visitors,
        total_visits, bounce_visits, median_mins_per_visit
    }
    'bounce' = visitor-day with ≤ 1 minute (Pendo rounds to nearest minute,
    so 0–1 min covers roughly 0–90 seconds of actual time).
    """
    headers = _pendo_headers()
    first_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    src = {
        "pageEvents": {"pageId": page_id},
        "timeSeries": {"period": "dayRange", "first": first_ms, "count": days},
    }

    # Query 1: volume rollup (views, minutes, unique visitors)
    vol_pipeline = {
        "response": {"mimeType": "application/json"},
        "request": {"pipeline": [
            {"source": src},
            {"group": {"group": ["accountId", "visitorId"], "fields": [
                {"v_views": {"sum": "numEvents"}},
                {"v_mins":  {"sum": "numMinutes"}},
            ]}},
            {"group": {"group": ["accountId"], "fields": [
                {"total_views":     {"sum": "v_views"}},
                {"total_minutes":   {"sum": "v_mins"}},
                {"unique_visitors": {"count": "visitorId"}},
            ]}},
        ]},
    }

    # Query 2: per visitor-day visits (for bounce rate + median)
    visit_pipeline = {
        "response": {"mimeType": "application/json"},
        "request": {"pipeline": [
            {"source": src},
            {"group": {"group": ["accountId", "visitorId", "day"], "fields": [
                {"visit_mins": {"sum": "numMinutes"}},
            ]}},
        ]},
    }

    vol_r   = requests.post(f"{PENDO_BASE}/aggregation", headers=headers, json=vol_pipeline,   timeout=60)
    visit_r = requests.post(f"{PENDO_BASE}/aggregation", headers=headers, json=visit_pipeline, timeout=60)
    vol_r.raise_for_status()
    visit_r.raise_for_status()

    # Build per-account visit duration lists for median + bounce
    visit_mins_by_acct: dict[str, list[int]] = {}
    for row in visit_r.json().get("results", []):
        acct_id = str(row.get("accountId") or "")
        if acct_id:
            visit_mins_by_acct.setdefault(acct_id, []).append(int(row.get("visit_mins", 0) or 0))

    def _median(vals: list[int]) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        n = len(s)
        mid = n // 2
        return float(s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2)

    result = {}
    for row in vol_r.json().get("results", []):
        acct_id = str(row.get("accountId") or "")
        if not acct_id:
            continue
        visits    = visit_mins_by_acct.get(acct_id, [])
        bounces   = sum(1 for m in visits if m <= 1)
        result[acct_id] = {
            "total_views":          int(row.get("total_views", 0) or 0),
            "total_minutes":        round(float(row.get("total_minutes", 0) or 0), 1),
            "unique_visitors":      int(row.get("unique_visitors", 0) or 0),
            "total_visits":         len(visits),
            "bounce_visits":        bounces,
            "bounce_pct":           round(bounces / len(visits) * 100) if visits else 0,
            "median_mins_per_visit": _median(visits),
        }
    return result


def get_pendo_page_analytics(days: int = 90) -> dict[str, dict]:
    """
    Fetch page analytics for Overview + Legacy pages for all accounts.
    Returns dict keyed by normalized account name:
        {
          overview:      {total_views, total_minutes, unique_visitors}
          legacy_list:   {total_views, total_minutes, unique_visitors}
          legacy_detail: {total_views, total_minutes, unique_visitors}
          legacy_total:  {total_views, total_minutes, unique_visitors}  ← list + detail combined
        }
    Results are cached to disk for 12 hours.
    """
    if not _pendo_available():
        return {}
    cache_key = f"days_{days}"
    try:
        with open(PAGE_CACHE_PATH) as f:
            cache = json.load(f)
        entry = cache.get(cache_key, {})
        cached_at = entry.get("_cached_at")
        if cached_at:
            age_hours = (datetime.now(timezone.utc).timestamp() - cached_at) / 3600
            if age_hours < 12:
                return entry.get("data", {})
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        cache = {}

    # Fetch all three pages
    overview      = _fetch_page_stats_for_page(PAGE_IDS["overview"],      days)
    legacy_list   = _fetch_page_stats_for_page(PAGE_IDS["legacy_list"],   days)
    legacy_detail = _fetch_page_stats_for_page(PAGE_IDS["legacy_detail"], days)

    # Build account_id → name map from the accounts we already know about
    acct_stats = get_pendo_account_stats()
    id_to_key = {v["pendo_account_id"]: k for k, v in acct_stats.items() if v.get("pendo_account_id")}

    # Combine all account IDs seen across all pages
    all_ids = set(overview) | set(legacy_list) | set(legacy_detail)

    _blank = {
        "total_views": 0, "total_minutes": 0.0, "unique_visitors": 0,
        "total_visits": 0, "bounce_visits": 0, "bounce_pct": 0,
        "median_mins_per_visit": 0.0,
    }

    by_name: dict[str, dict] = {}
    for acct_id in all_ids:
        name_key = id_to_key.get(acct_id)
        if not name_key:
            continue

        ov  = overview.get(acct_id,      dict(_blank))
        ll  = legacy_list.get(acct_id,   dict(_blank))
        ld  = legacy_detail.get(acct_id, dict(_blank))

        # Combine legacy list + detail: sum visits/bounces, recompute pct + median
        leg_visits  = ll["total_visits"]  + ld["total_visits"]
        leg_bounces = ll["bounce_visits"] + ld["bounce_visits"]
        # Weighted median approximation: use whichever sub-page dominates by visit count
        if ll["total_visits"] >= ld["total_visits"]:
            leg_median = ll["median_mins_per_visit"]
        else:
            leg_median = ld["median_mins_per_visit"]

        by_name[name_key] = {
            "overview":    ov,
            "legacy_list": ll,
            "legacy_detail": ld,
            "legacy_total": {
                "total_views":           ll["total_views"]    + ld["total_views"],
                "total_minutes":         round(ll["total_minutes"] + ld["total_minutes"], 1),
                "unique_visitors":       max(ll["unique_visitors"], ld["unique_visitors"]),
                "total_visits":          leg_visits,
                "bounce_visits":         leg_bounces,
                "bounce_pct":            round(leg_bounces / leg_visits * 100) if leg_visits else 0,
                "median_mins_per_visit": leg_median,
            },
        }

    # Save to cache
    cache[cache_key] = {"_cached_at": datetime.now(timezone.utc).timestamp(), "data": by_name}
    os.makedirs(os.path.dirname(PAGE_CACHE_PATH), exist_ok=True)
    with open(PAGE_CACHE_PATH, "w") as f:
        json.dump(cache, f)

    return by_name


def clear_page_analytics_cache() -> None:
    try:
        os.remove(PAGE_CACHE_PATH)
    except FileNotFoundError:
        pass
