"""
Test what meetings data is accessible with current HubSpot scopes.
Usage: python3 tests/test_meetings_access.py
"""

import sys, os, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.connectors.hubspot import HEADERS, BASE_URL
from datetime import datetime, timedelta, timezone

print("\n=== Meetings Access Test ===\n")

# Test 1: Can we list meetings at all?
print("1. Fetching recent meetings...")
url = f"{BASE_URL}/crm/v3/objects/meetings"
params = {
    "limit": 5,
    "properties": "hs_timestamp,hs_meeting_title,hubspot_owner_id,hs_meeting_start_time",
}
r = requests.get(url, headers=HEADERS, params=params)
print(f"   Status: {r.status_code}")
if r.ok:
    meetings = r.json().get("results", [])
    print(f"   ✅ Got {len(meetings)} meetings")
    for m in meetings:
        props = m.get("properties", {})
        print(f"      - {props.get('hs_meeting_title', 'No title')} | "
              f"{(props.get('hs_meeting_start_time') or props.get('hs_timestamp') or '')[:10]}")
else:
    print(f"   ❌ {r.text}")

# Test 2: Can we search meetings by date?
print("\n2. Searching meetings in last 90 days...")
since = datetime.now(timezone.utc) - timedelta(days=90)
since_ms = str(int(since.timestamp() * 1000))
search_url = f"{BASE_URL}/crm/v3/objects/meetings/search"
payload = {
    "filterGroups": [{"filters": [{
        "propertyName": "hs_meeting_start_time",
        "operator": "GTE",
        "value": since_ms,
    }]}],
    "properties": ["hs_meeting_start_time", "hubspot_owner_id", "hs_meeting_title"],
    "limit": 5,
}
r2 = requests.post(search_url, headers=HEADERS, json=payload)
print(f"   Status: {r2.status_code}")
if r2.ok:
    data = r2.json()
    print(f"   ✅ Total meetings in last 90 days: {data.get('total', '?')}")
    for m in data.get("results", [])[:5]:
        props = m.get("properties", {})
        print(f"      - {props.get('hs_meeting_title', 'No title')} | "
              f"{(props.get('hs_meeting_start_time') or '')[:10]}")
else:
    print(f"   ❌ {r2.text}")

# Test 3: Company association
print("\n3. Testing company-meeting association...")
companies_url = f"{BASE_URL}/crm/v3/objects/companies/search"
payload2 = {
    "filterGroups": [{"filters": [{"propertyName": "lifecyclestage", "operator": "EQ", "value": "customer"}]}],
    "properties": ["name"], "limit": 1,
}
rc = requests.post(companies_url, headers=HEADERS, json=payload2)
if rc.ok:
    company = rc.json().get("results", [{}])[0]
    company_id = company.get("id")
    company_name = company.get("properties", {}).get("name", "?")
    assoc_url = f"{BASE_URL}/crm/v3/objects/companies/{company_id}/associations/meetings"
    r3 = requests.get(assoc_url, headers=HEADERS)
    print(f"   Company: {company_name}")
    if r3.ok:
        print(f"   ✅ Found {len(r3.json().get('results', []))} associated meetings")
    else:
        print(f"   ❌ {r3.text}")

print("\n=== Done ===\n")
