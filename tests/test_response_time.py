"""
Test median email response time calculation.
Usage: python3 tests/test_response_time.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.connectors.hubspot import get_active_customers
from backend.connectors.hubspot_activity import (
    get_recent_emails,
    get_email_company_associations,
    build_company_email_stats,
    build_company_response_stats,
)

print("\n=== Email Response Time Test ===\n")

print("1. Fetching owner IDs from active customers...")
customers = get_active_customers()
owner_ids = list({
    str(c.get("properties", {}).get("hubspot_owner_id"))
    for c in customers
    if c.get("properties", {}).get("hubspot_owner_id")
})
print(f"   {len(owner_ids)} owner IDs found")

print("\n2. Fetching emails (last 90 days)...")
emails = get_recent_emails(days=90, owner_ids=owner_ids)
print(f"   {len(emails)} emails fetched")

# Check how many have thread IDs
with_thread = sum(1 for e in emails if e.get("properties", {}).get("hs_email_thread_id"))
print(f"   {with_thread} have a thread ID ({round(with_thread/max(len(emails),1)*100)}%)")

print("\n3. Fetching company associations...")
email_ids = [e["id"] for e in emails]
email_to_company = get_email_company_associations(email_ids)
print(f"   {len(email_to_company)} email-to-company links found")

print("\n4. Computing response time stats...")
response_stats = build_company_response_stats(emails, email_to_company)
print(f"   Companies with response time data: {len(response_stats)}")

if response_stats:
    times = [v["median_response_minutes"] for v in response_stats.values()]
    times.sort()
    print(f"   Fastest median: {times[0]} min")
    print(f"   Slowest median: {times[-1]} min")
    overall_median = times[len(times)//2]
    print(f"   Overall median across all companies: {overall_median} min ({round(overall_median/60, 1)}h)")

    print("\n   Sample — 10 companies with fastest response:")
    company_names = {c["id"]: c.get("properties", {}).get("name", "?") for c in customers}
    shown = 0
    for cid, v in sorted(response_stats.items(), key=lambda x: x[1]["median_response_minutes"]):
        name = company_names.get(cid, cid)
        n = v["response_sample_size"]
        mins = v["median_response_minutes"]
        print(f"      {name[:40]:40s}  {mins:7.1f} min  (n={n})")
        shown += 1
        if shown >= 10:
            break

    print("\n   Sample — 10 companies with slowest response:")
    shown = 0
    for cid, v in sorted(response_stats.items(), key=lambda x: x[1]["median_response_minutes"], reverse=True):
        name = company_names.get(cid, cid)
        n = v["response_sample_size"]
        mins = v["median_response_minutes"]
        print(f"      {name[:40]:40s}  {mins:7.1f} min  ({round(mins/60, 1)}h)  (n={n})")
        shown += 1
        if shown >= 10:
            break

print("\n=== Done ===\n")
