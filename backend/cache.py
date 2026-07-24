import json
import os
import time
import threading

CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache.json")
CACHE_TTL = 3600       # 1 hour — serve fresh data
STALE_TTL = 7200       # 2 hours — serve stale data while refreshing in background

_refresh_lock = threading.Lock()
_refresh_in_progress = False


def _cache_age() -> float | None:
    """Return seconds since cache was written, or None if no cache exists."""
    try:
        return time.time() - os.path.getmtime(CACHE_PATH)
    except FileNotFoundError:
        return None


def load_cache() -> list | None:
    """
    Return cached accounts if the cache is fresh enough, else None.
    'Fresh' means written within STALE_TTL seconds.
    """
    age = _cache_age()
    if age is None or age > STALE_TTL:
        return None
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def is_cache_stale() -> bool:
    """True if cache exists but is older than CACHE_TTL (should be refreshed)."""
    age = _cache_age()
    return age is not None and age > CACHE_TTL


def save_cache(accounts: list) -> None:
    """Write accounts list to disk as JSON."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(accounts, f)
    os.replace(tmp, CACHE_PATH)  # atomic swap


def refresh_in_background(fetch_fn) -> None:
    """
    Spawn a background thread to refresh the cache without blocking the UI.
    Only one refresh runs at a time.
    """
    global _refresh_in_progress

    def _run():
        global _refresh_in_progress
        try:
            accounts = fetch_fn()
            save_cache(accounts)
        finally:
            _refresh_in_progress = False

    with _refresh_lock:
        if _refresh_in_progress:
            return
        _refresh_in_progress = True

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def cache_last_updated() -> str | None:
    """Return human-readable last-updated string, or None."""
    age = _cache_age()
    if age is None:
        return None
    minutes = int(age // 60)
    if minutes < 1:
        return "just now"
    if minutes == 1:
        return "1 minute ago"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    return f"{hours} hour{'s' if hours != 1 else ''} ago"
