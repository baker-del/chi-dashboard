import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from backend.connectors.hubspot import HEADERS, BASE_URL, hs_request

EMAIL_PROPERTIES = [
    "hs_timestamp",
    "hs_email_direction",
    "hubspot_owner_id",
    "hs_email_thread_id",
]

MEETING_PROPERTIES = [
    "hs_meeting_start_time",
    "hubspot_owner_id",
    "hs_meeting_title",
]

# HubSpot email direction values
OUTBOUND = "EMAIL"
INBOUND = "INCOMING_EMAIL"


# ── Emails ─────────────────────────────────────────────────────────────────────

def fetch_emails_in_window(start_dt, end_dt, owner_ids=None):
    """Fetch emails between two datetimes, optionally filtered to specific owners."""
    url = f"{BASE_URL}/crm/v3/objects/emails/search"

    filters = [
        {"propertyName": "hs_timestamp", "operator": "GTE", "value": str(int(start_dt.timestamp() * 1000))},
        {"propertyName": "hs_timestamp", "operator": "LT",  "value": str(int(end_dt.timestamp() * 1000))},
    ]
    if owner_ids:
        filters.append({"propertyName": "hubspot_owner_id", "operator": "IN", "values": [str(o) for o in owner_ids]})

    payload = {"filterGroups": [{"filters": filters}], "properties": EMAIL_PROPERTIES, "limit": 100}
    results = []
    after = None

    while True:
        if after:
            payload["after"] = after
        r = hs_request("post", url, headers=HEADERS, json=payload)
        data = r.json()
        results.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    return results


def get_recent_emails(days=90, owner_ids=None):
    """Fetch emails in weekly batches (sequential — HubSpot search is capped at 4 req/s)."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    all_emails = []
    window_start = start

    while window_start < now:
        window_end = min(window_start + timedelta(days=7), now)
        all_emails.extend(fetch_emails_in_window(window_start, window_end, owner_ids=owner_ids))
        window_start = window_end

    return all_emails


def get_email_company_associations(email_ids):
    """Batch-fetch company associations in parallel chunks of 100."""
    url = f"{BASE_URL}/crm/v3/associations/emails/companies/batch/read"
    batches = [email_ids[i:i + 100] for i in range(0, len(email_ids), 100)]

    def fetch_batch(batch):
        r = hs_request("post", url, headers=HEADERS, json={"inputs": [{"id": str(e)} for e in batch]})
        r.raise_for_status()
        result = {}
        for item in r.json().get("results", []):
            email_id = str(item.get("from", {}).get("id"))
            to_list = item.get("to", [])
            if to_list:
                result[email_id] = str(to_list[0].get("id"))
        return result

    email_to_company = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for mapping in pool.map(fetch_batch, batches):
            email_to_company.update(mapping)

    return email_to_company


def build_company_email_stats(emails, email_to_company):
    """Aggregate email stats per company."""
    stats = {}

    for email in emails:
        email_id = str(email.get("id"))
        company_id = email_to_company.get(email_id)
        if not company_id:
            continue

        props = email.get("properties", {})
        ts_str = props.get("hs_timestamp")
        direction = props.get("hs_email_direction", "")

        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else None
        except (ValueError, AttributeError):
            ts = None

        if company_id not in stats:
            stats[company_id] = {
                "last_email_date": None,
                "last_outbound_date": None,
                "email_count": 0,
                "outbound_count": 0,
                "inbound_count": 0,
            }

        s = stats[company_id]
        s["email_count"] += 1

        if direction == OUTBOUND:
            s["outbound_count"] += 1
            if ts and (s["last_outbound_date"] is None or ts > s["last_outbound_date"]):
                s["last_outbound_date"] = ts
        elif direction == INBOUND:
            s["inbound_count"] += 1

        if ts and (s["last_email_date"] is None or ts > s["last_email_date"]):
            s["last_email_date"] = ts

    return stats


def build_company_response_stats(emails, email_to_company):
    """
    Compute median email response time (hours) per company.
    Groups emails into threads, finds inbound→outbound pairs,
    measures time from inbound to first outbound reply.
    """
    from collections import defaultdict

    company_threads = defaultdict(lambda: defaultdict(list))

    for email in emails:
        email_id = str(email.get("id"))
        company_id = email_to_company.get(email_id)
        if not company_id:
            continue
        props = email.get("properties", {})
        ts_str = props.get("hs_timestamp")
        direction = props.get("hs_email_direction", "")
        thread_id = props.get("hs_email_thread_id")
        if not thread_id or not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        company_threads[company_id][thread_id].append((ts, direction))

    stats = {}
    for company_id, threads in company_threads.items():
        response_times = []
        for messages in threads.values():
            messages.sort(key=lambda x: x[0])
            for i, (ts, direction) in enumerate(messages):
                if direction == INBOUND:
                    for j in range(i + 1, len(messages)):
                        next_ts, next_dir = messages[j]
                        if next_dir == OUTBOUND:
                            delta_minutes = (next_ts - ts).total_seconds() / 60
                            # Ignore noise (< 1 min = auto-responder) and outliers (> 30 days)
                            if 1 <= delta_minutes <= 43200:
                                response_times.append(delta_minutes)
                            break
        if response_times:
            response_times.sort()
            n = len(response_times)
            mid = n // 2
            median = response_times[mid] if n % 2 else (response_times[mid - 1] + response_times[mid]) / 2
            stats[company_id] = {
                "median_response_minutes": round(median, 1),
                "response_times": response_times,  # raw list for % under threshold calcs
                "response_sample_size": n,
            }
    return stats


def get_company_email_stats(days=90, owner_ids=None):
    emails = get_recent_emails(days=days, owner_ids=owner_ids)
    email_ids = [e["id"] for e in emails]
    email_to_company = get_email_company_associations(email_ids)
    stats = build_company_email_stats(emails, email_to_company)
    response_stats = build_company_response_stats(emails, email_to_company)
    # Merge response stats into email stats
    for company_id, r in response_stats.items():
        if company_id in stats:
            stats[company_id].update(r)
        else:
            stats[company_id] = r
    return stats


# ── Meetings ───────────────────────────────────────────────────────────────────

def get_recent_meetings(days=90, owner_ids=None):
    """Fetch all meetings in the last N days. 1,261/90d — no batching needed."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    url = f"{BASE_URL}/crm/v3/objects/meetings/search"

    filters = [{"propertyName": "hs_meeting_start_time", "operator": "GTE", "value": str(int(since.timestamp() * 1000))}]
    if owner_ids:
        filters.append({"propertyName": "hubspot_owner_id", "operator": "IN", "values": [str(o) for o in owner_ids]})

    payload = {"filterGroups": [{"filters": filters}], "properties": MEETING_PROPERTIES, "limit": 100}
    results = []
    after = None

    while True:
        if after:
            payload["after"] = after
        r = hs_request("post", url, headers=HEADERS, json=payload)
        data = r.json()
        results.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    return results


