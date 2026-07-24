"""
One-time script to find the exact names of CR_ properties on deals.
Usage: python3 tests/find_deal_properties.py
"""

import sys, os, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.connectors.hubspot import HEADERS, BASE_URL

url = f"{BASE_URL}/crm/v3/properties/deals"
response = requests.get(url, headers=HEADERS)
response.raise_for_status()
props = response.json().get("results", [])
cr_props = [p for p in props if p["name"].startswith("cr_")]

print(f"\nFound {len(cr_props)} CR_ properties on deals:\n")
for p in cr_props:
    print(f"  - {p['name']} ({p['type']}) — label: {p['label']}")
print()
