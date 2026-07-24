"""
Test what email data is accessible with our current HubSpot scopes.
Usage: python3 tests/test_email_access.py
"""

import sys, os, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.connectors.hubspot import HEADERS, BASE_URL

# Test 1: Can we access the emails endpoint at all?
print("\n=== Email Access Test ===\n")
print("1. Fetching recent emails...")
url = f"{BASE_URL}/crm/v3/objects/emails"
params = {
    "limit": 5,
    "properties": "hs_timestamp,hs_email_direction,hs_email_subject,hubspot_owner_id,hs_email_status",
    "sort": "-hs_timestamp",
}
try:
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    emails = r.json().get("results", [])
    print(f"   ✅ Got {len(emails)} emails")
    for e in emails:
        props = e.get("properties", {})
        print(f"      - [{props.get('hs_email_direction', '?')}] "
              f"{props.get('hs_timestamp', '?')[:10]} | "
              f"Subject: {str(props.get('hs_email_subject', ''))[:40]}")
except Exception as ex:
    print(f"   ❌ Failed: {ex}")

# Test 2: Can we fetch all properties on an email record?
print("\n2. Fetching all properties on one email...")
try:
    r = requests.get(url, headers=HEADERS, params={"limit": 1})
    r.raise_for_status()
    results = r.json().get("results", [])
    if results:
        email_id = results[0]["id"]
        r2 = requests.get(f"{BASE_URL}/crm/v3/properties/emails", headers=HEADERS)
        r2.raise_for_status()
        props = r2.json().get("results", [])
        print(f"   ✅ Found {len(props)} properties on email objects")
        useful = [p for p in props if any(k in p["name"] for k in
                  ["direction", "timestamp", "subject", "owner", "status", "from", "to", "body"])]
        print(f"   Useful properties:")
        for p in useful:
            print(f"      - {p['name']} ({p['type']}) — {p['label']}")
except Exception as ex:
    print(f"   ❌ Failed: {ex}")

# Test 3: Can we get emails associated with a specific company?
print("\n3. Testing company-email association (using first known company)...")
try:
    companies_url = f"{BASE_URL}/crm/v3/objects/companies/search"
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "lifecyclestage", "operator": "EQ", "value": "customer"}]}],
        "properties": ["name"],
        "limit": 1,
    }
    r = requests.post(companies_url, headers=HEADERS, json=payload)
    r.raise_for_status()
    company = r.json().get("results", [{}])[0]
    company_id = company.get("id")
    company_name = company.get("properties", {}).get("name", "Unknown")

    assoc_url = f"{BASE_URL}/crm/v3/objects/companies/{company_id}/associations/emails"
    r2 = requests.get(assoc_url, headers=HEADERS)
    r2.raise_for_status()
    email_assocs = r2.json().get("results", [])
    print(f"   Company: {company_name}")
    print(f"   ✅ Found {len(email_assocs)} associated email records")
except Exception as ex:
    print(f"   ❌ Failed: {ex}")

print("\n=== Done ===\n")