def get_meeting_company_associations(meeting_ids):
    """Batch-fetch company associations for a list of meeting IDs."""
    url = f"{BASE_URL}/crm/v3/associations/meetings/companies/batch/read"
    meeting_to_company = {}

    for i in range(0, len(meeting_ids), 100):
        batch = meeting_ids[i:i + 100]
        r = hs_request("post", url, headers=HEADERS, json={"inputs": [{"id": str(m)} for m in batch]})
        r.raise_for_status()
        for item in r.json().get("results", []):
            meeting_id = str(item.get("from", {}).get("id"))
            to_list = item.get("to", [])
            if to_list:
                meeting_to_company[meeting_id] = str(to_list[0].get("id"))

    return meeting_to_company


def build_company_meeting_stats(meetings, meeting_to_company):
    """Aggregate meeting stats per company."""
    stats = {}

    for meeting in meetings:
        meeting_id = str(meeting.get("id"))
        company_id = meeting_to_company.get(meeting_id)
        if not company_id:
            continue

        props = meeting.get("properties", {})
        ts_str = props.get("hs_meeting_start_time")

        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else None
        except (ValueError, AttributeError):
            ts = None

        if company_id not in stats:
            stats[company_id] = {"last_meeting_date": None, "meeting_count": 0}

        s = stats[company_id]
        s["meeting_count"] += 1

        if ts and (s["last_meeting_date"] is None or ts > s["last_meeting_date"]):
            s["last_meeting_date"] = ts

    return stats


def get_company_meeting_stats(days=90, owner_ids=None):
    meetings = get_recent_meetings(days=days, owner_ids=owner_ids)
    meeting_ids = [m["id"] for m in meetings]
    meeting_to_company = get_meeting_company_associations(meeting_ids)
    return build_company_meeting_stats(meetings, meeting_to_company)


def get_upcoming_meetings(days=90, owner_ids=None):
    """Fetch meetings scheduled in the next N days."""
    now = datetime.now(timezone.utc)
    future_end = now + timedelta(days=days)
    url = f"{BASE_URL}/crm/v3/objects/meetings/search"

    filters = [
        {"propertyName": "hs_meeting_start_time", "operator": "GTE", "value": str(int(now.timestamp() * 1000))},
        {"propertyName": "hs_meeting_start_time", "operator": "LTE", "value": str(int(future_end.timestamp() * 1000))},
    ]
    if owner_ids:
        filters.append({"propertyName": "hubspot_owner_id", "operator": "IN", "values": [str(o) for o in owner_ids]})

    payload = {"filterGroups": [{"filters": filters}], "properties": MEETING_PROPERTIES, "limit": 100}
    results = []
    after = None
    while True:
        if after:
            payload["after"] = after
        r = hs_request("post", url, headers=HEADERS, json=payload)
        data = r.json()
        results.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return results


def get_team_meetings_summary(days=90, owner_ids=None):
    """
    Return team-level meeting counts for the selected historical period + upcoming 90 days.
    No company association needed — operates on raw timestamps only.
    """
    now = datetime.now(timezone.utc)
    today = now.date()

    past = get_recent_meetings(days=days, owner_ids=owner_ids)
    upcoming = get_upcoming_meetings(days=90, owner_ids=owner_ids)

    def _ts(m):
        s = m.get("properties", {}).get("hs_meeting_start_time", "")
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
        except (ValueError, AttributeError):
            return None

    past_ts    = [t for m in past     if (t := _ts(m)) is not None]
    upcoming_ts = [t for m in upcoming if (t := _ts(m)) is not None]

    # Calendar week boundaries (Mon = start)
    this_week_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - timedelta(days=today.weekday())
    last_week_start = this_week_start - timedelta(days=7)

    this_week_count = sum(1 for t in past_ts if t >= this_week_start)
    last_week_count = sum(1 for t in past_ts if last_week_start <= t < this_week_start)
    weeks_in_period = max(days / 7, 1)

    return {
        "total":         len(past_ts),
        "avg_per_week":  round(len(past_ts) / weeks_in_period, 1),
        "this_week":     this_week_count,
        "last_week":     last_week_count,
        "upcoming_90d":  len(upcoming_ts),
    }
