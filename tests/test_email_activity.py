"""
Test email activity with CSM owner filter.
Usage: python3 tests/test_email_activity.py
"""

import sys, os, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.connectors.hubspot import HEADERS, BASE_URL, get_active_customers
from backend.connectors.hubspot_activity import fetch_emails_in_window
from datetime import datetime, timedelta, timezone

print("\n=== Email Activity Test (with CSM filter) ===\n")

# Step 1: Get owner IDs from customer records
print("1. Getting owner IDs from active customers...")
customers = get_active_customers()
owner_ids = list({
    str(c.get("properties", {}).get("hubspot_owner_id"))
    for c in customers
    if c.get("properties", {}).get("hubspot_owner_id")
})
print(f"   Found {len(owner_ids)} distinct owner IDs on customer accounts")
print(f"   IDs: {owner_ids}\n")

# Step 2: Test one 7-day window with the owner filter
print("2. Testing one 7-day window with owner filter...")
now = datetime.now(timezone.utc)
week_start = now - timedelta(days=7)
emails = fetch_emails_in_window(week_start, now, owner_ids=owner_ids)
print(f"   Emails in last 7 days (with filter): {len(emails)}")
print(f"   Projected 90-day total: ~{len(emails) * 13} emails")

# Step 3: Compare — same window without filter
print("\n3. Same 7-day window WITHOUT owner filter...")
emails_unfiltered = fetch_emails_in_window(week_start, now, owner_ids=None)
print(f"   Emails in last 7 days (no filter): {len(emails_unfiltered)}")

reduction = round((1 - len(emails) / max(len(emails_unfiltered), 1)) * 100)
print(f"\n   Volume reduction with filter: {reduction}%")
print("\n=== Done ===\n")
